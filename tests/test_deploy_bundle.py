from pathlib import Path
import tempfile
import unittest
import zipfile

class BundleTests(unittest.TestCase):
    def test_allowlist_only_and_all_images_bound(self):
        from scripts.build_deploy_bundle import build
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            path=build(root,Path(temp)/'bundle.zip')
            with zipfile.ZipFile(path) as package:
                names=package.namelist();self.assertEqual(len(names),len(set(names)))
                self.assertIn('data/catalog.sqlite',names);self.assertIn('frontend/index.html',names)
                self.assertIn('schemas.py',names);self.assertIn('deploy/install.sh',names)
                self.assertEqual(len([n for n in names if n.startswith('data/media/')]),147)
                self.assertFalse(any(n.startswith(('local_evidence/','tests/','data_pipeline/')) or '.env' in n or n.endswith(('.xlsx','.zip','.pyc')) for n in names))
                self.assertIsNone(package.testzip())
