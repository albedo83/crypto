"""Execution brokers — one per BotInstance.

PaperBroker simulates fills in memory. Phase-3 defaults reproduce the legacy
paper engine exactly (fill at mark, no slippage, flat funding); the phase-7
corrections (paper_slippage_bps, paper_gap_fills, paper_funding_model) are
carried by Params and shipped OFF.

LiveBroker (phase 4) wraps hl.HLAccount — one HL account per bot. Contract
difference vs PaperBroker: open()/close() RAISE on exchange failure (the
BotInstance catches, logs EXEC_*_FAILED, and keeps/skips the position).

Shared contract :
    open(symbol, direction, size_usdt, mark)            -> Fill
    close(symbol, direction, trigger_price, mark)       -> Fill
    trade_funding_usdt(symbol, direction, size_usdt,
                       accrued, entry_ms=0, exit_ms=0)   -> float
"""

from __future__ import annotations

import logging

from .settings import Params

log = logging.getLogger("alfred")


class Fill:
    """Execution result. Field semantics differ by side :
    - open  : size_usdt = filled NOTIONAL in USD (sz × avgPx)
    - close : size_usdt = closed COIN quantity (0.0 = unknown/not reported,
              caller skips the partial-fill reconcile)."""
    __slots__ = ("avg_px", "size_usdt")

    def __init__(self, avg_px: float, size_usdt: float):
        self.avg_px = avg_px
        self.size_usdt = size_usdt


class PaperBroker:
    """In-memory execution. Stateless besides Params."""

    is_live = False

    def __init__(self, p: Params):
        self.p = p

    def open(self, symbol: str, direction: int, size_usdt: float,
             mark: float) -> Fill:
        """Simulated entry fill at mark, optionally direction-adverse slippage."""
        px = mark
        slip = self.p.paper_slippage_bps
        if slip > 0:
            px = mark * (1 + direction * slip / 2 / 1e4)  # half RT per leg
        return Fill(px, size_usdt)

    def close(self, symbol: str, direction: int, trigger_price: float,
              mark: float) -> Fill:
        """Simulated exit fill.

        `trigger_price` is the synthetic price of the rule that fired (or the
        mark for mark-priced exits). Legacy semantics: book the trigger.
        paper_gap_fills=True books the worse of (trigger, mark) — models the
        gap between two polling ticks (phase-7 correction, default OFF).
        """
        px = trigger_price
        if self.p.paper_gap_fills:
            px = min(trigger_price, mark) if direction == 1 else max(trigger_price, mark)
        slip = self.p.paper_slippage_bps
        if slip > 0:
            px = px * (1 - direction * slip / 2 / 1e4)
        return Fill(px, 0.0)

    def trade_funding_usdt(self, symbol: str, direction: int,
                           size_usdt: float, accrued: float,
                           entry_ms: int = 0, exit_ms: int = 0) -> float:
        """Funding adjustment vs the flat FUNDING_DRAG_BPS baked into cost_bps.

        flat model → 0 (the flat estimate stands, legacy behavior).
        accrual model → `accrued` (maintained hourly by the BotInstance)
        minus the flat estimate, mirroring the live swap formula.
        """
        if self.p.paper_funding_model != "accrual":
            return 0.0
        flat = -size_usdt * self.p.funding_drag_bps / 1e4
        return accrued - flat


class LiveBroker:
    """Hyperliquid execution via hl.HLAccount. open()/close() raise on
    exchange failure — callers handle (skip entry / keep position + retry)."""

    is_live = True

    def __init__(self, p: Params, account):
        self.p = p
        self.account = account            # hl.HLAccount

    def open(self, symbol: str, direction: int, size_usdt: float,
             mark: float) -> Fill:
        r = self.account.open_market(symbol, direction == 1, size_usdt, mark)
        return Fill(r["avgPx"], r["sz"] * r["avgPx"])

    def close(self, symbol: str, direction: int, trigger_price: float,
              mark: float) -> Fill:
        """Books the REAL exchange fill. Falls back to mark when HL returns
        no fill info (close succeeded but response lacked avgPx)."""
        r = self.account.close_market(symbol)
        if r is None:
            return Fill(mark if mark > 0 else trigger_price, 0.0)
        return Fill(r["avgPx"], r["sz"])

    def trade_funding_usdt(self, symbol: str, direction: int,
                           size_usdt: float, accrued: float,
                           entry_ms: int = 0, exit_ms: int = 0) -> float:
        """Real funding swap (legacy v11.7.5): replace the flat
        FUNDING_DRAG_BPS estimate baked into cost_bps with the exact
        user_funding_history sum over the trade window.

        Returns `real - flat` so the caller's
        `pnl = size × net/1e4 + adjustment` lands on
        `size × (gross − cost)/1e4 + real_funding + size × drag/1e4`.
        Fail-open: HL error → real=0 → adjustment cancels only the flat part.
        """
        real = 0.0
        if entry_ms and exit_ms:
            real = self.account.position_funding(symbol, entry_ms, exit_ms)
        flat = -size_usdt * self.p.funding_drag_bps / 1e4
        return real - flat
