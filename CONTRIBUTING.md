# 团队协作

## 工作流程

1. 从主分支拉取最新代码。
2. 每个任务新建分支，例如 `git switch -c codex/topsis-tests`。
3. 修改后运行测试，并在界面检查预算、品牌、空结果、全零权重和对比功能。
4. 只暂存本次改动，先用 `git diff --staged` 检查，勿上传密码或未经授权的数据。
5. 推送自己的分支并创建合并请求，请另一名队友审核后合并。
6. 网站部署单独进行；本地提交不会自动更新公开网站。

## 当前模块

- `app.py`：Streamlit 页面、筛选和结果展示。
- `pipeline.py`：**两个界面共用的推荐管线**。串起「抽取 → 契约适配 → 筛选 →
  TOPSIS 排序 → 事实清单」，不 import 任何界面框架。`handle()` 是一轮对话的
  完整流程，`candidate_frame` / `models_payload` / `build_brief` / `ask_text`
  等构件供 Streamlit 的分阶段界面单独取用。
  **新增界面必须调这一层，不要复制里面的逻辑**——复制之后「含糊预算不封顶」
  这类红线就会改一处漏一处。
- `web/server.py`：Starlette Web 服务，把同一套内核暴露给静态前端。
  只用已装好的 `starlette` + `uvicorn`，不新增依赖。
- `web/static/index.html`：静态前端。分类与卡片字段**只按数据表里真实存在的
  列划分**，没有「长续航 / 游戏性能 / 折叠大屏」这类无依据的分类。
- `quiz.py`：快速问答的题库。每题是「生活场景 + 一个内核真能算的维度倾向」，
  选项里的 `say` 是**拼进需求文本的原话**，措辞直接决定抽取结果——
  改文案前先跑 `tests/test_quiz.py`。
  问卷产出后完全走 `pipeline.handle_quiz`，本模块不含任何筛选逻辑。
- `engine.py`：工作簿读取、字段转换和 TOPSIS。暂时保留接口，避免整理时改变排序。
- `schemas.py`：结构定义与有效范围校验，只做校验不做计算，不依赖 pandas/numpy。
- `providers/jev.py`：语义理解适配层。`parse_needs` / `classify_preferences`。
- `providers/llm.py`：外接大模型适配（OpenAI 兼容端点）+ `PROMPT_VERSION`
  + 共用的 HTTP 层 `post_chat` / `content_of`。
- `providers/recommend.py`：把引擎结果写成一段推荐语。**唯一让大模型自由生成
  文字的地方**，因此 prompt 里写死了「不得提到清单外的机型、不得编造参数」。
- `providers/env.py`：`.env` 加载器（零依赖）。优先级「真实环境变量 > `.env`
  > `st.secrets`」，默认**只填不覆盖**。返回值只含键名，不含值。
- `needs_extraction.py`：**需求抽取的对外契约入口**。`extract_needs` / `validate_result`。
  界面与算法都只调这一层，不直接碰 `providers/`。
- `bridge.py`：把抽取结果翻译成界面控件值与引擎入参。
  `bridge_contract()` 是契约 dict 的入口，`bridge()` 是底层实现。
- `tools/check_llm.py`：**真实 API 体检**。本项目唯一会真调线上服务、产生费用的
  入口；测试套件永远走本地假服务。核对契约合规、证据保真、推荐语不越界。
- `docs/needs-extraction-contract.md`：接口契约（字段定义、失败处理、下游用法）。
- `docs/needs-extraction-test-record.md`：**实际测试记录**，由脚本生成，勿手改。
- `tests/test_engine.py`：数据检查、排序方向、稳定 ID 及边界测试。
- `tests/test_schemas.py`：契约层与需求评测集的自洽性测试。
- `tests/test_jev.py`：需求理解层测试。
- `tests/test_needs_extraction.py`：契约入口的三态与字段校验测试。
- `tests/test_recommend.py`：推荐语生成测试（防编造型号、失败兜底）。
- `tests/test_env.py`：`.env` 解析与优先级测试。
- `tests/test_app_flow.py`：**界面端到端测试**。用 `AppTest` 在对话框里真实
  打字、真实发 HTTP，验证「一轮出机型」「多轮追问」「失败退回手动模式」。
- `tests/test_pipeline.py`：管线单测。重点锁契约红线：含糊预算必须追问、
  机型只能来自数据表、追问有上限、弃权后必须出结果、降级到模板文案。
- `tests/test_web_server.py`：**Web 接口测试**。真起一个服务、用标准库
  `urllib` 打真端口，连路由与静态托管一起验。分类器通过
  `app.state.classifier` 注入替身，跑测试不产生线上费用。
- `tests/test_quiz.py`：**题库测试**。逐项断言每个选项被内核读成了什么，
  并穷举全部 3×3×3×3×3 = 243 种答案组合，要求每种都能解析且不触发多余追问。
  改题库文案后必跑。
- `tools/check_quiz_ui.py`：**问答流程的浏览器验证**（Playwright + 本机 Edge）。
  验证 DOM 可见性、答题推进、结果渲染——CSS 特异性与 `hidden` 失效这类
  问题单测抓不到，只有真浏览器能发现。用法：
  `python tools/check_quiz_ui.py http://127.0.0.1:8010`
- `tests/isolation.py`：`IsolatedEnvTest` 基类。**任何会触发 `load_dotenv()`
  的用例都必须继承它**（见「测试必须与开发机的 `.env` 隔离」）。
- `tests/fake_openai.py`：本地假 OpenAI 服务，按 system prompt 区分
  「需求抽取」与「推荐语」两类请求（不参与 unittest 自动发现）。
- `tests/needs_testset.json`：20 条中文需求及人工标准答案。
- `tests/evaluate_needs.py`：需求理解评测脚本（不参与 unittest 自动发现）。
- `tests/record_needs.py`：生成实际测试记录（不参与 unittest 自动发现）。

### 四条不许破的线

1. **机型只能由 `engine.rank()` 产生**。任何时候都不许让大模型直接报机型——
   它不知道本地数据表，会编造型号、价格和参数。
2. **推荐语必须渲染在机型列表之上，且列表始终可见**。推荐语只是解说，
   用户看到的机型以列表为准。
3. **`extract_needs` 与 `write_recommendation` 都不许抛异常**。前者返回
   `status="failed"` 的完整结构，后者返回 `("", 原因)`；界面据此退回手动模式。
4. **两个界面必须共用 `pipeline`**。Streamlit 与 Web 服务都调
   `pipeline.handle`，筛选口径、追问规则、机型来源只有一份实现。
   在界面里复制管线逻辑，等于把上面的红线复制成两份，迟早改一处漏一处。

`rank(frame, weights, target)` 输入候选 DataFrame、五项权重及目标屏幕尺寸，返回带 `score` 的排序表。
新增算法应保持原始配置记录可追溯，并单独测试其排序方向、常量列、同分和异常输入。不同算法分数不得直接视为同一量纲。

## 稳定配置 ID

`load_data()` 用配置身份派生稳定 ID：`stable_id(产品名称, 运行内存, 存储)` 取三者
SHA-1 前 8 位，形如 `SKU-29AD96F7`。约束：

- **与行序无关**：新增、删除或重排数据不会改变既有记录的 ID。旧实现用 Excel 行号生成
  `ROW-xxx`，任何行序变动都会让全部 ID 漂移，无法跨版本复算排序。
- **同型号不同存储必须是不同 ID**，因此 `(产品名称, 运行内存, 存储)` 在数据文件中必须唯一；
  出现重复时 `load_data()` 直接抛错，不静默去重。
- **排序主键仍是 `score`**，ID 只用于同分时的稳定次序。改 ID 方案不得改变任何得分。
- 新增或修改数据后必须跑 `StableIdTests`，不要只改断言里的数字来让测试通过。

`ROW_ID_LEGACY_PREFIX` 仅用于断言「不再生成行号 ID」，不参与运行时逻辑。

## 需求理解评测

`tests/needs_testset.json` 是 20 条中文需求及人工标准答案，覆盖工作文档第 4 章强调的
边界：含糊预算不得自动设上限、模糊程度词不得擅自定量、预算上限与价格偏好分开、
存储门槛与存储偏好分开、未覆盖需求不得用替代指标充数、排除式品牌限定不得当作白名单、
全权委托时不得替用户编造约束。

偏好强度口径见该文件 `_strength_convention`：含「最/必须」等最高级记「非常重要」，
仅含「越…越好」这类明确方向记「重要」，弱方向提示记「一般」。同句式不得给出不同强度。

运行评测：

```powershell
python tests/evaluate_needs.py --impl provider   # 评 providers/jev.py
python tests/evaluate_needs.py --impl baseline   # 内置规则基线（对照）
python tests/evaluate_needs.py --verbose         # 逐例打印
```

**两组实现都带有评估泄漏**：`RuleBaseline` 与 `MockClassifier` 的规则都是参照本
测试集调整出来的，它们的高分不构成语义理解能力证据。接入真实 Jev 服务后必须重测，
届时两组分数只作对照，不得作为交付结果上报。

`MockClassifier` 的定位是「先打通接口的确定性替身」（工作文档第 9 章：先用模拟
服务打通接口，再接真实 API）。真实服务的延迟、中文需求识别率、调用费用需实测。

## 需求理解层（providers/jev.py）

`parse_needs(text, classifier=...)` 返回 `Needs`：偏好分类、原文证据片段、待确认字段。
必须遵守的设计红线（工作文档第 2、4 章）：

- 每个维度只能取 `CHOICES` 中的值，**必须包含「未提及」**；
- 「未提及」（中性先验，映射 1）与「不在意」（用户主动归零，映射 0）语义不同，不得互相顶替；
- 供应商 `confidence` **不是**偏好强度也不是正确率，只作记录；
- 含糊预算（「3000 左右」）不设上限，进 `needs_confirmation` 交给用户；
- 存储门槛（「256G 起步」）与存储偏好（「越大越好」）分开判断；
- 排除式品牌（「不要华为」）不转成白名单，进 `needs_confirmation`；
- 无数据支撑的需求（续航/影像/真实游戏性能）记入 `unsupported_needs`，
  不得用电池容量、像素或刷新率替代；
- 默认 `confirmed=False`，结果必须过用户确认页。

`HttpClassifier` 是真实服务接入点：凭据从环境变量读（默认 `JEV_API_KEY`），
超时 10 秒、最多重试 1 次；失败抛 `JevUnavailable`，由调用方回退到表单/固定问卷。
密钥不得写入代码、日志或仓库。

## 大模型配置

三个环境变量：`LLM_BASE_URL`、`LLM_MODEL`、`LLM_API_KEY`（可选 `LLM_THINKING`）。
来源与优先级：

    真实环境变量  >  .env  >  .streamlit/secrets.toml

- `.env` 由 `providers/env.py` 的 `load_dotenv()` 读取，`app.py` 启动时调用。
  **此前 `.env.example` 承诺的「复制为 .env 就能用」从未生效**——没有任何代码
  读它。改这一环时注意别再把文档和实现对不上。
- 三者都是「只填不覆盖」（`override=False`），所以临时 `export` 一个值永远
  压得住文件里的旧值。这条有测试锁住（`LoadTests.test_existing_env_wins_by_default`）。
- **不要在 `LLMClassifier.__init__` 里隐式加载 `.env`**。`test_llm.py` 的
  `ConfigTests` 用 `mock.patch.dict(clear=True)` 模拟「未配置」，隐式加载会让
  本机真实的 `.env` 污染这些用例。加载必须是入口处的显式动作。

### 测试必须与开发机的 `.env` 隔离

`load_dotenv()` 会写 `os.environ`，这是**全局副作用**。`test_app_flow.py` 通过
`AppTest` 跑真实 `app.py`，`web/server.py` 也会在首个请求时加载 `.env`；两者都会
把 `.env` 的值灌进整个 unittest 进程，再顺着模块执行顺序泄漏到后面的测试
（`test_app_flow` 排在 `test_llm` 前面）。

不隔离的后果：**开发机上有 `.env` 的人跑测试会红，没有的人会绿。**

基类统一放在 `tests/isolation.py`：`IsolatedEnvTest` 把 `LLM_ENV_FILE` 指向一个
不存在的路径——`find_dotenv()` 随即返回 None，`.env` 被彻底屏蔽。
**任何会触发 `load_dotenv()` 的用例都必须继承它**，`test_app_flow.py` 与
`test_web_server.py` 都是这么做的。

### 两个模型侧的坑

1. **`deepseek-chat` 是遗留名**。请求仍被受理，但实际由 DeepSeek-V4.1-Flash
   提供服务并按 Flash 计价，返回体里的 `model` 会被换成别的名字。当前可用名是
   `deepseek-flash` 与 `deepseek-v4-pro`。
2. **thinking 模式默认关闭，别打开**。DeepSeek V4 默认开启，实测同一输入
   4.7× 慢、4.3× 输出 token，抽取结果逐字一致；且 **thinking 模式下
   `temperature` 被静默忽略**，抽取链路依赖的 `temperature=0` 会形同虚设。
   开关是 `LLM_THINKING=1`，理由写在 `providers/llm.py` 的 `thinking_payload`。

`response_format` 与 `thinking` 不是所有兼容端点都认。400 时由
`post_chat_resilient` **逐项剥掉重试**，而不是直接判定服务不可用。

验证接通：

```powershell
python tools/check_llm.py          # 真调线上服务，会花钱，别放进 unittest
```

`providers/env.py` 的返回值只记录**键名**，不记录值——密钥不能出现在任何可打印
的地方。`mask()` 只用于展示。

## 测试

在 `phone-demo` 目录执行：

```powershell
python -m unittest discover -s tests -v
```

测试**永远不连真实服务、不消耗额度**，一律走 `tests/fake_openai.py` 的本地假服务。
唯一会花钱的入口是 `tools/check_llm.py`，它不参与 `unittest discover`。

现有数据测试绑定本地基线（46 款、156 条配置），需要授权数据副本。改变数据时应审核这些断言，不可仅为通过测试而随意改数字。
后续将算法合成数据测试与真实数据集检查分离后，再接入持续集成。
