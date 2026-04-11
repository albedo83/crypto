# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Crypto trading bot for Hyperliquid DEX (accessible from France). Paper/live trading on 28 altcoins.

**The bot is 12 modules** in `analysis/bot/` + `analysis/reversal.html` (dashboard). `analysis/reversal.py` is a 6-line backward-compat shim. Backtests are in `backtests/`.

Version in `analysis/bot/config.py` `VERSION` constant (currently 11.3.3). Dashboard on `:8097`.

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
    ├── db.py          — SQLite schema, tick/event logging, one-time CSV migration
    ├── persistence.py — SQLite writes, state save/load, market snapshots
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

### Signals in one line

5 active signals: **S1** (BTC momentum → LONG alts), **S5** (sector divergence follow), **S8** (capitulation flush LONG), **S9** (fade ±20%/24h extreme moves), **S10** (squeeze + false breakout fade). S2 removed, S4 suspended.

For detailed conditions, parameters, and research behind each signal see **`docs/bot.md`** (French). For the history of changes see **`CHANGELOG.md`**.

### API endpoints (summary)

Dashboard-facing routes live in `analysis/bot/web.py`. Read-only: `/api/health`, `/api/state`, `/api/signals`, `/api/trades`, `/api/pnl`, `/api/chart/{symbol}`. Mutating: `/api/close/{symbol}`, `/api/pause`, `/api/resume`, `/api/reset`, `/api/capital` (DCA). All require auth except `/login`, `/auth`.

## Gotchas that affect coding

Things that will bite you when modifying the code. For signal-specific details, backtest rationale, and parameter history see `docs/bot.md`.

### Versioning & deployment
- Bump `VERSION` in `config.py` for every code change and use `/release` skill (updates `CHANGELOG.md`, `docs/bot.md`, `CLAUDE.md`, commits).
- Restart bots after bumping — `VERSION` is only read at startup.
- Dashboard HTML is cached in memory on first request. Restart bot to pick up HTML changes.
- Paper (:8097) and Live (:8098) run in parallel from the same code, separate output dirs. Only DXY cache is shared.
- `@reboot` crontab runs `start_bots.sh` (paper + live + Bot 2 + admin panel + Telegram alert).
- Nginx subpaths via env vars: `ADMIN_ROOT_PATH=/crypto`, `HL_ROOT_PATH=/paper` or `/bot`. Empty = direct port access still works.

### State & persistence
- Atomic writes (`.tmp` then `os.replace`). Positions, paused state, MAE/MFE, trajectories, capital survive restarts. `.loaded` backup kept on load.
- **SQLite is the source of truth** for trades, trajectories, market snapshots, ticks, events. CSV writes were removed in v11.3.1 (migration helper in `db.py` still runs once if old CSVs exist).
- Feature cache persisted and restored on restart if < 2h old (avoids blank dashboard).
- `fcntl` file lock on `STATE_FILE.lock` prevents two bot instances sharing state.
- `self.trades` is `deque(maxlen=500)`. Use `list(self.trades)` before slicing.

### P&L math (critical)
- `size_usdt` is the **notional** (already leveraged). `pnl = size_usdt × price_change`. **Do NOT multiply by LEVERAGE again.** This was the v11.3.0 double-leverage bug — all stop values halved after the fix.
- Compounding: `current_capital = bot._capital + _total_pnl`. Big losses shrink position sizes dramatically.
- "Balance" (dashboard) = capital + **realized** P&L only. "Equity" (exchange card, live only) = real Hyperliquid spot USDC + perps marginSummary and includes unrealized. Drawdown is computed on balance, not equity.
- Position table: `Position` column = notionnel (`size_usdt`), `Marge` column = notionnel/leverage.

### Concurrency & safety
- `bot._pos_lock` guards all `self.positions` mutations.
- `db._db_lock` (in `analysis/bot/db.py`) serializes SQLite writes across scan, API, and collector threads.
- `load_trades` is called once at startup before the scan thread — no DB lock held.
- `api_pause`, `api_reset`, `api_close_symbol` are sync handlers (`def`, not `async def`) so FastAPI runs them in a threadpool. Prevents blocking the event loop during exchange close.
- DXY cached in memory (`self._dxy_cache`); API handlers never call Yahoo directly.
- `_http_fetch` retries 3× with exponential backoff on all price/candle fetches.

### Execution (live mode)
- SDK (`eth_account` + `hyperliquid`) is lazy-imported only when `HL_MODE=live`. Paper mode has zero SDK dependency.
- Order size must be in coin units: `sz = size_usdt / price`, rounded to `szDecimals` from exchange metadata.
- Fill price extracted from order response (`statuses[0]["filled"]["avgPx"]`), with fallback to `user_fills_by_time` + 500ms delay, then market price.
- Failed exchange closes tracked in `bot._failed_closes` and retried on the next scan.
- Reconciliation (hourly scan) compares bot positions vs `user_state()`. Mismatches trigger Telegram alert but NO auto-fix.
- Every 60s the live bot refreshes `bot._exchange_account` from Hyperliquid (spot + perps). This is what the "Equity" card shows.

### Auth & UI
- Dashboard auth: HTML login form via `DASHBOARD_USER`/`DASHBOARD_PASS` in `.env`. HMAC-signed stateless session cookies (30-day expiry) survive restarts. 10 attempts/5min/IP rate limit.
- Admin panel on `:8090` (behind `/crypto/` on nginx). Aggregates all bots via `admin_config.json` and proxies with cached auth cookies.

### Observation-only data (don't use for decisions yet)
- OI / funding / premium / `entry_crowding` / `entry_confluence` / `entry_session` are logged in each trade and in hourly market snapshots — not used for signals until 50+ trades per pre-registered protocols.
- `/api/state.signal_drift` exposes rolling WR/avg bps/P&L for monitoring. Quarantine logic itself is disabled (protections list in `docs/bot.md`).
- `S9F_OBS` events (±3% / 2h) are logged but not traded — need 6+ months of live data.

### Supervisor (v11.3.5+)
`supervisor.py` is a standalone Python process meant to run daily via crontab (08:00 UTC). It reads `/api/state`, `/api/trades`, `/api/health`, `/api/pnl` from each bot, assembles a context (plus `CLAUDE.md`/`docs/bot.md`/`docs/backtests.md`), calls Claude via the Anthropic SDK with prompt caching on the static block, and ships a structured report via Telegram. **Observation + suggestions only — never writes to the bot.**
- Config: `ANTHROPIC_API_KEY`, `SUPERVISOR_MODEL` (default `claude-haiku-4-5`), `SUPERVISOR_ENABLED` in `.env`
- Zero runtime coupling: no imports from `analysis/bot/*`, reads endpoints over `127.0.0.1` only
- Kill-switch: `SUPERVISOR_ENABLED=0` or `crontab -e` comment the line
- Audit: every run writes a `SUPERVISOR_REPORT` event into the existing `events` table
- Testing: `supervisor.py --dry-run` (no API), `--no-telegram` (API but stdout), `--model X` (override)
- Crontab: `0 8 * * * cd /home/crypto && .venv/bin/python3 supervisor.py >> analysis/output/supervisor.log 2>&1`
- Cost: ~$1.50/month on haiku with prompt caching (~6k cached tokens per run)

## Related docs
- `docs/bot.md` — detailed bot description (French): signals, parameters, protections, research, architecture.
- `docs/backtests.md` — rolling backtest results for the current parameters, regenerated via `python3 -m backtests.backtest_rolling`.
- `CHANGELOG.md` — release history, maintained via `/release` skill.
