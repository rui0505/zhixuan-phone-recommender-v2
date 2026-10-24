# ECS 初步演示交付（2026-10-07）

公网入口：http://47.243.189.12/ 。HTML 灰绿排版 + Starlette/Uvicorn 独立 API + 只读 SQLite + 147 张精确机型 WebP。不是 Streamlit 新皮肤，原 Streamlit 入口保留。

代码 PR：https://github.com/rui0505/zhixuan-phone-recommender-v2/pull/1 。分支 `codex/html-api-ui` 已推送，未合并；主分支为 `main`。队友使用自己的 GitHub 账号接受私有仓库邀请，克隆后运行 `git switch codex/html-api-ui`，不要共享账号、密钥或远程 root。

## 部署与实测

- 实例：香港 Ubuntu 22.04 / 2 核 2 GiB。当前目录 `/opt/zhixuan/current` 指向 `/opt/zhixuan/releases/html-api-20261007-0d658b1`；安装包 SHA `18105882902204ecb1cbd7843db9380cfc9a502bbcb55655074448f1021787d4`，186 个白名单文件 + 包清单，147 图。
- Nginx 对外 80，代理 `127.0.0.1:8600`；API 以 `zhixuan` 用户运行，有 systemd 沙箱/内存限制、单 worker、限流。安全组只新增用户批准的 TCP 80 / 0.0.0.0/0；没有扩大后台、数据库或 SSH 访问。
- 服务器本地与用户电脑通过公网真实 TCP：默认新机 51 条完整排名，CSV/XLSX/JSON 全部解析、SKU 次序及证据指纹一致。公网页面已生成 51 排名、真实图片解码、准确 SKU 详情/来源与两配置对比。`/healthz`、`/readyz` 为 200。无前端错误日志。
- 安装脚本重载 Nginx 后首次立即探测 404；后续独立内网与公网探测成功。该脚本设计为首次安装，不可盲目重跑覆盖既有安装。
- GitHub 首次 Linux 检查暴露 Git 换行转换，已用 `.gitattributes` 对两份发布文件禁用文本转换；暂存 Git 字节与原文件一致，修复后的 push/PR 检查均通过。不是修改 JSON 值或重新生成发布库。

数据库 SHA：`128207745f5450c285cb8d6c61ff2c692f19387f2df0823690cd5b323c453bc8`；原发布清单 SHA：`d7d899e07c540deab0ff01ae19555765cd159809dd7c7bce4b3499b87b45ee22`。默认决策 `3ef65bfa4fa8db48c0177a42bb5596209e1f79726d3ad34fe1b7ab5689fd45d6`，排名 `d096455fd8f7a913356522712329e3d6e435295974bd0f0703c57e6a7c3a5a85`。

## 后续改算法与数据

界面：`frontend/`；HTTP 契约与调用：`api/`；统一排序核心：`engine.py` 的 `rank_decision_input`；目录、可比输入、证据：`catalog/`；建库：`data_pipeline/`。先建立个人功能分支，用合成数据与离线回归验证，再 PR。VIKOR 要新增实现及算法版本，不返回模拟分数。LLM 不能替代 ranking 核心。

图片与数值分开：`data/image_manifest.json` 绑定数值快照，图片模型 ID 精确关联；不代表 SKU 实际颜色、二手成色和库存。数据库更新后旧图覆盖层失配会隐藏/拒绝媒体，需要重新建立精确映射与批准发布。原 Excel/ZIP/源文件不进入部署包。

## DeepSeek 与运维边界

API 接入框架已存在，但 `llm=false`；本轮没有用聊天中暴露的旧密钥、没有真实模型调用。请自行撤销旧密钥。新密钥仅由维护者在服务器私下写入 `/etc/zhixuan.env`（权限 600），设置 `PHONE_ENABLE_LLM=true`、`LLM_API_KEY`，重启 `zhixuan`。不要提交到 Git、前端、聊天或 URL。运行默认最多 20 次尝试/进程/日，重启会重置；不是供应商账单硬上限，需另设供应商预算。模型只返回合法事实 ID，程序生成解释，不改变推荐。

此为 HTTP/IP 初步演示，尚未配置域名/HTTPS；不要输入敏感信息。正式长期公开前补 HTTPS、密钥/费用管理、价格时效与运维告警。免费额度用尽、到期及流量超额可能计费，需账号负责人自行设置提醒和后续续用/停止计划。

待人工：WPS、20 条数据抽查、指定宽度新版视觉、OS Blob 下载落盘。639 应用/37 管线/12 Node 离线测试已通过，不能据此声称上述人工项通过。整体数据验收仍 PENDING，拍照/游戏数据不足时不补零；游戏不是完整绝对性能评分。

完整设计裁决与未判断范围：[审查记录](html-api-final-review.md)。
