# -*- coding: utf-8 -*-
"""URL 模板路由表、分页上下文构造与回退链。"""

import asyncio
import re
import urllib.parse


UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_LANG = "zh-CN,zh;q=0.9,en;q=0.8"
DEFAULT_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
XML_ACCEPT = "application/rss+xml,application/xml,text/xml;q=0.9,*/*;q=0.8"
ATOM_ACCEPT = "application/atom+xml,application/xml;q=0.9,*/*;q=0.8"
JSON_ACCEPT = "application/json,text/plain;q=0.9,*/*;q=0.8"
GITHUB_ACCEPT = "application/vnd.github+json,application/json;q=0.9,*/*;q=0.8"

MAX_LIMIT = 20
TIMEOUT_DDG = 10
TIMEOUT_DEFAULT = 20

TYPES = [
    "web",
    "image",
    "news",
    "video",
    "academic",
    "code",
    "map",
    "shopping",
    "translate",
    "rss",
]

PLATFORMS = [
    "baidu",
    "bing",
    "ddg",
    "google",
    "quark",
    "sogou",
    "360",
    "yahoo",
    "ecosia",
    "arxiv",
    "wikipedia",
    "github",
]

# 需要登录态的平台，按硬约束直接拒绝。
DENIED_PLATFORMS = {
    "weibo",
    "zhihu",
    "xiaohongshu",
    "douyin",
    "bilibili",
    "toutiao",
    "taobao",
    "jd",
    "wechat",
    "qq",
}

PLATFORM_ALIASES = {
    "so360": "360",
    "so.com": "360",
    "duckduckgo": "ddg",
    "ddg-html": "ddg",
    "wiki": "wikipedia",
    "gh": "github",
    "scholar": "google",
}

DEFAULT_PLATFORM = {
    "web": "bing",
    "image": "bing",
    "news": "bing",
    "video": "bing",
    "academic": "arxiv",
    "code": "github",
    "map": "bing",
    "shopping": "bing",
    "translate": "bing",
    "rss": "bing",
}

BING_TIME_FILTER = {
    "d": "interval_1",
    "w": "interval_2",
    "m": "interval_3",
    "y": "interval_4",
}

# 主通道无结果或失败时按顺序尝试的备用路由。
FALLBACK_ROUTES = {
    # Bing 新闻 RSS 通道已不稳定，依次回退到 Google News RSS、
    # 百度新闻 HTML 与 Bing 网页 RSS。
    ("news", "bing"): [("news", "google"), ("news", "baidu"), ("rss", "bing")],
}

SITE_RE = re.compile(r"^[A-Za-z0-9.-]+$")
LANG_RE = re.compile(r"^[A-Za-z0-9,;=._ -]+$")
REGION_RE = re.compile(r"^[A-Za-z0-9-]+$")


def route(url, parser="generic", **extra):
    item = {"url": url, "parser": parser}
    item.update(extra)
    return item


ROUTES = {
    # ---- 网页 -----------------------------------------------------------
    ("web", "baidu"): route(
        "https://www.baidu.com/s?ie=utf-8&wd={q}&pn={pn}&rn={limit}"
    ),
    ("web", "bing"): route(
        "https://www.bing.com/search?q={q}&first={first}&count={limit}{time_filter}",
        bing_time=True,
    ),
    ("web", "ddg"): route(
        "https://html.duckduckgo.com/html/",
        method="POST",
        data={"q": "{qraw}", "b": "", "kl": "{kl}", "s": "{s}"},
        browse="https://html.duckduckgo.com/html/?q={q}&kl={kl}",
        timeout=TIMEOUT_DDG,
    ),
    ("web", "google"): route(
        "https://www.google.com/search?q={q}&num={limit}&start={start}&hl={lang}&gbv=1"
    ),
    ("web", "quark"): route(
        "https://quark.sm.cn/s?q={q}&page={page}"
    ),
    ("web", "sogou"): route(
        "https://www.sogou.com/web?query={q}&page={page}"
    ),
    ("web", "360"): route(
        "https://www.so.com/s?q={q}&pn={page}"
    ),
    ("web", "yahoo"): route(
        "https://search.yahoo.com/search?p={q}&b={first}"
    ),
    ("web", "ecosia"): route(
        "https://www.ecosia.org/search?q={q}&p={p}"
    ),

    # ---- 新闻 -----------------------------------------------------------
    ("news", "bing"): route(
        "https://www.bing.com/news/search?q={q}&format=RSS&first={first}&count={limit}{time_filter}",
        parser="rss",
        source="bing-rss",
        bing_time=True,
        accept=XML_ACCEPT,
    ),
    ("news", "baidu"): route(
        "https://news.baidu.com/ns?word={q}&pn={pn}&rn={limit}&cl=2&ct=1"
    ),
    ("news", "google"): route(
        "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en",
        parser="rss",
        source="google-news-rss",
        accept=XML_ACCEPT,
    ),
    ("news", "sogou"): route(
        "https://news.sogou.com/news?query={q}&page={page}"
    ),
    ("news", "360"): route(
        "https://news.so.com/ns?q={q}&pn={page}"
    ),
    ("news", "yahoo"): route(
        "https://news.search.yahoo.com/search?p={q}&b={first}"
    ),
    ("news", "ddg"): route(
        "https://duckduckgo.com/?q={q}&iar=news&ia=news",
        timeout=TIMEOUT_DDG,
    ),

    # ---- 图片 -----------------------------------------------------------
    ("image", "baidu"): route(
        "https://image.baidu.com/search/index?tn=baiduimage&word={q}&pn={pn}&rn={limit}"
    ),
    ("image", "bing"): route(
        "https://www.bing.com/images/search?q={q}&first={first}&count={limit}{time_filter}",
        bing_time=True,
    ),
    ("image", "ddg"): route(
        "https://duckduckgo.com/?q={q}&iax=images&ia=images",
        timeout=TIMEOUT_DDG,
    ),
    ("image", "google"): route(
        "https://www.google.com/search?tbm=isch&q={q}&start={start}&num={limit}&gbv=1"
    ),
    ("image", "sogou"): route(
        "https://pic.sogou.com/pics?query={q}&start={start}"
    ),
    ("image", "360"): route(
        "https://image.so.com/i?q={q}&pn={page}"
    ),
    ("image", "yahoo"): route(
        "https://images.search.yahoo.com/search/images?p={q}&b={first}"
    ),
    ("image", "ecosia"): route(
        "https://www.ecosia.org/images?q={q}&p={p}"
    ),

    # ---- 视频 -----------------------------------------------------------
    ("video", "baidu"): route(
        "https://v.baidu.com/v?word={q}&pn={pn}"
    ),
    ("video", "bing"): route(
        "https://www.bing.com/videos/search?q={q}&first={first}&count={limit}{time_filter}",
        bing_time=True,
    ),
    ("video", "ddg"): route(
        "https://duckduckgo.com/?q={q}&iax=videos&ia=videos",
        timeout=TIMEOUT_DDG,
    ),
    ("video", "google"): route(
        "https://www.google.com/search?tbm=vid&q={q}&start={start}&num={limit}&gbv=1"
    ),
    ("video", "sogou"): route(
        "https://v.sogou.com/v?query={q}&page={page}"
    ),
    ("video", "360"): route(
        "https://tv.360kan.com/s?q={q}"
    ),
    ("video", "yahoo"): route(
        "https://video.search.yahoo.com/search/video?p={q}&b={first}"
    ),

    # ---- 学术 -----------------------------------------------------------
    ("academic", "arxiv"): route(
        "https://export.arxiv.org/api/query?search_query=all:{q}"
        "&start={offset}&max_results={limit}&sortBy=relevance",
        parser="arxiv",
        accept=ATOM_ACCEPT,
        sleep_after=3,
    ),
    ("academic", "wikipedia"): route(
        "https://{lang}.wikipedia.org/w/api.php?action=query&list=search"
        "&srsearch={q}&format=json&srlimit={limit}&sroffset={offset}&utf8=1",
        parser="wikipedia",
        accept=JSON_ACCEPT,
    ),
    ("academic", "google"): route(
        "https://scholar.google.com/scholar?q={q}&start={start}&hl={lang}"
    ),
    ("academic", "bing"): route(
        "https://www.bing.com/search?q={q}+site%3Aarxiv.org"
        "&first={first}&count={limit}{time_filter}",
        bing_time=True,
    ),

    # ---- 代码 -----------------------------------------------------------
    ("code", "github"): route(
        "https://api.github.com/search/repositories?q={q}&per_page={limit}&page={page}",
        parser="github",
        accept=GITHUB_ACCEPT,
        headers={"X-GitHub-Api-Version": "2022-11-28"},
    ),
    ("code", "bing"): route(
        "https://www.bing.com/search?q={q}+site%3Agithub.com"
        "&first={first}&count={limit}{time_filter}",
        bing_time=True,
    ),
    ("code", "google"): route(
        "https://www.google.com/search?q={q}+site%3Agithub.com"
        "&start={start}&num={limit}&gbv=1"
    ),

    # ---- 地图 -----------------------------------------------------------
    ("map", "baidu"): route(
        "https://map.baidu.com/search/{qpath}"
    ),
    ("map", "bing"): route(
        "https://www.bing.com/maps?q={q}"
    ),
    ("map", "google"): route(
        "https://www.google.com/maps/search/{qpath}"
    ),
    ("map", "360"): route(
        "https://ditu.so.com/?k={q}"
    ),

    # ---- 购物 -----------------------------------------------------------
    ("shopping", "bing"): route(
        "https://www.bing.com/shop?q={q}&first={first}"
    ),
    ("shopping", "google"): route(
        "https://www.google.com/search?tbm=shop&q={q}&start={start}&gbv=1"
    ),
    ("shopping", "baidu"): route(
        "https://www.baidu.com/s?wd={q}&tn=shopping"
    ),
    ("shopping", "yahoo"): route(
        "https://shopping.yahoo.com/search?p={q}"
    ),

    # ---- 翻译 -----------------------------------------------------------
    ("translate", "bing"): route(
        "https://www.bing.com/translator?text={q}&from=auto&to={lang}"
    ),
    ("translate", "google"): route(
        "https://translate.google.com/?sl=auto&tl={lang}&text={q}&op=translate"
    ),
    ("translate", "baidu"): route(
        "https://fanyi.baidu.com/#auto/{lang}/{qpath}"
    ),

    # ---- RSS ------------------------------------------------------------
    ("rss", "bing"): route(
        "https://www.bing.com/search?q={q}&format=rss&first={first}&count={limit}{time_filter}",
        parser="rss",
        source="bing-rss",
        bing_time=True,
        accept=XML_ACCEPT,
    ),
    ("rss", "google"): route(
        "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en",
        parser="rss",
        source="google-news-rss",
        accept=XML_ACCEPT,
    ),
    ("rss", "yahoo"): route(
        "https://news.search.yahoo.com/rss?p={q}",
        parser="rss",
        source="yahoo-rss",
        accept=XML_ACCEPT,
    ),
}


def normalize_platform(value):
    value = (value or "").strip().lower()
    return PLATFORM_ALIASES.get(value, value)


def build_context(query, limit, page, time_range, lang, region, current_route):
    offset = (page - 1) * limit
    lang_base = lang.split(",")[0].split("-")[0].strip().lower() or "en"
    context = {
        "q": urllib.parse.quote_plus(query),
        "qpath": urllib.parse.quote(query, safe=""),
        "qraw": query,
        "limit": limit,
        "page": page,
        "first": (page - 1) * 10 + 1,
        "start": (page - 1) * 10,
        "pn": (page - 1) * 10,
        "offset": offset,
        "s": (page - 1) * 10,
        "p": page - 1,
        "kl": region or "wt-wt",
        "lang": lang_base,
        "time_filter": "",
    }
    if time_range and current_route.get("bing_time"):
        context["time_filter"] = "&qft=" + BING_TIME_FILTER[time_range]
    return context


def supported_platforms(type_name):
    return sorted(platform for (item_type, platform) in ROUTES if item_type == type_name)


async def fallback_route_generator(type_name, platform):
    """异步产出主通道失败后应尝试的备用路由。

    消费方可并发调度多个备用路由，取第一个返回 ok 的结果，从而
    将串行回退改为并发抓取，提升批量搜索效率。
    """
    for fb_type, fb_platform in FALLBACK_ROUTES.get((type_name, platform), []):
        yield fb_type, fb_platform
        # 让出控制权，允许并发调度器调度其他协程。
        await asyncio.sleep(0)
