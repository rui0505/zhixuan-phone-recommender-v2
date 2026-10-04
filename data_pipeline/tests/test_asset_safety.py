import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from stage_assets import is_safe_member


class AssetSafetyTests(unittest.TestCase):
    def test_asset_windows_paths_rejected_before_extraction(self):
        for name in ('..\\x.png','a\\..\\..\\x.png','C:\\x.png','\\\\host\\share\\x.png','a/../x.png','x.png:evil','a\x00.png'):
            with self.subTest(name=name): self.assertFalse(is_safe_member(name))
        self.assertTrue(is_safe_member('phones/中文/原图.png'))
