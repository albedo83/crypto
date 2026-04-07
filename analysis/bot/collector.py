"""WebSocket trade flow collector — aggregates Hyperliquid trades into 60s buckets."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field

import orjson
import websockets

from .config import ALL_SYMBOLS

log = logging.getLogger("multisignal")

WS_URL = "wss://api.hyperliquid.xyz/ws"
BUCKET_SECONDS = 60
FLUSH_INTERVAL = 10
RECONNECT_DELAYS = [1, 2, 4, 8, 15, 30, 30]


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


class TradeFlowCollector:
    """Connects to Hyperliquid WebSocket, subscribes to trades for all symbols,
    aggregates into 60-second buckets, and flushes to SQLite."""

    def __init__(self, db):
        self._db = db
        self._symbols = set(ALL_SYMBOLS)
        self._buckets: dict[tuple[int, str], Bucket] = defaultdict(Bucket)

    async def run(self):
        """Main loop — connect, collect, reconnect forever."""
        attempt = 0
        while True:
            try:
                await self._connect_and_collect()
                attempt = 0
            except asyncio.CancelledError:
                self._flush_all()
                log.info("Trade flow collector stopped")
                return
            except Exception as e:
                delay = RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
                log.warning("WS trade collector: %s — reconnecting in %ds", e, delay)
                await asyncio.sleep(delay)
                attempt += 1

    async def _connect_and_collect(self):
        async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=10,
                                      max_size=2**22) as ws:
            log.info("Trade flow collector connected (%d symbols)", len(self._symbols))
            for sym in self._symbols:
                await ws.send(orjson.dumps({
                    "method": "subscribe",
                    "subscription": {"type": "trades", "coin": sym}
                }).decode())

            last_flush = time.time()
            async for raw in ws:
                msg = orjson.loads(raw)
                if msg.get("channel") == "trades":
                    self._ingest(msg["data"])

                now = time.time()
                if now - last_flush >= FLUSH_INTERVAL:
                    self._flush_completed(now)
                    last_flush = now

    def _ingest(self, trades: list[dict]):
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

    def _flush_completed(self, now: float):
        cutoff = _bucket_ts(now)
        completed = [(k, b) for k, b in self._buckets.items() if k[0] < cutoff]
        if not completed or not self._db:
            return
        rows = []
        for (ts, sym), b in completed:
            vwap = round(b.sum_px_sz / b.sum_sz, 6) if b.sum_sz > 0 else 0
            rows.append((ts, sym, round(b.buy_vol, 2), round(b.sell_vol, 2),
                         b.buy_count, b.sell_count, round(b.max_trade_usd, 2), vwap))
            del self._buckets[(ts, sym)]
        try:
            self._db.executemany(
                "INSERT OR IGNORE INTO trade_flow VALUES (?,?,?,?,?,?,?,?)", rows)
            self._db.commit()
        except Exception as e:
            log.warning("Trade flow flush: %s", e)

    def _flush_all(self):
        """Force-flush all buckets (shutdown)."""
        self._flush_completed(time.time() + BUCKET_SECONDS * 2)
