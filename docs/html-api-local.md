# HTML + API 版交付

## 当前边界

保留旧 Streamlit 入口 `app.py`，新版入口为 `api.app:app`。新版 HTML 保留原来的灰绿配色、圆角卡片和三导航，用真实 API 展示目录、TOPSIS 推荐、来源、四配置对比与完整排名导出。两种入口共用 `catalog.adapter`、`engine.rank_decision_input` 和绑定证据。VIKOR 未实现；以后在算法层新增，不在浏览器重算分数。

数据库 147 机型 / 150 版本 / 484 配置：新机 51、二手 351、未知 82。价格是记录快照。缺失不补 0，不用像素或芯片名称制造能力分；拍照/游戏仅比较协议与条件可比的观测。机器验收 PASS 不等于 WPS、人工验收通过。

## 队友开始开发

1. 接受仓库负责人发出的 GitHub 协作邀请，登录自己的账号。
2. `git clone https://github.com/rui0505/zhixuan-phone-recommender-v2.git`，进入目录。负责人需先将本版功能分支推送后，队友才可获取该分支；本地提交不代表已上传。
3. 从审核通过的版本新建自己的功能分支，例如 `git switch -c feature/your-task`。不要多人直接修改主分支。
4. WorkBuddy 打开克隆目录，要求它只改分配文件、保留原始数据、跑测试，并查看差异后再提交。
5. `git add <具体文件>`、`git commit -m "说明"`、`git push -u origin feature/your-task`；到 GitHub 创建 PR，由负责人审查、合并和部署。

建议一人负责 `frontend/`，一人负责 `api/` 与测试；算法改动在 `engine.py` 和决策输入层单独评审。数据增补走混合建库→机器质检→人工抽查→导出只读发布快照，不直接编辑正式 `catalog.sqlite`。

## 启动新版

Python 3.12。Windows PowerShell：

```powershell
python -m venv .venv-api
./.venv-api/Scripts/python.exe -m pip install -r requirements-api.txt
./.venv-api/Scripts/python.exe -m uvicorn api.app:app --host 127.0.0.1 --port 8600
```

Ubuntu：

```bash
python3.12 -m venv .venv-api
.venv-api/bin/python -m pip install -r requirements-api.txt
.venv-api/bin/python -m uvicorn api.app:app --host 127.0.0.1 --port 8600
```

浏览器打开 `http://127.0.0.1:8600/`。前后端同域，不需要 Node 构建或 CORS。API-only 无 Streamlit、原 Excel、原图片 ZIP、密钥依赖。独立验证：`python scripts/api_smoke.py --self-host`，会实际请求 TCP 接口并解析 CSV/XLSX/JSON。

开发完整测试需安装 `requirements-dev.txt`。运行 `scripts/check_release.py --root .`、`python -m unittest discover -s tests -v`、`node --test tests/frontend/*.test.mjs`；数据管线测试设置 `PHONE_CATALOG_DB=data/catalog.sqlite` 后运行 `python -m unittest discover -s data_pipeline/tests -v`。

## 图片与 DeepSeek

`data/image_manifest.json` 是另行授权的展示层，绑定数据库 SHA-256；`data/media/*.webp` 仅通过允许列表公开。旧数据集 model_id 不直接沿用：按品牌和完整型号精确关联。147 张机型图不代表该 SKU 的颜色、成色或现货。图片授权为负责人声明，不是独立版权审查。

DeepSeek 默认关闭。服务器配置 `PHONE_ENABLE_LLM=true`、`LLM_API_KEY=<新密钥>`、`LLM_MODEL=deepseek-flash` 后重启服务。不要将真实密钥写入 README、Git、前端、浏览器 URL 或聊天；公开过的旧密钥应撤销。接口只让模型选择已存在的事实 ID，程序生成解释，拒绝编造事实，错误回退模板，排名不变。固定官方 API 地址、15 秒超时、无自动重试、单并发、每进程每天最多 20 次尝试；重启重置此计数，**不是账单硬上限**，请另设服务商预算。

## 服务器部署

使用 `deploy/` 中的 systemd 与 Nginx 配置；后端只监听 `127.0.0.1:8600`，公开 Nginx 80。只读程序和数据库，以非登录 `zhixuan` 用户运行。生产只上传允许清单的包，不上传开发环境、源工作簿、原归档、日志或密钥。HTTPS/域名后续另设；HTTP 展示阶段不要发送敏感资料。旧 Streamlit 站点保留回退。
