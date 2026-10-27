"""Public extraction boundaries: no live network, no production database writes."""
import importlib.util
from dataclasses import replace
import tempfile
from pathlib import Path
import unittest

from starlette.testclient import TestClient
from providers.jev import normalise_response
from tests.api_fixture import make_release


class FixtureClassifier:
    def classify(self, text):
        payload = {
            'judgements': [dict(criterion=k, choice='一般' if k == 'weight' else '未提及',
                                evidence='轻一点' if k == 'weight' else '', verdict='extracted')
                           for k in ('weight', 'storage', 'screen', 'photo',
                                     'game', 'battery', 'chipset')],
            'hard_constraints': {'budget_reference': 2000 if '左右' in text else 3000,
                                 'budget_min': None, 'budget_max': None if '左右' in text else 3000,
                                 'min_storage': 256, 'brands': [], 'target_screen': None},
            'evidence': {'budget': '2000元左右' if '左右' in text else '3000元以内'},
            'excluded_brands': ['苹果'] if '不要苹果' in text else [], 'unsupported_needs': [], 'asks': [],
        }
        return normalise_response(payload, provider='fixture', model_version='offline', source_text=text)


class BrokenClassifier:
    def classify(self, text):
        raise RuntimeError('SECRET_KEY_DO_NOT_DISPLAY')


class InventedCapClassifier(FixtureClassifier):
    def classify(self, text):
        result=super().classify(text)
        return replace(result,hard_hints={**result.hard_hints,'budget_min':1800,'budget_max':2200})


class FixedCapClassifier(FixtureClassifier):
    def __init__(self,value):self.value=value
    def classify(self,text):
        result=super().classify(text)
        return replace(result,hard_hints={**result.hard_hints,'budget_min':None,'budget_max':self.value})


class NeedsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def service(self, **kwargs):
        self.assertIsNotNone(importlib.util.find_spec('api.needs'), 'public needs service is missing')
        from api.needs import NeedsService
        options = dict(enabled=True, key='OFFLINE_PRIVATE_KEY_SENTINEL', classifier=FixtureClassifier(),
                       quota_path=self.root/'quota.sqlite', daily_limit=20)
        options.update(kwargs)
        return NeedsService(**options)

    def test_vague_budget_is_inferred_and_draft_stays_unconfirmed(self):
        """含糊预算：推断区间填进草稿，但必须标成推断值且仍需用户确认。

        2026-10-25 修订：旧口径是"预算边界一律留空 + 追问上限"。改为推断，
        但红线不变——推断值必须显式告知（`budget_inferred` + notice 文案）。
        """
        value = self.service().extract('2000元左右，轻一点，至少256G，不要苹果', ['Apple'])
        self.assertEqual(value['extraction']['budget']['reference'], 2000)
        budget = value['draft']['needs']['budget']
        self.assertLess(budget['min'], 2000)
        self.assertGreater(budget['max'], 2000)
        self.assertTrue(value['draft']['budget_inferred'])
        self.assertFalse(value['draft']['confirmed'])
        self.assertIn('推断', value['notice'])
        self.assertNotIn('budget.max',
                         [c['field'] for c in value['extraction']['clarifications']])
        # 拍照「未提及」保留初值1，参评是否启用由后端按默认参考策略决定。
        self.assertEqual(value['draft']['weights']['dxomark_photo'], 1)
        # 屏幕是目标型指标：没有目标尺寸时必须关闭，否则算不出偏差。
        self.assertEqual(value['draft']['weights']['screen_size_inch'], 1)

    def test_storage_threshold_and_excluded_brand_do_not_become_preferences(self):
        value = self.service().extract('3000元以内，轻一点，至少256G，不要苹果', ['Apple'])
        self.assertEqual(value['draft']['needs']['min_storage'], 256)
        self.assertEqual(value['draft']['needs']['brands'], {'include':[], 'exclude':['Apple']})
        # 存储未提及 → WEIGHT_TABLE 的默认值 3（不再是统一的 1）
        self.assertEqual(value['draft']['weights']['storage_gb'], 3)
        self.assertEqual(next(p['level'] for p in value['extraction']['preferences']
                              if p['criterion']=='storage'), '未提及')

    def test_model_cannot_invent_different_boundaries_for_vague_budget(self):
        """模型编的 1800–2200 必须被丢掉，草稿里的区间只能是程序按锚点推断的。"""
        value=self.service(classifier=InventedCapClassifier()).extract('2000元左右，轻一点', ['Apple'])
        budget=value['draft']['needs']['budget']
        self.assertNotEqual(budget,{'min':1800,'max':2200})
        # 2000 左右的推断区间：下浮 15%、上浮 25%
        self.assertEqual(budget,{'min':1700,'max':2500})
        self.assertTrue(value['draft']['budget_inferred'])

    def test_explicit_cap_after_vague_budget_is_preserved(self):
        value=self.service(classifier=InventedCapClassifier()).extract('2000元左右，最多2200元，轻一点', ['Apple'])
        self.assertEqual(value['draft']['needs']['budget'],{'min':None,'max':2200})
        self.assertFalse(value['draft']['budget_inferred'])

    def test_source_guard_rejects_partial_chinese_numbers_and_vague_suffix(self):
        """原文没有依据的边界必须被清掉——推断区间不是模型那个数。"""
        for text,value in [('1500元左右，最多二千五百元，轻一点',2000),
                           ('2000元左右，最多2200元左右，轻一点',2200)]:
            result=self.service(classifier=FixedCapClassifier(value)).extract(text,['Apple'])
            self.assertNotEqual(result['draft']['needs']['budget']['max'],value)
            if '二千五百' in text:
                # Independent rules now recover the complete explicit Chinese amount.
                self.assertEqual(result['draft']['needs']['budget']['max'],2500)
                self.assertFalse(result['draft']['budget_inferred'])
            else:
                # Two contradictory vague anchors require review, not a guessed range.
                self.assertIsNone(result['draft']['needs']['budget']['max'])
                self.assertFalse(result['draft']['budget_inferred'])
        from needs_extraction import budget_bound_has_source
        self.assertFalse(budget_bound_has_source('2000元左右，至少256 GB',256,'min'))

    def test_failed_provider_returns_manual_fallback_without_secret(self):
        value = self.service(classifier=BrokenClassifier()).extract('3000元以内，轻一点', ['Apple'])
        self.assertEqual(value['status'], 'failed')
        self.assertIsNone(value['draft'])
        self.assertNotIn('SECRET_KEY', str(value))

    def test_daily_limit_survives_service_recreation(self):
        first = self.service(daily_limit=1)
        first.extract('3000元以内，轻一点', ['Apple'])
        from api.contracts import ApiError
        with self.assertRaises(ApiError) as caught:
            self.service(daily_limit=1).extract('3000元以内，轻一点', ['Apple'])
        self.assertEqual(caught.exception.status, 429)

    def test_unknown_brand_requires_manual_confirmation_without_silent_drop(self):
        value = self.service().extract('3000元以内，轻一点，不要苹果', ['小米'])
        self.assertIsNone(value['draft'])
        self.assertEqual(value['status'], 'degraded')
        self.assertIn('品牌', value['notice'])

    def test_disabled_service_does_not_call_provider(self):
        from api.contracts import ApiError
        with self.assertRaises(ApiError) as caught:
            self.service(enabled=False, classifier=BrokenClassifier()).extract('轻一点', ['Apple'])
        self.assertEqual(caught.exception.status, 503)

    def test_http_extract_and_invalid_input(self):
        from api.app import create_app
        service = self.service()
        app = create_app(make_release(self.root/'release'), needs_service=service)
        with TestClient(app) as client:
            response = client.post('/api/v1/needs', json={'text':'2000元左右，轻一点'})
            self.assertEqual(response.status_code, 200)
            # 含糊预算被推断成区间（2000 下浮 15%、上浮 25%），不是留空
            self.assertEqual(response.json()['draft']['needs']['budget'], {'min':1700,'max':2500})
            self.assertTrue(response.json()['draft']['budget_inferred'])
            for body in ({'text':''}, {'text':True}, {'text':'x'*2001},
                         {'text':'轻一点','api_key':'SECRET'}, {'text':'轻一点','weights':{'price':10}}):
                self.assertEqual(client.post('/api/v1/needs', json=body).status_code, 422)
            self.assertEqual(client.post('/api/v1/needs?endpoint=bad', json={'text':'轻一点'}).status_code, 422)
            self.assertNotIn('OFFLINE_PRIVATE_KEY_SENTINEL', response.text)

    def test_meta_advertises_extraction_separately_from_explanation(self):
        from api.app import create_app
        app = create_app(make_release(self.root/'release'), needs_service=self.service(enabled=False))
        with TestClient(app) as client:
            self.assertIs(client.get('/api/v1/meta').json()['features']['needs_extraction'], False)


if __name__ == '__main__':
    unittest.main()
