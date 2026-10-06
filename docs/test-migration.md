# 测试范围与迁移

原包独立环境基线：481 tests、0 failures。46 机型/156 配置及最高分 0.684495 在本地原包副本验证并留日志。
它们是旧数据结果，不能套到新版 147 机型/150 版本/484 SKU。

- 原有需求、预算、追问、fake HTTP 测试保留，不跳过失败。
- `test_app_flow.py` 改跑 `reference_streamlit.py`，仅证明历史抽取契约，不证明新版上线。
- 新 `test_app_catalog.py` 覆盖生产快照、确认、不足、缓存、对比、抽取失败与安全。
- `legacy_fixture.py` 临时合成表：9 解析示例名称/18 配置，数值全虚构，不是产品事实。统计断言改为合成规模，样例价重合成 2000/180；ID/筛选/异常/HTTP 字段断言不变。
- 0.684495 原基线仍留日志，不对新合成值重断言；改用独立双机型闭式期望（仅价格差异，1/0）。
- 单机型旧 .5 成功改为不足；至少两个独立机型且确实同分才并列 .5。
- 模型测试只显式本地 fake/loopback，fake key 覆盖并恢复环境，生产三开关默认关闭。
- 管线测试可设 `PHONE_CATALOG_DB=data/catalog.sqlite` 跑发布库回归；全量单元格/重建稳定性走本地 acceptance 独立检验。

CI 未实际运行不能说 GitHub Actions 通过；WPS/人工待验不计为机器 PASS。干净环境实测由发布记录更新。

2026-10-06：全新 Python 3.12.14 + 仅版本化源码/快照，无 data/phones.xlsx、桌面输入和密钥；应用 566/566、管线 35/35、release PASS、pip check 通过。原始全量 acceptance 仍为机器 12/12、人工/WPS 待验。
