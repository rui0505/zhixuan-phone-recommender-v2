# 需求抽取模块 · 实际测试记录

> 交付物第 5 项：一批测试需求、人工预期结果和**实际测试记录**。
> 本文件由 `tests/record_needs.py` 生成，可随时重跑复现。

| 项 | 值 |
|---|---|
| 生成时间 | 2026-10-02 11:04:37 +0800 |
| 生成时 HEAD | `wb/llm-api-provider` / `d34323d`（本文件本身通常还未提交，故 HEAD 会略早于文件所在提交） |
| schema_version | `1.0` |
| prompt_version | `extract-needs-v1.1` |
| Python | 3.13.14 (Windows 11) |
| 契约入口单测 | 64/64 通过 |
| 真实 API | ✅ 已接通（DeepSeek `deepseek-flash`），见第 6 节 |

复现命令（在 `phone-demo` 目录）：

```bash
python -m unittest tests.test_needs_extraction -v   # 契约三态与字段校验
python tests/record_needs.py                        # 重新生成本文件
python tests/evaluate_needs.py --impl provider --classifier mock --verbose
python tests/smoke_llm_e2e.py                       # 本地假服务端到端
```

## 1. 结论摘要

| 路径 | 说明 | 通过 | 通过率 |
|---|---|---|---|
| `mock` | `extract_needs` + `MockClassifier`（确定性替身） | 20/20 | 100.0% |
| `http` | `extract_needs` + 真实 `LLMClassifier` → 本地假 OpenAI 服务 | 20/20 | 100.0% |

⚠️ **这两组数字都不能当作能力证据**，原因见第 4 节。

## 2. 三态覆盖

契约要求任何失败都不抛异常，界面必须能无条件渲染。三态各自的验证：

| status | 触发条件 | 验证方式 | 结果 |
|---|---|---|---|
| `ok` | 模型返回合法结果 | 20 条评测 + 基准例子逐字段比对 | 见第 3 节 |
| `degraded` | 取值非法 / 维度缺失 / evidence 与原文不符 | `test_needs_extraction.StatusDegradedTests`（4 例） | 通过 |
| `failed` | 输入为空 / 上游异常 / 未配置模型 | `test_needs_extraction.StatusFailedTests`（8 例） | 通过 |

## 3. 基准例子（你给的原始需求）

输入：`想买 2000 元左右、拍照好点、轻一点的手机。`

两条红线各自的实测结果：

| 红线 | 人工预期 | 实际输出 | 结论 |
|---|---|---|---|
| 记下数字但不封顶 | `reference=2000`，`min/max` 为空 | `reference=2000`，`min=None`，`max=None` | ✅ |
| 拍照需求保留且不换算 | `unsupported_needs=[影像]`，无影像维度 | `unsupported_needs=['影像']`，维度仍为 ['price', 'weight', 'storage', 'refresh', 'screen'] | ✅ |
| 必须回问用户 | 至少含 `budget.max` | `clarifications=['budget.max', 'min_storage']` | ✅ |

完整返回结构：

```json
{
  "schema_version": "1.0",
  "prompt_version": "extract-needs-v1.1",
  "status": "ok",
  "input_text": "想买 2000 元左右、拍照好点、轻一点的手机。",
  "budget": {
    "reference": 2000.0,
    "min": null,
    "max": null,
    "evidence": "2000"
  },
  "min_storage": null,
  "target_screen": null,
  "brands": {
    "include": [],
    "exclude": []
  },
  "preferences": [
    {
      "criterion": "price",
      "level": "未提及",
      "evidence": ""
    },
    {
      "criterion": "weight",
      "level": "一般",
      "evidence": "轻一点"
    },
    {
      "criterion": "storage",
      "level": "未提及",
      "evidence": ""
    },
    {
      "criterion": "refresh",
      "level": "未提及",
      "evidence": ""
    },
    {
      "criterion": "screen",
      "level": "未提及",
      "evidence": ""
    }
  ],
  "unsupported_needs": [
    {
      "need": "影像",
      "evidence": "拍照"
    }
  ],
  "clarifications": [
    {
      "field": "budget.max",
      "question": "你说的是「2000 左右」，没有明确上限。最多能接受到多少？",
      "reason": "含糊预算不自动封顶，必须由用户确认"
    },
    {
      "field": "min_storage",
      "question": "你没有说明最低存储容量，需要多大？",
      "reason": "存储门槛无法可靠提取"
    }
  ],
  "meta": {
    "provider": "mock",
    "model": "mock-v1",
    "elapsed_ms": 0,
    "error": null
  }
}
```

> ⚠️ 注意 `budget.evidence` 这里只是 `"2000"`，而契约示例写的是 `"2000 元左右"`。原因是确定性替身 `MockClassifier` 的证据粒度只到数字本身；真实模型的 prompt 明确要求逐字复制原话片段，接入后应能拿到完整短语。**这一项必须等真实 API 接入后重新核对。**

## 4. 逐例记录（20 条）

字段口径：

- 硬约束按数值比对，`null` 与缺失视为同一件事；
- `preferences.*` 要求精确相等，不做部分给分；
- `unsupported_needs` 漏标与多标都算不一致（漏标更严重）；
- `must_ask` 只检查「该问的问了没有」，**多问属于保守，不算错**。

判定列的三档：`✅` 完全一致；`✅ 多问` / `✅ 多标` 方向正确但更保守，不计为错误；`❌` 不一致（`❌ 漏标` 最严重，会生成虚假结论）。

### CN-01 · 预算含糊不得自动设上限

**输入**：`预算 3000 左右，主要看价格，越便宜越好`

| 字段 | 人工预期 | 实际输出 | 判定 |
|---|---|---|---|
| `budget.min` | `null` | `null` | ✅ |
| `budget.max` | `null` | `null` | ✅ |
| `budget.reference` | `3000` | `3000` | ✅ |
| `min_storage` | `null` | `null` | ✅ |
| `brands.include` | `[]` | `[]` | ✅ |
| `target_screen` | `null` | `null` | ✅ |
| `preferences.price` | `重要` | `重要` | ✅ |
| `preferences.weight` | `未提及` | `未提及` | ✅ |
| `preferences.storage` | `未提及` | `未提及` | ✅ |
| `preferences.refresh` | `未提及` | `未提及` | ✅ |
| `preferences.screen` | `未提及` | `未提及` | ✅ |
| `unsupported_needs` | `[]` | `[]` | ✅ |
| `must_ask` | `[budget_max]` | `[budget_max, min_storage]` | ✅ 多问（偏保守） |

**结果**：PASS

> 判据：“3000 左右”不能自动理解为“不可超过 3000”，必须回问确认预算上限；同时“越便宜越好”是独立的价格偏好，与预算上限分开判断。强度按统一口径处理：仅含“越…越好”而无最高级表述，记“重要”（映射 2）。

### CN-02 · 模糊程度词不得擅自定量

**输入**：`预算 3000 以内，别太重`

| 字段 | 人工预期 | 实际输出 | 判定 |
|---|---|---|---|
| `budget.min` | `null` | `null` | ✅ |
| `budget.max` | `3000` | `3000` | ✅ |
| `budget.reference` | `3000` | `3000` | ✅ |
| `min_storage` | `null` | `null` | ✅ |
| `brands.include` | `[]` | `[]` | ✅ |
| `target_screen` | `null` | `null` | ✅ |
| `preferences.price` | `未提及` | `未提及` | ✅ |
| `preferences.weight` | `一般` | `一般` | ✅ |
| `preferences.storage` | `未提及` | `未提及` | ✅ |
| `preferences.refresh` | `未提及` | `未提及` | ✅ |
| `preferences.screen` | `未提及` | `未提及` | ✅ |
| `unsupported_needs` | `[]` | `[]` | ✅ |
| `must_ask` | `[]` | `[]` | ✅ |

**结果**：PASS

> 判据：“3000 以内”是明确的上限，可提取为 budget_max=3000。但“别太重”只是方向性表达，不能擅自设为 180 克；重量维度记为“一般”表示方向存在但强度低，不生成具体阈值。

### CN-03 · 256GB 起步与存储越大越好分开判断

**输入**：`想买个 iPhone，256GB 起步，预算 8000 以内`

| 字段 | 人工预期 | 实际输出 | 判定 |
|---|---|---|---|
| `budget.min` | `null` | `null` | ✅ |
| `budget.max` | `8000` | `8000` | ✅ |
| `budget.reference` | `8000` | `8000` | ✅ |
| `min_storage` | `256` | `256` | ✅ |
| `brands.include` | `[苹果]` | `[苹果]` | ✅ |
| `target_screen` | `null` | `null` | ✅ |
| `preferences.price` | `未提及` | `未提及` | ✅ |
| `preferences.weight` | `未提及` | `未提及` | ✅ |
| `preferences.storage` | `未提及` | `未提及` | ✅ |
| `preferences.refresh` | `未提及` | `未提及` | ✅ |
| `preferences.screen` | `未提及` | `未提及` | ✅ |
| `unsupported_needs` | `[]` | `[]` | ✅ |
| `must_ask` | `[]` | `[]` | ✅ |

**结果**：PASS

> 判据：“256GB 起步”是硬约束门槛，不是“存储越大越好”的偏好。storage 偏好必须留在“未提及”，否则会把门槛重复计分两次。品牌限定转成品牌名“苹果”。

### CN-04 · 同时命中门槛与偏好时必须分别抽取

**输入**：`要 iPhone，存储越大越好，最少 256G，预算 6000 到 9000`

| 字段 | 人工预期 | 实际输出 | 判定 |
|---|---|---|---|
| `budget.min` | `6000` | `6000` | ✅ |
| `budget.max` | `9000` | `9000` | ✅ |
| `budget.reference` | `null` | `null` | ✅ |
| `min_storage` | `256` | `256` | ✅ |
| `brands.include` | `[苹果]` | `[苹果]` | ✅ |
| `target_screen` | `null` | `null` | ✅ |
| `preferences.price` | `未提及` | `未提及` | ✅ |
| `preferences.weight` | `未提及` | `未提及` | ✅ |
| `preferences.storage` | `重要` | `重要` | ✅ |
| `preferences.refresh` | `未提及` | `未提及` | ✅ |
| `preferences.screen` | `未提及` | `未提及` | ✅ |
| `unsupported_needs` | `[]` | `[]` | ✅ |
| `must_ask` | `[]` | `[]` | ✅ |

**结果**：PASS

> 判据：与 CN-03 对照：本句同时出现“最少 256G”（门槛）和“存储越大越好”（偏好），两者都要抽出来，storage 记“重要”。这题用来防止把门槛误吞或把偏好误丢。

### CN-05 · 无数据支撑的需求不生成优胜结论

**输入**：`拍照要好的，其他随便，预算四千左右`

| 字段 | 人工预期 | 实际输出 | 判定 |
|---|---|---|---|
| `budget.min` | `null` | `null` | ✅ |
| `budget.max` | `null` | `null` | ✅ |
| `budget.reference` | `4000` | `4000` | ✅ |
| `min_storage` | `null` | `null` | ✅ |
| `brands.include` | `[]` | `[]` | ✅ |
| `target_screen` | `null` | `null` | ✅ |
| `preferences.price` | `未提及` | `未提及` | ✅ |
| `preferences.weight` | `未提及` | `未提及` | ✅ |
| `preferences.storage` | `未提及` | `未提及` | ✅ |
| `preferences.refresh` | `未提及` | `未提及` | ✅ |
| `preferences.screen` | `未提及` | `未提及` | ✅ |
| `unsupported_needs` | `[影像]` | `[影像]` | ✅ |
| `must_ask` | `[budget_max]` | `[budget_max, min_storage, target_screen]` | ✅ 多问（偏保守） |

**结果**：PASS

> 判据：现库没有可比的拍照实测评测，影像只能记为未覆盖需求。“其他随便”不得被理解成五项全零（那等同于拒绝评分），应保持未提及走中性先验。

### CN-06 · 未覆盖需求不得用替代指标充数

**输入**：`续航越久越好，屏幕别太大，6.1 英寸差不多`

| 字段 | 人工预期 | 实际输出 | 判定 |
|---|---|---|---|
| `budget.min` | `null` | `null` | ✅ |
| `budget.max` | `null` | `null` | ✅ |
| `budget.reference` | `null` | `null` | ✅ |
| `min_storage` | `null` | `null` | ✅ |
| `brands.include` | `[]` | `[]` | ✅ |
| `target_screen` | `6.1` | `6.1` | ✅ |
| `preferences.price` | `未提及` | `未提及` | ✅ |
| `preferences.weight` | `未提及` | `未提及` | ✅ |
| `preferences.storage` | `未提及` | `未提及` | ✅ |
| `preferences.refresh` | `未提及` | `未提及` | ✅ |
| `preferences.screen` | `重要` | `重要` | ✅ |
| `unsupported_needs` | `[续航]` | `[续航]` | ✅ |
| `must_ask` | `[]` | `[budget_max, min_storage]` | ✅ 多问（偏保守） |

**结果**：PASS

> 判据：续航无数据支撑，记为未覆盖，不得用电池容量替代。“6.1 英寸差不多”给出了具体尺寸，抽为 target_screen=6.1，同时屏幕接近偏好记重要。“别太大”是方向，不额外生成下界。

### CN-07 · 刷新率不得等同于真实游戏性能

**输入**：`打游戏不卡就行，刷新率要高，一加或者红米`

| 字段 | 人工预期 | 实际输出 | 判定 |
|---|---|---|---|
| `budget.min` | `null` | `null` | ✅ |
| `budget.max` | `null` | `null` | ✅ |
| `budget.reference` | `null` | `null` | ✅ |
| `min_storage` | `null` | `null` | ✅ |
| `brands.include` | `[Redmi, 一加]` | `[Redmi, 一加]` | ✅ |
| `target_screen` | `null` | `null` | ✅ |
| `preferences.price` | `未提及` | `未提及` | ✅ |
| `preferences.weight` | `未提及` | `未提及` | ✅ |
| `preferences.storage` | `未提及` | `未提及` | ✅ |
| `preferences.refresh` | `非常重要` | `非常重要` | ✅ |
| `preferences.screen` | `未提及` | `未提及` | ✅ |
| `unsupported_needs` | `[真实游戏性能]` | `[真实游戏性能]` | ✅ |
| `must_ask` | `[]` | `[budget_max, min_storage]` | ✅ 多问（偏保守） |

**结果**：PASS

> 判据：“打游戏不卡”是无数据支撑的真实游戏性能需求，记为未覆盖；“刷新率要高”是唯一有数据支撑的维度，记非常重要。不能因为两个诉求相邻就把刷新率当成游戏性能的代理。

### CN-08 · 无信息时保留中性先验而非全零

**输入**：`随便看看，没什么特别要求`

| 字段 | 人工预期 | 实际输出 | 判定 |
|---|---|---|---|
| `budget.min` | `null` | `null` | ✅ |
| `budget.max` | `null` | `null` | ✅ |
| `budget.reference` | `null` | `null` | ✅ |
| `min_storage` | `null` | `null` | ✅ |
| `brands.include` | `[]` | `[]` | ✅ |
| `target_screen` | `null` | `null` | ✅ |
| `preferences.price` | `未提及` | `未提及` | ✅ |
| `preferences.weight` | `未提及` | `未提及` | ✅ |
| `preferences.storage` | `未提及` | `未提及` | ✅ |
| `preferences.refresh` | `未提及` | `未提及` | ✅ |
| `preferences.screen` | `未提及` | `未提及` | ✅ |
| `unsupported_needs` | `[]` | `[]` | ✅ |
| `must_ask` | `[]` | `[budget_max, min_storage, target_screen]` | ✅ 多问（偏保守） |

**结果**：PASS

> 判据：无任何偏好信号时五项都记未提及，映射为等权中性先验。“没要求”不等于“五项权重为零”——后者会让系统拒绝计算，也违背中性先验的设计。

### CN-09 · “不在意”与“未提及”必须可区分

**输入**：`轻一点好，越轻越好，但价格不太在意`

| 字段 | 人工预期 | 实际输出 | 判定 |
|---|---|---|---|
| `budget.min` | `null` | `null` | ✅ |
| `budget.max` | `null` | `null` | ✅ |
| `budget.reference` | `null` | `null` | ✅ |
| `min_storage` | `null` | `null` | ✅ |
| `brands.include` | `[]` | `[]` | ✅ |
| `target_screen` | `null` | `null` | ✅ |
| `preferences.price` | `不在意` | `不在意` | ✅ |
| `preferences.weight` | `重要` | `重要` | ✅ |
| `preferences.storage` | `未提及` | `未提及` | ✅ |
| `preferences.refresh` | `未提及` | `未提及` | ✅ |
| `preferences.screen` | `未提及` | `未提及` | ✅ |
| `unsupported_needs` | `[]` | `[]` | ✅ |
| `must_ask` | `[]` | `[budget_max, min_storage]` | ✅ 多问（偏保守） |

**结果**：PASS

> 判据：“价格不太在意”是用户明确表态，记“不在意”（映射 0）；与“未提及”（映射 1 的中性先验）语义不同，两者混淆会让中性先验被错误归零。“越轻越好”按统一口径记“重要”（映射 2），与 CN-13 的“最轻”（含最高级，记非常重要）形成强度对照。

### CN-10 · 屏幕维度在现有引擎中按与目标尺寸的绝对偏差处理

**输入**：`预算 5000，屏幕要 6.7 英寸，越大越好`

| 字段 | 人工预期 | 实际输出 | 判定 |
|---|---|---|---|
| `budget.min` | `null` | `null` | ✅ |
| `budget.max` | `null` | `null` | ✅ |
| `budget.reference` | `5000` | `5000` | ✅ |
| `min_storage` | `null` | `null` | ✅ |
| `brands.include` | `[]` | `[]` | ✅ |
| `target_screen` | `6.7` | `6.7` | ✅ |
| `preferences.price` | `未提及` | `未提及` | ✅ |
| `preferences.weight` | `未提及` | `未提及` | ✅ |
| `preferences.storage` | `未提及` | `未提及` | ✅ |
| `preferences.refresh` | `未提及` | `未提及` | ✅ |
| `preferences.screen` | `重要` | `重要` | ✅ |
| `unsupported_needs` | `[]` | `[]` | ✅ |
| `must_ask` | `[]` | `[budget_max, min_storage]` | ✅ 多问（偏保守） |

**结果**：PASS

> 判据：现有 engine 把 screen 转为 |screen - target_screen| 后按成本型计算，因此“越大越好”与“给定目标尺寸”在此引擎中语义冲突。取用户给出的明确尺寸 6.7 作为 target_screen，偏好强度记重要，并在确认页提示现有算法是“接近目标”而非“越大越好”。

### CN-11 · 排除式品牌限定不得表达为白名单

**输入**：`不要华为，其他牌子都行，存储 512G 以上，价格四五千`

| 字段 | 人工预期 | 实际输出 | 判定 |
|---|---|---|---|
| `budget.min` | `null` | `null` | ✅ |
| `budget.max` | `null` | `null` | ✅ |
| `budget.reference` | `4500` | `4500` | ✅ |
| `min_storage` | `512` | `512` | ✅ |
| `brands.include` | `[]` | `[]` | ✅ |
| `target_screen` | `null` | `null` | ✅ |
| `preferences.price` | `未提及` | `未提及` | ✅ |
| `preferences.weight` | `未提及` | `未提及` | ✅ |
| `preferences.storage` | `未提及` | `未提及` | ✅ |
| `preferences.refresh` | `未提及` | `未提及` | ✅ |
| `preferences.screen` | `未提及` | `未提及` | ✅ |
| `unsupported_needs` | `[]` | `[]` | ✅ |
| `must_ask` | `[excluded_brands]` | `[budget_max, excluded_brands]` | ✅ 多问（偏保守） |

**结果**：PASS

> 判据：“不要华为”是排除条件，不是白名单。现有筛选只支持 brands 白名单，因此 must_ask 要求把「品牌排除集合」作为独立待确认字段提交给用户确认：要么转为全品牌列出后由程序减去华为，要么由用户改为明确白名单。不得静默忽略这条否定约束，也不得把华为填进白名单。“价格四五千”是明确区间，抽为 4000–5000；“存储 512G 以上”是门槛，抽为 min_storage=512。

### CN-12 · 不存在的字段不得提供虚假过滤

**输入**：`屏幕要好一点的，分辨率高`

| 字段 | 人工预期 | 实际输出 | 判定 |
|---|---|---|---|
| `budget.min` | `null` | `null` | ✅ |
| `budget.max` | `null` | `null` | ✅ |
| `budget.reference` | `null` | `null` | ✅ |
| `min_storage` | `null` | `null` | ✅ |
| `brands.include` | `[]` | `[]` | ✅ |
| `target_screen` | `null` | `null` | ✅ |
| `preferences.price` | `未提及` | `未提及` | ✅ |
| `preferences.weight` | `未提及` | `未提及` | ✅ |
| `preferences.storage` | `未提及` | `未提及` | ✅ |
| `preferences.refresh` | `未提及` | `未提及` | ✅ |
| `preferences.screen` | `未提及` | `未提及` | ✅ |
| `unsupported_needs` | `[分辨率]` | `[分辨率]` | ✅ |
| `must_ask` | `[target_screen]` | `[budget_max, min_storage, target_screen]` | ✅ 多问（偏保守） |

**结果**：PASS

> 判据：“分辨率高”对应数据表的分辨率列，但该列不参与现有五项评分，记为未覆盖需求。“屏幕要好”无法映射到尺寸，必须回问目标尺寸，而不能猜测。

### CN-13 · 单一维度极端偏好应产生单维主导权重

**输入**：`给我推荐个最轻的，价格无所谓`

| 字段 | 人工预期 | 实际输出 | 判定 |
|---|---|---|---|
| `budget.min` | `null` | `null` | ✅ |
| `budget.max` | `null` | `null` | ✅ |
| `budget.reference` | `null` | `null` | ✅ |
| `min_storage` | `null` | `null` | ✅ |
| `brands.include` | `[]` | `[]` | ✅ |
| `target_screen` | `null` | `null` | ✅ |
| `preferences.price` | `不在意` | `不在意` | ✅ |
| `preferences.weight` | `非常重要` | `非常重要` | ✅ |
| `preferences.storage` | `未提及` | `未提及` | ✅ |
| `preferences.refresh` | `未提及` | `未提及` | ✅ |
| `preferences.screen` | `未提及` | `未提及` | ✅ |
| `unsupported_needs` | `[]` | `[]` | ✅ |
| `must_ask` | `[]` | `[budget_max, min_storage]` | ✅ 多问（偏保守） |

**结果**：PASS

> 判据：与 CN-09 类似但强度更高：“最轻”是极端表达，记非常重要；其余保持未提及。此例用于检查初始权重是否确实由 weight 主导，而不是被四个中性先验稀释得过于均匀。

### CN-14 · 同句内混合含糊预算、门槛、偏好与中等强度

**输入**：`预算三千多，存储越大越好，尽量 512G，屏幕刷新率一般就行`

| 字段 | 人工预期 | 实际输出 | 判定 |
|---|---|---|---|
| `budget.min` | `null` | `null` | ✅ |
| `budget.max` | `null` | `null` | ✅ |
| `budget.reference` | `3000` | `3000` | ✅ |
| `min_storage` | `512` | `512` | ✅ |
| `brands.include` | `[]` | `[]` | ✅ |
| `target_screen` | `null` | `null` | ✅ |
| `preferences.price` | `未提及` | `未提及` | ✅ |
| `preferences.weight` | `未提及` | `未提及` | ✅ |
| `preferences.storage` | `重要` | `重要` | ✅ |
| `preferences.refresh` | `一般` | `一般` | ✅ |
| `preferences.screen` | `未提及` | `未提及` | ✅ |
| `unsupported_needs` | `[]` | `[]` | ✅ |
| `must_ask` | `[budget_max]` | `[budget_max]` | ✅ |

**结果**：PASS

> 判据：“三千多”含糊，budget_max 待确认；“尽量 512G”是门槛，min_storage=512；“存储越大越好”是偏好，按与 CN-04 一致的口径记“重要”（“越大越好”是明确的强度表达但不含“最重要/必须”这类最高级）；“刷新率一般就行”是明确的低强度表态，记一般（映射 1）而非未提及。此例中的存储同时有门槛与偏好，与 CN-03/CN-04 形成对照，用来确认两者不会互相吞并。

### CN-15 · 可量化的上界与不可量化的手感分开处理

**输入**：`想要个小屏旗舰，单手能拿住，别超过 6.2 英寸`

| 字段 | 人工预期 | 实际输出 | 判定 |
|---|---|---|---|
| `budget.min` | `null` | `null` | ✅ |
| `budget.max` | `null` | `null` | ✅ |
| `budget.reference` | `null` | `null` | ✅ |
| `min_storage` | `null` | `null` | ✅ |
| `brands.include` | `[]` | `[]` | ✅ |
| `target_screen` | `6.2` | `6.2` | ✅ |
| `preferences.price` | `未提及` | `未提及` | ✅ |
| `preferences.weight` | `未提及` | `未提及` | ✅ |
| `preferences.storage` | `未提及` | `未提及` | ✅ |
| `preferences.refresh` | `未提及` | `未提及` | ✅ |
| `preferences.screen` | `非常重要` | `非常重要` | ✅ |
| `unsupported_needs` | `[机身尺寸手感]` | `[机身尺寸手感]` | ✅ |
| `must_ask` | `[]` | `[budget_max, min_storage]` | ✅ 多问（偏保守） |

**结果**：PASS

> 判据：“别超过 6.2 英寸”给出明确上界，抽为 target_screen=6.2 并因强调“小屏旗舰”记非常重要。“单手能拿住”依赖宽度和重量等无数据指标，记为未覆盖，不得用屏幕尺寸替代。

### CN-16 · 显式点名“其他都一般”应作用于全部剩余维度

**输入**：`价格最重要，其他都一般，预算 4000 以内`

| 字段 | 人工预期 | 实际输出 | 判定 |
|---|---|---|---|
| `budget.min` | `null` | `null` | ✅ |
| `budget.max` | `4000` | `4000` | ✅ |
| `budget.reference` | `4000` | `4000` | ✅ |
| `min_storage` | `null` | `null` | ✅ |
| `brands.include` | `[]` | `[]` | ✅ |
| `target_screen` | `null` | `null` | ✅ |
| `preferences.price` | `非常重要` | `非常重要` | ✅ |
| `preferences.weight` | `一般` | `一般` | ✅ |
| `preferences.storage` | `一般` | `一般` | ✅ |
| `preferences.refresh` | `一般` | `一般` | ✅ |
| `preferences.screen` | `一般` | `一般` | ✅ |
| `unsupported_needs` | `[]` | `[]` | ✅ |
| `must_ask` | `[]` | `[]` | ✅ |

**结果**：PASS

> 判据：与 CN-01 的关键差别：本句预算上限明确可提取，且用户显式规定了剩余维度的强度。“其他都一般”是整体表态，应对四项未点名的可评分维度统一记“一般”。

### CN-17 · 库里不存在的品牌不得被静默忽略

**输入**：`iQOO 或者 realme，性价比高一点，2000 到 3000`

| 字段 | 人工预期 | 实际输出 | 判定 |
|---|---|---|---|
| `budget.min` | `2000` | `2000` | ✅ |
| `budget.max` | `3000` | `3000` | ✅ |
| `budget.reference` | `null` | `null` | ✅ |
| `min_storage` | `null` | `null` | ✅ |
| `brands.include` | `[iQOO, realme]` | `[iQOO, realme]` | ✅ |
| `target_screen` | `null` | `null` | ✅ |
| `preferences.price` | `重要` | `重要` | ✅ |
| `preferences.weight` | `未提及` | `未提及` | ✅ |
| `preferences.storage` | `未提及` | `未提及` | ✅ |
| `preferences.refresh` | `未提及` | `未提及` | ✅ |
| `preferences.screen` | `未提及` | `未提及` | ✅ |
| `unsupported_needs` | `[处理器性能]` | `[处理器性能]` | ✅ |
| `must_ask` | `[]` | `[]` | ✅ |

**结果**：PASS

> 判据：数据表已有品牌前缀里没有 iQOO 和 realme，二者都会落到“其他”。品牌字段仍应按用户原话记录 iQOO/realme，并在确认页提示当前数据可能无匹配候选，而不是静默换成空品牌列表。“性价比高”映射为价格偏好（记重要）；性价比还隐含处理器性能，记为未覆盖。

### CN-18 · 用户主动给出的具体阈值应记录但引擎暂不支持

**输入**：`重量要轻，最好 180 克以内；价格 3500 左右；存储 256 起步`

| 字段 | 人工预期 | 实际输出 | 判定 |
|---|---|---|---|
| `budget.min` | `null` | `null` | ✅ |
| `budget.max` | `null` | `null` | ✅ |
| `budget.reference` | `3500` | `3500` | ✅ |
| `min_storage` | `256` | `256` | ✅ |
| `brands.include` | `[]` | `[]` | ✅ |
| `target_screen` | `null` | `null` | ✅ |
| `preferences.price` | `未提及` | `未提及` | ✅ |
| `preferences.weight` | `重要` | `重要` | ✅ |
| `preferences.storage` | `未提及` | `未提及` | ✅ |
| `preferences.refresh` | `未提及` | `未提及` | ✅ |
| `preferences.screen` | `未提及` | `未提及` | ✅ |
| `unsupported_needs` | `[]` | `[]` | ✅ |
| `must_ask` | `[budget_max]` | `[budget_max]` | ✅ |

**结果**：PASS

> 判据：本句是 CN-02 的对照：用户这次主动给出了 180 克作为参考，但现有 engine 的重量维度是连续成本型，没有重量上界筛选。应记录用户给出的 180 克到待确认字段并说明该阈值不参与筛选，而不是悄悄丢掉或偷偷加进筛选。“价格 3500 左右”是模糊预算，必须回问预算上限。“存储 256 起步”是门槛，抽为 min_storage=256，storage 偏好保持未提及。

### CN-19 · 全权委托时不得替用户编造约束

**输入**：`什么都行，你帮我决定吧`

| 字段 | 人工预期 | 实际输出 | 判定 |
|---|---|---|---|
| `budget.min` | `null` | `null` | ✅ |
| `budget.max` | `null` | `null` | ✅ |
| `budget.reference` | `null` | `null` | ✅ |
| `min_storage` | `null` | `null` | ✅ |
| `brands.include` | `[]` | `[]` | ✅ |
| `target_screen` | `null` | `null` | ✅ |
| `preferences.price` | `未提及` | `未提及` | ✅ |
| `preferences.weight` | `未提及` | `未提及` | ✅ |
| `preferences.storage` | `未提及` | `未提及` | ✅ |
| `preferences.refresh` | `未提及` | `未提及` | ✅ |
| `preferences.screen` | `未提及` | `未提及` | ✅ |
| `unsupported_needs` | `[]` | `[]` | ✅ |
| `must_ask` | `[budget_max, min_storage, target_screen]` | `[budget_max, min_storage, target_screen]` | ✅ |

**结果**：PASS

> 判据：用户放弃决策不等于允许系统虚构需求。五项偏好保持未提及走中性先验，关键硬约束（预算、最低存储、目标尺寸）必须回问。这题用来检测理解层是否出现“幻觉式补全”。

### CN-20 · 五项同时出现时的完整抽取

**输入**：`预算 6000 以内，要轻（越轻越好），存储 512G 以上，刷新率不重要，屏幕 6.4 英寸左右`

| 字段 | 人工预期 | 实际输出 | 判定 |
|---|---|---|---|
| `budget.min` | `null` | `null` | ✅ |
| `budget.max` | `6000` | `6000` | ✅ |
| `budget.reference` | `6000` | `6000` | ✅ |
| `min_storage` | `512` | `512` | ✅ |
| `brands.include` | `[]` | `[]` | ✅ |
| `target_screen` | `6.4` | `6.4` | ✅ |
| `preferences.price` | `未提及` | `未提及` | ✅ |
| `preferences.weight` | `重要` | `重要` | ✅ |
| `preferences.storage` | `未提及` | `未提及` | ✅ |
| `preferences.refresh` | `不在意` | `不在意` | ✅ |
| `preferences.screen` | `重要` | `重要` | ✅ |
| `unsupported_needs` | `[]` | `[]` | ✅ |
| `must_ask` | `[]` | `[]` | ✅ |

**结果**：PASS

> 判据：五项可评分维度中三项有信号、两项无信号：硬约束抽 budget_max=6000 / min_storage=512 / target_screen=6.4；weight 按统一口径因“越轻越好”记重要（映射 2），refresh 因“不重要”记不在意（映射 0，需用户确认后锁定为零），screen 记重要，price 与 storage 偏好保持未提及。

## 5. 评估泄漏声明（必读）

`MockClassifier` 是照本评测集**逐条调出来的确定性替身**，它在这 20 条上的表现**不构成任何语义理解能力证据**。它存在的意义只有一个：在真实模型接入前，把接口、结构、失败处理、评测口径先打通并锁死。

同理，`RuleBaseline`（`--impl baseline`）也是参照同一份标准答案写的对照实现。
两者分数都只能用作**管道自检**，不能作为交付结果上报。

本地假 OpenAI 服务（`http` 路径）内部同样委托 `MockClassifier`，
因此它验证的是**线格式**（真实 socket → 真实 JSON 编解码 → 归一化 → 契约结构），
不是模型准确率。若 `http` 与 `mock` 分数不一致，说明是管道问题，不是模型问题。

## 6. 真实 API 实测（2026-10-01）

**已经接上真实服务。** 服务商 DeepSeek，模型 `deepseek-flash`，
端点 `https://api.deepseek.com/v1`，走 OpenAI 兼容的 `/chat/completions`。

复现命令：

```bash
python tools/check_llm.py          # 真调一次线上服务，会花钱
python tools/check_llm.py --json   # 额外打印完整契约 JSON
```

### 6.1 实测结果

| 项 | 状态 | 实测值 |
|---|---|---|
| 真实 API 连通 | ✅ | 抽取链路 1738 ms；返回 `status=ok`，`validate_result` 返回 **0 个问题** |
| evidence 粒度 | ✅ **真实链路不复现替身的问题** | 模型给出 `预算三千左右`（完整原话片段），而 `MockClassifier` 只回 `"3000"`。逐条核对 5 个维度，**无一条证据是编造的** |
| 含糊预算不封顶 | ✅ | 「预算三千左右」→ `reference=3000, max=None`，未被擅自封顶 |
| 真实链路延迟 | ✅ | 抽取 1738 ms；推荐语 1829 ms。均远低于 60 s 超时阈值 |
| 推荐语不编造型号 | ✅ | 生成 434 字，提到的 5 款机型**全部**在引擎清单内，未出现清单外机型 |
| JSON 合规率 | ⚠️ 样本量 1 | 单条基准例子一次通过，无需重试。**1/1 不等于 100%**，20 条评测集尚未在真实模型上跑过 |

### 6.2 实测中发现并修掉的三个缺陷

**① 模型不填 `asks`，导致界面不追问。**

对「预算三千左右，想要轻一点的，主要看价格，256G 起步就行」，
真实模型返回 `asks: []`——它把数字记进了 `budget_reference`，
却没把 `budget_max` 列进 `asks`。而 `clarifications` 原本完全依赖
`asks`，于是**界面不会问「最多能接受到多少」**，契约 §4.1 的红线
「含糊预算不自动封顶，由确认页问用户」直接落空。

修法：判定改成从**抽取出的字段**推导，不看模型填了什么——
`budget.max is None` 就必须问，与 `bridge._snap_budget` 的 pending
逻辑同源。修完真实链路已能正确问出「你说的是「3000 左右」，
没有明确上限。最多能接受到多少？」

**② DeepSeek V4 默认开启 thinking 模式，纯亏。**

同一输入实测（同一 prompt）：

| | thinking 开（DeepSeek 默认） | thinking 关 |
|---|---|---|
| 墙钟耗时 | 4166 ms | **890 ms** |
| completion tokens | 836 | **195** |
| 其中推理 tokens | 640 | 0 |
| 抽取结果 | — | **逐字一致** |

更要紧的是：**thinking 模式下 `temperature` 被静默忽略**
（官方文档明说「不会报错，但也不会生效」），而抽取链路依赖
`temperature=0` 保证可复现。已在两条链路默认关闭，
`LLM_THINKING=1` 可打开。

**附**：`deepseek-chat` 是遗留模型名。请求仍会被受理，但实际由
DeepSeek-V4.1-Flash 提供服务并按 Flash 计价，返回体里的 `model`
会被换成 `deepseek-flash`。当前可用名是 `deepseek-flash` 与
`deepseek-v4-pro`。

**③ 多轮对话里，用户后补的明确上限被红线检查误伤。**

（2026-10-02 接入 Web 服务时发现，影响 Streamlit 与 Web 两条链路。）

契约红线「含糊预算不得自动封顶」的检查原先拿**整段拼接历史**去
找含糊短语。第二轮用户补了「最多 4000」之后，第一轮那句
「预算三千左右」仍然留在 `input_text` 里，于是 `max=4000` 被判定为
「原文含含糊预算却设了硬边界」——`to_blue_needs` 抛 `ValueError`，
`handle_turn` 直接回「需求输出未通过接口检查」。**用户老老实实答完
追问，反而出不了机型。**

修法：`_vague_boundary_conflict` 只在两件事同时成立时才判违规——
① 含糊短语出现在**最后一句**用户输入里（多轮历史各占一行）；
② **含糊短语里那个数字本身**被写进了 `min`/`max`。这样
「预算 3000 左右」被写成 `max=3000` 仍会被拦住，而
「预算 3000 左右，最多 4000」不会。**宁可漏报，不可误报**——
误报会让界面拒绝一条合法需求。回归用例见
`tests/test_needs_extraction.py` 的 4 条多轮用例与
`tests/test_pipeline.py::ClarifyTests`。

### 6.3 仍未验证的部分

| 项 | 状态 | 说明 |
|---|---|---|
| 20 条评测集在真实模型上的通过率 | ❌ 未实测 | 本记录的 20 条走的是 `MockClassifier` 与本地假服务，**不是真实模型**。真实模型的中文边界判断（「四五千」取中值、拍照归入未覆盖需求）尚未逐条核对 |
| 真实模型 JSON 合规率 | ⚠️ 1/1 | 见 6.1。样本太少，不足以断言稳定 |
| 超时 / 空内容 / 非法 JSON 的真实复现 | ⚠️ 部分 | 逻辑已实现并有单测覆盖，但未在真实网络抖动下验证 |
| 并发与限流 | ❌ 未实测 | 未测并发上限与 429 行为 |

### 6.4 接入方式

把 `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` 写进 `.env`
（模板见 `.env.example`），或用环境变量传入。

优先级：**真实环境变量 > `.env` > `.streamlit/secrets.toml`**，
三者都是「只填不覆盖」。密钥不落盘、不入代码、不打印。

> 修正一条此前的记录：`.env.example` 曾写着「复制为 `.env` 后填入真实值」，
> 但在本轮之前**没有任何代码读 `.env`**——照做是无效的。现已由
> `providers/env.py` 的 `load_dotenv()` 补上，`app.py` 启动时调用。

### 6.5 端到端 UI

`tests/test_app_flow.py` 用 Streamlit `AppTest` 真实点击过控件；
另用 Playwright 驱动本机 Edge 截取五个状态（首屏 / 追问 / 出机型 /
排除品牌追问 / 结果区）并做 DOM 断言。

注意：`test_app_flow.py` 的用例继承 `IsolatedEnvTest`，会把
`LLM_ENV_FILE` 指向不存在的路径以屏蔽开发机的 `.env`。
否则「有 `.env` 的人跑测试会红、没有的人会绿」。
