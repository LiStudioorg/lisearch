# -*- coding: utf-8 -*-
"""统一网络抓取模块。

特性：
    - 按 BOM、Content-Type、<meta charset> 自动识别字符集，避免中文乱码。
    - 优先提取 article/main 正文，并过滤导航、页脚、广告等噪声区块。
    - 默认放行本机回环与 RFC1918 局域网地址；默认拒绝 169.254.0.0/16 链路本地地址。
    - 支持 --public-only 切换到严格公网模式，--allow-link-local 显式放行链路本地。
    - 手动跟随重定向，每一跳都重新校验地址策略。

仅使用 curl 与 Python 标准库。错误信息写入 stderr。
"""

import argparse
import asyncio
import codecs
import html
import ipaddress
import os
import re
import socket
import subprocess
import sys
import tempfile
import urllib.parse
from html.parser import HTMLParser

from core.logger import debug, error as log_error, info


UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 25
DEFAULT_MAX_CHARS = 20000
MAX_REDIRECTS = 5
MAX_BYTES = 8000000

COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
CHARSET_RE = re.compile(r"charset\s*=\s*[\"']?\s*([A-Za-z0-9._:-]+)", re.IGNORECASE)
META_CHARSET_RE = re.compile(
    rb"<meta\b[^>]*charset\s*=\s*[\"']?\s*([A-Za-z0-9._:-]+)",
    re.IGNORECASE,
)
SPACE_RE = re.compile(r"[ \t\f\v\u00a0]+")
BLANK_RE = re.compile(r"\n[ \t]*\n(?:[ \t]*\n)+")
TAG_ONLY_RE = re.compile(r"<[^>]+>", re.DOTALL)

ARTICLE_RE = re.compile(r"<(article|main)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
MAIN_DIV_RE = re.compile(r"<div\b([^>]*)>(.*?)</div\s*>", re.IGNORECASE | re.DOTALL)
MAIN_ATTR_RE = re.compile(
    r"""(?:id|class)\s*=\s*["'][^"']*(?:article|content|main|post|entry|body-text)[^"']*["']""",
    re.IGNORECASE,
)

# 这些标签整体丢弃。
SKIP_TAGS = {
    "script",
    "style",
    "noscript",
    "svg",
    "iframe",
    "canvas",
    "template",
    "nav",
    "header",
    "footer",
    "aside",
    "form",
    "button",
    "select",
    "textarea",
    "option",
}

# 这些标签在输出中产生换行。
BLOCK_TAGS = {
    "p",
    "div",
    "section",
    "article",
    "main",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "tr",
    "br",
    "blockquote",
    "pre",
    "figure",
    "figcaption",
    "dt",
    "dd",
    "table",
    "thead",
    "tbody",
    "ul",
    "ol",
    "hr",
}

# 无结束标签的元素不进入跳过栈。
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}

# 通过 class/id/role 识别噪声容器。
NOISE_ATTR_RE = re.compile(
    r"(?:^|[\s_-])(?:nav|navbar|navigation|menu|footer|sidebar|side|aside|"
    r"comment|comments|advert|advertisement|ads?|banner|breadcrumb|copyright|"
    r"share|sharing|social|related|recommend|pagination|pager|login|signup|"
    r"subscribe|newsletter|popup|modal|cookie|toolbar|widget|sponsor|promo)"
    r"(?:$|[\s_-])",
    re.IGNORECASE,
)
NOISE_ROLES = {"navigation", "banner", "contentinfo", "complementary", "search"}

# 常见字符集别名，统一到 Python 可识别的名称。
ENCODING_ALIASES = {
    "gb2312": "gb18030",
    "gbk": "gb18030",
    "gb_2312-80": "gb18030",
    "utf8": "utf-8",
    "unicode-1-1-utf-8": "utf-8",
}
LEGACY_WESTERN = {"iso-8859-1", "latin-1", "latin1", "windows-1252", "cp1252"}

# 地址策略（SSRF 防护，默认放行本机与局域网）
LOCAL_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".home", ".lan")
LOCAL_HOSTS = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}


def log(message, level="INFO"):
    """统一日志入口，支持 DEBUG/INFO/ERROR 级别。"""
    if level == "DEBUG":
        debug("fetch", message)
    elif level == "ERROR":
        log_error("fetch", message)
    else:
        info("fetch", message)


# ---------------------------------------------------------------------------
# 字符集识别
# ---------------------------------------------------------------------------

def normalize_encoding(name):
    """把常见字符集别名转换为 Python codec 名称。"""
    name = (name or "").strip().strip("\"'").lower()
    name = ENCODING_ALIASES.get(name, name)
    if not name:
        return ""
    try:
        codecs.lookup(name)
        return name
    except LookupError:
        return ""


def can_decode(data, encoding):
    try:
        data.decode(encoding)
        return True
    except (UnicodeDecodeError, LookupError):
        return False


def detect_encoding(data, content_type=""):
    """按 BOM、响应头、meta 标签的顺序推断字符集。"""
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"

    declared = []
    match = CHARSET_RE.search(content_type or "")
    if match:
        declared.append(match.group(1))
    head = data[:8192]
    match = META_CHARSET_RE.search(head)
    if match:
        declared.append(match.group(1).decode("ascii", "ignore"))

    for raw in declared:
        encoding = normalize_encoding(raw)
        if not encoding or not can_decode(data, encoding):
            continue
        # 服务器常把 UTF-8 页面误标成 latin-1，优先按 UTF-8 解码。
        if encoding in LEGACY_WESTERN and can_decode(data, "utf-8"):
            return "utf-8"
        return encoding

    if can_decode(data, "utf-8"):
        return "utf-8"
    for encoding in ("gb18030", "big5", "shift_jis", "euc-kr"):
        if can_decode(data, encoding):
            return encoding
    return "utf-8"


# ---------------------------------------------------------------------------
# 地址策略（SSRF 防护）
# ---------------------------------------------------------------------------

def _address_kind(ip):
    """把地址分为公网、回环、私网、链路本地和应拒绝的类别。"""
    if ip.is_global:
        return "public"
    if ip.is_link_local:
        return "link_local"
    if ip.is_loopback:
        return "loopback"
    if ip.is_private:
        return "private"
    return "blocked"


def validate_url(url, public_only=False, allow_link_local=False):
    """校验 URL 的地址策略；返回空字符串表示允许。

    默认放行本机回环与 RFC1918 局域网地址，方便抓取本地服务；
    默认拒绝 169.254.0.0/16 链路本地地址，降低云元数据 SSRF 风险。
    public_only=True 时退回严格的“仅公网”模式。
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError as exc:
        return "无效 URL: %s" % exc
    if parsed.scheme not in ("http", "https"):
        return "仅允许 http 和 https"
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        return "URL 无主机名"

    local_name = host in LOCAL_HOSTS or host.endswith(LOCAL_HOST_SUFFIXES)

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None

    if literal is not None:
        kind = _address_kind(literal)
        if kind == "public":
            return ""
        if kind in ("loopback", "private") and not public_only:
            return ""
        if kind == "link_local" and allow_link_local and not public_only:
            return ""
        return "拒绝非公网地址：%s（%s）" % (host, kind)

    if local_name and not public_only:
        return ""

    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        return "无效端口：%s" % exc
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        return "无法解析主机 %s：%s" % (host, exc)
    if not infos:
        return "无法解析主机 %s" % host

    addresses = {info[4][0].split("%", 1)[0] for info in infos}
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return "意外地址 %s 对应的主机 %s" % (address, host)
        kind = _address_kind(ip)
        if kind == "public":
            continue
        if kind in ("loopback", "private") and not public_only:
            continue
        if kind == "link_local" and allow_link_local and not public_only:
            continue
        return "拒绝主机 %s：其解析为 %s 地址 %s" % (host, kind, address)
    return ""


# ---------------------------------------------------------------------------
# HTTP 请求
# ---------------------------------------------------------------------------

def curl_once(url, timeout, headers=None, method="GET", data=None):
    """请求单个 URL，不自动跟随重定向。

    返回 (HTTP 状态码, Content-Type, 响应头字典, 响应字节, 错误信息)。
    """
    body_fd, body_path = tempfile.mkstemp(prefix="webfetch-body-")
    head_fd, head_path = tempfile.mkstemp(prefix="webfetch-head-")
    os.close(body_fd)
    os.close(head_fd)

    cmd = [
        "curl",
        "-sS",
        "--compressed",
        "--max-time",
        str(timeout),
        "--max-filesize",
        str(MAX_BYTES),
        "--proto",
        "=http,https",
        "-A",
        UA,
        "-o",
        body_path,
        "-D",
        head_path,
        "-w",
        "%{http_code}\t%{content_type}\t%{redirect_url}",
    ]

    for name, value in (headers or {}).items():
        cmd.extend(["-H", "%s: %s" % (name, value)])

    if method.upper() == "POST":
        cmd.extend(["-X", "POST"])
        if data:
            cmd.extend(["--data", urllib.parse.urlencode(data)])

    cmd.append(url)

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout + 5,
            text=True,
        )
        parts = proc.stdout.rstrip("\n").split("\t")
        code = int(parts[0]) if parts and parts[0].isdigit() else 0
        content_type = parts[1] if len(parts) > 1 else ""
        redirect_url = parts[2] if len(parts) > 2 else ""

        try:
            with open(body_path, "rb") as fh:
                body = fh.read(MAX_BYTES + 1)
        except OSError as exc:
            return code, content_type, {}, b"", "无法读取响应体：%s" % exc

        if len(body) > MAX_BYTES:
            return code, content_type, {}, b"", "响应超过 %s 字节" % MAX_BYTES

        response_headers = {}
        try:
            with open(head_path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if ":" in line:
                        name, value = line.split(":", 1)
                        response_headers[name.strip().lower()] = value.strip()
        except OSError:
            pass

        if proc.returncode != 0:
            detail = proc.stderr.strip() or "curl 退出码 %d" % proc.returncode
            log("curl 非零退出 %s：%s" % (url, detail), level="ERROR")
            return code, content_type, response_headers, body, detail

        return code, content_type, response_headers, body, ""
    except subprocess.TimeoutExpired:
        log("curl 超时 %s（%s 秒）" % (url, timeout), level="ERROR")
        return 0, "", {}, b"", "curl 超时后 %s 秒" % timeout
    except OSError as exc:
        log("无法运行 curl %s：%s" % (url, exc), level="ERROR")
        return 0, "", {}, b"", "无法运行 curl：%s" % exc
    except Exception as exc:  # 防御性处理：任何异常都不能破坏调用方
        log("意外获取错误 %s：%s" % (url, exc), level="ERROR")
        return 0, "", {}, b"", "意外获取错误：%s" % exc
    finally:
        for path in (body_path, head_path):
            try:
                os.unlink(path)
            except OSError:
                pass


def http_fetch(url, timeout, public_only=False, allow_link_local=False,
               headers=None, method="GET", data=None):
    """逐跳校验并跟随重定向。

    返回 (状态码, Content-Type, 响应头字典, 响应字节, 错误信息)。
    """
    current = url
    req_headers = dict(headers or {})
    debug("fetch", "HTTP %s %s (timeout=%ss)" % (method.upper(), url, timeout))
    for _ in range(MAX_REDIRECTS + 1):
        reason = validate_url(current, public_only, allow_link_local)
        if reason:
            log("SSRF 拒绝 %s：%s" % (url, reason), level="ERROR")
            return 0, "", {}, b"", reason
        code, content_type, response_headers, body, error = curl_once(
            current, timeout, headers=req_headers, method=method, data=data
        )
        if error:
            return code, content_type, response_headers, body, error
        location = response_headers.get("location") or ""
        if code in (301, 302, 303, 307, 308) and location:
            current = urllib.parse.urljoin(current, location)
            # 301/302/303 将 POST 降级为 GET，模拟浏览器/curl 默认行为。
            if code in (301, 302, 303) and method.upper() == "POST":
                method = "GET"
                data = None
                req_headers.pop("Content-Length", None)
                req_headers.pop("Content-Type", None)
            continue
        debug("fetch", "HTTP %s %s -> %d (%d bytes)" % (
            method.upper(), url, code, len(body)))
        return code, content_type, response_headers, body, ""
    return 0, "", {}, b"", "重定向过多（上限 %s 次）" % MAX_REDIRECTS


# ---------------------------------------------------------------------------
# 正文提取
# ---------------------------------------------------------------------------

class TextExtractor(HTMLParser):
    """基于 HTMLParser 的正文文本提取器，可跳过噪声子树。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip_stack = []

    @staticmethod
    def _is_noise(attrs):
        for key, value in attrs:
            key = (key or "").lower()
            value = (value or "").lower()
            if key == "role" and value in NOISE_ROLES:
                return True
            if key == "aria-hidden" and value == "true":
                return True
            if key in ("class", "id") and NOISE_ATTR_RE.search(value):
                return True
        return False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if self.skip_stack:
            if tag not in VOID_TAGS:
                self.skip_stack.append(tag)
            return
        if tag in SKIP_TAGS or self._is_noise(attrs):
            if tag not in VOID_TAGS:
                self.skip_stack.append(tag)
            return
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if self.skip_stack:
            return
        if tag in SKIP_TAGS or self._is_noise(attrs):
            return
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.skip_stack:
            if tag in VOID_TAGS:
                return
            if tag in self.skip_stack:
                index = len(self.skip_stack) - 1 - self.skip_stack[::-1].index(tag)
                del self.skip_stack[index:]
            return
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.skip_stack:
            return
        if data:
            self.parts.append(data)


def pick_main_html(body):
    """优先挑出 article/main 或正文容器，挑不到则返回原始 HTML。"""
    candidates = [match.group(0) for match in ARTICLE_RE.finditer(body)]
    if candidates:
        lengths = [len(TAG_ONLY_RE.sub("", chunk)) for chunk in candidates]
        longest = max(lengths)
        second = sorted(lengths)[-2] if len(lengths) > 1 else 0
        # 单个长正文旁若只有很短的推荐卡片，只保留主正文；
        # 多个篇幅接近的 article 视为列表页，全部保留。
        if longest >= 500 and longest >= max(1, second) * 3:
            return candidates[lengths.index(longest)]
        return "\n".join(candidates)

    best = ""
    best_length = 0
    for match in MAIN_DIV_RE.finditer(body):
        if not MAIN_ATTR_RE.search(match.group(1)):
            continue
        length = len(TAG_ONLY_RE.sub("", match.group(2)))
        if length > best_length:
            best = match.group(0)
            best_length = length
    return best or body


def finish_text(parts, max_chars):
    """折叠空白、去掉多余空行，并按上限截断。"""
    text = "".join(parts)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    text = SPACE_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = BLANK_RE.sub("\n\n", text).strip()

    if max_chars > 0 and len(text) > max_chars:
        text = text[:max_chars].rsplit("\n", 1)[0].rstrip()
        text += "\n\n[内容已截断]"
    return text


def html_to_text(body, max_chars=DEFAULT_MAX_CHARS):
    """把 HTML 字符串或字节转成可读纯文本。"""
    if isinstance(body, (bytes, bytearray)):
        body = bytes(body).decode(detect_encoding(bytes(body)), errors="replace")
    body = COMMENT_RE.sub(" ", body)
    body = pick_main_html(body)
    parser = TextExtractor()
    parser.feed(body)
    parser.close()
    return finish_text(parser.parts, max_chars)


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="fetch.py",
        description="Fetch a URL and print cleaned main-content text.",
    )
    parser.add_argument("url", help="http or https URL")
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="per-request curl timeout in seconds (default: 25)",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help="maximum output characters, 0 means unlimited (default: 20000)",
    )
    parser.add_argument(
        "--public-only",
        action="store_true",
        default=os.environ.get("WEB_FETCH_PUBLIC_ONLY", "").lower()
        in ("1", "true", "yes", "on"),
        help="only allow public internet addresses (strict SSRF mode)",
    )
    parser.add_argument(
        "--allow-link-local",
        action="store_true",
        default=os.environ.get("WEB_FETCH_ALLOW_LINK_LOCAL", "").lower()
        in ("1", "true", "yes", "on"),
        help="allow 169.254.0.0/16 link-local addresses",
    )
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    reason = validate_url(args.url, args.public_only, args.allow_link_local)
    if reason:
        log("拒绝 URL：%s" % reason)
        return 2

    code, content_type, response_headers, body, error = http_fetch(
        args.url,
        max(1, args.timeout),
        public_only=args.public_only,
        allow_link_local=args.allow_link_local,
    )
    if error:
        log("抓取失败：%s" % error)
        return 1
    if code < 200 or code >= 300:
        log("抓取失败：HTTP %s" % code)
        return 1

    encoding = detect_encoding(body, content_type)
    text = html_to_text(body, max_chars=max(0, args.max_chars))
    if not text:
        log("从 %s 未提取到可读文本" % args.url)
        return 1

    log("按 %s 解码，%d 字符" % (encoding, len(text)))
    print(text)
    return 0


# ---------------------------------------------------------------------------
# 异步并发抓取（asyncio + 线程池，零外部依赖）
# ---------------------------------------------------------------------------

async def async_http_fetch(url, timeout, public_only=False,
                           allow_link_local=False, headers=None,
                           method="GET", data=None):
    """在线程池中并发执行阻塞的 http_fetch。

    curl 本身是阻塞子进程，通过 asyncio.to_thread 将多个请求并发
    提交，适合回退路由并发抓取或批量 URL 获取。
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, http_fetch, url, timeout, public_only, allow_link_local,
        headers, method, data,
    )


async def async_fetch_many(requests, max_concurrency=5):
    """并发获取多个 URL。

    requests: 列表，每项为 (url, kwargs_dict)。
    返回与 requests 同序的 (code, content_type, headers, body, error) 元组列表。
    """
    semaphore = asyncio.Semaphore(max_concurrency)

    async def fetch_one(req):
        url, kwargs = req
        async with semaphore:
            return await async_http_fetch(url, **kwargs)

    return await asyncio.gather(*[fetch_one(req) for req in requests])


if __name__ == "__main__":
    sys.exit(main())
