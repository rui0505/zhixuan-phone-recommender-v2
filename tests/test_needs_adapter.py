import copy
import unittest
import pandas as pd
from needs_extraction import extract_needs
from needs_adapter import to_blue_needs
from providers.jev import MockClassifier
from preferences import filter_candidates


class AdapterTests(unittest.TestCase):
    def result(self, text):
        return extract_needs(text, classifier=MockClassifier())

    def test_grades_and_original_record(self):
        result = self.result('预算3000以内，最轻的手机，价格无所谓')
        original = copy.deepcopy(result)
        blue = to_blue_needs(result)
        self.assertEqual(blue['preferences']['weight'], 'very_important')
        self.assertEqual(blue['preferences']['price'], 'unimportant')
        self.assertEqual(blue['extraction_record'], result)
        blue['extraction_record']['budget']['max'] = 9999
        self.assertEqual(result, original)

    def test_vague_reference_not_hard_constraint(self):
        blue = to_blue_needs(self.result('想买2000元左右的手机'))
        self.assertEqual(blue['budget_reference'], 2000)
        self.assertIsNone(blue['hard_constraints']['max_price'])
        self.assertIsNone(blue['hard_constraints']['min_price'])

    def test_exact_boundaries_and_exclusions_filter(self):
        blue = to_blue_needs(self.result('预算2000到3000，不要华为，存储256GB起步'))
        frame = pd.DataFrame({
            'price': [1999,2000,3000,3001,2500,2500],
            'storage': [256,256,256,256,256,128],
            'brand': ['荣耀','荣耀','苹果','苹果','华为','荣耀'],
        })
        self.assertEqual(filter_candidates(frame, blue).index.tolist(), [1,2])

    def test_degraded_is_kept_for_confirmation(self):
        result = self.result('预算3000以内，轻一点')
        result['status'] = 'degraded'
        result['meta']['error'] = '需要用户检查'
        blue = to_blue_needs(result)
        self.assertEqual(blue['extraction_status'], 'degraded')

    def test_failed_and_malformed_are_rejected(self):
        for result in (extract_needs(''), {}):
            with self.subTest(result=result), self.assertRaises(ValueError):
                to_blue_needs(result)

    def test_all_clarifications_retained_independently_of_tradeoff_limit(self):
        result = self.result('想买手机')
        result['clarifications'] = [
            {'field': field, 'question': '请检查'+field, 'reason': '未提及'}
            for field in ('budget.max','min_storage','target_screen')]
        self.assertEqual(len(to_blue_needs(result)['clarification_questions']), 3)


if __name__ == '__main__':
    unittest.main()
