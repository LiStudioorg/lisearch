# -*- coding: utf-8 -*-
"""统一日志：支持 DEBUG/INFO/ERROR 三级，由 WEB_SEARCH_LOG_LEVEL 环境变量控制。

默认级别为 INFO；设置 WEB_SEARCH_LOG_LEVEL=DEBUG 可输出详细诊断，
设置为 ERROR 则只输出错误。所有日志写入 stderr，不干扰 stdout JSON。
"""

import os
import sys


_LEVELS = {"DEBUG": 10, "INFO": 20, "ERROR": 30}
_CURRENT_LEVEL = _LEVELS.get(
    os.environ.get("WEB_SEARCH_LOG_LEVEL", "INFO").upper(), 20
)


def set_level(name):
    """运行时切换日志级别。"""
    global _CURRENT_LEVEL
    _CURRENT_LEVEL = _LEVELS.get(name.upper(), 20)


def _should(level):
    return _LEVELS.get(level, 20) >= _CURRENT_LEVEL


def debug(tag, message):
    """调试级别日志。"""
    if _should("DEBUG"):
        print("[debug][%s] %s" % (tag, message), file=sys.stderr)


def info(tag, message):
    """信息级别日志。"""
    if _should("INFO"):
        print("[info][%s] %s" % (tag, message), file=sys.stderr)


def error(tag, message):
    """错误级别日志（始终输出，除非级别被设得更高）。"""
    if _should("ERROR"):
        print("[error][%s] %s" % (tag, message), file=sys.stderr)
