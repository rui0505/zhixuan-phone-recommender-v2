# 2026-10-11 光影界面发布

当前线上版本：`/opt/zhixuan/releases/glass-ui-20261011-r1`，入口 https://zhixuanji.cn/，服务器 47.86.39.2。回退版本 `deepseek-needs-20261009-r2` 保留。

frontend/ 已同步部署内容：用户插画背景、深青毛玻璃与青蓝交互效果。正式 `/api/v1/` 调用及 DeepSeek 需求草稿 / TOPSIS / 导出 / 详情来源 / 对比流程保持。motion.mjs 仅做视觉增强。scripts/build_deploy_bundle.py 的白名单包含 frontend/assets/zhixuan-world.png。

10 个文件公网 SHA256 一致，服务 ready；数据库及排序指纹不变。真实模型草稿与确认后的 20 个可比配置已浏览器验证；无错误日志。完整发布包、旧文件和证据保存在工作区 outputs/zhixuan-glass-release-20261011/。
