from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from api.contracts import ApiError, Pagination, parse_recommendation, parse_catalog_query
from catalog.adapter import build_input
from catalog.release import ReleaseStore, file_sha
from catalog.evidence import build_evidence, verify_bundle
from engine import rank_decision_input
from tests.api_fixture import make_release, request_payload
from tests.catalog_fixture import insert


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('api.service'), 'canonical service missing')
        from api.service import CatalogService
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.db = make_release(Path(self.temp.name))
        self.store = ReleaseStore(self.db); self.release = self.store.get()
        self.service = CatalogService(self.store)
        self.body = request_payload(self.release)
        self.request = parse_recommendation(self.body, ['品牌1','品牌2'])

    def test_same_input_matches_core(self):
        di = build_input(self.release.repo, self.request.needs, self.request.weights,6.5,'new')
        expected = rank_decision_input(di,target_screen=6.5)
        outcome = self.service.recommend(self.request)
        self.assertEqual(outcome.result.ranked.to_dict('records'),expected.ranked.to_dict('records'))
        for name in ('decision_sha256','ranking_sha256','status'):
            self.assertEqual(getattr(outcome.result,name),getattr(expected,name))
        self.assertEqual(outcome.result.backups.to_dict('records'),expected.backups.to_dict('records'))
        self.assertEqual(outcome.result.unscored_candidates.to_dict('records'),expected.unscored_candidates.to_dict('records'))
        self.assertEqual(len(outcome.result.ranked),2); verify_bundle(outcome.bundle)

    def test_pagination_never_changes_ranking_hash(self):
        outcome=self.service.recommend(self.request)
        a=self.service.recommendation_payload(outcome,Pagination(1,1))
        b=self.service.recommendation_payload(outcome,Pagination(2,1))
        self.assertEqual(a['ranking_sha256'],b['ranking_sha256'])
        self.assertEqual(a['ranked_total'],2); self.assertEqual(b['ranked_total'],2)
        self.assertNotEqual(a['ranked'][0]['sku_id'],b['ranked'][0]['sku_id'])
        self.assertEqual(list(a['explanations']),[a['ranked'][0]['sku_id']])
        self.assertNotIn(b['ranked'][0]['sku_id'],a['explanations'][a['ranked'][0]['sku_id']])

    def test_interleaved_users_and_inputs_are_independent(self):
        before=deepcopy(self.body); first=self.service.recommend(self.request)
        other=request_payload(self.release,needs={'budget':{'min':None,'max':1100},'min_storage':None,'brands':{'include':[],'exclude':[]}})
        second=self.service.recommend(parse_recommendation(other,['品牌1','品牌2']))
        self.assertEqual(second.result.status,'insufficient_comparable_data')
        verify_bundle(first.bundle); self.assertEqual(self.body,before)
        self.assertEqual(len(self.service.recommendation_payload(first,Pagination())['ranked']),2)

    def test_no_candidates_or_missing_game_do_not_invent_scores(self):
        for updates,status in (({'needs':{'budget':{'min':None,'max':1},'min_storage':None,'brands':{'include':[],'exclude':[]}}},'no_candidates'),
                               ({'weights':{'honor_of_kings_avg_fps':10}},'insufficient_comparable_data')):
            request=parse_recommendation(request_payload(self.release,**updates),['品牌1','品牌2'])
            payload=self.service.recommendation_payload(self.service.recommend(request),Pagination())
            self.assertEqual(payload['status'],status); self.assertEqual(payload['ranked'],[])
            self.assertIsNone(payload['evidence']); self.assertEqual(payload['explanations'],{})

    def test_target_and_weights_match_confirmed_request(self):
        request=parse_recommendation(request_payload(self.release,weights={'screen_size_inch':7,'price_cny':3},target_screen=6.1),['品牌1','品牌2'])
        p=self.service.recommendation_payload(self.service.recommend(request),Pagination())
        self.assertEqual(p['target_screen'],6.1); self.assertEqual(p['effective_weights'],{'price_cny':0.3,'screen_size_inch':0.7})
        self.assertEqual(p['selection_rules']['target_screen'],6.1)

    def test_unknown_sku_is_404(self):
        with self.assertRaises(ApiError) as error:self.service.details('ABSENT')
        self.assertEqual(error.exception.status,404)

    def test_multiple_skus_of_one_model_are_not_two_models(self):
        def prepare(con):
            insert(con,'skus',sku_id='S3',variant_id='V1',model_id='M1',ram_raw='12GB',storage_raw='512GB',storage_gb=512)
            insert(con,'price_snapshots',price_id='P3',sku_id='S3',amount=1500,currency='CNY',condition='new',price_kind='official_current_price')
        db=make_release(Path(self.temp.name)/'single',prepare=prepare)
        from api.service import CatalogService
        store=ReleaseStore(db); service=CatalogService(store)
        request=parse_recommendation(request_payload(store.get(),weights={'price_cny':1},needs={'budget':{'min':None,'max':None},'min_storage':None,'brands':{'include':['品牌1'],'exclude':[]}}),['品牌1','品牌2'])
        outcome=service.recommend(request)
        self.assertEqual(len(outcome.di['candidates']),2)
        self.assertEqual(outcome.result.status,'insufficient_comparable_data'); self.assertIsNone(outcome.bundle)

    def test_name_stays_data_and_unsafe_sources_are_not_links(self):
        def prepare(con):
            con.execute("UPDATE phone_models SET display_name=? WHERE model_id='M1'",('<script>window.pwn=1</script>',))
            insert(con,'sources',source_id='BAD',url='javascript:alert(1)')
            con.execute("UPDATE specifications SET source_id='BAD' WHERE subject_id='S1'")
        from api.service import CatalogService
        store=ReleaseStore(make_release(Path(self.temp.name)/'unsafe',prepare=prepare)); service=CatalogService(store)
        self.assertEqual(service.details('S1')['model']['display_name'],'<script>window.pwn=1</script>')
        self.assertEqual(service.details('S1')['sources'][0]['urls'],[])
        p=service.recommendation_payload(service.recommend(parse_recommendation(request_payload(store.get()),['品牌1','品牌2'])),Pagination())
        self.assertNotIn('javascript:',json.dumps(p,ensure_ascii=False))

    def test_catalog_search_is_literal_and_filters_before_pagination(self):
        p=self.service.catalog(parse_catalog_query([('max_price','1100'),('page_size','1')],['品牌1','品牌2']))
        self.assertEqual(p['total'],1); self.assertEqual(p['items'][0]['sku_id'],'S1')
        for q in ('%','[','NOT FOUND'):
            self.assertEqual(self.service.catalog(parse_catalog_query([('q',q)],['品牌1','品牌2']))['total'],0)

    def test_public_projection_and_missing_are_safe(self):
        p=self.service.details('S1'); text=json.dumps(p,ensure_ascii=False,allow_nan=False)
        for forbidden in ('source_record_id','local_path','raw_rows','evidence_snippets','cover_asset_id'):
            self.assertNotIn('"'+forbidden+'"',text)
        self.assertIsNone(p['model']['release_month'])
        self.assertEqual([x['sku']['sku_id'] for x in self.service.compare(['S2','S1'])['items']],['S2','S1'])

    def test_release_mismatch_and_mid_compute_change_rejected(self):
        bad=deepcopy(self.body); bad['expected_release']['snapshot_sha256']='0'*64
        with self.assertRaises(ApiError) as e:self.service.recommend(parse_recommendation(bad,['品牌1','品牌2']))
        self.assertEqual(e.exception.code,'release_changed')
        original=build_input
        def change(*args,**kwargs):
            di=original(*args,**kwargs)
            self.db.with_name('release_manifest.json').write_text('{}',encoding='utf-8')
            return di
        with patch('catalog.default_references.build_input',side_effect=change):
            with self.assertRaises(ApiError) as e:self.service.recommend(self.request)
        self.assertEqual(e.exception.status,409)

    def test_export_recomputes_full_ranking_and_checks_hashes(self):
        p=self.service.recommendation_payload(self.service.recommend(self.request),Pagination(1,1))
        fps={k:p[k] for k in ('decision_sha256','ranking_sha256')}
        exported=json.loads(self.service.export(self.request,fps,'json'))
        self.assertEqual(len(exported['ranking']),2)
        with self.assertRaises(ApiError) as e:self.service.export(self.request,{**fps,'ranking_sha256':'0'*64},'csv')
        self.assertEqual(e.exception.code,'decision_changed')

    def test_production_parity_and_baseline_bytes_preserved(self):
        from api.service import CatalogService
        db=Path(__file__).resolve().parents[1]/'data/catalog.sqlite'; before=file_sha(db)
        release=ReleaseStore(db).get(); service=CatalogService(ReleaseStore(db))
        request=parse_recommendation(request_payload(release),service.meta()['brands'])
        di=build_input(release.repo,request.needs,request.weights,request.target_screen,request.mode)
        core=rank_decision_input(di,target_screen=request.target_screen)
        actual=service.recommend(request).result
        self.assertEqual(actual.ranking_sha256,core.ranking_sha256)
        self.assertEqual(actual.decision_sha256,core.decision_sha256)
        # 2026-10-26：默认权重新增 电池容量2 / 处理器档位2，两者都是正权重，
        # 没有这两项数据的机型不再具备资格——候选从 51 条降到 40 条（少 4 个机型）。
        # 这是「未提及给默认值」的代价，不是 bug。
        self.assertEqual(len(actual.ranked),40); self.assertEqual(file_sha(db),before)
