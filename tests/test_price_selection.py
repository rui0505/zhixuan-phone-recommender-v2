import tempfile
import unittest
from pathlib import Path
from catalog.repository import CatalogRepository, _select_single_price
from catalog_fixture import create_catalog, insert


class PriceTests(unittest.TestCase):
    def test_unknown_price_never_enters_new_mode(self):
        rows = [dict(sku_id='S1', price_id='P1', price_kind='marketplace_listing_price', condition='unknown', price_cny=100),
                dict(sku_id='S1', price_id='P2', price_kind='official_current_price', condition='new', price_cny=200)]
        self.assertEqual(_select_single_price(rows, 'new')[0]['price_id'], 'P2')

    def test_duplicate_snapshots_select_stably(self):
        rows = [dict(sku_id='S1', price_kind='official_current_price', condition='new', price_cny=100,
                     price_id=i, observed_at=d) for i, d in [('P3','未知'),('P2','2026-01-02'),('P1','2026-01-02')]]
        self.assertEqual(_select_single_price(rows, 'new')[0]['price_id'], 'P1')
        self.assertEqual(_select_single_price(rows[::-1], 'new')[0]['price_id'], 'P1')

    def test_budget_does_not_select_old_cheaper_price(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'c.sqlite'; con=create_catalog(db)
            insert(con, 'price_snapshots', price_id='LATEST', sku_id='S1', amount=4000, currency='CNY',
                   condition='new', price_kind='official_current_price', observed_at='2026-02-01')
            con.commit(); con.close()
            rows=CatalogRepository(str(db)).as_records(CatalogRepository(str(db)).load_catalog(mode='new', max_price=1500))
            self.assertEqual(rows, [])

    def test_declared_priority_precedes_observation_date(self):
        rows = [dict(sku_id='S1', price_id='CURRENT', price_kind='official_current_price', condition='new', observed_at=None),
                dict(sku_id='S1', price_id='LAUNCH', price_kind='official_launch_price', condition='new', observed_at='2026-02-01')]
        self.assertEqual(_select_single_price(rows,'new')[0]['price_id'], 'CURRENT')
        self.assertEqual(_select_single_price(rows,'new', ['official_launch_price','official_current_price'])[0]['price_id'], 'LAUNCH')

    def test_unknown_priority_is_rejected(self):
        with self.assertRaises(ValueError):
            _select_single_price([], 'new', ['cheapest_unknown'])
