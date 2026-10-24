# 首页主视觉替换（本地，2026-10-23）

用户选定候选一：Max Petrunin 的 AI 数据网络图片。图片由 Unsplash 官方 CDN 下载，
本地文件 `frontend/assets/zhixuan-ai-network.jpg`，1800×1800 JPEG，600896 字节。
来源、许可链接和 SHA-256 见 `frontend/assets/credits.json`，页脚保留作者链接。

变更仅涉及首页视觉和发布文件清单：

- `frontend/index.html`：换用新图，将图片限制到首页介绍区，更新样式缓存版本和作者来源。
- `frontend/glass.css`：桌面右侧构图、文字侧遮罩、手机裁切；输入区和结果区不铺图片。
- `scripts/build_deploy_bundle.py`、`tests/test_deploy_bundle.py`：发布包包含新图和来源清单，
  不再带上未使用的二次元背景。旧 `zhixuan-world.png` 原文件保留，未覆盖或删除。
- 既有需求抽取、推荐 API、排序算法、数据库和其他未提交改动均未调整。

本地验收：打包测试 1 项通过、前端 Node 测试 15 项通过；新图 HTTP 200，JPEG 可解码，
`/readyz` 返回 ready。桌面及 390px 手机宽度实测无横向溢出，标题和输入区可读。
设计扫描仅提示原有发光阴影和极窄屏小字号，未扩大本次范围改写整站设计。

预览：http://127.0.0.1:8603/ 。本次没有推送、合并或部署云服务器。
算法实验分支 `codex/competition-preference-fusion` / `b8cfd71` 与本次视觉替换是不同工作副本；
其研究算法尚未接入生产 HTML/API，不能把更换图片当成算法已上线。
