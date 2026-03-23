# Backtester Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Download 90 days of Binance Futures historical data and backtest the LiveBot v5.6 strategy with multi-run parameter optimization.

**Architecture:** Two standalone scripts (download + backtest) that read/write CSV files. No modification to livebot.py — import its constants read-only. Download is idempotent. Backtest replays 1-minute ticks through identical signal/trading logic. Multi-run tests one parameter at a time (not exhaustive grid).

**Tech Stack:** Python 3.13, aiohttp (download), numpy, pandas, matplotlib (backtest/plots). All already in .venv.

---

## File Structure

```
analysis/
  livebot.py                    ← NOT MODIFIED (read-only import of constants)
  download_data.py              ← NEW: download historical data from Binance REST
  backtest_v2.py                ← NEW: backtester + multi-run + report
  output/
    backtest_data/              ← NEW: downloaded CSVs (klines, oi, funding, ls_ratio)
    backtest_results/           ← NEW: results (trades CSV, runs CSV, PNG charts)
```

---

### Task 1: Data Downloader

**Files:**
- Create: `analysis/download_data.py`

This script downloads historical data from Binance public REST API for all symbols.

**Data sources (all public, no API key):**

| Data | Endpoint | Granularity | Fields |
|------|----------|-------------|--------|
| Klines | `/fapi/v1/klines` | 1m | open, high, low, close, volume, timestamp |
| Open Interest | `/futures/data/openInterestHist` | 5m | oi, timestamp |
| Funding Rate | `/fapi/v1/fundingRate` | 8h | rate, timestamp |
| L/S Ratio Global | `/futures/data/globalLongShortAccountRatio` | 5m | longAccount, timestamp |
| L/S Ratio Top | `/futures/data/topLongShortPositionRatio` | 5m | longAccount, timestamp |

- [ ] **Step 1: Create download_data.py with CLI and rate-limited fetcher**

```python
"""Download Binance Futures historical data for backtesting.

Usage: python3 -m analysis.download_data --days 90
"""
```

Key implementation details:
- CLI: `argparse` with `--days` (default 90) and `--symbols` (default from livebot)
- Rate limit: `asyncio.Semaphore(5)` + 0.2s delay between requests
- Pagination: Binance limits 1500 klines per request, 500 for OI/LS. Loop with `startTime`/`endTime`.
- Output: one CSV per (symbol, datatype) in `output/backtest_data/`
  - `ADAUSDT_klines_1m.csv`
  - `ADAUSDT_oi_5m.csv`
  - `ADAUSDT_funding.csv`
  - `ADAUSDT_ls_global_5m.csv`
  - `ADAUSDT_ls_top_5m.csv`
- Idempotent: if CSV exists and last row timestamp > (now - 1 day), skip download
- Progress: print `Downloading ADAUSDT klines... 30/90 days` style progress

- [ ] **Step 2: Test download with 1 symbol, 1 day**

Run: `python3 -m analysis.download_data --days 1 --symbols ADAUSDT`
Expected: CSVs created in `output/backtest_data/`, ~1440 kline rows, ~288 OI rows

- [ ] **Step 3: Run full download (90 days, 15 symbols)**

Run: `python3 -m analysis.download_data --days 90`
Expected: ~15 min, ~260 MB total. Print summary at end.

- [ ] **Step 4: Commit**

```bash
git add analysis/download_data.py
git commit -m "Add Binance historical data downloader (klines, OI, funding, L/S ratio)"
```

---

### Task 2: Backtester Core — Data Loading + Signal Replay

**Files:**
- Create: `analysis/backtest_v2.py`

This is the core backtester. It loads historical CSVs and replays the LiveBot signal logic minute by minute.

- [ ] **Step 1: Create backtest_v2.py with data loading**

Load all CSVs into pandas DataFrames, resample to 1-minute aligned timeline:
- Klines: already 1m, use close as mid_price
- OI: 5m → forward-fill to 1m
- Funding: 8h → forward-fill to 1m (rate stays constant between settlements)
- L/S ratios: 5m → forward-fill to 1m
- Merge all into one DataFrame per symbol with columns: `timestamp, close, oi, funding_rate, crowd_long, top_long`
- BTC klines loaded separately for lead-lag signal

- [ ] **Step 2: Implement signal computation (identical to livebot v5.6)**

Port `_compute_signals()` logic to work on DataFrames instead of live state:
- OI divergence: compare OI[-OI_LOOKBACK] vs OI[-1] and price[-OI_LOOKBACK] vs price[-1] at each minute tick. Use same thresholds (price > 3 bps, OI > 0.03%).
- Graduated strength: same formula `np.clip((min(abs(price_change), 20) / 20 + min(abs(oi_change), 0.3) / 0.3) / 2, 0.3, 1.0)`
- Funding proximity: compute minutes to next settlement (00h, 08h, 16h UTC), same rate thresholds
- BTC lead-lag: BTC return over 1 tick, same clip formula
- Smart money: z-score of (top_long - crowd_long) over rolling window of 30
- Composite: same weights (OI 0.35, smart 0.30, funding 0.20, leadlag 0.15)
- Active signals count + leverage map

Import constants from livebot.py:
```python
from analysis.livebot import (
    TRADE_SYMBOLS_LIST, TRADE_SESSIONS, SESSION_CONFIG, LEVERAGE_MAP,
    MAX_LEVERAGE, OI_LOOKBACK, COST_BPS, SLIPPAGE_BPS, HOLD_MINUTES,
    MIN_HOLD_MINUTES, COOLDOWN_MINUTES, TRAIL_ACTIVATE_BPS,
    TRAIL_DRAWDOWN_BPS, VOL_WINDOW, VOL_MAX_BPS, MAX_SPREAD_BPS,
    TREND_LOOKBACK, TREND_THRESHOLD_BPS, CORRELATION_MAX,
    CAPITAL_USDT, BASE_RISK_PCT, MAX_RISK_PCT, MAX_RISK_TOTAL_PCT,
    STREAK_DISABLE, STREAK_COOLDOWN_H, FUNDING_GRAB_MINUTES,
)
```

- [ ] **Step 3: Implement trading logic (identical to livebot v5.6)**

Port `_trading_logic()`:
- Session filter (Asia/US/Overnight, skip Europe)
- Session-specific min_score and lev_mult
- Funding grab (lower threshold near settlement)
- Cross-symbol correlation filter
- Entry filters: cooldown, streak, OI required, spread, vol, trend
- Proportional sizing by score
- Exit logic: timeout, reversal (after min hold), stop loss, trailing stop
- Funding simulation: deduct/credit at each settlement crossing
- Track: trades list, P&L, balance, wins, streaks, cooldowns

- [ ] **Step 4: Verify on 1 day of data**

Run: `python3 -m analysis.backtest_v2 --days 1`
Expected: Prints summary with number of trades, P&L, win rate. Should produce some trades.

- [ ] **Step 5: Commit**

```bash
git add analysis/backtest_v2.py
git commit -m "Add backtester core: data loading + signal/trading replay"
```

---

### Task 3: Multi-Run Parameter Optimization

**Files:**
- Modify: `analysis/backtest_v2.py`

Add `--multi-run` flag that tests parameters one-at-a-time.

- [ ] **Step 1: Add multi-run logic**

Parameter grid (one-at-a-time, not exhaustive):

| Parameter | CLI flag | Values | Default |
|-----------|----------|--------|---------|
| Stop loss | `--stop-loss` | -30, -40, -50, -60 | -40 |
| Trail activate | `--trail-activate` | 15, 20, 25, 30 | 25 |
| Trail drawdown | `--trail-drawdown` | 10, 15, 20 | 15 |
| OI lookback | `--oi-lookback` | 12, 18, 24, 30 | 18 |
| Trend threshold | `--trend-threshold` | 30, 50, 70 | 50 |

One-at-a-time means: for each parameter, test all its values while keeping others at default. Total runs: 4+4+3+4+3 = 18 runs + 1 baseline = 19 runs.

Each run produces: `{ param, value, trades, wins, win_rate, gross_bps, net_pnl_usdt, max_drawdown, avg_hold_min }`

- [ ] **Step 2: Add comparison table output**

Print table to terminal:
```
Parameter         Value  Trades  Win%   Net P&L  MaxDD  Avg Hold
─────────────────────────────────────────────────────────────────
[baseline]        -      XXX     XX%    $XXX     $XX    XXm
stop_loss         -30    XXX     XX%    $XXX     $XX    XXm
stop_loss         -40    XXX     XX%    $XXX     $XX    XXm
...
```

Save to `output/backtest_results/runs_comparison.csv`

- [ ] **Step 3: Commit**

```bash
git add analysis/backtest_v2.py
git commit -m "Add multi-run parameter optimization (one-at-a-time)"
```

---

### Task 4: Charts and Trade Export

**Files:**
- Modify: `analysis/backtest_v2.py`

- [ ] **Step 1: Add equity curve PNG**

Using matplotlib:
- Plot cumulative P&L ($) over time for baseline config
- If `--multi-run`: overlay the best-performing config
- Mark drawdown periods in red shading
- Title: "LiveBot v5.6 Backtest — {days}d, {n_trades} trades, ${pnl}"
- Save to `output/backtest_results/equity_curve.png`

- [ ] **Step 2: Add per-session and per-symbol bar charts**

Two additional PNGs:
- `pnl_by_session.png` — bar chart of P&L by session (Asia/US/Overnight)
- `pnl_by_symbol.png` — bar chart of P&L by symbol (sorted best→worst)

- [ ] **Step 3: Add trade CSV export**

Save detailed trades for baseline run to `output/backtest_results/trades_default.csv`:
Same columns as livebot_trades.csv: symbol, direction, entry_time, exit_time, entry_price, exit_price, hold_min, leverage, size_usdt, gross_bps, net_bps, leveraged_net_bps, pnl_usdt, reason, session

- [ ] **Step 4: Full run verification (90 days)**

Run: `python3 -m analysis.backtest_v2 --days 90 --multi-run`
Expected: ~19 runs, prints comparison table, generates 3 PNGs + 2 CSVs in output/backtest_results/

- [ ] **Step 5: Commit**

```bash
git add analysis/backtest_v2.py
git commit -m "Add charts (equity, session, symbol) and trade CSV export"
```

---

### Task 5: CLI Polish and Documentation

**Files:**
- Modify: `analysis/backtest_v2.py`
- Modify: `analysis/download_data.py`

- [ ] **Step 1: Add CLI help and single-param override**

```bash
# Download data
python3 -m analysis.download_data --days 90

# Backtest with default config
python3 -m analysis.backtest_v2

# Backtest with custom param
python3 -m analysis.backtest_v2 --stop-loss 30

# Multi-run comparison
python3 -m analysis.backtest_v2 --multi-run

# Both combined
python3 -m analysis.backtest_v2 --days 90 --multi-run
```

- [ ] **Step 2: Update CLAUDE.md with backtest commands**

Add to Commands section:
```bash
# Backtest
python3 -m analysis.download_data --days 90   # download historical data (~15 min)
python3 -m analysis.backtest_v2               # run backtest with current config
python3 -m analysis.backtest_v2 --multi-run   # parameter optimization
```

- [ ] **Step 3: Final commit**

```bash
git add analysis/download_data.py analysis/backtest_v2.py CLAUDE.md
git commit -m "v5.7.0 — Backtester with historical data download and multi-run optimization"
git tag -a v5.7.0 -m "v5.7.0 — Backtester: 90d historical data, multi-run param optimization"
```
