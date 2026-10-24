import unittest
from catalog_fixture import conditions


class ComparabilityTests(unittest.TestCase):
    """契约：同机构、同画质、同帧目标才可比；其余条件记录但不阻断。

    历史教训：这里曾经要求 11 项条件全部非空，而真实数据里没有任何一条 run
    能同时满足，导致游戏维度整体静默失效。下面用 legacy 键名的用例守住这一点。
    """

    def group(self, updates):
        from catalog.comparability import game_group
        return game_group(dict(institution='测试机构', benchmark='王者荣耀', protocol='test-v1',
                               applicable_scope='model', conditions_json=conditions(**updates)))

    def test_missing_required_condition_blocks_comparison(self):
        for key in ('fps_target', 'quality'):
            with self.subTest(key=key):
                group, reason = self.group({key: None})
                self.assertIsNone(group)
                self.assertIn(key, reason)

    def test_missing_institution_blocks_comparison(self):
        from catalog.comparability import game_group
        group, reason = game_group(dict(institution='', benchmark='王者荣耀', protocol='test-v1',
                                        applicable_scope='model',
                                        conditions_json=conditions()))
        self.assertIsNone(group)
        self.assertIn('institution', reason)

    def test_60_and_120_fps_are_separate(self):
        self.assertNotEqual(self.group({'fps_target': 60})[0], self.group({'fps_target': 120})[0])

    def test_different_quality_and_institution_are_separate(self):
        baseline = self.group({})[0]
        self.assertNotEqual(baseline, self.group({'quality': '高清'})[0])
        from catalog.comparability import game_group
        other = game_group(dict(institution='另一家机构', benchmark='王者荣耀', protocol='test-v1',
                                applicable_scope='model', conditions_json=conditions()))
        self.assertNotEqual(baseline, other[0])

    def test_uncontrolled_conditions_do_not_split_groups_but_are_reported(self):
        """温度/散热/机身版本等不参与分组，但必须出现在 uncontrolled 里如实披露。"""
        baseline, base_detail = self.group({})
        for key, value in (('ambient_temp_c', 30), ('cooler', '有'), ('os_version', 'OS2'),
                           ('scene', '训练')):
            with self.subTest(key=key):
                group, detail = self.group({key: value})
                self.assertEqual(group, baseline)
                self.assertNotIn(key, detail['uncontrolled'])
        # 夹具的 conditions() 不含 screen_brightness，因此它是唯一未被记录的条件
        self.assertEqual(base_detail['uncontrolled'], ['screen_brightness'])
        self.assertIn('ambient_temp_c', base_detail['recorded'])

    def test_legacy_frame_mode_only_run_still_groups(self):
        """真实数据大多只有 frame_mode 原文，没有 quality / fps_target 字段。"""
        from catalog.comparability import game_group
        group, detail = game_group(dict(
            institution='www.3elife.net', benchmark='王者荣耀', protocol='王者荣耀帧率/功耗/温度实测',
            applicable_scope='model',
            conditions_json={'frame_mode': '最高可用画质；120帧', 'other': '30分钟'}))
        self.assertIsNotNone(group, detail)
        self.assertEqual(detail['quality'], '最高可用画质')
        self.assertEqual(detail['fps_target'], 120.0)
        self.assertEqual(detail['recorded'].get('duration_min'), 30)

    def test_legacy_aliases_are_normalised(self):
        from catalog.comparability import normalise_game_conditions
        values, _ = normalise_game_conditions({'match_mode': '10V10', 'ambient_temp': '25±0.5℃',
                                               'os': 'iOS18.0'})
        self.assertEqual(values['scene'], '10V10')
        self.assertEqual(values['ambient_temp_c'], '25±0.5℃')
        self.assertEqual(values['os_version'], 'iOS18.0')

    def test_source_marked_missing_is_rejected(self):
        from catalog.comparability import game_group
        group, reason = game_group(dict(
            institution='', benchmark='王者荣耀', protocol='p', applicable_scope='model',
            conditions_json={'missing_reason': 'not_found'}))
        self.assertIsNone(group)
        self.assertIn('source_marked_missing', reason)

    def test_invalid_conditions_json_is_rejected(self):
        from catalog.comparability import game_group
        # None / 空容器等价于"没有条件"，走 missing_conditions；这里只测真正的非法形态
        for payload in ('{not json', 42):
            with self.subTest(payload=payload):
                group, reason = game_group(dict(institution='x', benchmark='王者荣耀',
                                                protocol='p', applicable_scope='model',
                                                conditions_json=payload))
                self.assertIsNone(group)
                self.assertIn('invalid_conditions_json', reason)

    def test_absent_conditions_report_missing_not_invalid(self):
        from catalog.comparability import game_group
        for payload in (None, []):
            with self.subTest(payload=payload):
                group, reason = game_group(dict(institution='x', benchmark='王者荣耀',
                                                protocol='p', applicable_scope='model',
                                                conditions_json=payload))
                self.assertIsNone(group)
                self.assertIn('missing_conditions', reason)

    def test_boolean_fps_target_is_rejected(self):
        group, reason = self.group({'fps_target': True})
        self.assertIsNone(group)
        self.assertIn('fps_target', reason)


if __name__ == '__main__':
    unittest.main()
