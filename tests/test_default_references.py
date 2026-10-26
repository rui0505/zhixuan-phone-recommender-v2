from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from starlette.testclient import TestClient
from api.app import create_app
from api.contracts import ApiError, DEFAULT_WEIGHTS, parse_recommendation
from api.needs import NeedsService
from providers.jev import MockClassifier
from tests.api_fixture import make_release, request_payload
from tests.catalog_fixture import insert
from catalog.release import ReleaseStore


class DefaultReferenceTests(unittest.TestCase):
    def test_extraction_retains_positive_initial_weights_and_unmentioned_levels(self):
        with tempfile.TemporaryDirectory() as tmp:
            service=NeedsService(enabled=True,key='OFFLINE',classifier=MockClassifier(),quota_path=Path(tmp)/'quota.sqlite')
            value=service.extract('3000元以内',[])
        self.assertEqual(value['draft']['weights']['dxomark_photo'],1)
        self.assertEqual(value['draft']['weights']['honor_of_kings_avg_fps'],1)
        self.assertIn('dxomark_photo',value['draft']['defaulted_metrics'])
        self.assertEqual(next(p['level'] for p in value['extraction']['preferences'] if p['criterion']=='photo'),'未提及')
        self.assertEqual(DEFAULT_WEIGHTS['honor_of_kings_avg_fps'],1)

    def test_default_real_request_defers_incomplete_measurements_and_exports_same_decision(self):
        with TestClient(create_app()) as client:
            meta=client.get('/api/v1/meta').json()
            body={**meta['defaults'],'evaluation_mode':'measured','needs':{'budget':{'min':None,'max':None},'min_storage':None,'brands':{'include':[],'exclude':[]}},'method':'hybrid_rrf','game_scope':'unconfirmed','confirmed':True,'expected_release':meta['release']}
            response=client.post('/api/v1/recommend',json=body)
            self.assertEqual(response.status_code,200,response.text)
            value=response.json()
            self.assertEqual(value['status'],'ready')
            self.assertEqual(value['ranked_total'],40)
            policy=value['selection_rules']['default_reference_policy']
            self.assertEqual(policy['requested_weights']['dxomark_photo'],1)
            self.assertEqual(policy['requested_weights']['honor_of_kings_avg_fps'],1)
            self.assertEqual({d['metric_key'] for d in policy['deferred']},{'dxomark_photo','honor_of_kings_avg_fps'})
            self.assertEqual(value['effective_weights']['dxomark_photo'],0)
            self.assertEqual(body['weights']['dxomark_photo'],1)
            exported=client.post('/api/v1/export/json',json={'request':body,'fingerprints':{k:value[k] for k in ('decision_sha256','ranking_sha256')}})
            self.assertEqual(exported.status_code,200,exported.text)
            self.assertEqual(len(exported.json()['ranking']),40)
            self.assertEqual(exported.json()['evidence']['decision_context']['selection_rules']['default_reference_policy'],policy)
            explicit=deepcopy(body)
            explicit['weights']['honor_of_kings_avg_fps']=3
            explicit['defaulted_metrics'].remove('honor_of_kings_avg_fps')
            rejected=client.post('/api/v1/recommend',json=explicit)
            self.assertEqual(rejected.status_code,422)
            self.assertEqual(rejected.json()['error']['code'],'game_scope_unconfirmed')

    def test_complete_default_photo_is_used_and_partial_default_photo_is_deferred(self):
        for count in (1,2):
            def prepare(con):
                for n in range(1,count+1):
                    insert(con,'benchmark_runs',run_id=f'PHOTO{n}',model_id=f'M{n}',subject_type='model',subject_id=f'M{n}',benchmark='DXOMARK',institution='DXOMARK',protocol='dxomark_v5',applicable_scope='model')
                    insert(con,'metric_observations',observation_id=f'PHOTO{n}',run_id=f'PHOTO{n}',metric_key='dxomark_photo',value_num=100+n,comparison_group='dxomark_v5')
            with self.subTest(count=count), tempfile.TemporaryDirectory() as tmp:
                db=make_release(Path(tmp),prepare=prepare)
                release=ReleaseStore(db).get()
                body=request_payload(release,method='hybrid_rrf',weights={'storage_gb':3,'dxomark_photo':1},defaulted_metrics=['dxomark_photo'])
                with TestClient(create_app(db)) as client:
                    response=client.post('/api/v1/recommend',json=body)
                    self.assertEqual(response.status_code,200,response.text)
                    value=response.json()
                    self.assertEqual(value['ranked_total'],2)
                    self.assertEqual(value['effective_weights']['dxomark_photo']>0,count==2)
                    if count==1:
                        explicit={**body,'weights':{'storage_gb':3,'dxomark_photo':4},'defaulted_metrics':[]}
                        value=client.post('/api/v1/recommend',json=explicit).json()
                        self.assertEqual(value['status'],'insufficient_comparable_data')
                        self.assertGreater(value['effective_weights']['dxomark_photo'],0)

    def test_default_metadata_cannot_downgrade_explicit_high_weights(self):
        with tempfile.TemporaryDirectory() as tmp:
            release=ReleaseStore(make_release(Path(tmp))).get()
            for marker in (['dxomark_photo'],['unknown'],['dxomark_photo','dxomark_photo'],'dxomark_photo'):
                body=request_payload(release,weights={'dxomark_photo':9},defaulted_metrics=marker)
                with self.subTest(marker=marker),self.assertRaises(ApiError):parse_recommendation(body,[])
