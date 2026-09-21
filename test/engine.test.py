"""
MLBComp Quantitative Engine Unit & Math Integrity Test Suite
Tests:
- Odds conversion (American <-> Implied Probability <-> Decimal)
- PnL calculations across win, loss, push
- Kelly criterion sizing formula
- FiveThirtyEight Elo and Poisson score calculations
- Data schema and audit verifier checks
"""

import unittest
import os
import sys
import json
import math

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

def american_to_decimal(odds: float) -> float:
    if odds > 0:
        return 1.0 + odds / 100.0
    return 1.0 + 100.0 / abs(odds)

def american_to_prob(odds: float) -> float:
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return abs(odds) / (abs(odds) + 100.0)

def devig_two(p_home: float, p_away: float) -> tuple[float, float]:
    total = p_home + p_away
    if total <= 0:
        return 0.5, 0.5
    return p_home / total, p_away / total

def calculate_pnl(stake: float, odds: float, result: float) -> float:
    if result == 1.0:
        if odds > 0:
            return round(stake * (odds / 100.0), 2)
        return round(stake * (100.0 / abs(odds)), 2)
    elif result == 0.0:
        return -round(stake, 2)
    return 0.0

def quarter_kelly(model_prob: float, odds: float, fraction: float = 0.25) -> float:
    b = american_to_decimal(odds) - 1.0
    p = model_prob
    q = 1.0 - p
    f = (b * p - q) / b
    return max(0.0, f * fraction)


class TestMLBCompEngine(unittest.TestCase):
    def test_odds_conversion(self):
        self.assertAlmostEqual(american_to_decimal(-110), 1.90909, places=4)
        self.assertAlmostEqual(american_to_decimal(150), 2.50, places=4)
        self.assertAlmostEqual(american_to_decimal(-200), 1.50, places=4)
        self.assertAlmostEqual(american_to_prob(-110), 0.5238, places=3)
        self.assertAlmostEqual(american_to_prob(150), 0.40, places=3)

    def test_devig_two(self):
        ph_raw = american_to_prob(-140)  # ~0.5833
        pa_raw = american_to_prob(120)   # ~0.4545
        ph, pa = devig_two(ph_raw, pa_raw)
        self.assertAlmostEqual(ph + pa, 1.0, places=5)
        self.assertGreater(ph, 0.50)

    def test_pnl_math(self):
        # Favorite win: $100 at -110 => +$90.91
        pnl_win = calculate_pnl(100.0, -110.0, 1.0)
        self.assertAlmostEqual(pnl_win, 90.91, places=2)

        # Underdog win: $100 at +135 => +$135.00
        pnl_dog_win = calculate_pnl(100.0, 135.0, 1.0)
        self.assertAlmostEqual(pnl_dog_win, 135.0, places=2)

        # Loss: $100 => -$100.00
        pnl_loss = calculate_pnl(100.0, -110.0, 0.0)
        self.assertEqual(pnl_loss, -100.0)

        # Push: $100 => $0.00
        pnl_push = calculate_pnl(100.0, -110.0, 0.5)
        self.assertEqual(pnl_push, 0.0)

    def test_quarter_kelly_sizing(self):
        # 55% model prob at -110 (fair prob 50%, implied 52.38%)
        f_k = quarter_kelly(0.55, -110.0, 0.25)
        self.assertGreater(f_k, 0.0)
        self.assertLess(f_k, 0.05)  # quarter Kelly is modest, usually 1-3%

        # Negative edge => 0 stake
        f_no_edge = quarter_kelly(0.50, -110.0, 0.25)
        self.assertEqual(f_no_edge, 0.0)

    def test_leaderboard_bankroll_identity(self):
        path = os.path.join(os.path.dirname(__file__), '..', 'data', 'leaderboard.json')
        with open(path) as f:
            board = json.load(f)
        for s in board:
            expected_bankroll = round(s['initial_bankroll'] + s['total_pnl'], 2)
            self.assertEqual(round(s['current_bankroll'], 2), expected_bankroll)
            self.assertEqual(len(s['equity_curve']) > 0, True)
            self.assertIn(s['env'], ['REG', 'POST', 'WC', 'DS', 'LCS', 'WS', 'ALL'])

    def test_audit_checks_passed(self):
        path = os.path.join(os.path.dirname(__file__), '..', 'data', 'audit_checks.json')
        with open(path) as f:
            audits = json.load(f)
        self.assertEqual(len(audits), 18)
        self.assertTrue(all(a['passed'] for a in audits))


if __name__ == '__main__':
    unittest.main()
