import dataclasses
from datetime import datetime, timezone
import json
import os
import sqlite3
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from alfred.botinstance import BotInstance
from alfred.settings import BotConfig
from alfred import rules
import ai_entry_arbiter as ai
import ai_jev as jev

ANSWER_HOLD = {"model": "jev-1.13.0", "usage": {"input_tokens": 400, "output_tokens": 30},
               "answers": {"decision": {"type": "choice", "choice": "HOLD", "confidence": 0.9,
                                        "probabilities": {"HOLD": 0.95, "GO": 0.05}},
                           "win": {"type": "noul", "noul": 0.1}}}


class ConfigTests(unittest.TestCase):
    def test_no_act_mode_exists(self):
        for mode in ("act", "ACT", "live", "", "shadow"):
            with patch.dict(os.environ, {"AI_JEV_MODE": mode, "TYPESAFE_API_KEY": "k"}):
                self.assertIn(jev.config()["mode"], ("off", "shadow"))
                self.assertEqual(jev.config()["mode"], "shadow" if mode == "shadow" else "off")

    def test_off_without_key(self):
        env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
        env["AI_JEV_MODE"] = "shadow"
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(jev.config()["mode"], "off")


class JudgeTests(unittest.TestCase):
    def setUp(self):
        self.ctx = patch("ai_external_context.context_for",
                         return_value={"facts": [{"symbol": "NEAR", "id": "f1"},
                                                 {"symbol": "DOGE", "id": "f2"},
                                                 {"symbol": "MACRO", "id": "m"}]})
        self.ctx.start()
        self.addCleanup(self.ctx.stop)

    def test_parses_and_filters_facts_per_symbol(self):
        seen = []
        def post(payload, timeout):
            seen.append(json.loads(payload["state"]))
            return ANSWER_HOLD
        with patch.object(jev, "_post", side_effect=post):
            r = jev.judge("entry", [{"symbol": "NEAR", "prior_decision": {"x": 1}}], {},
                          model="jev-latest", timeout=5, max_items=5)
        v = r["verdicts"]["NEAR"]
        self.assertEqual((v["decision"], v["win"]), ("HOLD", 0.1))
        self.assertEqual({f["id"] for f in seen[0]["external_facts"]}, {"f1", "m"})
        self.assertNotIn("prior_decision", seen[0]["item"])

    def test_errors_and_incomplete_answers_never_raise(self):
        def post(payload, timeout):
            if "NEAR" in payload["state"]:
                raise OSError("down")
            return {"answers": {"decision": {"type": "choice", "choice": "GO"}}}
        with patch.object(jev, "_post", side_effect=post):
            r = jev.judge("entry", [{"symbol": "NEAR"}, {"symbol": "DOGE"}], {},
                          model="m", timeout=5, max_items=5)
        self.assertEqual(r["verdicts"], {})
        self.assertIn("OSError", r["errors"]["NEAR"])
        self.assertIn("incomplète", r["errors"]["DOGE"])

    def test_timeout_is_bounded(self):
        with patch.object(jev, "_post", side_effect=lambda p, t: time.sleep(3)):
            t0 = time.time()
            r = jev.judge("exit", [{"symbol": "NEAR"}], {}, model="m", timeout=0.2, max_items=5)
        self.assertLess(time.time() - t0, 2.5)
        self.assertIn("timeout", r["errors"]["NEAR"])


class ShadowNoEffectTests(unittest.TestCase):
    """JEV HOLD sur tout : la taille et les fills restent ceux des règles."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        states = {s: SimpleNamespace(price=10.0, oi_history=[], impact_bid=0, impact_ask=0)
                  for s in ["NEAR", "AAVE"]}
        master = SimpleNamespace(states=states, snapshot=None)
        self.bot = BotInstance(BotConfig(id="live", label="t", mode="paper",
                                         capital_initial=500), master, self.tmp.name)
        self.addCleanup(self.bot.db.close)
        self.bot.notifier = Mock(); self.bot._place_hard_stop = Mock(); self.bot._save_state = Mock()
        self.bot._btc_z = 0
        self.now = datetime.now(timezone.utc); self.market = rules.MarketCtx(btc_z=0)
        patch.dict(os.environ, {"AI_ARBITER_ENABLED": "1", "AI_ARBITER_MODE": "shadow",
                                "AI_JEV_MODE": "shadow", "TYPESAFE_API_KEY": "k"}).start()
        patch.object(ai, "is_tripped", return_value=False).start()
        patch.object(ai, "arbitrate_safe", return_value={"verdicts": {}, "meta": {}}).start()
        patch("ai_external_context.context_for", return_value={"facts": []}).start()
        self.addCleanup(patch.stopall)

    def sig(self, symbol="NEAR"):
        return dict(symbol=symbol, strategy="S5", direction=-1, z=3, strength=2, info="t")

    def events(self, name):
        return self.bot.db.conn.execute("SELECT symbol, data FROM events WHERE event=?",
                                        (name,)).fetchall()

    def test_hold_verdict_does_not_change_fill(self):
        with patch.object(jev, "_post", return_value=ANSWER_HOLD):
            self.assertEqual(self.bot._rank_and_enter([self.sig()], self.now, self.market), 1)
        expected = rules.position_size("S5", -1, 500, 0, self.bot.p)
        self.assertAlmostEqual(self.bot.positions["NEAR"].size_usdt, expected)
        rows = self.events("JEV_ENTRY_SHADOW")
        self.assertEqual(len(rows), 1)
        d = json.loads(rows[0][1])
        self.assertEqual((d["decision"], d["strategy"], d["dir"]), ("HOLD", "S5", "SHORT"))
        cost = [json.loads(r[1]) for r in self.events("AI_COST")]
        self.assertEqual([c["source"] for c in cost], ["jev_entry"])

    def test_api_failure_keeps_entry_and_logs_failopen(self):
        with patch.object(jev, "_post", side_effect=OSError("down")):
            self.assertEqual(self.bot._rank_and_enter([self.sig()], self.now, self.market), 1)
        self.assertEqual(len(self.events("JEV_FAILOPEN")), 1)
        self.assertEqual(self.events("JEV_ENTRY_SHADOW"), [])

    def test_other_bots_never_call(self):
        self.bot.id = "paper"
        with patch.object(jev, "judge") as j:
            self.bot._jev_shadow("entry", [{"symbol": "NEAR"}], {}, {})
        j.assert_not_called()

    def test_off_mode_never_calls(self):
        with patch.dict(os.environ, {"AI_JEV_MODE": "off"}), patch.object(jev, "judge") as j:
            self.bot._jev_shadow("entry", [{"symbol": "NEAR"}], {}, {})
        j.assert_not_called()


class ScorecardTests(unittest.TestCase):
    def test_counterfactuals(self):
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE trades(symbol,strategy,direction,entry_time,exit_time,pnl_usdt)")
        c.execute("CREATE TABLE events(ts,event,symbol,data)")
        t0 = 1_790_000_000
        iso = lambda ts: datetime.fromtimestamp(ts, timezone.utc).isoformat()
        c.executemany("INSERT INTO trades VALUES(?,?,?,?,?,?)", [
            ("A", "S5", "SHORT", iso(t0 + 5), iso(t0 + 9000), -10.0),   # HOLD, perdant
            ("B", "S1", "LONG", iso(t0 + 5), iso(t0 + 9000), 20.0),     # GO, gagnant
            ("C", "S9", "SHORT", iso(t0 + 5), None, None)])              # en cours
        ev = lambda e, s, d: c.execute("INSERT INTO events VALUES(?,?,?,?)",
                                       (t0, e, s, json.dumps(d)))
        ev("JEV_ENTRY_SHADOW", "A", {"strategy": "S5", "dir": "SHORT", "decision": "HOLD", "win": 0.2})
        ev("JEV_ENTRY_SHADOW", "B", {"strategy": "S1", "dir": "LONG", "decision": "GO", "win": 0.8})
        ev("JEV_ENTRY_SHADOW", "C", {"strategy": "S9", "dir": "SHORT", "decision": "GO", "win": 0.5})
        ev("JEV_ENTRY_SHADOW", "D", {"strategy": "S9", "dir": "SHORT", "decision": "GO", "win": 0.5})
        ms = (t0 + 5) * 1000
        ev("JEV_EXIT_SHADOW", "A", {"action": "HOLD", "net_pnl": -3.0, "entry_ts_ms": ms, "better_exit_now": 0.3})
        ev("JEV_EXIT_SHADOW", "A", {"action": "CUT", "net_pnl": -4.0, "entry_ts_ms": ms, "better_exit_now": 0.9})
        ev("JEV_EXIT_SHADOW", "A", {"action": "CUT", "net_pnl": -8.0, "entry_ts_ms": ms, "better_exit_now": 0.9})
        s = jev.scorecard(c)
        e, x = s["entry"], s["exit"]
        self.assertEqual((e["n"], e["resolved"], e["pending"], e["not_entered"]), (4, 2, 1, 1))
        self.assertEqual(e["delta_if_hold_vetoed"], 10.0)
        self.assertEqual(e["brier"], round((0.2 ** 2 + 0.2 ** 2) / 2, 4))
        # première CUT à −4, final −10 ⇒ couper aurait sauvé 6
        self.assertEqual((x["positions"], x["cut_positions"], x["delta_if_cut"]), (1, 1, 6.0))
        # D encore ouverte (absente de trades) : en cours, pas « non entrée »
        s = jev.scorecard(c, [{"symbol": "D", "strategy": "S9", "direction": -1,
                               "entry_time": iso(t0 + 8)}])
        self.assertEqual((s["entry"]["pending"], s["entry"]["not_entered"]), (2, 0))


if __name__ == "__main__":
    unittest.main()
