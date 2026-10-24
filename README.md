# 知选 v2：证据可追溯的手机推荐

人工确认需求 → 同口径候选矩阵 → TOPSIS 相对排序 → 程序证据解释。
新增 HTML + 独立 API 入口 `api.app:app`，保留 Streamlit `app.py`。新版协作、启动、图片与可选 DeepSeek 边界见 [HTML/API 交付](docs/html-api-local.md)。网页版本的 PR #1 已合并；2026-10-24 的发布验收见 [公网发布记录](docs/consumer-ux-public-release-20261024.md)。本次完整汇总的代码、资料与分支边界见 [仓库同步说明](docs/repository-sync-20261024.md)。

## 功能与边界

- 147 机型、150 版本、484 SKU；卫星版本和存储配置独立关联。
- 预算、品牌包含/排除、最低存储、成色和手动权重；排序、对比、来源、CSV/XLSX/JSON 导出。
- 数据/规则/算法版本与快照哈希绑定；换库后旧确认和解释失效。
- 默认无 API key、不调用付费模型。可选抽取后仍须手动确认，程序产生解释。
- TOPSIS 是可比候选内相对匹配分，不是绝对性能或购买概率。默认权重是试验设置，不是学习出的偏好。
- 拍照只用同协议 DXOMARK 照片子分，不混 v4/v5/v6、不把归一化总分重复计权。
- 游戏须同次测试且关键条件齐全，缺数据只列备选；不以处理器/刷新率代替实测。1% Low 无有效覆盖，不推算完整总分。
- 历史价格有 new/used/unknown；先选快照再筛预算，不承诺实时库存/报价。
- 机器 PASS；整体 PENDING、WPS NOT_CHECKED、人工抽查待完成。另行授权的 147 个机型图片通过独立展示清单关联，不修改数值库；不是 SKU 成色/库存证明。生产默认仍为 TOPSIS；实验室另有 VIKOR Q 指数对照。

## 交互算法实验室（0.2，独立入口）

实验代码来自分支 `codex/competition-preference-fusion` 的提交 `b8cfd71`，现已与最新主线网页代码汇总到 `codex/all-optimizations-20261024`。
运行环境与数据复用本仓库，实验室仍是独立入口；上传代码不会自动将偏好学习接入 HTML/API 或更新云服务器。

```powershell
.\.venv\Scripts\python.exe -m streamlit run algorithm_lab.py --server.address 127.0.0.1 --server.port 8502
```

浏览器访问 `http://127.0.0.1:8502/`，确认预算及偏好后，最多回答五道手机二选一问题。
支持跳过、撤销、权重区间、逐算法稳定性、对照排序和完整记录导出。核心模块更新后应重启服务，
避免开发服务器保留旧 Python 模块。

默认查看学习后 TOPSIS，使用信息增益选题；TODIM、RRF 融合、VIKOR 和加性效用可切换比较。
经典 TODIM 的逆权重损失项可能与重要性语义冲突，融合尚未证明普遍优于单算法。
本版本未训练深度模型，也未调用 JEV 外部服务；使用可复算的有限粒子贝叶斯学习。

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_competition.py --users 24 --particles 128 --seed 20261020 --output local_evidence/v02-default.json
.\.venv\Scripts\python.exe scripts/evaluate_competition.py --users 96 --particles 128 --seed 20261021 --output local_evidence/v02-validation-a.json
.\.venv\Scripts\python.exe scripts/evaluate_competition.py --users 96 --particles 128 --seed 20261022 --output local_evidence/v02-validation-b.json
```

所有效果数据来自合成用户，不代表真实购买效果。完整记录在忽略的 `local_evidence/`，
可分享摘要在 `docs/experiments/`。参见[实验报告](docs/competition-algorithm-results.md)、
[计算设计](docs/competition-algorithm-design.md)及[队友接入说明](docs/competition-algorithm-handoff.md)。

## 安装与启动（Windows / WorkBuddy）

仓库地址：https://github.com/rui0505/zhixuan-phone-recommender-v2 。私有仓库先接受管理员邀请；用自己的 GitHub 账号，不共享 token。

需要本次完整汇总代码时，克隆后执行 `git switch codex/all-optimizations-20261024`；主线通过团队 PR 流程更新。

```powershell
git clone https://github.com/rui0505/zhixuan-phone-recommender-v2.git
cd zhixuan-phone-recommender-v2
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/check_release.py --root .
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Linux/macOS 使用 `python3.12 -m venv .venv`，解释器换为 `.venv/bin/python`。
无需桌面 Excel、图片 ZIP、Node 或 Word；生产网页只读 `data/catalog.sqlite`。
Python 3.12；直接依赖锁定在 requirements，传递依赖由 pip 解析。
Streamlit 1.65 自身依赖 Starlette/Uvicorn/Pillow；应用不启动旧 API 或建库/报告工具。

## 测试与协作

见 [队友贡献说明](CONTRIBUTING.md)、[测试迁移说明](docs/test-migration.md)、[发布清单](docs/release-checklist.md)。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py' -v
$env:PHONE_CATALOG_DB='data/catalog.sqlite'
.\.venv\Scripts\python.exe -m unittest discover -s data_pipeline/tests -p 'test_*.py' -v
```

旧 HTML/API/Excel 适配器只作回归参考，不是公网入口。旧 UI 测试跑 `tests/reference_streamlit.py` 与合成夹具；新版由 `test_app_catalog.py` 单独验收。
全量原件保真/重建稳定性、人审和 WPS 检查在数据管理员本地单独验收，不能由 CI PASS 代替。

## 独立发布

Streamlit 新应用选实际新仓库 / `main` / `app.py` / Python 3.12；不填 LLM 密钥。
仓库私有与网页公开是不同设置。只发布白名单快照，不上传原件；HTML/ECS 入口另行授权的 147 张机型图通过独立展示层发布，不代表实际成色/库存。
已验证公网地址：[知选 v2](https://zhixuan-phone-recommender-v2-kkpzqq7j9pqblxb5ka6u29.streamlit.app/)（2026-10-06）。
已退出 Streamlit 管理账号实测：无需登录，目录、推荐、详情、对比、无候选/数据不足提示和中文 CSV/XLSX 下载可用。
相同默认请求的 51 条云端导出与本地复算一致；旧网站仍为原来的 46 机型版本，未替换。
云端实测 Python 3.12.15；平台自动将 PyArrow 25.0.0 换为 24.0.0。与本地 3.12.14 的环境记录分开，不声称全依赖锁定。
源码提交、部署分支与数据快照已核对；平台未提供本轮可独立验证的运行提交 SHA，不能把分支 HEAD 当作平台证明。

代码暂未选择开源许可证；私有协作不等于授权再分发。公开前应完成来源、版权与事实抽查，比赛材料据实披露数据和创新边界。
