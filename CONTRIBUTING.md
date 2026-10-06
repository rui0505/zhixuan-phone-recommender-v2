# 队友协作与 WorkBuddy 接入

## 首次连接

1. 自己的 GitHub 账号接受邀请，安装 Git/Python 3.12，按 README 克隆和建立环境。
2. WorkBuddy “打开文件夹”，选择包含 `app.py` 与 `.git` 的新仓库根目录。
3. `git remote -v`、`git branch --show-current` 确认新仓库；仅仓库本地配置 `git config user.name "你的名字"` 和 `git config user.email "你的提交邮箱"`。
4. 首次 push 由 Git Credential Manager/GitHub 页面认证，不共享账号或把 token 写到 URL。

## 每次开发

```powershell
git switch main
git pull --ff-only
git switch -c codex/你的任务简称
# WorkBuddy 修改后，自己检查 diff 与实际测试
git diff --check
.\.venv\Scripts\python.exe scripts/check_release.py --root .
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py' -v
git add <本次改动的明确文件>
git commit -m "说明改变"
git push -u origin codex/你的任务简称
```

GitHub 提 PR，base=`main`，说明目的、测试、限制，另一队员审核再合并。不要强推 main，不懂冲突时停下请同伴帮忙，不 reset/覆盖本地改动。
若账号计划不支持强制保护分支，仍遵守团队 PR 流程；不能声称保护已生效。
WorkBuddy 是本地编辑助手，Git 管理协作版本；“AI说完成”不等于测试/提交/远端验证。

## 给 WorkBuddy 的提示模板

> 在新仓库独立功能分支修改【具体模块】，先读 README/CONTRIBUTING。
> 不改源数值，不提交密钥/原件，不动旧仓库/网站，只改本任务文件。
> 算法用 CatalogRepository、规范 ID、DecisionInput、缺失和可比性状态。
> 先失败测试再实现，报告实际命令、输出、改动和限制。未获管理员批准不合并 main 或启用付费模型。

分工：算法队员 `engine.py`/`catalog/decision_input.py`，界面队员 `app.py`/`ui/`，数据管理员 `data_pipeline/`/注册表/白名单快照。
界面不能独立计算另一套排名，解释必须引用同一结果和版本。

## 补数据

原件只读，新来源生成新版本，不手改 SQLite。数值、原文、单位、作用域、来源可追溯，NULL≠0。
机型/版本/SKU/价格快照/测试运行独立 ID；维护 `data_pipeline/id_registry.csv`，不能只按名称粗暴合并。

```powershell
# 本地复制 example 为被忽略的 build_config.local.json，填授权原件路径。
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe data_pipeline/build_dataset.py --config data_pipeline/config/build_config.local.json
.\.venv\Scripts\python.exe data_pipeline/tests/acceptance.py --config data_pipeline/config/build_config.local.json --rebuild-check
.\.venv\Scripts\python.exe scripts/export_snapshot.py --help
```

Excel 作者工具另需 artifact-tool/Node；无工具先 `--data-only` 验证，不声称工作簿已生成/复核。
管理员验收后用 exporter 白名单重建发布库，核数值/SHA/manifest 后提 PR。CI 不证明未提交原件保真；WPS、人审、图片许可单列状态。

## 禁止提交

真实 `.env`/secrets/token、桌面绝对路径配置、原件 Excel/ZIP、全文评测摘录、未审核图片、虚拟环境、个人日志。
`local_build/`、`local_evidence/` 不入库，两个新找到 CSV 不属本次范围。
共享模型启用前另行核实预算、服务端限额与输入隐私；三个 true 标志不是费用控制器。
