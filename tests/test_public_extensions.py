import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient
from tests.api_fixture import make_release, request_payload
from catalog.release import ReleaseStore


class PublicExtensions(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.db=make_release(self.root)
        self.release=ReleaseStore(self.db).get()

    def test_image_association_is_exact_and_fail_closed(self):
        from api.images import ImageCatalog
        media=self.root/'media'; media.mkdir(); image=media/'A1.webp'; image.write_bytes(b'RIFF-test-WEBP')
        manifest=self.root/'image_manifest.json'
        document={'approval':'OWNER_APPROVED_PUBLIC_DISPLAY','snapshot_sha256':self.release.key['snapshot_sha256'],
                  'models':{'M1':{'filename':'A1.webp','sha256':hashlib.sha256(image.read_bytes()).hexdigest(),'color':'测试色'}}}
        manifest.write_text(json.dumps(document),encoding='utf-8')
        images=ImageCatalog(manifest,media,self.release.key['snapshot_sha256'])
        self.assertEqual(images.for_model('M1')['url'],'/media/A1.webp')
        self.assertIsNone(images.for_model('M2'))
        self.assertIsNone(images.path_for('../image_manifest.json'))
        image.write_bytes(b'changed'); self.assertIsNone(images.path_for('A1.webp'))
        document['models']['M1']['filename']='../secret.webp'; manifest.write_text(json.dumps(document),encoding='utf-8')
        self.assertIsNone(ImageCatalog(manifest,media,self.release.key['snapshot_sha256']).for_model('M1'))
        document['approval']='PENDING'; manifest.write_text(json.dumps(document),encoding='utf-8')
        self.assertEqual(ImageCatalog(manifest,media,self.release.key['snapshot_sha256']).count,0)

    def test_explanation_uses_bound_fact_ids_and_safe_fallback(self):
        from api.explanations import ExplanationService
        from api.app import create_app
        from api.contracts import parse_recommendation
        app=create_app(self.db); body=request_payload(self.release)
        result=app.state.service.recommend(parse_recommendation(body,app.state.service.meta()['brands']))
        from catalog.evidence import build_evidence
        bundle=build_evidence(result.di,result.result,result.needs,sku_ids=['S1'])
        engine=ExplanationService(enabled=True,key='test-only-not-a-real-key',daily_limit=1)
        fact=next(iter(bundle['facts']))
        with patch('api.explanations.post_chat',return_value=({'choices':[{'message':{'content':json.dumps({'fact_ids':[fact]})}}]},None)) as post:
            response=engine.explain(bundle)
            self.assertEqual(response['mode'],'deepseek_verified_facts')
            self.assertIn(fact,response['text']); self.assertEqual(response['ranking_sha256'],result.result.ranking_sha256)
            self.assertEqual(post.call_args.kwargs['retries'],0)
        self.assertEqual(engine.explain(bundle)['mode'],'template')  # limit cannot trigger another paid call
        broken=ExplanationService(enabled=True,key='test-only-not-a-real-key')
        with patch('api.explanations.post_chat',return_value=({'choices':[{'message':{'content':'{"fact_ids":["MADE-UP"]}'}}]},None)):
            response=broken.explain(bundle)
            self.assertEqual(response['mode'],'template'); self.assertNotIn('MADE-UP',response['text'])

    def test_explanation_endpoint_rejects_forged_scores_and_preserves_ranking(self):
        from api.app import create_app
        with TestClient(create_app(self.db)) as client:
            body=request_payload(self.release); ranked=client.post('/api/v1/recommend',json=body).json()
            request={'request':body,'fingerprints':{k:ranked[k] for k in ('decision_sha256','ranking_sha256')},'sku_id':'S1'}
            response=client.post('/api/v1/explain',json=request)
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['mode'],'template')
            self.assertEqual(response.json()['ranking_sha256'],ranked['ranking_sha256'])
            request['score']=999
            self.assertEqual(client.post('/api/v1/explain',json=request).status_code,422)
            del request['score']; request['fingerprints']['ranking_sha256']='0'*64
            self.assertEqual(client.post('/api/v1/explain',json=request).status_code,409)

    def test_api_media_is_allowlisted_not_general_file_access(self):
        from api.app import create_app
        from api.images import ImageCatalog
        media=self.root/'media'; media.mkdir(); (media/'secret.txt').write_text('SECRET',encoding='utf-8')
        with TestClient(create_app(self.db,image_catalog=ImageCatalog(self.root/'none.json',media,self.release.key['snapshot_sha256']))) as client:
            self.assertEqual(client.get('/media/secret.txt').status_code,404)
            self.assertEqual(client.get('/media/../source.sqlite').status_code,404)

