"""Prospective, independent, paper-only experiment books.

Funding is an estimate: previously observed hourly rates integrated over time,
not settlement history. Unobserved intervals over 120 seconds and stale samples
are counted as missing (zero estimated funding), never filled using future rates.
Fills assume unlimited liquidity, 4 bps RT adverse slippage, 9 bps RT fees;
no simulated liquidation. These are comparisons, not executable performance.
"""
from __future__ import annotations

import json
import math
import os
from collections import deque
from dataclasses import replace
from datetime import datetime, timezone

from .botinstance import BotInstance
from .brokers import PaperBroker
from .settings import BotConfig
from . import rules


class _SilentNotifier:
    def send(self, *args, **kwargs):
        return None


class _ExperimentBroker(PaperBroker):
    def __init__(self, p, owner):
        super().__init__(p)
        self.owner = owner

    def trade_funding_usdt(self, symbol, direction, size_usdt, accrued,
                           entry_ms=0, exit_ms=0):
        self.owner._accrue_funding(exit_ms / 1000)
        real_estimate = self.owner._funding.get(symbol, {}).get("usd", 0.0)
        return real_estimate + size_usdt * self.p.funding_drag_bps / 1e4


class ExperimentBot(BotInstance):
    VARIANTS = {"control", "s1", "cap3"}
    FUNDING_MAX_GAP_S = 120

    def __init__(self, master, data_dir, variant, *, param_overrides=None, capital=500):
        if variant not in self.VARIANTS:
            raise ValueError("Unknown experiment variant")
        if not math.isfinite(capital) or capital <= 0:
            raise ValueError("Experiment capital must be positive and finite")
        self._persistence_seq = 0
        self.s1_extensions = 0
        self.cohort_skips = 0
        self.funding_gap_count = 0
        self.variant = variant
        self._funding = {}
        self.funding_missing_seconds = 0.0
        self.observation_gap_seconds = 0.0
        self.last_observation_ts = 0.0
        overrides = dict(param_overrides or {})
        overrides.update(paper_gap_fills=True, paper_slippage_bps=4.0,
                         paper_funding_model="flat", slippage_bps=0.0,
                         taker_fee_bps=9.0, hard_stop_enabled=False)
        cfg = BotConfig(id="exp_" + variant, label="EXPERIMENT " + variant,
                        mode="paper", capital_initial=capital, overrides=overrides)
        super().__init__(cfg, master, data_dir)
        self.trades = deque()  # campaign ledger is not truncated at 5000 rows
        self.notifier = _SilentNotifier()
        self.broker = _ExperimentBroker(self.p, self)

    def _ai_exit_overlay(self, now, market):
        return None

    def _catch_up_excursions(self):
        # Do not retroactively substitute candle extremes for observed ticks.
        return None

    def load(self):
        positions = None
        if os.path.exists(self.state_file):
            with open(self.state_file) as f:
                raw = json.load(f)
            extra = raw.get("experiment")
            if not extra or extra.get("variant") != self.variant:
                raise ValueError("Missing or incompatible experiment state")
            self._persistence_seq = extra.get("persistence_seq", 0)
            self.s1_extensions = extra.get("s1_extensions", 0)
            self.cohort_skips = extra.get("cohort_skips", 0)
            self.funding_gap_count = extra.get("funding_gap_count", 0)
            self._funding = extra.get("funding", {})
            self.funding_missing_seconds = extra.get("funding_missing_seconds", 0.0)
            self.observation_gap_seconds = extra.get("observation_gap_seconds", 0.0)
            self.last_observation_ts = extra.get("last_observation_ts", 0.0)
            self._last_trail_eval_4h = extra.get("last_trail_eval_4h", 0.0)
            for key in ("capital", "total_pnl", "peak_balance"):
                if not math.isfinite(raw[key]):
                    raise ValueError("Invalid experiment accounting checkpoint")
            positions = raw.get("positions", [])
            symbols = [pos["symbol"] for pos in positions]
            if (len(symbols) != len(set(symbols))
                    or any(sym not in self.p.all_symbols for sym in symbols)
                    or set(self._funding) != set(symbols)):
                raise ValueError("Incomplete experiment positions/funding checkpoint")
            for pos in positions:
                for key in ("entry_price", "size_usdt"):
                    if not math.isfinite(pos[key]) or pos[key] <= 0:
                        raise ValueError("Invalid experiment position value")
            for ledger in self._funding.values():
                for key in ("ts", "entry_ts", "usd", "missing_s"):
                    if not math.isfinite(ledger[key]):
                        raise ValueError("Invalid funding checkpoint")
                if ledger.get("rate") is not None and not math.isfinite(ledger["rate"]):
                    raise ValueError("Invalid funding rate")
        super().load()
        if positions is not None and len(self.positions) != len(positions):
            raise ValueError("Experiment checkpoint could not be completely restored")
        self._funding = {k: v for k, v in self._funding.items() if k in self.positions}

    def _rate(self, sym, ts):
        st = self.states.get(sym)
        if (st is None or not math.isfinite(st.funding) or st.updated_at <= 0
                or ts - st.updated_at > self.FUNDING_MAX_GAP_S
                or st.updated_at > ts + 1):
            return None
        return st.funding

    def _seed_funding(self):
        for sym, pos in self.positions.items():
            if sym not in self._funding:
                ts = pos.entry_time.timestamp()
                self._funding[sym] = {"ts": ts, "entry_ts": ts, "usd": 0.0,
                                      "rate": self._rate(sym, ts), "missing_s": 0.0}

    def _accrue_funding(self, ts):
        self._seed_funding()
        for sym, pos in self.positions.items():
            ledger = self._funding[sym]
            elapsed = ts - ledger["ts"]
            if elapsed <= 0:
                continue
            rate = ledger.get("rate")
            if elapsed <= self.FUNDING_MAX_GAP_S and rate is not None:
                ledger["usd"] -= pos.direction * pos.size_usdt * rate * elapsed / 3600
            else:
                self.funding_gap_count += 1
                ledger["missing_s"] += elapsed
                self.funding_missing_seconds += elapsed
            ledger.update(ts=ts, rate=self._rate(sym, ts))

    def persistence_extra(self):
        self._seed_funding()
        return {"variant": self.variant, "funding": self._funding,
                "persistence_seq": self._persistence_seq,
                "s1_extensions": self.s1_extensions, "cohort_skips": self.cohort_skips,
                "funding_gap_count": self.funding_gap_count,
                "funding_missing_seconds": self.funding_missing_seconds,
                "observation_gap_seconds": self.observation_gap_seconds,
                "last_observation_ts": self.last_observation_ts,
                "last_trail_eval_4h": self._last_trail_eval_4h}

    def _save_state(self):
        count = self.db.conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        if count != len(self.trades):
            raise RuntimeError("Experiment trade ledger differs from in-memory accounting")
        self._persistence_seq += 1
        super()._save_state()
        with open(self.state_file) as f:
            saved = json.load(f)
        if saved.get("experiment", {}).get("persistence_seq") != self._persistence_seq:
            raise RuntimeError("Experiment checkpoint was not written")

    def on_tick(self, now=None):
        now = now or datetime.now(timezone.utc)
        ts = now.timestamp()
        if self.last_observation_ts and ts - self.last_observation_ts > 120:
            self.observation_gap_seconds += ts - self.last_observation_ts
        self.last_observation_ts = ts
        self._accrue_funding(ts)
        result = super().on_tick(now)
        self._save_state()
        return result

    def _record_close(self, sym, exit_price, exit_dt, reason, funding_adj, cost_bps=None):
        # Funding value is already captured by broker; remove before atomic save.
        self._funding.pop(sym, None)
        super()._record_close(sym, exit_price, exit_dt, reason, funding_adj, cost_bps)

    def _available_entry_margin(self):
        unrealized = 0.0
        used = 0.0
        for sym, pos in self.positions.items():
            st = self.states.get(sym)
            price = st.price if st and st.price > 0 else pos.entry_price
            unrealized += pos.size_usdt * pos.direction * (price / pos.entry_price - 1)
            used += pos.size_usdt / self.p.leverage
        funding = sum(x["usd"] for x in self._funding.values())
        return max(0.0, self._capital + self._total_pnl + unrealized + funding - used)

    def _entry_skip_reason(self, sig, counters, market, capital):
        reason = super()._entry_skip_reason(sig, counters, market, capital)
        if reason:
            return reason
        if self.variant == "cap3" and sum(
            p.strategy == sig["strategy"] and p.direction == sig["direction"]
            for p in self.positions.values()
        ) >= 3:
            self.cohort_skips += 1
            return "experiment_strategy_direction_cap3"
        return None

    def _evaluate_exit(self, pos, unrealized, market, *, trail_gate=True):
        if (self.variant == "s1" and pos.strategy == "S1"
                and pos.hours_to_timeout <= 0 and not pos.extended and unrealized > 0):
            # All ordinary protections still run; only natural timeout is deferred.
            protective = rules.evaluate_exit(replace(pos, hours_to_timeout=1),
                                             unrealized, market, self.p,
                                             trail_gate=trail_gate)
            if protective is not None:
                return protective
            self.s1_extensions += 1
            return rules.ExitDecision("extend", "experiment_s1_extend", None, 24)
        return super()._evaluate_exit(pos, unrealized, market, trail_gate=trail_gate)

    def experiment_snapshot(self, now):
        warnings = []
        unrealized = 0.0
        exposure = 0.0
        for sym, pos in self.positions.items():
            st = self.states.get(sym)
            exposure += pos.size_usdt
            if (not st or st.price <= 0 or not math.isfinite(st.price)
                    or st.updated_at <= 0 or now.timestamp() - st.updated_at > 120):
                warnings.append("stale_price:" + sym)
                continue
            exit_px = st.price * (1 - pos.direction * 2 / 10000)
            unrealized += pos.size_usdt * (pos.direction * (exit_px / pos.entry_price - 1)
                                          - self.p.taker_fee_bps / 10000)
        unrealized += sum(x["usd"] for x in self._funding.values())
        if self.funding_missing_seconds:
            warnings.append("estimated_funding_has_missing_intervals")
        valid = not any(x.startswith("stale_price:") for x in warnings)
        return {"equity": self._capital + self._total_pnl + unrealized if valid else None,
                "realized_pnl": self._total_pnl,
                "unrealized_pnl": unrealized if valid else None,
                "positions_count": len(self.positions), "closed_trades": len(self.trades),
                "exposure_usdt": exposure, "s1_extensions": self.s1_extensions,
                "cohort_skips": self.cohort_skips, "status": self.status,
                "warnings": warnings, "funding_gap_count": self.funding_gap_count,
                "funding_missing_seconds": self.funding_missing_seconds}
