# 独立 v2 发布清单

- [x] 原件：四输入哈希不变、36803 单元格一致。
- [x] 全量：147/150/484，外键 0，独立重建业务摘要/注册表一致。
- [x] 发布：catalog-2026-10-06-v2.1 / rules-2.3.0；2,904,064 bytes，SHA 128207745f5450c285cb8d6c61ff2c692f19387f2df0823690cd5b323c453bc8。
- [x] 本地真实浏览器：排序、来源、SKU 对比、中文 CSV/XLSX 下载。
- [x] 当前发布快照本地/匿名云端 CSV 和 XLSX 全字段复算：默认新机 51 条，第一 S0462，score 0.8583227811355777；决策/排序哈希一致。
- [x] 全新 Python 3.12.14 环境，无原始工作簿副本：应用 576/576、管线 37/37、release PASS；pip check 无冲突（2026-10-06）。
- [x] 一次整体独立审查；五个 Important 经 RED→GREEN 修复，无 Critical 或 Minor。
- [x] 新私有 GitHub 实际建立；功能源码 main 3fbab3b25c168a25910db116a2f5c4855dcf74c4，独立克隆、安装、启动与发布检查通过。
- [x] 新 Streamlit 实际构建，退出平台管理账号后公开无登录；本地同请求一致。
- [x] 匿名目录、S0001 详情/来源、S0001 与 S0011 对比、预算上限 1 元无候选、游戏权重 10 数据不足提示。
- [x] 旧站仍为 46 机型/156 配置；因闲置休眠，点击平台唤醒后正常显示，代码和数据未替换。
- [ ] 平台独立的运行提交 SHA 证明（未暴露，不能据 main HEAD 推定）。
- [ ] WPS/20 条人工抽查（待完成，整体 PENDING）。

图片发布 0、依据待确认；VIKOR 未实现；真实模型未启用；原件未上传。
提交、HTTP200、平台已创建不能代替功能验收。

本次只修正 2 条内外屏刷新率歧义的派生值为 NULL；原文和原始数值均未改。
重建后机器验收 12/12 PASS、ID 注册表与独立业务摘要一致；人工 0/20、WPS NOT_CHECKED，整体 PENDING。
最新 XLSX 已检查 15 张表预览；本次重新生成的 DOCX 尚未进行渲染复核，不作为已视觉验收的最终文档交付。

直接依赖已锁定；实测传递版本记录在 manifest.runtime_contract，未声称全依赖锁定。
GitHub Linux CI 已实际成功：[Offline catalog checks](https://github.com/rui0505/zhixuan-phone-recommender-v2/actions/runs/37466510009)。
云端 Python 3.12.15；平台对 PyArrow 25.0.0 的自动兼容修复为 24.0.0，与本地环境记录分开。
新站：https://zhixuan-phone-recommender-v2-kkpzqq7j9pqblxb5ka6u29.streamlit.app/ 。
退出 Streamlit 管理账号用于匿名测试；管理员下次管理应用需重新登录，GitHub 登录未退出。
导出新数据快照后必须重新验证干净环境并更新 runtime_contract，不能照抄本轮测试状态。
