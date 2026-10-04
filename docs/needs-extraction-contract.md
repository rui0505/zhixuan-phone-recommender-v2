# 需求抽取模块 · 接口契约

**状态：已确认（2026-10-01）。** 三个决定已定稿，实现已完成，实际测试记录见
[`needs-extraction-test-record.md`](./needs-extraction-test-record.md)。

- 负责人：需求抽取模块
- 上游：界面（用户输入的中文需求文本）
- 下游：算法（按用户确认的偏好生成权重、筛选、跑 TOPSIS）
- 本模块**不做推荐**，**不计算数值权重**

实现入口：`needs_extraction.extract_needs()` / `needs_extraction.validate_result()`

---

## 1. 职责边界

| 做 | 不做 |
|---|---|
| 把中文需求文本抽成结构化字段 | 推荐具体机型 |
| 记录原话依据（evidence） | 计算数值权重 |
| 标出需要用户澄清的字段 | 排序、TOPSIS |
| 标出数据覆盖不到的需求 | 判断"拍照好"该给多少分 |
| 调用 DeepSeek API 并处理失败 | 开发 Harness、训练模型 |

---

## 2. 输入输出

```python
extract_needs(user_text: str) -> dict
```

- 输入：用户在网页里写的一句或多句中文购机需求
- 输出：**固定结构的 dict**（见第 3 节），**任何情况下都不抛异常**

---

## 3. 返回结构

以你给的例子为例：

> 输入：`想买 2000 元左右、拍照好点、轻一点的手机。`

```json
{
  "schema_version": "1.0",
  "prompt_version": "extract-needs-v1.1",
  "status": "ok",
  "input_text": "想买 2000 元左右、拍照好点、轻一点的手机。",

  "budget": {
    "reference": 2000,
    "min": null,
    "max": null,
    "evidence": "2000 元左右"
  },

  "min_storage": null,
  "target_screen": null,

  "brands": {
    "include": [],
    "exclude": []
  },

  "preferences": [
    {"criterion": "price",   "level": "未提及", "evidence": ""},
    {"criterion": "weight",  "level": "一般",   "evidence": "轻一点"},
    {"criterion": "storage", "level": "未提及", "evidence": ""},
    {"criterion": "refresh", "level": "未提及", "evidence": ""},
    {"criterion": "screen",  "level": "未提及", "evidence": ""}
  ],

  "unsupported_needs": [
    {"need": "影像", "evidence": "拍照好点"}
  ],

  "clarifications": [
    {
      "field": "budget.max",
      "question": "「2000 元左右」没有明确上限，你能接受到多少？",
      "reason": "含糊预算不自动封顶"
    }
  ],

  "meta": {
    "provider": "deepseek",
    "model": "deepseek-chat",
    "elapsed_ms": 1830,
    "error": null
  }
}
```

**这个例子的要点**（对应你强调的两条红线）：

- `budget.reference = 2000` —— **记下了**你提到的数字
- `budget.max = null` —— **没有**擅自把上限设成 2000
- `unsupported_needs = [影像]` —— 拍照需求**保留**，并标记为数据覆盖不到
- `preferences` 里**没有**任何"摄像头像素权重"之类的东西

---

## 4. 字段定义

### 4.1 `budget` 对象

| 字段 | 类型 | 含义 |
|---|---|---|
| `reference` | number \| null | 用户提到的**单一锚点数字**，仅供界面显示与确认页引用。**永远不参与筛选。** |
| `min` | number \| null | 硬下界，只记**明确表达** |
| `max` | number \| null | 硬上界，只记**明确表达** |
| `evidence` | string | 原话片段，逐字复制 |

`reference` 的取值规则：

| 用户原话 | reference | min | max |
|---|---|---|---|
| `2000 元左右` | 2000 | null | null |
| `三千多` | 3000 | null | null |
| `预算 3000 以内` | 3000 | null | 3000 |
| `6000 到 9000` | null | 6000 | 9000 |
| `2000 到 3000` | null | 2000 | 3000 |
| `不超过 5000` | 5000 | null | 5000 |
| 完全没提 | null | null | null |

> 有明确区间时 `reference` 留 null —— 区间已经由 min/max 表达，再编一个中值属于无中生有。

### 4.2 `preferences` 数组

固定 5 项，**顺序不限但必须齐全**：

| `criterion` | 含义 |
|---|---|
| `price` | 价格更低 |
| `weight` | 机身更轻 |
| `storage` | 存储更大 |
| `refresh` | 刷新率更高 |
| `screen` | 屏幕接近偏好 |

`level` 取值**只能**是这五个：

| `level` | 含义 |
|---|---|
| `未提及` | 用户**根本没提到**该维度 |
| `不在意` | 用户**主动否定**该维度（"价格无所谓"） |
| `一般` | 弱方向提示或显式低强度（"别太重"） |
| `重要` | 明确方向性偏好（"越轻越好"） |
| `非常重要` | 最高级或强制表达（"最轻"、"必须轻"） |

> ⚠️ **`未提及` 与 `不在意` 是两件完全不同的事。** 前者走中性先验，后者是用户主动归零。
> 二者不可合并，也不可由模型推断（"他夸了重量，应该不在乎价格" ← 禁止）。

> **本模块只输出等级，不输出数字。** 等级 → 数值权重的映射由算法同学负责，映射表在
> `schemas.py` 的 `LEVEL_MAP`，是双方共用的唯一来源。

### 4.3 `unsupported_needs` 数组

数据覆盖不到的需求。`need` 取值限定在：

```
影像 / 续航 / 真实游戏性能 / 分辨率 / 机身尺寸手感 / 信号质量 / 处理器性能
```

> ⚠️ **不得用替代指标充数**：拍照 ≠ 摄像头像素，续航 ≠ 电池容量，游戏性能 ≠ 刷新率。
> **漏标比多标严重得多** —— 漏标会让下游生成虚假结论，多标只是保守。

### 4.4 `clarifications` 数组

需要用户澄清的内容。每项带一个**可直接展示的问题**，界面确认页直接渲染：

| 字段 | 含义 |
|---|---|
| `field` | 受影响的字段路径，如 `budget.max`、`min_storage`、`brands.exclude`；降级项为 `preferences.<criterion>` |
| `question` | 给用户看的问题原句 |
| `reason` | 为什么必须问（内部用，可不上界面） |

`field` 取值与 `must_ask` 字段名的对应关系（联调时用）：

| `field` | 等价字段名 |
|---|---|
| `budget.max` | `budget_max` |
| `budget.min` | `budget_min` |
| `min_storage` | `min_storage` |
| `target_screen` | `target_screen` |
| `brands.exclude` | `excluded_brands` |
| `preferences.*` | （降级项，不是待确认的硬约束，联调时跳过） |

> 顺序即优先级：预算最影响候选集，排最前。同一 `field` 只保留第一条。

### 4.5 `status` 与 `meta`

`status` 三态：

| 值 | 含义 | 下游该怎么处理 |
|---|---|---|
| `ok` | 模型返回合法结果 | 正常走确认页 |
| `degraded` | 拿到回复但部分字段非法，已重置 | 走确认页，且必须提示哪些字段被重置 |
| `failed` | 超时 / 空内容 / 非法 JSON | 退回表单模式，`clarifications` 列出所有必须问的字段 |

`meta.error` 在非 `ok` 时给出原因字符串，**不得包含密钥**。

---

## 5. 失败处理

**任何异常都不向上抛。** 界面必须能无条件渲染，所以失败也返回同一结构。

| 情况 | status | 处理 |
|---|---|---|
| 请求超时 | `failed` | 重试 1 次后仍失败 → 全维度 `未提及`，`clarifications` 列出全部待问字段 |
| 返回空内容 | `failed` | 同上 |
| 返回非法 JSON | `failed` | 先剥 ``` 围栏、再取括号平衡的 `{...}`；仍失败则同超时 |
| 维度缺失 | `degraded` | 缺失维度补 `未提及`，加一条 clarification |
| `level` 取值非法 | `degraded` | 该维度降级为 `未提及`，加一条 clarification |
| `evidence` 不在原文里 | `degraded` | **保留 level，但标记 evidence 不可信**（见下） |

> `evidence` 校验：模型有时会编造原话。校验方式是判断该片段是否**逐字出现在输入文本里**。
> 不匹配时**不丢弃 level**（level 是模型的判断，evidence 只是佐证），
> 而是往 `clarifications` 追加一条 `field="preferences.<criterion>"` 的项，
> 让用户在确认页看到「这条判断的依据不在你的原话里」，自行确认。
>
> 归一化把非法值悄悄改成「未提及」是必要的，但**不能静默** ——
> 用户需要知道哪些维度其实没抽到，否则会以为系统读懂了。
> 这类「被兜住的错误」统一走 `clarifications`，并且会让 `status` 变为 `degraded`。

---

## 6. 与现有代码的差距（已全部补齐）

| # | 原差距 | 影响 | 现状 |
|---|---|---|---|
| 1 | **没有 `budget.reference` 字段** | 「2000 左右」「三千多」的数字直接丢失（评测集 CN-01/05/14/18 四条） | ✅ `HardConstraints.budget_reference`；刻意不进 `to_filter_kwargs()` |
| 2 | `parse_needs` 不叫 `extract_needs` | 需要按契约改名或加别名 | ✅ 新增 `needs_extraction.extract_needs()` 作为契约入口，`parse_needs` 保留为内部实现 |
| 3 | `SYSTEM_PROMPT` **没有版本号** | 无法追溯某次结果是哪个 prompt 产出的 | ✅ `providers.llm.PROMPT_VERSION`（现为 `extract-needs-v1.1`，v1.1 增加了多轮补充说明），写进返回结构 |
| 4 | 失败时**抛 `JevUnavailable`** | 界面要写 try/except；契约要求返回 `status=failed` 的完整结构 | ✅ 双保险 `except`，永不抛；8 条单测覆盖 |
| 5 | `clarifications` 目前是裸字段名（`["budget_max"]`） | 确认页没法直接渲染成问题 | ✅ 每项带 `field` / `question` / `reason` |
| 6 | 硬约束**没有 evidence** | 只有 preferences 带原话依据 | ✅ `budget.evidence`；模型侧还要求给出 `min_storage` / `target_screen` / `brands` 的证据 |
| 7 | 没有**实际测试记录**文件 | 契约要求第 5 项交付物 | ✅ `docs/needs-extraction-test-record.md`，由 `tests/record_needs.py` 生成 |
| 8 | 「四五千」被当成硬区间 4000–5000 | 与"含糊预算不设上限"精神冲突 | ✅ 改为 `reference=4500`，min/max 留 null（决定 1 方案 B） |

---

## 7. 三个决定的结论（2026-10-01 已确认）

**决定 1：「四五千」这类两个中文数字连写的，算明确硬约束吗？**

✅ **采用方案 B。** `四五千` → `reference=4500`，`min`/`max` 都留 `null`，并进澄清清单。

> 口径收敛为：**只有三种表达算明确硬约束** ——
> 「N 到 M」→ min/max；「N 以内 / 以下 / 不超过 N」→ max；「N 以上 / 不低于 N」→ min。
> 其余（「2000 左右」「三千多」「四五千」「预算 5000」）一律只填 `reference`。
>
> 注：「预算 5000」这类**裸数字**也归入含糊一类 —— 它没说清是上限还是下限，
> 所以 `reference=5000`、`min`/`max` 留空。

**决定 2：`preferences` 要不要附带数字等级？**

✅ **采用方案 A，只给中文等级。** 返回结构里**不出现任何数字权重**。

> `未提及` 与 `一般` 在 `LEVEL_MAP` 里都是 1，附数字必然被下游误用成权重。
> 等级 → 权重的映射是算法同学的唯一职责，唯一来源 `schemas.LEVEL_MAP`。

**决定 3：失败时抛异常还是返回结构？**

✅ **采用方案 A，返回 `status="failed"` 的完整结构，永不抛。**

> 界面必须能无条件渲染。`extract_needs` 对空输入、`JevUnavailable`、
> 以及任何未预期异常都做了兜底，返回同一套字段（全维度 `未提及`，
> `clarifications` 列出全部待问字段）。

---

## 8. 交付物清单

| # | 交付物 | 状态 | 位置 |
|---|---|---|---|
| 1 | 有版本号的需求抽取 Prompt | ✅ 完成 | `providers/llm.py` → `PROMPT_VERSION`、`SYSTEM_PROMPT` |
| 2 | `extract_needs(user_text)` | ✅ 完成 | `needs_extraction.py` |
| 3 | 固定返回结构 + 字段校验 | ✅ 完成 | `needs_extraction.validate_result()` + `tests/test_needs_extraction.py`（50 例） |
| 4 | 超时/空内容/非法结果处理 | ✅ 完成 | `extract_needs` 三态 + `providers/llm.py` 重试/降级 |
| 5 | 测试需求 + 人工预期 + 实际测试记录 | ✅ 完成 | `tests/needs_testset.json`（20 条）+ `docs/needs-extraction-test-record.md` |

**已知未完成项**（不阻塞接口对接，但必须在真实 API 接入后补）：

- 真实 DeepSeek API **尚未实测**（缺 endpoint / model / key）；
- 因此 `docs/needs-extraction-test-record.md` 里的通过率**不构成能力证据**。

---

## 9. 下游怎么用

```python
from needs_extraction import extract_needs

result = extract_needs(user_text)

# 界面：按 status 决定渲染方式，永远不用 try/except
if result["status"] == "failed":
    render_form_mode(result["clarifications"])   # 退回表单，逐条问
else:
    render_confirm_panel(result)                 # 走确认页

# 算法：只消费 level，自己映射成权重
for item in result["preferences"]:
    level = item["level"]                        # 未提及/不在意/一般/重要/非常重要
    weight = LEVEL_MAP.get(level, 0)             # 映射是算法侧的事
```

**注意**：`budget["reference"]` 只用于界面显示与确认页引用，
**不得**作为筛选条件传给引擎（`HardConstraints.to_filter_kwargs()` 刻意不含它）。

### 界面侧的实际接法（已落地）

`app.py` 已按本契约改造完成，可直接参考：

```python
from bridge import bridge_contract
from needs_extraction import FIELD_PATH_TO_ASK, STATUS_FAILED, extract_needs

result = extract_needs(_join_history(history))     # 多轮：把用户先后说的拼成编号列表
if result["status"] == STATUS_FAILED:
    ...                                            # 退出手动模式，不抛异常
bridged, degraded = bridge_contract(result, known_brands=ALL_BRANDS)

# clarifications 的 question 可直接渲染成追问话术
pending = [c for c in result["clarifications"]
           if c["field"] in FIELD_PATH_TO_ASK and c["field"] not in already_asked]
```

三个要点：

1. **`bridge_contract()` 而不是 `bridge()`**。契约返回 dict，`bridge()` 消费
   `Needs` 对象；`bridge_contract` 做一次适配，好处是 `bridge()` 的全部规则
   （含糊预算不封顶、排除品牌不转白名单、存储向上取整）**只有一份实现**。
2. **`clarifications[].field` 是字段路径**（`budget.max`），界面要的是字段名
   （`budget_max`）。映射表在 `needs_extraction.FIELD_PATH_TO_ASK`，
   是双方共用的唯一来源。
3. **追问要记「问过什么」**。同一字段问过一次就不再问，否则用户会被困在
   追问循环里（用户答「随便」时，抽取器可能把它读成「屏幕随便」而冒出新的待确认项）。

`extract_needs` 超时/重试由 `providers/llm.py` 内部处理，界面不需要写 `try/except`。

### 推荐语生成（另一个模块）

机型由引擎算，不由大模型报。大模型的第二个职责是把结果写成一段话，
见 `providers/recommend.py`：

```python
from providers.recommend import Brief, fallback_text, write_recommendation

advice, error = write_recommendation(brief)   # 永不抛；失败返回 ("", 原因)
if not advice:
    advice = fallback_text(brief)             # 模板兜底
```

事实清单（`Brief`）里只有引擎算出的真实机型与参数，prompt 硬性禁止模型
提到清单外的型号或编造参数。**界面必须始终把引擎的机型列表渲染在推荐语
下方——以列表为准，不以推荐语为准。**
