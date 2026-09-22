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

import math

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
        self.assertTrue(any(s.sid == 'MLB_POST_DEDICATED_001' and s.model == 'post_elo' for s in catalog))
        self.assertTrue(any(s.sid == 'MLB_POST_MODEL_C_001' and s.model == 'post_elo' for s in catalog))
        self.assertTrue(any((s.extra or {}).get('alias_of') == 'MLB_POST_XREG_001' for s in catalog))

    def test_dedicated_postseason_elo_uses_post_prior(self):
        from mlbcomp.engine.strategies import model_post_elo, model_xreg
        class Game: pass
        self.assertTrue(math.isnan(model_post_elo(Game(), {}, {})))
        self.assertAlmostEqual(model_post_elo(Game(), {'p_post_elo': 0.61, 'p_elo': 0.4}, {}), 0.61)
        self.assertAlmostEqual(model_xreg(Game(), {'p_elo': 0.4, 'p_post_elo': 0.61}, {}), 0.4)

    def test_chronological_split_does_not_mix_future_timestamps(self):
        from mlbcomp.engine.evaluation import chronological_split
        frame = __import__('pandas').DataFrame([
            {'decision_time': '2024-04-01', 'game_pk': 1, 'y': 0},
            {'decision_time': '2024-04-01', 'game_pk': 2, 'y': 1},
            {'decision_time': '2024-06-01', 'game_pk': 3, 'y': 1},
            {'decision_time': '2024-08-01', 'game_pk': 4, 'y': 0},
            {'decision_time': '2024-09-01', 'game_pk': 5, 'y': 1},
        ])
        split = chronological_split(frame)
        self.assertGreater(len(split.train), 0)
        self.assertGreater(len(split.test), 0)
        self.assertLessEqual(split.train.decision_time.max(), split.validate.decision_time.min() if len(split.validate) else split.test.decision_time.min())
        if len(split.validate):
            self.assertLessEqual(split.validate.decision_time.max(), split.test.decision_time.min())
        # Same-timestamp games stay in one partition.
        self.assertEqual(set(split.train[split.train.decision_time == '2024-04-01'].game_pk), {1, 2})

    def test_export_refuses_to_wipe_source_snapshot(self):
        from mlbcomp.web import export_static
        tmp = Path(self.tmp.name) / 'export'
        tmp.mkdir()
        old_data, old_feat = export_static.DATA, export_static.FEAT
        export_static.DATA = tmp
        export_static.FEAT = tmp / 'features'
        try:
            (tmp / 'summary.json').write_text(json.dumps({'data_mode': 'SOURCE_SNAPSHOT', 'total_strategies': 70}))
            with self.assertRaises(RuntimeError):
                export_static.export()
            self.assertEqual(json.loads((tmp / 'summary.json').read_text())['data_mode'], 'SOURCE_SNAPSHOT')
        finally:
            export_static.DATA, export_static.FEAT = old_data, old_feat

    def test_export_writes_safe_empty_competition_payloads(self):
        from mlbcomp.web import export_static
        tmp = Path(self.tmp.name) / 'export2'
        tmp.mkdir()
        old_data, old_feat = export_static.DATA, export_static.FEAT
        export_static.DATA = tmp
        export_static.FEAT = tmp / 'features'
        try:
            summary = export_static.export(ledger_cap=50, force=True)
            for name in ('competitions.json', 'competitors.json'):
                payload = json.loads((tmp / name).read_text())
                self.assertEqual(payload.get('competitions', payload.get('entrants')), [])
            self.assertEqual(summary['data_mode'], 'NO_SOURCE_SNAPSHOT')
            self.assertEqual(summary['total_competitions'], 0)
            self.assertTrue(summary['competition_seed'])
        finally:
            export_static.DATA, export_static.FEAT = old_data, old_feat

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


    def test_postseason_round_formats(self):
        from mlbcomp.config import round_needed, round_format_label
        self.assertEqual(round_needed('WC', 2019), 1)
        self.assertEqual(round_needed('WC', 2020), 2)
        self.assertEqual(round_needed('WC', 2021), 1)
        self.assertEqual(round_needed('WC', 2022), 2)
        self.assertEqual(round_needed('DS', 2024), 3)
        self.assertEqual(round_needed('LCS', 2024), 4)
        self.assertEqual(round_needed('WS', 2024), 4)
        self.assertEqual(round_format_label('WC', 2019), 'BO1')
        self.assertEqual(round_format_label('WC', 2024), 'BO3')
        self.assertEqual(round_format_label('DS', 2024), 'BO5')
        self.assertEqual(round_format_label('WS', 2024), 'BO7')


class TestCompetitionEngine(unittest.TestCase):
    """Adversarial checks on the simulated-competition layer."""

    def _row(self, **kw):
        base = dict(bet_id=1, game_pk=700001, strategy_id='MLB_REG_ELO_001',
                    env='REG', season=2026, round_code=None, market='ML',
                    selection='HOME', model_prob=0.6, fair_price=0.6,
                    made_at='2026-05-02T18:05:00Z', status='EVAL', result=None,
                    pnl=None, stake=0.0, verification_status='NO_MARKET_PRICE',
                    game_date='2026-05-02', home_abbr='NYY', away_abbr='BOS')
        base.update(kw)
        return base

    def _dataset(self):
        rows = []
        # Ten scored REG days, alternating results for two strategies.
        for i in range(10):
            day = f'2026-05-{i + 1:02d}'
            rows.append(self._row(bet_id=100 + i, game_pk=700000 + i, game_date=day,
                                  selection='HOME', model_prob=0.6,
                                  result='W' if i % 2 == 0 else 'L',
                                  made_at=f'{day}T18:05:00Z'))
            rows.append(self._row(bet_id=200 + i, game_pk=710000 + i, game_date=day,
                                  strategy_id='MLB_REG_FORM_001', selection='AWAY',
                                  model_prob=0.55, result='L' if i % 3 else 'W'))
        # Verified-price row: the only row allowed to carry paper PnL.
        rows.append(self._row(bet_id=300, game_pk=720000, game_date='2026-05-15',
                              model_prob=0.62, result='W', stake=100.0, pnl=61.29,
                              verification_status='VERIFIED_PRICE',
                              market_price=-110, quote_source_url='https://example.test/q',
                              quote_observed_at='2026-05-15T16:00:00Z',
                              quote_available_at='2026-05-15T16:01:00Z',
                              quote_price_american=-110))
        # A PROPOSED row inside a window must never be scored.
        rows.append(self._row(bet_id=301, game_pk=720001, game_date='2026-05-16',
                              result=None, status='PROPOSED'))
        return rows

    def test_score_rows_uses_real_outcomes_only(self):
        from mlbcomp.engine.competition import score_rows
        rows = self._dataset()
        scored = score_rows([r for r in rows if r['strategy_id'] == 'MLB_REG_ELO_001'])
        self.assertEqual(scored['picks'], 11)          # 10 EVAL + 1 verified, PROPOSED excluded
        self.assertEqual(scored['wins'], 6)
        self.assertEqual(scored['losses'], 5)
        self.assertAlmostEqual(scored['accuracy'], 6 / 11)
        # Brier pairs the selected-side probability with the actual result.
        brier = sum(((0.6 - (1 if r['result'] == 'W' else 0)) ** 2 if r['bet_id'] != 300
                     else (0.62 - 1) ** 2) for r in rows
                    if r['strategy_id'] == 'MLB_REG_ELO_001' and r['result']) / 11
        self.assertAlmostEqual(scored['brier'], brier)
        self.assertEqual(scored['verified_price_rows'], 1)
        self.assertEqual(scored['pnl'], 61.29)

    def test_no_pnl_without_verified_rows(self):
        from mlbcomp.engine.competition import score_rows
        rows = [r for r in self._dataset() if r['verification_status'] != 'VERIFIED_PRICE']
        scored = score_rows(rows)
        self.assertIsNone(scored['pnl'])
        self.assertIsNone(scored['roi'])
        self.assertEqual(scored['pnl_status'], 'UNAVAILABLE_NO_VERIFIED_PRICE_ROWS_IN_WINDOW')
        self.assertEqual(scored['verified_price_rows'], 0)

    def test_build_is_deterministic_and_seed_sensitive(self):
        from mlbcomp.engine.competition import build_payloads, COMPETITION_SPECS
        strategies = [{'sid': 'MLB_REG_ELO_001', 'id': 'MLB_REG_ELO_001', 'env': 'REG',
                       'market': 'ML', 'model': 'elo', 'name': 'Elo baseline'}]
        board = [{'id': 'MLB_REG_ELO_001'}]
        rows = self._dataset()
        first = build_payloads(strategies, board, rows, seed='seed-a', generated_at='2026-09-22T00:00:00Z')
        second = build_payloads(strategies, board, rows, seed='seed-a', generated_at='2026-09-22T00:00:00Z')
        self.assertEqual(first, second)
        third = build_payloads(strategies, board, rows, seed='seed-b', generated_at='2026-09-22T00:00:00Z')
        self.assertNotEqual(
            [(c['id'], c['window_start']) for c in third[1]['competitions']],
            [(c['id'], c['window_start']) for c in first[1]['competitions']],
            'a different recorded seed must change at least one drawn window')

    def test_window_timestamps_are_second_precise_and_bounded(self):
        from mlbcomp.engine.competition import build_payloads
        strategies = [{'sid': 'MLB_REG_ELO_001', 'id': 'MLB_REG_ELO_001', 'env': 'REG',
                       'market': 'ML', 'model': 'elo', 'name': 'Elo baseline'}]
        _, payload = build_payloads(strategies, [], self._dataset(),
                                    seed='seed-a', generated_at='2026-09-22T00:00:00Z')
        import re
        stamp = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
        for comp in payload['competitions']:
            if comp.get('status') == 'NOT_DRAWN':
                self.assertTrue(comp['not_drawn_reason'])
                self.assertIsNone(comp['window_start'])
                continue
            self.assertRegex(comp['window_start'], stamp)
            self.assertRegex(comp['window_end'], stamp)
            self.assertTrue(comp['window_start'].endswith('00:00:00Z'))
            self.assertTrue(comp['window_end'].endswith('23:59:59Z'))
            self.assertLessEqual(comp['window_start'][:10], comp['window_end'][:10])
        self.assertEqual(payload['rng_seed'], 'seed-a')

    def test_min_picks_gate_and_scope_isolation(self):
        from mlbcomp.engine.competition import (CompetitionSpec, Slice, Entrant,
                                                build_competitions)
        entrant = Entrant(username='Tester', slice=Slice('MLB_REG_FORM_001'),
                          style='full-spectrum', parent_name='form', parent_env='REG',
                          parent_model='form', parent_market='ML')
        spec = CompetitionSpec('T1', 'Test comp', 'REG', 5, ('2026-05-01', '2026-05-06'),
                               min_picks=20, description='test')
        comps = build_competitions(self._dataset(), [entrant], [spec], seed='s')
        standing = comps[0]['standings'][0]
        self.assertFalse(standing['qualified'])
        self.assertIsNone(standing['rank'])
        self.assertEqual(standing['qualification_status'], 'BELOW_MIN_PICKS')
        # A REG-scoped competition never sees POST/round rows.
        post_row = self._row(env='POST', round_code='DS', result='W', game_date='2026-05-02')
        comps2 = build_competitions(self._dataset() + [post_row], [entrant], [spec], seed='s')
        self.assertEqual(comps2[0]['standings'][0]['picks'], standing['picks'])

    def test_slice_predicates_are_pure_filters(self):
        from mlbcomp.engine.competition import Slice
        row = self._row(selection='OVER 8.5', model_prob=0.58, season=2025,
                        round_code='DS')
        self.assertTrue(Slice('MLB_REG_TOTALS_001', side='OVER').matches(
            {**row, 'strategy_id': 'MLB_REG_TOTALS_001'}))
        self.assertFalse(Slice('MLB_REG_TOTALS_001', side='UNDER').matches(
            {**row, 'strategy_id': 'MLB_REG_TOTALS_001'}))
        s = Slice('MLB_X', min_prob=0.55, max_prob=0.6, seasons=(2024,), rounds=('DS',))
        self.assertFalse(s.matches({**row, 'strategy_id': 'MLB_X'}))            # wrong season
        self.assertTrue(Slice('MLB_X', min_prob=0.55, rounds=('DS',)).matches(
            {**row, 'strategy_id': 'MLB_X'}))
        self.assertFalse(Slice('MLB_X', min_prob=0.59).matches(
            {**row, 'strategy_id': 'MLB_X'}))                                   # prob too low

    def test_empty_export_is_a_valid_no_data_state(self):
        from mlbcomp.engine.competition import build_payloads
        competitors, competitions = build_payloads([], [], [], seed='s')
        self.assertEqual(competitors['entrants'], [])
        self.assertEqual(competitions['competitions'], [])

    def test_players_register_populated_and_valid(self):
        root = Path(__file__).resolve().parent.parent
        players_path = root / 'data' / 'players.json'
        self.assertTrue(players_path.exists())
        players = json.loads(players_path.read_text())
        self.assertGreater(len(players), 4000)
        p0 = players[0]
        for key in ('player_id', 'name', 'mlb_id'):
            self.assertIn(key, p0)
            self.assertTrue(p0[key])

    def test_experiments_models_a_through_e_present(self):
        root = Path(__file__).resolve().parent.parent
        exps = json.loads((root / 'data' / 'research_experiments.json').read_text())
        exp_ids = {x.get('id') for x in exps}
        for model_id in ('EXP_A', 'EXP_B', 'EXP_C', 'EXP_D', 'EXP_E'):
            self.assertIn(model_id, exp_ids)
            item = next(x for x in exps if x.get('id') == model_id)
            self.assertIn('brier', item)
            self.assertIn('log_loss', item)
            self.assertGreater(item.get('sample_size', 0), 0)

    def test_no_unverified_pnl_in_ledger_file(self):
        root = Path(__file__).resolve().parent.parent
        ledger = json.loads((root / 'data' / 'bets_ledger.json').read_text())
        for row in ledger:
            if row.get('verification_status') != 'VERIFIED_PRICE':
                self.assertTrue(row.get('pnl') is None or row.get('pnl') == 0)


if __name__ == '__main__':
    unittest.main()
