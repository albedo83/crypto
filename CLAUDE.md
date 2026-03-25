# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Crypto trading research + automated bot for Hyperliquid DEX (decentralized exchange accessible from France). Two generations of work:

**Current active system:**
- **Multi-Signal Bot** (`analysis/reversal.py`): 5 validated strategies on 28 altcoins, 2x leverage, paper trading on Hyperliquid. Dashboard on `:8097`. Version in `VERSION` constant (currently 10.1.0).

**Legacy systems (disabled):**
- **LiveBot** (`analysis/livebot.py` v5.6.0): OI divergence on Binance Futures, 17 symbols, `:8095`. Stopped — Binance Futures banned in France.
- **CarryBot** (`analysis/carrybot.py`): Funding carry on Binance, `:8096`. Stopped.
- **Collector** (`src/collector/`): Binance WS → TimescaleDB. Systemd service disabled.
- **Dashboard** (`src/dashboard/`): FastAPI + htmx for collector. Systemd service disabled.
- **PostgreSQL**: Idle, historical data from March 15-22 2026.

## Commands

```bash
# Multi-Signal Bot (the one running)
nohup .venv/bin/python3 -m analysis.reversal > analysis/output/reversal_v10.log 2>&1 &
# Dashboard: http://0.0.0.0:8097
# Stop: fuser -k 8097/tcp
# Logs: tail -f analysis/output/reversal_v10.log
# Trades: analysis/output/reversal_trades.csv
# State: analysis/output/reversal_state.json

# Legacy LiveBot (disabled)
# nohup .venv/bin/python3 -m analysis.livebot > analysis/output/livebot.log 2>&1 &
# Dashboard: http://0.0.0.0:8095 — Stop: fuser -k 8095/tcp
```

No test framework, linter, or CI pipeline is configured.

## Multi-Signal Bot Architecture

```
Hyperliquid REST API
    ├── metaAndAssetCtxs (prices, every 60s)
    ├── candleSnapshot (4h candles, every hour, 30 symbols)
    └── Yahoo Finance (DXY, every 6h, cached)
            │
            ▼
    analysis/reversal.py  (single asyncio process)
    ├── Features (24 indicators per token per candle)
    ├── 5 signals (S1, S2, S4, S5, S8)
    ├── Position manager (max 6, stop -25%, 60-72h timeout)
    ├── State persistence (JSON atomic writes + CSV trades)
    └── Dashboard (FastAPI on :8097, live counters)
```

### Symbols (28 traded + 2 reference)

**Traded:** ARB, OP, AVAX, SUI, APT, SEI, NEAR, AAVE, MKR, COMP, SNX, PENDLE, DYDX, DOGE, WLD, BLUR, LINK, PYTH, SOL, INJ, CRV, LDO, STX, GMX, IMX, SAND, GALA, MINA

**Reference:** BTC, ETH (for BTC lead-lag features, not traded)

### Sectors (for S5 signal)

| Sector | Tokens |
|--------|--------|
| L1 | SOL, AVAX, SUI, APT, NEAR, SEI |
| DeFi | AAVE, MKR, CRV, SNX, PENDLE, COMP, DYDX, LDO, GMX |
| Gaming | GALA, IMX, SAND |
| Infra | LINK, PYTH, STX, INJ, ARB, OP |
| Meme | DOGE, WLD, BLUR, MINA |

### Strategies

| Signal | Condition | Action | z-score | Hold | Size ($1k) |
|--------|-----------|--------|---------|------|------------|
| S1 | BTC 30d > +20% | LONG alts | 6.42 | 72h | $241 |
| S2 | Alt index 7d < -10% | LONG | 4.00 | 72h | $150 |
| S4 | Vol contraction + DXY rising > +1% | SHORT | 2.95 | 72h | $111 |
| S5 | Sector divergence > 10% + vol z > 1.0 | FOLLOW | 3.67 | 48h | $138 |
| S8 | Drawdown < -40% + vol spike + BTC weak | LONG | 6.99 | 60h | $262 |

All 5 survived train/test split + Monte Carlo validation. 1500+ rules tested, only these 5 pass.

### Config

- **Leverage**: 2x (optimal from parameter sweep — 3x = ruin from compounding losses)
- **Sizing**: 15% of current capital, z-weighted (stronger signal = bigger position)
- **Compounding**: Yes (capital grows/shrinks with P&L)
- **Stop loss**: -25% catastrophe guard (leveraged)
- **Max positions**: 6 (max 4 same direction)
- **Capital exposure**: max 90%
- **Costs**: 12 bps (7 taker + 3 slippage + 2 funding) × leverage
- **Cooldown**: 24h per symbol after exit
- **Scan interval**: Every hour (candles are 4h)

### API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Dashboard HTML (cached on startup) |
| `GET /api/state` | Balance, positions, signals, timing counters |
| `GET /api/signals` | All 28 tokens with features and triggered signals |
| `GET /api/trades` | Trade history (deque maxlen=500) |
| `GET /api/pnl` | Cumulative P&L curve |
| `POST /api/pause` | Close all positions + pause |
| `POST /api/resume` | Resume trading (forces immediate scan) |
| `POST /api/reset` | Close all + reset capital to $1000 |

### Backtest Results

$1,000 → $11,214 over 35 months (2023-08 to 2026-03). DD -54%. 57% months winning.

## Research Files

All in `analysis/`. The backtest files document the exhaustive search that led to the 5 signals:

| File | What it tested | Result |
|------|---------------|--------|
| `backtest_genetic.py` | Exhaustive scan 700+ rules + genetic algo | Found S1, S2, S4 |
| `backtest_sector.py` | Sector divergence (fade vs follow) | Found S5 (follow works) |
| `backtest_newcombos.py` | Multi-condition combos (S7-S10 + shorts) | Found S8 (capitulation) |
| `backtest_deep_s8.py` | Deep S8 threshold sweep + 4th condition | S8 + btc_7d < -300 best (z=6.99) |
| `backtest_short_search.py` | 8 SHORT signal ideas, 378 variants | No SHORT passes z>2.0 |
| `backtest_regime.py` | Regime gating (bull/bear filter on S1-S4) | Regime gating hurts all signals |
| `backtest_optimize.py` | Stop loss, trailing, signal exit, z-sizing | No stop is best, z-weight helps |
| `backtest_boost.py` | Leverage, sizing, hold, max positions sweep | 2x optimal |
| `backtest_gp.py` | Genetic programming (expression trees) | All overfit |
| `backtest_explore2*.py` | Cross-sectional, calendar, momentum, ML | Nothing new |
| `backtest_explore3.py` | Liquidation cascades, macro, candle offsets | Nothing survives |
| `backtest_smart.py` | Smart priority (scored, reservation, replacement) | No improvement |
| `backtest_1h.py` | 1h candle resolution | No new discoveries |
| `backtest_v920.py` | Full portfolio backtest all signals combined | Final validation |
| `backtest_genetic_final.py` | Combined portfolio with compounding | Final numbers |

Full research journal: `docs/research_findings.md`
Bot documentation (French): `docs/bot.md`

## Legacy LiveBot Architecture

```
Binance Futures WS + REST → analysis/livebot.py (:8095)
├── 4 signals: OI divergence + smart money + funding proximity + BTC lead-lag
├── 17 symbols, sessions Asia/US/Overnight
├── $1000 virtual, max 4 positions, 2h timeout, -100bps stop
└── CSV: livebot_trades.csv, livebot_signals.csv
```

Legacy collector/dashboard/migrations in `src/` and `migrations/`. PostgreSQL schema: 9 hypertables, 4 matviews, 7 migrations.

## Gotchas

- **DXY filter is critical for S4**: S4 SHORT only active when DXY 7d > +100 bps. Without it, S4 shorts in bull markets and loses. Yahoo Finance API can fail silently — check logs for "DXY unavailable" warnings.
- **DXY cache**: Stored in `analysis/output/pairs_data/macro_DXY.json`, refreshed every 6h. Cache uses 5-trading-day return (`closes[-6]`).
- **Feature cache**: `_refresh_feature_cache()` runs once per hourly scan. `_scan_signals()` and `get_signals()` use cached features. Cache is empty on startup until first scan completes.
- **State persistence**: Atomic writes (write to `.tmp` then `os.replace()`). On load, original is preserved (copy to `.loaded`). Positions survive restarts.
- **Compounding effect**: `current_capital = CAPITAL_USDT + _total_pnl`. After big losses, position sizes shrink dramatically. After big wins, positions grow and DD risk increases.
- **HTML cache**: Dashboard HTML is cached in memory on first request. Restart bot to pick up HTML changes.
- **Trades deque**: `self.trades` is `deque(maxlen=500)`. Use `list(self.trades)` before slicing (deque doesn't support slicing).
- **Versioning**: `VERSION` constant in `analysis/reversal.py`. Increment on every change (semver). Displayed in dashboard header and `/api/state`.
- **Two bot generations**: LiveBot (Binance, disabled) and Multi-Signal Bot (Hyperliquid, active) are independent. Don't confuse ports (:8095 vs :8097) or trade files.
- **S6 was removed**: Liquidation bounce signal had z=8.04 in isolation but loses -$627 to -$1,552 in portfolio. Standalone backtest was misleading (simpler backtester, no position limits).
- **S8 capitulation is rare**: Fires ~1/month in portfolio (drawdown > -40% is extreme). When it fires, 70% win rate with avg +413 bps. Max 7 consecutive losses observed (April 2024 crash).
- **SHORT signals are hard**: 378 short variants tested, none pass z > 2.0. S4+DXY remains the only viable short. Altcoin markets have structural long bias.
