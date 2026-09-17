"""Accounting and censoring regressions; synthetic DBs, no production writes."""

import json
from pathlib import Path
import random
import sqlite3
import tempfile
import unittest

from audit_live_paper import (cohort, decompose, intervention_evidence,
                              load_bot, make_report, timestamp, trade_key)


def trade(symbol="ARB", size=100.0, pnl=10.0, entry="2026-09-01T00:03:00Z",
          exit="2026-09-02T00:03:00Z"):
    return dict(symbol=symbol, strategy="S5", direction="LONG", size_usdt=size,
                pnl_usdt=pnl, entry_time=entry, exit_time=exit, reason="timeout")


def book(rows, positions=()):
    return cohort({"trades": rows, "state": {"positions": positions}},
                  timestamp("2026-09-01"), timestamp("2026-09-03"))


class AccountingTests(unittest.TestCase):
    def test_size_and_return_components_are_distinct(self):
        # Paper 100 * 10%; Live 50 * 6% => delta -7 = size -5 + return -2.
        r = decompose(book([trade()]), book([trade(size=50, pnl=3)]))
        self.assertAlmostEqual(r["attribution"]["size_effect_usd"], -5)
        self.assertAlmostEqual(r["attribution"]["return_effect_usd"], -2)
        self.assertAlmostEqual(r["live_minus_paper_usd"], -7)

    def test_unmatched_and_pending_are_not_execution_slippage(self):
        p = book([trade(), trade("DOGE", pnl=-4)])
        l = book([trade("SOL", pnl=7)], [trade(exit=None)])
        r = decompose(p, l)
        self.assertEqual(r["matched_closed"], 0)
        self.assertEqual(r["attribution"]["pending_pair_realized_delta_usd"], -10)
        self.assertEqual(r["attribution"]["unmatched_closed_delta_usd"], 11)
        self.assertEqual(r["attribution"]["return_effect_usd"], 0)
        self.assertEqual(r["live_minus_paper_usd"], 1)

    def test_future_exit_is_censored(self):
        r = decompose(book([trade()]), book([trade(pnl=999, exit="2026-09-04T00:00Z")]))
        self.assertEqual(r["closed_pnl_usd"]["live"], 0)
        self.assertEqual(r["attribution"]["pending_pair_realized_delta_usd"], -10)

    def test_duplicates_refuse_to_emit(self):
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            book([trade(), trade(entry="2026-09-01T01:00Z")])

    def test_timezones_and_different_entry_periods(self):
        self.assertEqual(trade_key(trade()), trade_key(trade(entry="2026-09-01T02:03:00+02:00")))
        r = decompose(book([trade()]), book([trade(entry="2026-09-01T04:03:00Z")]))
        self.assertEqual(r["matched_closed"], 0)
        self.assertEqual(len(r["unmatched"]), 2)

    def test_invalid_financial_input_refuses_to_emit(self):
        for size in (0, -1, float("nan"), float("inf")):
            with self.subTest(size=size), self.assertRaises(ValueError):
                book([trade(size=size)])
        with self.assertRaises(ValueError):
            book([trade(pnl=float("nan"))])

    def test_identity_with_random_sizes_returns_and_missing_entries(self):
        rng = random.Random(17)
        for _ in range(50):
            p, l = [], []
            for i in range(20):
                if rng.random() < .8:
                    p.append(trade(str(i), rng.uniform(10, 1000), rng.uniform(-100, 100)))
                if rng.random() < .8:
                    l.append(trade(str(i), rng.uniform(10, 1000), rng.uniform(-100, 100)))
            r = decompose(book(p), book(l))
            self.assertAlmostEqual(r["identity_residual_usd"], 0, places=8)

    def test_ai_evidence_must_target_this_position(self):
        p = decompose(book([trade()]), book([trade()]))["pairs"][0]
        base = dict(ts=timestamp("2026-09-01T10:00Z"), event="ARBITER_EXIT_DECISION",
                    symbol="ARB", data={"entry_ts_ms": timestamp(p["live_entry"]) * 1000,
                                        "action": "LOCK"})
        wrong = dict(base, data=dict(base["data"], entry_ts_ms=0))
        self.assertEqual(len(intervention_evidence(p, [base, wrong])), 1)


class ReadOnlyTests(unittest.TestCase):
    def test_real_sqlite_input_and_state_are_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            for name in ("paper", "live"):
                directory = root / "bots" / name
                directory.mkdir(parents=True)
                state = {"version": "fixture", "positions": [],
                         "_perf_track_start_ts": timestamp("2026-09-01")}
                (directory / "state.json").write_text(json.dumps(state))
                c = sqlite3.connect(directory / "bot.db")
                r = trade(size=100 if name == "paper" else 50,
                          pnl=10 if name == "paper" else 3)
                c.execute("CREATE TABLE trades (symbol,strategy,direction,size_usdt,pnl_usdt,entry_time,exit_time,reason)")
                c.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?)", tuple(r.values()))
                c.execute("CREATE TABLE events (ts,event,symbol,data)")
                c.execute("INSERT INTO events VALUES (?,?,?,?)",
                          (timestamp("2026-09-03"), "SCAN", None, "{}"))
                c.commit()
                c.close()
            before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
            report = make_report(root)
            self.assertEqual(report["matched_closed"], 1)
            self.assertAlmostEqual(report["live_minus_paper_usd"], -7)
            self.assertEqual(before, {p: p.read_bytes() for p in root.rglob("*") if p.is_file()})
            with self.assertRaisesRegex(ValueError, "exceeds"):
                make_report(root, until=timestamp("2026-09-04"))

    def test_missing_database_is_not_created(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            directory = root / "bots/live"
            directory.mkdir(parents=True)
            (directory / "state.json").write_text("{}")
            with self.assertRaises(sqlite3.OperationalError):
                load_bot(root, "live")
            self.assertFalse((directory / "bot.db").exists())


if __name__ == "__main__":
    unittest.main()
