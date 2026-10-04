"""需求理解层测试（providers/jev.py）。

覆盖工作文档第 4 章的边界规则，以及接口的失败/降级行为。
不依赖外部服务，可离线运行。
"""

import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from providers.jev import (  # noqa: E402
    ASK_BUDGET_MAX,
    ASK_EXCLUDED_BRANDS,
    HttpClassifier,
    JevUnavailable,
    MockClassifier,
    classify_preferences,
    parse_needs,
)
from schemas import CHOICES, CRITERIA, Needs  # noqa: E402


class MockDeterminismTests(unittest.TestCase):
    """模拟实现必须确定性可复现——否则无法回放会话。"""

    def test_same_input_same_output(self):
        classifier = MockClassifier()
        text = "预算 3000 左右，存储 256G 以上，越轻越好"
        first = classifier.classify(text)
        second = classifier.classify(text)
        self.assertEqual(
            [(j.criterion, j.choice, j.evidence) for j in first.judgements],
            [(j.criterion, j.choice, j.evidence) for j in second.judgements],
        )
        self.assertEqual(first.asks, second.asks)

    def test_all_criteria_present(self):
        result = MockClassifier().classify("随便看看")
        self.assertEqual({j.criterion for j in result.judgements}, set(CRITERIA))

    def test_choices_always_legal(self):
        for text in ("随便", "最轻的", "越便宜越好", "别太重", ""):
            for item in MockClassifier().classify(text).judgements:
                self.assertIn(item.choice, CHOICES, text)


class BoundaryRuleTests(unittest.TestCase):
    """工作文档第 4 章的设计红线。"""

    def test_ambiguous_budget_is_not_capped(self):
        """「3000 左右」不得被自动当作上限。"""
        needs = parse_needs("预算 3000 左右，越便宜越好")
        self.assertIsNone(needs.hard.budget_max)
        self.assertIn(ASK_BUDGET_MAX, needs.needs_confirmation)

    def test_vague_chinese_budget_is_not_capped(self):
        """中文数字的含糊表达同样不得设硬约束，但数字必须留下。

        口径于 2026-10-01 由需求方确认：只有「N 到 M」「N 以内」「N 以上」
        算明确硬约束。「四五千」取中值 4500 记入 budget_reference，
        不再当作 4000–5000 的硬区间。
        """
        for text, reference in (
            ("预算三千多", 3000),
            ("预算四千左右", 4000),
            ("价格四五千", 4500),
        ):
            needs = parse_needs(text)
            self.assertIsNone(needs.hard.budget_max, text)
            self.assertIsNone(needs.hard.budget_min, text)
            self.assertEqual(needs.hard.budget_reference, reference, text)
            self.assertIn(ASK_BUDGET_MAX, needs.needs_confirmation)

    def test_explicit_range_is_a_hard_constraint(self):
        """「N 到 M」是唯一的中文/阿拉伯数字硬区间形式。"""
        needs = parse_needs("预算 6000 到 9000")
        self.assertEqual(needs.hard.budget_min, 6000)
        self.assertEqual(needs.hard.budget_max, 9000)
        # 区间已由 min/max 表达，不再编一个中值当参考
        self.assertIsNone(needs.hard.budget_reference)

    def test_bare_budget_number_is_reference_only(self):
        """「预算 5000」光秃秃一个数字，不算明确上限。"""
        needs = parse_needs("预算 5000，屏幕要大")
        self.assertIsNone(needs.hard.budget_max)
        self.assertEqual(needs.hard.budget_reference, 5000)

    def test_storage_floor_is_not_read_as_budget(self):
        """「存储 512 以上」是存储门槛，不得被当成预算下界。"""
        needs = parse_needs("存储 512 以上")
        self.assertIsNone(needs.hard.budget_min)
        self.assertEqual(needs.hard.min_storage, 512)

    def test_storage_floor_accepts_every_unit_spelling(self):
        """带单位、不带单位、TB 都要认出来。

        回归：TB 曾完全不被识别（模式里只写死了 `\\d+\\s*G`），
        用户说「要 1TB 的」会被读成「没提存储」，界面反而去追问存储门槛。
        另一头，把单位改成**必填**又会让「存储 512 以上」这种不带单位的写法丢失。
        """
        cases = (
            ("存储 512 以上", 512),
            ("存储 512 起", 512),
            ("存储 512G 以上", 512),
            ("存储 512GB 起", 512),
            ("存储 1TB 以上", 1024),
            ("要 1TB 的", 1024),
            ("要 1T 的", 1024),
            ("最少 128G", 128),
            ("尽量 256G", 256),
            ("256G 起步就行", 256),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(parse_needs(text).hard.min_storage, expected, text)

    def test_bare_number_without_storage_context_stays_a_budget(self):
        """没有「存储」语境又不带单位时，仍是预算，不能被吞成存储门槛。

        这是上一条的边界：放宽单位匹配的前提是语境足够明确。
        """
        needs = parse_needs("预算 512 以上")
        self.assertEqual(needs.hard.budget_min, 512)
        self.assertIsNone(needs.hard.min_storage)

    def test_reference_is_not_a_filter(self):
        """参考预算永远不进筛选参数。"""
        needs = parse_needs("预算 3000 左右")
        self.assertNotIn("budget_reference", needs.hard.to_filter_kwargs())

    def test_explicit_cap_is_extracted(self):
        for text, expected in (
            ("预算 3000 以内", 3000),
            ("预算 8000 以下", 8000),
            ("预算不超过 6000", 6000),
        ):
            self.assertEqual(parse_needs(text).hard.budget_max, expected, text)

    def test_storage_floor_is_not_preference(self):
        """存储门槛与存储偏好必须分开。"""
        floor_only = parse_needs("想买个 iPhone，256GB 起步，预算 8000 以内")
        self.assertEqual(floor_only.hard.min_storage, 256)
        self.assertEqual(floor_only.preference_for("storage").choice, "未提及")

        both = parse_needs("要 iPhone，存储越大越好，最少 256G，预算 6000 到 9000")
        self.assertEqual(both.hard.min_storage, 256)
        self.assertEqual(both.preference_for("storage").choice, "重要")

    def test_screen_phrase_does_not_leak_into_storage(self):
        """「越大越好」指屏幕时不得被判成存储偏好。"""
        needs = parse_needs("预算 5000，屏幕要 6.7 英寸，越大越好")
        self.assertEqual(needs.preference_for("storage").choice, "未提及")
        self.assertEqual(needs.hard.target_screen, 6.7)
        self.assertEqual(needs.preference_for("screen").choice, "重要")

    def test_explicit_indifference_differs_from_unmentioned(self):
        indifferent = parse_needs("轻一点好，越轻越好，但价格不太在意")
        self.assertEqual(indifferent.preference_for("price").choice, "不在意")
        self.assertFalse(indifferent.preference_for("price").is_default)

        silent = parse_needs("轻一点好，越轻越好")
        self.assertEqual(silent.preference_for("price").choice, "未提及")
        self.assertTrue(silent.preference_for("price").is_default)

    def test_excluded_brand_is_not_whitelisted(self):
        needs = parse_needs("不要华为，其他牌子都行，存储 512G 以上")
        self.assertEqual(tuple(needs.hard.brands), ())
        self.assertTrue(
            any(item.startswith(ASK_EXCLUDED_BRANDS) for item in needs.needs_confirmation)
        )

    def test_excluded_brand_listed_once(self):
        """排除项在待确认清单里只能出现一次。"""
        needs = parse_needs("不要华为，存储 512G 以上")
        entries = [i for i in needs.needs_confirmation if ASK_EXCLUDED_BRANDS in i]
        self.assertEqual(len(entries), 1, needs.needs_confirmation)
        self.assertIn("华为", entries[0])

    def test_whitelist_brand_maps_to_data_label(self):
        needs = parse_needs("打游戏不卡就行，刷新率要高，一加或者红米")
        self.assertEqual(set(needs.hard.brands), {"一加", "Redmi"})

    def test_unsupported_needs_recorded(self):
        """无数据支撑的需求记为未覆盖，不得用替代指标充数。"""
        needs = parse_needs("拍照要好的，续航越久越好，打游戏不卡")
        for label in ("影像", "续航", "真实游戏性能"):
            self.assertIn(label, needs.unsupported_needs)

    def test_blanket_statement_applies_to_unmentioned(self):
        """「其他都一般」作用于未点名的维度，不覆盖已表态项。"""
        needs = parse_needs("价格最重要，其他都一般，预算 4000 以内")
        self.assertEqual(needs.preference_for("price").choice, "非常重要")
        for key in ("weight", "storage", "refresh", "screen"):
            self.assertEqual(needs.preference_for(key).choice, "一般", key)

    def test_strength_convention(self):
        """「越…越好」记重要；含最高级才记非常重要。"""
        self.assertEqual(
            parse_needs("越轻越好").preference_for("weight").choice, "重要"
        )
        self.assertEqual(
            parse_needs("给我推荐个最轻的").preference_for("weight").choice,
            "非常重要",
        )


class ParseNeedsContractTests(unittest.TestCase):
    def test_returns_needs(self):
        self.assertIsInstance(parse_needs("预算 3000 以内"), Needs)

    def test_unconfirmed_by_default(self):
        """默认不把结果标成已确认——必须过用户确认页。"""
        needs = parse_needs("越轻越好")
        self.assertFalse(needs.preference_for("weight").confirmed)
        self.assertTrue(needs.unconfirmed_criteria())

    def test_confirmed_flag_propagates(self):
        needs = parse_needs("越轻越好", confirmed=True)
        self.assertTrue(needs.preference_for("weight").confirmed)
        self.assertEqual(needs.unconfirmed_criteria(), [])

    def test_evidence_kept(self):
        """每一项都保留原文证据，便于确认页展示。"""
        needs = parse_needs("越轻越好")
        self.assertTrue(needs.preference_for("weight").raw_text)

    def test_unsupported_not_in_judgements(self):
        """未覆盖需求不得混进可评分维度。"""
        needs = parse_needs("拍照要好的")
        self.assertEqual(len(needs.preferences), len(CRITERIA))
        self.assertNotIn("影像", {p.criterion for p in needs.preferences})

    def test_classify_preferences_defaults_to_mock(self):
        result = classify_preferences("预算 3000 以内")
        self.assertEqual(result.provider, "mock")


class HttpClassifierTests(unittest.TestCase):
    """真实服务接入点的失败行为——失败必须显式，不得静默降级。"""

    def test_missing_endpoint_raises(self):
        with self.assertRaises(JevUnavailable):
            HttpClassifier(endpoint="")

    def test_missing_key_raises(self):
        classifier = HttpClassifier(endpoint="https://example.invalid/jev")
        with self.assertRaises(JevUnavailable):
            classifier.classify("预算 3000 以内")

    def test_illegal_choice_degrades_to_unmentioned(self):
        """供应商返回非法选项时不猜，退回未提及并标 unknown。"""
        classifier = HttpClassifier(endpoint="https://example.invalid/jev")
        normalised = classifier._normalise(
            {"judgements": [{"criterion": "price", "choice": "有点想要"}]}
        )
        price = normalised.judgement_for("price")
        self.assertEqual(price.choice, "未提及")
        self.assertEqual(price.verdict, "unknown")

    def test_legal_choice_passes_through_with_confidence(self):
        classifier = HttpClassifier(endpoint="https://example.invalid/jev")
        normalised = classifier._normalise(
            {
                "judgements": [
                    {"criterion": "weight", "choice": "重要", "confidence": 0.9,
                     "evidence": "越轻越好"}
                ],
                "asks": ["budget_max"],
            }
        )
        weight = normalised.judgement_for("weight")
        self.assertEqual(weight.choice, "重要")
        self.assertAlmostEqual(weight.confidence, 0.9)
        self.assertIn("budget_max", normalised.asks)

    def test_unknown_criterion_in_payload_ignored(self):
        classifier = HttpClassifier(endpoint="https://example.invalid/jev")
        normalised = classifier._normalise(
            {"judgements": [{"criterion": "battery", "choice": "重要"}]}
        )
        self.assertEqual({j.criterion for j in normalised.judgements}, set(CRITERIA))
        self.assertEqual(
            normalised.judgement_for("price").choice, "未提及"
        )

    def test_confidence_is_recorded_but_not_strength(self):
        """confidence 只作记录，不得当作偏好强度。"""
        classifier = HttpClassifier(endpoint="https://example.invalid/jev")
        normalised = classifier._normalise(
            {"judgements": [{"criterion": "price", "choice": "一般",
                             "confidence": 0.99}]}
        )
        item = normalised.judgement_for("price").to_preference()
        self.assertEqual(item.choice, "一般")
        self.assertEqual(item.mapped_level, 1)  # 由 choice 决定，不由 confidence
        self.assertAlmostEqual(item.confidence, 0.99)


if __name__ == "__main__":
    unittest.main()
