from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from starlette.testclient import TestClient
from api.app import create_app
from api.contracts import DEFAULT_WEIGHTS
from catalog.spec_reference import reference_values
from tests.api_fixture import make_release, request_payload
from catalog.release import ReleaseStore


class SpecReferenceTests(unittest.TestCase):
    def test_fixed_anchors_zero_missing_and_game_components(self):
        values=reference_values({'storage_gb':256,'weight_g':260,'battery_capacity_mah':5500,
            'chipset_tier':80,'screen_size_inch':6.5},6.5,'50MP主摄，12MP超广角，10MP长焦')
        self.assertEqual(values['ref_storage'],40)
        self.assertEqual(values['ref_weight'],0)
        self.assertEqual(values['ref_battery'],50)
        self.assertEqual(values['ref_screen'],100)
        self.assertEqual(values['ref_game'],74)
        self.assertGreater(values['ref_photo'],60)
        missing=reference_values({'chipset_tier':80},6.5,None)
        self.assertIsNone(missing['ref_game'])
        self.assertIsNone(missing['ref_photo'])
        self.assertIsNone(reference_values({},None,'未知')['ref_screen'])

    def test_real_api_seven_scores_provenance_export_and_modes(self):
        with TestClient(create_app()) as client:
            meta=client.get('/api/v1/meta').json()
            body={**meta['defaults'],'needs':{'budget':{'min':None,'max':None},'min_storage':None,
                'brands':{'include':[],'exclude':[]}},'method':'hybrid_rrf','evaluation_mode':'spec_reference',
                'game_scope':'unsupported','confirmed':True,'expected_release':meta['release']}
            response=client.post('/api/v1/recommend',json=body)
            self.assertEqual(response.status_code,200,response.text)
            data=response.json()
            self.assertEqual(data['status'],'ready')
            self.assertEqual(data['evaluation_mode'],'spec_reference')
            self.assertGreaterEqual(data['ranked_total'],35)
            self.assertEqual(len(data['effective_weights']),7)
            self.assertTrue(all(v>0 for v in data['effective_weights'].values()))
            for card in data['ranked']:
                scores={s['metric_key']:s for s in card['reference_scores']}
                self.assertEqual(len(scores),7)
                self.assertTrue(all(s['estimated'] and s['basis'] and 0<=s['score']<=100 for s in scores.values()))
                self.assertAlmostEqual(scores['ref_game']['score'],round(.8*scores['ref_chipset']['score']+.2*scores['ref_battery']['score'],2),places=2)
            facts=data['evidence']['facts'].values()
            self.assertTrue(all(f['status']=='derived_reference' and f['raw_inputs'] for f in facts))
            exported=client.post('/api/v1/export/json',json={'request':body,'fingerprints':{k:data[k] for k in ('decision_sha256','ranking_sha256')}})
            self.assertEqual(exported.status_code,200,exported.text)
            self.assertEqual(exported.json()['evidence']['decision_context']['selection_rules']['evaluation_mode'],'spec_reference')
            measured=deepcopy(body); measured['evaluation_mode']='measured'; measured['game_scope']='unconfirmed'
            old=client.post('/api/v1/recommend',json=measured)
            self.assertEqual(old.status_code,200,old.text)
            self.assertEqual(old.json()['ranked_total'],40)
            self.assertNotEqual(data['decision_sha256'],old.json()['decision_sha256'])
            cross=client.post('/api/v1/export/json',json={'request':measured,'fingerprints':{k:data[k] for k in ('decision_sha256','ranking_sha256')}})
            self.assertEqual(cross.status_code,409)
            stale={**body,'expected_reference_rule_version':'old'}
            self.assertEqual(client.post('/api/v1/recommend',json=stale).status_code,409)
            bad={**body,'evaluation_mode':'unknown'}
            self.assertEqual(client.post('/api/v1/recommend',json=bad).status_code,422)

    def test_hard_constraints_survive_and_zero_weight_does_not_require_missing_source(self):
        with TestClient(create_app()) as client:
            meta=client.get('/api/v1/meta').json()
            body={'needs':{'budget':{'min':None,'max':4000},'min_storage':256,'brands':{'include':[],'exclude':['苹果']}},
                'weights':{'storage_gb':3},'target_screen':6.5,'mode':'new','method':'hybrid_rrf',
                'evaluation_mode':'spec_reference','confirmed':True,'expected_release':meta['release']}
            r=client.post('/api/v1/recommend',json=body)
            self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(r.json()['status'],'ready')
            for card in r.json()['ranked']:
                self.assertLessEqual(card['price_cny'],4000)
                self.assertGreaterEqual(card['storage_gb'],256)
                self.assertNotEqual(card['brand'],'苹果')

    def test_source_revision_invalidates_confirmed_requests_and_primary_fixes_are_pinned(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=make_release(Path(tmp)); release=ReleaseStore(db).get()
            with TestClient(create_app(db)) as client:
                meta=client.get('/api/v1/meta').json()
                body=request_payload(release,weights={'chipset_tier':2},evaluation_mode='spec_reference',
                    expected_reference_rule_version=meta['reference_rule_version'])
                self.assertEqual(client.post('/api/v1/recommend',json=body).status_code,200)
                path=db.with_name('chipset-primary-reference.json'); doc=json.loads(path.read_text(encoding='utf8'))
                self.assertEqual([doc['scores'][k] for k in ('天玑8300-Ultra','天玑6080','天玑6020')],[44,12,11])
                doc['version']='test-revision';path.write_text(json.dumps(doc),encoding='utf8')
                self.assertNotEqual(client.get('/api/v1/meta').json()['reference_rule_version'],meta['reference_rule_version'])
                r=client.post('/api/v1/recommend',json=body)
                self.assertEqual(r.status_code,409)
                self.assertEqual(r.json()['error']['code'],'reference_rule_changed')
