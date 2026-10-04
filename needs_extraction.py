"""需求抽取模块 · 对外契约入口。

三个人共用的接口。界面同学调用 `extract_needs(user_text)` 拿到固定结构的
dict；算法同学消费其中的 `preferences[].level`。

完整契约见 `docs/needs-extraction-contract.md`。

## 设计约束（2026-10-01 与需求方确认）

1. **只输出等级，不输出数字权重。** 等级 → 权重的映射由算法同学负责，
   唯一来源是 `schemas.LEVEL_MAP`。本模块不碰数值权重。
2. **含糊预算不封顶，但数字必须留下来。** 「2000 左右」→
   `budget.reference=2000`，`min`/`max` 留 null 并进澄清清单。
3. **任何失败都不抛异常**，返回 `status="failed"` 的完整结构。
   界面必须能无条件渲染，不能因为 API 挂了就白屏。
4. **排除式品牌与白名单分开**（`brands.include` / `brands.exclude`），
   绝不互相转换。

## 用法

    from needs_extraction import extract_needs

    result = extract_needs("想买 2000 元左右、拍照好点、轻一点的手机。")
    if result["status"] == "ok":
        for item in result["preferences"]:
            print(item["criterion"], item["level"])
"""

from __future__ import annotations

import re
import time
from typing import Any, Mapping

from providers.jev import (
    UNSUPPORTED_RULES,
    JevResult,
    JevUnavailable,
    classify_preferences,
)
from schemas import CHOICES, CRITERIA

try:
    from providers.llm import PROMPT_VERSION
except ImportError:  # pragma: no cover - 只在 providers 缺失时触发
    PROMPT_VERSION = "unknown"

SCHEMA_VERSION = "1.0"

CRITERION_LABELS = {
    "price": "价格更低",
    "weight": "机身更轻",
    "storage": "存储更大",
    "refresh": "刷新率更高",
    "screen": "屏幕接近偏好",
}

UNSUPPORTED_VALUES = (
    "影像",
    "续航",
    "真实游戏性能",
    "分辨率",
    "机身尺寸手感",
    "信号质量",
    "处理器性能",
)

# 字段名 → (契约字段路径, 给用户看的问题, 为什么要问)
CLARIFICATION_TEXT: dict[str, tuple[str, str, str]] = {
    "budget_max": (
        "budget.max",
        "你没有给出预算上限，最多能接受到多少？",
        "含糊预算不自动封顶，必须由用户确认",
    ),
    "budget_min": (
        "budget.min",
        "你没有给出预算下限，最低能接受到多少？",
        "缺少预算下界",
    ),
    "min_storage": (
        "min_storage",
        "你没有说明最低存储容量，需要多大？",
        "存储门槛无法可靠提取",
    ),
    "target_screen": (
        "target_screen",
        "你没有说明偏好的屏幕尺寸，想要多大？",
        "屏幕偏好无法可靠提取",
    ),
    "excluded_brands": (
        "brands.exclude",
        "你排除了某些品牌，希望我们怎么处理？",
        "排除式品牌不能当白名单用",
    ),
}

# 整条链路失败时，这些字段一律要问
FALLBACK_ASKS = ("budget_max", "min_storage", "target_screen")

# 契约里的 `clarifications[].field` 是字段**路径**（`budget.max`），
# 而界面与评测脚本用的是字段**名**（`budget_max`）。这张表是两者的桥。
# `preferences.*` 是降级项，不是待确认的硬约束，联调时跳过。
FIELD_PATH_TO_ASK = {
    "budget.max": "budget_max",
    "budget.min": "budget_min",
    "min_storage": "min_storage",
    "target_screen": "target_screen",
    "brands.exclude": "excluded_brands",
}

# 含糊预算的表达。「2000 左右」这类不能自动封顶，但数字要留在 reference。
# 用于 `validate_result` 反查「含糊预算被当成了硬边界」这种红线错误。
_VAGUE_BUDGET_PATTERNS = (
    r"\d+\s*元?\s*(?:左右|上下|差不多|多|来|把|出头)",
    r"[零一二两三四五六七八九]\s*千\s*(?:多|左右|来|把)",
    r"[零一二两三四五六七八九][零一二两三四五六七八九]\s*千",
)

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_FAILED = "failed"


# --- 对外入口 ---------------------------------------------------------------


def extract_needs(
    user_text: str,
    *,
    classifier: Any = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """把用户的中文购机需求抽成契约规定的固定结构。

    `classifier` 留空时用 `providers.llm.LLMClassifier`（需要环境变量
    `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY`）；测试时可注入替身。

    **本函数不抛异常。** 失败返回 `status="failed"`，结构与其他情况一致。
    """
    started = time.monotonic()
    text = (user_text or "").strip()
    if not text:
        return _failed(text, "输入为空", started)

    try:
        if classifier is None:
            classifier = _default_classifier()
        result = classify_preferences(text, classifier=classifier)
    except JevUnavailable as error:
        return _failed(text, str(error), started)
    except Exception as error:  # noqa: BLE001 - 契约要求永不抛
        return _failed(text, f"{type(error).__name__}: {error}", started)

    return _shape(text, result, started)


def validate_result(result: Mapping[str, Any]) -> list[str]:
    """校验返回结构是否符合契约。返回问题列表，空列表表示通过。

    给下游同学在联调时自查用，也用于本模块的单测。
    """
    problems: list[str] = []

    for key in (
        "schema_version",
        "prompt_version",
        "status",
        "input_text",
        "budget",
        "min_storage",
        "target_screen",
        "brands",
        "preferences",
        "unsupported_needs",
        "clarifications",
        "meta",
    ):
        if key not in result:
            problems.append(f"缺少顶层字段：{key}")
    if problems:
        return problems

    if result["status"] not in (STATUS_OK, STATUS_DEGRADED, STATUS_FAILED):
        problems.append(f"status 取值非法：{result['status']!r}")

    budget = result["budget"]
    for key in ("reference", "min", "max", "evidence"):
        if key not in budget:
            problems.append(f"budget 缺少字段：{key}")
    for key in ("reference", "min", "max"):
        value = budget.get(key)
        if value is not None and not isinstance(value, (int, float)):
            problems.append(f"budget.{key} 必须是数字或 null，实际 {value!r}")
        if isinstance(value, (int, float)) and value <= 0:
            problems.append(f"budget.{key} 必须大于 0，实际 {value}")

    if (
        budget.get("min") is not None
        and budget.get("max") is not None
        and budget["min"] > budget["max"]
    ):
        problems.append(
            f"budget.min({budget['min']}) 大于 budget.max({budget['max']})，"
            "条件自相矛盾，应提示用户确认而不是自动放宽"
        )

    # 红线：含糊预算不得自动封顶。
    # 注意「预算 3000 以内」时 reference 与 max 同为 3000 是**合法**的
    # （契约 §4.1 明确如此），所以不能只看两者是否相等——必须回到原文，
    # 看这个数字旁边是不是跟着「左右 / 多 / 四五千」这类含糊量词。
    vague = _vague_boundary_conflict(budget, result.get("input_text") or "")
    if vague:
        problems.append(
            f"原文含含糊预算「{vague}」，却设了硬边界"
            f"（min={budget.get('min')}, max={budget.get('max')}）；"
            "按口径应只填 reference，由确认页问用户"
        )

    if result["min_storage"] is not None and result["min_storage"] <= 0:
        problems.append("min_storage 必须大于 0")
    if result["target_screen"] is not None and not 3.0 <= result["target_screen"] <= 10.0:
        problems.append("target_screen 超出 3.0–10.0 合理范围")

    brands = result["brands"]
    for key in ("include", "exclude"):
        if key not in brands:
            problems.append(f"brands 缺少字段：{key}")
        elif not isinstance(brands[key], list):
            problems.append(f"brands.{key} 必须是数组")
    overlap = set(brands.get("include") or ()) & set(brands.get("exclude") or ())
    if overlap:
        problems.append(f"同一品牌同时出现在白名单与排除名单：{sorted(overlap)}")

    preferences = result["preferences"]
    seen = [item.get("criterion") for item in preferences]
    if sorted(seen) != sorted(CRITERIA):
        problems.append(f"preferences 必须恰好包含 {list(CRITERIA)}，实际 {seen}")
    for item in preferences:
        if item.get("level") not in CHOICES:
            problems.append(
                f"{item.get('criterion')} 的 level={item.get('level')!r} 不在 {list(CHOICES)}"
            )
        if not isinstance(item.get("evidence"), str):
            problems.append(f"{item.get('criterion')} 的 evidence 必须是字符串")

    for item in result["unsupported_needs"]:
        if item.get("need") not in UNSUPPORTED_VALUES:
            problems.append(f"unsupported_needs 出现未约定的取值：{item.get('need')!r}")

    for item in result["clarifications"]:
        for key in ("field", "question", "reason"):
            if not item.get(key):
                problems.append(f"clarifications 项缺少 {key}：{item}")

    meta = result["meta"]
    for key in ("provider", "model", "elapsed_ms", "error"):
        if key not in meta:
            problems.append(f"meta 缺少字段：{key}")
    if meta.get("error") and result["status"] == STATUS_OK:
        problems.append("status=ok 却带有 meta.error")

    return problems


# --- 内部实现 ---------------------------------------------------------------


def _default_classifier() -> Any:
    from providers.llm import LLMClassifier

    return LLMClassifier()


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _find_vague_budget(text: str) -> str:
    """在原文里找出含糊预算片段，找不到返回空串。

    只认紧跟在数字后面的量词，避免误伤「屏幕 6.4 英寸左右」这类
    非预算表达（数字与量词之间隔着「英寸」，不匹配）。
    """
    for pattern in _VAGUE_BUDGET_PATTERNS:
        match = re.search(pattern, text)
        if match:
            return match.group(0).strip()
    return ""


# 汉字数字 → 阿拉伯数字。只用到 0–9，够覆盖「三千」「四五千」这类表达。
_CN_DIGITS = {
    "零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}


def _numbers_in(fragment: str) -> set[float]:
    """把一段文本里的数字抠出来，含「三千」这类汉字数字。

    「四五千」会同时给出 4、5 与 5000——多给几个候选值没关系，调用方只拿它
    做「这个数字是不是来自这段含糊表达」的判断。
    """
    numbers = {float(raw) for raw in re.findall(r"\d+(?:\.\d+)?", fragment)}
    numbers |= {float(_CN_DIGITS[digit]) for digit in
                re.findall(r"[零一二两三四五六七八九]", fragment)}
    numbers |= {float(_CN_DIGITS[digit]) * 1000 for digit in
                re.findall(r"([零一二两三四五六七八九])\s*千", fragment)}
    return numbers


def _last_utterance(text: str) -> str:
    """取多轮对话里**最后一句**用户输入。

    `pipeline.join_history` 把先后几句编号后各占一行，所以最后一个非空行
    就是用户最新说的话；单轮输入没有换行，原样返回。
    """
    lines = [line for line in (text or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _vague_boundary_conflict(budget: Mapping[str, Any], text: str) -> str:
    """含糊预算被当成了硬边界？返回那段含糊原文，没有则返回空串。

    比「原文出现过含糊短语就报错」严格得多，只判两件事同时成立：

    1. **最后一句**用户输入里有含糊短语。多轮对话里用户先说「预算三千左右」、
       后补「最多 4000」，拼起来的原文两句话都在——此时 `max=4000` 是正确的，
       不该判违规。只看最后一句就绕开了这个误伤。
    2. **含糊短语里那个数字本身**被写进了 `min`/`max`。这样「预算 3000 左右」
       被写成 `max=3000` 仍会被拦住，而「预算 3000 左右，最多 4000」不会。

    漏网情形：含糊短语的数字与硬边界数字不同（如「3000 左右」被写成
    `max=4000`）。这种模型错误靠数字比对查不出来，但至少不会把正确结果误判为
    违规——**宁可漏报，不可误报**，误报会让界面直接拒绝一条合法需求。
    """
    vague = _find_vague_budget(_last_utterance(text))
    if not vague:
        return ""
    stated = _numbers_in(vague)
    for key in ("min", "max"):
        value = budget.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if float(value) in stated:
            return vague
    return ""


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _clarification(field: str, question: str = "", reason: str = "") -> dict[str, str]:
    path, default_question, default_reason = CLARIFICATION_TEXT.get(
        field, (field, "", "")
    )
    return {
        "field": path,
        "question": question or default_question,
        "reason": reason or default_reason,
    }


def _blank_result(text: str, status: str, error: str | None, started: float) -> dict[str, Any]:
    """全空的骨架。失败与降级都以它为底，保证字段永远齐全。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "prompt_version": PROMPT_VERSION,
        "status": status,
        "input_text": text,
        "budget": {"reference": None, "min": None, "max": None, "evidence": ""},
        "min_storage": None,
        "target_screen": None,
        "brands": {"include": [], "exclude": []},
        "preferences": [
            {"criterion": criterion, "level": "未提及", "evidence": ""}
            for criterion in CRITERIA
        ],
        "unsupported_needs": [],
        "clarifications": [_clarification(field) for field in FALLBACK_ASKS],
        "meta": {
            "provider": "",
            "model": "",
            "elapsed_ms": _elapsed_ms(started),
            "error": error,
        },
    }


def _failed(text: str, error: str, started: float) -> dict[str, Any]:
    """整条链路失败：结构照常返回，让界面能退回表单模式。"""
    return _blank_result(text, STATUS_FAILED, error, started)


def _raw_items(result: JevResult) -> list[Mapping[str, Any]]:
    """取模型原始回复里的 judgements 列表（归一化之前）。"""
    raw = result.raw or {}
    items = raw.get("judgements") or raw.get("results") or []
    return [item for item in items if isinstance(item, Mapping)]


def _degradations(result: JevResult, text: str) -> list[dict[str, str]]:
    """找出「模型答得不对、已被归一化兜住」的地方，逐条转成澄清项。

    归一化把非法值悄悄改成「未提及」是必要的，但**不能静默**——用户需要
    知道哪些维度其实没抽到，否则会以为系统读懂了。

    没有原始回复可比对时（`raw` 为空，例如确定性替身）直接返回空：
    宁可漏报也不要误报，否则会给每条需求都挂上五条假警报。
    """
    issues: list[dict[str, str]] = []
    raw_items = _raw_items(result)
    if not raw_items:
        return issues

    raw_by: dict[str, Mapping[str, Any]] = {}
    for item in raw_items:
        criterion = item.get("criterion")
        if criterion in CRITERIA:
            raw_by[criterion] = item

    for criterion in CRITERIA:
        label = CRITERION_LABELS[criterion]
        item = raw_by.get(criterion)
        if item is None:
            issues.append(
                _clarification(
                    f"preferences.{criterion}",
                    f"模型没有给出「{label}」的判断，请确认你的偏好。",
                    "模型漏答该维度，已按「未提及」处理",
                )
            )
            continue
        choice = item.get("choice")
        if choice not in CHOICES:
            issues.append(
                _clarification(
                    f"preferences.{criterion}",
                    f"模型对「{label}」给出了无法识别的取值，请确认你的偏好。",
                    f"取值 {choice!r} 非法，已按「未提及」处理",
                )
            )
        evidence = _text(item.get("evidence"))
        if evidence and evidence not in text:
            issues.append(
                _clarification(
                    f"preferences.{criterion}",
                    f"「{label}」的依据「{evidence}」不在你的原话里，请确认。",
                    "模型给出的证据与原文不符，该判断可能不可靠",
                )
            )
    return issues


def _budget_evidence(result: JevResult) -> str:
    raw = result.raw or {}
    evidence = raw.get("evidence")
    if isinstance(evidence, Mapping):
        return _text(evidence.get("budget"))
    return ""


def _hard_evidence(result: JevResult, key: str) -> str:
    raw = result.raw or {}
    evidence = raw.get("evidence")
    if isinstance(evidence, Mapping):
        return _text(evidence.get(key))
    return ""


def _unsupported_items(result: JevResult, text: str) -> list[dict[str, str]]:
    """未覆盖需求，附带触发它的原话片段。

    证据取自原文里真实出现的关键词，不编造。找不到就留空串。
    """
    items: list[dict[str, str]] = []
    for need in result.unsupported:
        evidence = ""
        for words, label in UNSUPPORTED_RULES:
            if label != need:
                continue
            for word in words:
                if word in text:
                    evidence = word
                    break
            break
        items.append({"need": need, "evidence": evidence})
    return items


def _budget_max_item(reference: float | None) -> dict[str, str]:
    """「预算上限是多少」这一问。有参考值就把数字说进去，问得更具体。"""
    if reference is not None:
        return _clarification(
            "budget_max",
            f"你说的是「{reference:g} 左右」，没有明确上限。"
            "最多能接受到多少？",
        )
    return _clarification("budget_max")


def _clarifications(
    result: JevResult,
    *,
    reference: float | None,
    high: float | None = None,
    exclude: list[str],
) -> list[dict[str, str]]:
    """组装澄清清单。顺序即优先级：预算最影响候选集，排最前。"""
    items: list[dict[str, str]] = []

    # **确定性兜底，不依赖模型**：上界没定就必须问。
    #
    # 契约 §4.1 的红线是「含糊预算不自动封顶，由确认页问用户」。实测
    # DeepSeek 对「预算三千左右，想要轻一点的，主要看价格，256G 起步就行」
    # 返回 `asks: []`——它把预算上限记在了 `budget_reference` 上，却没把它
    # 列进 `asks`。若只信模型的 `asks`，界面就不会追问，用户永远拿不到
    # 「最多能接受到多少」这个问题，红线也就落空了。
    #
    # 判定条件与 `bridge._snap_budget` 的 pending 逻辑**保持一致**：
    # `high is None`（原文没给明确上界）→ 问。两处口径必须同源，
    # 否则会出现「结果区提示未确认、对话里却不问」这种自相矛盾。
    if high is None:
        items.append(_budget_max_item(reference))

    for field in result.asks:
        if field == "excluded_brands":
            continue  # 下面按实际品牌给更具体的问题
        if field == "budget_max":
            # 兜底已经放过了，这里再来一次由去重合并；文案同源
            items.append(_budget_max_item(reference))
        else:
            items.append(_clarification(field))

    if exclude:
        names = "、".join(exclude)
        items.append(
            _clarification(
                "excluded_brands",
                f"你排除了{names}。希望我们直接过滤掉这些品牌吗？",
            )
        )

    # 去重：同一 field 只留第一条
    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if item["field"] not in seen:
            seen.add(item["field"])
            deduped.append(item)
    return deduped


def _shape(text: str, result: JevResult, started: float) -> dict[str, Any]:
    hints = result.hard_hints or {}
    reference = _num(hints.get("budget_reference"))
    low = _num(hints.get("budget_min"))
    high = _num(hints.get("budget_max"))
    storage = _num(hints.get("min_storage"))
    screen = _num(hints.get("target_screen"))

    include = [item for item in (hints.get("brands") or ()) if isinstance(item, str)]
    exclude = [item for item in result.excluded_brands if isinstance(item, str)]

    judgements = {item.criterion: item for item in result.judgements}
    preferences = []
    for criterion in CRITERIA:
        item = judgements.get(criterion)
        preferences.append(
            {
                "criterion": criterion,
                "level": item.choice if item is not None else "未提及",
                "evidence": _text(item.evidence) if item is not None else "",
            }
        )

    clarifications = _clarifications(
        result,
        reference=reference,
        high=high,
        exclude=exclude,
    )
    degradations = _degradations(result, text)
    clarifications.extend(degradations)

    payload = _blank_result(text, STATUS_OK, None, started)
    payload["status"] = STATUS_DEGRADED if degradations else STATUS_OK
    payload["budget"] = {
        "reference": reference,
        "min": low,
        "max": high,
        "evidence": _budget_evidence(result),
    }
    payload["min_storage"] = storage
    payload["target_screen"] = screen
    payload["brands"] = {"include": include, "exclude": exclude}
    payload["preferences"] = preferences
    payload["unsupported_needs"] = _unsupported_items(result, text)
    payload["clarifications"] = clarifications
    payload["meta"] = {
        "provider": result.provider,
        "model": result.model_version,
        "elapsed_ms": _elapsed_ms(started),
        "error": None,
    }
    return payload


__all__ = [
    "SCHEMA_VERSION",
    "STATUS_DEGRADED",
    "STATUS_FAILED",
    "STATUS_OK",
    "CRITERION_LABELS",
    "FALLBACK_ASKS",
    "FIELD_PATH_TO_ASK",
    "UNSUPPORTED_VALUES",
    "extract_needs",
    "validate_result",
]
