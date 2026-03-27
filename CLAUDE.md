# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Crypto trading bot for Hyperliquid DEX (accessible from France). Paper/live trading on 28 altcoins.

**The bot is 2 files** : `analysis/reversal.py` (~1600 lines) + `analysis/reversal.html`. Everything else is research/backtests.

Version in `VERSION` constant (currently 10.4.1). Dashboard on `:8097`.

### Execution Modes

- **Paper** (`HL_MODE=paper`, default): simulates positions in memory, reads prices from Hyperliquid public API
- **Live** (`HL_MODE=live`): places real orders via `hyperliquid-python-sdk`, reconciles with exchange every scan

Config in `.env` (gitignored): `HL_MODE`, `HL_PRIVATE_KEY`, `TG_BOT_TOKEN`, `TG_CHAT_ID`, `DASHBOARD_USER`, `DASHBOARD_PASS`.

## Commands

```bash
# Paper bot (:8097, $1000 simulated)
nohup .venv/bin/python3 -m analysis.reversal > analysis/output/reversal_v10.log 2>&1 &

# Live bot (:8098, $100 real)
HL_MODE=live HL_CAPITAL=100 WEB_PORT=8098 HL_OUTPUT_DIR=analysis/output_live \
  nohup .venv/bin/python3 -m analysis.reversal > analysis/output_live/reversal_v10.log 2>&1 &

# Both restart automatically on VPS reboot via crontab (@reboot /home/crypto/start_bots.sh)

# Stop: fuser -k 8097/tcp (paper) or fuser -k 8098/tcp (live)
# Logs: tail -f analysis/output/reversal_v10.log (paper)
#        tail -f analysis/output_live/reversal_v10.log (live)
# Dashboard: http://0.0.0.0:8097 (paper) / http://0.0.0.0:8098 (live) — auth required
```

No test framework, linter, or CI pipeline is configured.

## Bot Architecture

```
Hyperliquid REST API (read)
    ├── metaAndAssetCtxs (prices + OI + funding + premium, every 60s)
    ├── candleSnapshot (4h candles, every hour, 30 symbols)
    └── Yahoo Finance (DXY, every 6h, cached 48h)
            │
            ▼
    analysis/reversal.py  (single asyncio process)
    ├── Features (24 calculated per token, 13 used in production)
    ├── 5 signals (S1, S2, S4, S5, S8)
    ├── Crowding engine (OI + funding + premium → score 0-100)
    ├── Position manager (max 6/4dir/2sect, stop -25%/-15%, 48-72h timeout)
    ├── Signal quarantine (win rate < 20% → auto-disable)
    ├── Execution (paper or live via hyperliquid-python-sdk)
    ├── Reconciliation (bot state vs exchange, every scan)
    ├── Telegram alerts (entry/exit/error/kill-switch)
    ├── State persistence (JSON atomic writes + CSV trades + CSV market + CSV trajectories)
    ├── 12 observation dimensions per trade (OI, crowding, stress, disp, shock, clean, lead, conf, session, age, retest)
    └── Dashboard (FastAPI on :8097, live counters)
            │
            ▼ (live mode only)
Hyperliquid SDK (write)
    ├── market_open / market_close (taker orders)
    ├── update_leverage (2x cross on all symbols)
    └── user_state (reconciliation)
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

| Signal | Condition | Action | z-score | Hold | Size ($100) |
|--------|-----------|--------|---------|------|-------------|
| S1 | BTC 30d > +20% | LONG alts | 6.42 | 72h | $24 |
| S2 | Alt index 7d < -10% | LONG | 4.00 | 72h | $15 |
| S4 | Vol contraction + DXY rising > +1% | SHORT | 2.95 | 72h | $11 |
| S5 | Sector divergence > 10% + vol z > 1.0 | FOLLOW | 3.67 | 48h | $14 |
| S8 | Drawdown < -40% + vol spike + BTC weak | LONG | 6.99 | 60h | $26 |

All 5 survived train/test split + Monte Carlo + portfolio integration + walk-forward validation.

### Config

- **Leverage**: 2x (optimal from parameter sweep — 3x = ruin from compounding losses)
- **Sizing**: 12% base + 3% bonus (z>4), z-weighted, haircut S8 ×0.8 (stronger signal = bigger position)
- **Compounding**: Yes (capital grows/shrinks with P&L)
- **Stop loss**: -25% catastrophe guard (S1/S2/S4/S5), -15% for S8 (matches backtest)
- **Max positions**: 6 (max 4 same direction, max 2 per sector)
- **Capital exposure**: max 90%
- **Costs**: 12 bps (7 taker + 3 slippage + 2 funding) × leverage
- **Cooldown**: 24h per symbol after exit
- **Scan interval**: Every hour (candles are 4h)

### API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Dashboard HTML (cached on startup) |
| `GET /api/state` | Balance, positions, signals, timing, drift, degraded, OI summary |
| `GET /api/signals` | All 28 tokens with features, OI, crowding, triggered signals |
| `GET /api/trades` | Trade history (deque maxlen=500) |
| `GET /api/pnl` | Cumulative P&L curve |
| `POST /api/pause` | Close all positions + pause |
| `POST /api/resume` | Resume trading (forces immediate scan) |
| `POST /api/reset` | Close all + reset all state to zero |

### Backtest Results

$1,000 → $11,214 over 32 months (2023-08 to 2026-03). DD -54%. 63% months winning.

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
| `backtest_robustness.py` | Walk-forward rolling + leave-N-tokens-out | Confirms all signals |

Bot documentation (French): `docs/bot.md`

## Gotchas

- **DXY filter is critical for S4**: S4 SHORT only active when DXY 7d > +100 bps. Without it, S4 shorts in bull markets and loses. DXY has 3-tier fallback: fresh < 6h, stale 6-48h (S4 stays active), expired > 48h (S4 disabled + degraded banner).
- **DXY cache**: Stored in `analysis/output/pairs_data/macro_DXY.json`. Cache uses 5-trading-day return (`closes[-6]`).
- **Feature cache**: `_refresh_feature_cache()` runs once per hourly scan. `_scan_signals()` and `get_signals()` use cached features. Cache is empty on startup until first scan completes.
- **State persistence**: Atomic writes (write to `.tmp` then `os.replace()`). On load, original is preserved (copy to `.loaded`). Positions, paused state, loss streak, MAE/MFE, and trajectories survive restarts.
- **Compounding effect**: `current_capital = CAPITAL_USDT + _total_pnl`. After big losses, position sizes shrink dramatically. After big wins, positions grow and DD risk increases.
- **HTML cache**: Dashboard HTML is cached in memory on first request. Restart bot to pick up HTML changes.
- **Trades deque**: `self.trades` is `deque(maxlen=500)`. Use `list(self.trades)` before slicing (deque doesn't support slicing).
- **Versioning**: `VERSION` constant in `analysis/reversal.py`. **ALWAYS bump VERSION when modifying reversal.py** — patch for bugfixes, minor for features, major for breaking changes. Update the version in the docstring (line 1), `bot.md` title, and `CLAUDE.md` (this file) at the same time. The version displayed on the dashboard is the user's proof that the correct code is running.
- **S6 was removed**: Liquidation bounce signal had z=8.04 in isolation but loses -$627 to -$1,552 in portfolio. Standalone backtest was misleading (simpler backtester, no position limits).
- **S8 capitulation is rare**: Fires ~1/month in portfolio (drawdown > -40% is extreme). When it fires, 70% win rate with avg +413 bps. Max 7 consecutive losses observed (April 2024 crash).
- **SHORT signals are hard**: 378 short variants tested, none pass z > 2.0. S4+DXY remains the only viable short. Altcoin markets have structural long bias.
- **OI/funding/premium collected but not used for signals**: Observation phase. Data logged in `reversal_market.csv` (hourly snapshots, ~15 MB/year) and in each trade's `signal_info`. Will be analyzed after 50+ trades to determine if OI delta improves S2/S8 quality.
- **Crowding score (0-100)**: Measures leverage stress per token (OI delta + funding + premium + vol_z). Displayed in dashboard, logged in trades. Not used for decisions yet.
- **Signal quarantine**: If a signal's rolling win rate drops below 20% on last 10 trades, it's auto-disabled. Below 30% → sizing halved. Prevents silent degradation.
- **Signal drift**: `/api/state` exposes `signal_drift` with rolling stats per signal (win rate, avg bps, P&L on last 20 trades).
- **Execution mode**: `HL_MODE` in `.env`. Default `paper` simulates in memory. `live` places real orders via SDK. Mode shown in startup log and `/api/state`. **Never push .env to git**.
- **SDK lazy import**: `eth_account` + `hyperliquid` only imported when `HL_MODE=live`. Paper mode has zero SDK dependency.
- **Reconciliation**: Every hourly scan, bot compares its position dict vs `user_state()` from exchange. Mismatches (orphans/ghosts) trigger Telegram alert but NO auto-fix.
- **Telegram**: Uses raw urllib POST (no dependency). Fire-and-forget, never blocks the bot. Events: open, close, error, kill-switch, startup, reconciliation mismatch.
- **Order sizing**: SDK expects size in coin units, not USDT. Conversion: `sz = size_usdt / price`, rounded to `szDecimals` from exchange metadata.
- **Dashboard auth**: HTTP Basic Auth via `DASHBOARD_USER`/`DASHBOARD_PASS` in `.env`. Empty = no auth. Uses `secrets.compare_digest` (timing-safe).
- **Auto-restart**: `@reboot` crontab runs `start_bots.sh` which starts both instances + sends Telegram alert on VPS reboot.
- **Dual instances**: Paper (:8097, `analysis/output/`) and Live (:8098, `analysis/output_live/`) run in parallel from the same code. Only DXY cache is shared (global market data).
- **Detailed bot documentation**: `docs/bot.md` contains the full bot description — signals, parameters, research, estimates, risks, architecture, production plan. Keep it in sync with code changes.
