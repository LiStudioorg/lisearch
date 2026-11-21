---
name: web-search
description: 当需要实时网页、新闻、图片、视频、学术论文、代码仓库、地图、购物、翻译或 RSS 信息时触发；支持 bing/baidu/ddg/google/quark/sogou/360/yahoo/ecosia/arxiv/wikipedia/github 路由；零 API Key、零 pip 包，仅用 curl + python3 标准库。
---

# web-search

零依赖搜索技能：`curl` + `python3` 标准库 + `grep/sed`。所有结果统一为 JSON，失败时返回可手动打开的搜索页 URL。

## 决策树

| 查询类型 | 首选平台 | 降级平台 | 备注 |
|---|---|---|---|
| 中文网页 | `--type web --platform baidu` | bing → sogou → 360 | 百度对中文时效内容更好；被拦时换 bing |
| 英文网页 | `--type web --platform bing` | ddg → google → yahoo → ecosia | DDG 使用 POST，超时 10s |
| 新闻 | `--type news --platform bing` | baidu → google → sogou → 360 → yahoo | bing 走 RSS；`--time` 仅 bing 系列生效 |
| 图片 | `--type image --platform bing` | baidu → google → sogou → 360 → yahoo → ecosia | 图片直链可能需要浏览器打开 |
| 视频 | `--type video --platform bing` | baidu → google → sogou → 360 → yahoo | 多数视频页需浏览器播放 |
| 学术论文 | `--type academic --platform arxiv` | wikipedia → google → bing | arXiv 请求后固定 sleep 3s，勿并发轰炸 |
| 百科概念 | `--type academic --platform wikipedia` | bing → baidu | `--lang zh` 用中文维基，`--lang en` 用英文维基 |
| 代码仓库 | `--type code --platform github` | bing → google | GitHub 未登录搜索限额 10 次/分钟，剩余 <5 自动熔断 |
| 地图 / 购物 | `--type map --platform bing` / `--type shopping --platform bing` | baidu → google → sogou → 360 → yahoo | 结果页多为 JS，抠不出时直接用返回 URL |
| 翻译 | `--type translate --platform bing` | google → baidu | 主要返回翻译页 URL，不保证正文 |
| RSS / 订阅 | `--type rss --platform bing` | google → yahoo | bing/google/yahoo 均返回可解析 RSS |

同一类型可与任意已支持平台组合；未实现的组合会返回 `status: "error"` 并列出可用平台。

## 硬约束

1. 所有查询词、站点、路径片段必须 urlencode；禁止把原始输入裸拼进 URL。
2. 超时：DuckDuckGo 10 秒，其他平台 20 秒；`fetch.py` 25 秒。
3. `--limit` 范围 1–20，超出自动夹紧；`--page` 从 1 开始。
4. 需要登录态的平台（微博、知乎、小红书、抖音、B 站、淘宝、京东等）直接拒绝。
5. 回答必须带来源 URL；引用正文前先用 `fetch.py` 读取并核对原文。`fetch.py` 默认允许本机与局域网地址，需要严格公网模式时加 `--public-only`。
6. `--site` 只允许字母、数字、点、横线（`^[A-Za-z0-9.-]+$`）。
7. 不引入 API Key、第三方 pip 包或外部服务；stdout 只输出 JSON，日志与异常一律写 stderr。
8. 同一 `(type, platform)` 连续 5 次被 blocked 后冷却 120 秒；冷却期间返回 `status: "cooldown"`。LRU 缓存上限 256 条、TTL 600 秒。

## 执行入口

先探测环境（可选）：

```bash
bash scripts/probe.sh
```

搜索（在技能根目录执行）：

```bash
python3 core/search.py "量子计算" --type web --platform baidu --limit 8
python3 core/search.py "openai" --type news --platform bing --time w --limit 5
python3 core/search.py "transformer" --type academic --platform arxiv --limit 5
python3 core/search.py "rust async" --type code --platform github --limit 5
python3 core/search.py "site reliability" --type web --platform ddg --site example.com
python3 core/search.py "apple" --type image --platform bing --limit 6 --page 2
```

抓取结果正文：

```bash
python3 scripts/fetch.py "https://example.com/article"
```

## 参数

| 参数 | 取值 | 说明 |
|---|---|---|
| 位置参数 | 查询词 | 必填（`--list` 除外） |
| `--type` | `web image news video academic code map shopping translate rss` | 默认 `web` |
| `--platform` | 见决策树/`--list` | 默认随类型变化 |
| `--limit` | `1`–`20` | 默认 8 |
| `--page` | `>=1` | 默认 1；bing/百度/谷歌分页已适配 |
| `--time` | `d w m y` | 仅 bing 系列生效，映射为 `qft=interval_N` |
| `--site` | 域名 | 追加 ` site:域名` |
| `--lang` | 如 `zh-CN`、`en-US` | 影响 `Accept-Language`，也决定维基/翻译语言 |
| `--region` | 如 `cn-zh`、`us-en`、`wt-wt` | 仅 DuckDuckGo 的 `kl` 参数 |
| `--list` | 无 | 打印类型与可用平台 |

## 输出约定

成功时：

```json
[
  {
    "title": "标题",
    "url": "https://...",
    "snippet": "摘要或锚文本",
    "source": "bing",
    "status": "ok",
    "fetched_at": "2026-01-01T00:00:00Z"
  }
]
```

非 `ok` 时仍返回单元素数组，`url` 为搜索页地址，可直接在浏览器打开：

| status | 含义 | 建议动作 |
|---|---|---|
| `empty` | 无结果或 HTML 抠不出 | 打开 `url`，或换平台/换关键词 |
| `blocked` | 429/503、验证码、反爬页 | 换平台；同引擎连续 5 次会冷却 120s |
| `cooldown` | 熔断冷却中 | 等冷却结束或换平台 |
| `error` | 参数/组合/网络/解析错误 | 看 stderr；修正参数或换路由 |

## 资源指针

| 场景 | 读取 |
|---|---|
| 网页类 URL 模板、分页参数 | `references/platforms-web.md` |
| 图片/新闻/视频/地图/购物模板 | `references/platforms-media.md` |
| arXiv / Wikipedia / GitHub API 参数与节流 | `references/platforms-api.md` |
| 遇到 `blocked` / `empty`，或需要粗抠正则与日志约定 | `references/parsing-notes.md` |
| 首次部署、合规声明、文件清单 | `README.md` |

## 使用提示

- 先 `--limit 5` 小样本探测；确认平台可用后再放大。
- 中文查询优先 `baidu`，英文查询优先 `bing`/`ddg`；学术优先 `arxiv`，代码优先 `github`。
- 新闻/博客结论至少交叉两个来源；正文引用前用 `fetch.py` 验证。
- 不要并发循环调用同一 `(type, platform)`；命中缓存 5 分钟内不会重复请求。
