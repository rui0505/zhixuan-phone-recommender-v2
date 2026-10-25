"""Jev 语义理解适配层。

职责：把「用户中文需求原文」转成内核可用的 `Needs`，并隔离外部服务的
返回格式。对应工作文档第 4 章与第 9 章接口表。

## 能力边界（工作文档第 2 章，必须遵守）

Jev 只负责**有明确选项或等级的判断**，不直接生成任意 JSON 字段、问题
文本或商品事实。程序负责数值、过滤、选择策略与校验。

具体到本模块：
- 每个可评分维度只能取 `CHOICES` 中的值，必须包含「未提及」；
- 「未提及」与「不在意」语义不同，不得互相顶替；
- 供应商返回的 confidence **不是**偏好强度，也不是正确率，只作记录；
- 无法可靠提取的字段不猜，进 `asks` 交给用户确认；
- 数据覆盖不到的维度（续航/影像/真实游戏性能）记为未覆盖需求，
  不用电池容量、像素、刷新率替代。

## 两个实现

- `MockClassifier`：确定性规则实现，**离线可用**，用于先打通接口。
  它的评测得分**不构成语义理解能力证据**（规则是参照评测集写的，
  属评估泄漏）。真实效果必须接真实服务后重测。
- `HttpClassifier`：真实服务接入点。凭据从环境变量读取，不落盘、
  不打印；超时与重试按工作文档第 9 章（10 秒、最多重试 1 次），
  失败时由调用方回退到表单/固定问卷。
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

from schemas import (
    CHOICES,
    CRITERIA,
    EXTRACTION_CRITERIA,
    PERFORMANCE_CRITERIA,
    HardConstraints,
    Needs,
    PreferenceItem,
    SchemaError,
    VERDICT_EXTRACTED,
    VERDICT_INFERRED,
    VERDICT_UNKNOWN,
)

# --- 强度口径（与 tests/needs_testset.json 的 _strength_convention 一致）----
#
#   非常重要 含最高级或强制表达：最重要、最轻、必须、一定要
#   重要     明确方向性偏好：越…越好、要轻、越便宜越好
#   一般     弱方向提示或显式低强度：别太重、轻一点、一般就行
#   不在意   用户明确否定该维度：无所谓、不重要、不太在意
#
# 同句式必须给同一强度，不得因措辞微调而升降级。

# 品牌别名 → 数据表品牌标签（数据表的品牌由产品名前缀判定）。
BRAND_ALIASES: dict[str, str] = {
    "iPhone": "苹果",
    "苹果": "苹果",
    "红米": "Redmi",
    "Redmi": "Redmi",
    "小米": "小米",
    "荣耀": "荣耀",
    "华为": "华为",
    "三星": "三星",
    "vivo": "vivo",
    "OPPO": "OPPO",
    "一加": "一加",
    "iQOO": "iQOO",
    "realme": "realme",
}

NEGATION_MARKERS: tuple[str, ...] = ("不要", "不想要", "不考虑", "排除", "除了")

# 中文数字（口语预算写法，如「四五千」「三千多」）。
CN_DIGITS: dict[str, int] = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}

# 含糊量词：出现即不得把数字当作明确上限（工作文档第 4 章第一条边界）。
VAGUE_MARKERS: tuple[str, ...] = ("左右", "多", "上下", "来", "把", "大约", "差不多")

# 数据无法覆盖的需求 → 关键词。漏标会导致虚假结论，宁可保守多标。
UNSUPPORTED_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("拍照", "影像", "相机好"), "影像"),
    (("续航", "电池耐用", "待机"), "续航"),
    (("打游戏", "游戏性能", "不卡", "王者荣耀", "和平精英", "原神", "崩坏：星穹铁道", "绝区零"), "真实游戏性能"),
    (("分辨率", "清晰度"), "分辨率"),
    (("单手", "手感", "拿得住"), "机身尺寸手感"),
    (("性价比",), "处理器性能"),
    (("信号",), "信号质量"),
)

# 必须回问的字段：无法可靠提取且会显著改变候选集的项。
ASK_BUDGET_MAX = "budget_max"
ASK_MIN_STORAGE = "min_storage"
ASK_TARGET_SCREEN = "target_screen"
ASK_EXCLUDED_BRANDS = "excluded_brands"

GAME_NAMES = ('王者荣耀', '和平精英', '原神', '崩坏：星穹铁道', '绝区零', '三角洲行动')
PERFORMANCE_EVIDENCE_PATTERNS = {
    'photo': r'拍照|影像|相机|摄影',
    'battery': r'续航|电池耐用|待机|使用时长',
    'game': r'游戏|爱打|爱玩|喜欢玩|只玩|平均帧率|' + '|'.join(GAME_NAMES),
}


def performance_evidence_valid(criterion: str, evidence: str, source: str) -> bool:
    """A performance grade requires source text that actually concerns that dimension."""
    return bool(evidence and evidence in source
                and re.search(PERFORMANCE_EVIDENCE_PATTERNS[criterion], evidence))


@dataclass(frozen=True)
class CriterionJudgement:
    """单个维度的判断结果，带原文证据。"""

    criterion: str
    choice: str
    evidence: str = ""
    verdict: str = VERDICT_UNKNOWN
    confidence: float | None = None

    def __post_init__(self) -> None:
        if self.criterion not in EXTRACTION_CRITERIA:
            raise SchemaError(f"未知维度：{self.criterion!r}")
        if self.choice not in CHOICES:
            raise SchemaError(f"{self.criterion} 的 choice 非法：{self.choice!r}")

    def to_preference(self, *, confirmed: bool = False) -> PreferenceItem:
        """转成契约层的 PreferenceItem。

        confidence 原样带入仅作记录——工作文档明确它不等于偏好强度。
        """
        return PreferenceItem(
            criterion=self.criterion,
            choice=self.choice,
            raw_text=self.evidence,
            verdict=self.verdict,
            confirmed=confirmed,
            confidence=self.confidence,
        )


@dataclass(frozen=True)
class JevResult:
    """语义理解的完整结果。"""

    judgements: tuple[CriterionJudgement, ...] = ()
    hard_hints: Mapping[str, Any] = field(default_factory=dict)
    excluded_brands: tuple[str, ...] = ()
    asks: tuple[str, ...] = ()
    unsupported: tuple[str, ...] = ()
    provider: str = "unknown"
    model_version: str = ""
    raw: Mapping[str, Any] | None = None

    def judgement_for(self, criterion: str) -> CriterionJudgement | None:
        for item in self.judgements:
            if item.criterion == criterion:
                return item
        return None


class PreferenceClassifier(Protocol):
    """分类器契约。内核只依赖这个 Protocol。"""

    name: str

    def classify(self, text: str) -> JevResult:
        ...


# --- 确定性模拟实现 ---------------------------------------------------------


def _is_storage_context(text: str, start: int, end: int, *, window: int = 8) -> bool:
    """判断 text[start:end] 处的数字是不是存储容量，而不是预算。

    「存储 512 以上」和「512G 以上」都指存储门槛。不做这个区分的话，
    存储门槛会被误读成预算下界，把候选集整个筛空。
    """
    before = text[max(0, start - window) : start]
    if any(token in before for token in ("存储", "容量", "内存", "空间")):
        return True
    return text[end : end + window].lstrip().upper().startswith("G")


class MockClassifier:
    """离线确定性规则实现，用于先打通接口。

    ⚠️ 规则是参照 `tests/needs_testset.json` 调整出来的，因此在那个评测集上
    得分偏高属于评估泄漏，**不构成语义理解能力证据**。真实效果必须接真实
    服务后重测（工作文档第 9 章：先用模拟服务打通接口，再接真实 API）。
    """

    name = "mock"

    def classify(self, text: str) -> JevResult:
        source = text or ""
        judgements = [self._judge(criterion, source) for criterion in EXTRACTION_CRITERIA]
        judgements = self._apply_blanket(source, judgements)

        hard, asks = self._hard_constraints(source)
        excluded = self._excluded_brands(source)
        if excluded:
            # 排除式限定现有筛选表达不了，必须让用户确认如何处理
            asks.append(ASK_EXCLUDED_BRANDS)
        unsupported = self._unsupported(source)
        ordered_asks = sorted(set(asks))

        # 填 `raw`：真实供应商会返回原始报文，替身也照做，这样下游
        # （证据校验、降级检测）在离线测试里也能被完整走到。
        raw = {
            "judgements": [
                {
                    "criterion": item.criterion,
                    "choice": item.choice,
                    "evidence": item.evidence,
                    "verdict": item.verdict,
                }
                for item in judgements
            ],
            "hard_constraints": dict(hard),
            "evidence": self._evidence(source, hard),
            "excluded_brands": list(excluded),
            "asks": ordered_asks,
            "unsupported_needs": list(unsupported),
        }

        return JevResult(
            judgements=tuple(judgements),
            hard_hints=hard,
            excluded_brands=excluded,
            asks=tuple(ordered_asks),
            unsupported=unsupported,
            provider=self.name,
            model_version="mock-v1",
            raw=raw,
        )

    @staticmethod
    def _evidence(source: str, hard: Mapping[str, Any]) -> dict[str, str]:
        """给硬约束找一段原话片段。

        替身只是把数字在原句里定位出来，不做语义判断——真实供应商由模型
        直接给出 evidence。目的是让离线测试也能覆盖契约里的证据字段。
        """

        def snippet(value: Any) -> str:
            if value is None:
                return ""
            for token in (f"{value:g}", str(int(value))):
                index = source.find(token)
                if index >= 0:
                    return source[index : index + len(token)]
            return ""

        brands = hard.get("brands") or []
        return {
            "budget": snippet(
                hard.get("budget_reference")
                or hard.get("budget_max")
                or hard.get("budget_min")
            ),
            "min_storage": snippet(hard.get("min_storage")),
            "target_screen": snippet(hard.get("target_screen")),
            "brands": brands[0] if brands else "",
        }

    @staticmethod
    def _apply_blanket(
        text: str, judgements: Sequence[CriterionJudgement]
    ) -> list[CriterionJudgement]:
        """「其他都一般」是整体表态，作用于尚未点名的可评分维度。

        与 CN-16 对应；不得覆盖用户已单独表态的维度。
        """
        if "其他都一般" not in text and "其余都一般" not in text:
            return list(judgements)
        patched: list[CriterionJudgement] = []
        for item in judgements:
            if item.choice == "未提及" and item.criterion in CRITERIA:
                patched.append(
                    CriterionJudgement(
                        criterion=item.criterion,
                        choice="一般",
                        evidence="其他都一般",
                        verdict=VERDICT_EXTRACTED,
                    )
                )
            else:
                patched.append(item)
        return patched

    # --- 各维度判断 -----------------------------------------------------

    def _judge(self, criterion: str, text: str) -> CriterionJudgement:
        handler = getattr(self, f"_judge_{criterion}")
        return handler(text)

    @staticmethod
    def _pick(
        criterion: str,
        text: str,
        table: Sequence[tuple[tuple[str, ...], str]],
    ) -> CriterionJudgement:
        """按顺序匹配关键词表，首个命中即用；未命中记「未提及」。"""
        for words, choice in table:
            for word in words:
                if word in text:
                    return CriterionJudgement(
                        criterion=criterion,
                        choice=choice,
                        evidence=word,
                        verdict=VERDICT_EXTRACTED,
                    )
        return CriterionJudgement(
            criterion=criterion,
            choice="未提及",
            verdict=VERDICT_UNKNOWN,
        )

    def _judge_price(self, text: str) -> CriterionJudgement:
        return self._pick("price", text, (
            (("价格无所谓", "价格不太在意", "价格不太在乎", "价格不重要"), "不在意"),
            (("价格最重要", "预算最重要", "最看重价格"), "非常重要"),
            (("越便宜越好", "主要看价格", "价格优先"), "重要"),
            (("性价比",), "重要"),
        ))

    def _judge_weight(self, text: str) -> CriterionJudgement:
        return self._pick("weight", text, (
            (("最轻", "必须轻", "一定要轻"), "非常重要"),
            (("越轻越好", "重量要轻", "轻一些好", "轻一点好"), "重要"),
            (("别太重", "轻一点", "稍微轻", "不要太重"), "一般"),
        ))

    def _judge_storage(self, text: str) -> CriterionJudgement:
        # 门槛（256G 起步）与偏好（越大越好）分开判断：这里只判偏好。
        # 注意「越大越好」必须带存储语境，否则会误吞 CN-10 那种指屏幕尺寸的
        # 「越大越好」——同一措辞在不同语境下指向不同维度。
        return self._pick("storage", text, (
            (("存储最重要", "容量最重要"), "非常重要"),
            (("存储越大越好", "容量越大越好", "存储越大", "容量越大"), "重要"),
        ))

    def _judge_refresh(self, text: str) -> CriterionJudgement:
        return self._pick("refresh", text, (
            (("刷新率要高", "高刷"), "非常重要"),
            (("刷新率不重要", "刷新率无所谓", "刷新率不太在意"), "不在意"),
            (("刷新率一般", "刷新率够用"), "一般"),
        ))

    def _judge_screen(self, text: str) -> CriterionJudgement:
        if "小屏" in text:
            return CriterionJudgement(
                criterion="screen", choice="非常重要",
                evidence="小屏", verdict=VERDICT_EXTRACTED,
            )
        target = self._screen_target(text)
        if target is not None:
            # evidence 必须是**原文里的片段**，不能重新拼装。
            # 写成 f"{target:g}英寸" 会丢掉原文的数字与「英寸」之间的空格，
            # `_degradations` 用 `evidence not in text` 校验时会判定「证据与
            # 原文不符」，给每条尺寸需求都挂上一条假追问。
            return CriterionJudgement(
                criterion="screen", choice="重要",
                evidence=self._screen_evidence(text), verdict=VERDICT_EXTRACTED,
            )
        return CriterionJudgement(
            criterion="screen", choice="未提及", verdict=VERDICT_UNKNOWN,
        )

    def _judge_performance(self, criterion: str, text: str) -> CriterionJudgement:
        # Last clause that names this preference wins; unrelated later turns do not erase it.
        relevant = [part.strip() for part in re.split(r'[，。；,;\n]', text)
                    if re.search(PERFORMANCE_EVIDENCE_PATTERNS[criterion], part)]
        if not relevant:
            return CriterionJudgement(criterion, '未提及')
        evidence = re.sub(r'^\s*\d+[.、]\s*', '', relevant[-1])
        if re.search(r'不在意|不重要|无所谓|不太在意|不玩|不打游戏', evidence):
            choice, verdict = '不在意', VERDICT_EXTRACTED
        elif re.search(r'最重要|最看重|必须|一定要|优先级最高', evidence):
            choice, verdict = '非常重要', VERDICT_EXTRACTED
        elif re.search(r'重要|优先|越.+越好|要好|要强', evidence):
            choice, verdict = '重要', VERDICT_EXTRACTED
        elif re.search(r'一般|够用|好一点|好点', evidence):
            choice, verdict = '一般', VERDICT_EXTRACTED
        else:
            choice, verdict = '重要', VERDICT_INFERRED
        return CriterionJudgement(criterion, choice, evidence, verdict)

    def _judge_game(self, text: str) -> CriterionJudgement:
        return self._judge_performance('game', text)

    def _judge_photo(self, text: str) -> CriterionJudgement:
        return self._judge_performance('photo', text)

    def _judge_battery(self, text: str) -> CriterionJudgement:
        return self._judge_performance('battery', text)

    # --- 硬约束 ---------------------------------------------------------

    def _hard_constraints(self, text: str) -> tuple[dict[str, Any], list[str]]:
        asks: list[str] = []
        low, high, reference = self._budget_window(text)
        storage = self._storage_floor(text)
        screen = self._screen_target(text)
        brands = self._brands(text)

        if high is None:
            # 含糊预算（「3000 左右」）或未提预算 → 不设上限，必须回问
            asks.append(ASK_BUDGET_MAX)
        if storage is None and high is None:
            asks.append(ASK_MIN_STORAGE)
        if screen is None and any(
            word in text for word in ("随便", "什么都行", "屏幕要", "屏幕好", "屏幕大")
        ):
            asks.append(ASK_TARGET_SCREEN)

        return (
            {
                "budget_min": low,
                "budget_max": high,
                # 参考值，仅供界面显示与确认页引用，永不参与筛选
                "budget_reference": reference,
                "min_storage": storage,
                "brands": brands,
                "target_screen": screen,
            },
            asks,
        )

    @staticmethod
    def _budget_window(
        text: str,
    ) -> tuple[float | None, float | None, float | None]:
        """返回 (min, max, reference)。

        只有三种表达算**明确硬约束**（写进 min/max，参与筛选）：

        - 「N 到 M」区间
        - 「N 以内 / 以下 / 之内」，「不超过 / 最多 N」
        - 「N 以上 / 不低于 N」

        其余一律只填 `reference`，min/max 留空并进澄清清单。含糊预算不自动封顶：
        「2000 左右」「三千多」「四五千」「预算 5000」都不能当成上限，
        但**数字必须留下来**给用户看，否则「2000 左右」的信息会整个丢失。
        """
        # 1. 明确区间：6000 到 9000 / 2000-3000。有区间就不填 reference，
        #    再编一个中值属于无中生有。
        match = re.search(r"(\d+)\s*(?:到|至|-|~)\s*(\d+)", text)
        if match:
            first, second = float(match.group(1)), float(match.group(2))
            return (min(first, second), max(first, second), None)

        # 2. 明确上限（量词在数字前）：不超过 6000 / 最多 5000 / 不高于 4000
        match = re.search(r"(?:不超过|最多|不高于|最高|顶多)\s*(\d+)", text)
        if match:
            cap = float(match.group(1))
            return (None, cap, cap)

        # 3. 明确上限（量词在数字后）：3000 以内 / 8000 以下 / 6000 之内
        match = re.search(r"(\d+)\s*(?:元)?\s*(?:以内|以下|之内)", text)
        if match:
            cap = float(match.group(1))
            return (None, cap, cap)

        # 4. 明确下界：不低于 3000 / 3000 以上。
        #    必须逐个筛掉存储语境——「存储 512 以上」是门槛不是预算。
        match = re.search(r"(?:不低于|至少)\s*(\d+)", text)
        if match:
            floor = float(match.group(1))
            return (floor, None, floor)
        for match in re.finditer(r"(\d+)\s*(?:元)?\s*(?:以上|起步)", text):
            if not _is_storage_context(text, match.start(1), match.end(1)):
                floor = float(match.group(1))
                return (floor, None, floor)

        # --- 以下只填 reference，不设任何硬约束 --------------------------

        # 5. 中文数字：四五千 → 4500（取中值）；三千多 / 四千左右 → 3000 / 4000
        match = re.search(
            r"([零一二两三四五六七八九])([零一二两三四五六七八九])?千", text
        )
        if match:
            first = CN_DIGITS[match.group(1)]
            second = CN_DIGITS[match.group(2)] if match.group(2) else None
            if second is not None:
                low, high = min(first, second), max(first, second)
                return (None, None, (low + high) / 2 * 1000)
            return (None, None, float(first * 1000))

        # 6. 阿拉伯数字，**必须带预算语境**，否则会误吃「256G」「6.4 英寸」
        for pattern in (
            r"预算\s*(\d+)",
            r"价位\s*(\d+)",
            r"价格\s*(\d+)",
            r"(\d+)\s*元",
            r"(\d+)\s*(?:左右|上下|差不多)",
        ):
            match = re.search(pattern, text)
            if match:
                return (None, None, float(match.group(1)))

        return (None, None, None)

    @staticmethod
    def _storage_floor(text: str) -> float | None:
        """存储门槛（与存储偏好分开判断）。"""
        # 单位换算表：数据表用 GB 存，`engine.load_data` 也把 TB 行乘 1024。
        # 抽取层必须做同样的换算，否则用户说「要 1TB 的」会被读成「没提存储」，
        # 门槛丢失 → 界面反而去追问存储，而用户已经答过了。
        units = {"G": 1.0, "GB": 1.0, "T": 1024.0, "TB": 1024.0}
        # 单位在**有明确语境**时可以省略（「存储 512 以上」「最少 512」），
        # 但光秃秃的「512 以上」不行——那是预算下界。所以：
        # 带「存储 / 容量 / 最少 / 尽量」的模式里单位可选，其余必须带单位。
        patterns = (
            (r"(\d+(?:\.\d+)?)\s*(TB|GB|T|G)\s*(?:以上|起步|起|的|就行|也可以)", True),
            (r"最少\s*(\d+(?:\.\d+)?)\s*(TB|GB|T|G)?", False),
            (r"尽量\s*(\d+(?:\.\d+)?)\s*(TB|GB|T|G)?", False),
            (r"(?:存储|容量)\s*(\d+(?:\.\d+)?)\s*(TB|GB|T|G)?\s*(?:起步|起|以上|的)?", False),
        )
        for pattern, unit_required in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if not match:
                continue
            unit = (match.group(2) or "").upper()
            if unit_required and not unit:
                continue
            return float(match.group(1)) * units.get(unit, 1.0)
        return None

    @staticmethod
    def _screen_target(text: str) -> float | None:
        match = re.search(r"(\d+(?:\.\d+)?)\s*英寸", text)
        return float(match.group(1)) if match else None

    @staticmethod
    def _screen_evidence(text: str) -> str:
        """取原文里那段尺寸表达，供 `evidence` 使用。

        与 `_screen_target` 用同一套模式，保证「判出来的数字」和「写下的证据」
        指向同一处原文；找不到时返回空串（调用方会退化成「未提及」路径）。
        """
        match = re.search(r"\d+(?:\.\d+)?\s*英寸", text)
        return match.group(0) if match else ""

    @staticmethod
    def _brands(text: str) -> list[str]:
        """品牌白名单。排除式表达（不要华为）不转成白名单。"""
        text = text.replace('王者荣耀', '')
        names: list[str] = []
        for alias, label in BRAND_ALIASES.items():
            if alias not in text or label in names:
                continue
            if any(marker + alias in text for marker in NEGATION_MARKERS):
                continue
            names.append(label)
        return names

    @staticmethod
    def _excluded_brands(text: str) -> tuple[str, ...]:
        """被明确排除的品牌。现有筛选只支持白名单，故交给用户确认。"""
        found: list[str] = []
        for alias, label in BRAND_ALIASES.items():
            if any(marker + alias in text for marker in NEGATION_MARKERS):
                if label not in found:
                    found.append(label)
        return tuple(found)

    @staticmethod
    def _unsupported(text: str) -> tuple[str, ...]:
        found: list[str] = []
        for words, label in UNSUPPORTED_RULES:
            if any(word in text for word in words) and label not in found:
                found.append(label)
        return tuple(found)


# --- 供应商返回归一化（HttpClassifier 与外部 LLM 共用） ---------------------


def normalise_response(
    body: Mapping[str, Any],
    *,
    provider: str,
    model_version: str = "",
    source_text: str | None = None,
) -> JevResult:
    """把供应商返回格式归一化。

    供应商字段名与取值不进内核：这里做映射与校验，非法值一律降级为
    「未提及」并标记 unknown，不让脏数据进入排序链路。

    `source_text` 传入原始需求文本时，额外校验 `evidence` 是否真的出现在
    原文里：不在则把 verdict 从 extracted 降级为 inferred（模型可能编造
    证据），但**不丢弃 choice**——choice 是模型判断，evidence 只是佐证。
    新性能偏好必须有对应维度的原文证据；无依据时退回未提及。
    不传 source_text 则跳过原文包含校验，保持既有调用方行为不变；
    对外 extraction 入口仍会在 shaping 时再次核对性能证据。
    """
    judgements: list[CriterionJudgement] = []
    raw_items = body.get("judgements") or body.get("results") or []
    by_criterion: dict[str, Mapping[str, Any]] = {}
    for item in raw_items:
        if isinstance(item, Mapping) and item.get("criterion") in EXTRACTION_CRITERIA:
            by_criterion[item["criterion"]] = item

    for criterion in EXTRACTION_CRITERIA:
        item = by_criterion.get(criterion)
        if item is None:
            judgements.append(
                CriterionJudgement(
                    criterion=criterion, choice="未提及", verdict=VERDICT_UNKNOWN
                )
            )
            continue
        evidence = str(item.get("evidence", "") or "")
        choice = item.get("choice")
        if choice not in CHOICES:
            # 非法选项不猜，退回未提及
            judgements.append(
                CriterionJudgement(
                    criterion=criterion,
                    choice="未提及",
                    evidence=evidence,
                    verdict=VERDICT_UNKNOWN,
                )
            )
            continue
        verdict = item.get("verdict", VERDICT_INFERRED)
        if verdict not in (VERDICT_EXTRACTED, VERDICT_INFERRED, VERDICT_UNKNOWN):
            verdict = VERDICT_UNKNOWN
        if (criterion in PERFORMANCE_CRITERIA and choice != '未提及'
                and source_text is not None and not performance_evidence_valid(criterion, evidence, source_text)):
            # Performance intent needs actual source, even if the provider labels it inferred.
            choice, verdict = '未提及', VERDICT_UNKNOWN
        if (criterion in PERFORMANCE_CRITERIA and choice not in ('未提及', '不在意')
                and evidence and not re.search(r'重要|最看重|必须|一定要|优先|越.+越好|要好|要强|一般|够用|好一点|好点', evidence)):
            verdict = VERDICT_INFERRED
            if choice == '非常重要':
                choice = '重要'
        if (
            source_text is not None
            and verdict == VERDICT_EXTRACTED
            and evidence
            and evidence not in source_text
        ):
            # 声称来自原文却对不上 → 证据是编的，降级为推断，交由用户确认
            verdict = VERDICT_INFERRED
        confidence = item.get("confidence")
        judgements.append(
            CriterionJudgement(
                criterion=criterion,
                choice=choice,
                evidence=evidence,
                verdict=verdict,
                confidence=float(confidence) if confidence is not None else None,
            )
        )

    hard = body.get("hard_constraints") or {}
    return JevResult(
        judgements=tuple(judgements),
        hard_hints=dict(hard) if isinstance(hard, Mapping) else {},
        excluded_brands=tuple(body.get("excluded_brands") or ()),
        asks=tuple(body.get("asks") or ()),
        unsupported=tuple(body.get("unsupported_needs") or ()),
        provider=provider,
        model_version=body.get("model_version", model_version),
        raw=body,
    )


# --- 真实服务接入点 ---------------------------------------------------------


class JevUnavailable(RuntimeError):
    """真实服务不可用。调用方应回退到表单 / 固定问卷路线。"""


class HttpClassifier:
    """真实 Jev 服务接入点。

    工作文档第 9 章：请求超时先设 10 秒、最多重试 1 次，失败使用回退路线；
    凭据放在环境变量，不记录密钥。

    注意：本类只负责「取回并归一化」，不负责业务判断。真实服务的
    延迟、中文需求识别率、调用费用需实测。
    """

    name = "http"

    def __init__(
        self,
        endpoint: str | None = None,
        *,
        api_key_env: str = "JEV_API_KEY",
        model_version: str = "",
        timeout: float = 10.0,
        retries: int = 1,
    ) -> None:
        self.endpoint = endpoint or os.environ.get("JEV_ENDPOINT", "")
        self.api_key_env = api_key_env
        self.model_version = model_version
        self.timeout = timeout
        self.retries = max(0, retries)
        if not self.endpoint:
            raise JevUnavailable(
                "未配置 JEV_ENDPOINT。请设置环境变量后重试；"
                "未接入真实服务时应使用 MockClassifier 或表单路线。"
            )

    def classify(self, text: str) -> JevResult:
        key = os.environ.get(self.api_key_env, "")
        if not key:
            raise JevUnavailable(
                f"环境变量 {self.api_key_env} 未设置。密钥不写入代码或日志。"
            )
        payload = json.dumps(
            {"text": text, "criteria": list(EXTRACTION_CRITERIA), "choices": list(CHOICES)}
        ).encode("utf-8")

        last_error: Exception | None = None
        for _ in range(self.retries + 1):
            request = urllib.request.Request(self.endpoint, data=payload, method="POST")
            # 令牌只存在于请求头，不打印、不落盘
            request.add_header("Authorization", f"Bearer {key}")
            request.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = json.loads(response.read().decode("utf-8"))
                return normalise_response(body, provider=self.name,
                                          model_version=self.model_version, source_text=text)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
                last_error = error
        raise JevUnavailable(
            f"Jev 服务调用失败（已重试 {self.retries} 次）：{type(last_error).__name__}"
        )

    def _normalise(self, body: Mapping[str, Any]) -> JevResult:
        """委托给模块级 normalise_response。

        与外部 LLM 实现共用同一套降级规则，避免「非法 choice 怎么处理」
        在两个 provider 里各写一份、日后分叉。
        """
        return normalise_response(
            body, provider=self.name, model_version=self.model_version
        )


# --- 对内接口 ---------------------------------------------------------------


def classify_preferences(
    text: str,
    *,
    classifier: PreferenceClassifier | None = None,
) -> JevResult:
    """工作文档第 9 章接口：`classify_preferences`。

    未指定分类器时用 `MockClassifier`（离线、确定性），便于先打通链路。
    """
    return (classifier or MockClassifier()).classify(text)


def parse_needs(
    text: str,
    *,
    classifier: PreferenceClassifier | None = None,
    confirmed: bool = False,
) -> Needs:
    """工作文档第 4 章接口：`parse_needs(text, schema)`。

    返回偏好分类、证据片段与待确认字段。**不猜**：
    - 含糊预算不设上限，进 `needs_confirmation`；
    - 排除式品牌不转白名单，进 `needs_confirmation`；
    - 未提及的维度保留中性先验，且标记为系统默认。

    `confirmed=True` 仅在用户已逐项确认后使用；默认 False，表示
    结果仍需用户过一遍确认页。
    """
    result = classify_preferences(text, classifier=classifier)

    hints = result.hard_hints
    hard = HardConstraints(
        budget_min=_opt_float(hints.get("budget_min")),
        budget_max=_opt_float(hints.get("budget_max")),
        min_storage=_opt_float(hints.get("min_storage")),
        brands=tuple(hints.get("brands") or ()),
        target_screen=_opt_float(hints.get("target_screen")),
        budget_reference=_opt_float(hints.get("budget_reference")),
    )

    preferences = [
        item.to_preference(confirmed=confirmed) for item in result.judgements
        if item.criterion in CRITERIA
    ]

    # 排除式品牌用带值形式（便于确认页直接展示「排除了谁」），
    # 替换掉分类器给出的裸字段名，避免同一件事在清单里出现两次。
    pending = [item for item in result.asks if item != ASK_EXCLUDED_BRANDS]
    if result.excluded_brands:
        pending.append(f"{ASK_EXCLUDED_BRANDS}=" + "/".join(result.excluded_brands))

    return Needs(
        hard=hard,
        preferences=preferences,
        unsupported_needs=list(result.unsupported),
        raw_text=text,
        needs_confirmation=pending,
    )


def _opt_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "BRAND_ALIASES",
    "NEGATION_MARKERS",
    "VAGUE_MARKERS",
    "ASK_BUDGET_MAX",
    "ASK_MIN_STORAGE",
    "ASK_TARGET_SCREEN",
    "ASK_EXCLUDED_BRANDS",
    "CriterionJudgement",
    "JevResult",
    "PreferenceClassifier",
    "MockClassifier",
    "HttpClassifier",
    "JevUnavailable",
    "normalise_response",
    "classify_preferences",
    "parse_needs",
]
