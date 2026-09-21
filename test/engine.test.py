"""MLBComp safety, math and platform-contract tests."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from mlbcomp import db
from mlbcomp.config import american_to_decimal, am_to_prob, devig_two
from mlbcomp.engine.ledger import ObservedQuote, Prediction, record_prediction, record_wager, settle_wager, verify_chain
from mlbcomp.engine.strategies import build_catalog


class TestMLBCompEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / 'test.db'
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self.old_path
        self.tmp.cleanup()

    def test_odds_math(self):
        self.assertAlmostEqual(american_to_decimal(-110), 1.90909, places=4)
        self.assertAlmostEqual(american_to_decimal(150), 2.5, places=4)
        self.assertAlmostEqual(am_to_prob(-110), 0.5238, places=3)
        home, away = devig_two(am_to_prob(-140), am_to_prob(120))
        self.assertAlmostEqual(home + away, 1.0, places=6)

    def test_catalog_keeps_postseason_rounds_separate(self):
        catalog = build_catalog()
        self.assertGreaterEqual(len(catalog), 50)
        self.assertTrue(all(s.env in {'REG', 'POST', 'WC', 'DS', 'LCS', 'WS'} for s in catalog))
        for round_code in ('WC', 'DS', 'LCS', 'WS'):
            self.assertTrue(any(s.env == round_code for s in catalog))
        self.assertTrue(any('hierarchical' in s.model for s in catalog if s.env == 'POST'))

    def test_missing_quote_cannot_create_wager(self):
        p = Prediction('p', 'strategy_v1', 10, 'REG', None, '2026-09-21T12:00:00Z',
                       '2026-09-21T11:59:00Z', 'HOME', .55, .55, -122, .03, None)
        record_prediction(p)
        quote = ObservedQuote('source', 'https://example.test', '2026-09-21T11:00:00Z',
                              '2026-09-21T11:00:00Z', -110, 'ML', 'HOME', verification_status='NOT_VERIFIED')
        with self.assertRaises(ValueError):
            record_wager(p, quote, 10000, stake=100)
        with db.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM immutable_ledger').fetchone()[0], 0)

    def test_ledger_is_hash_chained_and_settlement_is_append_only(self):
        p = Prediction('p', 'strategy_v1', 10, 'REG', None, '2026-09-21T12:00:00Z',
                       '2026-09-21T11:59:00Z', 'HOME', .55, .55, -122, .03, None)
        q = ObservedQuote('source', 'https://example.test', '2026-09-21T11:00:00Z',
                          '2026-09-21T11:00:00Z', -110, 'ML', 'HOME')
        record_prediction(p)
        bet_id = record_wager(p, q, 10000, stake=100)
        settle_wager(bet_id, 'W', 'source')
        chain = verify_chain()
        self.assertTrue(chain['valid'])
        self.assertEqual(chain['rows'], 2)
        with db.connect() as conn:
            with self.assertRaises(Exception):
                conn.execute('DELETE FROM immutable_ledger')


if __name__ == '__main__':
    unittest.main()
