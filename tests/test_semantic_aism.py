"""Hand-computed structures and real provider/API boundaries; no live model calls."""
from copy import deepcopy
import importlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from starlette.testclient import TestClient
from api.app import create_app
from api.needs import NeedsService, OfficialNeedsClassifier
from needs_extraction import extract_needs
from providers.jev import normalise_response
from providers.llm import LLMClassifier


def module():
    try: return importlib.import_module('semantic_aism')
    except ModuleNotFoundError: return None


def reply():
    return {'judgements': [dict(criterion=c, choice='非常重要' if c == 'game' else '未提及',
                               evidence='游戏最重要' if c == 'game' else '', verdict='extracted')
                           for c in ('weight', 'storage', 'screen', 'photo', 'game', 'battery', 'chipset')],
            'hard_constraints': {'budget_min': None, 'budget_max': 3000, 'budget_reference': 3000,
                                 'min_storage': None, 'target_screen': None, 'brands': []},
            'excluded_brands': [], 'asks': [], 'unsupported_needs': [],
            'evidence': {'budget': '最多3000元'}}


def envelope():
    return {'choices': [{'message': {'content': json.dumps(reply(), ensure_ascii=False)}}]}


class OfflineClassifier:
    def classify(self, text):
        return normalise_response(reply(), provider='offline', model_version='fixture', source_text=text)


class AismGraphTests(unittest.TestCase):
    def analyse(self, nodes, edges):
        api = module()
        self.assertIsNotNone(api, 'AISM graph analyser is not implemented')
        return api.analyse_structure(nodes, edges)

    def test_chain_closure_and_opposite_layers_are_hand_computed(self):
        value = self.analyse(['a', 'b', 'c'], [('a', 'b'), ('b', 'c')])
        self.assertEqual(value['adjacency_matrix'], [[0, 1, 0], [0, 0, 1], [0, 0, 0]])
        self.assertEqual(value['reachability_matrix'], [[1, 1, 1], [0, 1, 1], [0, 0, 1]])
        self.assertEqual(value['up_layers'], [['c'], ['b'], ['a']])
        self.assertEqual(value['down_layers'], [['a'], ['b'], ['c']])

    def test_disconnected_nodes_are_not_given_fabricated_dependencies(self):
        value = self.analyse(['a', 'b'], [])
        self.assertEqual(value['reachability_matrix'], [[1, 0], [0, 1]])
        self.assertEqual(value['up_layers'], [['a', 'b']])
        self.assertEqual(value['down_layers'], [['a', 'b']])
        self.assertEqual(value['skeleton_edges'], [])

    def test_branching_with_isolated_node_has_different_extraction_layers(self):
        value = self.analyse(['a', 'b', 'c', 'd'], [('a', 'c'), ('b', 'c')])
        self.assertEqual(value['up_layers'], [['c', 'd'], ['a', 'b']])
        self.assertEqual(value['down_layers'], [['a', 'b', 'd'], ['c']])

    def test_cycle_stays_together_and_terminates(self):
        value = self.analyse(['a', 'b', 'c'], [('a', 'b'), ('b', 'a'), ('b', 'c')])
        self.assertEqual(value['strong_components'], [['a', 'b'], ['c']])
        self.assertEqual(value['up_layers'], [['c'], ['a', 'b']])
        self.assertEqual(value['down_layers'], [['a', 'b'], ['c']])
        self.assertEqual(value['skeleton_edges'], [{'source': ['a', 'b'], 'target': ['c']}])

    def test_transitive_edge_is_removed_only_from_component_skeleton(self):
        value = self.analyse(['a', 'b', 'c'], [('a', 'b'), ('b', 'c'), ('a', 'c')])
        self.assertEqual(value['adjacency_matrix'][0][2], 1)
        self.assertEqual(value['skeleton_edges'], [dict(source=['a'], target=['b']), dict(source=['b'], target=['c'])])

    def test_bad_nodes_and_unknown_edge_endpoints_fail_explicitly(self):
        api = module(); self.assertIsNotNone(api)
        for nodes, edges in [([], []), (['a', 'a'], []), (['a'], [('a', 'b')]),
                             (['a', 1], []), (['a']*50, [])]:
            with self.subTest(nodes=nodes), self.assertRaises(ValueError):
                api.analyse_structure(nodes, edges)

    def test_default_model_has_only_documented_score_dependencies(self):
        api = module(); self.assertIsNotNone(api)
        value = api.semantic_structure()
        self.assertEqual([n['id'] for n in value['nodes']], ['weight', 'storage', 'screen', 'photo', 'game', 'battery', 'chipset'])
        self.assertEqual({(e['source'], e['target']) for e in value['relations']}, {('chipset', 'game'), ('battery', 'game')})
        self.assertEqual(value['up_layers'], [['weight', 'storage', 'screen', 'photo', 'game'], ['battery', 'chipset']])
        self.assertEqual(value['down_layers'], [['weight', 'storage', 'screen', 'photo', 'battery', 'chipset'], ['game']])
        self.assertEqual(value['basis_type'], 'project_scoring_dependencies')
        self.assertFalse(value['empirically_validated'])

    def test_model_digest_changes_with_relations_or_definitions(self):
        api = module(); self.assertIsNotNone(api)
        config = json.loads((Path(__file__).resolve().parents[1]/'data/aism-semantic-structure.json').read_text(encoding='utf-8'))
        first = api.build_semantic_structure(config)
        changed = deepcopy(config); changed['relations'] = changed['relations'][:1]
        self.assertNotEqual(api.build_semantic_structure(changed)['model_sha256'], first['model_sha256'])
        changed = deepcopy(config); changed['nodes'][0]['definition'] += '，测试定义修订'
        self.assertNotEqual(api.build_semantic_structure(changed)['model_sha256'], first['model_sha256'])

    def test_public_snapshot_is_not_mutable_by_callers(self):
        api = module(); self.assertIsNotNone(api)
        value = api.semantic_structure(); value['nodes'][0]['label'] = 'changed'
        self.assertEqual(api.semantic_structure()['nodes'][0]['label'], '重量')


class AismIntegrationTests(unittest.TestCase):
    def test_general_adapter_sends_calculated_structure_in_actual_request(self):
        with mock.patch.dict(os.environ, {'TEST_AISM_KEY': 'OFFLINE'}), mock.patch(
                'providers.llm.post_chat_resilient', return_value=(envelope(), None, [])) as transport:
            classifier = LLMClassifier(base_url='https://example.invalid/v1', model='offline', api_key_env='TEST_AISM_KEY')
            classifier.classify('最多3000元，游戏最重要')
        prompt = transport.call_args.args[1]['messages'][0]['content']
        api = module(); self.assertIsNotNone(api)
        self.assertIn(api.semantic_structure()['model_sha256'], prompt)
        self.assertIn('UP', prompt); self.assertIn('DOWN', prompt)
        self.assertIn('处理器', prompt); self.assertIn('不自动', prompt)

    def test_official_adapter_uses_the_same_computed_system_prompt(self):
        with mock.patch('api.needs.post_chat', return_value=(envelope(), None)) as transport:
            OfficialNeedsClassifier('OFFLINE').classify('最多3000元，游戏最重要')
        prompt = transport.call_args.args[1]['messages'][0]['content']
        from providers.llm import SYSTEM_PROMPT, PROMPT_VERSION
        self.assertEqual(prompt, SYSTEM_PROMPT)
        self.assertIn('aism', PROMPT_VERSION.lower())
        self.assertIn(PROMPT_VERSION, prompt)

    def test_game_preference_does_not_propagate_to_chip_or_battery(self):
        with tempfile.TemporaryDirectory() as folder:
            service = NeedsService(enabled=True, key='OFFLINE', classifier=OfflineClassifier(),
                                   quota_path=Path(folder)/'quota.sqlite')
            result = service.extract('最多3000元，游戏最重要', ['Apple'])
        self.assertIsNotNone(result['draft'])
        self.assertEqual(result['draft']['weights']['honor_of_kings_avg_fps'], 9)
        self.assertEqual(result['draft']['weights']['chipset_tier'], 2)
        self.assertEqual(result['draft']['weights']['battery_capacity_mah'], 2)
        self.assertNotIn('verification', result['extraction'])
        self.assertIn('semantic_structure', result['extraction'])
        self.assertFalse(result['draft']['confirmed'])

    def test_http_meta_and_extraction_bind_identical_structure(self):
        with tempfile.TemporaryDirectory() as folder:
            service = NeedsService(enabled=True, key='OFFLINE', classifier=OfflineClassifier(), quota_path=Path(folder)/'quota.sqlite')
            with TestClient(create_app(needs_service=service)) as client:
                meta = client.get('/api/v1/meta').json()
                extraction = client.post('/api/v1/needs', json={'text': '最多3000元，游戏最重要'}).json()['extraction']
        self.assertIn('needs_semantic_structure', meta)
        self.assertEqual(meta['needs_semantic_structure'], extraction['semantic_structure'])

    def test_failed_extraction_keeps_structure_but_not_fabricated_grades(self):
        value = extract_needs('', classifier=OfflineClassifier())
        self.assertEqual(value['status'], 'failed')
        self.assertTrue(all(p['level'] == '未提及' for p in value['preferences']))
        self.assertIn('semantic_structure', value)
