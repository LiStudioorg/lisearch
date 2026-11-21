# web-search

> ## ⚠️ 合规声明（请先阅读）
>
> 本工具通过自动化方式访问公开搜索结果页，**仅供个人学习、研究与低频非商业用途**。
>
> - 自动化抓取可能违反目标网站的服务条款、robots 约定或当地法律；使用者需自行确认并承担全部责任。
> - 请保持低频、串行、小批量访问；不要用于批量采集、商业分发、训练数据构建或任何规避访问控制的行为。
> - **任何商业用途、产品集成或大规模使用，请改用各平台官方 API，并遵守其授权、配额与计费规则。**
> - 本工具不提供登录态、不绕过付费墙、不破解验证码；遇到 `blocked` 请停止重试并改用官方渠道。
> - 搜索结果与页面内容的版权归原网站及原作者所有；引用时保留来源 URL。
> - 项目维护者不对因使用本工具产生的封禁、数据损失或法律纠纷负责。

## 特性

| 项目 | 说明 |
|---|---|
| 零 API Key | 不需要注册任何账号或申请密钥 |
| 零 pip 包 | 仅用 Python 3 标准库 |
| 零外部服务 | 仅依赖系统 `curl`，直接访问目标站点 |
| 多平台 | 百度、必应、DuckDuckGo、谷歌、夸克、搜狗、360、Yahoo、Ecosia |
| 多类型 | 网页、图片、新闻、视频、学术、代码、地图、购物、翻译、RSS |
| 结构化优先 | Bing RSS、arXiv Atom、Wikipedia API、GitHub API |
| 稳定性 | 10 分钟 LRU 缓存、连续 5 次 blocked 冷却 120 秒 |
| 统一输出 | JSON 数组：`title/url/snippet/source/status/fetched_at` |
| 可回退 | 抠不出结果时返回搜索页 URL，浏览器可直接打开 |

## 环境要求

| 依赖 | 要求 | 用途 |
|---|---|---|
| `curl` | 任意较新版本（支持 `--compressed`、`--max-time`） | HTTP 请求 |
| `python3` | 3.8+ | 路由、解析、缓存、熔断 |
| `bash` | 3.2+ | `probe.sh` |
| 浏览器打开器 | `xdg-open`（Linux）或 `open`（macOS），可选 | 手动打开回退 URL |
| 网络 | 可访问目标搜索站点 | 实际搜索 |

无需 `pip install`，无需环境变量，无需配置文件。

## 安装

1. 将整个 `web-search/` 目录放入所用 Agent 的技能目录。常见位置：
   - 项目级：`<project>/.opencode/skills/web-search/`
   - 用户级：`~/.config/opencode/skills/web-search/`
   - 其他 Agent 框架请放到其约定的 skills 目录，保持 `SKILL.md` 位于技能根目录。
2. 赋予脚本执行权限（可选，用 `python3`/`bash` 调用时非必需）：

   ```bash
   chmod +x scripts/fetch.py scripts/probe.sh
   ```

3. 探测环境：

   ```bash
   bash scripts/probe.sh
   ```

4. 查看支持的类型与平台：

   ```bash
   python3 core/search.py --list
   ```

5. 持久化状态（可选）：缓存与熔断器默认写入 `~/.cache/web-search/state.sqlite3`
  （写穿，无权限时静默退回进程内缓存）。可通过环境变量覆盖：

   - `WEB_SEARCH_STATE_DIR`：状态目录（默认 `~/.cache/web-search`）
   - `WEB_SEARCH_STATE_DB`：SQLite 数据库文件路径
   - `WEB_SEARCH_DISABLE_PERSIST=1`：完全禁用持久化，仅使用进程内 LRU 缓存与熔断器

## 手动测试

在技能根目录执行：

```bash
# 环境
bash scripts/probe.sh

# 中文网页
python3 core/search.py "量子计算" --type web --platform baidu --limit 5

# 英文网页 + 站点过滤 + 分页
python3 core/search.py "site reliability" --type web --platform bing \
  --site example.com --limit 5 --page 1

# 新闻（Bing RSS）+ 时间范围（仅 bing 系列）
python3 core/search.py "人工智能" --type news --platform bing --time w --limit 5

# 图片
python3 core/search.py "red panda" --type image --platform bing --limit 5

# 视频
python3 core/search.py "rust tutorial" --type video --platform bing --limit 5

# 学术论文（请求后 sleep 3 秒）
python3 core/search.py "large language model" --type academic --platform arxiv --limit 5

# 百科（--lang 决定维基语言）
python3 core/search.py "transformers" --type academic --platform wikipedia \
  --lang en --limit 5

# 代码仓库（未登录限额约 10 次/分钟）
python3 core/search.py "fastapi" --type code --platform github --limit 5

# DuckDuckGo（POST，超时 10 秒，地区用 kl）
python3 core/search.py "privacy tools" --type web --platform ddg \
  --region us-en --limit 5

# 抓取正文（引用前核对）
python3 scripts/fetch.py "https://example.com/article"

# 抓取本机或局域网服务（默认允许）
python3 scripts/fetch.py "http://127.0.0.1:8080/"
python3 scripts/fetch.py "http://192.168.1.10/status"

# 严格公网模式（拒绝回环与私网）
python3 scripts/fetch.py "https://example.com/article" --public-only
```

`fetch.py` 默认放行本机回环与 RFC1918 局域网地址，方便读取本地服务；
默认拒绝 `169.254.0.0/16` 链路本地地址（如云元数据 `169.254.169.254`），
需要时可用 `--allow-link-local` 显式放行。`--public-only` 会拒绝所有非公网地址。

输出为 JSON，可直接交给后续 Agent 处理。所有日志与错误写入 stderr。

## 输出与退出码

```json
[
  {
    "title": "示例标题",
    "url": "https://example.com/page",
    "snippet": "摘要",
    "source": "bing",
    "status": "ok",
    "fetched_at": "2026-01-01T00:00:00Z"
  }
]
```

| status | 含义 |
|---|---|
| `ok` | 成功解析出结果 |
| `empty` | 无结果或 HTML 无可解析链接，`url` 为搜索页 |
| `blocked` | 429/503、403、验证码或额度不足，`url` 为搜索页 |
| `cooldown` | 熔断冷却中，`url` 为搜索页 |
| `error` | 参数、组合、网络或解析错误 |

| 退出码 | 含义 |
|---|---|
| `0` | `ok` / `empty` / `blocked` / `cooldown` |
| `1` | `error` |
| `2` | 参数错误或不存在的 `type/platform` 组合 |

## 文件说明

| 文件 | 作用 |
|---|---|
| `SKILL.md` | 技能主入口，触发条件、决策树、硬约束、执行命令 |
| `core/search.py` | 搜索执行器（主入口）：路由、编码、缓存、熔断、解析、统一 JSON |
| `scripts/fetch.py` | 抓取单个 URL，自动识别字符集、提取正文、过滤噪声，并支持本机/局域网地址 |
| `scripts/probe.sh` | 检查 curl、python3、浏览器打开器 |
| `references/platforms-web.md` | 网页/翻译 URL 模板与分页参数 |
| `references/platforms-media.md` | 图片/新闻/视频/地图/购物/RSS 模板 |
| `references/platforms-api.md` | arXiv/Wikipedia/GitHub API 参数、字段、节流 |
| `references/parsing-notes.md` | HTML 粗抠、captcha 识别、熔断、空结果、日志约定 |
| `README.md` | 合规声明、安装、测试、文件说明（本文件） |

## 常见问题

| 问题 | 处理 |
|---|---|
| 返回 `blocked` | 换平台；同一引擎连续 3 次会冷却 60 秒，不要继续重试 |
| 返回 `empty` | 打开返回的 `url` 查看；换关键词或平台；部分页面依赖 JS |
| 返回 `cooldown` | 等待提示的秒数，或改用其他平台 |
| 中文结果差 | 优先 `baidu`，降级 `bing`/`sogou`/`360` |
| 英文结果差 | 优先 `bing`/`ddg`，降级 `google`/`yahoo`/`ecosia` |
| GitHub 额度不足 | 改用 `--platform bing` 搜索 `site:github.com`，或等待限额恢复 |
| arXiv 很慢 | 设计如此：每次请求后固定 sleep 3 秒，避免违反使用约定 |
| 想清缓存 | 删除 `~/.cache/web-search/state.sqlite3` 或设置 `WEB_SEARCH_DISABLE_PERSIST=1` |

## 合规使用建议

- 每次会话只做必要查询；优先使用缓存与结构化 API。
- 单平台连续失败 2–3 次即停止，改用官方 API 或人工检索。
- 不采集个人隐私数据，不抓取登录后内容，不绕过验证码与付费墙。
- 引用内容时标注来源 URL，并遵守原站版权与转载规则。
