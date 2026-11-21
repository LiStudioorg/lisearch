# 图片 / 新闻 / 视频 / 地图 / 购物 / RSS 模板

`{q}` 为 urlencode 后的查询词；`{first}/{start}/{pn}/{offset}` 均为整数。

## 新闻

| 平台 | URL 模板 | 分页 | 备注 |
|---|---|---|---|
| 必应新闻（RSS） | `https://www.bing.com/news/search?q={q}&format=RSS&first={first}&count={limit}{time_filter}` | `first` | 首选；结构化 RSS，`--time` 生效 |
| 百度新闻 | `https://news.baidu.com/ns?word={q}&pn={pn}&rn={limit}&cl=2&ct=1` | `pn=(page-1)*10` | 中文时效新闻备选 |
| 谷歌新闻 | `https://news.google.com/search?q={q}&hl=en-US&gl=US&ceid=US:en` | 无 | 页面依赖 JS，失败时用回退 URL |
| 搜狗新闻 | `https://news.sogou.com/news?query={q}&page={page}` | `page` | 中文备选 |
| 360 新闻 | `https://news.so.com/ns?q={q}&pn={page}` | `pn=page` | 中文备选 |
| Yahoo 新闻 | `https://news.search.yahoo.com/search?p={q}&b={first}` | `b=(page-1)*10+1` | 英文备选 |
| DuckDuckGo 新闻 | `https://duckduckgo.com/?q={q}&iar=news&ia=news` | 无 | JS 页，超时 10s，通常回退 URL |

## 图片

| 平台 | URL 模板 | 分页 | 备注 |
|---|---|---|---|
| 百度图片 | `https://image.baidu.com/search/index?tn=baiduimage&word={q}&pn={pn}&rn={limit}` | `pn=(page-1)*10` | 中文图片优先 |
| 必应图片 | `https://www.bing.com/images/search?q={q}&first={first}&count={limit}{time_filter}` | `first` | 结果较稳定 |
| DuckDuckGo 图片 | `https://duckduckgo.com/?q={q}&iax=images&ia=images` | 无 | JS 页，通常回退 URL |
| 谷歌图片 | `https://www.google.com/search?tbm=isch&q={q}&start={start}&num={limit}&gbv=1` | `start=(page-1)*10` | 可能触发同意页 |
| 搜狗图片 | `https://pic.sogou.com/pics?query={q}&start={start}` | `start=(page-1)*10` | 中文备选 |
| 360 图片 | `https://image.so.com/i?q={q}&pn={page}` | `pn=page` | 中文备选 |
| Yahoo 图片 | `https://images.search.yahoo.com/search/images?p={q}&b={first}` | `b=(page-1)*10+1` | 英文备选 |
| Ecosia 图片 | `https://www.ecosia.org/images?q={q}&p={p}` | `p=page-1` | 索引与 Bing 相近 |

## 视频

| 平台 | URL 模板 | 分页 | 备注 |
|---|---|---|---|
| 百度视频 | `https://v.baidu.com/v?word={q}&pn={pn}` | `pn=(page-1)*10` | 中文视频备选 |
| 必应视频 | `https://www.bing.com/videos/search?q={q}&first={first}&count={limit}{time_filter}` | `first` | 首选 |
| DuckDuckGo 视频 | `https://duckduckgo.com/?q={q}&iax=videos&ia=videos` | 无 | JS 页，通常回退 URL |
| 谷歌视频 | `https://www.google.com/search?tbm=vid&q={q}&start={start}&num={limit}&gbv=1` | `start=(page-1)*10` | 英文优先 |
| 搜狗视频 | `https://v.sogou.com/v?query={q}&page={page}` | `page` | 中文备选 |
| 360 视频 | `https://tv.360kan.com/s?q={q}` | 无 | 中文备选；旧域名 `tv.so.com` 已失效 |
| Yahoo 视频 | `https://video.search.yahoo.com/search/video?p={q}&b={first}` | `b=(page-1)*10+1` | 英文备选 |

## 地图

| 平台 | URL 模板 | 备注 |
|---|---|---|
| 百度地图 | `https://map.baidu.com/search/{qpath}` | 路径编码；JS 地图，通常回退 URL |
| 必应地图 | `https://www.bing.com/maps?q={q}` | 首选；可回退浏览器打开 |
| 谷歌地图 | `https://www.google.com/maps/search/{qpath}` | 路径编码；JS 地图 |
| 360 地图 | `https://ditu.so.com/?k={q}` | 中文备选 |

## 购物

| 平台 | URL 模板 | 备注 |
|---|---|---|
| 必应购物 | `https://www.bing.com/shop?q={q}&first={first}` | 首选 |
| 谷歌购物 | `https://www.google.com/search?tbm=shop&q={q}&start={start}&gbv=1` | 比价备选 |
| 百度购物 | `https://www.baidu.com/s?wd={q}&tn=shopping` | 中文商品备选 |
| Yahoo 购物 | `https://shopping.yahoo.com/search?p={q}` | 英文备选 |

## RSS

| 平台 | URL 模板 | 解析 | 备注 |
|---|---|---|---|
| 必应 RSS | `https://www.bing.com/search?q={q}&format=rss&first={first}&count={limit}{time_filter}` | `<item>` 正则 | 网页/新闻通用，`--time` 生效 |
| 谷歌新闻 RSS | `https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en` | `<item>` 正则 | 英文新闻聚合 |
| Yahoo RSS | `https://news.search.yahoo.com/rss?p={q}` | `<item>` 正则 | 英文备选 |

必应新闻频道的 `/news/search?format=RSS` 已不稳定，可能返回 HTML 首页；
`news/bing` 会自动回退到 Google News RSS、百度新闻，最后再回退到 Bing 网页 RSS。

## 编码与回退

| 项目 | 约定 |
|---|---|
| 查询编码 | 所有 `{q}` 已经是 `quote_plus` 后的值，模板中禁止再次拼接原始输入 |
| 路径编码 | 地图类 `{qpath}` 使用 `quote(..., safe="")` |
| 解析失败 | 返回搜索页 URL，`status` 为 `empty` 或 `blocked` |
| 图片/视频直链 | 只作为结果 URL 输出，不在服务端下载大文件 |
