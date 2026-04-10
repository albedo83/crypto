# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [11.3.3] — 2026-04-10

### Fixed
- Price charts could only display ~24h even though the API returned 72h of data. Root cause was LightweightCharts silently clamping due to `minBarSpacing` constraints on narrower containers.

### Changed
- `/api/chart` downsamples to 200 points (was 600) — 22-minute granularity for a 72h window, plenty for visual inspection and safer for rendering across all container widths.
- Chart `timeScale` uses `minBarSpacing: 0.001` and `rightOffset: 0` so the full data range always fits regardless of viewport.
- Chart x-axis labels use an explicit `tickMarkFormatter` showing `JJ/MM HHh` on every tick so the date is never ambiguous.

## [11.3.2] — 2026-04-10

### Fixed
- `/api/chart` downsamples ticks via SQLite `GROUP BY` bucketing so 72h windows stay under ~600 points; LightweightCharts could previously only render ~1 day of the 3 days returned.
- `build_pnl_curve` crashed with `NameError: bot is not defined` on every P&L chart request; function now takes `capital` as a parameter and uses `CAPITAL_USDT` as the historical baseline so the curve stays consistent after DCA.
- `api_reset` logged `CAPITAL_USDT` instead of `bot._capital`, giving wrong values after DCA injection.
- Trades table on the dashboard renders exit time in the local browser timezone instead of UTC.

### Changed
- `db.py` docstring and imports cleaned up; CSV migration paths now derived locally in `migrate_csv_to_db`.
- `load_trades` documents that it must only be called at startup (no `_db_lock` acquired).
- `admin_config.json` and `start_bots.sh` explicitly comment Bot 2 as direct-port access only (no nginx subpath).

### Removed
- Dead `TRADES_CSV` and `MARKET_CSV` constants from `config.py` — all persistence is SQLite-only since v11.3.1.

## [11.3.1] — 2026-04-10

### Changed
- All persistence now goes through SQLite exclusively. `write_trade`, `write_trajectory`, and `log_market_snapshot` no longer write CSV files. `load_trades` reads from SQLite at startup.
- The CSV migration helper in `db.py` is preserved for upgrades from pre-11.3.1 installations (runs only if tables are empty).
- Admin panel and bot dashboards accept `ADMIN_ROOT_PATH` / `HL_ROOT_PATH` env vars so they can be mounted behind nginx subpaths (`/crypto/`, `/paper/`, `/bot/`). Redirects and login forms use relative paths so direct port access still works.
- Admin panel "Open" button uses `path` from `admin_config.json` when set; falls back to `host:port` for direct-access bots.

## [11.3.0] — 2026-04

### Added
- Admin panel on `:8090` aggregating multiple bot instances with auto-login tokens.
- DCA capital injection from the dashboard via `/api/capital`.
- Real Hyperliquid spot balance + perps equity displayed on live dashboard (separate from the bot's accounting balance).
- Manual single-position close button per position row.
- Stateless HMAC signed sessions that survive bot restarts.
- HTML login page (Dashlane-compatible) replacing HTTP Basic Auth.
- Notionnel and Marge columns in the position table.
- Bot 2 slot on `:8099` (paper mode until private key is set).

### Fixed
- P&L double-leverage bug: `size_usdt` is notional, but the formula multiplied by `LEVERAGE` again, making P&L 2× reality. Stops halved to match (`-1250` bps instead of `-2500`).
- Position table now distinguishes notionnel (`size_usdt`) from marge (`size_usdt / 2`).
- NaN display on dashboard during startup before first price fetch.
- Position marker placement on chart after zoom/pan.
- Admin panel crash when a bot is offline (graceful fallback).
- Orphan positions on crash by persisting state immediately after opening.
- Race conditions on `_failed_closes`, `api_reset`, and reconcile dict access via `_pos_lock`.
- SQLite thread safety via `_db_lock` serializing all writes.
- State file corruption on orjson serialization error (keeps existing state file on failure).
- HTTP retry with exponential backoff on price, candle, and DXY fetches.
- File lock (`fcntl`) preventing two bot instances on the same state file.

### Changed
- Base sizing increased from 12% to 18% (+138% P&L in backtest, DD -81%).
- Slot reservation: macro signals limited to 2 slots, token signals to 4 (was 3). +157% P&L without compounding.
- Kill-switch, loss streak, quarantine, and exposure cap all disabled — backtests showed they destroy compounding returns (-65% to -99% P&L).
- S9 early exit at `-500` bps after 8h (S5/S8/S10 early exits tested and destroy value in compounding).
- Auto-restart via `@reboot` crontab running `start_bots.sh`.
