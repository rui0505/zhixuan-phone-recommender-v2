"""`quiz` 模块的测试。

重点不在「题库有几道题」，而在**每个选项拼出的那句话，内核能正确理解**。
题库的措辞直接决定抽取结果，改一个词就可能让某个选项变成无效选项
（解析不出任何维度，或触发多余的追问），所以这里逐项断言。

这些断言的价值：题库是「产品内容」，很容易被随手改文案，而改坏了不会报错，
只会静默地让某些选项失去作用。
"""

from __future__ import annotations

import unittest

from providers.jev import MockClassifier
from quiz import (
    MAX_QUESTIONS,
    OPTIONS,
    QUESTIONS,
    SCENE_UNSUPPORTED,
    answer_labels,
    answer_says,
    build_text,
    expected_unsupported,
    progress,
    question_for,
    quiz_payload,
)
from needs_extraction import extract_needs

# 一整套能答完的答卷，多个测试复用。
FULL_ANSWERS = {
    'budget': 'b2', 'scene': 'photo', 'carry': 'light',
    'screen': 'large', 'storage': 's512',
}


class QuizStructureTests(unittest.TestCase):
    """题库本身的结构约束。"""

    def test_questions_within_limit(self):
        self.assertTrue(0 < len(QUESTIONS) <= MAX_QUESTIONS,
                        f'题数 {len(QUESTIONS)} 超出上限 {MAX_QUESTIONS}')

    def test_question_keys_unique(self):
        keys = [q.key for q in QUESTIONS]
        self.assertEqual(len(keys), len(set(keys)), f'题号重复：{keys}')

    def test_each_question_has_at_least_two_options(self):
        for question in QUESTIONS:
            self.assertGreaterEqual(
                len(question.options), 2,
                f'{question.key} 只有 {len(question.options)} 个选项',
            )

    def test_option_keys_unique_within_question(self):
        for question in QUESTIONS:
            keys = [o.key for o in question.options]
            self.assertEqual(len(keys), len(set(keys)),
                             f'{question.key} 的选项号重复：{keys}')

    def test_every_option_has_text_fields(self):
        for question in QUESTIONS:
            self.assertTrue(question.title.strip())
            for option in question.options:
                self.assertTrue(option.key.strip())
                self.assertTrue(option.label.strip(), f'{question.key}/{option.key} 缺 label')
                self.assertTrue(option.say.strip(),
                                f'{question.key}/{option.key} 缺 say——这个选项会毫无作用')

    def test_options_index_covers_every_option(self):
        expected = sum(len(q.options) for q in QUESTIONS)
        self.assertEqual(len(OPTIONS), expected)

    def test_question_for_finds_and_misses(self):
        self.assertIsNotNone(question_for(QUESTIONS[0].key))
        self.assertIsNone(question_for('does-not-exist'))

    def test_payload_is_json_ready(self):
        import json

        payload = quiz_payload()
        self.assertEqual(len(payload['questions']), len(QUESTIONS))
        # 能被序列化就说明没有混进非基本类型
        json.dumps(payload)

    def test_payload_does_not_leak_say(self):
        """`say` 是内部组句用的，不该出现在给前端的载荷里。

        泄漏了会诱使前端拿它当展示文案，两处文案就此分叉。
        """
        for item in quiz_payload()['questions']:
            for option in item['options']:
                self.assertNotIn('say', option)


class AnswerSaysTests(unittest.TestCase):
    """答案表 → 原话列表。"""

    def test_order_follows_question_definition(self):
        """同样的答案组合必须得到同样的句子——与用户点击顺序无关。"""
        a = {'storage': 's256', 'budget': 'b1', 'carry': 'light'}
        b = {'carry': 'light', 'storage': 's256', 'budget': 'b1'}
        self.assertEqual(answer_says(a), answer_says(b))
        self.assertEqual(build_text(a), build_text(b))

    def test_unknown_keys_ignored(self):
        says = answer_says({'budget': 'b1', 'nope': 'x', 'scene': 'unknown'})
        self.assertEqual(says, [OPTIONS[('budget', 'b1')].say])

    def test_empty_answers_give_empty_text(self):
        self.assertEqual(answer_says({}), [])
        self.assertEqual(build_text({}), '')

    def test_text_ends_with_period_and_is_single_line(self):
        """必须是单行——多行会被 pipeline 当成多轮对话。"""
        text = build_text(FULL_ANSWERS)
        self.assertTrue(text.endswith('。'))
        self.assertNotIn('\n', text)

    def test_list_value_takes_known_items_only(self):
        says = answer_says({'budget': ['b1', 'nope']})
        self.assertEqual(says, [OPTIONS[('budget', 'b1')].say])

    def test_non_string_value_ignored(self):
        self.assertEqual(answer_says({'budget': 123}), [])
        self.assertEqual(answer_says({'budget': None}), [])


class OptionParsingTests(unittest.TestCase):
    """逐个选项断言：这句话被内核读成了什么。

    这是本文件的核心。每一项失败都意味着题库里有个选项名不副实。
    """

    @classmethod
    def setUpClass(cls):
        cls.classifier = MockClassifier()

    def parse(self, say: str) -> dict:
        return extract_needs(say, classifier=self.classifier)

    @staticmethod
    def levels(result) -> dict[str, str]:
        return {
            item['criterion']: item['level']
            for item in result['preferences']
            if item['level'] != '未提及'
        }

    def test_budget_options_set_explicit_bounds(self):
        """三个预算选项都必须是**明确边界**，否则会触发追问。"""
        cases = {
            'b1': (None, 3000.0),
            'b2': (3000.0, 4500.0),
            'b3': (4500.0, 6500.0),
        }
        for key, (low, high) in cases.items():
            with self.subTest(key=key):
                result = self.parse(OPTIONS[('budget', key)].say)
                self.assertEqual(result['budget']['min'], low)
                self.assertEqual(result['budget']['max'], high)
                asked = [x['field'] for x in result['clarifications']]
                self.assertNotIn('budget.max', asked,
                                 f'{key} 不该触发预算追问')

    def test_scene_photo_flags_unsupported_image(self):
        """拍照场景必须被标记为内核算不了——这是诚实性的落点。"""
        result = self.parse(OPTIONS[('scene', 'photo')].say)
        needs = [item['need'] for item in result['unsupported_needs']]
        self.assertIn('影像', needs)

    def test_scene_game_flags_unsupported_game(self):
        result = self.parse(OPTIONS[('scene', 'game')].say)
        needs = [item['need'] for item in result['unsupported_needs']]
        self.assertIn('真实游戏性能', needs)

    def test_scene_game_also_moves_refresh(self):
        """游戏场景给了刷新率一个落点，否则这个选项对结果毫无影响。"""
        result = self.parse(OPTIONS[('scene', 'game')].say)
        self.assertEqual(self.levels(result).get('refresh'), '非常重要')

    def test_scene_photo_also_moves_storage(self):
        """拍照场景给了存储一个落点（照片视频占地方）。"""
        result = self.parse(OPTIONS[('scene', 'photo')].say)
        self.assertEqual(self.levels(result).get('storage'), '重要')

    def test_carry_light_is_important_weight(self):
        result = self.parse(OPTIONS[('carry', 'light')].say)
        self.assertEqual(self.levels(result).get('weight'), '重要')

    def test_carry_careless_expresses_no_preference(self):
        result = self.parse(OPTIONS[('carry', 'careless')].say)
        self.assertNotIn('weight', self.levels(result))

    def test_screen_options_set_target_size(self):
        cases = {'small': 6.1, 'middle': 6.5, 'large': 6.8}
        for key, size in cases.items():
            with self.subTest(key=key):
                result = self.parse(OPTIONS[('screen', key)].say)
                self.assertEqual(result['target_screen'], size)

    def test_storage_options_set_floor(self):
        """1TB 必须被认成 1024GB，不能因为单位是 TB 就丢失门槛。"""
        cases = {'s256': 256.0, 's512': 512.0, 's1t': 1024.0}
        for key, floor in cases.items():
            with self.subTest(key=key):
                result = self.parse(OPTIONS[('storage', key)].say)
                self.assertEqual(result['min_storage'], floor,
                                 f'{key} 的存储门槛没被解析出来')

    def test_no_option_triggers_a_spurious_preference_clarification(self):
        """任何选项都不该触发「依据与原文不符」这类假追问。

        这条是回归：屏幕题的 evidence 曾因重新拼装字符串（丢掉数字与
        「英寸」之间的空格）而被判为与原文不符，给每个尺寸选项都挂了假追问。
        """
        for (qkey, okey), option in OPTIONS.items():
            with self.subTest(option=f'{qkey}/{okey}'):
                result = self.parse(option.say)
                spurious = [
                    x['field'] for x in result['clarifications']
                    if x['field'].startswith('preferences.')
                ]
                self.assertEqual(spurious, [],
                                 f'{qkey}/{okey} 触发了假追问：{spurious}')


class FullAnswerTests(unittest.TestCase):
    """整套答卷的行为。"""

    @classmethod
    def setUpClass(cls):
        cls.classifier = MockClassifier()

    def parse(self, answers):
        return extract_needs(build_text(answers), classifier=self.classifier)

    def test_full_answer_produces_no_questions(self):
        """答完五题后不该再有任何追问——用户已经答过了。"""
        result = self.parse(FULL_ANSWERS)
        self.assertEqual(result['clarifications'], [])

    def test_full_answer_carries_every_dimension(self):
        text = build_text(FULL_ANSWERS)
        result = extract_needs(text, classifier=self.classifier)
        self.assertEqual(result['budget']['min'], 3000.0)
        self.assertEqual(result['budget']['max'], 4500.0)
        self.assertEqual(result['min_storage'], 512.0)
        self.assertEqual(result['target_screen'], 6.8)
        levels = {
            item['criterion']: item['level']
            for item in result['preferences']
            if item['level'] != '未提及'
        }
        self.assertEqual(levels.get('weight'), '重要')
        self.assertEqual(levels.get('screen'), '重要')

    def test_every_combination_parses_without_questions(self):
        """穷举所有选项组合：每种组合都必须能被解析且不触发追问。

        3×3×3×3×3 = 243 种。跑得快（纯规则），但能一次性覆盖题库里
        所有措辞的交叉影响——两个各自正常的选项拼在一起也可能出问题。
        """
        from itertools import product

        keys = [q.key for q in QUESTIONS]
        combos = list(product(*(q.options for q in QUESTIONS)))
        self.assertEqual(len(combos), 243)

        failures = []
        for combo in combos:
            answers = {key: option.key for key, option in zip(keys, combo)}
            result = self.parse(answers)
            if result['clarifications']:
                pending = [x['field'] for x in result['clarifications']]
                failures.append((answers, pending))
        self.assertEqual(
            failures, [],
            f'{len(failures)} 种组合触发了追问，例如：{failures[:3]}',
        )

    def test_every_combination_yields_at_least_one_preference(self):
        """每种组合都应至少带一个非默认偏好。

        否则「用户答了五道题，结果权重全按中性先验算」——白答了。
        """
        from itertools import product

        keys = [q.key for q in QUESTIONS]
        bad = []
        for combo in product(*(q.options for q in QUESTIONS)):
            answers = {key: option.key for key, option in zip(keys, combo)}
            result = self.parse(answers)
            stated = [
                item for item in result['preferences']
                if item['level'] != '未提及'
            ]
            if not stated:
                bad.append(answers)
        self.assertEqual(bad, [], f'这些组合没有产生任何偏好：{bad[:3]}')


class UnsupportedTests(unittest.TestCase):
    """「问了但算不了」的处理。"""

    def test_expected_unsupported_matches_scene(self):
        self.assertEqual(expected_unsupported({'scene': 'photo'}), ['影像'])
        self.assertEqual(expected_unsupported({'scene': 'game'}), ['真实游戏性能'])
        self.assertEqual(expected_unsupported({'scene': 'daily'}), [])

    def test_expected_unsupported_is_ordered_and_deduped(self):
        self.assertEqual(expected_unsupported({'scene': 'photo', 'carry': 'light'}),
                         ['影像'])

    def test_expected_unsupported_agrees_with_extraction(self):
        """前端提前提示的口径必须与真正抽取出来的结果一致。

        两处不一致会让用户看到「说好了会提醒，结果提醒里没有它」。
        """
        classifier = MockClassifier()
        for option in QUESTIONS[1].options:
            with self.subTest(option=option.key):
                predicted = expected_unsupported({'scene': option.key})
                result = extract_needs(option.say, classifier=classifier)
                actual = [item['need'] for item in result['unsupported_needs']]
                self.assertEqual(predicted, actual,
                                 f'{option.key}：预计 {predicted}，实际 {actual}')


class LabelAndProgressTests(unittest.TestCase):
    def test_labels_follow_question_order(self):
        labels = answer_labels({'storage': 's256', 'budget': 'b1'})
        self.assertEqual(labels[0]['label'],
                         OPTIONS[('budget', 'b1')].label)
        self.assertEqual(labels[1]['label'],
                         OPTIONS[('storage', 's256')].label)
        self.assertTrue(all(item['question'] for item in labels))

    def test_labels_ignore_unknown(self):
        self.assertEqual(answer_labels({'budget': 'zzz'}), [])
        self.assertEqual(answer_labels({}), [])

    def test_progress_counts_answered(self):
        self.assertEqual(progress({}), {'answered': 0, 'total': len(QUESTIONS)})
        self.assertEqual(progress(FULL_ANSWERS),
                         {'answered': len(QUESTIONS), 'total': len(QUESTIONS)})
        self.assertEqual(progress({'budget': 'b1'})['answered'], 1)

    def test_progress_ignores_unknown_answers(self):
        """题号不认识就不该算进已答数，否则进度条会骗人。"""
        self.assertEqual(progress({'nope': 'x'})['answered'], 0)


if __name__ == '__main__':
    unittest.main()
