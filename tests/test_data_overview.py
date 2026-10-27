import unittest
from starlette.testclient import TestClient
from api.app import create_app


class DataOverviewTests(unittest.TestCase):
    def test_source_composition_uses_actual_record_kinds_and_counts_every_source_once(self):
        with TestClient(create_app()) as client:
            data=client.get('/api/v1/meta').json()
        rows=data['source_summary']
        self.assertEqual(sum(row['count'] for row in rows),data['counts']['sources'])
        self.assertTrue(all(isinstance(row['count'],int) and row['count']>0 for row in rows))
        self.assertIn({'label':'影像资料','count':75},rows)
        self.assertNotIn('官方',str(rows))
        self.assertNotIn('用户反馈',str(rows))
