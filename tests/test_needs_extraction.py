"""需求抽取契约入口测试（needs_extraction.py）。

对应交付物第 3、4 项：
- 固定返回结构 + 字段校验（`validate_result`）
- 超时 / 空内容 / 非法结果的处理（三态 `ok` / `degraded` / `failed`）

重点验证三条红线：
1. **永不抛异常** —— 界面必须能无条件渲染；
2. **含糊预算不封顶，但数字要留下** —— reference 记下来，min/max 留空；
3. **参考值不参与筛选** —— `budget_reference` 不进 `to_filter_kwargs()`。

不依赖外部服务，可离线运行。
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from needs_extraction import (  # noqa: E402
    SCHEMA_VERSION,
    STATUS_DEGRADED,
    STATUS_FAILED,
    STATUS_OK,
    UNSUPPORTED_VALUES,
    extract_needs,
    validate_result,
)
from providers.jev import (  # noqa: E402
    JevUnavailable,
    MockClassifier,
    normalise_response,
)
from schemas import CHOICES, CRITERIA, HardConstraints  # noqa: E402

REFERENCE_TEXT = "想买 2000 元左右、拍照好点、轻一点的手机。"


class _StubClassifier:
    """返回指定 payload 的替身，用来构造降级场景。

    走真实的 `normalise_response`，因此降级规则与线上同一条代码路径。
    """

    name = "stub"

    def __init__(self, payload):
        self.payload = payload

    def classify(self, text):
        return normalise_response(
            self.payload,
            provider="stub",
            model_version="stub-1",
            source_text=text,
        )


class _BoomClassifier:
    """调用即抛异常，用来验证「永不抛」的兜底。"""

    name = "boom"

    def classify(self, text):
        raise RuntimeError("上游炸了")


def _payload(**overrides):
    """一份合法的模型回复骨架，按需覆盖字段。"""
    body = {
        "judgements": [
            {"criterion": c, "choice": "未提及", "evidence": "", "verdict": "unknown"}
            for c in CRITERIA
        ],
        "hard_constraints": {
            "budget_min": None,
            "budget_max": None,
            "budget_reference": None,
            "min_storage": None,
            "brands": [],
            "target_screen": None,
        },
        "evidence": {"budget": "", "min_storage": "", "target_screen": "", "brands": ""},
        "excluded_brands": [],
        "asks": [],
        "unsupported_needs": [],
    }
    body.update(overrides)
    return body


def _no_llm_env():
    """清掉大模型环境变量，强制走「未配置」分支。"""
    return mock.patch.dict(os.environ, {}, clear=False)


class ReferenceExampleTests(unittest.TestCase):
    """用户给的基准例子必须逐字段对得上。"""

    def setUp(self):
        self.result = extract_needs(REFERENCE_TEXT, classifier=MockClassifier())

    def test_status_is_ok(self):
        self.assertEqual(self.result["status"], STATUS_OK)

    def test_budget_reference_is_recorded(self):
        """「2000 左右」的数字必须留下来。"""
        self.assertEqual(self.result["budget"]["reference"], 2000.0)

    def test_budget_bounds_are_left_empty(self):
        """含糊预算不得自动封顶——这是第一条红线。"""
        self.assertIsNone(self.result["budget"]["min"])
        self.assertIsNone(self.result["budget"]["max"])

    def test_lightweight_preference_is_captured(self):
        weight = {item["criterion"]: item for item in self.result["preferences"]}["weight"]
        self.assertEqual(weight["level"], "一般")
        self.assertEqual(weight["evidence"], "轻一点")

    def test_camera_need_becomes_the_photo_dimension(self):
        """2026-10-26 修订：拍照不再是"未覆盖"，它由 photo 维度承接。

        旧契约把拍照标成 unsupported_needs=["影像"]，但那样一来用户说"拍照好"
        就永远不会影响排序。现在改为给出 photo 的偏好等级，并且不再标未覆盖。
        """
        needs = [item["need"] for item in self.result["unsupported_needs"]]
        self.assertNotIn("影像", needs)
        photo = {i["criterion"]: i for i in self.result["preferences"]}["photo"]
        self.assertEqual(photo["level"], "重要")
        self.assertIn("拍照", photo["evidence"])   # 证据取自原文片段

    def test_preferences_cover_exactly_the_agreed_criteria(self):
        seen = {item["criterion"] for item in self.result["preferences"]}
        self.assertEqual(seen, set(CRITERIA))

    def test_clarification_does_not_ask_for_budget_ceiling(self):
        """有锚点时不再追问上限——改由 bridge 推断区间并回显（2026-10-25 修订）。"""
        fields = [item["field"] for item in self.result["clarifications"]]
        self.assertNotIn("budget.max", fields)

    def test_validate_result_passes(self):
        self.assertEqual(validate_result(self.result), [])

    def test_versions_are_present(self):
        self.assertEqual(self.result["schema_version"], SCHEMA_VERSION)
        self.assertTrue(self.result["prompt_version"])

    def test_input_text_is_echoed_verbatim(self):
        self.assertEqual(self.result["input_text"], REFERENCE_TEXT)


class StatusFailedTests(unittest.TestCase):
    """失败路径：**任何异常都不抛**，返回同一结构。"""

    def test_empty_input_is_failed_not_an_exception(self):
        result = extract_needs("   ", classifier=MockClassifier())
        self.assertEqual(result["status"], STATUS_FAILED)
        self.assertIn("输入为空", result["meta"]["error"])
        self.assertEqual(validate_result(result), [])

    def test_empty_input_asks_all_fallback_fields(self):
        result = extract_needs("", classifier=MockClassifier())
        fields = {item["field"] for item in result["clarifications"]}
        self.assertEqual(fields, {"budget.max", "min_storage", "target_screen"})

    def test_empty_input_has_all_criteria_at_unmentioned(self):
        result = extract_needs("", classifier=MockClassifier())
        levels = {item["criterion"]: item["level"] for item in result["preferences"]}
        self.assertEqual(set(levels), set(CRITERIA))
        self.assertTrue(all(level == "未提及" for level in levels.values()))

    def test_classifier_exception_is_swallowed(self):
        result = extract_needs("随便买个手机", classifier=_BoomClassifier())
        self.assertEqual(result["status"], STATUS_FAILED)
        self.assertIn("RuntimeError", result["meta"]["error"])

    def test_jev_unavailable_is_swallowed(self):
        class _Unavailable:
            name = "unavailable"

            def classify(self, text):
                raise JevUnavailable("服务不可用")

        result = extract_needs("随便买个手机", classifier=_Unavailable())
        self.assertEqual(result["status"], STATUS_FAILED)
        self.assertIn('服务不可用',result['meta']['error'])
        self.assertIn('不显示上游异常内容',result['meta']['error'])

    def test_missing_config_returns_failed(self):
        """没配 LLM_BASE_URL 时不抛，退回 failed 结构。"""
        with mock.patch.dict(os.environ, {}, clear=True):
            result = extract_needs("想买个手机")
        self.assertEqual(result["status"], STATUS_FAILED)
        self.assertIn("LLM_BASE_URL", result["meta"]["error"])

    def test_failed_result_is_still_renderable(self):
        """界面必须能无条件渲染：字段一个都不能少。"""
        result = extract_needs("", classifier=MockClassifier())
        self.assertEqual(validate_result(result), [])
        self.assertEqual(len(result["preferences"]), len(CRITERIA))
        self.assertIn("include", result["brands"])
        self.assertIn("exclude", result["brands"])

    def test_error_message_does_not_leak_key(self):
        with mock.patch.dict(os.environ, {"LLM_API_KEY": "sk-secret-value"}, clear=False):
            result = extract_needs("", classifier=MockClassifier())
        self.assertNotIn("sk-secret-value", str(result["meta"]))


class StatusDegradedTests(unittest.TestCase):
    """拿到回复但字段非法：降级而不静默。"""

    def test_illegal_choice_becomes_unmentioned_and_asks(self):
        payload = _payload()
        payload["judgements"][0]["choice"] = "超级重要"  # 不在 CHOICES 里
        result = extract_needs("随便买个手机", classifier=_StubClassifier(payload))
        self.assertEqual(result["status"], STATUS_DEGRADED)
        weight = {i["criterion"]: i for i in result["preferences"]}["weight"]
        self.assertEqual(weight["level"], "未提及")
        fields = [i["field"] for i in result["clarifications"]]
        self.assertIn("preferences.weight", fields)

    def test_missing_criterion_is_reported(self):
        payload = _payload()
        payload["judgements"] = [
            item for item in payload["judgements"] if item["criterion"] != "photo"
        ]
        result = extract_needs("随便买个手机", classifier=_StubClassifier(payload))
        self.assertEqual(result["status"], STATUS_DEGRADED)
        fields = [i["field"] for i in result["clarifications"]]
        self.assertIn("preferences.photo", fields)

    def test_fabricated_evidence_degrades_but_keeps_level(self):
        """evidence 编造 → 降级，但**不丢弃 level**（level 是模型判断）。"""
        text = "想要轻一点的手机"
        payload = _payload()
        payload["judgements"][0].update(
            {"choice": "重要", "evidence": "我要最轻的", "verdict": "extracted"}
        )
        result = extract_needs(text, classifier=_StubClassifier(payload))
        self.assertEqual(result["status"], STATUS_DEGRADED)
        weight = {i["criterion"]: i for i in result["preferences"]}["weight"]
        self.assertEqual(weight["level"], "重要")
        reasons = " ".join(i["reason"] for i in result["clarifications"])
        self.assertIn("证据与原文不符", reasons)

    def test_verbatim_evidence_stays_ok(self):
        text = "想要轻一点的手机"
        payload = _payload()
        payload["judgements"][1].update(
            {"choice": "一般", "evidence": "轻一点", "verdict": "extracted"}
        )
        result = extract_needs(text, classifier=_StubClassifier(payload))
        self.assertEqual(result["status"], STATUS_OK)
        self.assertEqual(validate_result(result), [])

    def test_empty_raw_does_not_produce_false_alarms(self):
        """没有原始回复可比对时不得误报——宁可漏报也不误报。"""

        class _NoRaw:
            name = "no-raw"

            def classify(self, text):
                from providers.jev import JevResult

                return JevResult(
                    judgements=(),
                    hard_hints={},
                    excluded_brands=(),
                    asks=(),
                    unsupported=(),
                    provider="no-raw",
                    model_version="",
                    raw={},
                )

        result = extract_needs("随便买个手机", classifier=_NoRaw())
        fields = [i["field"] for i in result["clarifications"]]
        self.assertNotIn("preferences.weight", fields)


class ValidateResultTests(unittest.TestCase):
    """`validate_result` 必须能查出各类结构性问题。"""

    def setUp(self):
        self.good = extract_needs(REFERENCE_TEXT, classifier=MockClassifier())

    def _clone(self):
        import copy

        return copy.deepcopy(self.good)

    def _problems(self, mutate):
        result = self._clone()
        mutate(result)
        return validate_result(result)

    def test_good_result_has_no_problems(self):
        self.assertEqual(validate_result(self.good), [])

    def test_missing_top_level_key(self):
        problems = self._problems(lambda r: r.pop("clarifications"))
        self.assertTrue(any("缺少顶层字段" in p for p in problems))

    def test_illegal_status(self):
        problems = self._problems(lambda r: r.__setitem__("status", "fine"))
        self.assertTrue(any("status 取值非法" in p for p in problems))

    def test_vague_budget_turned_into_hard_cap_is_flagged(self):
        """把「2000 左右」当硬上限，正是要防的红线错误。"""

        def mutate(r):
            r["budget"]["max"] = r["budget"]["reference"]

        problems = self._problems(mutate)
        self.assertTrue(any("却设了硬边界" in p for p in problems))

    def test_explicit_cap_matching_reference_is_not_flagged(self):
        """「预算 3000 以内」→ reference 与 max 同为 3000 是**合法**的。

        契约 §4.1 明确如此，不能因为两者相等就误报。
        """
        result = extract_needs("预算 3000 以内，别太重", classifier=MockClassifier())
        self.assertEqual(result["budget"]["reference"], 3000.0)
        self.assertEqual(result["budget"]["max"], 3000.0)
        self.assertEqual(validate_result(result), [])

    def test_explicit_range_is_not_flagged(self):
        result = extract_needs("预算 4000 到 6000", classifier=MockClassifier())
        self.assertEqual(validate_result(result), [])

    def test_screen_size_with_vague_word_is_not_a_budget(self):
        """「屏幕 6.4 英寸左右」不得被当成含糊预算。"""
        result = extract_needs(
            "预算 6000 以内，屏幕 6.4 英寸左右", classifier=MockClassifier()
        )
        self.assertEqual(result["budget"]["max"], 6000.0)
        self.assertEqual(validate_result(result), [])

    def test_chinese_vague_number_is_flagged(self):
        """「四五千」被当成硬区间同样要拦。"""

        def mutate(r):
            r["input_text"] = "价格四五千"
            r["budget"]["min"] = 4000
            r["budget"]["max"] = 5000

        problems = self._problems(mutate)
        self.assertTrue(any("却设了硬边界" in p for p in problems))

    def _multiturn(self, input_text, **budget):
        result = extract_needs("占位", classifier=MockClassifier())
        result["input_text"] = input_text
        result["budget"].update({"reference": None, "min": None, "max": None})
        result["budget"].update(budget)
        return result

    def test_explicit_cap_after_vague_turn_is_not_flagged(self):
        """多轮回归：用户后补的明确上限必须生效。

        原先的红线检查拿整段拼接历史去找含糊短语，于是第二轮用户补了
        「最多 4000」之后，第一轮那句「三千左右」仍留在原文里，`max=4000`
        被误判成「含糊预算被封顶」。后果是界面直接拒绝一条合法需求
        （Streamlit 与 Web 服务同时中招），用户答完追问反而出不了机型。
        """
        joined = "1. 预算三千左右\n2. 最多 4000"
        problems = validate_result(self._multiturn(joined, max=4000.0))
        self.assertEqual([p for p in problems if "却设了硬边界" in p], [])

    def test_same_number_confirmed_in_a_later_turn_is_not_flagged(self):
        # 「三千左右」→ 用户随后确认「那就 3000」：数字相同，但边界来自新的一句。
        joined = "1. 预算三千左右\n2. 那就 3000 吧"
        problems = validate_result(self._multiturn(joined, max=3000.0))
        self.assertEqual([p for p in problems if "却设了硬边界" in p], [])

    def test_vague_phrase_in_the_last_turn_is_still_flagged(self):
        # 反向：含糊短语就在用户刚说的那句里，被当成边界仍要拦。
        joined = "1. 预算 4000 到 6000\n2. 其实三千左右也行"
        problems = validate_result(self._multiturn(joined, min=3000.0, max=6000.0))
        self.assertTrue(any("却设了硬边界" in p for p in problems))

    def test_single_turn_vague_plus_explicit_cap_is_not_flagged(self):
        # 一句话里同时有参考值和明确上限，明确的那个说了算。
        text = "预算 3000 左右，最多 4000"
        problems = validate_result(
            self._multiturn(text, reference=3000.0, max=4000.0)
        )
        self.assertEqual([p for p in problems if "却设了硬边界" in p], [])

    def test_negative_budget_is_flagged(self):
        problems = self._problems(lambda r: r["budget"].__setitem__("max", -1))
        self.assertTrue(any("必须大于 0" in p for p in problems))

    def test_contradictory_budget_is_flagged(self):
        """下界高于上界是模型会犯的错，必须在契约层拦住。"""

        def mutate(r):
            r["budget"]["min"] = 9000
            r["budget"]["max"] = 3000

        problems = self._problems(mutate)
        self.assertTrue(any("条件自相矛盾" in p for p in problems))

    def test_normal_range_is_not_flagged(self):
        result = extract_needs("预算 4000 到 6000", classifier=MockClassifier())
        self.assertEqual(validate_result(result), [])

    def test_budget_type_must_be_number_or_null(self):
        problems = self._problems(lambda r: r["budget"].__setitem__("reference", "2000"))
        self.assertTrue(any("必须是数字或 null" in p for p in problems))

    def test_brand_overlap_is_flagged(self):
        def mutate(r):
            r["brands"]["include"] = ["小米"]
            r["brands"]["exclude"] = ["小米"]

        problems = self._problems(mutate)
        self.assertTrue(any("同时出现在白名单与排除名单" in p for p in problems))

    def test_preferences_must_cover_every_criterion(self):
        problems = self._problems(lambda r: r["preferences"].pop())
        self.assertTrue(any("必须恰好包含" in p for p in problems))

    def test_invalid_level_is_flagged(self):
        problems = self._problems(lambda r: r["preferences"][0].__setitem__("level", "高"))
        self.assertTrue(any("不在" in p for p in problems))

    def test_unknown_unsupported_value_is_flagged(self):
        def mutate(r):
            r["unsupported_needs"] = [{"need": "颜值", "evidence": "好看"}]

        problems = self._problems(mutate)
        self.assertTrue(any("未约定的取值" in p for p in problems))

    def test_clarification_needs_a_question(self):
        def mutate(r):
            r["clarifications"] = [{"field": "budget.max", "question": "", "reason": "x"}]

        problems = self._problems(mutate)
        self.assertTrue(any("缺少 question" in p for p in problems))

    def test_screen_out_of_range_is_flagged(self):
        problems = self._problems(lambda r: r.__setitem__("target_screen", 99.0))
        self.assertTrue(any("超出 3.0–10.0" in p for p in problems))

    def test_ok_status_must_not_carry_error(self):
        def mutate(r):
            r["meta"]["error"] = "有错"

        problems = self._problems(mutate)
        self.assertTrue(any("却带有 meta.error" in p for p in problems))

    def test_unsupported_values_are_the_agreed_set(self):
        # 影像 / 处理器性能已由 photo / chipset 维度覆盖，不再属于未覆盖集合。
        self.assertNotIn("影像", UNSUPPORTED_VALUES)
        self.assertNotIn("处理器性能", UNSUPPORTED_VALUES)
        self.assertIn("续航", UNSUPPORTED_VALUES)
        self.assertNotIn("颜值", UNSUPPORTED_VALUES)


class ReferenceIsNotAFilterTests(unittest.TestCase):
    """参考预算**永远不参与筛选**——第二条红线。"""

    def test_budget_reference_is_not_in_filter_kwargs(self):
        hard = HardConstraints(budget_reference=2000.0)
        self.assertNotIn("budget_reference", hard.to_filter_kwargs())

    def test_reference_alone_produces_no_filter(self):
        """只有参考值时，筛选条件里不该出现任何预算键。"""
        hard = HardConstraints(budget_reference=2000.0)
        kwargs = hard.to_filter_kwargs()
        self.assertNotIn("budget_min", kwargs)
        self.assertNotIn("budget_max", kwargs)

    def test_explicit_bounds_do_reach_the_filter(self):
        hard = HardConstraints(budget_min=2000.0, budget_max=3000.0)
        kwargs = hard.to_filter_kwargs()
        self.assertEqual(kwargs["budget_min"], 2000.0)
        self.assertEqual(kwargs["budget_max"], 3000.0)

    def test_reference_must_be_positive(self):
        with self.assertRaises(Exception):
            HardConstraints(budget_reference=0)

    def test_reference_does_not_affect_bounds_validation(self):
        """参考值可以落在区间之外，不应触发 min>max 之类的校验。"""
        hard = HardConstraints(budget_min=3000.0, budget_max=5000.0, budget_reference=2000.0)
        self.assertEqual(hard.budget_reference, 2000.0)


class BudgetMaxBackstopTests(unittest.TestCase):
    """预算上界的追问口径（2026-10-25 修订）。

    旧口径：只要 `budget_max is None` 就追问「最多能接受到多少」。
    新口径：用户给了锚点（「三千左右」）时**不再追问**，改由
    `bridge._snap_budget` 按非对称阶梯推断区间并回显（见
    `test_bridge.BridgeBudgetInferenceTests`）；既无锚点也无上界时才必须问。

    不变的部分：判定从**抽取出的字段**推导，不看模型的 `asks`——实测 DeepSeek 会
    把数字记进 `budget_reference` 却漏填 `asks`，只信模型就永远不问。
    """

    def _clarifications(self, **hard):
        """用给定硬约束跑一次契约，返回 clarifications 列表。

        注意先把 `asks` 摘出来再展开 `hard`，否则 `**hard` 会把它也塞进
        `hard_constraints` 里。
        """
        asks = hard.pop("asks", [])
        body = _payload(
            hard_constraints={
                "budget_min": None,
                "budget_max": None,
                "budget_reference": None,
                "min_storage": None,
                "brands": [],
                "target_screen": None,
                **hard,
            },
            asks=asks,
        )
        result = extract_needs(REFERENCE_TEXT, classifier=_StubClassifier(body))
        return result["clarifications"]

    def test_vague_budget_with_reference_is_not_asked(self):
        """有锚点就不追问：改为推断区间 + 回显，用户直接改控件更省事。"""
        fields = [item["field"] for item in self._clarifications(budget_reference=3000)]
        self.assertNotIn("budget.max", fields)

    def test_model_asks_do_not_reintroduce_the_question_when_reference_exists(self):
        """模型自己填了 asks 也不能改变口径，否则行为取决于模型输出。"""
        fields = [
            item["field"]
            for item in self._clarifications(budget_reference=3000, asks=["budget_max"])
        ]
        self.assertNotIn("budget.max", fields)

    def test_no_budget_at_all_also_asks(self):
        """既无锚点也无边界：程序没有任何依据，必须问。"""
        items = self._clarifications()
        self.assertIn("budget.max", [item["field"] for item in items])

    def test_lower_bound_only_still_asks(self):
        """只有下界没有上界：没有可推断的中心，必须问上限。"""
        fields = [item["field"] for item in self._clarifications(budget_min=8000)]
        self.assertIn("budget.max", fields)

    def test_explicit_upper_bound_never_asks(self):
        fields = [item["field"] for item in self._clarifications(budget_max=5000)]
        self.assertNotIn("budget.max", fields)

    def test_explicit_upper_bound_does_not_ask(self):
        """「3000 以内」是明确上界，不该再问。"""
        items = self._clarifications(budget_reference=3000, budget_max=3000)
        self.assertNotIn("budget.max", [i["field"] for i in items])

    def test_explicit_range_does_not_ask(self):
        items = self._clarifications(budget_min=2000, budget_max=4000)
        self.assertNotIn("budget.max", [i["field"] for i in items])

    def test_model_asks_are_ignored_when_reference_exists(self):
        """有锚点时，模型自己列 budget_max 也不追问——否则行为取决于模型输出。"""
        body = _payload(
            hard_constraints={
                "budget_min": None,
                "budget_max": None,
                "budget_reference": 3000,
                "min_storage": None,
                "brands": [],
                "target_screen": None,
            },
            asks=["budget_max"],
        )
        result = extract_needs(REFERENCE_TEXT, classifier=_StubClassifier(body))
        fields = [item["field"] for item in result["clarifications"]]
        self.assertNotIn("budget.max", fields)

    def test_budget_question_comes_first(self):
        """预算最影响候选集，排在待确认清单最前。

        用「只有下界没有上界」触发追问——有锚点的场景已不再追问。
        """
        body = _payload(
            hard_constraints={
                "budget_min": 8000,
                "budget_max": None,
                "budget_reference": None,
                "min_storage": None,
                "brands": [],
                "target_screen": None,
            },
            asks=["min_storage"],
        )
        result = extract_needs(REFERENCE_TEXT, classifier=_StubClassifier(body))
        fields = [item["field"] for item in result["clarifications"]]
        self.assertEqual(fields[0], "budget.max")

    def test_still_passes_validate_result(self):
        """加兜底不能破坏契约结构。"""
        body = _payload(
            hard_constraints={
                "budget_min": None,
                "budget_max": None,
                "budget_reference": 3000,
                "min_storage": None,
                "brands": [],
                "target_screen": None,
            }
        )
        result = extract_needs(REFERENCE_TEXT, classifier=_StubClassifier(body))
        self.assertEqual(validate_result(result), [])


class LevelVocabularyTests(unittest.TestCase):
    """等级词表必须与算法同学共用同一份。"""

    def test_choices_are_the_agreed_five(self):
        self.assertEqual(CHOICES, ("未提及", "不在意", "一般", "重要", "非常重要"))

    def test_unmentioned_and_indifferent_are_distinct(self):
        self.assertNotEqual("未提及", "不在意")

    def test_output_never_contains_numeric_weight(self):
        result = extract_needs(REFERENCE_TEXT, classifier=MockClassifier())
        for item in result["preferences"]:
            self.assertNotIn("level_value", item)
            self.assertNotIn("weight", item)


if __name__ == "__main__":
    unittest.main()
