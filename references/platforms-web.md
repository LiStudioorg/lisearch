# 网页类平台 URL 模板

`{q}` 必须替换为 `quote_plus(query)`；`{first}`、`{start}`、`{pn}` 等为整数。分页均从 `page=1` 开始。

## 网页搜索

| 平台 | URL 模板 | 分页参数 | 备注 |
|---|---|---|---|
| 百度 | `https://www.baidu.com/s?ie=utf-8&wd={q}&pn={pn}&rn={limit}` | `pn=(page-1)*10` | 中文优先；重定向链接需浏览器解析 |
| 必应 | `https://www.bing.com/search?q={q}&first={first}&count={limit}{time_filter}` | `first=(page-1)*10+1` | `time_filter` 为 `&qft=interval_1..4` |
| DuckDuckGo | `https://html.duckduckgo.com/html/`（POST） | 表单 `s=(page-1)*10` | 表单字段：`q`、`b`、`kl`、`s`；超时 10s |
| DuckDuckGo（浏览） | `https://html.duckduckgo.com/html/?q={q}&kl={kl}` | `s` | 仅作为 blocked/empty 时的回退地址 |
| 谷歌 | `https://www.google.com/search?q={q}&num={limit}&start={start}&hl={lang}&gbv=1` | `start=(page-1)*10` | `gbv=1` 取基础 HTML，降低 JS 依赖 |
| 夸克 | `https://quark.sm.cn/s?q={q}&page={page}` | `page` | 移动端起家，桌面 HTML 可能较简 |
| 搜狗 | `https://www.sogou.com/web?query={q}&page={page}` | `page` | 中文网页备选 |
| 360 搜索 | `https://www.so.com/s?q={q}&pn={page}` | `pn=page` | 注意 `pn` 是页码而非偏移 |
| Yahoo | `https://search.yahoo.com/search?p={q}&b={first}` | `b=(page-1)*10+1` | 英文备选 |
| Ecosia | `https://www.ecosia.org/search?q={q}&p={p}` | `p=page-1` | 基于 Bing 索引，结果可能重复 |

## 翻译页（仅提供入口，正文解析不稳定）

| 平台 | URL 模板 | 参数 | 备注 |
|---|---|---|---|
| 必应翻译 | `https://www.bing.com/translator?text={q}&from=auto&to={lang}` | `lang` 为基础语言码 | 首选；页面可直接打开 |
| 谷歌翻译 | `https://translate.google.com/?sl=auto&tl={lang}&text={q}&op=translate` | `lang` 为基础语言码 | 可能触发同意页 |
| 百度翻译 | `https://fanyi.baidu.com/#auto/{lang}/{qpath}` | `qpath` 为路径编码 | 中英互译备选 |

## 通用规则

| 项目 | 约定 |
|---|---|
| 查询编码 | 查询参数用 `quote_plus`，路径片段用 `quote(..., safe="")` |
| 站点过滤 | 查询词后追加 ` site:domain`，域名只允许 `[A-Za-z0-9.-]` |
| 时间范围 | 仅 bing 系列：`d→interval_1`、`w→interval_2`、`m→interval_3`、`y→interval_4` |
| 语言 | `Accept-Language` 使用 `--lang` 原值；URL 内语言用其基础码（如 `zh`、`en`） |
| 地区 | 仅 DuckDuckGo 使用 `kl`（如 `cn-zh`、`us-en`、`wt-wt`） |
| 回退 | 解析失败时输出当前搜索页 URL，供浏览器直接打开 |
