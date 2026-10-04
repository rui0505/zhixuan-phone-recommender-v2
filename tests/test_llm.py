"""外部大模型 API 适配层测试（providers/llm.py）。

全部用假的 HTTP 响应，**不依赖网络、不消耗额度、不需要密钥**。
覆盖两类风险：

1. 解析健壮性：模型回复常被 ```json 围栏或解释文字包裹，必须能抠出 JSON；
2. 降级安全：模型输出非法值或编造证据时，绝不能让脏数据进入排序链路。
"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from providers.jev import (  # noqa: E402
    JevUnavailable,
    MockClassifier,
    parse_needs,
)
from providers.llm import (  # noqa: E402
    LLMClassifier,
    OPTIONAL_PAYLOAD_FIELDS,
    extract_json_object,
    thinking_from_env,
    thinking_payload,
)
from schemas import CHOICES, CRITERIA, VERDICT_EXTRACTED, VERDICT_INFERRED  # noqa: E402

VALID_BODY = {
    "judgements": [
        {"criterion": "price", "choice": "重要", "evidence": "越便宜越好", "verdict": "extracted"},
        {"criterion": "weight", "choice": "一般", "evidence": "别太重", "verdict": "extracted"},
        {"criterion": "storage", "choice": "未提及", "evidence": "", "verdict": "unknown"},
        {"criterion": "refresh", "choice": "未提及", "evidence": "", "verdict": "unknown"},
        {"criterion": "screen", "choice": "未提及", "evidence": "", "verdict": "unknown"},
    ],
    "hard_constraints": {
        "budget_min": None,
        "budget_max": None,
        "min_storage": 256,
        "brands": [],
        "target_screen": None,
    },
    "excluded_brands": [],
    "asks": ["budget_max"],
    "unsupported_needs": ["续航"],
}


def reply(payload, *, model="fake-model", fenced=False):
    """构造一个 OpenAI 兼容的 chat/completions 响应。"""
    content = json.dumps(payload, ensure_ascii=False)
    if fenced:
        content = "```json\n" + content + "\n```"
    return {
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": content}}],
    }


class FakeResponse:
    def __init__(self, body):
        self._raw = json.dumps(body, ensure_ascii=False).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def classifier(**kw):
    opts = {"base_url": "https://example.invalid/v1", "model": "fake-model"}
    opts.update(kw)
    return LLMClassifier(**opts)


class ExtractJsonTests(unittest.TestCase):
    """模型回复的解析健壮性。"""

    def test_plain_object(self):
        self.assertEqual(extract_json_object('{"a": 1}'), {"a": 1})

    def test_fenced_object(self):
        self.assertEqual(extract_json_object('```json\n{"a": 1}\n```'), {"a": 1})

    def test_object_with_surrounding_prose(self):
        text = '好的，结果如下：\n{"a": 1}\n希望有帮助。'
        self.assertEqual(extract_json_object(text), {"a": 1})

    def test_braces_inside_string_do_not_break_scan(self):
        """字符串里的花括号不能干扰括号平衡计数。"""
        text = '前言 {"note": "含 } 和 { 的文本", "b": 2} 后记'
        self.assertEqual(extract_json_object(text)["b"], 2)

    def test_escaped_quote_inside_string(self):
        text = '{"note": "他说 \\"你好\\"", "b": 3}'
        self.assertEqual(extract_json_object(text)["b"], 3)

    def test_no_json_raises(self):
        with self.assertRaises(ValueError):
            extract_json_object("这里没有 JSON")

    def test_unbalanced_raises(self):
        with self.assertRaises(ValueError):
            extract_json_object('{"a": 1')


class ConfigTests(unittest.TestCase):
    """配置缺失必须在构造/调用时明确失败，而不是静默跑空。"""

    def test_missing_base_url_raises(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(JevUnavailable):
                LLMClassifier(model="m")

    def test_missing_model_raises(self):
        # 清空环境变量，否则开发机上导出的 LLM_MODEL 会让这条用例失去意义
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(JevUnavailable):
                LLMClassifier(base_url="https://example.invalid/v1", model="")

    def test_missing_key_raises(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(JevUnavailable):
                classifier().classify("随便看看")

    def test_endpoint_join(self):
        self.assertEqual(
            classifier().endpoint, "https://example.invalid/v1/chat/completions"
        )

    def test_trailing_slash_not_doubled(self):
        c = classifier(base_url="https://example.invalid/v1/")
        self.assertEqual(c.endpoint, "https://example.invalid/v1/chat/completions")


class ClassifyTests(unittest.TestCase):
    """正常路径与降级路径。"""

    def setUp(self):
        self.env = mock.patch.dict("os.environ", {"LLM_API_KEY": "test-key"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def _patch_reply(self, body):
        return mock.patch(
            "providers.llm.urllib.request.urlopen",
            return_value=FakeResponse(body),
        )

    def test_valid_reply_is_normalised(self):
        with self._patch_reply(reply(VALID_BODY)):
            result = classifier().classify("预算三千左右，越便宜越好，别太重，256G 起步，续航要够")
        by = {item.criterion: item for item in result.judgements}
        self.assertEqual(by["price"].choice, "重要")
        self.assertEqual(by["weight"].choice, "一般")
        self.assertEqual(by["storage"].choice, "未提及")
        self.assertEqual(result.provider, "llm-api")
        self.assertEqual(result.hard_hints["min_storage"], 256)
        self.assertIn("续航", result.unsupported)

    def test_all_criteria_present_even_if_model_omits(self):
        """模型漏字段时，缺失维度必须补成未提及，不能少给。"""
        partial = dict(VALID_BODY)
        partial["judgements"] = [VALID_BODY["judgements"][0]]
        with self._patch_reply(reply(partial)):
            result = classifier().classify("越便宜越好")
        self.assertEqual(len(result.judgements), len(CRITERIA))
        self.assertEqual(
            {i.criterion for i in result.judgements}, set(CRITERIA)
        )

    def test_illegal_choice_downgraded_to_unmentioned(self):
        """非法取值绝不猜，退回未提及 + unknown。"""
        bad = json.loads(json.dumps(VALID_BODY))
        bad["judgements"][0]["choice"] = "极其重要"  # 不在 CHOICES 里
        with self._patch_reply(reply(bad)):
            result = classifier().classify("越便宜越好")
        price = result.judgement_for("price")
        self.assertEqual(price.choice, "未提及")
        self.assertEqual(price.verdict, "unknown")

    def test_fabricated_evidence_downgraded_to_inferred(self):
        """声称 extracted 但证据不在原文 → 降级为 inferred，交由用户确认。"""
        bad = json.loads(json.dumps(VALID_BODY))
        bad["judgements"][0]["evidence"] = "这段原文里根本没有"
        with self._patch_reply(reply(bad)):
            result = classifier().classify("越便宜越好")
        price = result.judgement_for("price")
        self.assertEqual(price.verdict, VERDICT_INFERRED)
        # choice 保留：证据可疑不等于判断错误
        self.assertEqual(price.choice, "重要")

    def test_genuine_evidence_stays_extracted(self):
        with self._patch_reply(reply(VALID_BODY)):
            result = classifier().classify("越便宜越好，别太重")
        self.assertEqual(result.judgement_for("price").verdict, VERDICT_EXTRACTED)

    def test_fenced_reply_is_parsed(self):
        with self._patch_reply(reply(VALID_BODY, fenced=True)):
            result = classifier().classify("越便宜越好")
        self.assertEqual(result.judgement_for("price").choice, "重要")

    def test_non_json_reply_raises(self):
        with self._patch_reply(
            {"choices": [{"message": {"content": "抱歉，我无法完成"}}]}
        ):
            with self.assertRaises(JevUnavailable):
                classifier().classify("随便看看")

    def test_empty_choices_raises(self):
        with self._patch_reply({"choices": []}):
            with self.assertRaises(JevUnavailable):
                classifier().classify("随便看看")

    def test_content_as_parts_array(self):
        """部分端点把 content 返回成分段数组。"""
        payload = reply(VALID_BODY)
        payload["choices"][0]["message"]["content"] = [
            {"type": "text", "text": json.dumps(VALID_BODY, ensure_ascii=False)}
        ]
        with self._patch_reply(payload):
            result = classifier().classify("越便宜越好")
        self.assertEqual(result.judgement_for("price").choice, "重要")

    def test_json_mode_400_falls_back_without_response_format(self):
        """端点不支持 response_format 时，去掉该字段重试而不是直接失败。"""
        seen = []

        def fake_urlopen(request, timeout=None):
            body = json.loads(request.data.decode("utf-8"))
            seen.append("response_format" in body)
            if "response_format" in body:
                raise urllib_error_400()
            return FakeResponse(reply(VALID_BODY))

        with mock.patch("providers.llm.urllib.request.urlopen", side_effect=fake_urlopen):
            result = classifier().classify("越便宜越好")

        self.assertEqual(seen, [True, False])
        self.assertEqual(result.judgement_for("price").choice, "重要")

    def test_network_failure_raises(self):
        import urllib.error

        with mock.patch(
            "providers.llm.urllib.request.urlopen",
            side_effect=urllib.error.URLError("boom"),
        ):
            with self.assertRaises(JevUnavailable):
                classifier().classify("随便看看")

    def test_key_never_appears_in_error_message(self):
        """失败信息里不得泄露密钥。"""
        import urllib.error

        with mock.patch.dict("os.environ", {"LLM_API_KEY": "sk-super-secret"}):
            with mock.patch(
                "providers.llm.urllib.request.urlopen",
                side_effect=urllib.error.URLError("boom"),
            ):
                try:
                    classifier().classify("随便看看")
                except JevUnavailable as error:
                    self.assertNotIn("sk-super-secret", str(error))
                else:
                    self.fail("应当抛出 JevUnavailable")


def urllib_error_400():
    import urllib.error

    return urllib.error.HTTPError(
        "https://example.invalid/v1/chat/completions", 400, "Bad Request", {}, None
    )


class ThinkingModeTests(unittest.TestCase):
    """DeepSeek V4 默认开启 thinking，对本项目是纯亏，必须默认关掉。

    实测（deepseek-flash，同一 prompt）：
    - 开：4166 ms / 836 completion tokens（其中 640 是推理）
    - 关： 890 ms / 195 completion tokens，抽取结果逐字一致

    更要紧的是 thinking 模式下 `temperature` 会被静默忽略，而抽取链路
    依赖 `temperature=0` 保证可复现。
    """

    def test_payload_shape(self):
        self.assertEqual(thinking_payload(False), {"thinking": {"type": "disabled"}})
        self.assertEqual(thinking_payload(True), {"thinking": {"type": "enabled"}})

    def test_default_is_disabled(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            c = LLMClassifier(base_url="https://example.invalid/v1", model="m")
        self.assertFalse(c.thinking)

    def test_env_var_enables(self):
        for value in ("1", "true", "YES", "on"):
            with mock.patch.dict("os.environ", {"LLM_THINKING": value}):
                self.assertTrue(thinking_from_env(), f"{value!r} 应当视为开启")
        for value in ("", "0", "no", "off", "随便"):
            with mock.patch.dict("os.environ", {"LLM_THINKING": value}):
                self.assertFalse(thinking_from_env(), f"{value!r} 应当视为关闭")

    def test_explicit_argument_beats_env(self):
        with mock.patch.dict("os.environ", {"LLM_THINKING": "1"}):
            self.assertTrue(classifier(thinking=True).thinking)
            self.assertFalse(classifier(thinking=False).thinking)

    def test_sent_with_request(self):
        env = mock.patch.dict("os.environ", {"LLM_API_KEY": "test-key"})
        env.start()
        self.addCleanup(env.stop)
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured.update(json.loads(request.data.decode("utf-8")))
            return FakeResponse(reply(VALID_BODY))

        with mock.patch("providers.llm.urllib.request.urlopen", side_effect=fake_urlopen):
            classifier().classify("越便宜越好")

        self.assertEqual(captured["thinking"], {"type": "disabled"})
        # 抽取链路依赖 temperature=0 可复现；关掉 thinking 后它才真正生效
        self.assertEqual(captured["temperature"], 0.0)


class OptionalFieldDegradationTests(unittest.TestCase):
    """不同端点认的可选字段不一样，400 时要逐项剥掉，而不是整条失败。

    否则换一家供应商就整条链路挂掉——而这些字段都不是必需的：
    `response_format` 丢了还能从散文里抠 JSON，`thinking` 丢了只损失一点速度。
    """

    def setUp(self):
        env = mock.patch.dict("os.environ", {"LLM_API_KEY": "test-key"})
        env.start()
        self.addCleanup(env.stop)

    def test_drops_response_format_then_thinking(self):
        seen: list[tuple[bool, bool]] = []

        def fake_urlopen(request, timeout=None):
            body = json.loads(request.data.decode("utf-8"))
            seen.append(("response_format" in body, "thinking" in body))
            if "response_format" in body or "thinking" in body:
                raise urllib_error_400()
            return FakeResponse(reply(VALID_BODY))

        with mock.patch("providers.llm.urllib.request.urlopen", side_effect=fake_urlopen):
            result = classifier().classify("越便宜越好")

        self.assertEqual(seen, [(True, True), (False, True), (False, False)])
        self.assertEqual(result.judgement_for("price").choice, "重要")

    def test_drop_order_is_declared(self):
        """顺序有意义：先丢影响输出形态的，最后才丢影响成本的。"""
        self.assertEqual(OPTIONAL_PAYLOAD_FIELDS, ("response_format", "thinking"))

    def test_all_400_still_raises_without_leaking_key(self):
        with mock.patch.dict("os.environ", {"LLM_API_KEY": "sk-super-secret"}):
            with mock.patch(
                "providers.llm.urllib.request.urlopen",
                side_effect=lambda *a, **k: urllib_error_400(),
            ):
                try:
                    classifier().classify("越便宜越好")
                except JevUnavailable as error:
                    self.assertNotIn("sk-super-secret", str(error))
                else:
                    self.fail("全部字段被剥掉后仍 400，应当抛 JevUnavailable")

    def test_json_mode_off_skips_response_format(self):
        """`json_mode=False` 时不带 response_format，只可能丢 thinking。"""
        seen: list[bool] = []

        def fake_urlopen(request, timeout=None):
            body = json.loads(request.data.decode("utf-8"))
            seen.append("response_format" in body)
            if "thinking" in body:
                raise urllib_error_400()
            return FakeResponse(reply(VALID_BODY))

        with mock.patch("providers.llm.urllib.request.urlopen", side_effect=fake_urlopen):
            classifier(json_mode=False).classify("越便宜越好")

        self.assertEqual(seen, [False, False])


class ContractTests(unittest.TestCase):
    """与内核契约的一致性：换实现不得改变调用方代码。"""

    def test_parse_needs_accepts_llm_classifier(self):
        env = mock.patch.dict("os.environ", {"LLM_API_KEY": "test-key"})
        env.start()
        self.addCleanup(env.stop)
        with mock.patch(
            "providers.llm.urllib.request.urlopen",
            return_value=FakeResponse(reply(VALID_BODY)),
        ):
            needs = parse_needs("越便宜越好，256G 起步", classifier=classifier())
        self.assertEqual(needs.hard.min_storage, 256)
        self.assertEqual(needs.preference_for("price").choice, "重要")
        # 默认未确认，必须过用户确认页
        self.assertFalse(needs.preference_for("price").confirmed)

    def test_every_choice_is_within_choices(self):
        with mock.patch.dict("os.environ", {"LLM_API_KEY": "k"}):
            with mock.patch(
                "providers.llm.urllib.request.urlopen",
                return_value=FakeResponse(reply(VALID_BODY)),
            ):
                result = classifier().classify("越便宜越好")
        for item in result.judgements:
            self.assertIn(item.choice, CHOICES)

    def test_satisfies_classifier_protocol(self):
        """结构上满足 PreferenceClassifier：有 name，有 classify。"""
        c = classifier()
        self.assertIsInstance(c.name, str)
        self.assertTrue(callable(c.classify))
        self.assertNotEqual(c.name, MockClassifier.name)


if __name__ == "__main__":
    unittest.main()
