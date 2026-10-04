import unittest
from catalog_fixture import conditions


class ComparabilityTests(unittest.TestCase):
    def group(self, updates):
        from catalog.comparability import game_group
        return game_group(dict(institution='测试机构', benchmark='王者荣耀', protocol='test-v1',
                               applicable_scope='model', conditions_json=conditions(**updates)))

    def test_missing_target_or_resolution_blocks_game_comparison(self):
        for key in ('fps_target','render_resolution','game_version','measurement_method'):
            with self.subTest(key=key):
                group,reason=self.group({key:None})
                self.assertIsNone(group); self.assertIn(key,reason)

    def test_60_and_120_fps_are_separate(self):
        self.assertNotEqual(self.group({'fps_target':60})[0],self.group({'fps_target':120})[0])

    def test_temperature_cooler_os_and_scene_are_not_merged(self):
        baseline=self.group({})[0]
        for key,value in [('ambient_temp_c',30),('cooler','有'),('os_version','OS2'),('scene','训练')]:
            with self.subTest(key=key): self.assertNotEqual(baseline,self.group({key:value})[0])
