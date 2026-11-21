#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""零依赖多引擎搜索执行器（主入口）。

仅依赖 curl 与 Python 标准库。

用法：
    python3 core/search.py "查询词" [选项]

示例：
    python3 core/search.py "量子计算" --type web --platform baidu --limit 8
    python3 core/search.py "openai" --type news --platform bing --time w
    python3 core/search.py "transformers" --type academic --platform arxiv --limit 5

stdout 始终输出 JSON 数组，诊断信息全部写入 stderr。
"""

import asyncio
import os
import sys

# 确保 `python3 core/search.py` 直接执行时也能定位到技能根目录下的 core 包。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import argparse
import html
import json
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections import OrderedDict
from datetime import datetime, timezone

from core.cache import (
    BREAKERS,
    BREAKER_COOLDOWN,
    CACHE,
    BlockedError,
    QuotaError,
    breaker_remaining,
    cache_get,
    cache_put,
    force_cooldown,
    record_blocked,
    record_success,
)
from core.fetcher import detect_encoding, http_fetch
from core.logger import debug, error as log_error, info
from core.router import (
    DEFAULT_ACCEPT,
    DEFAULT_LANG,
    DEFAULT_PLATFORM,
    DENIED_PLATFORMS,
    FALLBACK_ROUTES,
    LANG_RE,
    MAX_LIMIT,
    PLATFORM_ALIASES,
    REGION_RE,
    ROUTES,
    SITE_RE,
    TYPES,
    TIMEOUT_DEFAULT,
    TIMEOUT_DDG,
    build_context,
    normalize_platform,
    supported_platforms,
)


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clip(text, size):
    text = (text or "").strip()
    return text if len(text) <= size else text[: size - 1].rstrip() + "…"


def collapse(text):
    return re.sub(r"\s+", " ", text or "").strip()


def strip_tags(text):
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", text or "", flags=re.DOTALL)
    return collapse(html.unescape(re.sub(r"<[^>]+>", " ", text)))


def rss_tag(block, name):
    match = re.search(
        r"<%s\b[^>]*>(.*?)</%s\s*>" % (re.escape(name), re.escape(name)),
        block,
        re.IGNORECASE | re.DOTALL,
    )
    return match.group(1) if match else ""


def parse_rss(body, current_route, ctx):
    items = []
    seen = set()
    for block in re.findall(
        r"<item\b[^>]*>(.*?)</item\s*>", body, re.IGNORECASE | re.DOTALL
    ):
        title = strip_tags(rss_tag(block, "title"))
        link = strip_tags(rss_tag(block, "link"))
        if not link:
            href = re.search(
                r"<link\b[^>]*href=[\"']([^\"']+)[\"']", block, re.IGNORECASE
            )
            link = html.unescape(href.group(1)).strip() if href else ""
        snippet = strip_tags(rss_tag(block, "description"))
        if not title or not link:
            continue
        key = link.split("#", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "title": clip(title, 300),
                "url": link,
                "snippet": clip(snippet, 500),
            }
        )
        if len(items) >= ctx["limit"]:
            break
    return items


def parse_arxiv(body, current_route, ctx):
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ValueError("arXiv Atom 解析错误：%s" % exc)
    items = []
    for entry in root.findall("atom:entry", ns):
        title = collapse(
            "".join(entry.findtext("atom:title", default="", namespaces=ns))
        )
        summary = collapse(
            "".join(entry.findtext("atom:summary", default="", namespaces=ns))
        )
        url = collapse(entry.findtext("atom:id", default="", namespaces=ns))
        if not url:
            link = entry.find("atom:link", ns)
            if link is not None:
                url = link.attrib.get("href", "")
        if not title or not url:
            continue
        items.append(
            {
                "title": clip(title, 300),
                "url": url,
                "snippet": clip(summary, 500),
            }
        )
        if len(items) >= ctx["limit"]:
            break
    return items


def parse_wikipedia(body, current_route, ctx):
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise ValueError("Wikipedia JSON 解析错误：%s" % exc)
    pages = payload.get("query", {}).get("search", [])
    items = []
    for page in pages:
        title = collapse(page.get("title", ""))
        if not title:
            continue
        snippet = strip_tags(page.get("snippet", ""))
        wiki_url = "https://%s.wikipedia.org/wiki/%s" % (
            ctx["lang"],
            urllib.parse.quote(title.replace(" ", "_"), safe=""),
        )
        items.append(
            {
                "title": clip(title, 300),
                "url": wiki_url,
                "snippet": clip(snippet, 500),
            }
        )
        if len(items) >= ctx["limit"]:
            break
    return items


def parse_github(body, current_route, ctx, response_headers=None):
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise ValueError("GitHub JSON 解析错误：%s" % exc)

    limits = []
    rate_limit = payload.get("rate_limit") or {}
    if isinstance(rate_limit, dict) and rate_limit.get("remaining") is not None:
        limits.append(rate_limit.get("remaining"))
    if response_headers and response_headers.get("x-ratelimit-remaining") is not None:
        limits.append(response_headers.get("x-ratelimit-remaining"))
    for remaining in limits:
        try:
            if int(remaining) < 5:
                raise QuotaError("GitHub 速率限制剩余=%s" % remaining)
        except (TypeError, ValueError):
            pass

    items = []
    for repo in payload.get("items", []):
        name = collapse(repo.get("full_name", ""))
        url = repo.get("html_url", "")
        if not name or not url:
            continue
        description = collapse(repo.get("description", "") or "")
        stars = repo.get("stargazers_count")
        snippet = description
        if isinstance(stars, int):
            snippet = (snippet + " ★ %d" % stars).strip()
        items.append(
            {
                "title": clip(name, 300),
                "url": url,
                "snippet": clip(snippet, 500),
            }
        )
        if len(items) >= ctx["limit"]:
            break
    return items


BLOCK_RE = re.compile(
    r"(?:captcha|verify you are human|are you a robot|unusual traffic|"
    r"access denied|robot check|人机验证|安全验证|访问验证|请输入验证码|验证码|"
    r"请稍候|正在验证|安全检查)",
    re.IGNORECASE,
)

ANCHOR_RE = re.compile(
    r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
HEADING_RE = re.compile(
    r"<h[23]\b[^>]*>(.*?)</h[23]\s*>",
    re.IGNORECASE | re.DOTALL,
)
SNIPPET_RE = re.compile(
    r"<p\b[^>]*>(.*?)</p>"
    r"|<div\b[^>]*class=[\"'][^\"']*(?:abstract|caption|snippet|desc|summary)"
    r"[^\"']*[\"'][^>]*>(.*?)</div>",
    re.IGNORECASE | re.DOTALL,
)
WS_RE = re.compile(r"\s+")

NOISE_TEXT = {
    "登录",
    "注册",
    "首页",
    "上一页",
    "下一页",
    "更多",
    "设置",
    "隐私",
    "条款",
    "帮助",
    "about",
    "privacy",
    "terms",
    "settings",
    "sign in",
    "log in",
    "next",
    "previous",
    "home",
    "导航",
    "综合",
    "文心",
    "网页",
    "资讯",
    "知道",
    "文库",
    "贴吧",
    "意见反馈",
    "隐私管理",
    "切换少年版",
    "全部",
    "特大",
    "仅限颜色",
    "查看更多",
    "ai问答",
    "360搜索首页",
    "地图",
    "百科",
    "软件",
    "黑白",
    "照片",
    "剪贴画",
    "详细了解",
    "翻译",
    "360搜索客户端官网",
    "产品论坛",
    "按焦点排序",
    "按时间排序",
}


def unwrap_url(url):
    """拆开常见搜索结果跳转包装。"""
    try:
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path.rstrip("/").endswith("/url") and query.get("q"):
            return query["q"][0]
        if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/") and query.get("uddg"):
            return query["uddg"][0]
    except Exception:
        pass
    return url


def is_breadcrumb_title(title):
    lowered = title.lower()
    return (
        "›" in title
        or "»" in title
        or "http://" in lowered
        or "https://" in lowered
    )


def nearby_snippet(cleaned, position, title):
    """从已接受锚点附近的首个段落尽力提取摘要。"""
    window = cleaned[position:position + 2500]
    for match in SNIPPET_RE.finditer(window):
        candidate = strip_tags(match.group(1) or match.group(2) or "")
        if len(candidate) < 20 or candidate == title:
            continue
        if candidate.lower().startswith(("http://", "https://")):
            continue
        return clip(candidate, 500)
    return ""


def is_noise_link(href, title):
    """过滤明显的导航、登录、帮助等非结果链接。"""
    lowered = title.lower()
    if lowered in NOISE_TEXT or lowered.startswith("登录"):
        return True
    try:
        parsed = urllib.parse.urlparse(href)
    except Exception:
        return False
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if host.startswith("passport.") or host.startswith("login."):
        return True
    if host in ("hao123.com", "www.hao123.com"):
        return True
    if host == "ai.so.com" or (host == "so.com" and path in ("", "/")):
        return True
    if host == "fanyi.so.com" or host == "m.so.com" or host.endswith("360safe.com"):
        return True
    if host == "go.microsoft.com" and path.startswith("/fwlink"):
        return True
    if host in ("news.so.com",) and path.startswith("/hotnews"):
        return True
    if host in ("image.so.com", "tv.360kan.com") and path in ("", "/", "/i", "/s"):
        return True
    if host.endswith("so.com") and path == "/ns" and "rank=" in parsed.query:
        return True
    if host in ("baidu.com", "www.baidu.com") and path in ("", "/"):
        return True
    if host == "news.baidu.com" and (path in ("", "/") or path.startswith("/sf/")):
        return True
    if host == "image.baidu.com":
        return True

    # 同一引擎的导航/筛选链接不是搜索结果。
    if host.endswith("bing.com") and path in (
        "/search", "/images/search", "/videos/search", "/news/search"
    ):
        return True
    if host.endswith("google.com") and path in ("/search", "/images", "/videos"):
        return True
    if host.endswith("so.com") and (
        path.startswith("/help")
        or path in ("/s", "/i", "/video", "/doc/search", "/search")
        or "feedback" in path
    ):
        return True
    if host.endswith("sogou.com") and path in ("/web", "/news", "/pics", "/v", "/shopping"):
        return True
    if host.endswith("yahoo.com") and path == "/search":
        return True
    if host.endswith("baidu.com") and path == "/s":
        return True
    if host.endswith("ecosia.org") and path in ("/search", "/images"):
        return True
    return False


def normalize_anchor(href, base_url):
    href = html.unescape(href or "").strip()
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:", "blob:")):
        return ""
    if href.startswith("//"):
        href = "https:" + href
    elif not re.match(r"^https?://", href, re.IGNORECASE):
        href = urllib.parse.urljoin(base_url, href)
    href = unwrap_url(href)
    if not re.match(r"^https?://", href, re.IGNORECASE) or "w3.org" in href.lower():
        return ""
    return href


def parse_generic(body, current_route, ctx, base_url):
    # 先移除明显的非结果区域，再做粗粒度锚点提取。
    cleaned = re.sub(
        r"<(script|style|noscript|svg|template|nav|footer|header)\b[^>]*>.*?</\1\s*>",
        " ",
        body,
        flags=re.IGNORECASE | re.DOTALL,
    )
    candidates = OrderedDict()  # URL -> 结果；保留首次出现顺序

    def consider(raw_href, raw_title, position):
        title = strip_tags(raw_title)
        href = normalize_anchor(raw_href, base_url)
        if not href or not title or len(title) < 2:
            return
        if title.lower() in NOISE_TEXT or is_breadcrumb_title(title):
            return
        if is_noise_link(href, title):
            return
        key = href.split("#", 1)[0]
        existing = candidates.get(key)
        if existing is not None:
            # 搜索页常对缩略图和标题重复使用同一 URL。
            if len(title) > len(existing["title"]):
                existing["title"] = clip(title, 300)
                snippet = nearby_snippet(cleaned, position, title)
                if snippet:
                    existing["snippet"] = snippet
            return
        candidates[key] = {
            "title": clip(title, 300),
            "url": href,
            "snippet": nearby_snippet(cleaned, position, title) or clip(title, 500),
        }

    # 第一轮：结果标题通常位于 <h2> 或 <h3> 内。
    for heading in HEADING_RE.finditer(cleaned):
        base = heading.start(1)
        for match in ANCHOR_RE.finditer(heading.group(1)):
            consider(match.group(1), match.group(2), base + match.end())
        if len(candidates) >= ctx["limit"] * 4:
            break

    # 第二轮：部分引擎反过来用 <a> 包裹标题。
    if len(candidates) < ctx["limit"]:
        for match in ANCHOR_RE.finditer(cleaned):
            inner = match.group(2)
            heading = HEADING_RE.search(inner)
            if heading is None:
                continue
            consider(match.group(1), heading.group(1), match.end())
            if len(candidates) >= ctx["limit"] * 4:
                break

    # 第三轮：对没有标题标签的引擎兜底扫描全部锚点。
    # 图片/地图/翻译等垂直页大多依赖 JavaScript；没有标题命中时，
    # 返回搜索页回退地址比返回导航链接更诚实。
    if ctx.get("type") not in ("image", "map", "translate") and len(candidates) < ctx["limit"]:
        scanned = 0
        for match in ANCHOR_RE.finditer(cleaned):
            scanned += 1
            if scanned > 3000:
                break
            consider(match.group(1), match.group(2), match.end())
            if len(candidates) >= ctx["limit"] * 4:
                break

    return list(candidates.values())[: ctx["limit"]]


def looks_blocked(body, query):
    sample = (body or "")[:50000]
    query_lower = (query or "").lower()
    for match in BLOCK_RE.finditer(sample):
        token = match.group(0).lower()
        # 查询词本身涉及 captcha 时，避免把正常结果页误判为拦截页。
        if token not in query_lower:
            return True
    return False


def failure(status, message, url, source, fetched_at):
    return [
        {
            "title": clip("[%s] %s" % (status, message), 300),
            "url": url or "",
            "snippet": message,
            "source": source or "",
            "status": status,
            "fetched_at": fetched_at,
        }
    ]


def _execute_once(args):
    """执行单条路由，返回 (结果列表, 退出码)。"""
    fetched_at = utc_now()
    query = (args.query or "").strip()

    type_name = args.type
    platform = normalize_platform(args.platform) if args.platform else DEFAULT_PLATFORM[type_name]
    limit = max(1, min(MAX_LIMIT, args.limit))
    page = max(1, args.page)
    time_range = args.time_range
    lang = (args.lang or DEFAULT_LANG).strip()
    region = (args.region or "wt-wt").strip()

    if not query:
        return failure("error", "查询词为空", "", platform, fetched_at), 2

    if args.site:
        site = args.site.strip()
        if not SITE_RE.fullmatch(site) or len(site) > 253:
            return failure(
                "error",
                "--site 非法：仅允许字母、数字、点和横线",
                "",
                platform,
                fetched_at,
            ), 2
        query = "%s site:%s" % (query, site)

    if not LANG_RE.fullmatch(lang):
        return failure("error", "--lang 头部值非法", "", platform, fetched_at), 2
    if not REGION_RE.fullmatch(region):
        return failure(
            "error", "--region 非法，请使用 cn-zh、us-en 或 wt-wt 等值", "", platform, fetched_at
        ), 2

    if platform in DENIED_PLATFORMS:
        return failure(
            "error",
            "平台 '%s' 需要登录态，已拒绝" % platform,
            "",
            platform,
            fetched_at,
        ), 2

    current_route = ROUTES.get((type_name, platform))
    if current_route is None:
        available = ", ".join(supported_platforms(type_name)) or "(none)"
        return failure(
            "error",
            "不支持的组合 type=%s platform=%s；可用：%s"
            % (type_name, platform, available),
            "",
            platform,
            fetched_at,
        ), 2

    if time_range and not current_route.get("bing_time"):
        debug(
            "search",
            "--time 仅对必应系列路由生效，%s/%s 中已忽略" % (type_name, platform),
        )

    context = build_context(query, limit, page, time_range, lang, region, current_route)
    context["type"] = type_name
    request_url = current_route["url"].format(**context)
    browse_url = current_route.get("browse", current_route["url"]).format(**context)
    source = current_route.get("source", platform)

    cache_key = (
        type_name,
        platform,
        query,
        limit,
        page,
        time_range or "",
        args.site or "",
        lang,
        region,
    )
    cached = cache_get(cache_key)
    if cached is not None:
        return cached, 0

    breaker_key = (type_name, platform)
    remain = breaker_remaining(breaker_key)
    if remain > 0:
        info(
            "search",
            "%s/%s 处于冷却中，还剩 %s 秒" % (type_name, platform, remain),
        )
        return failure(
            "cooldown",
            "引擎在连续被拦截后冷却中；%s 秒后重试或打开搜索页" % remain,
            browse_url,
            source,
            fetched_at,
        ), 0

    headers = {
        "Accept-Language": lang,
        "Accept": current_route.get("accept", DEFAULT_ACCEPT),
    }
    headers.update(current_route.get("headers", {}))
    if current_route.get("method", "GET").upper() == "POST":
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    timeout = current_route.get("timeout", TIMEOUT_DDG if platform == "ddg" else TIMEOUT_DEFAULT)
    post_data = None
    if current_route.get("data"):
        post_data = {
            key: value.format(**context) for key, value in current_route["data"].items()
        }

    code, content_type, response_headers, body_bytes, error = http_fetch(
        request_url,
        headers=headers,
        timeout=timeout,
        method=current_route.get("method", "GET"),
        data=post_data,
    )
    body = body_bytes.decode(detect_encoding(body_bytes, content_type), errors="replace")

    if current_route.get("sleep_after"):
        time.sleep(current_route["sleep_after"])

    if error:
        log_error("search", "%s/%s 获取失败：%s" % (type_name, platform, error))
        return failure(
            "error",
            "网络错误：%s" % error,
            browse_url,
            source,
            fetched_at,
        ), 1

    try:
        if code in (403, 429, 503):
            raise BlockedError("HTTP %s" % code)
        if code == 0:
            raise ValueError("未返回 HTTP 状态码")
        if code >= 400:
            raise ValueError("HTTP %s" % code)
        if looks_blocked(body, query):
            raise BlockedError("检测到验证码或校验页面")

        parser = current_route.get("parser", "generic")
        if parser == "rss":
            parsed = parse_rss(body, current_route, context)
        elif parser == "arxiv":
            parsed = parse_arxiv(body, current_route, context)
        elif parser == "wikipedia":
            parsed = parse_wikipedia(body, current_route, context)
        elif parser == "github":
            parsed = parse_github(body, current_route, context, response_headers)
        else:
            parsed = parse_generic(body, current_route, context, request_url)

        if not parsed:
            record_success(breaker_key)
            info("search", "%s/%s 未解析出可用结果" % (type_name, platform))
            return failure(
                "empty",
                "未解析出可用结果；请在浏览器中打开搜索页",
                browse_url,
                source,
                fetched_at,
            ), 0

        results = [
            {
                "title": row["title"],
                "url": row["url"],
                "snippet": row["snippet"],
                "source": source,
                "status": "ok",
                "fetched_at": fetched_at,
            }
            for row in parsed[:limit]
        ]
        record_success(breaker_key)
        cache_put(cache_key, results)
        info("search", "%s/%s -> %d 条结果" % (type_name, platform, len(results)))
        return results, 0

    except QuotaError as exc:
        force_cooldown(breaker_key)
        log_error("search", "%s/%s 额度：%s" % (type_name, platform, exc))
        return failure(
            "blocked",
            "API 额度近乎耗尽；冷却 %s 秒" % BREAKER_COOLDOWN,
            browse_url,
            source,
            fetched_at,
        ), 0
    except BlockedError as exc:
        record_blocked(breaker_key)
        log_error("search", "%s/%s 被拦截：%s" % (type_name, platform, exc))
        return failure(
            "blocked",
            "引擎已拦截请求（%s）；请在浏览器中打开搜索页" % exc,
            browse_url,
            source,
            fetched_at,
        ), 0
    except Exception as exc:
        error("search", "%s/%s 解析错误：%s" % (type_name, platform, exc))
        return failure(
            "error",
            "无法解析响应：%s" % exc,
            browse_url,
            source,
            fetched_at,
        ), 1


def execute(args):
    """执行一次搜索，并在主通道失败时按配置尝试备用通道。"""
    results, code = _execute_once(args)

    if code != 0 or not results or results[0].get("status") == "ok":
        return results, code

    type_name = args.type
    platform = normalize_platform(args.platform) if args.platform else DEFAULT_PLATFORM[type_name]
    for fb_type, fb_platform in FALLBACK_ROUTES.get((type_name, platform), []):
        info(
            "search",
            "%s/%s 主通道返回 %s，回退到 %s/%s"
            % (type_name, platform, results[0].get("status"), fb_type, fb_platform),
        )
        fb_args = argparse.Namespace(**vars(args))
        fb_args.type = fb_type
        fb_args.platform = fb_platform
        fb_results, fb_code = _execute_once(fb_args)
        if fb_code == 0 and fb_results and fb_results[0].get("status") == "ok":
            info(
                "search",
                "回退成功：%s/%s -> %d 条结果" % (fb_type, fb_platform, len(fb_results)),
            )
            return fb_results, 0

    return results, code


async def async_execute(args):
    """异步执行：主通道失败时并发尝试所有备用回退路由，取第一个 ok 结果。

    与 execute 的串行回退不同，async_execute 会同时发起所有备用路由的
    抓取（通过 asyncio.to_thread 并发 curl），显著缩短新闻/图片等
    易拦截类型的端到端延迟。
    """
    results, code = _execute_once(args)

    if code != 0 or not results or results[0].get("status") == "ok":
        return results, code

    type_name = args.type
    platform = normalize_platform(args.platform) if args.platform else DEFAULT_PLATFORM[type_name]
    fallbacks = list(
        FALLBACK_ROUTES.get((type_name, platform), [])
    )
    if not fallbacks:
        return results, code

    async def try_fallback(fb_type, fb_platform):
        fb_args = argparse.Namespace(**vars(args))
        fb_args.type = fb_type
        fb_args.platform = fb_platform
        res, cd = await asyncio.get_event_loop().run_in_executor(
            None, _execute_once, fb_args
        )
        if cd == 0 and res and res[0].get("status") == "ok":
            return res, cd, (fb_type, fb_platform)
        return None, cd, (fb_type, fb_platform)

    info(
        "search",
        "%s/%s 主通道返回 %s，并发尝试 %d 条备用路由"
        % (type_name, platform, results[0].get("status"), len(fallbacks)),
    )

    completed = await asyncio.gather(
        *[try_fallback(ft, fp) for ft, fp in fallbacks]
    )

    # 按配置顺序返回首个成功的 ok 结果。
    for res, cd, route_key in completed:
        if cd == 0 and res and res[0].get("status") == "ok":
            info(
                "search",
                "并发回退成功：%s -> %d 条结果" % (route_key, len(res)),
            )
            return res, 0

    # 全部备用路由均失败，返回主通道的原始结果。
    return results, code


def build_parser():
    parser = argparse.ArgumentParser(
        prog="search.py",
        description="Zero-dependency multi-engine search (curl + Python stdlib).",
    )
    parser.add_argument("query", nargs="?", help="search query")
    parser.add_argument("--type", default="web", choices=TYPES, help="result type (default: web)")
    parser.add_argument(
        "--platform",
        default=None,
        help="engine/platform; default depends on --type",
    )
    parser.add_argument("--limit", type=int, default=8, help="results, 1-20 (default: 8)")
    parser.add_argument("--page", type=int, default=1, help="page number, starting at 1")
    parser.add_argument(
        "--time",
        dest="time_range",
        choices=["d", "w", "m", "y"],
        help="time range (bing-family routes only)",
    )
    parser.add_argument("--site", default=None, help="site filter, e.g. example.com")
    parser.add_argument("--lang", default=None, help="Accept-Language value, e.g. zh-CN or en-US")
    parser.add_argument("--region", default=None, help="DuckDuckGo kl value, e.g. cn-zh or us-en")
    parser.add_argument("--list", action="store_true", help="print supported routes and exit")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list:
        listing = {}
        for type_name in TYPES:
            listing[type_name] = supported_platforms(type_name)
        print(json.dumps(listing, ensure_ascii=False, indent=2))
        return 0

    if not args.query:
        parser.error("除非使用 --list，否则需要提供查询词")

    try:
        results, exit_code = execute(args)
    except Exception as exc:  # 即使出现意外异常，也要保持 JSON 输出契约
        log_error("search", "致命错误：%s" % exc)
        results = failure("error", "致命错误：%s" % exc, "", "", utc_now())
        exit_code = 1

    print(json.dumps(results, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
