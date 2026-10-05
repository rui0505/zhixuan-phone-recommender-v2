import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from contextlib import closing
from catalog_fixture import create_catalog,insert


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.source=self.root/'source.sqlite'; self.destination=self.root/'data/catalog.sqlite'
        self.con=create_catalog(self.source)
    def tearDown(self):
        self.con.close(); self.temp.cleanup()
    def export(self,**kwargs):
        from scripts.export_snapshot import export_snapshot
        return export_snapshot(self.source,self.destination,approved_asset_ids=set(),
                               release_status=kwargs.get('status',{'machine_acceptance':'PASS','overall_acceptance':'PENDING','wps_review':'NOT_CHECKED'}))
    def test_snapshot_business_values_equal_verified_source(self):
        manifest=self.export(); self.assertEqual(manifest['status'],'PASS')
        with closing(sqlite3.connect(self.destination)) as c:
            self.assertEqual(c.execute('select sku_id,storage_gb from skus order by sku_id').fetchall(), [('S1',256),('S2',256)])
            self.assertEqual(c.execute('select price_id,amount from price_snapshots order by price_id').fetchall(),[('P1',1000),('P2',2000)])
            self.assertEqual(c.execute('pragma foreign_key_check').fetchall(),[])
        from catalog.repository import CatalogRepository
        self.assertIsNotNone(CatalogRepository(str(self.destination)).get_model_details(model_id='M1'))
    def test_release_excludes_private_paths_and_raw_cells(self):
        insert(self.con,'sources',source_id='src',raw_text=r'C:\Users\private\source.xlsx',url='https://example.com/test')
        insert(self.con,'raw_rows',source_record_id='raw',file_key='private',sheet_name='secret',source_row_no=1,is_header_row=0,values_json='secret quote',value_types_json='[]')
        self.con.commit(); self.export()
        with closing(sqlite3.connect(self.destination)) as c:
            self.assertEqual(c.execute('select count(*) from raw_rows').fetchone()[0],0)
            self.assertEqual(c.execute('select raw_text from sources').fetchone()[0],None)
        self.assertNotIn(b'private',self.destination.read_bytes())
    def test_unapproved_images_not_published(self):
        insert(self.con,'assets',asset_id='A1',model_id='M1',extracted_path='C:/private/image.jpg')
        self.con.execute("update phone_models set cover_asset_id='A1'"); self.con.commit()
        manifest=self.export()
        with closing(sqlite3.connect(self.destination)) as c:
            self.assertEqual(c.execute('select count(*) from assets').fetchone()[0],0)
            self.assertIsNone(c.execute('select cover_asset_id from phone_models').fetchone()[0])
        self.assertEqual(manifest['images']['published_count'],0)
    def test_failed_check_does_not_replace_existing_snapshot(self):
        self.export(); before=self.destination.read_bytes()
        with self.assertRaises(ValueError): self.export(status={'machine_acceptance':'FAIL'})
        self.assertEqual(before,self.destination.read_bytes())
    def test_excluded_quote_specs_leave_no_orphan_provenance(self):
        insert(self.con,'specifications',spec_id='quote',subject_type='model',subject_id='M1',
               spec_key='review_conclusion_text',value_text='long third-party quote')
        insert(self.con,'spec_provenance',spec_id='quote',source_record_id='row1',source_col='review')
        self.con.commit(); self.export()
        with closing(sqlite3.connect(self.destination)) as c:
            self.assertEqual(c.execute("select count(*) from spec_provenance where spec_id='quote'").fetchone()[0],0)
    def test_checker_rejects_data_in_non_allowlisted_column(self):
        self.export()
        with closing(sqlite3.connect(self.destination)) as c:
            insert(c,'sources',source_id='injected',raw_text='unapproved complete review'); c.commit()
        from scripts.export_snapshot import sha
        path=self.destination.with_name('release_manifest.json')
        manifest=json.loads(path.read_text(encoding='utf-8'))
        manifest['snapshot_sha256']=sha(self.destination);manifest['table_counts']['sources']=1
        path.write_text(json.dumps(manifest),encoding='utf-8')
        from scripts.check_release import check_release
        self.assertEqual(check_release(self.root)['status'],'FAIL')
    def test_manifest_publish_failure_restores_old_database(self):
        self.export(); before=self.destination.read_bytes()
        self.con.execute('update skus set storage_gb=512'); self.con.commit()
        import os
        replace=os.replace
        def fail_manifest(a,b):
            if Path(b).name=='release_manifest.json': raise OSError('simulated manifest failure')
            return replace(a,b)
        with patch('scripts.export_snapshot.os.replace',side_effect=fail_manifest),self.assertRaises(OSError): self.export()
        self.assertEqual(self.destination.read_bytes(),before)
