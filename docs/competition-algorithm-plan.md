# 竞赛算法实验室 Implementation Plan

**Goal:** 交付可运行、可对照、可追溯的多决策交互推荐实验室。

**Architecture:** 原推荐引擎提供输入校验与对照。新增纯算法包、会话层、独立实验页面、
可复现实验脚本；原数据库、数值和线上默认入口不改变。

**Tech Stack:** 现有 Python / NumPy / pandas / Streamlit / unittest，无新增第三方依赖。

**Spec:** [设计](competition-algorithm-design.md)，由当前会话直接执行。

## Global Constraints

- 数据和指标比较口径由原 DecisionInput 决定；关闭指标不重开。
- 原 TOPSIS 不替换；新模块标记 experimental，保留结果和限制。
- 不共享密钥、不调用付费 API、不部署、不合并主分支。

## Review Focus

- 并列不能因为 SKU ID 次序制造“第一名概率”。
- 答案重放、撤销及跳过不能重复更新后验。
- 修改条件或发布数据后不能沿用旧会话。
- 排序优化不能改变 TOPSIS 原数值口径。
- 合成评测不能把询问题目重复当成最终测试题。

## Task 1: 排序核心

Files: `algorithms/ranking.py`, `tests/test_competition_ranking.py`
Interface: `DecisionProblem(di, target_screen)`, `scores(weights, theta, alpha, k)`。

- [x] 写手算 TODIM、TOPSIS 等价、RRF 同分、异常输入测试，先运行确认失败。
- [x] 实现冻结矩阵、向量化多粒子排序、VIKOR 对照。
- [x] 运行核心测试并检查结果。

## Task 2: 交互学习

Files: `algorithms/preference.py`, `tests/test_competition_preference.py`
Interface: `PreferenceSession(problem, config)`, `question()`, `answer()`, `undo()`, `report()`。

- [x] 先测试答案更新方向、幂等保护、跳过/撤销、选题限制和支持率。
- [x] 实现有界粒子后验、IG/分歧选题、会话证据与导出。
- [x] 运行排序与偏好测试。

## Task 3: 可运行入口

Files: `algorithm_lab.py`, `tests/test_algorithm_lab.py`, `README.md`
Consumes: Task 1/2 原生 Python 接口；使用 ReleaseStore 校验目录。

- [x] 先写启动、确认、回答、撤销、条件变化失效的 AppTest。
- [x] 实现独立实验室，所有推荐来自同一算法包，展示可核对原始参数。
- [x] 真实目录运行、界面测试与原项目回归。

## Task 4: 竞赛实验

Files: `scripts/evaluate_competition.py`, `tests/test_competition_evaluation.py`,
`docs/competition-algorithm-results.md`

- [x] 测试用户划分/留出候选对、确定性和报告指标。
- [x] 实现多策略、多用户、两类效用的可复现实验。
- [x] 运行实验与敏感性对照，保留原始结果，报告负结果。
- [x] 全套回归、代码审查、提交独立分支；不合并或部署。

## 验证记录（2026-10-22）

- 排序/偏好/评测针对测试 31 项通过，真实目录 AppTest 3 项通过。
- 全项目 unittest 673 项通过；发布库只读校验 PASS。整体数据人审/WPS 状态仍按发布清单单列。
- 浏览器已实际验证选择并提交、进入下一题并清空旧选项、跳过、撤销、算法切换及权重区间显示。
- 合成评测 3 个种子、216 份潜在权重、432 个用户×效用情景；25 方法/情景。
- 独立审查未发现新的阻断问题；同预算基线、Q 方向、配对区间与 TODIM 反转已复核。
- 原始实验与截图保留在 local_evidence/；可分享摘要、完整报告和接入说明随分支交付。
- 独立分支保留；未合并主线、未推送或更新云服务器。
