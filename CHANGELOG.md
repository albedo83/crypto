# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [11.3.5] — 2026-04-11

### Added
- **S10 health card** on the dashboard (`analysis/reversal.html`), fed by new `compute_s10_health(trades, days=30)` in `analysis/bot/trading.py` and exposed via `/api/state.s10_health`. Shows P&L, trade count, WR and average net bps over the last 30 days with a coloured status dot:
  - **green** — pnl > 0 and avg net > +10 bps
  - **yellow** — pnl ≥ 0 or avg net ≥ −20 bps
  - **red** — pnl < 0 and avg net < −20 bps (signal to flip the v11.3.4 S10 kill-switch)
  - **idle** — no S10 trades in the window
- **LLM supervisor bot** (`/home/crypto/supervisor.py`, ~450 lines): standalone Python process run once a day via crontab. Pulls `/api/state`, `/api/trades`, `/api/health`, `/api/pnl` from each bot, assembles a static context (`CLAUDE.md` + `docs/bot.md` + `docs/backtests.md`, ~25 kB / ~6 k tokens with `cache_control: ephemeral`), calls the Anthropic SDK (`claude-haiku-4-5` default, configurable via `SUPERVISOR_MODEL`), parses a strict JSON report and ships it as plain text via Telegram. Observation + suggestions only — never writes to the bot. Zero import from `analysis/bot/*` for runtime isolation. Configuration lives entirely in `.env`: `ANTHROPIC_API_KEY`, `SUPERVISOR_MODEL`, `SUPERVISOR_ENABLED`. Audit log in the `events` table (`event = 'SUPERVISOR_REPORT'`). Report language is French, focused on the live bot (paper used as a comparison baseline, Bot2 marked `DISABLED` in the per-bot `notes` field so it doesn't trigger false-positive anomalies). Cost measured in practice: ~$0.017/run with prompt cache hit → ~$0.50/month at one run per day. Crontab entry installed:
  ```
  0 8 * * * /home/crypto/.venv/bin/python3 /home/crypto/supervisor.py >> /home/crypto/analysis/output/supervisor.log 2>&1
  ```

### Fixed
- Dashboard silently stopped refreshing any card after the v11.3.4 S10 health card was added. Root cause: `const h = s.s10_health || {}` collided with `const h = Math.floor(up/3600)` declared earlier in the same `update()` scope, producing a `SyntaxError: Identifier 'h' has already been declared` at script load time that killed the whole update loop. All variables in the S10 health section renamed to `s10h`. Extracted script block now validates cleanly with `node --check`.
- `supervisor.py` Telegram delivery: the original `send_telegram` only checked HTTP status; Telegram returns `HTTP 200` with `ok: false` in the JSON body when Markdown parsing fails (routine on LLM-generated content containing underscores like `S10_ALLOW_LONGS` or unbalanced asterisks). Messages were accepted by the socket, logged as "Telegram sent", and never delivered. `send_telegram` now parses the response body, checks `ok == true`, and logs `description` on failure. `format_telegram` switched to pure plain text (no `parse_mode`) — emoji + whitespace structure is readable and immune to LLM content quirks.

### Changed
- `supervisor.py`: `BOTS` list gained a `notes` free-form field per bot, propagated into the Claude prompt so the model knows the operational status of each instance. Paper and Live carry descriptive notes; Bot2 is marked `DISABLED — running as paper placeholder, ignore P&L/trade counts`. `build_user_prompt` now explicitly instructs Claude to target the report at the Live bot (summary, `key_metrics`, anomalies, suggestions focused on Live), keeping Paper only as a comparison baseline for cross-bot divergence detection. The system prompt mandates French output for every textual field (`summary`, `detail`, `action`, `rationale`), with an explicit exclusion list of English filler words the model routinely slipped in.

## [11.3.4] — 2026-04-11

### Changed
- **S10 walk-forward filters** (`backtest_s10_walkforward.py`). Train 2023-10→2025-02 (16m), test 2025-02→2026-02 (12m out-of-sample). Two filters applied on top of the frozen squeeze detection:
  - `S10_ALLOW_LONGS = False` — LONG fades were 45% WR / -$4.8k on 28m. Rationale: fading a down-move ≈ fighting panic-selling continuation.
  - `S10_ALLOWED_TOKENS` — whitelist of 13 tokens (AAVE, APT, ARB, BLUR, COMP, CRV, INJ, MINA, OP, PYTH, SEI, SNX, WLD) whose S10 had positive P&L on the train window.
- Combined test-window impact: P&L +123% ($4 278 → $9 545), DD -41.3% → -32.6%. On the refreshed rolling backtest: 12m doubles ($3 959 → $8 007), 6m +$1 595, 3m +$422. Caveats documented in `docs/backtests.md`: 28m DD worsens by 8.7pp and 1m post-test regresses -$181 — the rule is a bet on the 2025-26 regime, not a universal law.
- Kill-switch preserved: reset `S10_ALLOW_LONGS = True` and `S10_ALLOWED_TOKENS = set(ALL_SYMBOLS)` in `config.py` to restore pre-v11.3.4 behaviour.

### Added
- `backtests/fetch_oi_history.py` — downloads Hyperliquid `asset_ctxs` daily dumps from S3 Requester Pays, filters to bot's 30 tokens, downsamples to hourly. ~$0.60 one-shot S3 egress for the full 3-year history (665k rows, 67 MB SQLite, gitignored).
- `backtests/backtest_oi_explore.py` — per-signal quartile analysis of `oi_delta_6h`, `oi_delta_24h`, `impact_spread`, `mark_oracle` at entry time.
- `backtests/backtest_oi_gates.py` — walk-forward validation of 7 single-feature gates + 3 combinations. **All rejected** (strict criterion: must improve on 4/4 windows).
- `backtests/backtest_s10_diag.py`, `backtest_s10_fix.py`, `backtest_s10_walkforward.py` — the diagnostic chain that surfaced the LONG/SHORT asymmetry and the token filter.
- `backtests/backtest_rolling.py` now exposes a `skip_fn` hook and returns the full trades list so any future gate hypothesis can be plugged in without forking the engine.

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
