import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from stage_derived import parse_refresh_rate


class RefreshRateTests(unittest.TestCase):
    def test_complex_or_approximate_rates_are_not_exact_single_values(self):
        for text in ('120Hz(内屏)，60Hz(外屏)', '60Hz(外屏)，120Hz(内屏)',
                     '约120Hz', '60–120Hz', '60-120Hz', '1至120Hz', '120Hz(内屏)'):
            with self.subTest(text=text):
                value, note, status = parse_refresh_rate(text)
                self.assertIsNone(value)
                self.assertEqual(note, text)
                self.assertEqual(status, 'unparsed')

    def test_exact_known_rate_annotations_are_preserved(self):
        for text in ('120Hz', '120Hz ProMotion', '120Hz（LTPO）', '90Hz'):
            with self.subTest(text=text):
                value, note, status = parse_refresh_rate(text)
                self.assertEqual(value, 90 if text == '90Hz' else 120)
                self.assertEqual(status, 'ok')
                self.assertEqual(note, text)
