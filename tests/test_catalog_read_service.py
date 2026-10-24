"""Breaks caught: stale release cache, cross-SKU leakage, unsafe provenance URLs."""
from contextlib import closing
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests.api_fixture import make_release
from tests.catalog_fixture import insert


class CatalogReadTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('catalog.release'), 'Pure release verifier is absent')
        self.assertIsNotNone(importlib.util.find_spec('catalog.read_views'), 'Pure SKU read views are absent')
        self.release = importlib.import_module('catalog.release')
        self.views = importlib.import_module('catalog.read_views')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = make_release(self.root)
        self.mp = self.db.with_name('release_manifest.json')

    def rewrite_manifest(self, **changes):
        data = json.loads(self.mp.read_text(encoding='utf-8'))
        data['snapshot_sha256'] = hashlib.sha256(self.db.read_bytes()).hexdigest()
        data.update(changes)
        self.mp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

    def test_safe_source_urls(self):
        self.assertIsNone(self.views.safe_url('https://user:pass@example.test/'))
        self.assertIsNone(self.views.safe_url('https://example.test/a\r\nb'))
        self.assertIsNone(self.views.safe_url('javascript:alert(1)'))
        self.assertEqual(self.views.safe_urls('https://example.test/a\njavascript:alert(1)\nhttps://example.test/a'),
                         ['https://example.test/a'])

    def test_import_without_streamlit(self):
        script = 'import catalog.release, catalog.read_views, sys; assert "streamlit" not in sys.modules'
        run = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_release_keys_and_cache(self):
        store = self.release.ReleaseStore(self.db)
        first = store.get()
        self.assertIs(first, store.get())
        self.assertEqual(first.key['snapshot_sha256'], hashlib.sha256(self.db.read_bytes()).hexdigest())
        self.assertEqual(first.key['manifest_sha256'], hashlib.sha256(self.mp.read_bytes()).hexdigest())
        self.assertEqual(first.key['dataset_version'], 'fixture-v1')
        self.assertEqual(first.key['feature_rule_version'], 'fixture-rule-v1')

    def test_changed_release_revalidated(self):
        store = self.release.ReleaseStore(self.db)
        first = store.get()
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("UPDATE price_snapshots SET amount=1001 WHERE price_id='P1'")
            con.commit()
        self.rewrite_manifest()
        second = store.get()
        self.assertIsNot(first, second)
        self.assertNotEqual(first.key, second.key)
        self.assertEqual(second.repo.as_records(second.repo.load_catalog(mode='new'))[0]['price_cny'], 1001)

    def test_changed_manifest_not_hidden_by_cache(self):
        store = self.release.ReleaseStore(self.db)
        first = store.get()
        self.rewrite_manifest(acceptance={'machine_acceptance':'PASS','overall_acceptance':'PENDING','wps_review':'PASS'})
        second = store.get()
        self.assertNotEqual(first.key['manifest_sha256'], second.key['manifest_sha256'])
        self.assertEqual(second.manifest['acceptance']['wps_review'], 'PASS')

    def test_invalid_release_rejected(self):
        store = self.release.ReleaseStore(self.db)
        store.get()
        with self.db.open('ab') as handle:
            handle.write(b'changed')
        with self.assertRaises(ValueError):
            store.get()

    def test_invalid_sqlite_with_matching_hash_rejected(self):
        self.db.write_bytes(b'not sqlite')
        self.rewrite_manifest()
        with self.assertRaises(ValueError):
            self.release.ReleaseStore(self.db).get()

    def test_version_mismatch_rejected(self):
        self.rewrite_manifest(dataset_version='not-fixture')
        with self.assertRaises(ValueError):
            self.release.ReleaseStore(self.db).get()

    def test_foreign_key_violation_rejected(self):
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("UPDATE price_snapshots SET sku_id='unknown' WHERE price_id='P1'")
            con.commit()
        self.rewrite_manifest()
        with self.assertRaises(ValueError):
            self.release.ReleaseStore(self.db).get()

    def test_machine_failed_rejected(self):
        self.rewrite_manifest(acceptance={'machine_acceptance':'FAIL'})
        with self.assertRaises(ValueError):
            self.release.ReleaseStore(self.db).get()

    def test_verification_race_rejected(self):
        from catalog.repository import CatalogRepository
        original = CatalogRepository.dataset_version
        def changing(repo):
            version = original(repo)
            with self.db.open('ab') as handle:
                handle.write(b'race')
            return version
        with patch.object(CatalogRepository, 'dataset_version', changing), self.assertRaises(ValueError):
            self.release.ReleaseStore(self.db).get()

    def test_sku_scope_and_sources(self):
        def prepare(con):
            insert(con,'phone_variants',variant_id='V3',model_id='M1',variant_name='卫星',name_is_source=0,satellite_support=1)
            insert(con,'skus',sku_id='S3',variant_id='V1',model_id='M1',ram_raw='16GB',storage_raw='512GB')
            insert(con,'skus',sku_id='S4',variant_id='V3',model_id='M1',ram_raw='16GB',storage_raw='512GB')
            insert(con,'specifications',spec_id='othercap',subject_type='sku',subject_id='S3',spec_key='weight_g',value_num=777)
            insert(con,'specifications',spec_id='sat',subject_type='variant',subject_id='V3',spec_key='weight_g',value_num=999)
            insert(con,'sources',source_id='src1',url='https://example.test/spec',publisher='测试')
            insert(con,'sources',source_id='src2',url='https://example.test/additional',publisher='测试')
            con.execute("UPDATE specifications SET source_id='src1' WHERE spec_id='weight_g1'")
            insert(con,'spec_provenance',spec_id='weight_g1',source_record_id='extra',source_col='重量',source_id='src2')
        self.db = make_release(self.root/'scope', prepare=prepare)
        repo = self.release.ReleaseStore(self.db).get().repo
        detail = self.views.sku_details(repo,'S1')
        self.assertEqual(detail['sku']['sku_id'],'S1')
        self.assertEqual({p['sku_id'] for p in detail['prices']},{'S1'})
        self.assertNotIn(777,[s['value_num'] for s in detail['specifications']])
        self.assertNotIn(999,[s['value_num'] for s in detail['specifications']])
        sources = self.views.sku_sources(repo,detail)
        self.assertEqual({s['source_id'] for s in sources},{'src1','src2'})
        self.assertIsNone(self.views.sku_details(repo,'unknown'))
