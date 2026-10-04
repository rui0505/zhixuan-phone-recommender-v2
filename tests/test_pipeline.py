"""`pipeline` 的单元测试。

这一层是 Streamlit 与 Web 服务共用的「怎么算」，所以测试重点不是界面长什么样，
而是**契约与红线**：

- 含糊预算必须追问，不得被程序悄悄封顶；
- 机型只能来自引擎，不能凭空出现；
- 追问有上限，用户弃权后必须出结果，不能困在追问循环里；
- 大模型不可用时降级到模板文案，绝不因为措辞失败就挡住机型列表。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
import sys

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from bridge import Bridged, bridge_contract  # noqa: E402
from engine import load_data  # noqa: E402
from pipeline import (  # noqa: E402
    MAX_ASK_ROUNDS,
    TOP_N,
    ask_text,
    candidate_frame,
    handle,
    is_declining,
    join_history,
    models_payload,
    needs_summary,
    phones_payload,
)
from providers.jev import MockClassifier  # noqa: E402


class PipelineTest(unittest.TestCase):
    """共用一个只读数据表，避免每个用例重复读 Excel。"""

    @classmethod
    def setUpClass(cls):
        cls.df = load_data()
        cls.classifier = MockClassifier()

    def turn(self, text, **kwargs):
        kwargs.setdefault('write', False)
        return handle(text, self.df, classifier=self.classifier, **kwargs)


# --- 文本工具 ---------------------------------------------------------------


class JoinHistoryTests(PipelineTest):
    def test_single_line_returned_as_is(self):
        self.assertEqual(join_history(['预算三千']), '预算三千')

    def test_multiple_lines_are_numbered(self):
        self.assertEqual(join_history(['预算三千', '要轻']), '1. 预算三千\n2. 要轻')

    def test_blank_lines_dropped(self):
        self.assertEqual(join_history(['预算三千', '   ', '']), '预算三千')

    def test_all_blank_is_empty_string(self):
        self.assertEqual(join_history(['', '  ']), '')


class DeclineTests(PipelineTest):
    def test_short_decline_phrases(self):
        for text in ('随便', '都行', '你看着办', '听你的', '不确定'):
            with self.subTest(text=text):
                self.assertTrue(is_declining(text))

    def test_long_text_is_not_a_decline(self):
        self.assertFalse(is_declining('随便看看两千块以内的手机'))

    def test_wusuowei_is_not_a_decline_marker(self):
        # 「无所谓」是偏好等级，不是弃权。混进来会把「价格无所谓」读成弃权。
        self.assertFalse(is_declining('价格无所谓'))


class AskTextTests(PipelineTest):
    def _items(self, count):
        return [{'question': f'问题{index}'} for index in range(count)]

    def test_single_item_wording(self):
        self.assertIn('我先确认一件事', ask_text(self._items(1)))

    def test_two_items_uses_chinese_numeral(self):
        self.assertIn('我先确认两件事', ask_text(self._items(2)))

    def test_three_items_uses_chinese_numeral(self):
        self.assertIn('我先确认三件事', ask_text(self._items(3)))

    def test_ten_items_falls_back_to_arabic(self):
        self.assertIn('我先确认10件事', ask_text(self._items(10)))

    def test_every_question_is_listed(self):
        text = ask_text(self._items(3))
        for index in range(3):
            self.assertIn(f'问题{index}', text)


# --- 筛选与载荷 -------------------------------------------------------------


class CandidateFrameTests(PipelineTest):
    def _bridged(self, **overrides):
        base = dict(
            budget=(1000, 6000), brands=[], excluded_brands=[],
            storage=256, target=6.5, weights={'price': 1.0},
        )
        base.update(overrides)
        return Bridged(**base)

    def test_budget_and_storage_are_hard_filters(self):
        candidates = candidate_frame(self.df, self._bridged(budget=(2000, 3000)))
        self.assertTrue((candidates.price >= 2000).all())
        self.assertTrue((candidates.price <= 3000).all())
        self.assertTrue((candidates.storage >= 256).all())

    def test_whitelist_keeps_only_named_brands(self):
        candidates = candidate_frame(self.df, self._bridged(brands=['苹果']))
        self.assertEqual(set(candidates.brand), {'苹果'})

    def test_excluded_brands_are_removed(self):
        candidates = candidate_frame(self.df, self._bridged(excluded_brands=['华为', '荣耀']))
        self.assertNotIn('华为', set(candidates.brand))
        self.assertNotIn('荣耀', set(candidates.brand))

    def test_whitelist_and_exclude_combined(self):
        # 白名单与排除名单是两件事：先取交集，再剔除。
        candidates = candidate_frame(
            self.df, self._bridged(brands=['华为', '小米'], excluded_brands=['华为'])
        )
        self.assertEqual(set(candidates.brand), {'小米'})


class ModelsPayloadTests(PipelineTest):
    def _ranked(self):
        bridged, _ = bridge_contract(
            {'status': 'ok', 'budget': {'min': 1000, 'max': 6000}, 'brands': {},
             'preferences': [], 'unsupported_needs': [], 'min_storage': 256,
             'target_screen': None, 'input_text': ''}
        )
        from engine import rank
        return rank(candidate_frame(self.df, bridged), bridged.weights, bridged.target)

    def test_dedupes_to_one_config_per_model(self):
        payload = models_payload(self._ranked(), TOP_N, 6000)
        names = [item['name'] for item in payload]
        self.assertEqual(len(names), len(set(names)))
        self.assertLessEqual(len(names), TOP_N)

    def test_reason_uses_stated_ceiling(self):
        payload = models_payload(self._ranked(), TOP_N, 6000)
        for item in payload:
            self.assertIn('比预算上限低', item['reason'])

    def test_reason_is_honest_without_a_ceiling(self):
        payload = models_payload(self._ranked(), TOP_N, None)
        for item in payload:
            self.assertEqual(item['reason'], '未设置预算上限')

    def test_payload_keys_are_stable(self):
        payload = models_payload(self._ranked(), 1, 6000)[0]
        self.assertEqual(
            set(payload),
            {'id', 'name', 'brand', 'config', 'chip', 'price', 'weight',
             'screen', 'refresh', 'storage', 'score', 'reason'},
        )


class PhonesPayloadTests(PipelineTest):
    def test_every_config_is_exported(self):
        self.assertEqual(len(phones_payload(self.df)), len(self.df))

    def test_does_not_invent_battery_or_fold(self):
        # 原始数据表里没有电池容量与折叠形态，接口就不得凭空给出字段。
        payload = phones_payload(self.df)[0]
        for key in ('battery', 'fold', 'foldable', 'charging_time'):
            self.assertNotIn(key, payload)

    def test_required_display_fields_present(self):
        payload = phones_payload(self.df)[0]
        for key in ('name', 'brand', 'config', 'price', 'weight', 'screen',
                    'refresh', 'storage', 'chip'):
            self.assertIn(key, payload)

    def test_is_json_serialisable(self):
        json.dumps(phones_payload(self.df), ensure_ascii=False)


# --- 一轮对话 ---------------------------------------------------------------


class HandleStageTests(PipelineTest):
    def test_empty_text_fails_without_raising(self):
        turn = self.turn('')
        self.assertEqual(turn.stage, 'failed')
        self.assertTrue(turn.error)

    def test_explicit_budget_goes_straight_to_result(self):
        turn = self.turn('预算2000到3000，越轻越好，主要看价格')
        self.assertEqual(turn.stage, 'result')
        self.assertEqual(turn.budget, [2000, 3000])
        self.assertGreater(turn.candidate_configs, 0)
        self.assertTrue(turn.models)

    def test_models_all_come_from_the_dataset(self):
        turn = self.turn('预算2000到3000，越轻越好')
        known = set(self.df['产品名称'])
        for model in turn.models:
            self.assertIn(model['name'], known)

    def test_models_are_capped_at_top_n(self):
        turn = self.turn('预算3000到6000')
        self.assertLessEqual(len(turn.models), TOP_N)

    def test_scores_are_within_unit_range(self):
        turn = self.turn('预算2000到3000，越轻越好')
        for model in turn.models:
            self.assertGreaterEqual(model['score'], 0.0)
            self.assertLessEqual(model['score'], 1.0)


class ClarifyTests(PipelineTest):
    def test_vague_budget_asks_instead_of_capping(self):
        # 契约红线：含糊预算不得由程序推断上限。
        turn = self.turn('预算三千左右，想要轻一点的')
        self.assertEqual(turn.stage, 'clarify')
        self.assertTrue(turn.questions)
        self.assertIn('budget.max', [item['field'] for item in turn.questions])
        self.assertEqual(turn.models, [])

    def test_question_text_mentions_the_reference_number(self):
        turn = self.turn('预算三千左右')
        question = next(item['question'] for item in turn.questions
                        if item['field'] == 'budget.max')
        self.assertIn('3000', question)
        self.assertIn('上限', question)

    def test_asked_fields_are_reported_back(self):
        turn = self.turn('预算三千左右')
        self.assertIn('budget.max', turn.asked)
        self.assertEqual(turn.ask_rounds, 1)

    def test_second_round_does_not_repeat_the_same_question(self):
        # 多轮回归：用户先说「三千左右」（含糊），后补「最多 4000」（明确）。
        # 拼起来的原文里含糊短语还在，但明确上限是用户刚说的，必须生效。
        first = self.turn('预算三千左右')
        second = self.turn(
            '最多 4000', asked=first.asked, ask_rounds=first.ask_rounds,
            history=['预算三千左右'],
        )
        self.assertEqual(second.stage, 'result')
        self.assertEqual(second.budget, [0, 4000])
        self.assertTrue(second.models)

    def test_round_cap_stops_asking(self):
        turn = self.turn('预算三千左右', ask_rounds=MAX_ASK_ROUNDS)
        self.assertEqual(turn.stage, 'result')
        self.assertEqual(turn.questions, [])

    def test_declining_user_gets_results_not_more_questions(self):
        first = self.turn('预算三千左右，屏幕大点')
        second = self.turn(
            '随便', asked=first.asked, ask_rounds=first.ask_rounds,
            history=['预算三千左右，屏幕大点'],
        )
        self.assertEqual(second.stage, 'result')
        self.assertEqual(second.questions, [])


class AdviceTests(PipelineTest):
    def test_write_false_uses_template_and_says_so(self):
        turn = self.turn('预算2000到3000，越轻越好')
        self.assertTrue(turn.advice)
        self.assertFalse(turn.advice_from_llm)
        self.assertIn('未请求大模型推荐语', turn.advice_error)

    def test_template_advice_lists_the_same_models(self):
        turn = self.turn('预算2000到3000，越轻越好')
        for model in turn.models:
            self.assertIn(model['name'], turn.advice)

    def test_unsupported_needs_are_surfaced(self):
        turn = self.turn('预算2000到3000，拍照要好，游戏要流畅')
        self.assertTrue(turn.unsupported)


class TurnSerialisationTests(PipelineTest):
    def test_result_turn_is_json_serialisable(self):
        turn = self.turn('预算2000到3000，越轻越好')
        payload = json.dumps(turn.to_dict(), ensure_ascii=False)
        self.assertIn('models', payload)

    def test_clarify_turn_is_json_serialisable(self):
        turn = self.turn('预算三千左右')
        payload = json.loads(json.dumps(turn.to_dict(), ensure_ascii=False))
        self.assertEqual(payload['stage'], 'clarify')
        self.assertTrue(payload['questions'])

    def test_failed_turn_is_json_serialisable(self):
        turn = self.turn('')
        payload = json.loads(json.dumps(turn.to_dict(), ensure_ascii=False))
        self.assertEqual(payload['stage'], 'failed')


class NeedsSummaryTests(PipelineTest):
    def test_mentions_stated_brands_and_storage(self):
        result = {
            'preferences': [{'criterion': 'weight', 'level': '重要'}],
        }
        bridged = Bridged(
            budget=(1000, 6000), brands=['小米'], excluded_brands=['华为'],
            storage=512, target=6.5, weights={'price': 1.0},
        )
        summary = needs_summary(result, bridged)
        self.assertIn('小米', summary)
        self.assertIn('华为', summary)
        self.assertIn('512', summary)
        self.assertIn('机身更轻=重要', summary)

    def test_says_so_when_no_preference_stated(self):
        bridged = Bridged(
            budget=(1000, 6000), brands=[], excluded_brands=[],
            storage=256, target=6.5, weights={'price': 1.0},
        )
        self.assertIn('没有表达任何偏好', needs_summary({'preferences': []}, bridged))


if __name__ == '__main__':
    unittest.main()
