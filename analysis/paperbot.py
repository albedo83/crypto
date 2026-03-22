"""Paper Trading Bot v2 — Composite signal, DB-backed, dashboard-ready.

Composite signal:
  - Book imbalance (TOB L1)     weight 0.35
  - OFI 5s (buy-sell flow)      weight 0.25
  - BTC lead-lag (5s ahead)     weight 0.15
  - Book velocity (dImb/dt)     weight 0.15
  - Trade intensity             weight 0.10

Filters: low volatility + tight spread (2.7x amplification)
Entry: composite z-score > 2.0 (extreme only → higher edge)
Hold: 60s, or reversal

Run:  python3 -m analysis.paperbot
Stop: Ctrl+C (graceful shutdown)
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

import asyncpg
import numpy as np
import orjson

from src.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [PAPERBOT] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("paperbot")

# ── Config ───────────────────────────────────────────────────────────
SYMBOLS = {1: "BTCUSDT", 2: "ETHUSDT", 3: "ADAUSDT"}
POLL_INTERVAL = 2
BUFFER_SECONDS = 300       # 5 min rolling buffer
HOLD_SECONDS = 60
COMPOSITE_THRESHOLD = 2.0  # z-score threshold for entry
COST_BPS = 5.0

WEIGHTS = {
    "book_imbalance": 0.35,
    "ofi": 0.25,
    "btc_lead": 0.15,
    "book_velocity": 0.15,
    "intensity": 0.10,
}


@dataclass
class Position:
    symbol: str
    instrument_id: int
    direction: int
    entry_price: float
    entry_time: datetime
    composite_score: float
    filters: dict


@dataclass
class SignalState:
    """Rolling signal state per instrument."""
    imbalances: deque = field(default_factory=lambda: deque(maxlen=120))
    mids: deque = field(default_factory=lambda: deque(maxlen=120))
    spreads: deque = field(default_factory=lambda: deque(maxlen=120))
    trade_counts: deque = field(default_factory=lambda: deque(maxlen=120))
    ofis: deque = field(default_factory=lambda: deque(maxlen=120))
    btc_rets: deque = field(default_factory=lambda: deque(maxlen=120))


class PaperBot:
    def __init__(self):
        self._pool: asyncpg.Pool | None = None
        self._running = False
        self._positions: dict[int, Position] = {}
        self._signals: dict[int, SignalState] = {iid: SignalState() for iid in SYMBOLS}
        self._trade_count = 0
        self._total_gross = 0.0
        self._total_net = 0.0
        self._wins = 0

    async def start(self):
        self._pool = await asyncpg.create_pool(dsn=settings.dsn, min_size=1, max_size=3)

        # Run migration if needed
        await self._pool.execute("""
            CREATE TABLE IF NOT EXISTS paper_trades (
                id SERIAL PRIMARY KEY, ts TIMESTAMPTZ DEFAULT now(),
                symbol TEXT, direction TEXT,
                entry_time TIMESTAMPTZ, exit_time TIMESTAMPTZ,
                entry_price DOUBLE PRECISION, exit_price DOUBLE PRECISION,
                hold_seconds DOUBLE PRECISION, composite_score DOUBLE PRECISION,
                gross_pnl_bps DOUBLE PRECISION, net_pnl_bps DOUBLE PRECISION,
                cost_bps DOUBLE PRECISION, reason TEXT, filters JSONB
            )
        """)
        await self._pool.execute("""
            CREATE TABLE IF NOT EXISTS paper_state (
                id SERIAL PRIMARY KEY, updated_at TIMESTAMPTZ DEFAULT now(),
                bot_running BOOLEAN DEFAULT false, total_trades INT DEFAULT 0,
                gross_pnl_bps DOUBLE PRECISION DEFAULT 0,
                net_pnl_bps DOUBLE PRECISION DEFAULT 0,
                win_rate DOUBLE PRECISION DEFAULT 0,
                positions JSONB DEFAULT '[]', signals JSONB DEFAULT '{}'
            )
        """)
        # Ensure state row exists
        cnt = await self._pool.fetchval("SELECT count(*) FROM paper_state")
        if cnt == 0:
            await self._pool.execute("INSERT INTO paper_state (bot_running) VALUES (true)")
        else:
            await self._pool.execute("UPDATE paper_state SET bot_running = true, updated_at = now()")

        self._running = True
        log.info("Bot started — composite signal, threshold=%.1f, hold=%ds, cost=%.1f bps",
                 COMPOSITE_THRESHOLD, HOLD_SECONDS, COST_BPS)

        try:
            await self._run_loop()
        except asyncio.CancelledError:
            pass
        finally:
            await self._shutdown()

    async def _run_loop(self):
        while self._running:
            try:
                await self._tick()
            except Exception:
                log.exception("Tick error")
            await asyncio.sleep(POLL_INTERVAL)

    async def _tick(self):
        now = datetime.now(timezone.utc)
        since = now - timedelta(seconds=BUFFER_SECONDS)

        async with self._pool.acquire() as conn:
            # ── Fetch recent data for all symbols ────────────
            book_rows = await conn.fetch("""
                SELECT instrument_id,
                       avg(bid_qty / NULLIF(bid_qty + ask_qty, 0)) AS imbalance,
                       last(mid_price, exchange_ts) AS mid,
                       avg(spread_bps) AS spread,
                       count(*) AS tick_count
                FROM book_tob
                WHERE exchange_ts > $1
                GROUP BY instrument_id
                ORDER BY instrument_id
            """, since)

            trade_rows = await conn.fetch("""
                SELECT instrument_id,
                       count(*) AS trade_count,
                       sum(CASE WHEN aggressor_side='BUY' THEN notional ELSE 0 END) AS buy_n,
                       sum(CASE WHEN aggressor_side='SELL' THEN notional ELSE 0 END) AS sell_n
                FROM trades_raw
                WHERE exchange_ts > $1 AND aggressor_side IS NOT NULL
                GROUP BY instrument_id
            """, now - timedelta(seconds=10))

            # Recent 2s snapshot for fine-grained imbalance
            snap_rows = await conn.fetch("""
                SELECT instrument_id,
                       avg(bid_qty / NULLIF(bid_qty + ask_qty, 0)) AS imbalance,
                       last(mid_price, exchange_ts) AS mid,
                       last(spread_bps, exchange_ts) AS spread
                FROM book_tob
                WHERE exchange_ts > $1
                GROUP BY instrument_id
            """, now - timedelta(seconds=3))

        book_map = {r["instrument_id"]: r for r in book_rows}
        trade_map = {r["instrument_id"]: r for r in trade_rows}
        snap_map = {r["instrument_id"]: r for r in snap_rows}

        # Get BTC return for lead-lag
        btc_state = self._signals.get(1)
        btc_ret = 0.0
        if btc_state and len(btc_state.mids) >= 2:
            btc_ret = (btc_state.mids[-1] / btc_state.mids[-2] - 1) * 1e4

        all_signals = {}

        for iid, sym in SYMBOLS.items():
            snap = snap_map.get(iid)
            book = book_map.get(iid)
            trade = trade_map.get(iid)
            if not snap or not book:
                continue

            state = self._signals[iid]
            imb = float(snap["imbalance"] or 0.5)
            mid = float(snap["mid"] or 0)
            spread = float(snap["spread"] or 0)

            if mid == 0:
                continue

            state.imbalances.append(imb)
            state.mids.append(mid)
            state.spreads.append(spread)

            # OFI
            if trade:
                buy_n = float(trade["buy_n"] or 0)
                sell_n = float(trade["sell_n"] or 0)
                total = buy_n + sell_n
                ofi = (buy_n - sell_n) / total if total > 0 else 0
            else:
                ofi = 0
            state.ofis.append(ofi)
            state.trade_counts.append(int(trade["trade_count"]) if trade else 0)
            state.btc_rets.append(btc_ret)

            if len(state.imbalances) < 10:
                continue

            # ── Compute composite signal ─────────────────────
            imb_arr = np.array(state.imbalances)
            mid_arr = np.array(state.mids)
            ofi_arr = np.array(state.ofis)
            tc_arr = np.array(state.trade_counts, dtype=float)
            sp_arr = np.array(state.spreads)

            def zscore(x):
                s = np.std(x)
                return (x[-1] - np.mean(x)) / s if s > 0 else 0.0

            z_imb = zscore(imb_arr)
            z_ofi = zscore(ofi_arr)
            z_intensity = zscore(tc_arr)

            # Book velocity
            if len(imb_arr) >= 3:
                vel = imb_arr[-1] - imb_arr[-3]  # change over ~10s
                vel_std = np.std(np.diff(imb_arr))
                z_vel = vel / vel_std if vel_std > 0 else 0.0
            else:
                z_vel = 0.0

            # BTC lead-lag (only for non-BTC symbols)
            if iid != 1 and len(state.btc_rets) >= 2:
                z_btc_lead = state.btc_rets[-2] / 5.0  # previous BTC return, normalized
            else:
                z_btc_lead = 0.0

            composite = (
                WEIGHTS["book_imbalance"] * z_imb +
                WEIGHTS["ofi"] * z_ofi +
                WEIGHTS["btc_lead"] * z_btc_lead +
                WEIGHTS["book_velocity"] * z_vel +
                WEIGHTS["intensity"] * z_intensity
            )

            # Filters
            rvol = np.std(np.diff(mid_arr) / mid_arr[:-1]) if len(mid_arr) > 10 else 0
            rvol_median = np.median(np.abs(np.diff(mid_arr) / mid_arr[:-1])) if len(mid_arr) > 10 else rvol
            low_vol = rvol <= rvol_median * 1.5
            tight_spread = spread <= float(np.median(sp_arr))

            all_signals[sym] = {
                "composite": round(float(composite), 3),
                "z_imb": round(float(z_imb), 2),
                "z_ofi": round(float(z_ofi), 2),
                "z_vel": round(float(z_vel), 2),
                "z_btc_lead": round(float(z_btc_lead), 2),
                "z_intensity": round(float(z_intensity), 2),
                "mid": float(mid),
                "spread_bps": round(float(spread), 2),
                "low_vol": bool(low_vol),
                "tight_spread": bool(tight_spread),
            }

            # ── Position management ──────────────────────────
            if iid in self._positions:
                pos = self._positions[iid]
                held = (now - pos.entry_time).total_seconds()
                reversal = (
                    (pos.direction == 1 and composite < -COMPOSITE_THRESHOLD) or
                    (pos.direction == -1 and composite > COMPOSITE_THRESHOLD)
                )
                if held >= HOLD_SECONDS or reversal:
                    reason = "timeout" if held >= HOLD_SECONDS else "reversal"
                    await self._close_position(iid, mid, now, reason)

            else:
                if not low_vol or not tight_spread:
                    continue
                if composite > COMPOSITE_THRESHOLD:
                    direction = 1
                elif composite < -COMPOSITE_THRESHOLD:
                    direction = -1
                else:
                    continue

                pos = Position(
                    symbol=sym, instrument_id=iid, direction=direction,
                    entry_price=mid, entry_time=now,
                    composite_score=composite,
                    filters={"low_vol": low_vol, "tight_spread": tight_spread,
                             "spread_bps": spread},
                )
                self._positions[iid] = pos
                side = "LONG" if direction == 1 else "SHORT"
                log.info("→ ENTER %s %s @ %.4f | score=%+.2f | z_imb=%+.1f z_ofi=%+.1f z_vel=%+.1f",
                         side, sym, mid, composite, z_imb, z_ofi, z_vel)

        # ── Update state in DB ───────────────────────────────
        positions_json = []
        for iid, pos in self._positions.items():
            mid = all_signals.get(pos.symbol, {}).get("mid", pos.entry_price)
            positions_json.append({
                "symbol": pos.symbol,
                "direction": "LONG" if pos.direction == 1 else "SHORT",
                "entry_price": pos.entry_price,
                "entry_time": pos.entry_time.isoformat(),
                "unrealized_bps": round(pos.direction * (mid / pos.entry_price - 1) * 1e4, 2),
                "composite": pos.composite_score,
            })

        win_rate = self._wins / self._trade_count if self._trade_count > 0 else 0

        await self._pool.execute("""
            UPDATE paper_state SET
                updated_at = now(), bot_running = true,
                total_trades = $1, gross_pnl_bps = $2, net_pnl_bps = $3,
                win_rate = $4, positions = $5, signals = $6
        """,
            self._trade_count, self._total_gross, self._total_net,
            win_rate, orjson.dumps(positions_json).decode(),
            orjson.dumps(all_signals, default=str).decode(),
        )

    async def _close_position(self, iid: int, exit_price: float,
                              exit_time: datetime, reason: str):
        pos = self._positions.pop(iid)
        gross = pos.direction * (exit_price / pos.entry_price - 1) * 1e4
        net = gross - COST_BPS
        hold_s = (exit_time - pos.entry_time).total_seconds()

        self._trade_count += 1
        self._total_gross += gross
        self._total_net += net
        if gross > 0:
            self._wins += 1

        await self._pool.execute("""
            INSERT INTO paper_trades
                (symbol, direction, entry_time, exit_time, entry_price, exit_price,
                 hold_seconds, composite_score, gross_pnl_bps, net_pnl_bps, cost_bps,
                 reason, filters)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
        """,
            pos.symbol,
            "LONG" if pos.direction == 1 else "SHORT",
            pos.entry_time, exit_time,
            pos.entry_price, exit_price,
            hold_s, pos.composite_score,
            gross, net, COST_BPS, reason,
            orjson.dumps(pos.filters, default=str).decode(),
        )

        arrow = "+" if gross > 0 else "-"
        log.info(
            "%s EXIT %s %s @ %.4f→%.4f | %.0fs | score=%+.1f | "
            "gross %+.1f net %+.1f | cumul %+.1f bps (#%d, win %.0f%%)",
            arrow, "LONG" if pos.direction == 1 else "SHORT",
            pos.symbol, pos.entry_price, exit_price, hold_s,
            pos.composite_score, gross, net,
            self._total_net, self._trade_count,
            (self._wins / self._trade_count * 100) if self._trade_count > 0 else 0,
        )

    async def _shutdown(self):
        log.info("Shutting down...")
        # Close open positions
        if self._positions:
            async with self._pool.acquire() as conn:
                for iid in list(self._positions):
                    row = await conn.fetchrow(
                        "SELECT mid_price FROM book_tob WHERE instrument_id=$1 "
                        "ORDER BY exchange_ts DESC LIMIT 1", iid)
                    if row:
                        await self._close_position(
                            iid, float(row["mid_price"]),
                            datetime.now(timezone.utc), "shutdown")

        await self._pool.execute(
            "UPDATE paper_state SET bot_running=false, updated_at=now()")

        log.info("=" * 50)
        log.info("FINAL: %d trades | gross %+.1f bps | net %+.1f bps | win %.0f%%",
                 self._trade_count, self._total_gross, self._total_net,
                 (self._wins / self._trade_count * 100) if self._trade_count > 0 else 0)

        if self._pool:
            await self._pool.close()


def main():
    bot = PaperBot()
    loop = asyncio.new_event_loop()

    def handle_sig(sig, frame):
        bot._running = False

    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)

    try:
        loop.run_until_complete(bot.start())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
