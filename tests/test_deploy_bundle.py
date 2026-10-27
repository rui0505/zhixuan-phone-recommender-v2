from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile

class BundleTests(unittest.TestCase):
    def test_extracted_bundle_can_import_and_rank_hybrid_without_source_checkout(self):
        # Catch missing root modules by exercising only the extracted runtime.
        from scripts.build_deploy_bundle import build
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            archive=build(root,Path(temp)/'bundle.zip')
            target=Path(temp)/'runtime'
            with zipfile.ZipFile(archive) as package:package.extractall(target)
            code="""
from api.app import create_app
from api.contracts import parse_recommendation
app=create_app()
service=app.state.service
meta=service.meta()
body={**meta['defaults'],'needs':{'budget':{'min':None,'max':None},'min_storage':None,'brands':{'include':[],'exclude':[]}},'method':'hybrid_rrf','confirmed':True,'expected_release':meta['release']}
outcome=service.recommend(parse_recommendation(body,meta['brands']))
assert outcome.result.status=='ready'
assert outcome.result.algorithm_version=='hybrid-rrf-1.0.0-experimental'
assert len(outcome.result.ranked)==40
assert 'price_cny' not in outcome.result.active_criteria
print('extracted hybrid runtime PASS')
"""
            env={key:value for key,value in os.environ.items() if key!='PYTHONPATH'}
            env['PHONE_ENABLE_LLM']='false'
            env.pop('PHONE_CATALOG_PATH',None)
            result=subprocess.run([sys.executable,'-c',code],cwd=target,env=env,
                                  capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_allowlist_only_and_all_images_bound(self):
        from scripts.build_deploy_bundle import build
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            path=build(root,Path(temp)/'bundle.zip')
            with zipfile.ZipFile(path) as package:
                names=package.namelist();self.assertEqual(len(names),len(set(names)))
                self.assertIn('data/catalog.sqlite',names);self.assertIn('frontend/index.html',names)
                self.assertIn('schemas.py',names);self.assertIn('deploy/install.sh',names)
                self.assertIn('needs_extraction.py',names);self.assertIn('api/needs.py',names)
                self.assertIn('frontend/needs.mjs',names)
                self.assertIn('frontend/glass.css',names);self.assertIn('frontend/motion.mjs',names)
                self.assertIn('frontend/assets/zhixuan-ai-network.jpg',names)
                self.assertIn('frontend/assets/credits.json',names)
                self.assertIn('frontend/assets/alpine-panorama.png',names)
                self.assertNotIn('frontend/assets/zhixuan-world.png',names)
                self.assertEqual(len([n for n in names if n.startswith('data/media/')]),147)
                self.assertFalse(any(n.startswith(('local_evidence/','tests/','data_pipeline/')) or '.env' in n or n.endswith(('.xlsx','.zip','.pyc')) for n in names))
                self.assertIsNone(package.testzip())
