"""Trade-flow aggregation — Hyperliquid `trades` WS messages bucketed per 60s.

Taken from analysis/bot/collector.py, minus the connection management: the
MarketDataMaster owns the single WS connection and feeds `ingest()` here.
Writes to the shared market DB (alfred/db.py Database).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass

from .db import Database

log = logging.getLogger("alfred")

BUCKET_SECONDS = 60
FLUSH_INTERVAL = 10


@dataclass
class Bucket:
    buy_vol: float = 0.0      # notional USD
    sell_vol: float = 0.0     # notional USD
    buy_count: int = 0
    sell_count: int = 0
    max_trade_usd: float = 0.0  # largest single trade (notional)
    sum_px_sz: float = 0.0    # for VWAP
    sum_sz: float = 0.0       # for VWAP


def _bucket_ts(epoch_s: float) -> int:
    return int(epoch_s) // BUCKET_SECONDS * BUCKET_SECONDS


class TradeFlowAggregator:
    """Aggregates trade messages into 60-second buckets, flushed to SQLite."""

    def __init__(self, db: Database, symbols):
        self._db = db
        self._symbols = set(symbols)
        self._buckets: dict[tuple[int, str], Bucket] = defaultdict(Bucket)
        self._last_flush = time.time()
        # v1.15.0 : le flush tourne désormais dans un worker thread (les
        # commits SQLite sur l'event loop gelaient l'ingestion WS quand
        # db.lock était tenu) — _buckets est partagé loop/thread.
        self._lock = threading.Lock()

    def ingest(self, trades: list[dict]) -> bool:
        """Buffer les trades. Retourne True quand un flush est dû — l'appelant
        exécute flush_completed HORS event loop (asyncio.to_thread)."""
        with self._lock:
            for t in trades:
                coin = t.get("coin", "")
                if coin not in self._symbols:
                    continue
                ts = t["time"] / 1000.0
                key = (_bucket_ts(ts), coin)
                b = self._buckets[key]
                sz = float(t["sz"])
                px = float(t["px"])
                notional = sz * px

                if t["side"] == "B":
                    b.buy_vol += notional
                    b.buy_count += 1
                else:
                    b.sell_vol += notional
                    b.sell_count += 1
                if notional > b.max_trade_usd:
                    b.max_trade_usd = notional
                b.sum_px_sz += px * sz
                b.sum_sz += sz

        now = time.time()
        if now - self._last_flush >= FLUSH_INTERVAL:
            self._last_flush = now
            return True
        return False

    def flush_completed(self, now: float) -> None:
        with self._lock:
            cutoff = _bucket_ts(now)
            completed = [(k, b) for k, b in self._buckets.items() if k[0] < cutoff]
            if not completed:
                return
            rows = []
            for (ts, sym), b in completed:
                vwap = round(b.sum_px_sz / b.sum_sz, 6) if b.sum_sz > 0 else 0
                rows.append((ts, sym, round(b.buy_vol, 2), round(b.sell_vol, 2),
                             b.buy_count, b.sell_count, round(b.max_trade_usd, 2), vwap))
                del self._buckets[(ts, sym)]
        # write hors lock : la DB a son propre lock, et garder _lock pendant
        # un commit re-créerait le blocage qu'on vient de retirer.
        self._db.write(
            "INSERT OR IGNORE INTO trade_flow VALUES (?,?,?,?,?,?,?,?)", rows)

    def flush_all(self) -> None:
        """Force-flush all buckets (shutdown)."""
        self.flush_completed(time.time() + BUCKET_SECONDS * 2)
