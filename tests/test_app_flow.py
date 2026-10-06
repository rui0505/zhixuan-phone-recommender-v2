"""app.py 的端到端流程测试：对话抽取 → 确认 → 追问修正 → 出机型。

这是唯一能证明「抽取层 + 引擎 + 推荐语都接进了界面」的测试。纯逻辑单测
全绿也说明不了界面是通的。

用 `streamlit.testing.v1.AppTest` 无头跑真实脚本，配合本地假 OpenAI 服务
走真实 HTTP 往返，因此不需要密钥。

覆盖三条线：
1. 一轮说清 → 直接出机型（extract + recommend 两次请求）
2. 信息不全 → 先追问，补齐后再出机型
3. 大模型不可用 → 手动模式照常可用，不白屏
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

import streamlit as st

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from engine import load_data  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402
from tests.fake_openai import FakeOpenAIServer  # noqa: E402
from tests.isolation import LLM_VARS, IsolatedEnvTest  # noqa: E402

APP = str(BASE / "app.py")


def send(at: AppTest, text: str) -> AppTest:
    """在对话框里发一句话。"""
    at.chat_input[0].set_value(text)
    at.run()
    return at


def last_message(at: AppTest) -> dict:
    chat = at.session_state.get("chat") or []
    assert chat, "对话历史是空的"
    return chat[-1]


class ManualModeTests(IsolatedEnvTest):
    """没有大模型时必须优雅退回手动模式，而不是崩。"""

    def test_runs_without_llm_configured(self):
        at = AppTest.from_file(APP, default_timeout=60).run()
        self.assertEqual([e.value for e in at.exception], [])

    def test_chat_input_is_disabled(self):
        at = AppTest.from_file(APP, default_timeout=60).run()
        self.assertEqual(len(at.chat_input), 1)
        self.assertTrue(at.chat_input[0].disabled)

    def test_manual_sliders_still_present(self):
        at = AppTest.from_file(APP, default_timeout=60).run()
        self.assertEqual(len(at.slider), 7)  # 预算 + 屏幕 + 5 个权重
        self.assertEqual(len(at.selectbox), 1)
        self.assertGreaterEqual(len(at.multiselect), 1)

    def test_hint_explains_chat_needs_llm(self):
        at = AppTest.from_file(APP, default_timeout=60).run()
        infos = " ".join(i.value for i in at.info)
        self.assertIn("对话框需要接入大模型", infos)

    def test_results_still_render(self):
        at = AppTest.from_file(APP, default_timeout=60).run()
        self.assertIn("推荐前五款", [s.value for s in at.subheader])

    def test_no_understanding_panel_before_any_turn(self):
        at = AppTest.from_file(APP, default_timeout=60).run()
        self.assertNotIn("我对你需求的理解", [s.value for s in at.subheader])


class ChatFlowTests(IsolatedEnvTest):
    """真实抽取HTTP → 中文契约适配 → 确认 → 调整 → 实际TOPSIS。

    环境变量的保存/恢复交给 `IsolatedEnvTest`：它除了清空 `LLM_*`，还会把
    `LLM_ENV_FILE` 指向不存在的路径以屏蔽开发机的 `.env`。少了后半条，
    测试结果就取决于本机有没有配 `.env`。
    """

    def setUp(self):
        super().setUp()
        self.server = FakeOpenAIServer(enable_auto_provider=True)
        self.server.__enter__()
        st.cache_resource.clear()
        self.at = AppTest.from_file(APP, default_timeout=30).run()

    def tearDown(self):
        self.server.__exit__(None, None, None)
        super().tearDown()

    def button(self, text):
        return next(x for x in self.at.button if x.label == text)

    def check(self, text):
        return next(x for x in self.at.checkbox if x.label == text)

    def confirm(self):
        self.check('我已检查硬条件、默认偏好、屏幕目标和待澄清内容').check()
        self.button('确认需求并生成推荐').click().run()
        self.assertFalse(self.at.exception)

    def answer(self, value):
        next(x for x in self.at.radio if x.label == '你的选择').set_value(value)
        self.button('提交回答并更新推荐').click().run()
        self.assertFalse(self.at.exception)

    def expected_ranking(self):
        from preferences import filter_candidates
        from engine import rank
        session = self.at.session_state['blue_session']
        return rank(filter_candidates(load_data(), session['needs']),
                    session['current_weights'], session['needs']['target_screen_inches'] or 6.5)

    def test_requires_confirmation_before_ranking(self):
        send(self.at, '预算3000以内，最轻的手机，存储256GB起步')
        self.assertFalse(self.at.exception)
        self.assertEqual(self.at.session_state['preference_mode'], '需求确认与追问')
        self.assertEqual(len(self.at.tabs), 0)
        self.assertNotIn('active_ranking_ids', self.at.session_state)
        self.assertEqual([r['kind'] for r in self.server.requests], ['extract'])

    def test_unchecked_confirmation_is_rejected(self):
        send(self.at, '预算3000以内，最轻的手机')
        self.button('确认需求并生成推荐').click().run()
        self.assertTrue(self.at.error)
        self.assertEqual(len(self.at.tabs), 0)

    def test_chinese_levels_drive_weights_and_rank(self):
        send(self.at, '预算3000以内，最轻的手机，存储256GB起步')
        self.confirm()
        session = self.at.session_state['blue_session']
        self.assertEqual(session['needs']['preferences']['weight'], 'very_important')
        self.assertAlmostEqual(session['current_weights']['weight'], .5)
        self.assertEqual(session['current_weights']['screen'], 0)
        self.assertEqual(self.at.session_state['active_ranking_ids'], self.expected_ranking().id.tolist())
        self.assertEqual(len(self.at.tabs), 3)

    def test_tradeoff_changes_real_ranking_weights(self):
        send(self.at, '预算3000以内，最轻的手机，存储256GB起步')
        self.confirm()
        self.answer('a')
        session = self.at.session_state['blue_session']
        self.assertAlmostEqual(session['current_weights']['weight'], 15/26)
        self.assertAlmostEqual(session['current_weights']['price'], 3/26)
        self.assertEqual(self.at.session_state['active_ranking_ids'], self.expected_ranking().id.tolist())
        self.assertEqual(len(session['history']), 1)

    def test_skip_and_rerun_do_not_reapply(self):
        send(self.at, '预算3000以内，最轻的手机')
        self.confirm()
        before = dict(self.at.session_state['blue_session']['current_weights'])
        self.answer('skip')
        self.at.run()
        self.assertEqual(self.at.session_state['blue_session']['current_weights'], before)
        self.assertEqual(len(self.at.session_state['blue_session']['history']), 1)

    def test_at_most_two_tradeoffs(self):
        send(self.at, '预算3000以内，最轻的手机')
        self.confirm()
        self.answer('a')
        self.answer('b')
        self.assertEqual(len(self.at.session_state['blue_session']['history']), 2)
        self.assertFalse(any(x.label == '你的选择' for x in self.at.radio))

    def test_no_hidden_hard_constraints(self):
        send(self.at, '想买2000元左右，轻一点的手机')
        self.confirm()
        from preferences import filter_candidates
        session = self.at.session_state['blue_session']
        self.assertIsNone(session['needs']['hard_constraints']['max_price'])
        self.assertIsNone(session['needs']['hard_constraints']['min_storage_gb'])
        self.assertEqual(len(filter_candidates(load_data(), session['needs'])), len(load_data()))

    def test_budget_min_and_excluded_brand_are_preserved(self):
        send(self.at, '预算2000到3000，不要华为，存储256GB起步')
        self.confirm()
        session = self.at.session_state['blue_session']
        hard = session['needs']['hard_constraints']
        self.assertEqual((hard['min_price'], hard['max_price']), (2000,3000))
        self.assertIn('华为', hard['excluded_brands'])
        ranked = self.expected_ranking()
        self.assertTrue(ranked.price.between(2000,3000).all())
        self.assertNotIn('华为', ranked.brand.tolist())

    def test_unsupported_need_is_visible(self):
        send(self.at, '预算3000以内，拍照好一点')
        self.assertIn('影像', ' '.join(x.value for x in self.at.warning))

    def test_all_zero_does_not_use_uniform_prior(self):
        send(self.at, '价格不重要，重量不重要，存储不重要，刷新率不重要，屏幕不重要')
        for selector in self.at.selectbox:
            selector.set_value('unimportant')
        self.confirm()
        self.assertTrue(self.at.error)
        self.assertIsNone(self.at.session_state['blue_session'])
        self.assertEqual(len(self.at.tabs), 0)

    def test_new_chat_invalidates_confirmation_and_history(self):
        send(self.at, '预算3000以内，最轻的手机')
        self.confirm()
        self.answer('a')
        send(self.at, '现在预算改成4000以内')
        self.assertFalse(self.at.exception)
        self.assertIsNone(self.at.session_state['blue_session'])
        self.assertNotIn('active_ranking_ids', self.at.session_state)
        self.assertEqual(len(self.at.tabs), 0)

    def test_reconfirm_invalid_conditions_invalidates_old_rank(self):
        send(self.at, '预算3000以内，最轻的手机')
        self.confirm()
        self.check('设置预算下限').check()
        next(x for x in self.at.number_input if '最低预算' in x.label).set_value(4000.)
        self.confirm()
        self.assertTrue(self.at.error)
        self.assertIsNone(self.at.session_state['blue_session'])
        self.assertNotIn('active_ranking_ids', self.at.session_state)

    def test_current_advice_uses_updated_ranking(self):
        send(self.at, '预算3000以内，最轻的手机')
        self.confirm()
        self.answer('a')
        expected = self.expected_ranking()
        self.button('生成当前推荐说明').click().run()
        self.assertFalse(self.at.exception)
        message = last_message(self.at)
        self.assertEqual(message['kind'], 'result')
        self.assertEqual(message['ranking_ids'], expected.id.tolist())
        self.assertEqual([m['name'] for m in message['models']],
                         expected.drop_duplicates('产品名称').head(5)['产品名称'].tolist())
        self.assertEqual([r['kind'] for r in self.server.requests], ['extract','recommend'])
        self.at.run()
        self.assertEqual(len(self.server.requests), 2)

    def test_clear_resets_both_modules(self):
        send(self.at, '预算3000以内，最轻的手机')
        self.confirm()
        self.button('清空对话与条件').click().run()
        self.assertFalse(self.at.exception)
        self.assertNotIn('extracted_needs', self.at.session_state)
        self.assertNotIn('blue_session', self.at.session_state)
        self.assertEqual(self.at.session_state['preference_mode'], '手动调整')
        self.assertEqual(len(self.at.slider), 7)

    def test_invalid_extraction_returns_to_manual_without_crashing(self):
        from unittest.mock import patch
        from needs_extraction import extract_needs
        from providers.jev import MockClassifier
        result = extract_needs('预算3000以内', classifier=MockClassifier())
        result['budget']['min'] = 4000
        with patch('needs_extraction.extract_needs', return_value=result):
            send(self.at, '预算3000以内')
        self.assertFalse(self.at.exception)
        self.assertEqual(last_message(self.at)['kind'], 'error')
        self.assertEqual(self.at.session_state['preference_mode'], '手动调整')
        self.assertEqual(len(self.at.tabs), 3)


class FallbackTests(IsolatedEnvTest):
    """大模型不可用时要给出可理解的错误，而不是白屏。"""

    def setUp(self):
        super().setUp()
        self.server = FakeOpenAIServer(status=500,enable_auto_provider=True)
        self.server.__enter__()
        st.cache_resource.clear()
        self.addCleanup(self._teardown)

    def _teardown(self):
        self.server.__exit__(None, None, None)
        for key in LLM_VARS:
            os.environ.pop(key, None)
        st.cache_resource.clear()

    def test_server_error_is_reported_in_chat(self):
        at = send(AppTest.from_file(APP, default_timeout=60).run(), "预算 3000 以内")
        self.assertEqual([e.value for e in at.exception], [])
        message = last_message(at)
        self.assertEqual(message["kind"], "error")
        self.assertIn("没能理解", message["text"])

    def test_error_is_also_recorded_for_the_sidebar(self):
        at = send(AppTest.from_file(APP, default_timeout=60).run(), "预算 3000 以内")
        self.assertTrue(at.session_state.get("parse_error"))

    def test_manual_mode_still_works_after_failure(self):
        at = send(AppTest.from_file(APP, default_timeout=60).run(), "预算 3000 以内")
        self.assertIn("推荐前五款", [s.value for s in at.subheader])

    def test_no_models_are_shown_on_failure(self):
        at = send(AppTest.from_file(APP, default_timeout=60).run(), "预算 3000 以内")
        self.assertIsNone(last_message(at).get("models"))


if __name__ == "__main__":
    unittest.main()
