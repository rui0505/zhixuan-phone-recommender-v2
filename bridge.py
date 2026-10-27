"""Needs → 界面控件与引擎入参的翻译层。

这是 `providers/`（产出结构化需求 `Needs`）与 `engine.rank`（需要
`weights` 与 `target`）之间缺失的一环。所有映射规则集中在这里，便于
单测与审计；`app.py` 只负责把结果塞进控件。

三条硬规则（来自工作文档，本模块是它们唯一的落点）：

1. **含糊预算由程序按锚点推断区间，并且必须回显**。`budget_max is None` 但用户
   给了锚点（「5000左右」「三千多」）时，按非对称阶梯推断一个区间，写进 `notes`
   让用户看见并可修改。**推断区间不是用户原话，不得伪装成明确约束**；
   既无锚点也无边界时才进 `pending` 追问。用户的明确边界（「不超过5000」）
   永远优先于推断，不会被覆盖。
   > 2026-10-25 修订：原规则是「含糊预算一律不推断、必须进 pending 追问」。
   > 改为推断的依据是实测——追问回答率低，且无锚点时程序会落到系统默认
   > (1000,6000)，说「8000左右」会被砍掉 10 个机型。红线的原意是
   > 「不得**静默**封顶」，推断 + 回显满足了这一点，因此保留红线、更换机制。
2. **排除式品牌不转白名单**。「不要华为」走独立的 `excluded_brands`
   过滤，绝不写进 `brands`（白名单）。这两件事在筛选语义上完全不同。
3. **未覆盖需求不得用替代指标充数**。原样透传给界面提示，不映射到任何
   可评分维度。

另外两件事必须做对，否则界面会崩：

- 控件值必须落在控件允许的取值集合内（存储只能取选项之一，屏幕要在
  滑块范围内，品牌必须存在于数据表）。越界直接抛 Streamlit 异常。
- 越界时**向上取整**而不是就近取整：存储门槛 300GB 应落到 512 而不是 256，
  否则会放出不满足门槛的候选。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from schemas import (
    CRITERIA,
    VERDICT_EXTRACTED,
    VERDICT_UNKNOWN,
    HardConstraints,
    Needs,
    PreferenceItem,
    SchemaError,
    WEIGHT_MAX,
    initial_weights,
)

# 控件取值域。与 app.py 里的控件定义必须一致。
BUDGET_LIMIT = (0, 20000)
BUDGET_STEP = 100
BUDGET_DEFAULT = (1000, 6000)
STORAGE_OPTIONS = (64, 128, 256, 512, 1024, 2048)
STORAGE_DEFAULT = 256
SCREEN_RANGE = (5.5, 7.0)
SCREEN_DEFAULT = 6.5
WEIGHT_STEP = 5
WEIGHT_SCALE = 100

# 含糊预算（「5000左右」）的推断阶梯：(下浮, 上浮)。
# 刻意不对称——手机价格分布右偏，用户对「贵一点」的容忍度高于「降级」。
# 系数由真实快照标定：新机模式下 1500–12000 每个锚点都能落到 >=3 个独立机型。
BUDGET_INFERENCE_LADDER = ((0.85, 1.25), (0.75, 1.40), (0.65, 1.55))
# 区间内至少要有这么多个独立机型，否则按阶梯放宽（解决价格断档）。
BUDGET_INFERENCE_MIN_MODELS = 3


@dataclass
class Bridged:
    """翻译结果：可直接喂给界面控件与 `engine.rank`。"""

    budget: tuple[int, int]
    brands: list[str]
    excluded_brands: list[str]
    storage: int
    target: float
    weights: dict[str, float]
    pending: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def needs_user_confirmation(self) -> bool:
        return bool(self.pending)


def _floor_step(value: float) -> int:
    """向下取整到滑块步长。宁可放宽下界，也不要错误排除用户能接受的机型。"""
    return int(value // BUDGET_STEP * BUDGET_STEP)


def _ceil_step(value: float) -> int:
    """向上取整到滑块步长。宁可放宽上界，也不要错误排除用户买得起的机型。"""
    return int(-(-value // BUDGET_STEP) * BUDGET_STEP)


def infer_budget_range(
    reference: float, price_index: Iterable[tuple[float, str]] | None = None
) -> tuple[int, int, int]:
    """把含糊锚点推断成可用预算区间。返回 `(下界, 上界, 放宽档位)`。

    锚点定中心，候选密度定宽度：区间内独立机型不足 `BUDGET_INFERENCE_MIN_MODELS`
    时按阶梯逐级放宽（新机模式在 3000–4999 有价格断档，固定比例会返回空）。

    `price_index` 是 `(价格, 机型ID)` 序列；不传则只用第一档（纯函数，不碰数据）。
    """
    if (isinstance(reference, bool) or not isinstance(reference, (int, float))
            or not math.isfinite(reference) or reference <= 0):
        raise ValueError("预算锚点必须是有限正数")
    floor, ceiling = BUDGET_LIMIT
    if price_index is None:
        low_factor, high_factor = BUDGET_INFERENCE_LADDER[0]
        return (_floor_step(reference * low_factor), _ceil_step(reference * high_factor), 1)
    pairs = [(float(amount), str(model)) for amount, model in price_index]
    for level, (low_factor, high_factor) in enumerate(BUDGET_INFERENCE_LADDER, 1):
        start = max(floor, reference * low_factor)
        end = min(ceiling, reference * high_factor)
        models = {model for amount, model in pairs if start <= amount <= end}
        if len(models) >= BUDGET_INFERENCE_MIN_MODELS:
            return _floor_step(start), _ceil_step(end), level
    low_factor, high_factor = BUDGET_INFERENCE_LADDER[-1]
    return (_floor_step(max(floor, reference * low_factor)),
            _ceil_step(min(ceiling, reference * high_factor)),
            len(BUDGET_INFERENCE_LADDER))


def _snap_budget(
    low: float | None,
    high: float | None,
    reference: float | None = None,
    price_index: Iterable[tuple[float, str]] | None = None,
) -> tuple[tuple[int, int], list[str], list[str]]:
    """把预算翻译成滑块区间。返回 (区间, 待确认项, 说明)。

    优先级：**明确上界 > 明确下界 > 锚点推断 > 系统默认**。
    用户的明确表达永远压过推断，推断只填补"什么都没说清"的空白。

    关键约束：**上界不得低于用户声明的下界**。用户说「预算 8000 以上」时，
    拿系统默认上界 6000 去配对会得到一个空区间，把用户的机型全部排除。
    这种情况下只能不封顶，并把「上界是多少」交给确认页。
    """
    pending: list[str] = []
    notes: list[str] = []
    floor, ceiling = BUDGET_LIMIT

    if high is not None:
        start = floor if low is None else _floor_step(low)
        if low is not None:
            notes.append(f"预算下界 ¥{start:,}（原文明确）")
        end = _ceil_step(high)
        notes.append(f"预算上界 ¥{end:,}（原文明确）")
    elif low is not None:
        # 有下界没上界：含糊表达，不得封顶，否则会卡在用户下界之下
        start = _floor_step(low)
        end = ceiling
        pending.append("budget_max")
        notes.append(f"预算下界 ¥{start:,}（原文明确）")
        notes.append("上界未明确，暂不封顶——请确认你能接受到多少")
    elif reference is not None:
        # 只有锚点：推断区间，并把它明确写出来让用户看见。
        start, end, level = infer_budget_range(reference, price_index)
        notes.append(f"预算按「{reference:g} 左右」推断为 ¥{start:,}–¥{end:,}（推断值，可修改）")
        if level > 1:
            notes.append(f"该价位候选较少，区间已放宽到第 {level} 档")
    else:
        start, end = BUDGET_DEFAULT
        pending.append("budget_max")
        notes.append(f"预算未提及，用系统默认 ¥{start:,}–¥{end:,}")

    start = max(floor, min(start, ceiling))
    end = max(floor, min(end, ceiling))
    if start > end:
        # 两侧都被夹到量程边缘时才可能出现
        start, end = end, start
    return (start, end), pending, notes


def _snap_storage(value: float | None) -> tuple[int, list[str], list[str]]:
    """存储门槛落到可选值上，**向上**取整。"""
    if value is None:
        return STORAGE_DEFAULT, [], [f"存储门槛未提及，用系统默认 {STORAGE_DEFAULT}GB"]

    for option in STORAGE_OPTIONS:
        if option >= value:
            note = f"存储门槛 {value:g}GB → 向上取到 {option}GB（原文明确）"
            if option != value:
                note = f"存储门槛 {value:g}GB 不在可选项内 → 向上取到 {option}GB"
            return option, [], [note]

    # 超过最大可选项：用最大值，并明确告知这是降级
    return (
        STORAGE_OPTIONS[-1],
        [],
        [f"存储门槛 {value:g}GB 超过数据最大档位，按 {STORAGE_OPTIONS[-1]}GB 处理"],
    )


def _clamp_screen(value: float | None) -> tuple[float, list[str]]:
    low, high = SCREEN_RANGE
    if value is None:
        return SCREEN_DEFAULT, [f"屏幕尺寸未提及，用系统默认 {SCREEN_DEFAULT} 英寸"]
    if value < low or value > high:
        clamped = min(max(value, low), high)
        return clamped, [f"屏幕尺寸 {value:g} 英寸超出可选范围，已收敛到 {clamped:g}"]
    return float(value), [f"屏幕尺寸 {value:g} 英寸（原文明确）"]


def _to_slider_weights(needs: Needs) -> tuple[dict[str, float], list[str]]:
    """偏好等级 → 0–10 滑块值（**绝对值**，不做占比归一化）。

    直接查 `schemas.WEIGHT_TABLE`。引擎侧装配矩阵时会自行归一化，所以这里保持
    绝对值——用户说「非常重要」的那一维才能真的压过其他维度的默认值。

    2026-10-26 修订：旧实现返回 0-100 的**占比**值，而 app.py 用 `value*10` 换算到
    0-10 滑块，结果每一维都被 clamp 到 10——LLM 抽出的偏好从来没有在界面上生效过。
    现在两端口径统一为 0-10，且不再做占比换算。
    """
    notes: list[str] = []
    try:
        raw = initial_weights(needs)
    except SchemaError:
        raw = {key: 1.0 for key in CRITERIA}
        notes.append(
            "所有维度都被标为「不在意」，无法生成权重，改用均匀先验；请确认是否真的都不在意"
        )

    weights: dict[str, float] = {
        key: float(max(0, min(WEIGHT_MAX, int(round(value))))) for key, value in raw.items()
    }
    # 取整后可能全部落到 0（极端偏斜时不会，但兜底）
    if sum(weights.values()) <= 0:
        weights = {key: 1.0 for key in CRITERIA}
        notes.append("权重取整后全为零，改用均匀权重")

    stated = [
        f"{item.criterion}={item.choice}"
        for item in needs.preferences
        if not item.is_default
    ]
    if stated:
        notes.append("偏好来自原文：" + "，".join(stated))
    else:
        notes.append("原文没有表达任何偏好，五项权重按中性先验均匀分配")
    return weights, notes


def _extract_excluded(needs: Needs) -> tuple[list[str], list[str]]:
    """从 `needs_confirmation` 里抠出排除式品牌。

    `Needs` 没有独立的 `excluded_brands` 字段——`providers.jev.parse_needs`
    把它编码成 `"excluded_brands=华为/荣耀"` 塞进 `needs_confirmation`。
    返回 (排除的品牌, 其余待确认项)。
    """
    excluded: list[str] = []
    rest: list[str] = []
    for item in needs.needs_confirmation:
        if item.startswith(_EXCLUDED_PREFIX):
            payload = item[len(_EXCLUDED_PREFIX) :]
            excluded.extend(part for part in payload.split("/") if part)
        else:
            rest.append(item)
    return excluded, rest


_EXCLUDED_PREFIX = "excluded_brands="

_PENDING_LABEL = {
    "budget_max": "预算上界",
    "budget_min": "预算下界",
    "min_storage": "存储门槛",
    "target_screen": "屏幕尺寸",
    "excluded_brands": "排除品牌",
}


def _humanize(item: str) -> str:
    """把待确认项转成人能读的短语。"""
    if item.startswith(_EXCLUDED_PREFIX):
        names = item[len(_EXCLUDED_PREFIX) :].replace("/", "、")
        return f"排除品牌：{names}"
    return _PENDING_LABEL.get(item, item)


def bridge(
    needs: Needs,
    known_brands: list[str] | None = None,
    price_index: Iterable[tuple[float, str]] | None = None,
) -> Bridged:
    """把 `Needs` 翻译成界面控件值与引擎入参。

    `known_brands` 传入数据表里实际存在的品牌；不在其中的品牌会被剔除并
    记录说明——否则 Streamlit 的 multiselect 会因为选项不存在而报错。

    `price_index` 传入 `(价格, 机型ID)` 序列，用于把含糊预算（「5000左右」）
    推断成有足够候选的区间；不传则只按锚点算固定比例，不做密度放宽。
    """
    budget, budget_pending, budget_notes = _snap_budget(
        needs.hard.budget_min,
        needs.hard.budget_max,
        reference=needs.hard.budget_reference,
        price_index=price_index,
    )
    storage, storage_pending, storage_notes = _snap_storage(needs.hard.min_storage)
    target, screen_notes = _clamp_screen(needs.hard.target_screen)
    weights, weight_notes = _to_slider_weights(needs)

    notes = budget_notes + storage_notes + screen_notes + weight_notes

    brands = list(needs.hard.brands)
    if known_brands is not None:
        unknown = [item for item in brands if item not in known_brands]
        brands = [item for item in brands if item in known_brands]
        if unknown:
            notes.append("原文提到的品牌不在数据表内，已忽略：" + "、".join(unknown))

    excluded, other_pending = _extract_excluded(needs)
    if known_brands is not None:
        excluded = [item for item in excluded if item in known_brands]

    pending = [
        _humanize(item)
        for item in _dedupe(budget_pending + storage_pending + other_pending)
    ]

    return Bridged(
        budget=budget,
        brands=brands,
        excluded_brands=excluded,
        storage=storage,
        target=target,
        weights=weights,
        pending=pending,
        unsupported=list(needs.unsupported_needs),
        notes=notes,
    )


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


# --- 契约 dict 适配 ----------------------------------------------------------
#
# `needs_extraction.extract_needs` 返回的是契约 dict，`bridge()` 消费的是
# `Needs` 对象。这里做一次转换，好处是 `bridge()` 的全部规则（含糊预算不封顶、
# 排除品牌不转白名单、存储向上取整…）**只有一份实现**，不会在两条链路上分叉。


def _opt_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _safe_hard(kwargs: dict[str, Any]) -> tuple[HardConstraints, list[str]]:
    """构造 `HardConstraints`，遇到非法组合时逐项降级而不是整条作废。

    真实模型偶尔会给出自相矛盾的硬约束（下界高于上界）或超范围的屏幕尺寸。
    `HardConstraints.__post_init__` 会直接抛，但**一条需求不该因为一个字段
    而整条丢掉**——丢字段、保其余，并记下丢了什么交给界面提示。
    """
    try:
        return HardConstraints(**kwargs), []
    except SchemaError:
        pass

    # 降级顺序：先丢自相矛盾的预算区间，再丢超范围的屏幕，最后丢存储与参考值。
    plan = (
        (("budget_min", "budget_max"), "预算区间自相矛盾，已忽略上下界"),
        (("target_screen",), "屏幕尺寸超出合理范围，已忽略"),
        (("min_storage",), "存储门槛非法，已忽略"),
        (("budget_reference",), "参考预算非法，已忽略"),
    )
    current = dict(kwargs)
    dropped: list[str] = []
    for names, reason in plan:
        for name in names:
            current[name] = None
        try:
            return HardConstraints(**current), dropped + [reason]
        except SchemaError:
            dropped.append(reason)
            continue
    return HardConstraints(), dropped


def needs_from_contract(result: Mapping[str, Any]) -> tuple[Needs, list[str]]:
    """把 `extract_needs` 的契约 dict 转成 `Needs` 对象。

    返回 `(needs, 降级说明)`。降级说明非空时界面应提示用户确认。
    """
    budget = result.get("budget") or {}
    brands = result.get("brands") or {}

    hard_kwargs: dict[str, Any] = {
        "budget_min": _opt_float(budget.get("min")),
        "budget_max": _opt_float(budget.get("max")),
        "budget_reference": _opt_float(budget.get("reference")),
        "min_storage": _opt_float(result.get("min_storage")),
        "target_screen": _opt_float(result.get("target_screen")),
        "brands": tuple(
            item for item in (brands.get("include") or []) if isinstance(item, str)
        ),
    }
    hard, degraded = _safe_hard(hard_kwargs)

    preferences: list[PreferenceItem] = []
    for item in result.get("preferences") or []:
        if not isinstance(item, Mapping):
            continue
        criterion = item.get("criterion")
        level = item.get("level")
        evidence = item.get("evidence")
        if not isinstance(criterion, str) or not isinstance(level, str):
            continue
        if criterion not in CRITERIA:
            continue
        try:
            preferences.append(
                PreferenceItem(
                    criterion=criterion,
                    choice=level,
                    raw_text=evidence if isinstance(evidence, str) else "",
                    verdict=(
                        VERDICT_EXTRACTED
                        if isinstance(evidence, str) and evidence
                        else VERDICT_UNKNOWN
                    ),
                )
            )
        except SchemaError:
            continue

    excluded = [item for item in (brands.get("exclude") or []) if isinstance(item, str)]
    # `bridge()` 从 `needs_confirmation` 里按前缀抠排除品牌，这里沿用同一编码，
    # 避免为了适配契约再写第二套解析逻辑。
    confirmation = [f"{_EXCLUDED_PREFIX}{'/'.join(excluded)}"] if excluded else []

    needs = Needs(
        hard=hard,
        preferences=preferences,
        unsupported_needs=[
            item["need"]
            for item in (result.get("unsupported_needs") or [])
            if isinstance(item, Mapping) and isinstance(item.get("need"), str)
        ],
        raw_text=result.get("input_text") or "",
        needs_confirmation=confirmation,
    )
    return needs, degraded


def bridge_contract(
    result: Mapping[str, Any],
    known_brands: list[str] | None = None,
    price_index: Iterable[tuple[float, str]] | None = None,
) -> tuple[Bridged, list[str]]:
    """契约 dict → 控件值与引擎入参。返回 `(Bridged, 降级说明)`。"""
    needs, degraded = needs_from_contract(result)
    return bridge(needs, known_brands=known_brands, price_index=price_index), degraded


__all__ = [
    "BUDGET_LIMIT",
    "BUDGET_STEP",
    "BUDGET_DEFAULT",
    "BUDGET_INFERENCE_LADDER",
    "BUDGET_INFERENCE_MIN_MODELS",
    "infer_budget_range",
    "STORAGE_OPTIONS",
    "STORAGE_DEFAULT",
    "SCREEN_RANGE",
    "SCREEN_DEFAULT",
    "WEIGHT_STEP",
    "Bridged",
    "bridge",
    "bridge_contract",
    "needs_from_contract",
]
