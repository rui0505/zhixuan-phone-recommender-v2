"""Consumer-facing explanations and evidence-based catalog categories."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from starlette.testclient import TestClient
from api.app import create_app
from api.contracts import Pagination, parse_recommendation
from catalog.release import file_sha
from tests.api_fixture import make_release, request_payload
from tests.catalog_fixture import game_run, insert


def category_fixture(con):
    insert(con, 'sources', source_id='REVIEW', url='https://example.com/review', publisher='测试机构')
    game_run(con, 'M1', 'G1', {'honor_of_kings_avg_fps':100})
    con.execute("UPDATE benchmark_runs SET source_id='REVIEW' WHERE run_id='G1'")
    insert(con, 'benchmark_runs', run_id='PHOTO', model_id='M1', subject_type='model', subject_id='M1',
           benchmark='DXOMARK', institution='DXOMARK', protocol='v5', applicable_scope='model', source_id='REVIEW')
    insert(con, 'metric_observations', observation_id='PHOTO1', run_id='PHOTO', metric_key='dxomark_photo',
           value_num=120, unit='分')


class ConsumerFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.db=make_release(Path(self.temp.name),prepare=category_fixture)
        self.client=TestClient(create_app(db_path=self.db));self.addCleanup(self.client.close)
        self.service=self.client.app.state.service

    def test_meta_and_card_categories_allow_overlap(self):
        meta=self.client.get('/api/v1/meta').json()
        self.assertIn('categories',meta)
        self.assertEqual({c['id'] for c in meta['categories']},{'gaming','senior','photography'})
        response=self.client.get('/api/v1/catalog?mode=new&category=gaming')
        self.assertEqual(response.status_code,200,response.text)
        payload=response.json();self.assertEqual(payload['total'],1)
        row=payload['items'][0];self.assertEqual(row['sku_id'],'S1')
        assessments={c['id']:c for c in row['category_assessments']}
        self.assertEqual(assessments['gaming']['status'],'evidence_available')
        self.assertEqual(assessments['photography']['status'],'evidence_available')
        self.assertEqual(assessments['senior']['status'],'pending_review')

    def test_category_filters_before_pagination_and_retains_budget(self):
        response=self.client.get('/api/v1/catalog?mode=new&category=gaming&page_size=1')
        self.assertEqual(response.status_code,200,response.text)
        payload=response.json()
        self.assertEqual(payload['total'],1)
        self.assertEqual(self.client.get('/api/v1/catalog?category=gaming&min_price=1500').json()['total'],0)
        self.assertEqual(self.client.get('/api/v1/catalog?category=photography&q=NOT_FOUND').json()['total'],0)
        senior=self.client.get('/api/v1/catalog?category=senior').json()
        self.assertEqual(senior['total'],0)
        self.assertIn('待',senior['category_notice'])
        pending=self.client.get('/api/v1/catalog?category=senior&category_status=pending_review&page_size=1').json()
        self.assertEqual(pending['total'],2);self.assertEqual(len(pending['items']),1)
        self.assertEqual(self.client.get('/api/v1/catalog?category=senior&category_status=pending_review&page=2&page_size=1').json()['items'][0]['sku_id'],'S2')

    def test_category_query_rejects_unknown_and_duplicate_values(self):
        for suffix in ('category=BAD','category=gaming&category=senior','category_status=pending_review',
                       'category=all&category_status=pending_review','category=gaming&category_status=BAD'):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.client.get('/api/v1/catalog?'+suffix).status_code,422)

    def test_details_include_scoped_category_sources(self):
        payload=self.client.get('/api/v1/skus/S1').json()
        self.assertIn('category_assessments',payload['sku'])
        assessments=payload['sku']['category_assessments']
        game=next(c for c in assessments if c['id']=='gaming')
        self.assertTrue(game['evidence_refs']);self.assertIn('https://example.com/review',str(game['evidence_refs']))
        self.assertIn('model',str(game['evidence_refs']))

    def test_reason_is_bound_to_this_page_and_ranking_is_unchanged(self):
        release=self.service.store.get();before=file_sha(self.db)
        request=parse_recommendation(request_payload(release),['品牌1','品牌2'])
        outcome=self.service.recommend(request);original=deepcopy(outcome.bundle)
        p=self.service.recommendation_payload(outcome,Pagination(1,1))
        self.assertIn('recommendation_reasons',p)
        self.assertEqual(set(p['recommendation_reasons']),{p['ranked'][0]['sku_id']})
        reason=p['recommendation_reasons'][p['ranked'][0]['sku_id']]
        self.assertTrue(reason['summary']);self.assertTrue(reason['matches'])
        self.assertTrue(reason['tradeoffs'] or reason['limitations'])
        self.assertNotIn('snapshot_sha256',reason['summary'])
        self.assertEqual(outcome.bundle,original)
        self.assertEqual(p['ranking_sha256'],outcome.result.ranking_sha256)
        self.assertEqual(file_sha(self.db),before)


if __name__=='__main__':unittest.main()
