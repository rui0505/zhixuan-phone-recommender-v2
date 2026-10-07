# 知选 v2：证据可追溯的手机推荐

人工确认需求 → 同口径候选矩阵 → TOPSIS 相对排序 → 程序证据解释。
新增 HTML + 独立 API 入口 `api.app:app`，保留 Streamlit `app.py`。新版协作、启动、图片与可选 DeepSeek 边界见 [HTML/API 交付](docs/html-api-local.md)。功能分支为 `codex/html-api-ui`，本地提交不代表已推送或部署。

## 功能与边界

- 147 机型、150 版本、484 SKU；卫星版本和存储配置独立关联。
- 预算、品牌包含/排除、最低存储、成色和手动权重；排序、对比、来源、CSV/XLSX/JSON 导出。
- 数据/规则/算法版本与快照哈希绑定；换库后旧确认和解释失效。
- 默认无 API key、不调用付费模型。可选抽取后仍须手动确认，程序产生解释。
- TOPSIS 是可比候选内相对匹配分，不是绝对性能或购买概率。默认权重是试验设置，不是学习出的偏好。
- 拍照只用同协议 DXOMARK 照片子分，不混 v4/v5/v6、不把归一化总分重复计权。
- 游戏须同次测试且关键条件齐全，缺数据只列备选；不以处理器/刷新率代替实测。1% Low 无有效覆盖，不推算完整总分。
- 历史价格有 new/used/unknown；先选快照再筛预算，不承诺实时库存/报价。
- 机器 PASS；整体 PENDING、WPS NOT_CHECKED、人工抽查待完成。另行授权的 147 个机型图片通过独立展示清单关联，不修改数值库；不是 SKU 成色/库存证明。VIKOR 未实现。

## 安装与启动（Windows / WorkBuddy）

仓库地址：https://github.com/rui0505/zhixuan-phone-recommender-v2 。私有仓库先接受管理员邀请；用自己的 GitHub 账号，不共享 token。

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
仓库私有与网页公开是不同设置。只发布白名单快照，不上传原件和图片。
已验证公网地址：[知选 v2](https://zhixuan-phone-recommender-v2-kkpzqq7j9pqblxb5ka6u29.streamlit.app/)（2026-10-06）。
已退出 Streamlit 管理账号实测：无需登录，目录、推荐、详情、对比、无候选/数据不足提示和中文 CSV/XLSX 下载可用。
相同默认请求的 51 条云端导出与本地复算一致；旧网站仍为原来的 46 机型版本，未替换。
云端实测 Python 3.12.15；平台自动将 PyArrow 25.0.0 换为 24.0.0。与本地 3.12.14 的环境记录分开，不声称全依赖锁定。
源码提交、部署分支与数据快照已核对；平台未提供本轮可独立验证的运行提交 SHA，不能把分支 HEAD 当作平台证明。

代码暂未选择开源许可证；私有协作不等于授权再分发。公开前应完成来源、版权与事实抽查，比赛材料据实披露数据和创新边界。
