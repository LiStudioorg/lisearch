# 解析、反爬与失败处理笔记

## 通用 HTML 粗抠

| 步骤 | 规则 |
|---|---|
| 预处理 | 删除 `script`、`style`、`noscript`、`svg`、`template`、`nav`、`header`、`footer` 区块 |
| 锚点正则 | `<a\b[^>]*href=["']([^"']+)["'][^>]*>(.*?)</a>`，忽略大小写、允许跨行 |
| 标题清洗 | 去标签、`html.unescape`、折叠连续空白，截断 300 字符 |
| 摘要清洗 | 锚文本作为摘要，截断 500 字符 |
| URL 清洗 | `&amp;` 反转义；协议相对地址补 `https:`；相对地址用 `urljoin` |
| 去重 | 按去掉 `#fragment` 的 URL 去重 |
| 跳过 | `#`、`javascript:`、`mailto:`、`tel:`、`data:`、`blob:`、含 `w3.org` 的链接 |
| 噪声过滤 | 登录、注册、首页、上一页、下一页、更多、设置、隐私、条款、about、privacy、terms、sign in 等 |
| 重定向拆包 | Google `/url?q=` 的 `q`；DuckDuckGo `/l/?uddg=` 的 `uddg` |

## 结构化通道

| 通道 | 解析方式 | 关键点 |
|---|---|---|
| `news/bing`、`rss/bing` | RSS `<item>` 正则 | 提取 `title`、`link`、`description`，兼容 CDATA |
| `rss/google`、`rss/yahoo` | RSS `<item>` 正则 | 同上；Google News 标题可能带来源后缀 |
| `academic/arxiv` | `xml.etree.ElementTree` | Atom 命名空间 `http://www.w3.org/2005/Atom` |
| `academic/wikipedia` | `json.loads` | `query.search`，摘要去 HTML 标签 |
| `code/github` | `json.loads` | 检查 `rate_limit.remaining` 与 `x-ratelimit-remaining` |

## captcha / 反爬识别关键词

| 类别 | 关键词（不区分大小写） |
|---|---|
| 英文 | `captcha`、`verify you are human`、`are you a robot`、`unusual traffic`、`access denied`、`robot check` |
| 中文 | `人机验证`、`安全验证`、`访问验证`、`请输入验证码`、`验证码` |
| 误报规避 | 若关键词本身出现在查询词中，则该次命中不单独判为 blocked |
| 检查范围 | 响应体前 50,000 字符 |

## HTTP 与失败分类

| 条件 | 分类 | 动作 |
|---|---|---|
| 200 且解析出结果 | `ok` | 写缓存，重置该引擎连续 blocked 计数 |
| HTTP 403 / 429 / 503 | `blocked` | blocked 计数 +1，返回搜索页 URL |
| 页面命中 captcha 关键词 | `blocked` | 同上 |
| GitHub 剩余额度 < 5 | `blocked` | 立即冷却 60 秒 |
| 200 但无结果/无可解析链接 | `empty` | 重置 blocked 计数，返回搜索页 URL |
| 网络错误、curl 非零、解析异常 | `error` | 返回搜索页 URL，stderr 记录原因 |
| 熔断冷却中 | `cooldown` | 不发起请求，返回剩余秒数与搜索页 URL |

## 熔断机制

| 项目 | 约定 |
|---|---|
| 状态键 | `(type, platform)`，如 `("web", "baidu")` |
| 触发条件 | 同一状态键连续 3 次 `blocked` |
| 冷却时长 | 60 秒 |
| 计数重置 | 任意成功响应（含 `empty`）重置连续计数 |
| 冷却期间 | 不请求上游；直接返回 `cooldown` |
| GitHub 特例 | 剩余额度 < 5 时立即熔断，不等连续 3 次 |
| 缓存优先级 | 先查 5 分钟 LRU 缓存，再判断熔断 |

## 空结果处理

| 场景 | 输出 |
|---|---|
| HTML 有内容但抠不出链接 | 单元素数组，`status="empty"`，`url` 为搜索页 |
| API 返回 0 条 | 同上 |
| 搜索页本身需要 JS | 同上，提示用浏览器打开 |
| 回退 URL | DuckDuckGo 使用 GET 浏览地址，其余使用实际请求 URL |
| 二次处理 | 可换平台重试；不要对同一平台连续重试超过 3 次 |

## 日志约定

| 项目 | 约定 |
|---|---|
| stdout | 只输出 JSON 数组，不允许调试文本 |
| stderr | 前缀 `[web-search]`，记录路由、缓存命中、blocked、冷却、解析异常 |
| 退出码 | `0`：ok/empty/blocked/cooldown；`1`：error；`2`：参数或组合错误 |
| 敏感信息 | 不记录 Cookie、Authorization；查询词可记录用于排障 |
| 失败可观测 | 每个非 ok 结果都保留 `source`、`status`、`fetched_at` 和可打开的 `url` |

## 缓存

| 项目 | 约定 |
|---|---|
| 数据结构 | 进程内 `OrderedDict` |
| 容量 | 128 条 |
| TTL | 300 秒 |
| 键 | 类型、平台、查询词、limit、page、time、site、lang、region |
| 命中 | 移动到队尾并返回副本；保留原始 `fetched_at` |
| 淘汰 | 超出容量时淘汰最久未使用项 |
