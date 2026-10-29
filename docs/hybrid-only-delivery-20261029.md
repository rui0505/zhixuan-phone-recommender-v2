# 仅保留 TOPSIS + TODIM 混合算法（2026-10-29）

正式选机页面与推荐接口现在仅提供 `hybrid_rrf`（TOPSIS + TODIM，经 RRF 融合排名）。固定参数 theta=1、alpha=0.5、k=10 沿用既有实现；不宣称优于原 TOPSIS，算法版本仍为 `hybrid-rrf-1.0.0-experimental`。

- 页面仅显示一个固定混合模式；初始加载、需求草稿、请求状态与默认请求均使用混合模式。
- `/api/v1/meta` 的 defaults.method 为 hybrid_rrf，algorithms 仅列出该模式。
- 省略 method 使用混合模式，显式请求 topsis 返回 422。先前保存的独立 TOPSIS 请求需刷新页面后重新确认。
- 保留内部 TOPSIS 数学实现，供混合算法与基线数值测试使用；公开选机入口不再提供单独 TOPSIS。
- 沿用已有目录、评论、录入及发布快照，未改评分规则、模型配置或维护密钥。

## 云端与公网验收

同一现有实例的 current 已从 `/opt/zhixuan/releases/intake-feedback-20261028-r1` 切换到 `/opt/zhixuan/releases/hybrid-default-20261029-r1`。先运行隔离服务验证，再原子切换并重启应用；原目录保留用于回退。服务器报告 `/root/hybrid-default-20261029-report.json`。

公网 HTTPS 实测：meta 仅混合算法；默认推荐 ready、40个配置；单独 TOPSIS 返回422；CSV/XLSX/JSON导出均200；目录仍147机型、484配置。Edge正式页面实际完成确认和推荐，显示“融合排序分”，设置只剩混合算法。

证据留在本机忽略目录：`local_evidence/hybrid-only-public-20261029.json`、`local_evidence/hybrid-only-public-20261029.png`。发布脚本及运行补丁仅在本机证据目录与服务器保留，不含密钥。

## 验证与代码同步

测试夹具明确选择当前混合算法；原独立 TOPSIS 接口断言改为拒绝测试，算法数值基线仍保留。新增测试覆盖默认算法、唯一算法能力、旧模式拒绝及页面选项移除。最终本地检查结果与GitHub同步状态见下方追加记录。

最终本地验证：完整844项回归中843项通过，剩余一项为旧测试使用价格作排序权重；将该测试改为七维中的存储权重后，其所属24项需求/性能检查全部通过。此次修正仅涉及测试数据，运行代码未再改动。另有92项Node、37项数据管线通过，发布快照检查PASS，公网实际推荐与三格式导出通过。

代码将沿现有 `codex/user-feedback-20261028` 分支及 PR #8 同步；main 合并状态与云端激活分别记录。
