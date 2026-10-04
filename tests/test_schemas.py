"""契约层与需求评测集的自洽性测试。

命名以 test_ 开头，以便 `python -m unittest discover -s tests -v` 自动发现
（CONTRIBUTING.md 规定的标准测试命令）。

这些测试不依赖外部服务，也不依赖 pandas/numpy，可离线运行。
"""

import json
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from schemas import (  # noqa: E402
    CHOICES,
    CRITERIA,
    Evidence,
    HardConstraints,
    Needs,
    PreferenceItem,
    RankedProduct,
    SchemaError,
    Session,
    initial_weights,
    validate_candidate_frame,
    validate_weights,
)

TESTSET = Path(__file__).resolve().parent / "needs_testset.json"


def load_cases():
    payload = json.loads(TESTSET.read_text(encoding="utf-8"))
    return payload["cases"]


class TestNeedsTestSetIntegrity(unittest.TestCase):
    """评测集自身的自洽性。标准答案写错会导致假高分，必须先保证它自洽。"""

    @classmethod
    def setUpClass(cls):
        cls.cases = load_cases()

    def test_at_least_twenty_cases(self):
        self.assertGreaterEqual(len(self.cases), 20)

    def test_ids_unique(self):
        ids = [case["id"] for case in self.cases]
        self.assertEqual(len(ids), len(set(ids)))

    def test_choices_within_vocabulary(self):
        for case in self.cases:
            for key, choice in case["expectations"].items():
                self.assertIn(key, CRITERIA, case["id"])
                self.assertIn(choice, CHOICES, case["id"])

    def test_hard_constraints_validate(self):
        for case in self.cases:
            payload = case["hard_constraints"]
            HardConstraints(
                budget_min=payload["budget_min"],
                budget_max=payload["budget_max"],
                min_storage=payload["min_storage"],
                brands=tuple(payload["brands"]),
                target_screen=payload["target_screen"],
            )

    def test_ambiguous_budget_not_auto_capped(self):
        """含糊预算不得被自动写成明确上限（工作文档第 4 章第一条边界）。"""
        by_id = {case["id"]: case for case in self.cases}
        for case_id in ("CN-01", "CN-05", "CN-14"):
            self.assertIsNone(
                by_id[case_id]["hard_constraints"]["budget_max"],
                f"{case_id} 的含糊预算不应被自动设为上限",
            )
            self.assertIn("budget_max", by_id[case_id]["must_ask"], case_id)

    def test_storage_floor_is_not_preference(self):
        """存储门槛与存储偏好必须分开判断。"""
        by_id = {case["id"]: case for case in self.cases}
        self.assertEqual(by_id["CN-03"]["expectations"]["storage"], "未提及")
        self.assertEqual(by_id["CN-03"]["hard_constraints"]["min_storage"], 256)
        self.assertEqual(by_id["CN-04"]["expectations"]["storage"], "重要")
        self.assertEqual(by_id["CN-04"]["hard_constraints"]["min_storage"], 256)

    def test_strength_convention_consistent(self):
        """「越…越好」不得升格为「非常重要」。"""
        by_id = {case["id"]: case for case in self.cases}
        for case_id, criterion in (
            ("CN-01", "price"),
            ("CN-04", "storage"),
            ("CN-09", "weight"),
            ("CN-10", "screen"),
            ("CN-14", "storage"),
            ("CN-20", "weight"),
        ):
            self.assertEqual(
                by_id[case_id]["expectations"][criterion],
                "重要",
                f"{case_id} 的「越…越好」应按统一口径记“重要”",
            )

    def test_negated_brand_is_not_whitelisted(self):
        """排除式品牌限定不得表达为白名单。"""
        by_id = {case["id"]: case for case in self.cases}
        self.assertEqual(by_id["CN-11"]["hard_constraints"]["brands"], [])
        self.assertIn("excluded_brands", by_id["CN-11"]["must_ask"])

    def test_delegation_does_not_invent_constraints(self):
        """全权委托时不得替用户编造约束。"""
        case = next(c for c in self.cases if c["id"] == "CN-19")
        for name in ("budget_max", "min_storage", "target_screen"):
            self.assertIsNone(case["hard_constraints"][name])
        self.assertTrue({"budget_max", "min_storage", "target_screen"} <= set(case["must_ask"]))


class TestWeightContract(unittest.TestCase):
    """权重与候选集边界（工作文档第 7 章：负权重、全零权重必须明确报错）。"""

    def test_negative_weight_rejected(self):
        with self.assertRaises(SchemaError):
            validate_weights(
                {"price": -1.0, "weight": 1, "storage": 1, "refresh": 1, "screen": 1}
            )

    def test_all_zero_weight_rejected(self):
        with self.assertRaises(SchemaError):
            validate_weights(dict.fromkeys(CRITERIA, 0))

    def test_nan_weight_rejected(self):
        with self.assertRaises(SchemaError):
            validate_weights(
                {"price": float("nan"), "weight": 1, "storage": 1, "refresh": 1, "screen": 1}
            )

    def test_infinite_weight_rejected(self):
        with self.assertRaises(SchemaError):
            validate_weights(
                {"price": float("inf"), "weight": 1, "storage": 1, "refresh": 1, "screen": 1}
            )

    def test_missing_criterion_rejected(self):
        with self.assertRaises(SchemaError):
            validate_weights({"price": 1, "weight": 1})

    def test_unknown_criterion_rejected(self):
        with self.assertRaises(SchemaError):
            validate_weights(
                {"price": 1, "weight": 1, "storage": 1, "refresh": 1, "screen": 1, "battery": 1}
            )

    def test_valid_weights_normalized(self):
        clean = validate_weights(
            {"price": 30, "weight": 20, "storage": 20, "refresh": 20, "screen": 10}
        )
        self.assertEqual(sum(clean.values()), 100)


class TestPreferenceMapping(unittest.TestCase):
    """确定性权重映射（工作文档第 4 章）。"""

    def test_level_map(self):
        for choice, level in (
            ("不在意", 0),
            ("一般", 1),
            ("重要", 2),
            ("非常重要", 3),
        ):
            item = PreferenceItem(criterion="price", choice=choice)
            self.assertEqual(item.mapped_level, level, choice)

    def test_unmentioned_marks_default(self):
        item = PreferenceItem(criterion="price", choice="未提及")
        self.assertTrue(item.is_default)
        self.assertEqual(item.mapped_level, 1)

    def test_explicit_indifference_is_not_default(self):
        item = PreferenceItem(criterion="price", choice="不在意")
        self.assertFalse(item.is_default)
        self.assertEqual(item.mapped_level, 0)

    def test_initial_weights_sum_to_one(self):
        needs = Needs(
            preferences=[
                PreferenceItem(criterion="price", choice="非常重要", confirmed=True),
                PreferenceItem(criterion="weight", choice="重要", confirmed=True),
            ]
        )
        weights = initial_weights(needs)
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=9)
        # price=3、weight=2，未提及的三项各 1 → 总和 3+2+1+1+1=8
        self.assertAlmostEqual(weights["price"], 3 / 8, places=9)
        self.assertAlmostEqual(weights["weight"], 2 / 8, places=9)
        self.assertAlmostEqual(weights["screen"], 1 / 8, places=9)

    def test_all_zero_levels_rejected(self):
        needs = Needs(
            preferences=[PreferenceItem(criterion="price", choice="不在意")]
        )
        with self.assertRaises(SchemaError):
            initial_weights(needs, criteria=["price"])

    def test_confidence_out_of_range_rejected(self):
        with self.assertRaises(SchemaError):
            PreferenceItem(criterion="price", choice="重要", confidence=1.5)

    def test_unknown_choice_rejected(self):
        with self.assertRaises(SchemaError):
            PreferenceItem(criterion="price", choice="有点想要")


class TestHardConstraintContract(unittest.TestCase):
    def test_inverted_budget_rejected(self):
        with self.assertRaises(SchemaError):
            HardConstraints(budget_min=9000, budget_max=3000)

    def test_absurd_screen_rejected(self):
        with self.assertRaises(SchemaError):
            HardConstraints(target_screen=99.0)

    def test_negative_storage_rejected(self):
        with self.assertRaises(SchemaError):
            HardConstraints(min_storage=-256)

    def test_empty_needs_are_all_none(self):
        hard = HardConstraints()
        self.assertEqual(hard.to_filter_kwargs(), {})


class TestEvidenceContract(unittest.TestCase):
    """证据包契约（工作文档第 8 章）。"""

    def test_known_fact_ids_includes_products_and_comparison(self):
        evidence = Evidence(
            ranked_products=[
                RankedProduct(
                    product_id="SKU-AAAA0001",
                    product_name="测试机",
                    config="8GB / 256GB",
                    score=0.7,
                    price=2999,
                    fact_ids=("F-001", "F-002"),
                )
            ],
            comparison_facts=[{"fact_id": "F-004", "text": "更轻 20g"}],
            fact_ids=["F-003"],
        )
        self.assertEqual(
            evidence.known_fact_ids(), {"F-001", "F-002", "F-003", "F-004"}
        )

    def test_empty_evidence_has_no_facts(self):
        self.assertEqual(Evidence().known_fact_ids(), set())


class TestSessionContract(unittest.TestCase):
    def test_blank_session_id_rejected(self):
        with self.assertRaises(SchemaError):
            Session(session_id="", user_id="u1", data_version="v1")

    def test_negative_seed_rejected(self):
        with self.assertRaises(SchemaError):
            Session(session_id="s1", user_id="u1", data_version="v1", seed=-1)


class TestCandidateFrameContract(unittest.TestCase):
    """候选集字段检查（不以 numpy 依赖为前提）。"""

    def test_missing_columns_raise(self):
        with self.assertRaises(SchemaError):
            validate_candidate_frame({"columns": ["price"], "empty": False})

    def test_empty_frame_returns_no_notices(self):
        class Frame:
            columns = list(CRITERIA)
            empty = True

        self.assertEqual(validate_candidate_frame(Frame()), [])

    def test_nan_row_reported(self):
        frame = [
            {
                "id": "SKU-GOOD",
                "price": 3000.0,
                "weight": 180.0,
                "storage": 256.0,
                "refresh": 120.0,
                "screen": 6.1,
            },
            {
                "id": "SKU-BAD",
                "price": 3000.0,
                "weight": float("nan"),
                "storage": 256.0,
                "refresh": 120.0,
                "screen": 6.1,
            },
        ]
        notices = validate_candidate_frame(frame)
        self.assertEqual(len(notices), 1)
        self.assertIn("SKU-BAD", notices[0])
        self.assertIn("weight", notices[0])


if __name__ == "__main__":
    unittest.main()
