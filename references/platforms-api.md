# arXiv / Wikipedia / GitHub 结构化接口

这些通道优先于 HTML 粗抠；返回 JSON/Atom，可直接解析。

## arXiv Atom API

| 项目 | 值 |
|---|---|
| 端点 | `https://export.arxiv.org/api/query` |
| 查询参数 | `search_query=all:{q}` |
| 分页 | `start=(page-1)*limit`、`max_results=limit` |
| 排序 | `sortBy=relevance` |
| 格式 | Atom XML（`application/atom+xml`） |
| 超时 | 20 秒 |
| 节流 | 每次请求后固定 `sleep 3` 秒；不要并发 |

| Atom 字段 | 用途 |
|---|---|
| `entry/title` | 标题（折叠换行） |
| `entry/id` | 论文 URL（通常为 abs 页） |
| `entry/link[@href]` | `id` 缺失时的备用链接 |
| `entry/summary` | 摘要，截断到 500 字符 |
| `entry/published` | 可选，用于人工判断时效 |

## Wikipedia Search API

| 项目 | 值 |
|---|---|
| 端点 | `https://{lang}.wikipedia.org/w/api.php` |
| 查询参数 | `action=query`、`list=search`、`srsearch={q}` |
| 分页 | `srlimit=limit`、`sroffset=(page-1)*limit` |
| 格式 | `format=json`、`utf8=1` |
| 超时 | 20 秒 |
| 语言 | `--lang` 的基础码，如 `zh`、`en` |

| JSON 字段 | 用途 |
|---|---|
| `query.search[].title` | 条目标题 |
| `query.search[].snippet` | 摘要，含 `<span>`，需去标签 |
| `query.search[].pageid` | 可选，用于构造稳定链接 |
| 结果 URL | `https://{lang}.wikipedia.org/wiki/{quote(title)}` |

## GitHub Repository Search API

| 项目 | 值 |
|---|---|
| 端点 | `https://api.github.com/search/repositories` |
| 查询参数 | `q={q}`、`per_page=limit`、`page=page` |
| 请求头 | `Accept: application/vnd.github+json`、`X-GitHub-Api-Version: 2022-11-28` |
| 格式 | JSON |
| 超时 | 20 秒 |
| 未登录限额 | 搜索约 10 次/分钟（以响应头为准） |
| 熔断 | `rate_limit.remaining` 或 `x-ratelimit-remaining` < 5 时冷却 60 秒 |

| JSON 字段 | 用途 |
|---|---|
| `items[].full_name` | `owner/repo`，作为标题 |
| `items[].html_url` | 仓库网页地址 |
| `items[].description` | 摘要 |
| `items[].stargazers_count` | 可选，附加到摘要 |
| `rate_limit.remaining` | 剩余额度；缺失时读响应头 |
| `total_count` | 总命中数；为 0 视为 `empty` |

## 通用约定

| 项目 | 约定 |
|---|---|
| 失败分类 | HTTP 403/429/503、验证码页、额度不足 → `blocked`；空结果 → `empty`；解析异常 → `error` |
| 编码 | 查询词必须 urlencode；语言只取基础码放入 URL |
| 日志 | 异常写 stderr，stdout 只保留 JSON 数组 |
| 缓存 | 成功结果按完整参数缓存 300 秒，LRU 容量 128 |
| 合规 | 仅个人低频非商业用途；商用请使用各平台官方 API 与授权 |
