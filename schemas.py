"""结构定义与有效范围校验。

对应实施工作文档第 3 章「数据结构与记录规范」与第 9 章接口表。

本模块只做校验，不做业务计算：不排序、不训练、不调用外部服务。
任何进入排序链路的用户输入都必须先经过这里，避免 NaN、无穷值、
负权重、权重全零等无效输入抵达 engine.rank。

设计约束（来自工作文档）：
- 缺失硬约束字段的手机默认不能宣称满足条件；
- 评分字段缺失时第一版从可评分集合移除并告知数量；
- 不把供应商 confidence 当作偏好强度或正确率；
- 不确定的字段保留为 None 并标记 needs_confirmation，不猜。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable, Mapping

# 本模块刻意只依赖标准库：契约校验不应被 numpy/pandas 的可用性绑架，
# 这样评测脚本与 CI 可以在无第三方依赖的环境下运行。

# --- 排序维度（与 engine.rank 的 keys 保持单一事实来源） -------------------

CRITERIA: tuple[str, ...] = ("weight", "storage", "screen", "photo", "game", "battery", "chipset")

CRITERIA_LABELS: dict[str, str] = {
    "weight": "机身更轻",
    "storage": "存储更大",
    "screen": "屏幕接近偏好",
    "photo": "拍照更好",
    "game": "王者荣耀帧率更高",
    "battery": "电池容量更大",
    "chipset": "处理器档位更高",
}

# 成本型为 True：数值越小越好。screen 在 engine 中先转为与目标尺寸的绝对偏差，
# 再作为成本型参与排序，因此此处也标记为成本型。
# 价格不在这里——价格由预算区间单独表达，不参与偏好权重。
COST_CRITERIA: frozenset[str] = frozenset({"weight", "screen"})

# --- 偏好等级（工作文档第 4 章「确定性权重映射」） ---------------------------

# 开发起点：不在意、一般、重要、非常重要 → 0、1、2、3。
LEVEL_MAP: dict[str, int] = {
    "不在意": 0,
    "一般": 1,
    "重要": 2,
    "非常重要": 3,
}

# 未提及映射到 1，但必须标记为系统默认，不得伪装成用户确认结果。
# 注意：这只是「等级序号」的映射，供问卷/抽取层判断倾向强度用。
# 真正决定权重数值的是下面的 WEIGHT_TABLE——两者不要混用。
UNMENTIONED_LEVEL = 1
NEUTRAL_LEVEL = 1

# 维度 × 偏好等级 → 绝对权重（0-10）。**刻意不做「等级 ÷ 等级总和」的归一化。**
#
# 旧做法（level / Σlevel）有两个死结：
#   1. 只突出一个维度时压不住其他维度。N=7 时单个维度最高只占 3/(3+6)=33%，
#      用户说「拍照非常重要」，其余六个未提及的维度合计仍占 67%。
#   2. 无法区分「样样都要好」和「什么都没说」——两者都退化成等权。
#
# 现在直接给绝对分，由引擎在矩阵装配时自行归一化（见 catalog/decision_input.py），
# 所以这里给绝对值是安全的。
#
# 「未提及」刻意非零：用户没说的维度按通用常识给一个低权重，而不是当成「不在意」，
# 否则预算充足的用户会拿到一堆只有单一长板的机型。
# 唯独 game 的未提及为 0——不提就是不打游戏；而且游戏权重一旦为正，候选池会从
# 51 个 SKU 砍到 8 个，可帧率在可比组内的极差只有 0.5%，等于用噪声换掉大部分候选。
WEIGHT_TABLE: dict[str, dict[str, int]] = {
    "storage": {"不在意": 0, "未提及": 3, "一般": 5, "重要": 7, "非常重要": 9},
    "weight": {"不在意": 0, "未提及": 2, "一般": 4, "重要": 7, "非常重要": 9},
    # photo 的「未提及」必须是 0：DXOMARK 照片子分在 new 模式只覆盖 11/51 个 SKU。
    # 给它正权重，候选池会从 51 直接掉到 11（再叠加电池只剩 6）——绝大多数机型
    # 连参与排序的资格都没有。这与 game 的「不提就是不打游戏」不同，是数据覆盖不足。
    "photo": {"不在意": 0, "未提及": 0, "一般": 4, "重要": 7, "非常重要": 9},
    "battery": {"不在意": 0, "未提及": 2, "一般": 4, "重要": 7, "非常重要": 9},
    "chipset": {"不在意": 0, "未提及": 2, "一般": 4, "重要": 7, "非常重要": 9},
    "screen": {"不在意": 0, "未提及": 1, "一般": 4, "重要": 6, "非常重要": 8},
    "game": {"不在意": 0, "未提及": 0, "一般": 3, "重要": 6, "非常重要": 9},
}
# 滑块量程。WEIGHT_TABLE 的取值不得超出它。
WEIGHT_MAX = 10

# Jev 的 Choice 取值集合。必须包含「未提及」，否则无法区分
# 「用户明确说不在意」与「用户根本没提」。
CHOICES: tuple[str, ...] = ("未提及", "不在意", "一般", "重要", "非常重要")

# 明确度等级：用于判断某个字段能否直接进入确认页，还是必须回问用户。
VERDICT_EXTRACTED = "extracted"  # 可从原文可靠提取
VERDICT_INFERRED = "inferred"  # 模型推断，需用户确认
VERDICT_UNKNOWN = "unknown"  # 无法可靠提取，必须询问


class SchemaError(ValueError):
    """结构或取值范围不合法。调用方应回到确认页或表单路线，而非继续计算。"""


def _require_finite(value: Any, name: str) -> float:
    """转成有限浮点值。None、NaN、inf 一律拒绝。"""
    if value is None:
        raise SchemaError(f"{name} 缺失，不能进入排序链路。")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise SchemaError(f"{name} 不是数值：{value!r}") from error
    if not math.isfinite(number):
        raise SchemaError(f"{name} 不是有限数值：{value!r}（NaN 或无穷值）")
    return number


def _require_non_negative_int(value: Any, name: str) -> int:
    number = _require_finite(value, name)
    if number < 0:
        raise SchemaError(f"{name} 不能为负：{number}")
    if number != int(number):
        raise SchemaError(f"{name} 必须是整数：{number}")
    return int(number)


@dataclass(frozen=True)
class HardConstraints:
    """硬约束：用于筛选，不参与加权评分。

    每一项都允许为 None。None 表示「用户没有给出该约束」，
    与「用户要求 3000 元以内」是两件不同的事——前者不筛选，后者才筛选。
    """

    budget_min: float | None = None
    budget_max: float | None = None
    min_storage: float | None = None
    brands: tuple[str, ...] = ()
    target_screen: float | None = None
    # 用户提到的单一锚点数字（「2000 左右」→ 2000）。**仅供界面显示与确认页引用，
    # 永远不参与筛选**，因此不出现在 to_filter_kwargs() 里。含糊预算的数字必须
    # 留下来给用户看，否则「2000 左右」的信息会整个丢失。
    budget_reference: float | None = None

    def __post_init__(self) -> None:
        for name in (
            "budget_min",
            "budget_max",
            "min_storage",
            "target_screen",
            "budget_reference",
        ):
            value = getattr(self, name)
            if value is None:
                continue
            number = _require_finite(value, name)
            if number <= 0:
                raise SchemaError(f"{name} 必须大于 0：{number}")
        if self.budget_min is not None and self.budget_max is not None:
            if self.budget_min > self.budget_max:
                raise SchemaError(
                    f"预算下界 {self.budget_min} 大于上界 {self.budget_max}，"
                    "条件自相矛盾，应提示用户确认而不是自动放宽。"
                )
        if self.target_screen is not None:
            if not 3.0 <= self.target_screen <= 10.0:
                raise SchemaError(
                    f"目标屏幕尺寸 {self.target_screen} 英寸超出合理范围 "
                    "3.0–10.0，应由用户确认。"
                )

    def to_filter_kwargs(self) -> dict[str, Any]:
        """转成筛选参数。只返回用户确实给出的条件。

        `budget_reference` **刻意不在这里** —— 它是参考值，不是筛选条件。
        把它混进筛选会让「2000 左右」变成硬上限，正是设计红线禁止的行为。
        """
        kwargs: dict[str, Any] = {}
        if self.budget_min is not None:
            kwargs["budget_min"] = self.budget_min
        if self.budget_max is not None:
            kwargs["budget_max"] = self.budget_max
        if self.min_storage is not None:
            kwargs["min_storage"] = self.min_storage
        if self.brands:
            kwargs["brands"] = tuple(self.brands)
        return kwargs


@dataclass
class PreferenceItem:
    """单个维度的偏好判断结果，保留原文、分类、映射与确认状态。

    工作文档第 4 章要求「初始权重的每一项保留原文、分类结果、映射规则
    和是否确认」。这里的字段一一对应。
    """

    criterion: str
    choice: str  # CHOICES 之一
    raw_text: str = ""  # 触发该分类的原文片段（证据）
    verdict: str = VERDICT_UNKNOWN  # extracted / inferred / unknown
    confirmed: bool = False  # 用户是否已确认
    locked_zero: bool = False  # 用户主动设为零，后续不采样、不追问
    confidence: float | None = None  # 供应商 confidence，仅作记录

    def __post_init__(self) -> None:
        if self.criterion not in CRITERIA:
            raise SchemaError(
                f"未知维度 {self.criterion!r}，只支持 {list(CRITERIA)}。"
            )
        if self.choice not in CHOICES:
            raise SchemaError(
                f"维度 {self.criterion} 的 choice={self.choice!r} 不在 "
                f"{list(CHOICES)} 中。必须包含「未提及」选项。"
            )
        if self.verdict not in (VERDICT_EXTRACTED, VERDICT_INFERRED, VERDICT_UNKNOWN):
            raise SchemaError(f"未知 verdict：{self.verdict!r}")
        if self.confidence is not None:
            value = _require_finite(self.confidence, "confidence")
            if not 0.0 <= value <= 1.0:
                raise SchemaError(
                    f"confidence 应在 0–1：{value}。"
                    "注意 confidence 不等于偏好强度或正确率。"
                )

    @property
    def is_default(self) -> bool:
        """未提及，走系统默认。"""
        return self.choice == "未提及"

    @property
    def mapped_level(self) -> int:
        """确定性权重映射：不在意的 0 / 一般 1 / 重要 2 / 非常重要 3。

        未提及映射到 1（中性先验），但调用方应同时检查 is_default。
        """
        if self.choice == "未提及":
            return UNMENTIONED_LEVEL
        return LEVEL_MAP[self.choice]


@dataclass
class Needs:
    """一次会话的需求集合：硬约束 + 偏好 + 未覆盖需求。"""

    hard: HardConstraints = field(default_factory=HardConstraints)
    preferences: list[PreferenceItem] = field(default_factory=list)
    unsupported_needs: list[str] = field(default_factory=list)
    raw_text: str = ""
    needs_confirmation: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for item in self.preferences:
            if item.criterion in seen:
                raise SchemaError(f"维度 {item.criterion} 重复出现。")
            seen.add(item.criterion)

    def preference_for(self, criterion: str) -> PreferenceItem | None:
        for item in self.preferences:
            if item.criterion == criterion:
                return item
        return None

    def unconfirmed_criteria(self) -> list[str]:
        """尚未确认的维度。工作文档要求先处理未确认项，再做偏好追问。"""
        return [item.criterion for item in self.preferences if not item.confirmed]

    def unsupported(self, criterion: str) -> bool:
        """该维度是否属于未覆盖需求（如续航、影像、真实游戏性能）。"""
        return criterion in self.unsupported_needs


@dataclass
class Session:
    """会话记录。data_version 用于实验分组与结果复算。"""

    session_id: str
    user_id: str
    data_version: str
    consent: bool = False
    created_at: str = ""
    engine_version: str = ""
    model_version: str = ""
    prompt_version: str = ""
    seed: int | None = None
    commit: str = ""

    def __post_init__(self) -> None:
        for name in ("session_id", "user_id", "data_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise SchemaError(f"{name} 不能为空。")
        if self.seed is not None:
            self.seed = _require_non_negative_int(self.seed, "seed")


@dataclass
class RankedProduct:
    """推荐记录中的单条候选，附带可引用事实编号。"""

    product_id: str
    product_name: str
    config: str
    score: float
    price: float
    criteria_values: dict[str, float] = field(default_factory=dict)
    fact_ids: tuple[str, ...] = ()


@dataclass
class Evidence:
    """解释 LLM 的结构化输入（工作文档第 8 章 ExplanationInput）。

    刻意不包含任何未在数据文件中存在的字段，避免模型凭记忆补参数。
    """

    ranked_products: list[RankedProduct] = field(default_factory=list)
    confirmed_needs: dict[str, str] = field(default_factory=dict)
    unsupported_needs: list[str] = field(default_factory=list)
    weight_history: list[dict[str, Any]] = field(default_factory=list)
    comparison_facts: list[dict[str, Any]] = field(default_factory=list)
    sensitivity_results: list[dict[str, Any]] = field(default_factory=list)
    fact_ids: list[str] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)
    data_timestamp: str = ""

    def known_fact_ids(self) -> set[str]:
        """证据包内全部合法 fact_id，供生成后校验使用。"""
        known = set(self.fact_ids)
        for product in self.ranked_products:
            known.update(product.fact_ids)
        for fact in self.comparison_facts:
            fact_id = fact.get("fact_id")
            if isinstance(fact_id, str):
                known.add(fact_id)
        return known


@dataclass
class Explanation:
    """解释 LLM 的结构化输出（工作文档第 8 章 ExplanationOutput）。"""

    summary: str = ""
    reasons: list[dict[str, Any]] = field(default_factory=list)
    tradeoffs: list[dict[str, Any]] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    is_template_fallback: bool = False


# --- 与 engine.rank 的边界校验 ---------------------------------------------


def validate_weights(weights: Mapping[str, Any]) -> dict[str, float]:
    """校验参与排序的五项权重。

    engine.rank 自身也会报错，但错误发生在排序内部时已经难以区分
    「用户输入错」与「程序算错」。这里提前拦截，让调用方回到确认页。
    """
    missing = [key for key in CRITERIA if key not in weights]
    if missing:
        raise SchemaError(f"权重缺少维度：{missing}")
    extra = [key for key in weights if key not in CRITERIA]
    if extra:
        raise SchemaError(f"权重含未知维度：{extra}")

    clean: dict[str, float] = {}
    for key in CRITERIA:
        value = _require_finite(weights[key], f"权重 {key}")
        if value < 0:
            raise SchemaError(
                f"权重 {key} 为负：{value}。工作文档要求负权重必须明确报错。"
            )
        clean[key] = value

    if sum(clean.values()) <= 0:
        raise SchemaError(
            "权重全零，无法区分候选。应提示用户至少设置一项大于 0，"
            "而不是返回一份看似有效的排名。"
        )
    return clean


def validate_candidate_frame(frame: Any) -> list[str]:
    """检查候选集是否具备评分所需字段，返回被移除的记录说明。

    工作文档第 3 章：缺失硬约束字段的手机默认不能宣称满足条件；
    评分字段缺失时第一版从本次可评分集合移除并告知数量。

    实现上逐行读取，避免为契约校验引入 numpy 依赖。候选集规模为百级，
    逐行代价可忽略；真正的向量化计算仍在 engine.rank 内完成。
    """
    required = set(CRITERIA)
    has_columns = hasattr(frame, "columns")
    if has_columns:
        columns = set(frame.columns)
    else:
        # list[dict] 形式：取所有记录键的并集作为列集合。
        try:
            columns = {key for row in frame for key in row}
        except TypeError:
            raise SchemaError(
                "候选集既不是 DataFrame 也不是可迭代的 dict 序列，无法检查字段。"
            )
    missing_columns = required - columns
    if missing_columns:
        raise SchemaError(
            f"候选集缺少评分列：{sorted(missing_columns)}，无法计算。"
        )

    if has_columns and getattr(frame, "empty", False):
        return []

    # 优先走 DataFrame 的逐行接口；也接受 list[dict] 形式的候选集。
    if hasattr(frame, "iterrows") and hasattr(frame, "index"):
        rows: Iterable[tuple[Any, Mapping[str, Any]]] = (
            (frame.iloc[position].get("id", f"第 {position} 行"), row)
            for position, (_, row) in enumerate(frame.iterrows())
        )
    else:
        rows = (
            (row.get("id", f"第 {position} 行"), row)
            for position, row in enumerate(frame)
        )

    notices: list[str] = []
    for identifier, row in rows:
        bad_fields: list[str] = []
        for key in CRITERIA:
            try:
                value = float(row[key])
            except (TypeError, ValueError, KeyError):
                bad_fields.append(key)
                continue
            if not math.isfinite(value):
                bad_fields.append(key)
        if bad_fields:
            notices.append(f"{identifier} 缺失或无效字段：{bad_fields}")
    return notices


def mapped_levels(needs: Needs) -> dict[str, int]:
    """把需求中的偏好映射成各维度的整数等级。

    未出现在需求里的维度不返回，由调用方决定是否补中性先验。
    """
    return {item.criterion: item.mapped_level for item in needs.preferences}


def initial_weights(needs: Needs, criteria: Iterable[str] = CRITERIA) -> dict[str, float]:
    """各维度的初始权重，按 `WEIGHT_TABLE` 查表得到 0-10 的**绝对值**。

    与旧实现的区别：不再返回「占比」，也不再要求总和为 1。
    - 旧：w0_j = level_j / Σlevel，导致只突出一个维度时它也压不住其他维度。
    - 新：w0_j = WEIGHT_TABLE[维度][等级]，绝对值，由引擎在装配矩阵时归一化。

    全部维度都是「不在意」（总权重为 0）时抛 `SchemaError`，
    由调用方询问用户或使用已确认的均匀先验。
    """
    levels = {item.criterion: item.choice for item in needs.preferences}
    weights: dict[str, float] = {}
    for key in criteria:
        row = WEIGHT_TABLE.get(key)
        if row is None:
            raise SchemaError(f"未定义的偏好维度：{key}")
        choice = levels.get(key, "未提及")
        if choice not in row:
            raise SchemaError(f"维度 {key} 的偏好等级非法：{choice}")
        weights[key] = float(row[choice])
    if sum(weights.values()) <= 0:
        raise SchemaError(
            "全部维度映射等级为零，无法生成初始权重。"
            "应询问用户或使用已确认的均匀先验。"
        )
    return weights


def as_dict(record: Any) -> dict[str, Any]:
    """递归转成可序列化字典，便于写入事件日志。"""
    if hasattr(record, "__dataclass_fields__"):
        return {key: as_dict(value) for key, value in asdict(record).items()}
    if isinstance(record, Mapping):
        return {key: as_dict(value) for key, value in record.items()}
    if isinstance(record, (list, tuple)):
        return [as_dict(item) for item in record]
    if isinstance(record, frozenset):
        return sorted(record)
    # numpy 标量实现 __float__/__int__，转成 Python 原生类型以便序列化。
    if hasattr(record, "item") and not isinstance(record, (str, bytes)):
        try:
            return record.item()
        except (AttributeError, ValueError):
            pass
    return record


__all__ = [
    "CRITERIA",
    "CRITERIA_LABELS",
    "COST_CRITERIA",
    "LEVEL_MAP",
    "CHOICES",
    "UNMENTIONED_LEVEL",
    "NEUTRAL_LEVEL",
    "VERDICT_EXTRACTED",
    "VERDICT_INFERRED",
    "VERDICT_UNKNOWN",
    "SchemaError",
    "HardConstraints",
    "PreferenceItem",
    "Needs",
    "Session",
    "RankedProduct",
    "Evidence",
    "Explanation",
    "validate_weights",
    "validate_candidate_frame",
    "mapped_levels",
    "initial_weights",
    "WEIGHT_TABLE",
    "WEIGHT_MAX",
    "as_dict",
]
