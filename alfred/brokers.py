"""Execution brokers — one per BotInstance.

PaperBroker simulates fills in memory. Phase-3 defaults reproduce the legacy
paper engine exactly (fill at mark, no slippage, flat funding); the phase-7
corrections (paper_slippage_bps, paper_gap_fills, paper_funding_model) are
carried by Params and shipped OFF.

LiveBroker (Hyperliquid SDK) lands in phase 4 with the live migration.
"""

from __future__ import annotations

import logging

from .settings import Params

log = logging.getLogger("alfred")


class Fill:
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
                           size_usdt: float, accrued: float) -> float:
        """Funding adjustment vs the flat FUNDING_DRAG_BPS baked into cost_bps.

        flat model → 0 (the flat estimate stands, legacy behavior).
        accrual model → `accrued` (maintained hourly by the BotInstance)
        minus the flat estimate, mirroring the live swap formula.
        """
        if self.p.paper_funding_model != "accrual":
            return 0.0
        flat = -size_usdt * self.p.funding_drag_bps / 1e4
        return accrued - flat
