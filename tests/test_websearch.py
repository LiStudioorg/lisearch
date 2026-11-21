#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""web-search 技能的单元测试套件（unittest）。

覆盖：路由表、持久化缓存、熔断器、字符集检测、正文提取、截断、
回退路由、解析器、拦截检测、执行状态与参数校验。

运行方式（在技能根目录执行）：
    python -m pytest tests/
    python -m unittest tests.test_websearch
"""

import argparse
import json
import os
import sys
import tempfile
import unittest
import urllib.parse
from collections import OrderedDict
from unittest.mock import patch

# 将技能根目录加入 sys.path，以便直接 import core 包。
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 让持久化状态写入临时文件，避免污染用户缓存目录，并保证可写。
_tmp_db = tempfile.mktemp(suffix=".sqlite3")
os.environ["WEB_SEARCH_STATE_DB"] = _tmp_db
os.environ["WEB_SEARCH_STATE_DIR"] = os.path.dirname(_tmp_db)
os.environ["WEB_SEARCH_DISABLE_PERSIST"] = ""

from core.cache import (  # noqa: E402
    BREAKER_COOLDOWN, BREAKERS, CACHE, BlockedError, QuotaError,
    breaker_remaining, cache_get, cache_put, force_cooldown,
    record_blocked, record_success, CACHE_MAX, CACHE_TTL,
)
from core.fetcher import detect_encoding, html_to_text, validate_url  # noqa: E402
from core.router import (  # noqa: E402
    FALLBACK_ROUTES, ROUTES, build_context, normalize_platform,
    supported_platforms,
)
import core.search as search  # noqa: E402


def make_args(**kw):
    base = dict(
        query="test query",
        type="web",
        platform="bing",
        limit=5,
        page=1,
        time_range=None,
        site=None,
        lang=None,
        region=None,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def sample_rss():
    return """<?xml version="1.0"?><rss><channel>
    <item><title><![CDATA[First &amp; Best]]></title>
      <link>https://example.com/a?x=1&amp;y=2</link>
      <description><![CDATA[Hello <b>world</b>]]></description></item>
    <item><title>Second</title><link>https://example.com/b</link>
      <description>Two</description></item>
    </channel></rss>"""


def sample_atom():
    return """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry><title>Paper One</title><id>http://arxiv.org/abs/1</id>
        <summary>Abstract one</summary></entry>
      <entry><title>Paper Two</title><id>http://arxiv.org/abs/2</id>
        <summary>Abstract two</summary></entry>
    </feed>"""


def sample_wiki():
    return json.dumps({
        "query": {"search": [
            {"title": "Transformer", "snippet": '<span class="searchmatch">Transformer</span> is a thing'},
            {"title": "Attention", "snippet": "Attention &amp; stuff"},
        ]}
    })


def sample_github(remaining=10):
    return json.dumps({
        "total_count": 2,
        "rate_limit": {"remaining": remaining},
        "items": [
            {"full_name": "a/b", "html_url": "https://github.com/a/b", "description": "Repo B", "stargazers_count": 12},
            {"full_name": "c/d", "html_url": "https://github.com/c/d", "description": None, "stargazers_count": 3},
        ],
    })


class TestRouteTable(unittest.TestCase):
    def test_route_count(self):
        self.assertGreaterEqual(len(ROUTES), 45, str(len(ROUTES)))

    def test_all_routes_format(self):
        for (type_name, platform), route in sorted(ROUTES.items()):
            ctx = build_context(
                "a b&c/中文", 7, 2,
                "w" if route.get("bing_time") else None,
                "zh-CN", "cn-zh", route,
            )
            url = route["url"].format(**ctx)
            self.assertNotIn(" ", url, url)
            self.assertNotIn("{", url, url)
            self.assertNotIn("}", url, url)
            if route.get("data"):
                data = {k: v.format(**ctx) for k, v in route["data"].items()}
                encoded = urllib.parse.urlencode(data)
                self.assertIn("a+b%26c", encoded, encoded)

    def test_default_routes_exist(self):
        self.assertTrue(
            all((t, p) in ROUTES for t, p in search.DEFAULT_PLATFORM.items())
        )


class TestCache(unittest.TestCase):
    def setUp(self):
        CACHE.clear()
        BREAKERS.clear()

    def tearDown(self):
        CACHE.clear()
        BREAKERS.clear()

    def test_lru_capacity_and_eviction(self):
        for i in range(CACHE_MAX + 1):
            cache_put(("k", i), [{"n": i}])
        self.assertEqual(len(CACHE), CACHE_MAX, str(len(CACHE)))
        self.assertIsNone(cache_get(("k", 0)))
        self.assertIsNotNone(cache_get(("k", 1)))

    def test_ttl_expiry(self):
        import core.cache as cache_mod
        cache_put(("k", 1), [{"n": 1}])
        old_ttl = cache_mod.CACHE_TTL
        cache_mod.CACHE_TTL = 0
        try:
            self.assertIsNone(cache_get(("k", 1)))
        finally:
            cache_mod.CACHE_TTL = old_ttl

    def test_persistent_cache_cross_process(self):
        # 模拟新进程：仅清空进程内 L1，持久化层保留。
        key = ("persist", "cache")
        cache_put(key, [{"n": 1}])
        OrderedDict.clear(CACHE)
        self.assertIsNotNone(cache_get(key))
        CACHE.clear()


class TestBreaker(unittest.TestCase):
    def setUp(self):
        CACHE.clear()
        BREAKERS.clear()

    def tearDown(self):
        CACHE.clear()
        BREAKERS.clear()

    def test_breaker_opens_at_five(self):
        # 阈值为 5：前 4 次不触发冷却，第 5 次触发。
        key = ("web", "bing")
        for _ in range(4):
            record_blocked(key)
        self.assertEqual(breaker_remaining(key), 0, str(breaker_remaining(key)))
        record_blocked(key)
        self.assertGreater(breaker_remaining(key), 0)
        BREAKERS.clear()

    def test_breaker_cooldown_120s(self):
        key = ("web", "bing")
        for _ in range(5):
            record_blocked(key)
        remain = breaker_remaining(key)
        self.assertGreaterEqual(remain, 1, str(remain))
        self.assertLessEqual(remain, BREAKER_COOLDOWN, str(remain))
        BREAKERS.clear()

    def test_force_cooldown(self):
        key = ("web", "bing")
        force_cooldown(key)
        self.assertGreater(breaker_remaining(key), 0)
        BREAKERS.clear()

    def test_persistent_breaker_cross_process(self):
        key = ("persist", "breaker")
        for _ in range(5):
            record_blocked(key)
        # 模拟新进程：仅清空进程内 L1。
        dict.clear(BREAKERS)
        # 再记录一次应保持冷却（来自 SQLite）。
        remain_before = breaker_remaining(key)
        dict.clear(BREAKERS)
        self.assertGreater(remain_before, 0)
        BREAKERS.clear()


class TestParsers(unittest.TestCase):
    def test_rss(self):
        ctx = build_context("test", 5, 1, None, "en-US", "wt-wt", ROUTES[("web", "bing")])
        items = search.parse_rss(sample_rss(), None, ctx)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "First & Best")
        self.assertEqual(items[0]["url"], "https://example.com/a?x=1&y=2")
        self.assertEqual(items[0]["snippet"], "Hello world")

    def test_arxiv(self):
        ctx = build_context("test", 5, 1, None, "en-US", "wt-wt", ROUTES[("web", "bing")])
        items = search.parse_arxiv(sample_atom(), None, ctx)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "Paper One")
        self.assertEqual(items[0]["url"], "http://arxiv.org/abs/1")

    def test_wikipedia(self):
        wiki_ctx = build_context(
            "test", 5, 1, None, "en-US", "wt-wt", ROUTES[("academic", "wikipedia")]
        )
        items = search.parse_wikipedia(sample_wiki(), None, wiki_ctx)
        self.assertEqual(len(items), 2)
        self.assertNotIn("<span", items[0]["snippet"])
        self.assertEqual(items[0]["url"], "https://en.wikipedia.org/wiki/Transformer")

    def test_github_ok(self):
        ctx = build_context("test", 5, 1, None, "en-US", "wt-wt", ROUTES[("web", "bing")])
        items = search.parse_github(sample_github(10), None, ctx)
        self.assertEqual(len(items), 2)
        self.assertIn("12", items[0]["snippet"])

    def test_github_quota_body(self):
        ctx = build_context("test", 5, 1, None, "en-US", "wt-wt", ROUTES[("web", "bing")])
        with self.assertRaises(QuotaError):
            search.parse_github(sample_github(4), None, ctx)

    def test_github_quota_header(self):
        ctx = build_context("test", 5, 1, None, "en-US", "wt-wt", ROUTES[("web", "bing")])
        with self.assertRaises(QuotaError):
            search.parse_github(
                sample_github(100), None, ctx, {"x-ratelimit-remaining": "3"}
            )

    def test_generic(self):
        html_doc = (
            "<html><nav><a href=\"/login\">登录</a></nav>"
            "<h2><a href=\"/r/1\">Result One</a></h2><p>This is a substantial snippet for result one.</p>"
            "<h3><a href=\"https://w3.org/TR/x\">W3 Spec</a></h3>"
            "<h2><a href=\"https://www.google.com/url?q=https%3A%2F%2Fexample.com%2Fa%3Fx%3D1&sa=U\">Result Two</a></h2>"
            "<p>Second result snippet text long enough to pass.</p>"
            "<h2><a href=\"/r/1\">Result One Longer</a></h2>"
            "</html>"
        )
        ctx = build_context("test", 5, 1, None, "en-US", "wt-wt", ROUTES[("web", "bing")])
        items = search.parse_generic(html_doc, None, ctx, "https://search.example/")
        self.assertEqual(len(items), 2, str(items))
        self.assertEqual(items[0]["url"], "https://search.example/r/1")
        self.assertEqual(items[1]["url"], "https://example.com/a?x=1")
        self.assertTrue(all("w3.org" not in x["url"] for x in items))
        self.assertEqual(sum(1 for x in items if "r/1" in x["url"]), 1)
        self.assertEqual(items[0]["title"], "Result One Longer")
        self.assertIn("substantial", items[0]["snippet"])


class TestBlockedDetection(unittest.TestCase):
    def test_captcha_block(self):
        self.assertTrue(search.looks_blocked("<title>百度安全验证</title>", "openai"))

    def test_query_captcha_exception(self):
        self.assertFalse(search.looks_blocked("<h1>captcha</h1>", "captcha tutorial"))

    def test_normal_page(self):
        self.assertFalse(search.looks_blocked("<html>results</html>", "openai"))


class TestExecuteStatuses(unittest.TestCase):
    def _run(self, fake, **kw):
        real = search.http_fetch
        try:
            with patch("core.search.http_fetch", new=fake):
                return search.execute(make_args(**kw))
        finally:
            search.http_fetch = real

    def test_ok(self):
        def fake_ok(url, **kw):
            return 200, "", {}, b'<h2><a href="https://example.com/x">Example X</a></h2><p>A sufficiently long snippet for the example result.</p>', ""
        res, code = self._run(fake_ok)
        self.assertEqual(code, 0)
        self.assertEqual(res[0]["status"], "ok")
        self.assertEqual(res[0]["source"], "bing")

    def test_429_blocked(self):
        def fake_429(url, **kw):
            return 429, "", {}, b"", ""
        res, code = self._run(fake_429)
        self.assertEqual(code, 0)
        self.assertEqual(res[0]["status"], "blocked")

    def test_captcha_blocked(self):
        def fake_captcha(url, **kw):
            return 200, "", {}, (
                "<html><title>安全验证</title>captcha</html>"
            ).encode("utf-8"), ""
        res, code = self._run(fake_captcha)
        self.assertEqual(code, 0)
        self.assertEqual(res[0]["status"], "blocked")

    def test_empty(self):
        def fake_empty(url, **kw):
            return 200, "", {}, b"<html><body>nothing here</body></html>", ""
        res, code = self._run(fake_empty)
        self.assertEqual(code, 0)
        self.assertEqual(res[0]["status"], "empty")

    def test_network_error(self):
        def fake_error(url, **kw):
            return 0, "", {}, b"", "curl timed out"
        res, code = self._run(fake_error)
        self.assertEqual(code, 1)
        self.assertEqual(res[0]["status"], "error")

    def test_cache_reuse(self):
        calls = []
        def fake_capture(url, **kw):
            calls.append(url)
            return 200, "", {}, b'<h2><a href="https://example.com/x">Example X</a></h2><p>A sufficiently long snippet for the example result.</p>', ""
        search.CACHE.clear()
        search.BREAKERS.clear()
        try:
            with patch("core.search.http_fetch", new=fake_capture):
                search.execute(make_args(query="cache test", platform="bing"))
                search.execute(make_args(query="cache test", platform="bing"))
            self.assertEqual(len(calls), 1, str(len(calls)))
        finally:
            search.CACHE.clear()
            search.BREAKERS.clear()

    def test_ddg_post(self):
        calls = []
        def fake_capture(url, **kw):
            calls.append(kw)
            return 200, "", {}, b"", ""
        search.CACHE.clear()
        search.BREAKERS.clear()
        try:
            with patch("core.search.http_fetch", new=fake_capture):
                search.execute(make_args(type="web", platform="ddg", region="us-en", limit=3))
            kw = calls[-1]
            self.assertEqual(kw["method"], "POST")
            self.assertIsInstance(kw["data"], dict)
            self.assertEqual(kw["data"]["kl"], "us-en")
            self.assertEqual(kw["timeout"], 10)
        finally:
            search.CACHE.clear()
            search.BREAKERS.clear()

    def test_fallback_route(self):
        rss_body = (
            '<?xml version="1.0"?><rss><channel>'
            '<item><title>Fallback News</title>'
            '<link>https://example.com/fallback</link>'
            '<description>Fallback snippet.</description></item>'
            '</channel></rss>'
        )

        def fake_fetch(url, **kw):
            if "bing.com/news" in url:
                # Bing 新闻主通道返回空 HTML，触发回退。
                return 200, "text/html", {}, b"<html><body>nothing</body></html>", ""
            if "news.google.com" in url:
                return 200, "application/rss+xml", {}, rss_body.encode("utf-8"), ""
            return 200, "", {}, b"", ""

        search.CACHE.clear()
        search.BREAKERS.clear()
        try:
            with patch("core.search.http_fetch", new=fake_fetch):
                res, code = search.execute(make_args(type="news", platform="bing", limit=3))
            self.assertEqual(code, 0)
            self.assertEqual(res[0]["status"], "ok")
            self.assertEqual(res[0]["source"], "google-news-rss")
            self.assertEqual(res[0]["title"], "Fallback News")
        finally:
            search.CACHE.clear()
            search.BREAKERS.clear()

    def test_github_cooldown(self):
        """GitHub 额度不足应触发强制熔断并返回 cooldown 状态。"""
        def fake_github(url, **kw):
            body = json.dumps({
                "total_count": 1,
                "rate_limit": {"remaining": 2},
                "items": [],
            }).encode("utf-8")
            return 200, "application/json", {}, body, ""

        search.CACHE.clear()
        search.BREAKERS.clear()
        try:
            with patch("core.search.http_fetch", new=fake_github):
                # 第 1 次：触发 QuotaError -> force_cooldown，返回 blocked。
                res1, code1 = search.execute(
                    make_args(type="code", platform="github", limit=3)
                )
                self.assertEqual(code1, 0)
                self.assertEqual(res1[0]["status"], "blocked")
                # 熔断器已开启，再次调用应直接返回 cooldown（不再触发网络请求）。
                res2, code2 = search.execute(
                    make_args(type="code", platform="github", limit=3)
                )
                self.assertEqual(code2, 0)
                self.assertEqual(res2[0]["status"], "cooldown")
                self.assertIn("冷却", res2[0]["snippet"])
        finally:
            search.CACHE.clear()
            search.BREAKERS.clear()


class TestValidation(unittest.TestCase):
    def test_unsupported_route(self):
        res, code = search.execute(make_args(query="x", type="map", platform="github"))
        self.assertEqual(code, 2)
        self.assertEqual(res[0]["status"], "error")

    def test_bad_site(self):
        res, code = search.execute(make_args(query="x", site="bad site!"))
        self.assertEqual(code, 2)
        self.assertEqual(res[0]["status"], "error")

    def test_denied_platform(self):
        res, code = search.execute(make_args(query="x", platform="zhihu"))
        self.assertEqual(code, 2)
        self.assertEqual(res[0]["status"], "error")

    def test_bad_lang(self):
        res, code = search.execute(make_args(query="x", lang="en\r\nX-Test: 1"))
        self.assertEqual(code, 2)
        self.assertEqual(res[0]["status"], "error")

    def test_bad_region(self):
        res, code = search.execute(make_args(query="x", region="us en"))
        self.assertEqual(code, 2)
        self.assertEqual(res[0]["status"], "error")

    def test_empty_query(self):
        res, code = search.execute(make_args(query="   "))
        self.assertEqual(code, 2)
        self.assertEqual(res[0]["status"], "error")


class TestFetcher(unittest.TestCase):
    def test_charset_gbk(self):
        gbk_html = (
            "<html><head><meta charset='gb2312'></head><body><article>"
            "<nav>导航噪声</nav><p>中文正文内容</p><footer>页脚噪声</footer>"
            "</article></body></html>"
        ).encode("gb18030")
        text = html_to_text(gbk_html)
        self.assertIn("中文正文内容", text)
        self.assertNotIn("导航噪声", text)
        self.assertNotIn("页脚噪声", text)

    def test_utf8_meta(self):
        html_doc = (
            "<html><head><meta charset='utf-8'></head><body><article>"
            "<h1>标题</h1><p>正文内容，用于测试字符集自动识别。</p>"
            "</article></body></html>"
        ).encode("utf-8")
        text = html_to_text(html_doc)
        self.assertIn("正文内容", text)

    def test_truncation(self):
        long_text = html_to_text("<main><p>" + "字" * 200 + "</p></main>", max_chars=40)
        self.assertIn("内容已截断", long_text)
        self.assertLess(len(long_text), 80)

    def test_ssrf_default_allows_loopback_and_private(self):
        self.assertEqual(validate_url("http://127.0.0.1/"), "")
        self.assertEqual(validate_url("http://10.0.0.5/"), "")
        self.assertEqual(validate_url("https://8.8.8.8/"), "")

    def test_ssrf_rejects_link_local(self):
        self.assertNotEqual(validate_url("http://169.254.169.254/"), "")

    def test_ssrf_public_only_rejects_loopback(self):
        self.assertNotEqual(validate_url("http://127.0.0.1/", public_only=True), "")

    def test_detect_encoding_utf8_bom(self):
        self.assertEqual(detect_encoding(b"\xef\xbb\xbf<html>"), "utf-8-sig")

    def test_detect_encoding_meta_gbk(self):
        data = b"<head><meta charset='gbk'></head><body>\xd6\xd0\xce\xc4</body>"
        self.assertEqual(detect_encoding(data), "gb18030")


if __name__ == "__main__":
    unittest.main()
