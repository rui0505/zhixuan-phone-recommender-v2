# 淘宝买家评价接入（2026-10-27）

状态：2026-10-27 已发布至 https://zhixuanji.cn/ ，服务器及公网 HTTPS 接口、资源核验通过；内置浏览器的公网画面验收仍受连接关闭影响。

## 使用方式

打开手机的「配置详情与来源」弹窗，标题旁新增「查看淘宝买家评价」。点击后在同一弹窗阅读该机型的评价，可按全部、好评、中评、差评、默认好评筛选，每页 20 条；「返回配置详情」恢复原来的配置资料。原有四页布局、七维偏好和推荐排序保持不变。

按用户明确要求，买家昵称使用 CSV 的「用户名」原值，淘宝原本已打码的昵称保持原样；没有昵称时显示「买家昵称未记录」。昵称与正文均通过 textContent 展示，评论中的 HTML 不执行。正文中的联系方式和邮箱继续替换为提示文字。

## 数据范围

- 来源：用户提供的 `D:/DSH/taobao_rate_spider/out/手机评价_最终版.csv`，本次没有执行爬取。
- 输入 5,143 条，按品牌和型号规范化后精确匹配，收录 5,108 条，覆盖现有目录 118 个机型。
- 35 条缺少型号，未猜测归属；未修改原始 CSV 和 catalog.sqlite。
- 收录中 5,104 条有可解析评价日期，807 条有追评；日期缺失如实标注。
- 评价等级沿用采集记录，默认好评单列，未按正文推测情感或计算好评率。
- 评价关联至机型，而非某个内存配置或新旧成色。页面说明可能来自不同配置与成色，并展示购买规格、商品原标题、商品 ID 和采集日期。
- 无对应评价的机型显示「暂无该机型的淘宝买家评价」。数据未接入、读取失败及筛选为空分别提供提示，读取失败可重试。

运行数据版本：`taobao-csv-e33a2377f287`。目录指纹：`128207745f5450c285cb8d6c61ff2c692f19387f2df0823690cd5b323c453bc8`。

## 实现与刷新

导入器：`scripts/import_buyer_reviews.py`。运行快照：`data/buyer_reviews.json`。在项目根目录执行：

```powershell
& '.\.venv\Scripts\python.exe' scripts/import_buyer_reviews.py --source 'D:/DSH/taobao_rate_spider/out/手机评价_最终版.csv'
```

只读接口：`GET /api/v1/skus/{sku_id}/reviews?page=1&page_size=20&grade=all`，允许 `all/good/neutral/bad/default_good/unknown`。返回当前目录发布指纹，并核对评价快照与目录一致；无效参数拒绝，损坏或指纹不符返回 503。原始 CSV 不作为静态文件开放。

## 验证

- 前端 49 项测试通过，包含实际 app 事件链路：详情 → 评价 → 翻页 → 差评筛选 → 返回配置；昵称和评论中的 HTML 保持普通文本。
- 本次相关后端及静态资源测试 23 项通过，另有 API 服务、契约、部署回归 30 项通过，共 53 项 Python 测试。
- 本地 8770 的真实 HTTP 路径核验：S0462（Redmi Turbo 4 Pro）共 132 条，107 好评、6 中评、19 差评；第二页与第一页无重复；昵称均来自原 CSV；详情接口及新静态文件可达。
- `local_evidence/buyer-reviews-20261027-http.json` 保存不含评价正文和昵称的验证摘要。
- Impeccable 手动检查发现既有 index.html 引入样式的字号、悬停对比度和阴影提示，新增 reviews.mjs/reviews.css 未被列出；本次保留既有视觉布局。
- Windows Computer Use 因无法可靠确认当前浏览器 URL 而停止；没有继续操作或绕过，因此未宣称桌面或手机画面已通过验收。

发布包：`local_evidence/zhixuan-buyer-reviews-20261027.zip`，8,191,608 字节，225 个运行文件，SHA-256 `cf11b7e8f1462b5494fd9c67252d3e90a8010ce17314d36e32a9d1212992ad8e`。包含运行代码、评价快照和既有批准图片，不含原 CSV、Git 或凭据。

## 云端发布结果

用户明确授权后，通过 Browser 插件连接用户指定的 Workbench 标签。原 URL 带有历史实例参数，页面当前连接及终端实际主机均核实为 `i-j6c8jqxomk685dgmtuc2` / `47.86.39.2`。旧页面文件管理会话过期，重新建立同一实例连接后仍绑定旧传输状态；为同一实例打开新 Workbench 页面后，文件传输恢复正常。

发布包和 `local_evidence/deploy-buyer-reviews-20261027.py` 实际上传至服务器 `/tmp`，两者在服务器的 SHA-256 均与本地一致。脚本 SHA-256：`f4e9422264c38f5dfa9d7b78b87dcd958e2c49319fe017dd450168f33a25868c`。

先解压到独立新目录，逐文件校验，并检查 SQLite 完整性、外键及目录指纹；使用已有环境在回环临时端口验证买家评价、推荐与导出后，才原子切换 current 并重启现有服务。切换后再次核验，脚本报告 `phase=activated`，zhixuan 与 nginx 均 active，配置文件指纹保持一致。本次未安装依赖、修改 DNS/证书/端口或输出凭据。

- 当前目录：`/opt/zhixuan/releases/alpine-ui-20261027-buyerreviews`。
- 上一目录：`/opt/zhixuan/releases/alpine-ui-20261027-ccd0413`，完整保留以备回退。
- 服务器记录：`/root/zhixuan-buyer-reviews-20261027-backup/alpine-ui-20261027-buyerreviews.json`。
- 本地服务器证据：`local_evidence/buyer-reviews-server-verify-20261027.txt`、`buyer-reviews-server-activated-20261027.txt`、`buyer-reviews-release-proof-20261027.png`。
- 公网验收：`local_evidence/verify_buyer_reviews_public.py` 输出 PASS；首页、模块、CSS 和资源共 28 项 SHA-256 与本地一致；评价内容及用户名逐条匹配运行快照，差评筛选 19 条，前两页无重复；默认排名 40 配置，预算 3000 且至少 256GB 返回 20 配置，CSV/XLSX/JSON 导出通过。
- 公网验收记录：`local_evidence/buyer-reviews-public-acceptance-20261027.json`，HTTPS 证书校验保持启用。

内置浏览器访问公网出现 `ERR_CONNECTION_CLOSED`，随后错误页的 data URL 被浏览器策略拒绝，没有绕过或继续操作该错误页；因此不宣称公网浏览器画面或交互验收完成。服务器及 Windows curl 的正常 HTTPS 请求已独立完成上述验证。GitHub 分支与 main 本次未推送、未合并；云端发布与 GitHub 集成分别记录。
