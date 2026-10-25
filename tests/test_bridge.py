"""bridge.py 的单测：Needs → 界面控件值的映射规则。

重点不是覆盖率，而是把三条硬规则和两条「不这样做界面会崩」的约束钉死：
含糊预算不封顶、排除品牌不转白名单、越界向上取整。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from bridge import (  # noqa: E402
    BUDGET_DEFAULT,
    BUDGET_LIMIT,
    SCREEN_DEFAULT,
    SCREEN_RANGE,
    STORAGE_DEFAULT,
    STORAGE_OPTIONS,
    WEIGHT_STEP,
    bridge,
    bridge_contract,
    needs_from_contract,
)
from needs_extraction import extract_needs  # noqa: E402
from providers.jev import MockClassifier  # noqa: E402
from schemas import CRITERIA, HardConstraints, Needs, PreferenceItem  # noqa: E402

BRANDS = ["苹果", "小米", "华为", "荣耀", "Redmi", "一加", "iQOO", "realme"]


def make(
    *,
    budget_min: float | None = None,
    budget_max: float | None = None,
    budget_reference: float | None = None,
    min_storage: float | None = None,
    brands: tuple[str, ...] = (),
    target_screen: float | None = None,
    choices: dict[str, str] | None = None,
    pending: list[str] | None = None,
    unsupported: list[str] | None = None,
) -> Needs:
    """按需构造一个 Needs，未指定的维度一律「未提及」。"""
    given = choices or {}
    preferences = [
        PreferenceItem(criterion=key, choice=given.get(key, "未提及"))
        for key in CRITERIA
    ]
    return Needs(
        hard=HardConstraints(
            budget_min=budget_min,
            budget_max=budget_max,
            budget_reference=budget_reference,
            min_storage=min_storage,
            brands=brands,
            target_screen=target_screen,
        ),
        preferences=preferences,
        unsupported_needs=list(unsupported or []),
        needs_confirmation=list(pending or []),
    )


class BudgetTests(unittest.TestCase):
    def test_ambiguous_budget_is_not_capped(self):
        """含糊预算不得由程序封顶，必须进待确认。"""
        result = bridge(make(budget_min=3000), known_brands=BRANDS)
        self.assertEqual(result.budget[0], 3000)
        self.assertIn("预算上界", result.pending)
        # 上界没有被"推断"成一个接近 3000 的数字
        self.assertGreater(result.budget[1], 3000)

    def test_explicit_upper_bound_is_kept(self):
        result = bridge(make(budget_max=8000), known_brands=BRANDS)
        # 只给了上界，下界就是滑块下限（没有下界约束）
        self.assertEqual(result.budget, (BUDGET_LIMIT[0], 8000))
        self.assertNotIn("预算上界", result.pending)

    def test_both_bounds_explicit(self):
        result = bridge(
            make(budget_min=6000, budget_max=9000), known_brands=BRANDS
        )
        self.assertEqual(result.budget, (6000, 9000))
        self.assertEqual(result.pending, [])

    def test_lower_bound_above_default_upper_is_not_capped_below(self):
        """「预算 8000 以上」不得被系统默认上界 6000 卡死。"""
        result = bridge(make(budget_min=8000), known_brands=BRANDS)
        self.assertEqual(result.budget[0], 8000)
        self.assertGreaterEqual(result.budget[1], 8000)
        self.assertIn("预算上界", result.pending)

    def test_lower_bound_rounds_down(self):
        """下界向下取整，宁可放宽也不要错误排除。"""
        result = bridge(make(budget_min=3950), known_brands=BRANDS)
        self.assertEqual(result.budget[0], 3900)


class BudgetInferenceTests(unittest.TestCase):
    """含糊锚点（「5000左右」）→ 可用区间。

    2026-10-25 新增：锚点定中心，候选密度定宽度，区间必须回显。
    旧行为是锚点被完全丢弃、落到系统默认 (1000,6000)——说「8000左右」会被
    砍掉 10 个机型，这是本类存在的原因。
    """

    def infer(self, reference, price_index=None):
        result = bridge(
            make(budget_reference=reference), known_brands=BRANDS, price_index=price_index
        )
        return result

    def test_reference_produces_an_asymmetric_range(self):
        result = self.infer(5000)
        low, high = result.budget
        # 上浮幅度大于下浮：价格分布右偏，用户对「贵一点」更宽容
        self.assertLess(5000 - low, high - 5000)
        self.assertLess(low, 5000)
        self.assertGreater(high, 5000)

    def test_reference_range_is_displayed_not_silent(self):
        """红线：推断可以，但不得静默。区间必须写进 notes。"""
        result = self.infer(5000)
        joined = " ".join(result.notes)
        self.assertIn("推断", joined)
        self.assertIn("可修改", joined)

    def test_reference_does_not_ask_for_upper_bound(self):
        result = self.infer(5000)
        self.assertNotIn("预算上界", result.pending)
        self.assertFalse(result.needs_user_confirmation)

    def test_high_reference_is_not_clipped_by_the_default_upper_bound(self):
        """「8000左右」不得被系统默认上界 6000 卡死。"""
        result = self.infer(8000)
        self.assertGreater(result.budget[1], 8000)

    def test_explicit_bounds_beat_inference(self):
        """明确表达永远优先于推断。"""
        explicit = bridge(
            make(budget_min=4000, budget_max=6000, budget_reference=5000),
            known_brands=BRANDS,
        )
        self.assertEqual(explicit.budget, (4000, 6000))
        self.assertEqual(explicit.pending, [])

    def test_density_widens_the_range_when_candidates_are_sparse(self):
        """区间内机型太少时逐级放宽，而不是返回一个空集。"""
        # 只在 4000–4100 放 1 个机型，其余机型离得很远
        sparse = [(4050.0, "M1"), (15000.0, "M2"), (18000.0, "M3")]
        result = self.infer(4000, price_index=sparse)
        low, high = result.budget
        self.assertGreater(high, 4000 * 1.25, "候选不足时必须放宽")
        self.assertTrue(any("放宽" in note for note in result.notes))

    def test_density_keeps_the_tight_range_when_candidates_are_dense(self):
        dense = [(p, f"M{i}") for i, p in enumerate(range(3400, 5000, 100))]
        result = self.infer(4000, price_index=dense)
        self.assertFalse(any("放宽" in note for note in result.notes))

    def test_no_reference_and_no_bounds_falls_back_to_default_and_asks(self):
        result = bridge(make(), known_brands=BRANDS)
        self.assertEqual(result.budget, BUDGET_DEFAULT)
        self.assertIn("预算上界", result.pending)

    def test_invalid_reference_is_rejected(self):
        from bridge import infer_budget_range

        for bad in (0, -1, float("nan"), float("inf"), True, "5000"):
            with self.subTest(reference=bad):
                with self.assertRaises(ValueError):
                    infer_budget_range(bad)

    def test_upper_bound_rounds_up(self):
        """上界向上取整，宁可放宽也不要错误排除。"""
        result = bridge(make(budget_max=8050), known_brands=BRANDS)
        self.assertEqual(result.budget[1], 8100)

    def test_upper_bound_clamped_to_widget_range(self):
        result = bridge(make(budget_max=99999), known_brands=BRANDS)
        self.assertLessEqual(result.budget[1], BUDGET_LIMIT[1])

    def test_budget_always_valid_tuple(self):
        for needs in (
            make(),
            make(budget_min=3000),
            make(budget_max=8000),
            make(budget_min=6000, budget_max=9000),
        ):
            with self.subTest(needs=needs.hard):
                low, high = bridge(needs, known_brands=BRANDS).budget
                self.assertLessEqual(low, high)
                self.assertGreaterEqual(low, BUDGET_LIMIT[0])
                self.assertLessEqual(high, BUDGET_LIMIT[1])


class StorageTests(unittest.TestCase):
    def test_unmentioned_uses_default_and_is_flagged(self):
        result = bridge(make(), known_brands=BRANDS)
        self.assertEqual(result.storage, STORAGE_DEFAULT)
        self.assertTrue(any("存储" in note for note in result.notes))

    def test_exact_option_is_kept(self):
        result = bridge(make(min_storage=512), known_brands=BRANDS)
        self.assertEqual(result.storage, 512)

    def test_rounds_up_not_down(self):
        """300GB 门槛必须落到 512，落到 256 会放出不满足门槛的候选。"""
        result = bridge(make(min_storage=300), known_brands=BRANDS)
        self.assertEqual(result.storage, 512)

    def test_just_above_option_rounds_up(self):
        result = bridge(make(min_storage=257), known_brands=BRANDS)
        self.assertEqual(result.storage, 512)

    def test_beyond_max_option_degrades(self):
        result = bridge(make(min_storage=4096), known_brands=BRANDS)
        self.assertEqual(result.storage, STORAGE_OPTIONS[-1])
        self.assertTrue(any("超过" in note for note in result.notes))

    def test_always_a_valid_option(self):
        for value in (None, 64, 100, 256, 300, 512, 1024, 2048, 4096):
            with self.subTest(value=value):
                result = bridge(make(min_storage=value), known_brands=BRANDS)
                self.assertIn(result.storage, STORAGE_OPTIONS)


class ScreenTests(unittest.TestCase):
    def test_unmentioned_uses_default(self):
        self.assertEqual(bridge(make(), known_brands=BRANDS).target, SCREEN_DEFAULT)

    def test_explicit_value_kept(self):
        self.assertEqual(bridge(make(target_screen=6.1), known_brands=BRANDS).target, 6.1)

    def test_too_small_is_clamped_up(self):
        result = bridge(make(target_screen=4.0), known_brands=BRANDS)
        self.assertEqual(result.target, SCREEN_RANGE[0])

    def test_too_large_is_clamped_down(self):
        result = bridge(make(target_screen=9.0), known_brands=BRANDS)
        self.assertEqual(result.target, SCREEN_RANGE[1])

    def test_always_within_slider_range(self):
        for value in (None, 4.0, 5.5, 6.5, 7.0, 9.0):
            with self.subTest(value=value):
                target = bridge(make(target_screen=value), known_brands=BRANDS).target
                self.assertGreaterEqual(target, SCREEN_RANGE[0])
                self.assertLessEqual(target, SCREEN_RANGE[1])


class BrandTests(unittest.TestCase):
    def test_excluded_brand_is_not_whitelisted(self):
        """「不要华为」绝不能变成白名单。"""
        result = bridge(
            make(pending=["excluded_brands=华为"]), known_brands=BRANDS
        )
        self.assertEqual(result.brands, [])
        self.assertEqual(result.excluded_brands, ["华为"])

    def test_excluded_brand_not_in_pending_anymore(self):
        result = bridge(
            make(pending=["excluded_brands=华为"]), known_brands=BRANDS
        )
        self.assertFalse(any("excluded_brands=" in item for item in result.pending))

    def test_multiple_excluded_brands(self):
        result = bridge(
            make(pending=["excluded_brands=华为/荣耀"]), known_brands=BRANDS
        )
        self.assertEqual(result.excluded_brands, ["华为", "荣耀"])

    def test_whitelist_kept_separate(self):
        result = bridge(make(brands=("小米",)), known_brands=BRANDS)
        self.assertEqual(result.brands, ["小米"])
        self.assertEqual(result.excluded_brands, [])

    def test_unknown_brand_is_dropped(self):
        """数据表里没有的品牌必须剔除，否则 multiselect 会抛异常。"""
        result = bridge(make(brands=("诺基亚",)), known_brands=BRANDS)
        self.assertEqual(result.brands, [])
        self.assertTrue(any("不在数据表内" in note for note in result.notes))

    def test_unknown_excluded_brand_is_dropped(self):
        result = bridge(
            make(pending=["excluded_brands=诺基亚"]), known_brands=BRANDS
        )
        self.assertEqual(result.excluded_brands, [])

    def test_other_pending_items_survive_extraction(self):
        result = bridge(
            make(pending=["excluded_brands=华为", "target_screen"]),
            known_brands=BRANDS,
        )
        self.assertIn("屏幕尺寸", result.pending)


class WeightTests(unittest.TestCase):
    def test_weights_sum_to_scale(self):
        result = bridge(
            make(choices={"price": "重要"}), known_brands=BRANDS
        )
        self.assertGreater(sum(result.weights.values()), 0)

    def test_all_criteria_present(self):
        result = bridge(make(), known_brands=BRANDS)
        self.assertEqual(set(result.weights), set(CRITERIA))

    def test_indifferent_maps_to_zero(self):
        result = bridge(
            make(choices={"price": "不在意"}), known_brands=BRANDS
        )
        self.assertEqual(result.weights["price"], 0)

    def test_highest_level_dominates(self):
        result = bridge(
            make(choices={"weight": "非常重要"}), known_brands=BRANDS
        )
        self.assertEqual(
            result.weights["weight"], max(result.weights.values())
        )

    def test_all_indifferent_falls_back_to_uniform(self):
        """全部「不在意」时等级总和为 0，必须兜底而不是崩。"""
        result = bridge(
            make(choices={key: "不在意" for key in CRITERIA}),
            known_brands=BRANDS,
        )
        self.assertGreater(sum(result.weights.values()), 0)
        self.assertTrue(any("均匀先验" in note for note in result.notes))

    def test_weights_are_non_negative(self):
        result = bridge(make(), known_brands=BRANDS)
        self.assertTrue(all(value >= 0 for value in result.weights.values()))

    def test_weights_on_slider_grid(self):
        result = bridge(
            make(choices={"price": "非常重要", "weight": "重要"}),
            known_brands=BRANDS,
        )
        for key, value in result.weights.items():
            with self.subTest(criterion=key):
                self.assertEqual(value % WEIGHT_STEP, 0)

    def test_unmentioned_is_neutral_not_zero(self):
        """未提及走中性先验，不能被当成不在意。"""
        result = bridge(
            make(choices={"price": "非常重要"}), known_brands=BRANDS
        )
        self.assertGreater(result.weights["weight"], 0)


class PassthroughTests(unittest.TestCase):
    def test_unsupported_needs_pass_through(self):
        result = bridge(
            make(unsupported=["影像", "续航"]), known_brands=BRANDS
        )
        self.assertEqual(result.unsupported, ["影像", "续航"])

    def test_unsupported_not_mapped_to_any_criterion(self):
        """未覆盖需求不得映射到可评分维度。"""
        result = bridge(make(unsupported=["影像"]), known_brands=BRANDS)
        self.assertNotIn("影像", result.weights)
        self.assertNotIn("影像", CRITERIA)

    def test_pending_is_deduplicated(self):
        result = bridge(
            make(budget_min=3000, pending=["budget_max"]), known_brands=BRANDS
        )
        self.assertEqual(result.pending.count("预算上界"), 1)

    def test_pending_is_human_readable(self):
        result = bridge(make(budget_min=3000), known_brands=BRANDS)
        self.assertIn("预算上界", result.pending)
        self.assertFalse(any("budget_max" == item for item in result.pending))

    def test_notes_always_explain_weights(self):
        result = bridge(make(), known_brands=BRANDS)
        self.assertTrue(any("偏好" in note or "权重" in note for note in result.notes))

    def test_no_known_brands_skips_filtering(self):
        result = bridge(make(brands=("诺基亚",)))
        self.assertEqual(result.brands, ["诺基亚"])


class ContractAdapterTests(unittest.TestCase):
    """契约 dict → Needs → 控件值。

    这一层存在的意义是让 `bridge()` 的全部规则只有一份实现，
    所以重点验证「契约字段被正确搬运」，而不是重复测 `bridge()` 本身的规则。
    """

    def _result(self, text: str) -> dict:
        return extract_needs(text, classifier=MockClassifier())

    def test_reference_budget_survives_the_round_trip(self):
        """「2000 左右」的参考值不能被适配层吃掉。"""
        needs, degraded = needs_from_contract(self._result("想买 2000 元左右的手机"))
        self.assertEqual(needs.hard.budget_reference, 2000.0)
        self.assertIsNone(needs.hard.budget_max)
        self.assertEqual(degraded, [])

    def test_explicit_bounds_are_carried_over(self):
        needs, _ = needs_from_contract(self._result("预算 4000 到 6000"))
        self.assertEqual(needs.hard.budget_min, 4000.0)
        self.assertEqual(needs.hard.budget_max, 6000.0)

    def test_preferences_and_evidence_are_carried_over(self):
        needs, _ = needs_from_contract(self._result("越轻越好，价格无所谓"))
        weight = needs.preference_for("weight")
        price = needs.preference_for("price")
        self.assertEqual(weight.choice, "重要")
        self.assertEqual(weight.raw_text, "越轻越好")
        self.assertEqual(price.choice, "不在意")

    def test_unsupported_needs_are_carried_over(self):
        needs, _ = needs_from_contract(self._result("拍照要好的，预算 4000 以内"))
        self.assertIn("影像", needs.unsupported_needs)

    def test_excluded_brands_use_the_existing_encoding(self):
        """排除品牌要靠 `needs_confirmation` 传下去，`bridge()` 才认得。"""
        result = self._result("不要华为，其他都行")
        needs, _ = needs_from_contract(result)
        self.assertTrue(any("华为" in item for item in needs.needs_confirmation))
        bridged, _ = bridge_contract(result, known_brands=BRANDS)
        self.assertEqual(bridged.excluded_brands, ["华为"])
        self.assertEqual(bridged.brands, [])

    def test_whitelist_is_not_polluted_by_exclusions(self):
        result = self._result("不要华为，其他都行")
        bridged, _ = bridge_contract(result, known_brands=BRANDS)
        self.assertNotIn("华为", bridged.brands)

    def test_end_to_end_matches_direct_bridge(self):
        """同一份需求，走契约适配与走原生 Needs 必须得到同样的控件值。"""
        text = "预算 3000 以内，256G 起步，越轻越好"
        result = self._result(text)
        from_contract, _ = bridge_contract(result, known_brands=BRANDS)
        direct = bridge(needs_from_contract(result)[0], known_brands=BRANDS)
        self.assertEqual(from_contract.budget, direct.budget)
        self.assertEqual(from_contract.storage, direct.storage)
        self.assertEqual(from_contract.target, direct.target)
        self.assertEqual(from_contract.weights, direct.weights)

    def test_contradictory_budget_degrades_instead_of_raising(self):
        """模型给出 min > max 时丢字段保其余，不能整条需求作废。"""
        result = self._result("预算 3000 以内")
        result["budget"]["min"] = 9000  # 人为造出自相矛盾
        needs, degraded = needs_from_contract(result)
        self.assertTrue(degraded, "应当记录降级原因")
        self.assertIsNone(needs.hard.budget_min)
        self.assertIsNone(needs.hard.budget_max)
        self.assertEqual(needs.hard.min_storage, None)

    def test_out_of_range_screen_degrades(self):
        result = self._result("预算 3000 以内")
        result["target_screen"] = 99.0
        needs, degraded = needs_from_contract(result)
        self.assertTrue(any("屏幕" in item for item in degraded))
        self.assertIsNone(needs.hard.target_screen)

    def test_unknown_criterion_is_skipped(self):
        result = self._result("预算 3000 以内")
        result["preferences"].append(
            {"criterion": "battery", "level": "重要", "evidence": ""}
        )
        needs, _ = needs_from_contract(result)
        self.assertIsNone(needs.preference_for("battery"))

    def test_unknown_brand_is_dropped_with_a_note(self):
        result = self._result("只看诺基亚")
        result["brands"]["include"] = ["诺基亚"]
        bridged, _ = bridge_contract(result, known_brands=BRANDS)
        self.assertEqual(bridged.brands, [])
        self.assertTrue(any("诺基亚" in note for note in bridged.notes))

    def test_empty_contract_does_not_crash(self):
        needs, degraded = needs_from_contract({})
        self.assertEqual(degraded, [])
        self.assertEqual(needs.preferences, [])


if __name__ == "__main__":
    unittest.main()
