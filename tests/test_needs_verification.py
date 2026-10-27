"""Inject wrong provider replies; exercise the real normaliser, verifier and API draft."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from starlette.testclient import TestClient
from api.needs import NeedsService
from providers.jev import normalise_response
from tests.api_fixture import make_release


def payload(*, hard=None, grades=None, exclude=()):
    grades = grades or {}
    return {
        'judgements': [dict(criterion=key, choice=grades.get(key, ('未提及', ''))[0],
                            evidence=grades.get(key, ('未提及', ''))[1], verdict='extracted')
                       for key in ('weight', 'storage', 'screen', 'photo', 'game', 'battery', 'chipset')],
        'hard_constraints': {'budget_min': None, 'budget_max': None, 'budget_reference': None,
                             'min_storage': None, 'target_screen': None, 'brands': [], **(hard or {})},
        'evidence': {'budget': '', 'min_storage': '', 'target_screen': '', 'brands': ''},
        'excluded_brands': list(exclude), 'asks': [], 'unsupported_needs': [],
    }


class ReplyClassifier:
    def __init__(self, reply): self.reply = reply
    def classify(self, text):
        return normalise_response(deepcopy(self.reply), provider='injected-test',
                                  model_version='offline-wrong-reply', source_text=text)


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def service(self, reply):
        return NeedsService(enabled=True, key='OFFLINE', classifier=ReplyClassifier(reply),
                            quota_path=self.root/'quota.sqlite')

    def extract(self, text, reply):
        result = self.service(reply).extract(text, ['Apple', '小米', '华为'])
        self.assertIsNotNone(result['draft'], result)
        return result

    def issue(self, result, field):
        issues = result['extraction'].get('verification', {}).get('issues', [])
        self.assertTrue(any(item['field'] == field for item in issues), issues)
        self.assertFalse(result['draft']['confirmed'])
        self.assertEqual(result['status'], 'degraded')
        return next(item for item in issues if item['field'] == field)

    def test_wrong_budget_is_corrected_with_original_value_for_review(self):
        result = self.extract('最多3000元，轻一点', payload(hard={'budget_max': 5000}))
        self.assertEqual(result['draft']['needs']['budget']['max'], 3000)
        issue = self.issue(result, 'budget.max')
        self.assertEqual(issue['model_value'], 5000)
        self.assertEqual(issue['rule_value'], 3000)
        self.assertIn('3000', issue['evidence'])

    def test_missing_explicit_budget_is_recovered(self):
        result = self.extract('预算不超过3200元', payload())
        self.assertEqual(result['draft']['needs']['budget']['max'], 3200)
        self.issue(result, 'budget.max')

    def test_storage_number_cannot_be_used_as_budget(self):
        result = self.extract('至少256GB，屏幕6.2英寸', payload(hard={'budget_max': 256}))
        self.assertIsNone(result['draft']['needs']['budget']['max'])
        self.assertIsNone(result['extraction']['budget']['reference'])
        self.issue(result, 'budget.max')

    def test_vague_budget_keeps_only_program_inference(self):
        result = self.extract('预算2000元左右', payload(hard={'budget_reference': 2000, 'budget_max': 2200}))
        self.assertIsNone(result['extraction']['budget']['max'])
        self.assertEqual(result['draft']['needs']['budget'], {'min': 1700, 'max': 2500})
        self.assertTrue(result['draft']['budget_inferred'])
        self.issue(result, 'budget.max')

    def test_currency_units_and_chinese_numerals_are_normalised(self):
        for text, want in [('预算最多0.5万元', 5000), ('不超过三千五百元', 3500)]:
            with self.subTest(text=text):
                result = self.extract(text, payload(hard={'budget_max': 500}))
                self.assertEqual(result['draft']['needs']['budget']['max'], want)

    def test_conflicting_same_turn_budget_is_not_silently_resolved(self):
        result = self.extract('预算最多3000元，预算上限4000元', payload(hard={'budget_max': 4000}))
        self.assertIsNone(result['draft']['needs']['budget']['max'])
        self.issue(result, 'budget.max')

    def test_latest_turn_supersedes_old_budget(self):
        result = self.extract('1. 最多3000元\n2. 改成最多3500元', payload(hard={'budget_max': 3000}))
        self.assertEqual(result['draft']['needs']['budget']['max'], 3500)

    def test_excluded_brand_cannot_become_whitelist(self):
        result = self.extract('不要苹果，只看小米', payload(hard={'brands': ['苹果']}))
        self.assertEqual(result['draft']['needs']['brands'], {'include': ['小米'], 'exclude': ['Apple']})
        self.issue(result, 'brands.include')
        self.issue(result, 'brands.exclude')

    def test_invented_brand_is_removed(self):
        result = self.extract('拍照重要', payload(hard={'brands': ['华为']}))
        self.assertEqual(result['draft']['needs']['brands']['include'], [])
        self.issue(result, 'brands.include')

    def test_brand_acceptance_revision_cancels_exclusion_without_whitelisting(self):
        result = self.extract('1. 不要苹果\n2. 苹果也可以', payload(exclude=['苹果']))
        self.assertEqual(result['draft']['needs']['brands'], {'include': [], 'exclude': []})

    def test_threshold_is_corrected_but_not_promoted_to_preference(self):
        result = self.extract('至少256GB', payload(hard={'min_storage': 512},
                              grades={'storage': ('非常重要', '至少256GB')}))
        self.assertEqual(result['draft']['needs']['min_storage'], 256)
        self.assertEqual(result['draft']['weights']['storage_gb'], 3)
        self.issue(result, 'min_storage')
        self.issue(result, 'preferences.storage')

    def test_ram_is_not_a_storage_threshold(self):
        result = self.extract('运行内存至少12GB', payload(hard={'min_storage': 12}))
        self.assertIsNone(result['draft']['needs']['min_storage'])
        self.issue(result, 'min_storage')

    def test_explicit_screen_size_is_checked(self):
        result = self.extract('屏幕6.2英寸', payload(hard={'target_screen': 6.8}))
        self.assertEqual(result['draft']['target_screen'], 6.2)
        self.issue(result, 'target_screen')

    def test_negation_stays_local_to_each_dimension(self):
        result = self.extract('拍照不重要，游戏最重要', payload(grades={
            'photo': ('非常重要', '拍照不重要'), 'game': ('不在意', '游戏最重要')}))
        self.assertEqual(result['draft']['weights']['dxomark_photo'], 0)
        self.assertEqual(result['draft']['weights']['honor_of_kings_avg_fps'], 9)
        self.issue(result, 'preferences.photo')
        self.issue(result, 'preferences.game')

    def test_seven_dimensions_are_all_checked(self):
        examples = [('weight', '重量不重要', 'weight_g'), ('storage', '存储不重要', 'storage_gb'),
                    ('screen', '屏幕尺寸不重要', 'screen_size_inch'), ('photo', '拍照不重要', 'dxomark_photo'),
                    ('game', '游戏不重要', 'honor_of_kings_avg_fps'),
                    ('battery', '电池容量不重要', 'battery_capacity_mah'),
                    ('chipset', '处理器不重要', 'chipset_tier')]
        for criterion, text, metric in examples:
            with self.subTest(criterion=criterion):
                result = self.extract(text, payload(grades={criterion: ('重要', text)}))
                self.assertEqual(result['draft']['weights'][metric], 0)
                self.issue(result, f'preferences.{criterion}')

    def test_unmentioned_is_not_indifferent_and_fabricated_evidence_is_removed(self):
        result = self.extract('预算3000元', payload(grades={'weight': ('不在意', '重量无所谓')}))
        self.assertEqual(result['draft']['weights']['weight_g'], 2)
        self.issue(result, 'preferences.weight')
        pref = next(p for p in result['extraction']['preferences'] if p['criterion'] == 'weight')
        self.assertEqual(pref['level'], '未提及')

    def test_unsupported_but_grounded_wording_is_flagged_not_rejected(self):
        result = self.extract('拍照效果得过得去', payload(grades={'photo': ('一般', '拍照效果得过得去')}))
        self.assertEqual(result['draft']['weights']['dxomark_photo'], 4)
        self.issue(result, 'preferences.photo')

    def test_rule_miss_does_not_turn_double_negation_into_indifference(self):
        result = self.extract('拍照不是不重要', payload(grades={'photo': ('重要', '拍照不是不重要')}))
        self.assertGreater(result['draft']['weights']['dxomark_photo'], 0)
        self.issue(result, 'preferences.photo')

    def test_later_preference_revision_wins_and_conflicting_same_turn_is_reviewed(self):
        result = self.extract('1. 拍照最重要\n2. 拍照不重要', payload(grades={'photo': ('非常重要', '拍照最重要')}))
        self.assertEqual(result['draft']['weights']['dxomark_photo'], 0)
        conflict = self.extract('拍照最重要，拍照不重要', payload(grades={'photo': ('非常重要', '拍照最重要')}))
        self.assertEqual(next(p['level'] for p in conflict['extraction']['preferences']
                              if p['criterion'] == 'photo'), '未提及')
        self.issue(conflict, 'preferences.photo')

    def test_correct_reply_has_no_false_safety_guarantee(self):
        result = self.extract('最多3000元，重量轻一点', payload(hard={'budget_max': 3000},
                              grades={'weight': ('一般', '重量轻一点')}))
        report = result['extraction'].get('verification')
        self.assertIsNotNone(report)
        self.assertEqual(report['issues'], [])
        self.assertEqual(report['status'], 'no_conflicts_detected')
        self.assertFalse(result['draft']['confirmed'])

    def test_real_http_endpoint_applies_verification(self):
        from api.app import create_app
        app = create_app(make_release(self.root/'release'), needs_service=self.service(payload(
            hard={'budget_max': 9000}, grades={'photo': ('重要', '拍照不重要')})))
        with TestClient(app) as client:
            response = client.post('/api/v1/needs', json={'text': '最多3000元，拍照不重要'})
            self.assertEqual(response.status_code, 200)
            result = response.json()
            self.assertEqual(result['draft']['needs']['budget']['max'], 3000)
            self.assertEqual(result['draft']['weights']['dxomark_photo'], 0)
            self.assertEqual(result['extraction']['verification']['status'], 'review_required')

    def test_explicit_very_important_and_must_are_not_weakened_by_rules(self):
        for text in ('拍照非常重要', '拍照必须好一点'):
            with self.subTest(text=text):
                result = self.extract(text, payload(grades={'photo': ('非常重要', text)}))
                self.assertEqual(result['draft']['weights']['dxomark_photo'], 9)

    def test_same_preference_repeated_keeps_a_literal_evidence_span(self):
        result = self.extract('拍照重要，拍照要好', payload())
        pref = next(p for p in result['extraction']['preferences'] if p['criterion'] == 'photo')
        self.assertEqual(pref['level'], '重要')
        self.assertIn(pref['evidence'], result['extraction']['input_text'])

    def test_postposed_brand_rejection_is_recovered(self):
        result = self.extract('苹果也不要', payload())
        self.assertEqual(result['draft']['needs']['brands']['exclude'], ['Apple'])

    def test_explicit_cap_equal_to_vague_anchor_is_still_valid(self):
        result = self.extract('预算3000元左右，最多3000元', payload(hard={'budget_max': 3000, 'budget_reference': 3000}))
        self.assertEqual(result['draft']['needs']['budget']['max'], 3000)

    def test_corrected_original_values_are_not_mutated_in_caller_payload(self):
        from needs_extraction import extract_needs
        from needs_verification import verify_extraction
        shaped = extract_needs('最多3000元', classifier=ReplyClassifier(payload(hard={'budget_max': 9000})))
        original = deepcopy(shaped)
        checked = verify_extraction('最多3000元', shaped)
        self.assertEqual(shaped, original)
        self.assertEqual(checked['budget']['max'], 3000)

    def test_storage_shorthand_never_creates_a_budget_floor(self):
        result = self.extract('预算3000元，256起步', payload(hard={'budget_reference': 3000, 'min_storage': 256}))
        self.assertIsNone(result['extraction']['budget']['min'])
        self.assertEqual(result['draft']['needs']['min_storage'], 256)
        self.assertTrue(result['draft']['budget_inferred'])

    def test_supported_vague_budget_expressions_keep_their_reference(self):
        for text, anchor in [('2000左右', 2000), ('预算四五千', 4500)]:
            with self.subTest(text=text):
                result = self.extract(text, payload(hard={'budget_reference': anchor}))
                self.assertEqual(result['extraction']['budget']['reference'], anchor)
                self.assertTrue(result['draft']['budget_inferred'])

    def test_adjacent_brand_directives_keep_separate_negation_scope(self):
        result = self.extract('不要苹果只看小米', payload(hard={'brands': ['小米']}, exclude=['苹果']))
        self.assertEqual(result['draft']['needs']['brands'], {'include': ['小米'], 'exclude': ['Apple']})

    def test_game_name_does_not_create_a_brand_filter(self):
        result = self.extract('只要王者荣耀能流畅运行', payload())
        self.assertEqual(result['draft']['needs']['brands'], {'include': [], 'exclude': []})

    def test_qualified_negation_is_reviewed_without_strengthening_preference(self):
        for text in ('拍照没有那么重要', '拍照不需要非常重要'):
            with self.subTest(text=text):
                result = self.extract(text, payload(grades={'photo': ('一般', text)}))
                self.assertEqual(result['draft']['weights']['dxomark_photo'], 4)
                self.issue(result, 'preferences.photo')

    def test_tb_threshold_converts_before_applying_gb_range(self):
        result = self.extract('至少1TB', payload(hard={'min_storage': 1024}))
        self.assertEqual(result['draft']['needs']['min_storage'], 1024)

    def test_rejected_or_range_screen_size_is_not_a_single_target(self):
        for text in ('不要6.7英寸的手机', '屏幕6到6.7英寸'):
            with self.subTest(text=text):
                result = self.extract(text, payload())
                self.assertIsNone(result['extraction']['target_screen'])
                self.issue(result, 'target_screen')

    def test_non_money_vague_numbers_do_not_create_budget_anchors(self):
        for text in ('屏幕6.2左右', '电池四五千mAh'):
            with self.subTest(text=text):
                result = self.extract(text, payload())
                self.assertIsNone(result['extraction']['budget']['reference'])

    def test_negated_only_brand_preference_does_not_create_whitelist(self):
        result = self.extract('我不只看苹果', payload())
        self.assertEqual(result['draft']['needs']['brands']['include'], [])
