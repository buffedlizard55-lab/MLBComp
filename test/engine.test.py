"""MLBComp safety, math and platform-contract tests."""
from __future__ import annotations

from dataclasses import replace
import json
import os
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from mlbcomp import db
from mlbcomp.config import american_to_decimal, am_to_prob, devig_two, prob_to_am
from mlbcomp.engine.ledger import ObservedQuote, Prediction, record_prediction, record_wager, settle_wager, verify_chain, kelly_stake
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

    def test_fair_odds_and_probability_round_trip(self):
        for probability, expected in ((.2, 400), (.4, 150), (.5, 100), (.6, -150), (.8, -400)):
            with self.subTest(probability=probability):
                self.assertEqual(prob_to_am(probability), expected)
                self.assertAlmostEqual(am_to_prob(expected), probability)
        for probability in (.01, .13, .33, .55, .73, .99):
            self.assertAlmostEqual(am_to_prob(prob_to_am(probability)), probability, places=3)
        for invalid in (0, 1, -.1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                prob_to_am(invalid)

    def test_kelly_rejects_invalid_inputs_and_caps_risk(self):
        self.assertEqual(kelly_stake(10000, .9, 150), 500)
        self.assertEqual(kelly_stake(10000, .2, -110), 0)
        valid = dict(bankroll=10000, probability=.55, american_odds=-110)
        for key in ('bankroll', 'probability', 'american_odds', 'fraction', 'cap'):
            for value in (float('nan'), float('inf'), None, 'invalid'):
                with self.subTest(key=key, value=value):
                    self.assertEqual(kelly_stake(**{**valid, key: value}), 0)
        for key, value in (('fraction', -1), ('cap', -1), ('fraction', 2), ('cap', 2), ('american_odds', 0)):
            self.assertEqual(kelly_stake(**{**valid, key: value}), 0)

    def valid_inputs(self):
        return (
            Prediction('p', 'strategy_v1', 10, 'REG', None, '2026-09-21T12:00:00Z',
                       '2026-09-21T11:59:00Z', 'HOME', .55, .55, -122, .03, None),
            ObservedQuote('source', 'https://example.test', '2026-09-21T11:00:00Z',
                          '2026-09-21T11:01:00Z', -110, 'ML', 'HOME'),
        )

    def test_wager_gate_rejects_invalid_inputs_without_writes(self):
        p, q = self.valid_inputs()
        cases = []
        for field, values in {
            'source_id': ['', ' '], 'source_url': [None, ' '],
            'selection': ['AWAY', ''], 'market_type': ['UNKNOWN'],
            'price_american': [0, float('nan'), float('inf')],
            'observed_at': ['invalid', '2026-09-21T11:00:00', '2026-09-21T12:01:00Z'],
            'available_at': [None, '2026-09-21T10:00:00Z', '2026-09-21T12:01:00Z'],
        }.items():
            for value in values:
                cases.append((p, replace(q, **{field: value}), 10000, 100))
        for field, values in {
            'model_probability': [0, 1, float('nan'), float('inf')],
            'decision_time': ['2026-09-21', 'invalid'],
            'data_cutoff_time': ['2026-09-21T12:01:00Z', '2026-09-21T11:59:00'],
            'availability_status': ['NOT_VERIFIED'],
        }.items():
            for value in values:
                cases.append((replace(p, **{field: value}), q, 10000, 100))
        for bankroll in (0, -100, float('nan'), float('inf')):
            cases.append((p, q, bankroll, 100))
        for stake in (0, -1, 501, float('nan'), float('inf')):
            cases.append((p, q, 10000, stake))
        for prediction, quote, bankroll, stake in cases:
            with self.subTest(prediction=prediction, quote=quote, bankroll=bankroll, stake=stake):
                with self.assertRaises(ValueError):
                    record_wager(prediction, quote, bankroll, stake=stake)
        with db.connect() as conn:
            for table in ('bets', 'immutable_ledger', 'executions', 'positions'):
                self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)

    def test_prediction_rejects_future_cutoff(self):
        p, _ = self.valid_inputs()
        with self.assertRaises(ValueError):
            record_prediction(replace(p, data_cutoff_time='2026-09-21T12:01:00Z'))

    def test_offset_timestamps_and_execution_time(self):
        p, q = self.valid_inputs()
        # 13:01 at UTC+02 is 11:01 UTC, before the noon decision.
        q = replace(q, available_at='2026-09-21T13:01:00+02:00')
        bet = record_wager(p, q, 10000, stake=100)
        with db.connect() as conn:
            execution = conn.execute('SELECT executed_at FROM executions WHERE bet_id=?', (bet,)).fetchone()
            self.assertEqual(execution[0], p.decision_time)
        self.assertTrue(verify_chain()['valid'])

    def test_settlement_outcomes_and_duplicate_rejection(self):
        p, q = self.valid_inputs()
        for outcome, pnl in (('W', 100), ('L', -100), ('P', 0), ('V', 0)):
            bet = record_wager(p, replace(q, price_american=100), 10000, stake=100)
            settle_wager(bet, outcome, 'source')
            with self.assertRaises(ValueError):
                settle_wager(bet, outcome, 'source')
            with db.connect() as conn:
                row = conn.execute('SELECT state, realized_pnl FROM positions WHERE bet_id=?', (bet,)).fetchone()
                self.assertEqual(tuple(row), ('CLOSED', pnl))
        self.assertEqual(verify_chain(), {'rows': 8, 'valid': True, 'invalid_ledger_ids': []})

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
