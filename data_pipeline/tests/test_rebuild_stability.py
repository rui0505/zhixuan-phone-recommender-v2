import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))


class RebuildTests(unittest.TestCase):
    def test_rebuild_has_same_business_digest(self):
        import common
        self.assertTrue(hasattr(common,'business_digest'),'business digest missing')
        with tempfile.TemporaryDirectory() as tmp:
            one=Path(tmp)/'one.sqlite'; two=Path(tmp)/'two.sqlite'
            for path,order,time in [(one,[('S1',100),('S2',200)],'old'),(two,[('S2',200),('S1',100)],'new')]:
                con=sqlite3.connect(path)
                con.executescript('CREATE TABLE skus(sku_id TEXT,storage_gb REAL); CREATE TABLE dataset_releases(dataset_version TEXT,built_at TEXT);')
                con.executemany('INSERT INTO skus VALUES (?,?)',order)
                con.execute('INSERT INTO dataset_releases VALUES (?,?)',('v1',time)); con.commit(); con.close()
            self.assertEqual(common.business_digest(one),common.business_digest(two))
            con=sqlite3.connect(two); con.execute('UPDATE skus SET storage_gb=300 WHERE sku_id="S1"');con.commit();con.close()
            self.assertNotEqual(common.business_digest(one),common.business_digest(two))
