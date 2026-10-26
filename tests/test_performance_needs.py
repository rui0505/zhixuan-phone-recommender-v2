"""Evidence and scope survive extraction without manufacturing product performance."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from api.needs import NeedsService
from needs_extraction import extract_needs, validate_result
from providers.jev import MockClassifier, normalise_response, parse_needs
from schemas import PreferenceItem


BASE = ('weight', 'storage', 'screen', 'photo', 'game', 'battery', 'chipset')
ALL = BASE


class FixedClassifier:
    def __init__(self, payload):
        self.payload = payload

    def classify(self, text):
        return normalise_response(deepcopy(self.payload), provider='fixture', source_text=text)


def legacy_payload():
    return {'judgements': [dict(criterion=c, choice='未提及', evidence='', verdict='unknown')
                           for c in BASE], 'hard_constraints': {}, 'asks': [],
            'unsupported_needs': [], 'excluded_brands': []}


class PerformanceNeedsTests(unittest.TestCase):
    def extract(self, text, classifier=None):
        return extract_needs(text, classifier=classifier or MockClassifier())

    def draft(self, text, classifier=None):
        with tempfile.TemporaryDirectory() as root:
            service = NeedsService(enabled=True, key='OFFLINE', classifier=classifier or MockClassifier(),
                                   quota_path=Path(root)/'quota.sqlite')
            return service.extract(text, [])

    def test_unmentioned_performance_defaults_zero_while_base_defaults_one(self):
        result = self.draft('预算3000元以内')
        self.assertEqual({p['criterion'] for p in result['extraction']['preferences']}, set(ALL))
        self.assertEqual(result['draft']['weights'], {
            'weight_g': 2, 'storage_gb': 3, 'battery_capacity_mah':2, 'chipset_tier':2,
            'screen_size_inch': 1, 'dxomark_photo': 0, 'honor_of_kings_avg_fps': 0,
            'battery_mixed_usage_duration_min': 0})
        self.assertEqual(PreferenceItem('battery', '未提及').mapped_level, 0)
        self.assertEqual(PreferenceItem('storage', '未提及').mapped_level, 1)

    def test_explicit_photo_superlative_maps_to_three_with_source(self):
        result = self.draft('拍照最重要，预算3000元以内')
        self.assertIn('photo', {p['criterion'] for p in result['extraction']['preferences']})
        photo = next(p for p in result['extraction']['preferences'] if p['criterion']=='photo')
        self.assertEqual(photo['level'], '非常重要')
        self.assertEqual(photo['evidence'], '拍照最重要')
        self.assertEqual(photo['verdict'], 'extracted')
        self.assertFalse(photo['confirmed'])
        self.assertEqual(result['draft']['weights']['dxomark_photo'], 9)
        self.assertFalse(result['draft']['confirmed'])

    def test_battery_importance_remains_positive_and_unavailable(self):
        result = self.draft('续航重要，预算3000元以内')
        key = 'battery_mixed_usage_duration_min'
        self.assertEqual(result['draft']['weights'].get(key), 0)
        self.assertEqual(result['draft']['weights']['battery_capacity_mah'],2)
        self.assertEqual(next(p['level'] for p in result['extraction']['preferences'] if p['criterion']=='battery'),'未提及')
        self.assertIn('续航', [p['need'] for p in result['extraction']['unsupported_needs']])

    def test_other_game_use_is_inferred_and_never_covertly_scored_as_hok(self):
        result = self.draft('爱打和平精英，预算3000元以内')
        extraction = result['extraction']
        self.assertIn('game', {p['criterion'] for p in extraction['preferences']})
        game = next(p for p in extraction['preferences'] if p['criterion']=='game')
        self.assertEqual(game['level'], '重要')
        self.assertEqual(game['verdict'], 'inferred')
        self.assertTrue(game['needs_confirmation'])
        self.assertEqual(extraction['game_use']['names'], ['和平精英'])
        self.assertIn(extraction['game_use']['evidence'], extraction['input_text'])
        self.assertTrue(extraction['game_use']['needs_scope_confirmation'])
        self.assertEqual(result['draft']['desired_weights']['honor_of_kings_avg_fps'], 6)
        self.assertEqual(result['draft']['weights']['honor_of_kings_avg_fps'], 6)
        self.assertEqual(result['draft']['game_scope'], 'unsupported')
        self.assertTrue(extraction['game_use']['needs_scope_confirmation'])
        self.assertIn('真实游戏性能', [p['need'] for p in extraction['unsupported_needs']])

    def test_general_game_requires_scope_but_named_hok_preserves_weight(self):
        general = self.draft('游戏性能最重要')
        self.assertEqual(general['draft'].get('desired_weights', {}).get('honor_of_kings_avg_fps'), 9)
        self.assertEqual(general['draft']['weights']['honor_of_kings_avg_fps'], 9)
        self.assertEqual(general['draft']['game_scope'], 'unconfirmed')
        hok = self.draft('王者荣耀平均帧率最重要')
        self.assertFalse(hok['extraction']['game_use']['needs_scope_confirmation'])
        self.assertIsNotNone(hok['draft'])
        self.assertEqual(hok['extraction']['brands']['include'], [])
        self.assertEqual(hok['draft']['weights']['honor_of_kings_avg_fps'], 9)

    def test_later_negative_overrides_earlier_performance_preference(self):
        result = self.draft('1. 拍照最重要，续航重要\n2. 拍照不在意，续航不重要')
        self.assertEqual(result['draft']['weights']['dxomark_photo'], 0)
        self.assertEqual(result['draft']['weights'].get('battery_mixed_usage_duration_min'), 0)
        self.assertEqual(next(p['evidence'] for p in result['extraction']['preferences']
                              if p['criterion']=='photo'), '拍照不在意')

    def test_unrelated_later_turn_preserves_earlier_performance_preference(self):
        result = self.draft('1. 拍照最重要\n2. 预算3000元以内')
        self.assertEqual(result['draft']['weights']['dxomark_photo'], 9)

    def test_invented_or_empty_performance_evidence_never_activates_weight(self):
        for evidence in ('续航重要', '', '预算3000元以内'):
            with self.subTest(evidence=evidence):
                payload = legacy_payload()
                payload['judgements'].append(dict(criterion='battery', choice='非常重要',
                                                  evidence=evidence, verdict='extracted'))
                result = self.draft('预算3000元以内', FixedClassifier(payload))
                self.assertEqual(result['draft']['weights'].get('battery_mixed_usage_duration_min'), 0)
                self.assertEqual(result['status'], 'degraded')
                self.assertTrue(any(c['field']=='preferences.battery'
                                    for c in result['extraction']['clarifications']))

    def test_named_game_survives_separate_strength_clause_and_later_correction(self):
        result = self.draft('爱打和平精英，游戏性能最重要')
        self.assertEqual(result['extraction']['game_use']['names'], ['和平精英'])
        self.assertEqual(result['draft']['game_scope'], 'unsupported')
        corrected = self.draft('1. 爱打和平精英\n2. 不玩和平精英，只玩王者荣耀，游戏性能最重要')
        self.assertEqual(corrected['extraction']['game_use']['names'], ['王者荣耀'])
        self.assertEqual(corrected['draft']['game_scope'], 'honor_of_kings')

    def test_going_out_for_photos_does_not_activate_game_preference(self):
        result = self.draft('喜欢拍照，出门玩要拍很多照片，存储越大越好')
        game = next(p for p in result['extraction']['preferences'] if p['criterion']=='game')
        self.assertEqual(game['level'], '未提及')
        self.assertEqual(result['draft']['weights']['honor_of_kings_avg_fps'], 0)
        self.assertEqual([p['need'] for p in result['extraction']['unsupported_needs']], [])

    def test_rejecting_another_game_preserves_hok_priority(self):
        result = self.draft('王者荣耀平均帧率最重要，不玩和平精英')
        self.assertEqual(result['draft']['weights']['honor_of_kings_avg_fps'], 9)
        self.assertEqual(result['draft']['game_scope'], 'honor_of_kings')
        self.assertEqual(result['extraction']['game_use']['names'], ['王者荣耀'])
        self.assertEqual(next(p['evidence'] for p in result['extraction']['preferences']
                              if p['criterion']=='game'), '王者荣耀平均帧率最重要')

    def test_rejecting_hok_preserves_other_positive_game_use(self):
        result = self.draft('爱打和平精英，完全不玩王者荣耀')
        self.assertEqual(result['draft']['weights']['honor_of_kings_avg_fps'], 6)
        self.assertEqual(result['draft']['game_scope'], 'unsupported')
        self.assertEqual(result['extraction']['game_use']['names'], ['和平精英'])
        self.assertTrue(result['extraction']['game_use']['needs_scope_confirmation'])

    def test_mixed_dimension_negation_only_changes_its_named_dimension(self):
        for text, photo, battery in (
                ('拍照不重要但电池容量最重要', 0, 9),
                ('电池容量不重要但拍照最重要', 9, 0),
                ('拍照不重要电池容量最重要', 0, 9),
                ('电池容量最重要拍照不重要', 0, 9),
                ('拍照和电池容量最重要', 9, 9)):
            with self.subTest(text=text):
                result = self.draft(text)
                self.assertEqual(result['draft']['weights']['dxomark_photo'], photo)
                self.assertEqual(result['draft']['weights']['battery_capacity_mah'], battery)

    def test_postposed_hok_rejection_cannot_authorize_generic_game_scope(self):
        payload = legacy_payload()
        payload['judgements'].append(dict(criterion='game', choice='非常重要',
                                          evidence='游戏性能最重要', verdict='extracted'))
        for rejection in ('王者荣耀不玩', '王者荣耀我不玩', '王者荣耀完全不玩'):
            with self.subTest(rejection=rejection):
                result = self.draft('游戏性能最重要，' + rejection, FixedClassifier(payload))
                self.assertEqual(result['draft']['weights']['honor_of_kings_avg_fps'], 9)
                self.assertEqual(result['extraction']['game_use']['names'], [])
                self.assertEqual(result['draft']['game_scope'], 'unconfirmed')
                self.assertTrue(result['extraction']['game_use']['needs_scope_confirmation'])

    def test_rejecting_the_only_previously_played_game_does_not_resurrect_its_use(self):
        result = self.draft('爱打和平精英，不玩和平精英')
        self.assertEqual(result['draft']['weights']['honor_of_kings_avg_fps'], 0)
        self.assertEqual(result['extraction']['game_use']['names'], [])

    def test_five_dimension_provider_remains_compatible_without_false_missing_alert(self):
        result = self.extract('预算2000元左右', FixedClassifier(legacy_payload()))
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(len(result['preferences']), 7)
        self.assertEqual(validate_result(result), [])
        self.assertFalse(any(c['field'].startswith('preferences.') for c in result['clarifications']))
        self.assertEqual(result['budget']['min'], None)
        self.assertEqual(result['budget']['max'], None)

    def test_legacy_parser_keeps_five_dimension_engine_contract(self):
        needs = parse_needs('拍照最重要', classifier=MockClassifier())
        self.assertEqual({p.criterion for p in needs.preferences}, set(BASE))

    def test_legacy_adapter_keeps_five_keys_and_full_performance_record(self):
        from needs_adapter import to_blue_needs
        extraction = self.extract('拍照最重要，续航重要')
        legacy = to_blue_needs(extraction)
        self.assertEqual(set(legacy['preferences']), set(BASE))
        self.assertEqual(len(legacy['extraction_record']['preferences']), 7)
        self.assertEqual(next(p['level'] for p in legacy['extraction_record']['preferences']
                              if p['criterion']=='photo'), '非常重要')

    def test_failed_extraction_still_has_eight_safe_preferences(self):
        result = self.extract('')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(result['preferences']), 7)
        self.assertEqual(validate_result(result), [])

    def test_scope_guard_prevents_positive_other_game_in_recommend_and_export(self):
        from api.contracts import ApiError, parse_recommendation, parse_export
        body = dict(needs={'budget': {'min': None, 'max': None}, 'min_storage': None,
                           'brands': {'include': [], 'exclude': []}},
                    weights={'price_cny': 1, 'honor_of_kings_avg_fps': 2},
                    target_screen=None, mode='new', confirmed=True,
                    expected_release={'dataset_version': 'v1', 'feature_rule_version': 'v1',
                                      'snapshot_sha256': 'a'*64, 'manifest_sha256': 'b'*64})
        self.assertEqual(getattr(parse_recommendation(body, []), 'game_scope', None), 'honor_of_kings')
        for scope, code in (('unconfirmed', 'game_scope_unconfirmed'), ('unsupported', 'unsupported_game')):
            scoped = {**body, 'game_scope': scope}
            with self.subTest(scope=scope), self.assertRaises(ApiError) as caught:
                parse_recommendation(scoped, [])
            self.assertEqual(caught.exception.code, code)
            with self.assertRaises(ApiError) as caught:
                parse_export({'request': scoped, 'fingerprints': {
                    'decision_sha256': 'c'*64, 'ranking_sha256': 'd'*64}}, [])
            self.assertEqual(caught.exception.code, code)
            scoped['weights'] = {'price_cny': 1, 'honor_of_kings_avg_fps': 0}
            self.assertEqual(parse_recommendation(scoped, []).game_scope, scope)
        for scope in ('anything', None, True):
            with self.assertRaises(ApiError):
                parse_recommendation({**body, 'game_scope': scope}, [])

    def test_use_only_provider_claim_cannot_masquerade_as_explicit_highest_grade(self):
        payload = legacy_payload()
        payload['judgements'].append(dict(criterion='game', choice='非常重要',
                                          evidence='爱打和平精英', verdict='extracted'))
        result = self.draft('爱打和平精英', FixedClassifier(payload))
        game = next(p for p in result['extraction']['preferences'] if p['criterion']=='game')
        self.assertEqual(game['verdict'], 'inferred')
        self.assertEqual(game['level'], '重要')
        self.assertEqual(result['draft']['game_scope'], 'unsupported')

    def test_service_rejects_direct_dataclass_scope_bypass(self):
        from dataclasses import replace
        from api.contracts import ApiError, parse_recommendation
        from api.service import CatalogService
        from catalog.release import ReleaseStore
        from tests.api_fixture import make_release, request_payload
        with tempfile.TemporaryDirectory() as root:
            store = ReleaseStore(make_release(Path(root)))
            request = parse_recommendation(request_payload(store.get()), [])
            service = CatalogService(store)
            for scope, code in (('unsupported', 'unsupported_game'), ('unconfirmed', 'game_scope_unconfirmed')):
                with self.subTest(scope=scope):
                    with self.assertRaises(ApiError) as caught:
                        service.recommend(replace(request, weights={'honor_of_kings_avg_fps': 2}, game_scope=scope))
                    self.assertEqual(caught.exception.code, code)

    def test_scope_is_bound_when_inactive_but_legacy_default_keeps_fingerprint(self):
        from api.contracts import parse_recommendation
        from api.service import CatalogService
        from catalog.release import ReleaseStore
        from tests.api_fixture import make_release, request_payload
        with tempfile.TemporaryDirectory() as root:
            store = ReleaseStore(make_release(Path(root)))
            service = CatalogService(store)
            body = request_payload(store.get())
            old = service.recommend(parse_recommendation(body, []))
            explicit = service.recommend(parse_recommendation({**body, 'game_scope': 'honor_of_kings'}, []))
            other = service.recommend(parse_recommendation({**body, 'game_scope': 'unsupported'}, []))
            self.assertEqual(old.result.decision_sha256, explicit.result.decision_sha256)
            self.assertNotEqual(old.result.decision_sha256, other.result.decision_sha256)
            self.assertEqual(other.result.decision_context['selection_rules']['game_scope'], 'unsupported')

    def test_http_draft_cannot_skip_game_scope_or_battery_capability_guard(self):
        from api.app import create_app
        from starlette.testclient import TestClient
        from tests.api_fixture import make_release
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            needs = NeedsService(enabled=True, key='OFFLINE', classifier=MockClassifier(),
                                 quota_path=folder/'quota.sqlite')
            with TestClient(create_app(make_release(folder/'release'), needs_service=needs)) as client:
                release = client.get('/api/v1/meta').json()['release']
                for text, code in (('爱打和平精英', 'unsupported_game'),
                                   ('游戏性能最重要', 'game_scope_unconfirmed')):
                    value = client.post('/api/v1/needs', json={'text': text}).json()
                    draft = value['draft']
                    body = {k: draft[k] for k in ('needs', 'weights', 'target_screen', 'game_scope')}
                    body.update(confirmed=True, expected_release=release, mode='new')
                    response = client.post('/api/v1/recommend', json=body)
                    self.assertEqual(response.status_code, 422)
                    self.assertEqual(response.json()['error']['code'], code)


if __name__ == '__main__':
    unittest.main()
