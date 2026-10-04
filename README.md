# 知选手机推荐原型

基于原始工作簿副本运行，支持预算、品牌和最低存储筛选，五项权重、TOPSIS、同机型合并、配置对比及 CSV 导出。权重可手动调整，也可用一句中文描述需求后由外接大模型自动抽取（见下文）。

## 启动

Windows 双击 `启动.cmd`。当前工作区已创建隔离环境 `.phone-venv`。

其他电脑：建议安装 Python 3.11，克隆仓库后在 `phone-demo` 目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

仓库已包含本次部署获准使用的 `data/phones.xlsx`，详情见 `data/README.md`。`启动.cmd` 仅用于当前电脑已有的父目录环境；新队友使用上述命令。

Streamlit 页面位于 `app.py`，静态前端与 Web 服务位于 `web/`，**两个界面共用的推荐管线位于 `pipeline.py`**（快速问答的题库在 `quiz.py`），字段转换与算法位于 `engine.py`，结构校验位于 `schemas.py`，测试位于 `tests/`。团队开发流程见 `CONTRIBUTING.md`。

配置记录使用由内容派生的稳定 ID（`SKU-xxxxxxxx`），不再使用取决于行序的 `ROW-xxx` 编号；同型号不同存储为不同 ID。详见 `CONTRIBUTING.md` 的「稳定配置 ID」。

数据位于 `data/phones.xlsx`，仅按负责人同意的部署及网页展示用途使用，不代表附有通用数据许可证。

## 对话框（自然语言 → 机型）

页面底部有一个对话框。用一句话说需求（例如「预算三千左右，想要轻一点的」），系统会：

1. **理解需求**：抽成五项偏好等级与硬约束；
2. **追问补齐**：信息不全时先问（如「你说的是『2000 左右』，没有明确上限。最多能接受到多少？」），最多两轮；
3. **算出机型**：用你本地数据表跑筛选 + TOPSIS，给出前五款；
4. **写一段话**：把引擎算出的真实结果交给大模型，写成一段自然的推荐说明。

条件会同步填进左侧滑块，可以继续手动微调；完整参数与对比在下方「为你推荐」标签页。

### 谁负责什么（重要）

| 环节 | 负责方 |
|---|---|
| 理解中文需求 | 大模型（`needs_extraction`） |
| 筛选、排序、给出机型与参数 | **本地引擎**（`engine.rank`） |
| 把结果写成一段话 | 大模型（`providers.recommend`） |

**机型永远由引擎算，不由大模型报。** 大模型不知道本地数据表里有哪 46 款机型，
让它直接推荐只会编造型号、价格和参数。它的两个职责是「理解」和「措辞」，
输入是引擎给的真实数据，没有机会引入新事实。

界面始终把引擎的机型列表渲染在推荐语下方——**以列表为准，不以推荐语为准**。

链路：`app.py` → `needs_extraction.extract_needs()`（**契约入口**）→ `providers/llm.py`（组装 prompt 调 OpenAI 兼容接口）→ `providers/jev.py`（归一化与安全降级）→ `bridge.bridge_contract()`（翻译成控件值与引擎入参）→ `engine.rank()` → `providers/recommend.py`（写推荐语）。

接口契约见 [`docs/needs-extraction-contract.md`](docs/needs-extraction-contract.md)，实际测试记录见 [`docs/needs-extraction-test-record.md`](docs/needs-extraction-test-record.md)。

### 已知限制

- **「一般」与「未提及」在当前权重映射里都取 1**，所以「轻一点」这类弱表达
  不会改变权重。要拉开差距请说「越轻越好」。这是 `schemas.initial_weights`
  的确定性映射规则，属于算法侧的决策，未在本轮改动。
- 推荐语可能偶尔不通顺，但**机型不会错**——列表来自引擎。
- 匹配度是候选集内的相对得分，换条件名次会变，不是质量评分。

### 接入真实 API

需要任意 OpenAI 兼容的 `/chat/completions` 端点（DeepSeek、通义、智谱、Kimi、本地 Ollama 均可）。

**方式一：`.env` 文件（本地开发，推荐）**

```powershell
cd phone-demo
cp .env.example .env
# 编辑 .env，填入真实值：
#   LLM_BASE_URL=https://api.deepseek.com/v1
#   LLM_MODEL=deepseek-flash
#   LLM_API_KEY=sk-xxxxxxxx
.\.venv\Scripts\python.exe -m streamlit run app.py
```

**方式二：环境变量**

```powershell
$env:LLM_BASE_URL="https://api.deepseek.com/v1"
$env:LLM_MODEL="deepseek-flash"
$env:LLM_API_KEY="sk-xxxxxxxx"
.\.venv\Scripts\python.exe -m streamlit run app.py
```

**方式三：Streamlit secrets（云端部署）**

复制 `.streamlit/secrets.toml.example` 为 `.streamlit/secrets.toml` 并填写。

**优先级**：真实环境变量 > `.env` > `st.secrets`。三者都是「只填不覆盖」，
所以临时 `export` 一个值永远压得住另外两个。

**方式四：不配置**。界面自动退回纯手动滑块模式，推荐功能不受影响。

`.env` 与 `secrets.toml` 都在 `.gitignore` 里。密钥只从环境变量或文件读取，
**不写入代码、日志或提交历史**（`load_dotenv` 只返回「写入了哪些键名」，不返回值）。

#### 两个容易踩的配置坑

**① `deepseek-chat` 是遗留模型名。** 请求仍会被受理，但实际由
DeepSeek-V4.1-Flash 提供服务并按 Flash 计价，返回体里的 `model` 会被换成
别的名字。当前可用名是 **`deepseek-flash`**（DeepSeek-V4.1-Flash）与
**`deepseek-v4-pro`**。

**② thinking 模式默认已关闭，别打开。** DeepSeek V4 默认开启 thinking，
对本项目是纯亏。同一输入实测（`deepseek-flash`，同一 prompt）：

| | thinking 开（DeepSeek 默认） | 本项目默认（关） |
|---|---|---|
| 墙钟耗时 | 4166 ms | **890 ms** |
| completion tokens | 836 | **195** |
| 其中推理 tokens | 640 | 0 |
| 抽取结果 | — | **逐字一致** |

更要紧的是：**thinking 模式下 `temperature` 被静默忽略**（官方文档明说
「不会报错，但也不会生效」），而抽取链路依赖 `temperature=0` 保证可复现。
需要时用 `LLM_THINKING=1` 打开。

另外，`response_format` 与 `thinking` 这两个字段不是所有 OpenAI 兼容端点都认。
不认的端点回 400 时，代码会**逐项剥掉这两个字段重试**，而不是直接判定服务不可用
（`providers/llm.py` 的 `post_chat_resilient`）。

### 验证真的接通了

```powershell
.\.venv\Scripts\python.exe tools\check_llm.py
```

**这是本项目唯一会真调线上服务、产生费用的入口**（测试套件永远走本地假服务）。
它会：

1. 打印配置摘要（密钥打码）与 `.env` 加载情况；
2. 真调一次需求抽取，打印服务商、模型、耗时、原始契约 JSON；
3. 核对 `validate_result` 的契约合规性，以及**证据是否逐字来自原话**；
4. 喂真实引擎结果生成推荐语，核对**推荐语没有提到清单外的机型**。

退出码：`0` 全通过 / `1` 有检查项失败 / `2` 配置不全。

```powershell
.\.venv\Scripts\python.exe tools\check_llm.py --text "预算四千左右，拍照好一点，别太重"
.\.venv\Scripts\python.exe tools\check_llm.py --json          # 打印完整契约 JSON
```

### 不用密钥试跑

```powershell
.\.venv\Scripts\python.exe tools\dev_server.py
```

启动本地假大模型服务（返回格式与真实供应商一致）+ Streamlit，用于演示与排查线格式问题。

### 单条需求调试

```powershell
.\.venv\Scripts\python.exe tools\extract.py --offline "预算三千左右，越便宜越好"
.\.venv\Scripts\python.exe tools\extract.py --fake-server --raw "不要华为，存储 512G 以上"
.\.venv\Scripts\python.exe tools\extract.py --show-prompt
```

### 设计红线（代码与测试均已固化）

- 含糊预算（「3000 左右」）不自动封顶，但数字要作为**参考值**留下；硬边界只认
  「N 到 M」「N 以内」「N 以上」三种明确表达
- 含糊预算的红线检查**只看最后一句用户输入**，且只在该含糊数字本身被写进
  `min`/`max` 时才判违规。多轮里用户先含糊后补的明确上限必须生效——
  否则用户老老实实答完追问，反而会被判「需求未通过接口检查」
- 参考预算**永不参与筛选**（`HardConstraints.to_filter_kwargs()` 刻意不含它）
- 排除式品牌（「不要华为」）走独立过滤，不转成白名单
- 「未提及」与「不在意」是两件事，未提及走中性先验
- 数据覆盖不到的需求（续航、影像、真实游戏性能）只提示，不用替代指标充数
- 模型给出的证据必须逐字出现在原文里，否则记入待确认清单（保留判断，但提示证据不可信）
- 抽取结果**只输出中文等级，不输出数字权重**；等级 → 权重的映射是算法侧的事
- 任何失败都不抛异常，返回 `status="failed"` 的完整结构，界面能无条件渲染

## Web 前端（静态页 + 本地引擎）

除 Streamlit 之外，还有一个**静态前端 + 独立 Web 服务**的组合，用于把同一套算法
内核接到产品化的页面上。

```powershell
.\.venv\Scripts\python.exe web\server.py
# 浏览器打开 http://127.0.0.1:8000/
```

端口用环境变量 `PORT` 改，监听地址用 `HOST`（默认 `127.0.0.1`）。

**两边共用同一份算法**：`app.py`（Streamlit）与 `web/server.py` 都调用
`pipeline.handle`，所以筛选口径、追问规则、机型来源完全一致，不会分叉。

### 接口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/` | 静态页（`web/static/index.html`） |
| GET | `/api/meta` | 品牌、预算量程、存储档位、维度标签、数据规模、大模型状态 |
| GET | `/api/phones` | 全部真实配置（46 款机型 / 156 条配置） |
| GET | `/api/quiz` | 快速问答的题库 |
| POST | `/api/recommend` | 一句话 → 追问清单 或 机型清单 |
| POST | `/api/quiz/recommend` | 答卷 → 机型清单（不追问） |

`POST /api/recommend` 的请求体：

```json
{"text": "预算4000以内，想要轻一点的",
 "history": ["上一轮说的话"], "asked": ["budget.max"], "ask_rounds": 1,
 "top_n": 5, "write": true}
```

`history` / `asked` / `ask_rounds` 是多轮对话状态，**由前端保存并原样回传**
（服务端无状态）。`write=false` 只出机型、不生成推荐语。

`POST /api/quiz/recommend` 的请求体：

```json
{"answers": {"budget": "b2", "scene": "photo", "carry": "light",
             "screen": "large", "storage": "s512"},
 "top_n": 5, "write": true}
```

选项编号由 `GET /api/quiz` 给出。答卷经 `pipeline.handle_quiz` 拼成一句中文需求，
走**与自由输入完全相同**的链路——`tests/test_web_server.py` 里有一条测试专门锁住
这一点（同样的答案，问答入口与自由输入入口给出的候选数必须相等）。

### 快速问答（「你拿这台手机干什么」）

首屏那句「快速问答，为你推荐最好的」打开一份五题的问卷，每题 A/B/C 三选一：
预算、主要用途、携带方式、屏幕尺寸、存储容量。答完直接出机型。

题库在 `quiz.py`，**不在页面里**（改文案只需改一处）。设计上有一条硬约束：

> 每道题的选项都必须做两件事——① 用**生活场景**描述，让不懂参数的用户也能选出来；
> ② 携带一个**内核真能算的维度倾向**。否则这道题对结果毫无影响，用户白答。

能算的只有价格、重量、存储、刷新率、屏幕五项。「拍照」「打游戏」照问，但它们的
价值是让用户觉得被听见，同时**如实进 `unsupported_needs`**，结果区会明说
「这几项本次没有数据依据，不会用别的参数替代」——不会拿处理器型号去替拍照下判断。
`tests/test_quiz.py` 会穷举全部 3×3×3×3×3 = 243 种答案组合，断言每种都能被正确
解析且不触发多余追问。

### 密钥不出服务端

前端只发一句中文给本服务，**从不接触密钥**。大模型调用全部发生在服务端，
密钥只经 `.env` 或环境变量进入 `os.environ`。

### 页面上的维度只按数据表里真实存在的字段划分

原始数据表有价格、重量、屏幕尺寸、刷新率、存储、品牌、处理器、像素串，
**没有电池容量、没有折叠形态**。所以页面上的分类是「小屏 / 大屏 / 轻薄 / 高刷 /
大存储」与「按预算 / 按品牌」，卡片只讲价格、重量、屏幕、刷新率、存储、品牌、
像素与处理器——**不存在「长续航」「游戏性能」「折叠大屏」这类分类**，
因为那会是凭空编造。测试（`tests/test_web_server.py`）会拦住这类回退。

## Streamlit Community Cloud 部署

关联 GitHub 仓库 `rui0505/zhixuan-phone-recommender`，选择分支 `master`、入口 `phone-demo/app.py`。建议选择 Python 3.11；依赖文件为入口同目录的 `requirements.txt`。应用访问权限需在平台单独设置；GitHub 仓库私有不等于网页私有。

## 算法边界

价格为原表记录价；摄像头、处理器和充电功率仅展示。屏幕偏好按绝对距离处理。排序为候选集内相对结果；未实现偏好学习、实时价格或性能测评。权重均为零时不计算。缺失数值会拒绝评分。

测试：`python -m unittest discover -s tests -v`。
