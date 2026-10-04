import importlib.util
import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
import build_dataset


class ReportStatusTests(unittest.TestCase):
    def report(self, checks):
        self.assertTrue(hasattr(build_dataset,'apply_report_status'),'status summarizer missing')
        report={'checks':checks, 'wps_review':'NOT_CHECKED'}
        build_dataset.apply_report_status(report)
        return report

    def test_failed_check_cannot_claim_database_completed(self):
        report=self.report([{'item':'关联有效','result':'FAIL'}])
        self.assertEqual(report['delivery_status']['数据库完成'],'FAIL 数据库机器验收失败')
        self.assertEqual(report['overall_acceptance'],'FAIL')

    def test_unreviewed_sample_not_passed(self):
        report=self.report([{'item':'关联有效','result':'PASS'}])
        self.assertEqual(report['manual_checks'][0]['result'],'NOT_CHECKED')
        self.assertEqual(report['manual_checks'][0]['completed'],0)
        self.assertNotEqual(report['overall_acceptance'],'PASS')

    def test_wps_not_checked_is_not_overall_pass(self):
        report=self.report([{'item':'关联有效','result':'PASS'}])
        self.assertEqual(report['machine_acceptance'],'PASS')
        self.assertEqual(report['overall_acceptance'],'PENDING')
        self.assertIn('NOT_CHECKED',report['delivery_status']['WPS 已复核或待复核'])
