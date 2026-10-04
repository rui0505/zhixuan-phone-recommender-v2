import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from stage_derived import build_derived, parse_game_conditions


class GameImportTests(unittest.TestCase):
    def test_conflicting_source_measurements_not_discarded_by_deduplication(self):
        rows=[dict(_model_id='M1',_variant_id='V1',_sku_id='S'+str(n),source_record_id='R'+str(n),
                   产品名称='手机',运行内存='12GB',存储='256GB',王者测试条件='120帧',游戏数据来源='test',
                   **{'王者平均帧率(FPS)':val}) for n,val in ((1,119),(2,118))]
        entities,_=build_derived({'conventions':{'storage_tb_to_gb':1024}}, {}, rows, [], [], {}, {}, '2026-10-04')
        fps=[o for o in entities['metric_observations'] if o['metric_key']=='honor_of_kings_avg_fps']
        self.assertEqual([o['value_num'] for o in fps],[119,118])
        self.assertEqual(len({o['run_id'] for o in fps}),2)

    def test_explicit_frame_target_and_resolution_are_structured_not_inferred(self):
        result,_=parse_game_conditions('120帧；渲染分辨率=1920x1080；iOS18.0')
        self.assertEqual(result.get('fps_target'),120)
        self.assertEqual(result.get('render_resolution'),'1920x1080')
        self.assertEqual(result.get('os_version'),'iOS18.0')
        self.assertNotIn('game_version',result)
