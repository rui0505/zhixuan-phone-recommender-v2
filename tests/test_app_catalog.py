"""Production Streamlit integration against an isolated copy of the released catalog."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from contextlib import closing
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from isolation import IsolatedEnvTest

BASE=Path(__file__).resolve().parents[1]
APP=str(BASE/'app.py')


class CatalogAppTests(IsolatedEnvTest):
    def setUp(self):
        super().setUp();self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        shutil.copy2(BASE/'data/catalog.sqlite',self.root/'catalog.sqlite')
        shutil.copy2(BASE/'data/release_manifest.json',self.root/'release_manifest.json')
        self.env=patch.dict(os.environ,{'PHONE_CATALOG_PATH':str(self.root/'catalog.sqlite')})
        self.env.start();self.at=AppTest.from_file(APP,default_timeout=60).run()
    def tearDown(self):self.env.stop();self.temp.cleanup();super().tearDown()
    def confirm(self):
        self.at.checkbox(key='manual_confirm').check()
        next(b for b in self.at.button if b.label=='确认需求并生成推荐').click().run()
        self.assertFalse(self.at.exception)
    def test_manual_recommendation_without_llm(self):
        self.assertFalse(self.at.exception);self.confirm()
        self.assertEqual(self.at.session_state['catalog_result'].status,'ready')
        self.assertGreaterEqual(len(self.at.session_state['catalog_result'].ranked),2)
        self.assertIn('topsis-1.0.0',' '.join(x.value for x in self.at.caption))
        self.assertEqual(self.at.session_state['catalog_bundle']['dataset_version'],
                         json.loads((self.root/'release_manifest.json').read_text(encoding='utf-8'))['dataset_version'])
        self.assertEqual(self.at.dataframe[0].value.columns[1],'score')
    def test_confirmation_is_required_and_all_zero_has_no_score(self):
        next(b for b in self.at.button if b.label=='确认需求并生成推荐').click().run()
        self.assertNotIn('catalog_result',self.at.session_state)
        for key in ('price','weight','storage','refresh','screen','photo','game'):
            self.at.slider(key='manual_w_'+key).set_value(0)
        self.confirm();self.assertNotIn('catalog_result',self.at.session_state)
        self.assertTrue(self.at.error)
    def test_catalog_distinguishes_satellite_variants(self):
        self.assertEqual(len(self.at.session_state['catalog_rows']),484)
        rows=[r for r in self.at.session_state['catalog_rows'] if r['display_name']=='vivo X200 Pro']
        self.assertEqual(len({r['variant_id'] for r in rows}),2)
        self.assertTrue(any(r['satellite_support'] for r in rows))
    def test_incomplete_game_shows_backups_not_score(self):
        for key in ('price','weight','storage','refresh','screen','photo'):self.at.slider(key='manual_w_'+key).set_value(0)
        self.at.slider(key='manual_w_game').set_value(10);self.confirm()
        result=self.at.session_state['catalog_result']
        self.assertEqual(result.status,'insufficient_comparable_data');self.assertTrue(result.ranked.empty)
        self.assertFalse(result.backups.empty);self.assertNotIn('score',result.backups)
        self.assertIn('1% Low',' '.join(x.value for x in self.at.warning))
    def test_dataset_change_invalidates_confirmed_ranking(self):
        self.confirm();sku=self.at.session_state['catalog_result'].ranked.iloc[0]['sku_id']
        with closing(sqlite3.connect(self.root/'catalog.sqlite')) as con:
            con.execute('UPDATE price_snapshots SET amount=amount+1 WHERE sku_id=?',(sku,))
            con.commit()
        manifest=json.loads((self.root/'release_manifest.json').read_text(encoding='utf-8'))
        manifest['snapshot_sha256']=hashlib.sha256((self.root/'catalog.sqlite').read_bytes()).hexdigest()
        (self.root/'release_manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
        self.at.run();self.assertFalse(self.at.exception)
        self.assertNotIn('catalog_result',self.at.session_state);self.assertNotIn('catalog_bundle',self.at.session_state)
        self.assertFalse(self.at.checkbox(key='manual_confirm').value)
        self.assertIn('重新确认',' '.join(x.value for x in self.at.warning))
    def test_hash_mismatch_blocks_cached_ranking_safely(self):
        self.confirm()
        with open(self.root/'catalog.sqlite','ab') as f:f.write(b'changed')
        self.at.run();self.assertFalse(self.at.exception)
        self.assertNotIn('catalog_result',self.at.session_state)
        text=' '.join(x.value for x in self.at.error)
        self.assertIn('校验',text);self.assertNotIn(str(self.root),text)
    def test_controls_changed_invalidate_previous_ranking(self):
        self.confirm();self.at.slider(key='manual_w_price').set_value(9).run()
        self.assertNotIn('catalog_result',self.at.session_state)
    def test_details_and_comparison_save_ids_not_row_numbers(self):
        rows=self.at.session_state['catalog_rows'];ids=[rows[0]['sku_id'],rows[1]['sku_id']]
        self.at.multiselect(key='compare_skus').set_value(ids).run()
        self.assertEqual(self.at.session_state['compare_skus'],ids)
        self.assertFalse(self.at.exception)
    def test_page_load_and_manual_confirmation_never_auto_request_model(self):
        from fake_openai import FakeOpenAIServer
        with FakeOpenAIServer() as server:
            self.at.run();self.confirm()
            self.assertEqual(server.requests,[])
    def test_optional_chat_extracts_but_does_not_rank_until_confirmation(self):
        from fake_openai import FakeOpenAIServer
        with FakeOpenAIServer(enable_auto_provider=True) as server:
            self.at.run();self.at.chat_input[0].set_value('预算1000到5000，不要苹果').run()
            self.assertFalse(self.at.exception)
            self.assertNotIn('catalog_result',self.at.session_state)
            self.assertTrue(self.at.checkbox(key='manual_use_max').value)
            self.assertEqual(self.at.number_input(key='manual_max').value,5000)
            self.confirm()
            self.assertEqual([r['kind'] for r in server.requests],['extract'])
            self.assertTrue(all(r['brand'] not in ('苹果','Apple') for r in self.at.session_state['catalog_input']['candidates'].to_dict('records')))
    def test_vague_chat_budget_is_not_a_hidden_cap(self):
        from fake_openai import FakeOpenAIServer
        with FakeOpenAIServer(enable_auto_provider=True):
            self.at.run();self.at.chat_input[0].set_value('想买2000元左右的手机').run()
            self.assertFalse(self.at.exception)
            self.assertFalse(self.at.checkbox(key='manual_use_max').value)
            self.assertFalse(self.at.checkbox(key='manual_use_min').value)
            self.assertNotIn('catalog_result',self.at.session_state)
    def test_provider_failure_keeps_manual_path_available(self):
        from fake_openai import FakeOpenAIServer
        with FakeOpenAIServer(status=500,enable_auto_provider=True):
            self.at.run();self.at.chat_input[0].set_value('预算1000到3000').run()
            self.assertFalse(self.at.exception);self.assertTrue(self.at.error)
            self.confirm();self.assertEqual(self.at.session_state['catalog_result'].status,'ready')
    def test_unknown_extracted_brand_is_not_silently_broadened(self):
        from catalog.adapter import resolve_brands
        with self.assertRaises(ValueError):resolve_brands(['不存在品牌'],['Apple','华为'])
        self.assertEqual(resolve_brands(['苹果'],['Apple','华为']),['Apple'])
    def test_invalid_database_with_matching_hash_fails_without_white_screen(self):
        (self.root/'catalog.sqlite').write_bytes(b'invalid sqlite')
        mp=self.root/'release_manifest.json';manifest=json.loads(mp.read_text(encoding='utf-8'))
        manifest['snapshot_sha256']=hashlib.sha256((self.root/'catalog.sqlite').read_bytes()).hexdigest()
        mp.write_text(json.dumps(manifest),encoding='utf-8')
        self.at.run();self.assertFalse(self.at.exception);self.assertTrue(self.at.error)
    def test_clear_returns_manual_controls_to_declared_defaults(self):
        self.at.slider(key='manual_w_price').set_value(9).run()
        next(b for b in self.at.button if b.label=='清空需求与结果').click().run()
        self.assertEqual(self.at.slider(key='manual_w_price').value,5)
        self.assertNotIn('catalog_result',self.at.session_state)
    def test_dataframe_labels_are_explicit_column_objects(self):
        self.confirm()
        configs=[json.loads(frame.proto.columns) for frame in self.at.dataframe]
        self.assertTrue(any(c.get('score',{}).get('label')=='相对匹配分' for c in configs))

    def test_single_matching_apple_is_displayed_without_score(self):
        self.at.multiselect(key='manual_brands').set_value(['Apple'])
        for key in ('weight','storage','refresh','screen','photo','game'):
            self.at.slider(key='manual_w_'+key).set_value(0)
        self.at.checkbox(key='manual_use_min').check()
        self.at.checkbox(key='manual_use_max').check()
        self.at.number_input(key='manual_min').set_value(5999.0)
        self.at.number_input(key='manual_max').set_value(5999.0)
        self.confirm()
        result=self.at.session_state['catalog_result']
        self.assertEqual(result.status,'insufficient_comparable_data')
        self.assertIn('满足条件但未评分的候选', [h.value for h in self.at.subheader])
        self.assertTrue(any('sku_id' in f.value and 'S0011' in f.value['sku_id'].tolist()
                            and 'score' not in f.value for f in self.at.dataframe))
