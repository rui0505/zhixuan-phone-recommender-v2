import copy
import json
import math
from pathlib import Path
import sys
import unittest

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from catalog.adapter import FRAME_FIELDS
from engine import load_data, rank
from preferences import (KEYS, QUESTION_BANK, apply_answer, build_initial_weights,
                         filter_candidates, next_question, normalize_weights,
                         update_weights, validate_needs)


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        self.needs = json.loads(Path(__file__).resolve().parents[1]
                               .joinpath('example_needs.json').read_text(encoding='utf-8'))
        self.weights = build_initial_weights(self.needs)['weights']

    def test_example(self):
        # weight=非常重要(9)，其余按维度默认值：storage 3、screen 1、photo 0、
        # game 0、battery 2、chipset 2，合计 17。
        # photo 的未提及是 0——DXOMARK 覆盖只有 11/51 个 SKU，给正权重会塌候选池。
        self.assertAlmostEqual(self.weights['weight'], 9 / 17)
        self.assertAlmostEqual(sum(self.weights.values()), 1)

    def test_all_normal(self):
        """全部「一般」不再是均匀分布——每个维度有自己的表值。"""
        self.needs['preferences'] = dict.fromkeys(KEYS, 'normal')
        record = build_initial_weights(self.needs)
        raw = record['raw_scores']
        total = sum(raw.values())
        for key in KEYS:
            self.assertAlmostEqual(record['weights'][key], raw[key] / total)
        # 存储的「一般」比游戏高，所以不应等权
        self.assertGreater(record['weights']['storage'], record['weights']['game'])

    def test_unknown_source(self):
        record = build_initial_weights(self.needs)
        self.assertEqual(record['sources']['storage'], 'system_default')
        self.assertEqual(record['raw_scores']['storage'], 3)

    def test_budget_not_price_preference(self):
        initial = dict(self.weights)
        self.needs['hard_constraints']['max_price'] = 9999
        self.assertEqual(build_initial_weights(self.needs)['weights'], initial)

    def test_zero(self):
        self.needs['preferences'] = dict.fromkeys(KEYS, 'unimportant')
        with self.assertRaises(ValueError):
            build_initial_weights(self.needs)

    def test_invalid_numeric(self):
        for value in (-1, float('nan'), float('inf'), True, '1'):
            weights = dict(self.weights, weight=value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_weights(weights)

    def test_invalid_enum(self):
        self.needs['preferences']['weight'] = 'very'
        with self.assertRaises(ValueError):
            validate_needs(self.needs)

    def test_missing_field(self):
        del self.needs['preferences']['weight']
        with self.assertRaises(ValueError):
            validate_needs(self.needs)

    def test_invalid_constraints(self):
        self.needs['hard_constraints']['max_price'] = True
        with self.assertRaises(ValueError):
            validate_needs(self.needs)

    def test_target_not_invented(self):
        self.needs['target_screen_inches'] = None
        record = build_initial_weights(self.needs)
        self.assertEqual(record['weights']['screen'], 0)
        self.assertEqual(record['raw_scores']['screen'], 1)
        self.assertEqual(record['inactive_keys'], ['screen'])

    def test_target_zero_no_effect(self):
        self.needs['target_screen_inches'] = None
        weights = build_initial_weights(self.needs)['weights']
        from tests.legacy_fixture import load_fixture
        frame = load_fixture().head(12)
        # 旧夹具只有 价格/重量/存储/屏幕 可评分，其余维度先过滤掉。
        legacy = {k: v for k, v in weights.items() if k in FRAME_FIELDS}
        self.assertEqual(rank(frame, legacy, 6.1).id.tolist(),
                         rank(frame, legacy, 6.8).id.tolist())

    def test_no_mutation(self):
        before = copy.deepcopy(self.needs)
        build_initial_weights(self.needs)
        self.assertEqual(self.needs, before)
        weights = dict(self.weights)
        update_weights(weights, 'weight', 'storage')
        self.assertEqual(weights, self.weights)

    def test_update_light(self):
        w = update_weights(self.weights, 'weight', 'storage')
        self.assertGreater(w['weight'], self.weights['weight'])
        self.assertLess(w['storage'], self.weights['storage'])
        self.assertAlmostEqual(sum(w.values()), 1)

    def test_update_storage(self):
        w = update_weights(self.weights, 'storage', 'weight')
        self.assertGreater(w['storage'], self.weights['storage'])
        self.assertLess(w['weight'], self.weights['weight'])
        self.assertAlmostEqual(sum(w.values()), 1)

    def test_no_zero_resurrection(self):
        w = dict(self.weights, photo=0)
        self.assertEqual(update_weights(w, 'weight', 'storage')['photo'], 0)
        with self.assertRaises(ValueError):
            update_weights(w, 'photo', 'storage')

    def test_skip_and_indifferent(self):
        for answer in ('skip', 'indifferent'):
            change = apply_answer(self.weights, QUESTION_BANK[0], answer, [])
            for key in KEYS:
                self.assertAlmostEqual(change['weights'][key], self.weights[key])
            self.assertEqual(change['record']['answer'], answer)

    def test_repeat(self):
        change = apply_answer(self.weights, QUESTION_BANK[0], 'a', [])
        with self.assertRaises(ValueError):
            apply_answer(change['weights'], QUESTION_BANK[0], 'a', change['answered_ids'])

    def test_question_rules(self):
        q = next_question(self.weights, [], self.needs['preferences'])
        self.assertEqual(q['a_key'], 'weight')
        self.assertIsNone(next_question(self.weights, ['light_storage', 'photo_storage'],
                                        self.needs['preferences']))
        self.assertIsNone(next_question(dict(weight=0, storage=0, screen=0, photo=0,
                                             game=0, battery=0, chipset=1),
                                        [], self.needs['preferences']))

    def test_hard_filter(self):
        frame = pd.DataFrame({'price': [500, 3001, 2500], 'storage': [256, 256, 128],
                              'brand': ['小米', '小米', '小米']})
        filtered = filter_candidates(frame, self.needs)
        self.assertEqual(filtered.price.tolist(), [500])

    def test_topsis_direction(self):
        # 仅价格与重量存在差异，使用控制条件验证排序对偏好改变的反应。
        frame = pd.DataFrame({'id': ['A', 'B'], '产品名称':['合成机A','合成机B'], 'price': [2500., 2000.], 'weight': [170., 200.],
                              'storage': [256., 256.], 'refresh': [120., 120.], 'screen': [6.5, 6.5]})
        light = dict(price=.01, weight=.99, storage=0, refresh=0, screen=0)
        cheap = dict(price=.99, weight=.01, storage=0, refresh=0, screen=0)
        self.assertEqual(rank(frame, light, 6.5).iloc[0].id, 'A')
        self.assertEqual(rank(frame, cheap, 6.5).iloc[0].id, 'B')


if __name__ == '__main__':
    unittest.main()
