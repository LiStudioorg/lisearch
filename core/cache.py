# -*- coding: utf-8 -*-
"""LRU 缓存 + 熔断器，支持 SQLite 持久化（写穿，无权限时静默退回内存）。

缓存上限 256 条，TTL 600 秒；熔断器在同一 (type, platform) 连续 5 次
被 blocked 后冷却 120 秒。所有状态首先写入进程内 L1，再同步写入
SQLite（写穿）；SQLite 不可用时自动静默退回纯内存模式。
"""

import json
import os
import sqlite3
import time
from collections import OrderedDict

from core.logger import info


CACHE_MAX = 256
CACHE_TTL = 600
BREAKER_THRESHOLD = 5
BREAKER_COOLDOWN = 120

# 持久化状态默认写入用户缓存目录；可用环境变量覆盖。
STATE_DIR = os.environ.get("WEB_SEARCH_STATE_DIR") or os.path.join(
    os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")),
    "web-search",
)
STATE_DB = os.environ.get("WEB_SEARCH_STATE_DB") or os.path.join(STATE_DIR, "state.sqlite3")
PERSIST_ENABLED = os.environ.get("WEB_SEARCH_DISABLE_PERSIST", "").lower() not in (
    "1", "true", "yes", "on",
)

_DB_INITIALIZED = False
_DB_WARNED = False


class BlockedError(Exception):
    """上游引擎明确拦截或验证了请求。"""


class QuotaError(BlockedError):
    """上游 API 额度接近耗尽。"""


def _db_connect():
    """打开 SQLite 状态库；失败时返回 None 并退回进程内状态。

    目录创建、建表、WAL 模式等任何失败都会被捕获，仅记录一条
    INFO 日志后返回 None，调用方继续使用进程内 OrderedDict，保证
    无写权限或磁盘满时程序不崩溃。
    """
    global _DB_INITIALIZED, _DB_WARNED
    if not PERSIST_ENABLED:
        return None
    try:
        directory = os.path.dirname(STATE_DB)
        if directory:
            os.makedirs(directory, exist_ok=True)
        conn = sqlite3.connect(STATE_DB, timeout=5)
        conn.execute("PRAGMA busy_timeout=5000")
        if not _DB_INITIALIZED:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS cache ("
                "key TEXT PRIMARY KEY, stored_at REAL NOT NULL, "
                "last_access REAL NOT NULL, payload TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS breakers ("
                "key TEXT PRIMARY KEY, count INTEGER NOT NULL, until REAL NOT NULL)"
            )
            conn.commit()
            _DB_INITIALIZED = True
        return conn
    except Exception as exc:
        if not _DB_WARNED:
            info("cache", "持久化状态不可用，回退到进程内缓存：%s" % exc)
            _DB_WARNED = True
        return None


def _db_run(sql, params=(), fetch=False):
    """执行一条 SQL；失败时静默退回进程内状态。"""
    conn = _db_connect()
    if conn is None:
        return None
    try:
        cursor = conn.execute(sql, params)
        rows = cursor.fetchall() if fetch else None
        conn.commit()
        return rows
    except sqlite3.Error as exc:
        info("cache", "状态库操作失败：%s" % exc)
        return None
    finally:
        conn.close()


def _state_key(key):
    return json.dumps(key, ensure_ascii=False, separators=(",", ":"))


class PersistentCache(OrderedDict):
    """带 SQLite 写穿的 LRU 缓存。"""

    def clear(self):
        super().clear()
        _db_run("DELETE FROM cache")

    def pop(self, key, default=None):
        if key in self:
            value = super().pop(key)
            _db_run("DELETE FROM cache WHERE key = ?", (_state_key(key),))
            return value
        return default

    def popitem(self, last=True):
        key, value = super().popitem(last=last)
        _db_run("DELETE FROM cache WHERE key = ?", (_state_key(key),))
        return key, value


class PersistentBreakers(dict):
    """带 SQLite 写穿的熔断状态。"""

    def clear(self):
        super().clear()
        _db_run("DELETE FROM breakers")


CACHE = PersistentCache()      # 进程内 L1：缓存键 -> (写入时间, 结果列表)
BREAKERS = PersistentBreakers()  # 进程内 L1：(类型, 平台) -> {"count", "until"}


def cache_get(key):
    now = time.time()
    item = CACHE.get(key)
    if item is not None:
        stored_at, results = item
        if now - stored_at > CACHE_TTL:
            CACHE.pop(key, None)
            return None
        CACHE.move_to_end(key)
        _db_run(
            "UPDATE cache SET last_access = ? WHERE key = ?",
            (now, _state_key(key)),
        )
        return [dict(row) for row in results]

    rows = _db_run(
        "SELECT stored_at, payload FROM cache WHERE key = ?",
        (_state_key(key),),
        fetch=True,
    )
    if not rows:
        return None
    stored_at, payload = rows[0]
    if now - float(stored_at) > CACHE_TTL:
        CACHE.pop(key, None)
        return None
    try:
        results = json.loads(payload)
    except (TypeError, ValueError):
        CACHE.pop(key, None)
        return None
    if not isinstance(results, list):
        CACHE.pop(key, None)
        return None
    CACHE[key] = (float(stored_at), results)
    CACHE.move_to_end(key)
    while len(CACHE) > CACHE_MAX:
        CACHE.popitem(last=False)
    _db_run(
        "UPDATE cache SET last_access = ? WHERE key = ?",
        (now, _state_key(key)),
    )
    return [dict(row) for row in results]


def cache_put(key, results):
    stored_at = time.time()
    payload = json.dumps(results, ensure_ascii=False)
    CACHE[key] = (stored_at, [dict(row) for row in results])
    CACHE.move_to_end(key)
    while len(CACHE) > CACHE_MAX:
        CACHE.popitem(last=False)
    _db_run(
        "INSERT OR REPLACE INTO cache(key, stored_at, last_access, payload) "
        "VALUES (?, ?, ?, ?)",
        (_state_key(key), stored_at, stored_at, payload),
    )
    _db_run(
        "DELETE FROM cache WHERE key NOT IN ("
        "SELECT key FROM cache ORDER BY last_access DESC LIMIT ?)",
        (CACHE_MAX,),
    )


def _load_breaker(key):
    state = BREAKERS.get(key)
    if state is not None:
        return {
            "count": int(state.get("count", 0)),
            "until": float(state.get("until", 0.0)),
        }
    rows = _db_run(
        "SELECT count, until FROM breakers WHERE key = ?",
        (_state_key(key),),
        fetch=True,
    )
    if rows:
        state = {"count": int(rows[0][0]), "until": float(rows[0][1])}
        BREAKERS[key] = dict(state)
        return state
    return {"count": 0, "until": 0.0}


def _save_breaker(key, state):
    BREAKERS[key] = dict(state)
    _db_run(
        "INSERT OR REPLACE INTO breakers(key, count, until) VALUES (?, ?, ?)",
        (_state_key(key), int(state["count"]), float(state["until"])),
    )


def breaker_remaining(key):
    state = _load_breaker(key)
    remain = int(state.get("until", 0.0) - time.time())
    return remain if remain > 0 else 0


def record_blocked(key):
    state = _load_breaker(key)
    state["count"] = int(state.get("count", 0)) + 1
    if state["count"] >= BREAKER_THRESHOLD:
        state["until"] = time.time() + BREAKER_COOLDOWN
        state["count"] = 0
        info("cache", "熔断已开启：%s/%s（%s 秒）" % (key[0], key[1], BREAKER_COOLDOWN))
    _save_breaker(key, state)


def record_success(key):
    state = _load_breaker(key)
    if state["count"] or state["until"]:
        state["count"] = 0
        _save_breaker(key, state)


def force_cooldown(key):
    _save_breaker(key, {"count": 0, "until": time.time() + BREAKER_COOLDOWN})
    info("cache", "强制熔断：%s/%s（%s 秒）" % (key[0], key[1], BREAKER_COOLDOWN))
