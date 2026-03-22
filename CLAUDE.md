# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Crypto trading bot + microstructure analysis for Binance Futures. **Current active system:**

- **LiveBot** (`analysis/livebot.py`): Single asyncio process — Binance WS + REST → in-memory signals → paper trading + built-in web dashboard. No DB dependency.

**Legacy systems (disabled, kept for reference):**
- Collector (`src/collector/`): WS → TimescaleDB. Systemd service disabled.
- Dashboard (`src/dashboard/`): FastAPI + htmx. Systemd service disabled.
- PostgreSQL: idle, contains historical analysis data (7 days, March 15-22 2026).

## Commands

```bash
# LiveBot (the one that matters)
nohup .venv/bin/python3 -m analysis.livebot > analysis/output/livebot.log 2>&1 &
# Dashboard: http://0.0.0.0:8095
# Stop: fuser -k 8095/tcp
# Logs: tail -f analysis/output/livebot.log
# Trades: analysis/output/livebot_trades.csv

# Analysis studies (use PostgreSQL, need collector data)
.venv/bin/python3 -m analysis.study_06_swing
.venv/bin/python3 -m analysis.study_07_asia_edge
.venv/bin/python3 -m analysis.study_10_symbol_scan

# Legacy collector/dashboard (disabled)
# sudo systemctl start crypto-collector crypto-dashboard

# Database migrations (legacy)
.venv/bin/alembic upgrade head
```

No test framework, linter, or CI pipeline is configured.

## LiveBot Architecture (v4)

```
Binance Futures
    ├── WebSocket (68 streams, 1 connection)
    │     bookTicker + aggTrade + markPrice@1s × 17 symbols
    └── REST (every 60s)
          openInterest × 17 symbols
              │
              ▼
    analysis/livebot.py  (single process)
    ├── SignalEngine (every 10s, 4 signals)
    │     OI divergence (35%) + smart money (30%)
    │     + funding proximity (20%) + BTC lead-lag (15%)
    │     → composite score per symbol
    ├── CapitalManager
    │     $1000 virtual capital, max 5 positions
    │     Rank signals by strength, allocate best first
    ├── TradingLogic
    │     Sessions: Asia (0-8h) + US (14-21h) + Overnight (21-24h)
    │     Entry: score > 0.3, ≥ 1 active signal
    │     Exit: 2h timeout / reversal / stop loss -100bps
    │     Leverage: 1x(1sig) → 2x(2sig) → 3x(3sig)
    ├── Dashboard (FastAPI on :8095)
    │     Ticker 1s / State 5s / Trades 10s
    └── CSV logger → analysis/output/livebot_trades.csv
```

### Symbols

**Traded (15):** ADAUSDT, BNBUSDT, BCHUSDT, TRXUSDT, HYPEUSDT, ZROUSDT, AAVEUSDT, LINKUSDT, SUIUSDT, AVAXUSDT, XRPUSDT, XMRUSDT, XLMUSDT, TONUSDT, LTCUSDT

**Reference (2):** BTCUSDT, ETHUSDT (for BTC lead-lag signal, not traded)

Selected by OI/volume ratio scan of 544 Binance Futures perpetuals (`study_10_symbol_scan.py`).

### Strategy: OI Divergence

- Price up + OI down = "weak long" → short (fade the move)
- Price down + OI up = "weak short" → long (fade the move)
- Boosted by funding rate proximity (< 2h to settlement) and BTC lead-lag
- European session (8-14h UTC) excluded — signal inverts there

### Capital Management

- $1000 virtual capital (paper trading)
- Max 4 concurrent positions
- Each position: 25% of capital as margin ($250) — full Kelly criterion
- With leverage: $250 (1x) to $750 (3x)
- Max 90% capital exposed
- When >4 signals: rank by |score|, take top 4

### Key Findings (from 10 analysis studies)

- OI divergence on ADA: +20.9 bps/trade net (37 trades over 7 days)
- Smart money divergence: rho +0.43 on SUI (strongest signal found, study 13)
- OI × Smart money correlation: 0.002 (independent → fusion improves +2.9 bps/trade)
- Asia session = strongest edge (+36 bps/trade), Europe = worst (-32 bps)
- Signal correlation across symbols: ~0.07 (independent → diversification works)
- Micro-structure signals (book imbalance, 5-30s) don't survive fees (edge < cost)
- Swing signals (OI divergence + smart money, 2h) survive fees (edge >> cost)

## Legacy Architecture

### Collector Pipeline (disabled)

```
Binance WS → WSManager → Dispatcher → Handler → BatchWriter → PostgreSQL
```

WSManager, Dispatcher, Handlers, BatchWriter, Engine, HealthMonitor — all in `src/collector/`.

### Legacy Dashboard (disabled)

FastAPI app factory in `src/dashboard/app.py`. Six routers: status, streams, data, metrics, alerts, paper.

### Database Schema

9 hypertables: trades_raw, book_tob, book_levels, mark_index, funding, open_interest, liquidations, heartbeat, collector_events. Plus 4 matviews (trades_1m, book_tob_1m, order_flow_1m, book_imbalance_1s), 2 SQL functions, paper_trades, paper_state.

Retention: book_tob 3d, book_levels 3d, trades_raw 7d, most tables 30d, funding 90d.

7 migrations in `migrations/versions/` (001-007).

## Gotchas

- **Session filter is critical**: European session (8-14h UTC) inverts the OI divergence signal. Never remove this filter.
- **OI polling rate**: 17 symbols × 0.15s delay = ~2.5s per cycle. Binance rate limit is ~10 req/s.
- **Binance WS limit**: 200 streams per connection. Current 68 streams fits in 1 connection.
- **Capital grows/shrinks**: P&L adjusts the capital for position sizing (compound effect).
- **PAXGUSDT excluded**: Gold token, scored high but behaves differently from crypto altcoins. Removed from Tier A.
- **No auto-optimization yet**: Wait for 2-3 weeks of live data before implementing self-tuning. Analysis files: `livebot_signals.csv` (every 60s, 17 symbols) + `livebot_trades.csv`. Future: nightly self-review at 8h UTC, disable losing symbols, adjust thresholds.
- **Two bots run in parallel**: LiveBot (:8095) = OI divergence swing, CarryBot (:8096) = funding carry. Independent capital, independent strategies. CarryBot is market-neutral (no directional risk).
