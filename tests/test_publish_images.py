import hashlib
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest


class PublishImages(unittest.TestCase):
    def test_exact_name_join_does_not_reuse_old_model_ids_or_guess(self):
        from scripts.publish_images import publish
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); source=root/'source.sqlite'; target=root/'target.sqlite'; assets=root/'assets'; assets.mkdir()
            from PIL import Image
            Image.new('RGB',(8,8),'white').save(assets/'cover.webp')
            with closing(sqlite3.connect(source)) as db:
                db.executescript('CREATE TABLE phone_models(model_id TEXT,brand TEXT,display_name TEXT); CREATE TABLE assets(asset_id TEXT,model_id TEXT,derived_path TEXT,color_name TEXT,is_cover INTEGER);')
                db.execute('INSERT INTO phone_models VALUES(?,?,?)',('OLD','Brand','Phone Pro'))
                db.execute('INSERT INTO assets VALUES(?,?,?,?,?)',('A1','OLD','cover.webp','白色',1))
                db.commit()
            with closing(sqlite3.connect(target)) as db:
                db.executescript('CREATE TABLE phone_models(model_id TEXT,brand TEXT,display_name TEXT);')
                db.executemany('INSERT INTO phone_models VALUES(?,?,?)',[('NEW','Brand','Phone Pro'),('OLD','Brand','Phone'),('M3','Brand','Phone Pro 卫星版')])
                db.commit()
            before=hashlib.sha256(target.read_bytes()).hexdigest()
            manifest=publish(source,assets,target,root/'published',approved=True)
            self.assertEqual(list(manifest['models']),['NEW'])
            self.assertEqual(manifest['snapshot_sha256'],before)
            self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(),before)
            self.assertCountEqual(manifest['missing_models'],['OLD','M3'])
            self.assertNotIn(str(root),json.dumps(manifest))
            with self.assertRaises(ValueError):publish(source,assets,target,root/'unapproved',approved=False)

