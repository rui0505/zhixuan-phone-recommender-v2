"""providers/recommend.py 的单测。

推荐语是**唯一一处让大模型自由生成文字**的地方，所以这里要钉死三件事：

1. 事实清单必须把引擎给的真实机型完整带上——模型没有素材就只能编。
2. 任何失败都不抛异常，返回空串让界面退回模板文案。
3. 提示词里必须明确禁止编造型号与参数，且版本号写进 prompt。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from providers.jev import JevUnavailable  # noqa: E402
from providers.recommend import (  # noqa: E402
    Brief,
    LLMWriter,
    RECOMMEND_PROMPT_VERSION,
    RECOMMEND_SYSTEM_PROMPT,
    fallback_text,
    render_facts,
    write_recommendation,
)
from tests.fake_openai import FakeOpenAIServer  # noqa: E402

LLM_VARS = ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY", "LLM_THINKING")


def a_model(name="小米 15", price=3999.0, score=0.81, **overrides):
    model = {
        "name": name,
        "config": "12GB / 256GB",
        "chip": "骁龙 8 Gen 3",
        "price": price,
        "weight": 191.0,
        "screen": 6.36,
        "refresh": 120.0,
        "storage": 256.0,
        "score": score,
        "reason": f"比预算上限低 {5000 - price:,.0f} 元",
    }
    model.update(overrides)
    return model


def a_brief(**overrides):
    brief = Brief(
        models=[a_model(), a_model("Redmi K80", 2599.0, 0.77)],
        needs_summary="品牌只看：小米；最低存储 256GB；偏好：机身更轻=重要",
        pending=["预算上界"],
        unsupported=["影像"],
        candidate_configs=23,
        total_configs=156,
        budget=(1000.0, 5000.0),
    )
    for key, value in overrides.items():
        setattr(brief, key, value)
    return brief


class RenderFactsTests(unittest.TestCase):
    """事实清单是模型的唯一素材，漏了什么模型就可能编什么。"""

    def test_lists_every_model_name(self):
        facts = render_facts(a_brief())
        self.assertIn("小米 15", facts)
        self.assertIn("Redmi K80", facts)

    def test_carries_real_numbers(self):
        facts = render_facts(a_brief())
        self.assertIn("3,999", facts)
        self.assertIn("191", facts)

    def test_carries_pending_and_unsupported(self):
        facts = render_facts(a_brief())
        self.assertIn("预算上界", facts)
        self.assertIn("影像", facts)

    def test_warns_that_the_list_is_exhaustive(self):
        facts = render_facts(a_brief())
        self.assertIn("只能提下面这些", facts)

    def test_labels_score_as_relative(self):
        """匹配度不得被说成质量分，清单里就要写清楚。"""
        facts = render_facts(a_brief())
        self.assertIn("仅在本候选集内有意义", facts)

    def test_handles_empty_brief(self):
        facts = render_facts(Brief())
        self.assertIn("未提取到明确需求", facts)

    def test_handles_missing_chip(self):
        facts = render_facts(Brief(models=[a_model(chip="")]))
        self.assertIn("数据缺失", facts)


class PromptTests(unittest.TestCase):
    """提示词必须把「不许编造」写成硬约束。"""

    def test_version_is_declared_in_the_prompt(self):
        self.assertIn(RECOMMEND_PROMPT_VERSION, RECOMMEND_SYSTEM_PROMPT)

    def test_forbids_models_outside_the_list(self):
        self.assertIn("不得提到清单之外的任何机型", RECOMMEND_SYSTEM_PROMPT)

    def test_forbids_inventing_specs(self):
        self.assertIn("不得编造任何参数", RECOMMEND_SYSTEM_PROMPT)

    def test_forbids_misreading_the_score(self):
        self.assertIn("相对得分", RECOMMEND_SYSTEM_PROMPT)

    def test_forbids_substitute_metrics(self):
        """拍照 ≠ 像素，续航 ≠ 电池容量。"""
        self.assertIn("不得用别的指标替代", RECOMMEND_SYSTEM_PROMPT)


class WriteRecommendationTests(unittest.TestCase):
    """`write_recommendation` 永不抛异常。"""

    def test_empty_brief_returns_a_reason(self):
        text, error = write_recommendation(Brief())
        self.assertEqual(text, "")
        self.assertIn("没有可推荐的机型", error)

    def test_missing_config_returns_a_reason(self):
        saved = {key: os.environ.pop(key, None) for key in LLM_VARS}
        self.addCleanup(
            lambda: [os.environ.__setitem__(k, v) for k, v in saved.items() if v]
        )
        text, error = write_recommendation(a_brief())
        self.assertEqual(text, "")
        self.assertIn("LLM_BASE_URL", error)

    def test_writer_exception_is_swallowed(self):
        class Boom:
            def write(self, brief):
                raise RuntimeError("上游炸了")

        text, error = write_recommendation(a_brief(), writer=Boom())
        self.assertEqual(text, "")
        self.assertIn("RuntimeError", error)

    def test_jev_unavailable_is_swallowed(self):
        class Unavailable:
            def write(self, brief):
                raise JevUnavailable("服务不可用")

        text, error = write_recommendation(a_brief(), writer=Unavailable())
        self.assertEqual(text, "")
        self.assertEqual(error, "服务不可用")

    def test_empty_model_reply_is_reported(self):
        class Silent:
            def write(self, brief):
                return "   "

        text, error = write_recommendation(a_brief(), writer=Silent())
        self.assertEqual(text, "")
        self.assertIn("空内容", error)

    def test_successful_write_returns_text(self):
        class Stub:
            def write(self, brief):
                return "这两款都符合你的条件。"

        text, error = write_recommendation(a_brief(), writer=Stub())
        self.assertEqual(text, "这两款都符合你的条件。")
        self.assertEqual(error, "")


class CleanTests(unittest.TestCase):
    """模型常加围栏和标题，要剥掉。"""

    def _write(self, raw):
        class Stub:
            def write(self, brief):
                return raw

        return write_recommendation(a_brief(), writer=Stub())[0]

    def test_strips_code_fence(self):
        self.assertEqual(self._write("```\n正 文\n```"), "正 文")

    def test_strips_markdown_heading(self):
        self.assertEqual(self._write("## 推荐理由\n正文"), "推荐理由\n正文")

    def test_plain_text_untouched(self):
        self.assertEqual(self._write("就是一句话。"), "就是一句话。")


class WriterConfigTests(unittest.TestCase):
    def test_missing_base_url_raises(self):
        saved = {key: os.environ.pop(key, None) for key in LLM_VARS}
        self.addCleanup(
            lambda: [os.environ.__setitem__(k, v) for k, v in saved.items() if v]
        )
        with self.assertRaises(JevUnavailable):
            LLMWriter()

    def test_missing_model_raises(self):
        with self.assertRaises(JevUnavailable):
            LLMWriter(base_url="https://example.invalid/v1", model="")

    def test_endpoint_join(self):
        writer = LLMWriter(base_url="https://example.invalid/v1", model="m")
        self.assertEqual(writer.endpoint, "https://example.invalid/v1/chat/completions")

    def test_trailing_slash_not_doubled(self):
        writer = LLMWriter(base_url="https://example.invalid/v1/", model="m")
        self.assertEqual(writer.endpoint, "https://example.invalid/v1/chat/completions")

    def test_missing_key_raises(self):
        writer = LLMWriter(
            base_url="https://example.invalid/v1", model="m", api_key_env="NO_SUCH_KEY"
        )
        with self.assertRaises(JevUnavailable):
            writer.write(a_brief())

    def test_thinking_disabled_by_default(self):
        """与 LLMClassifier 同一套开关：默认关。

        推荐语是短散文，不需要多步推理；而 thinking 模式下 temperature
        会被静默忽略，这里设的 0.3 就白设了。
        """
        saved = os.environ.pop("LLM_THINKING", None)
        self.addCleanup(
            lambda: os.environ.__setitem__("LLM_THINKING", saved) if saved else None
        )
        writer = LLMWriter(base_url="https://example.invalid/v1", model="m")
        self.assertFalse(writer.thinking)

    def test_thinking_env_enables(self):
        os.environ["LLM_THINKING"] = "1"
        self.addCleanup(lambda: os.environ.pop("LLM_THINKING", None))
        writer = LLMWriter(base_url="https://example.invalid/v1", model="m")
        self.assertTrue(writer.thinking)

    def test_thinking_argument_beats_env(self):
        os.environ["LLM_THINKING"] = "1"
        self.addCleanup(lambda: os.environ.pop("LLM_THINKING", None))
        writer = LLMWriter(base_url="https://example.invalid/v1", model="m", thinking=False)
        self.assertFalse(writer.thinking)


class EndToEndTests(unittest.TestCase):
    """走真实 HTTP 打到本地假服务，验证线格式与「不编造型号」。"""

    def setUp(self):
        self.server = FakeOpenAIServer()
        self.server.__enter__()
        self.addCleanup(self._teardown)

    def _teardown(self):
        self.server.__exit__(None, None, None)
        for key in LLM_VARS:
            os.environ.pop(key, None)

    def test_round_trip(self):
        brief = a_brief()
        text, error = write_recommendation(brief)
        self.assertEqual(error, "")
        self.assertTrue(text)
        for model in brief.models:
            self.assertIn(model["name"], text)

    def test_request_shape(self):
        write_recommendation(a_brief())
        self.assertEqual(len(self.server.requests), 1)
        request = self.server.requests[0]
        self.assertEqual(request["kind"], "recommend")
        self.assertEqual(request["path"], "/v1/chat/completions")
        self.assertTrue(request["authorization"].startswith("Bearer "))
        system = request["payload"]["messages"][0]["content"]
        self.assertIn("解说员", system)

    def test_request_disables_thinking(self):
        """推荐语链路也要关掉 thinking，否则 temperature=0.3 会被静默忽略。"""
        write_recommendation(a_brief())
        payload = self.server.requests[0]["payload"]
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["temperature"], 0.3)

    def test_no_invented_models_in_the_reply(self):
        """假服务只会念清单里的机型；真实模型若编造，这里的断言就是防线。"""
        brief = a_brief()
        text, _ = write_recommendation(brief)
        for candidate in ("iPhone 99", "小米 99", "华为 Mate 999"):
            self.assertNotIn(candidate, text)
        for model in brief.models:
            self.assertIn(model["name"], text)

    def test_server_error_returns_reason_not_exception(self):
        self.server.__exit__(None, None, None)
        for key in LLM_VARS:
            os.environ.pop(key, None)
        self.server = FakeOpenAIServer(status=500)
        self.server.__enter__()
        text, error = write_recommendation(a_brief())
        self.assertEqual(text, "")
        self.assertTrue(error)


class FallbackTextTests(unittest.TestCase):
    """大模型不可用时的模板文案。"""

    def test_lists_models_and_prices(self):
        text = fallback_text(a_brief())
        self.assertIn("小米 15", text)
        self.assertIn("3,999", text)

    def test_says_it_is_a_template(self):
        text = fallback_text(a_brief())
        self.assertIn("模板文案", text)

    def test_mentions_pending_and_unsupported(self):
        text = fallback_text(a_brief())
        self.assertIn("预算上界", text)
        self.assertIn("影像", text)

    def test_empty_brief_explains_how_to_relax(self):
        text = fallback_text(Brief())
        self.assertIn("没有候选机型", text)


if __name__ == "__main__":
    unittest.main()
