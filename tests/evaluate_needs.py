"""中文需求理解评测脚本。

对应实施工作文档第 11 章 B 的当天任务：
「写 20 条中文需求及人工标准答案，明确未知情况」。

用法（在 phone-demo 目录）：
    python tests/evaluate_needs.py                 # 用内置规则基线自测
    python tests/evaluate_needs.py --verbose       # 逐例打印差异

脚本本身不实现语义理解，只做评测：把理解层的输出与人工标准答案逐字段
比对，报告通过率，并单独列出被误判的边界例。

评测口径（工作文档第 10 章「防止评估泄漏」「指标定义」的精神）：
- 硬约束：只比对「是否该被提取」，null 与缺失视为同一件事；
- 偏好 Choice：exact match，不做部分给分；
- must_ask：标准答案要求回问的字段，若理解层未回问则记为漏问；
- unsupported_needs：漏标比多标更严重（多标只是保守，漏标会生成虚假结论），
  因此分别报告「漏标」与「多标」两类错误。
"""

from __future__ import annotations

import argparse
import json
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from schemas import CHOICES, CRITERIA, SchemaError, validate_weights  # noqa: E402

TESTSET = Path(__file__).resolve().parent / "needs_testset.json"

HARD_FIELDS = (
    "budget_min",
    "budget_max",
    "budget_reference",
    "min_storage",
    "brands",
    "target_screen",
)

# 需要按数值比对的字段。budget_reference 是参考值，同样按数值比对。
NUMERIC_HARD_FIELDS = (
    "budget_min",
    "budget_max",
    "budget_reference",
    "min_storage",
    "target_screen",
)


@dataclass
class CaseResult:
    """单例评测结果。"""

    case_id: str
    boundary: str
    errors: list[str] = field(default_factory=list)
    missing_unsupported: list[str] = field(default_factory=list)
    extra_unsupported: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not (self.errors or self.missing_unsupported or self.extra_unsupported)



class RuleBaseline:
    """最小规则基线，仅用于验证评测脚本自身可用。

    这是刻意做得简陋的对照实现：用关键词匹配和保守默认值，目的是让
    评测脚本有一个可运行的输入，并暴露评测口径是否正确。

    它不是交付的需求理解层——真实实现应由 providers/jev.py 调用 Jev
    完成分类，把不确定字段交给用户确认。基线在若干边界上失败是预期的，
    这些失败恰好证明评测集能区分「按规则工作」与「看起来合理」。
    """

    # 具体数值阈值。工作文档第 4 章：无法可靠提取时直接询问，
    # 不得擅自替用户设定。
    BRAND_PREFIXES = (
        "Redmi", "小米", "荣耀", "华为", "三星", "vivo", "OPPO", "一加", "iQOO", "realme",
    )
    NEGATED_MARKERS = ("不要", "不想要", "不考虑", "排除", "除了")

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.asks: list[str] = []

    def parse(self, text: str) -> dict[str, Any]:
        self.reset()
        hard: dict[str, Any] = {name: None for name in HARD_FIELDS}
        hard["brands"] = []
        prefs: dict[str, str] = {key: "未提及" for key in CRITERIA}

        screen_target = self._screen_target(text)
        low, high = self._budget_window(text)
        storage_floor = self._storage_floor(text)

        hard["budget_min"], hard["budget_max"] = low, high
        hard["min_storage"] = storage_floor
        hard["target_screen"] = screen_target
        hard["brands"] = self._brands(text)

        self._apply_price(text, prefs)
        self._apply_weight(text, prefs)
        self._apply_storage(text, prefs)
        self._apply_refresh(text, prefs)
        self._apply_screen(text, prefs, screen_target)
        self._apply_blanket(text, prefs)
        self._collect_asks(text, hard)

        return {
            "hard_constraints": hard,
            "expectations": prefs,
            "unsupported_needs": self._unsupported(text),
            "must_ask": sorted(set(self.asks)),
        }

    # --- 硬约束 ---------------------------------------------------------

    # 品牌别名 → 数据表品牌标签。数据表的品牌由产品名前缀判定，
    # 因此「红米」要归到 Redmi、「苹果/iPhone」归到苹果。
    BRAND_ALIASES = {
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
    NEGATED_MARKERS = ("不要", "不想要", "不考虑", "排除", "除了")

    @classmethod
    def _brands(cls, text: str) -> list[str]:
        """品牌白名单。排除式表达（不要华为）不转成白名单。"""
        names: list[str] = []
        for alias, label in cls.BRAND_ALIASES.items():
            if alias not in text or label in names:
                continue
            if any(marker + alias in text for marker in cls.NEGATED_MARKERS):
                continue  # 排除条件交给确认页，不静默忽略也不当白名单
            names.append(label)
        return names

    def _collect_asks(self, text: str, hard: dict[str, Any]) -> None:
        """列出必须回问用户的字段。"""
        asks: list[str] = []

        if hard["budget_max"] is None:
            asks.append("budget_max")
        if hard["min_storage"] is None and hard["budget_max"] is None:
            asks.append("min_storage")
        if hard["target_screen"] is None and any(
            word in text for word in ("随便", "什么都行", "屏幕要", "屏幕好")
        ):
            asks.append("target_screen")
        # 排除式品牌限定：现有筛选只有白名单，必须让用户确认如何处理
        if any(
            marker + alias in text
            for marker in self.NEGATED_MARKERS
            for alias in self.BRAND_ALIASES
        ):
            asks.append("excluded_brands")

        self.asks.extend(asks)

    # --- 偏好 -----------------------------------------------------------

    @staticmethod
    def _apply_price(text: str, prefs: dict[str, str]) -> None:
        if any(word in text for word in ("价格无所谓", "价格不太在意", "价格不太在乎")):
            prefs["price"] = "不在意"
        elif "价格最重要" in text:
            prefs["price"] = "非常重要"
        elif "越便宜越好" in text:
            prefs["price"] = "重要"
        elif "主要看价格" in text or "性价比" in text:
            prefs["price"] = "重要"

    @staticmethod
    def _apply_weight(text: str, prefs: dict[str, str]) -> None:
        # 强度口径（与评测集统一）：
        #   「最轻 / 必须轻」等最高级 → 非常重要
        #   「越轻越好 / 重量要轻」等明确方向 → 重要
        #   「别太重 / 轻一点」等方向性提示 → 一般
        if "最轻" in text:
            prefs["weight"] = "非常重要"
        elif "越轻越好" in text or "重量要轻" in text:
            prefs["weight"] = "重要"
        elif "别太重" in text or "轻一点" in text:
            prefs["weight"] = "一般"

    @staticmethod
    def _apply_storage(text: str, prefs: dict[str, str]) -> None:
        # 门槛（256G 起步）与偏好（越大越好）分开判断
        if "存储越大越好" in text:
            prefs["storage"] = "重要"

    @staticmethod
    def _apply_refresh(text: str, prefs: dict[str, str]) -> None:
        if "刷新率要高" in text:
            prefs["refresh"] = "非常重要"
        elif "刷新率不重要" in text:
            prefs["refresh"] = "不在意"
        elif "刷新率一般" in text:
            prefs["refresh"] = "一般"

    @staticmethod
    def _apply_screen(text: str, prefs: dict[str, str], target: float | None) -> None:
        if "小屏" in text:
            prefs["screen"] = "非常重要"
        elif target is not None:
            prefs["screen"] = "重要"

    @staticmethod
    def _apply_blanket(text: str, prefs: dict[str, str]) -> None:
        """「其他都一般」作用于尚未点名的可评分维度。"""
        if "其他都一般" not in text:
            return
        for key in ("weight", "storage", "refresh", "screen"):
            if prefs[key] == "未提及":
                prefs[key] = "一般"

    # --- 未覆盖需求 -----------------------------------------------------

    @staticmethod
    def _unsupported(text: str) -> list[str]:
        """数据无法覆盖的需求。漏标会导致虚假结论，因此宁可保守多标。"""
        needs: list[str] = []
        if "拍照" in text:
            needs.append("影像")
        if "续航" in text:
            needs.append("续航")
        if any(word in text for word in ("打游戏", "游戏性能")):
            needs.append("真实游戏性能")
        if "分辨率" in text:
            needs.append("分辨率")
        if "单手" in text:
            needs.append("机身尺寸手感")
        if "性价比" in text:
            needs.append("处理器性能")
        return needs

    # --- 数值解析 -------------------------------------------------------

    # 中文数字。基线只处理「四五千」「三千多」这类高频口语写法，
    # 真实实现应交给 Jev 做语义理解而不是扩正则。
    CN_DIGITS = {
        "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
        "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    }

    @classmethod
    def _cn_number(cls, text: str) -> float | None:
        """解析「四千」「四五千」这类中文数量，返回元为单位的下界估计。

        「四五千」返回 4000（区间下界），调用方据此给出区间。
        """
        import re

        match = re.search(r"([零一二两三四五六七八九])([零一二两三四五六七八九])?千", text)
        if not match:
            return None
        high = cls.CN_DIGITS.get(match.group(1))
        low = cls.CN_DIGITS.get(match.group(2)) if match.group(2) else None
        if high is None:
            return None
        return float(high * 1000)

    @staticmethod
    def _budget_window(text: str) -> tuple[float | None, float | None]:
        """只提取明确的预算区间或上限。

        「3000 左右」「三千多」「四千左右」都带模糊量词，不算明确上限，
        必须留给用户确认——这是工作文档第 4 章的第一条边界。
        """
        import re

        # 阿拉伯数字区间：4000 到 9000 / 2000-3000
        match = re.search(r"(\d+)\s*(?:到|-|至)\s*(\d+)", text)
        if match:
            return float(match.group(1)), float(match.group(2))

        # 中文数字区间：四五千 / 四到五千 / 五六千
        match = re.search(r"([零一二两三四五六七八九])([零一二两三四五六七八九])?千", text)
        if match:
            high = RuleBaseline.CN_DIGITS[match.group(1)]
            low_digit = match.group(2)
            if low_digit:
                low = RuleBaseline.CN_DIGITS[low_digit]
                return float(high * 1000), float(low * 1000)
            # 「四千左右」等模糊表达：不设上限
            return None, None

        # 明确上限：3000 以内 / 8000 以下 / 不超过 6000
        match = re.search(r"(\d+)\s*(?:元)?\s*(?:以内|以下|之内|不超过)", text)
        if match:
            return None, float(match.group(1))

        # 明确上限：预算 5000（数字之后不得紧跟模糊量词）
        # 注意：\d+ 需先吃满数字，再检查后续是否是「左右 / 多 / 上下」，
        # 否则回溯会把 3000 截成 300 而误判为明确上限。
        match = re.search(r"预算\s*(\d+)(?=\s*元?(?:\s|，|,|$|。|！|!))", text)
        if match and not re.search(
            r"预算\s*\d+\s*元?\s*(?:左右|多|上下|来|把)", text
        ):
            return None, float(match.group(1))

        return None, None

    @staticmethod
    def _storage_floor(text: str) -> float | None:
        import re

        for pattern in (
            r"(\d+)\s*G(?:B)?\s*(?:以上|起步|起)",
            r"最少\s*(\d+)\s*G",
            r"尽量\s*(\d+)\s*G",
            r"(\d+)\s*(?:G|GB)\s*(?:起步|起)",
            r"存储\s*(\d+)\s*(?:G|GB)?\s*(?:起步|起|以上)",
        ):
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return float(match.group(1))
        return None

    @staticmethod
    def _screen_target(text: str) -> float | None:
        import re

        match = re.search(r"(\d+(?:\.\d+)?)\s*英寸", text)
        return float(match.group(1)) if match else None


def load_testset(path: Path = TESTSET) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases", [])
    if len(cases) < 20:
        raise ValueError(f"评测集至少应有 20 例，当前 {len(cases)} 例。")
    seen: set[str] = set()
    for case in cases:
        if case["id"] in seen:
            raise ValueError(f"用例编号重复：{case['id']}")
        seen.add(case["id"])
        for key in CRITERIA:
            choice = case["expectations"].get(key)
            if choice not in CHOICES:
                raise ValueError(
                    f"{case['id']} 的 {key} 标准答案 {choice!r} 不在 {list(CHOICES)} 中。"
                )
    return cases


def evaluate_case(case: dict[str, Any], produced: dict[str, Any]) -> CaseResult:
    """比对单例。produced 的结构与测试集的标准答案一致。"""
    result = CaseResult(case_id=case["id"], boundary=case["boundary"])
    expected_hard = case["hard_constraints"]
    actual_hard = produced.get("hard_constraints", {})

    for name in HARD_FIELDS:
        want, got = expected_hard.get(name), actual_hard.get(name)
        if name == "brands":
            want, got = sorted(want or []), sorted(got or [])
        if name in NUMERIC_HARD_FIELDS:
            want = None if want is None else float(want)
            got = None if got is None else float(got)
        if want != got:
            result.errors.append(f"硬约束 {name}：期望 {want!r}，实际 {got!r}")

    expected_prefs = case["expectations"]
    actual_prefs = produced.get("expectations", {})
    for key in CRITERIA:
        want, got = expected_prefs.get(key), actual_prefs.get(key)
        if want != got:
            result.errors.append(f"偏好 {key}：期望 {want!r}，实际 {got!r}")

    expected_unsupported = set(case["unsupported_needs"])
    actual_unsupported = set(produced.get("unsupported_needs", []))
    result.missing_unsupported = sorted(expected_unsupported - actual_unsupported)
    result.extra_unsupported = sorted(actual_unsupported - expected_unsupported)

    expected_ask = set(case.get("must_ask", []))
    actual_ask = set(produced.get("must_ask", []))
    # must_ask 允许出现 HARD_FIELDS 之外的项（如 excluded_brands 的品牌排除集合），
    # 因为「必须回问用户」的字段不限于筛选字段。
    for name in sorted(expected_ask - actual_ask):
        result.errors.append(f"漏问：应回问 {name}，但未列入待确认")

    return result


def report(results: list[CaseResult], verbose: bool = False) -> int:
    """打印报告，返回失败例数。"""
    passed = [r for r in results if r.passed]
    failed = [r for r in results if not r.passed]

    print(f"总用例：{len(results)}　通过：{len(passed)}　未通过：{len(failed)}")
    print(f"通过率：{len(passed) / len(results) * 100:.1f}%")
    print()

    if failed:
        print("未通过用例：")
        for item in failed:
            print(f"  [{item.case_id}] {item.boundary}")
            if item.missing_unsupported:
                print(f"      漏标未覆盖需求（会生成虚假结论）：{item.missing_unsupported}")
            if item.extra_unsupported:
                print(f"      多标未覆盖需求（偏保守）：{item.extra_unsupported}")
            for error in item.errors:
                print(f"      {error}")
        print()

    if verbose:
        print("全部用例：")
        for item in results:
            mark = "PASS" if item.passed else "FAIL"
            print(f"  [{mark}] {item.case_id}  {item.boundary}")

    return len(failed)


def check_baseline_sanity(cases: list[dict[str, Any]]) -> list[str]:
    """检查评测集自身是否自洽，避免标准答案写错却测出高通过率。"""
    import re

    problems: list[str] = []
    for case in cases:
        text = case["text"]
        expected = case["expectations"]
        # 「未提及」是默认，不应与显式表态混用导致自相矛盾
        if expected["price"] == "未提及" and "越便宜越好" in text:
            problems.append(f"{case['id']}：文本含价格偏好但标准答案为未提及")
        if case["hard_constraints"]["min_storage"] is not None:
            if expected["storage"] != "未提及" and "越大越好" not in text:
                problems.append(
                    f"{case['id']}：有存储门槛但 storage 偏好非未提及，"
                    "需确认是门槛还是偏好"
                )
        # 含糊预算不得被写成硬约束（2026-10-01 口径，见 _budget_convention）
        hard = case["hard_constraints"]
        for marker in ("左右", "上下", "差不多"):
            if re.search(r"\d+\s*元?\s*" + marker, text):
                if hard["budget_max"] is not None or hard["budget_min"] is not None:
                    problems.append(
                        f"{case['id']}：含含糊量词「…{marker}」却设了预算硬约束，"
                        "应按口径只填 budget_reference"
                    )
                break
        if "四五千" in text and hard["budget_max"] is not None:
            problems.append(
                f"{case['id']}：中文数字连写（四五千）不得当成硬区间，"
                "应只填 budget_reference"
            )
    return problems


class ProviderAdapter:
    """把 providers/jev.py 的 parse_needs 输出适配成评测比对形状。

    这一层让评测脚本与实现解耦：换实现只需换 adapter，评测口径不变。
    """

    def __init__(self, classifier=None):
        from providers.jev import parse_needs

        self._parse_needs = parse_needs
        self._classifier = classifier

    def parse(self, text):
        needs = self._parse_needs(text, classifier=self._classifier)
        hard = {
            "budget_min": needs.hard.budget_min,
            "budget_max": needs.hard.budget_max,
            "budget_reference": needs.hard.budget_reference,
            "min_storage": needs.hard.min_storage,
            "brands": list(needs.hard.brands),
            "target_screen": needs.hard.target_screen,
        }
        prefs = {}
        for key in CRITERIA:
            item = needs.preference_for(key)
            prefs[key] = item.choice if item is not None else "未提及"

        # needs_confirmation 里可能有 "excluded_brands=华为" 这类带值项，
        # 比对时只取字段名。
        asks = {item.split("=", 1)[0] for item in needs.needs_confirmation}

        return {
            "hard_constraints": hard,
            "expectations": prefs,
            "unsupported_needs": list(needs.unsupported_needs),
            "must_ask": sorted(asks),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="中文需求理解评测")
    parser.add_argument("--verbose", action="store_true", help="逐例打印结果")
    parser.add_argument(
        "--impl",
        choices=("baseline", "provider"),
        default="baseline",
        help="baseline=内置规则基线（仅验证评测口径）；"
             "provider=providers/jev.py 的实现",
    )
    parser.add_argument(
        "--classifier",
        choices=("mock", "llm"),
        default="mock",
        help="provider 模式下的分类器：mock=确定性替身（默认）；"
             "llm=providers/llm.py 外接大模型（需 LLM_BASE_URL/LLM_MODEL/LLM_API_KEY）",
    )
    parser.add_argument(
        "--json",
        metavar="PATH",
        help="把逐例结果写入 JSON 文件，便于与其它实现对比",
    )
    args = parser.parse_args()

    cases = load_testset()
    problems = check_baseline_sanity(cases)
    if problems:
        print("评测集自身存在问题，先修正标准答案：")
        for problem in problems:
            print(f"  - {problem}")
        return 2

    if args.impl == "provider":
        try:
            from providers.jev import JevUnavailable
        except ImportError as error:
            print(f"无法加载 providers/jev.py：{error}")
            return 2

        classifier = None
        label = "providers/jev.py + MockClassifier"
        if args.classifier == "llm":
            try:
                from providers.llm import LLMClassifier

                classifier = LLMClassifier()
            except JevUnavailable as error:
                print(f"无法初始化外接大模型：{error}")
                return 2
            except ImportError as error:
                print(f"无法加载 providers/llm.py：{error}")
                return 2
            label = f"providers/llm.py + LLMClassifier（{classifier.model}）"
        adapter = ProviderAdapter(classifier=classifier)
    else:
        from providers.jev import JevUnavailable

        adapter = RuleBaseline()
        label = "内置规则基线"

    results = []
    for case in cases:
        try:
            produced = adapter.parse(case["text"])
        except JevUnavailableError as error:
            # 单例失败不应吞掉整轮评测：记录为「未通过」并保留现场。
            item = CaseResult(case_id=case["id"], boundary=case["boundary"])
            item.errors.append(f"调用失败：{error}")
            results.append(item)
            continue
        results.append(evaluate_case(case, produced))

    failed = report(results, verbose=args.verbose)
    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "impl": label,
                    "total": len(results),
                    "passed": sum(1 for item in results if item.passed),
                    "cases": [
                        {
                            "id": item.case_id,
                            "boundary": item.boundary,
                            "passed": item.passed,
                            "errors": item.errors,
                            "missing_unsupported": item.missing_unsupported,
                            "extra_unsupported": item.extra_unsupported,
                        }
                        for item in results
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"逐例结果已写入：{args.json}")
    print(f"实现：{label}")
    print()
    print(
        "警告：内置基线是参照本测试集逐条调整出来的，它在测试集上的高分\n"
        "不构成任何能力证据（评估泄漏）。providers/jev.py 的 MockClassifier\n"
        "同样如此——它只是先打通接口的确定性替身。真实效果必须接入真实\n"
        "Jev 服务后重测，届时两组分数都只作对照，不能作为交付结果上报。"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
