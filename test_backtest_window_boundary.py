"""Behavioural regression: next-open execution respects the tested window."""
import unittest
from unittest.mock import patch

from backtests import backtest_rolling as engine


class WindowBoundaryTests(unittest.TestCase):
    step = 4 * 3600 * 1000
    start = 1726488000000

    def run_fixture(self, end):
        # Only the first bar has a signal. The next bar has a deliberately
        # different price, making the old backwards final mark observable.
        bars = [dict(t=self.start + i * self.step, o=100 + i,
                     h=101 + i, l=99 + i, c=100 + i, v=1000)
                for i in range(3)]
        features = {"SOL": [dict(t=b["t"], _idx=i) for i, b in enumerate(bars)]}

        def candidate(ts, *args):
            return ([dict(coin="SOL", dir=-1, strat="S1", z=4,
                          strength=4, hold=100)] if ts == self.start else [])

        # Isolate detection only; portfolio checks, sizing, execution, costs,
        # and final settlement all use the real engine.
        with patch.object(engine._alf_signals, "detect_token_signals", return_value=[]), \
             patch.object(engine._alf_signals, "detect_squeeze_at", return_value=None):
            return engine.run_window(features, {"SOL": bars}, {}, {},
                                     self.start, end, start_capital=500,
                                     extra_candidate_fn=candidate, aligned=True)

    def test_last_signal_cannot_execute_after_end(self):
        result = self.run_fixture(self.start)
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["end_capital"], 500)

    def test_half_open_window_excludes_next_open_at_boundary(self):
        result = self.run_fixture(self.start + self.step - 1)
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["end_capital"], 500)

    def test_inclusive_boundary_preserves_valid_entry_and_final_mark(self):
        result = self.run_fixture(self.start + self.step)
        self.assertEqual(len(result["trades"]), 1)
        trade = result["trades"][0]
        self.assertEqual(trade["entry_t"], self.start + self.step)
        self.assertEqual(trade["exit_t"], trade["entry_t"])
        self.assertEqual(trade["reason"], "mtm_final")
        self.assertAlmostEqual(result["end_capital"], 500 + trade["pnl"])
        self.assertLess(trade["pnl"], 0)  # unchanged price still pays costs


if __name__ == "__main__":
    unittest.main()
