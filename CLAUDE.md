# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Crypto trading bot for Hyperliquid DEX (accessible from France). Paper/live trading on 28 altcoins.

**The bot is 12 modules** in `analysis/bot/` + `analysis/reversal.html` (dashboard). `analysis/reversal.py` is a 6-line backward-compat shim. Backtests are in `backtests/`.

Version in `analysis/bot/config.py` `VERSION` constant (currently 11.1.0). Dashboard on `:8097`.

### Execution Modes

- **Paper** (`HL_MODE=paper`, default): simulates positions in memory, reads prices from Hyperliquid public API
- **Live** (`HL_MODE=live`): places real orders via `hyperliquid-python-sdk`, reconciles with exchange every scan

Config in `.env` (gitignored): `HL_MODE`, `HL_PRIVATE_KEY`, `TG_BOT_TOKEN`, `TG_CHAT_ID`, `DASHBOARD_USER`, `DASHBOARD_PASS`.

## Commands

```bash
# Paper bot (:8097, $1000 simulated)
nohup .venv/bin/python3 -m analysis.reversal > analysis/output/reversal_v10.log 2>&1 &

# Live bot (:8098, $260 real)
HL_MODE=live HL_CAPITAL=260 WEB_PORT=8098 HL_OUTPUT_DIR=analysis/output_live \
  nohup .venv/bin/python3 -m analysis.reversal > analysis/output_live/reversal_v10.log 2>&1 &

# Both restart automatically on VPS reboot via crontab (@reboot $PROJECT_DIR/start_bots.sh)

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
    analysis/bot/  (12 modules, single asyncio process)
    ├── config.py      — constants, env, sizing
    ├── models.py      — SymbolState, Position, Trade dataclasses
    ├── net.py         — HTTP retry, price/candle fetching, Telegram
    ├── features.py    — technical features, OI, crowding, BTC, DXY, sector
    ├── signals.py     — S1/S5/S8/S9/S10 detection, squeeze, S9F observation
    ├── db.py          — SQLite schema, tick/event logging, CSV migration
    ├── persistence.py — CSV/DB writes, state save/load, market snapshots
    ├── exchange.py    — Hyperliquid SDK (open/close/reconcile)
    ├── trading.py     — entries (ranking/limits), exits (stop/timeout), P&L
    ├── web.py         — FastAPI dashboard + API responses
    ├── bot.py         — MultiSignalBot class (thin orchestrator)
    └── main.py        — entry point, signal handlers, uvicorn
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
| ~~S2~~ | ~~Alt index 7d < -10%~~ | **REMOVED** — loses in portfolio, slots better used by S8/S9 | — | — | — |
| ~~S4~~ | ~~Vol contraction + DXY rising~~ | **SUSPENDED** — only 2 trades in 32 months, -$124. Code kept commented. | — | — | — |
| S5 | Sector divergence > 10% + vol z > 1.0 | FOLLOW | 3.67 | 48h | $14 |
| S8 | Drawdown < -40% + vol spike + BTC weak | LONG | 6.99 | 60h | $26 |
| S9 | Token move > ±20% in 24h | FADE | 8.71 | 48h | $30 |
| S10 | Squeeze + false breakout | FADE breakout | 3.66 | 24h | $11 |

All 5 active signals survived train/test split + Monte Carlo validation. S2 removed (loses in portfolio). S4 suspended (2 trades in 32 months).

### Config

- **Leverage**: 2x (optimal from parameter sweep — 3x = ruin from compounding losses)
- **Sizing**: 12% base + 3% bonus (z>4), z-weighted, haircut S8 ×0.8, per-signal mult (S1×1.125, S5×1.50, S8×1.25, S9×1.35, S10×1.10)
- **Compounding**: Yes (capital grows/shrinks with P&L)
- **Stop loss**: -25% catastrophe guard (S1/S5), -15% for S8, adaptive for S9 (tighter on bigger moves)
- **Max positions**: 6 (max 4 same direction, max 2 per sector)
- **Capital exposure**: max 90%
- **Costs**: 12 bps (7 taker + 3 slippage + 2 funding) × leverage
- **Cooldown**: 24h per symbol after exit
- **Scan interval**: Every hour (candles are 4h)

### API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Dashboard HTML (cached on startup) |
| `GET /api/health` | Bot health: status (ok/degraded/stale), price_age, scan_age, exchange_ok. Returns 503 if stale. |
| `GET /api/state` | Balance, positions, signals, timing, drift, degraded, OI summary, drawdown, utilization |
| `GET /api/signals` | All 28 tokens with features, OI, crowding, triggered signals |
| `GET /api/trades` | Trade history (deque maxlen=500) |
| `GET /api/pnl` | Cumulative P&L curve |
| `POST /api/pause` | Close all positions + pause (sync handler, runs in threadpool) |
| `POST /api/resume` | Resume trading (forces immediate scan) |
| `POST /api/reset` | Close all + reset all state to zero (sync handler, runs in threadpool) |

### Backtest Results

$1,000 → $11,214 over 32 months (2023-08 to 2026-03). DD -54%. 63% months winning.

## Research Files

All in `analysis/`. The backtest files document the exhaustive search that led to the 6 signals:

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
| `backtest_pairs.py` | Intra-sector pairs trading (market-neutral) | Mean reversion doesn't work, even hedged |
| `backtest_funding.py` | Funding carry (short high-funding tokens) | Edge too small, costs eat it |
| `backtest_premium.py` | Premium mean reversion (perp vs spot) | z=1.41, doesn't pass MC |
| `backtest_sessions.py` | Intra-day session effects (Asia/EU/US) | No systematic bias |
| `backtest_decorr.py` | BTC/alts correlation breakdown | z=2.37 (weak, +8 bps/trade) |
| `backtest_wild.py` | 6 unconventional strategies (weekend, fade, disp, vol, momentum, Monday) | **Found S9** (fade extreme, z=8.71) |
| `backtest_squeeze.py` | Squeeze + false breakout expansion (Mode A/B) | **Found S10** (Mode B fade, z=3.66) |
| `backtest_squeeze_validation.py` | S10 deep validation: 5 checks (concentration, temporal, costs, params, uniqueness) | All 5 pass |
| `backtest_slot_reservation.py` | Slot reservation: macro vs token signal allocation | **Macro 2 / Token 3** optimal (DD -32% vs -44%) |
| `backtest_signal_boost.py` | 5 targeted improvements: S2 BTC filter, S9 threshold, S10 window, S2 early exit, S5 boost | **S2 early exit at -200bps** best (+87% P&L) |
| `backtest_signal_boost2.py` | 6 advanced tests: adaptive hold, token picking, S4 vol_z, combo S2+S8, S9 adaptive stop, immediate entry | **S9 adaptive stop** best (+54% S9 P&L), S2 removed |
| `backtest_short_search2.py` | 6 new SHORT ideas on 4h data (BTC neg, fade pump, overextension, sector fade, exhaustion, contagion) | **Nothing passes z>2.0** — structural long bias confirmed |
| `backtest_1h_fast.py` | Fast signals on 1h candles: S9-fast, micro-squeeze, volume spike, momentum | **S9-fast (fade ±3% in 2h)** promising: 588t, +88bps, train+test ✓ |
| `backtest_1h_fast2.py` | 6 more 1h patterns: BTC lead-lag, consecutive, 24h breakout, cross-alt, vol contraction, multi-TF | Nothing passes train+test |

Bot documentation (French): `docs/bot.md`

### SQLite Tick Database (v11.1.0)

Every 60s, the bot writes raw Hyperliquid API data to `{OUTPUT_DIR}/reversal_ticks.db`:

**Table `ticks`** (PRIMARY KEY: ts, symbol):
| Column | Source | Use |
|--------|--------|-----|
| mark_px | markPx | Perp price |
| oracle_px | oraclePx | Spot/oracle price |
| open_interest | openInterest | OI in coins |
| funding | funding | Funding rate |
| premium | premium | (mark-oracle)/oracle |
| day_ntl_vlm | dayNtlVlm | 24h notional volume USD |
| impact_bid | impactPxs[0] | Book depth bid side |
| impact_ask | impactPxs[1] | Book depth ask side |

**Table `events`** (ts, event, symbol, data JSON):
- `S9F_OBS`: ±3% move in 2h (dir, ret_2h)
- `SKIP`: signal generated but filtered (strategy, dir, reason)

30 symbols × 1440 min/day = ~5 MB/day, ~150 MB/month. WAL mode, NORMAL sync.

## Gotchas

- **DXY filter is critical for S4**: S4 SHORT only active when DXY 7d > +100 bps. Without it, S4 shorts in bull markets and loses. DXY has 3-tier fallback: fresh < 6h, stale 6-48h (S4 stays active), expired > 48h (S4 disabled + degraded banner).
- **DXY cache**: Stored in `analysis/output/pairs_data/macro_DXY.json`. Cache uses 5-trading-day return (`closes[-6]`).
- **Feature cache**: `_refresh_feature_cache()` runs once per hourly scan. `_scan_signals()` and `get_signals()` use cached features. Cache is persisted in state file and restored on restart if < 2h old (avoids blank dashboard during first scan).
- **State persistence**: Atomic writes (write to `.tmp` then `os.replace()`). On load, original is preserved (copy to `.loaded`). Positions, paused state, loss streak, MAE/MFE, and trajectories survive restarts.
- **Compounding effect**: `current_capital = CAPITAL_USDT + _total_pnl`. After big losses, position sizes shrink dramatically. After big wins, positions grow and DD risk increases.
- **HTML cache**: Dashboard HTML is cached in memory on first request. Restart bot to pick up HTML changes.
- **Trades deque**: `self.trades` is `deque(maxlen=500)`. Use `list(self.trades)` before slicing (deque doesn't support slicing).
- **Versioning**: `VERSION` constant in `analysis/bot/config.py`. **ALWAYS bump VERSION when modifying bot code** — patch for bugfixes, minor for features, major for breaking changes. Update `config.py`, `bot.md` title, and `CLAUDE.md` (this file) at the same time. The version displayed on the dashboard is the user's proof that the correct code is running.
- **S6 was removed**: Liquidation bounce signal had z=8.04 in isolation but loses -$627 to -$1,552 in portfolio. Standalone backtest was misleading (simpler backtester, no position limits).
- **S8 capitulation is rare**: Fires ~1/month in portfolio (drawdown > -40% is extreme). When it fires, 70% win rate with avg +413 bps. Max 7 consecutive losses observed (April 2024 crash).
- **SHORT signals are hard**: 378 short variants tested, none pass z > 2.0. S4+DXY remains the only viable short. Altcoin markets have structural long bias.
- **OI/funding/premium collected but not used for signals**: Observation phase. Data logged in `reversal_market.csv` (hourly snapshots, ~24 MB/year) and in each trade's structured fields (`entry_oi_delta`, `entry_crowding`, `entry_confluence`, `entry_session`) + `signal_info` string. Will be analyzed after 50+ trades per pre-registered protocols.
- **Crowding score (0-100)**: Measures leverage stress per token (OI delta + funding + premium + vol_z). Displayed in dashboard, logged in trades. Not used for decisions yet.
- **Signal quarantine**: If a signal's rolling win rate drops below 20% on last 10 trades, it's auto-disabled. Below 30% → sizing halved. Prevents silent degradation.
- **Signal drift**: `/api/state` exposes `signal_drift` with rolling stats per signal (win rate, avg bps, P&L on last 20 trades).
- **Execution mode**: `HL_MODE` in `.env`. Default `paper` simulates in memory. `live` places real orders via SDK. Mode shown in startup log and `/api/state`. **Never push .env to git**.
- **SDK lazy import**: `eth_account` + `hyperliquid` only imported when `HL_MODE=live`. Paper mode has zero SDK dependency.
- **Reconciliation**: Every hourly scan, bot compares its position dict vs `user_state()` from exchange. Mismatches (orphans/ghosts) trigger Telegram alert but NO auto-fix.
- **Telegram**: Uses raw urllib POST (no dependency). Fire-and-forget in daemon thread, never blocks scan or event loop. Events: open, close, error, kill-switch, startup, reconciliation mismatch, daily summary (midnight UTC).
- **Order sizing**: SDK expects size in coin units, not USDT. Conversion: `sz = size_usdt / price`, rounded to `szDecimals` from exchange metadata.
- **Dashboard auth**: HTTP Basic Auth via `DASHBOARD_USER`/`DASHBOARD_PASS` in `.env`. Empty = no auth. Uses `secrets.compare_digest` (timing-safe).
- **Auto-restart**: `@reboot` crontab runs `start_bots.sh` which starts both instances + sends Telegram alert on VPS reboot.
- **Dual instances**: Paper (:8097, `analysis/output/`) and Live (:8098, `analysis/output_live/`) run in parallel from the same code. Only DXY cache is shared (global market data).
- **Slot reservation**: Macro signals (S1) limited to 2 slots, token signals (S5/S8/S9/S10) to 4. Token slots increased from 3→4: +157% P&L without compounding, S5 becomes top contributor. DD unchanged (-38%).
- **S2 was removed**: Alt crash mean-reversion (z=4.00) loses in portfolio. Takes macro slots that S1/S8/S9 use better. S8 (capitulation flush) covers extreme crashes more effectively. See backtest_signal_boost2.py.
- **S9 adaptive stop**: Bigger faded moves get tighter stops. Formula: `max(-2500, -1000 - abs(ret_24h)/4)`. Example: fade +3000bps → stop -1750. Backtest: S9 PnL +54% vs fixed stop (backtest_signal_boost2.py Test 5).
- **S4 suspended**: Vol compression + DXY SHORT (z=2.95) — only 2 trades in 32-month backtest, -$124. Code kept commented in `_scan_signals` for reactivation. Was the only SHORT macro signal; bot is now LONG-biased (S5/S9/S10 can still short individual tokens).
- **S9-fast observation**: Logs `S9F_OBS` when a token moves ±3% in 2h (from 60s price ticks). NOT traded — observation only. Backtest on 7 months of 1h candles: 588 trades, +88.5 bps/trade, 54% win, train+test positive. Needs 6+ months of live data before integration decision. See backtest_1h_fast.py.
- **SHORT signals exhaustively tested**: 378 variants (backtest_short_search.py) + 150 variants (backtest_short_search2.py) — none pass z>2.0 with train+test positive. Altcoin markets have structural long bias. Only individual token fades (S9, S10) work as shorts.
- **DXY cached in memory**: `self._dxy_cache` stores the last DXY value + timestamp. Refreshed only during hourly scan (in `to_thread`). API handlers (`get_state`, `get_signals`) read from memory, never call Yahoo. Prevents event loop blocking.
- **Pause/Reset are sync handlers**: `api_pause()` and `api_reset()` are `def` (not `async def`) so FastAPI runs them in a threadpool. This prevents blocking the event loop during exchange close operations.
- **Fill price from order response**: `_execute_open`/`_execute_close` extract `avgPx` directly from the Hyperliquid order response (`statuses[0]["filled"]["avgPx"]`). Falls back to `user_fills_by_time` with 500ms delay, then market price as last resort.
- **HTTP retry**: `_http_fetch()` helper retries 3 times with exponential backoff (1s, 2s, 4s) on all price/candle/DXY fetches. Transient network failures no longer cause data gaps.
- **Position lock**: `self._pos_lock` (threading.Lock) guards all `self.positions` mutations (pop in `_close_position`, insert in `_scan_signals`) to prevent race conditions between scan thread and API endpoints.
- **Drawdown tracking**: `self._peak_balance` tracks high-water mark, persisted in state. Dashboard shows drawdown % from peak.
- **Daily Telegram summary**: Sent at midnight UTC. Includes day's trades, P&L by strategy, balance, drawdown, quarantine status.
- **Dashboard mobile**: Responsive CSS via `@media (max-width: 768px)`. Cards stack vertically, tables scroll horizontally.
- **Detailed bot documentation**: `docs/bot.md` contains the full bot description — signals, parameters, research, estimates, risks, architecture, production plan. Keep it in sync with code changes.
