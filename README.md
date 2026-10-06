# 知选 v2：证据可追溯的手机推荐

Streamlit 原型：人工确认需求 → 同口径候选矩阵 → TOPSIS 相对排序 → 程序证据解释。
本版本独立于旧仓库和旧网站；生产入口是根目录 `app.py`，部署分支为 `main`。

## 功能与边界

- 147 机型、150 版本、484 SKU；卫星版本和存储配置独立关联。
- 预算、品牌包含/排除、最低存储、成色和手动权重；排序、对比、来源、CSV/XLSX/JSON 导出。
- 数据/规则/算法版本与快照哈希绑定；换库后旧确认和解释失效。
- 默认无 API key、不调用付费模型。可选抽取后仍须手动确认，程序产生解释。
- TOPSIS 是可比候选内相对匹配分，不是绝对性能或购买概率。默认权重是试验设置，不是学习出的偏好。
- 拍照只用同协议 DXOMARK 照片子分，不混 v4/v5/v6、不把归一化总分重复计权。
- 游戏须同次测试且关键条件齐全，缺数据只列备选；不以处理器/刷新率代替实测。1% Low 无有效覆盖，不推算完整总分。
- 历史价格有 new/used/unknown；先选快照再筛预算，不承诺实时库存/报价。
- 机器 PASS；整体 PENDING、WPS NOT_CHECKED、人工抽查待完成。图片未发布，使用依据待确认。VIKOR 未实现。

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
当前尚无经本轮验证的新公网地址；管理员完成云端验收后填写。旧网站保持不动。

代码暂未选择开源许可证；私有协作不等于授权再分发。公开前应完成来源、版权与事实抽查，比赛材料据实披露数据和创新边界。
