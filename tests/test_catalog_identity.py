from pathlib import Path
import sqlite3
import tempfile
import unittest
from catalog.repository import CatalogRepository


class CatalogIdentityTests(unittest.TestCase):
    def test_satellite_alias_is_ambiguous_not_deduplicated(self):
        self.assertTrue(hasattr(CatalogRepository, 'resolve_legacy_sku_id'), 'legacy ambiguity resolver is absent')
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'catalog.sqlite'
            con = sqlite3.connect(db)
            con.executescript("CREATE TABLE phone_models(model_id TEXT,display_name TEXT); CREATE TABLE skus(sku_id TEXT,model_id TEXT,ram_raw TEXT,storage_raw TEXT);")
            con.execute('INSERT INTO phone_models VALUES (?,?)', ('M1', 'vivo X200 Pro'))
            con.executemany('INSERT INTO skus VALUES (?,?,?,?)', [('S1','M1','16GB','1TB'), ('S2','M1','16GB','1TB')])
            con.commit()
            con.close()
            import hashlib
            # Independent frozen legacy wire format, not the resolver under test.
            old = 'SKU-' + hashlib.sha1('vivo X200 Pro\x0116GB\x011TB'.encode()).hexdigest()[:8].upper()
            result = CatalogRepository(str(db)).resolve_legacy_sku_id(old)
            self.assertEqual(result['status'], 'ambiguous')
            self.assertEqual(result['sku_ids'], ['S1', 'S2'])
            self.assertEqual(CatalogRepository(str(db)).resolve_legacy_sku_id('SKU-UNKNOWN')['status'], 'not_found')

    def test_spec_provenance_returns_all_record_links(self):
        self.assertTrue(hasattr(CatalogRepository, 'get_spec_provenance'), 'provenance query is absent')
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'catalog.sqlite'
            con = sqlite3.connect(db)
            con.execute('CREATE TABLE spec_provenance(spec_id TEXT,source_record_id TEXT,source_col TEXT,source_id TEXT)')
            con.executemany('INSERT INTO spec_provenance VALUES (?,?,?,?)', [('SP1','R1','重量',None),('SP1','R2','重量',None)])
            con.commit()
            con.close()
            self.assertEqual([r['source_record_id'] for r in CatalogRepository(str(db)).get_spec_provenance('SP1')], ['R1', 'R2'])


if __name__ == '__main__':
    unittest.main()
