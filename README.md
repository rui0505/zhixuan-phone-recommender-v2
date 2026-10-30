# 知选 v2：证据可追溯的手机推荐

人工确认需求 → 硬条件筛选 → 七维评分与相对排序 → 有依据的推荐理由。

当前公网站点：[知选](https://zhixuanji.cn/)。HTML + 独立 API 入口为 `api.app:app`，保留 Streamlit `app.py`。仓库及提交历史已公开，无需邀请即可查阅；参赛安装统一使用 `main`，核心功能版本固定参考 `ec302d9eafde464f4c335099fe2f32b7a03b1ec9`（PR #8 合并）。[参赛材料与证据索引](docs/submission-20261030/README.md)集中提供测试摘要、算法复算、截图、反馈统计和版本记录。

四页新界面、七维偏好、百分制规格参考及买家评价已在 2026-10-27 发布到现有 ECS。详见 [界面发布记录](docs/alpine-public-release-20261027.md)、[评分规则与来源](docs/spec-reference-scoring-20261027.md)、[买家评价及云端发布](docs/buyer-reviews-20261027.md)。新版协作、图片与可选 DeepSeek 边界见 [HTML/API 交付](docs/html-api-local.md)。

## 功能与边界

- 147 机型、150 版本、484 SKU；卫星版本和存储配置独立关联。
- 预算、品牌包含/排除、最低存储和成色是硬条件；存储、重量、电池容量、处理器档位、屏幕尺寸、拍照、游戏是七个偏好维度。五档重要性按维度映射，未提及有正初值，明确不在意可为 0。
- 正式 HTML/API 仅提供 TOPSIS + TODIM 混合排序（RRF），默认即为混合模式；支持对比、来源与 CSV/XLSX/JSON 导出。独立 TOPSIS 请求已停用，见 [混合算法上线记录](docs/hybrid-only-delivery-20261029.md)。
- 数据/规则/算法版本与快照哈希绑定；换库后旧确认和解释失效。
- 默认无 API key、不调用付费模型。可选抽取后仍须手动确认，程序产生解释。
- 需求抽取采用 AISM 七维结构上下文，提供可审查的 UP/DOWN 分层；大模型逐维抽取，关系不自动传播偏好。初始结构依据与本地验收见 [AISM 交付记录](docs/aism-semantic-delivery-20261028.md)。
- 手机库独立展示全商品，默认全部成色147机型/484配置；自己的搜索、筛选和清空不影响智能选机。见 [独立手机库交付](docs/independent-catalog-delivery-20261028.md)。
- 混合排序分是可比候选内相对匹配分，不是绝对性能或购买概率。默认权重是试验设置，不是学习出的偏好。
- 默认规格参考模式提供固定规则的七维百分制估算，游戏用处理器和电池容量代理；不承诺为实测体验或经真实用户校准的性能。
- 可选实测模式中，拍照只用同协议 DXOMARK 照片子分，不混 v4/v5/v6；游戏须同次测试且关键条件齐全。1% Low 无有效覆盖，不推算完整实测总分。
- 详情页可查看 5,108 条淘宝买家评价（118 个机型），保留采集昵称，支持等级筛选、分页和追评；机型级样本可能来自不同配置与成色。
- 历史价格有 new/used/unknown；先选快照再筛预算，不承诺实时库存/报价。
- 机器 PASS；整体 PENDING、WPS NOT_CHECKED、人工抽查待完成。另行授权的 147 个机型图片通过独立展示清单关联，不修改数值库；不是 SKU 成色/库存证明。VIKOR 未实现。

## HTML/API 安装与启动（Windows / WorkBuddy）

公开仓库：https://github.com/rui0505/zhixuan-phone-recommender-v2 。匿名下载与克隆不需要邀请，也不需要提供 token。

```powershell
git clone https://github.com/rui0505/zhixuan-phone-recommender-v2.git
cd zhixuan-phone-recommender-v2
git switch main
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-api.txt
.\.venv\Scripts\python.exe scripts/check_release.py --root .
.\.venv\Scripts\python.exe -m uvicorn api.app:app --host 127.0.0.1 --port 8600
```

需复核本次参赛功能版本时，可在克隆后运行 `git checkout ec302d9eafde464f4c335099fe2f32b7a03b1ec9`，再执行安装与启动命令。该固定提交对应业务代码；后续参赛证据与说明更新见上方索引。

Linux/macOS 使用 `python3.12 -m venv .venv`，解释器换为 `.venv/bin/python`。
浏览器访问 `http://127.0.0.1:8600/`。无需桌面 Excel、图片 ZIP、Node 或 Word；生产网页读取只读目录快照、版本化规则、展示图片及评价运行快照。Node 仅用于前端测试。
Python 3.12；直接依赖锁定在 requirements，传递依赖由 pip 解析。

需要运行保留的 Streamlit 入口时，再安装 `requirements.txt` 并执行 `.\.venv\Scripts\python.exe -m streamlit run app.py`。

## 测试与协作

见 [队友贡献说明](CONTRIBUTING.md)、[测试迁移说明](docs/test-migration.md)、[发布清单](docs/release-checklist.md)。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py' -v
$env:PHONE_CATALOG_DB='data/catalog.sqlite'
.\.venv\Scripts\python.exe -m unittest discover -s data_pipeline/tests -p 'test_*.py' -v
```

HTML/API 是当前 ECS 的公网入口；旧兼容适配器及旧 UI 测试作为回归参考，旧 UI 测试跑 `tests/reference_streamlit.py` 与合成夹具。API/前端覆盖见 `tests/test_api_*.py` 与 `tests/frontend/`，Streamlit 目录入口由 `test_app_catalog.py` 验收。
全量原件保真/重建稳定性、人审和 WPS 检查在数据管理员本地单独验收，不能由 CI PASS 代替。

## 历史 Streamlit 发布记录（2026-10-06）

以下保留该日期的发布记录；2026-10-27 的 ECS 发布状态以顶部链接的发布文档为准。

Streamlit 新应用选实际新仓库 / `main` / `app.py` / Python 3.12；不填 LLM 密钥。
仓库私有与网页公开是不同设置。只发布白名单快照，不上传原件；HTML/ECS 入口另行授权的 147 张机型图通过独立展示层发布，不代表实际成色/库存。
已验证公网地址：[知选 v2](https://zhixuan-phone-recommender-v2-kkpzqq7j9pqblxb5ka6u29.streamlit.app/)（2026-10-06）。
已退出 Streamlit 管理账号实测：无需登录，目录、推荐、详情、对比、无候选/数据不足提示和中文 CSV/XLSX 下载可用。
相同默认请求的 51 条云端导出与本地复算一致；旧网站仍为原来的 46 机型版本，未替换。
云端实测 Python 3.12.15；平台自动将 PyArrow 25.0.0 换为 24.0.0。与本地 3.12.14 的环境记录分开，不声称全依赖锁定。
源码提交、部署分支与数据快照已核对；平台未提供本轮可独立验证的运行提交 SHA，不能把分支 HEAD 当作平台证明。

代码暂未选择开源许可证；源码公开不等于授权再分发。比赛材料据实披露数据和创新边界，许可状态沿用项目既有说明。

