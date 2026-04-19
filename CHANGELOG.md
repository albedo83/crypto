# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [11.7.2] — 2026-04-19

### Added
- Exit safeguard for trades approaching timeout without any sign of recovery. Walk-forward validated on historical data.
- Additional backtest tooling for exit optimization and entry-filter research (several hypotheses tested, most rejected).

### Changed
- Backtest engine aligned with the live bot for the new exit safeguard.

## [11.7.0] — 2026-04-18

### Added — dashboard redesign
- **Left sidebar with per-block toggles** (220px sticky on desktop). 11 sections grouped under PERFORMANCE / MARKET CONTEXT / SIGNALS / DIAGNOSTICS, each with its own checkbox. "Show all" / "Hide all" shortcuts. State persisted in `localStorage` under `dash-blocks-v2`.
- **Mobile drawer**: hamburger button (☰) in the header at < 1024px viewport; sidebar slides in from the left with a backdrop overlay.
- **Responsive card-mode for tables**: Open Positions and Trade History use `table.responsive` class — at < 640px each row becomes a vertical card with `data-label` cells (label left / value right).

### Changed — dashboard layout
- **Default view is now minimal**: only header, activity bar, market bar, Equity/PnL cards, and Open Positions. Every other section (Strategy Performance, P&L Curve, Price Chart, Trade History, Sector overview, Capital flow, Next-scan preview, Event timeline, OI delta grid, Signals cards, Signal proximity heatmap) is hidden by default and activated via the sidebar.
- Old "Show advanced indicators ▾" button + `toggleDetails()` helper removed (superseded by the per-block sidebar).
- Mobile breakpoints: tablet ≤ 1023px collapses sidebar to drawer; phone ≤ 640px switches to card-mode tables, 2-column cards, smaller charts; ≤ 420px cards become 1-column.
- New `toggleDrawer()` + `onToggleBlock()` handlers; renderers fire on-demand when a block is toggled on (no need to wait for the next refresh cycle).

## [11.6.2] — 2026-04-18

### Fixed — trading engine audit
- Stops now book at the stop trigger price rather than drifting with intra-scan mark price.
- Filled position sizes reflect the actual exchange fill, eliminating false "size mismatch" reconciliation alerts.
- Race condition removed in the entry loop when a dashboard action runs concurrently with a scan.
- In-memory trade history capacity extended so long-term statistics remain accurate.

### Fixed — dashboard fixes (from UI code review)
- Daily Telegram DEGRADED flag reads `recent20` win rate, not the now-lifetime top-level field (regression introduced in v11.6.0).
- Signal proximity S8 clamped to [0, 1] (was producing negative values due to missing clamps on several ratio terms).
- Signal proximity S10 normalized so firing threshold maps to 1.0 (was 0.5, made S10 never reach "firing" in the heatmap).
- Next-scan preview no longer emits 28 duplicate "S1 LONG" rows; S1 emits once as `ALTS`.
- Next-scan preview now includes S10 candidates (were invisible).
- `/api/events?limit=` capped to `[1, 200]` to prevent accidental/abusive large queries.

### Changed — dashboard layout
- "Show advanced indicators ▾" toggle moved out of Strategy Performance header into a dedicated centered button just above the collapsed block, on a dashed divider.
- P&L Curve, BTC live chart, and Trade History are now always visible (moved out of the collapse wrap). Default collapsed section still holds Sector overview, Capital flow, Next-scan preview, Event timeline, OI delta grid, Signals cards, Signal proximity heatmap.

## [11.6.0] — 2026-04-18

### Added
- **Details toggle** in dashboard: "Show more ▾" button in the Strategy Performance header. Collapses all sections below (Sector overview, Capital flow, Next-scan preview, Event timeline, OI delta 24h, P&L Curve, Chart, Signals, Trade History, Signal proximity) by default. Preference persisted in `localStorage`.
- **Strategy Performance — Recent 20 group**: alongside the existing stats, the table now shows lifetime AND recent 20 trades per strategy (Trades / WR / Avg bps / P&L × 2). Trend arrow (↑/↓/=) applies to the Recent 20 WR.

### Changed
- Strategy Performance now shows **lifetime stats by default** (previously: only a short rolling window, which misrepresented a strategy's structural edge).
- Backend helper returns both `lifetime` and `recent20` sub-dicts per strategy; legacy top-level fields remain for backward compat.
- Position table "Hold / Rem" column renamed "Remaining / held": remaining time is now bold (primary), elapsed hold shows dim below.
- Strategy Performance column groups (Signal / Lifetime / Recent 20) distinguished by subtle background color gradient instead of a hard vertical separator.

## [11.5.0] — 2026-04-18

### Added — dashboard batch 1 (inline position indicators)
- Stop-distance bar under each position's Unrealized cell (green→yellow→red gradient)
- Hold-progress bar under Hold cell
- Trajectory sparkline column showing unrealized P&L since entry
- OI delta 24h grid below positions: every token with Δ(OI,24h) %, gate-blocked cells highlighted, blacklisted tokens struck through
- Backend fields added to `/api/state` for each of the above

### Added — dashboard batch 2 (context indicators)
- Regime badge in top bar (BULL / BEAR / CHOPPY / STRESSED / RALLY / FLUSH), computed backend-side. STRESSED pulses.
- Signal proximity heatmap (28 tokens × 5 strategies): per-pair "% to firing" 0-100, gradient grey→green, rendered at the bottom of the dashboard.
- Sector overview grid (DeFi / L1 / Meme / Infra / Gaming) with position count, unrealized P&L, avg 24h return.
- Strategy Performance rows now show trend arrow (↑ / ↓ / =) based on win-rate direction. Red when WR is low AND trending down.
- Backend fields: `regime`, `regime_stress`, `sector_stats`, `proximity`, `trend`.

### Added — dashboard batch 3 (forward-looking indicators)
- Next-scan preview: every token currently firing a signal with status (`would enter` / `max_positions` / `blacklist` / `oi_gate` / `cooldown` / `max_long` / `max_sector` / etc.).
- Capital flow bars (muted palette, 10px rail): breakdown of open notional by strategy / direction / sector, with dollar legend.
- Event timeline ticker at the bottom: last 30 events (TRADE_OPEN / TRADE_CLOSE / SKIP / RECONCILE / SUPERVISOR_REPORT / PAUSE / RESUME) with icons, colored borders, relative time.
- Trailing-floor indicator displayed inline on positions with an active trailing stop.
- Backend: `preview` list, trailing-state flags; new `/api/events` endpoint.

### Fixed
- Admin panel auto-login: "Open" button no longer forces a re-auth. Each bot derives its HMAC secret from `sha256(password + AUTH_SALT)` since v11.4.6, but the admin was still signing tokens with its own salt-less secret. `/api/auth-token?port=<p>` now signs using the target bot's exact secret.

### Changed
- Dashboard UI fully English (removed remaining FR strings in labels, prompts, and error messages).
- Layout: Open Positions moved directly under the Equity/P&L cards. Action buttons (DCA / STOP / RESUME / RAZ / Release notes) relocated into the header.
- Position table adds columns "Path" (sparkline) and per-cell inline bars; column count unchanged.

## [11.4.10] — 2026-04-17

### Added
- Entry-level token filter removing structurally underperforming pairs based on backtest analysis. Removed tokens stay in the observation universe for future re-evaluation.

### Validation
- `docs/backtests.md` regenerated with current filters applied.

## [11.4.9] — 2026-04-17

### Added
- Additional entry filter based on derivatives flow, activating after a short warm-up period. Improves risk on specific signal families.
- Extended in-memory history required for the new filter.
- Skip events are logged for audit.

### Validation
- Walk-forward validated on multiple rolling windows with neutral drawdown impact. Selected after comparing against several alternative filters, all of which were rejected.

## [11.4.8] — 2026-04-17

### Changed
- Junior Telegram filter now also receives startup notifications.

## [11.4.7] — 2026-04-17

### Added — multi-instance identification
- **Telegram category filter** (`TG_CATEGORIES` env var): comma-separated allowlist of message categories. Default `*` = send everything (Paper/Live behavior unchanged). Junior uses `trade,daily` so it only receives trade open/close/fail alerts and the daily summary — no reconcile, login, DCA, or startup noise.
- **Bot label prefix in Telegram**: when `BOT_LABEL` is set, every message is prefixed with `[LABEL]`. Multiple Junior-like bots sharing a chat stay visually distinguishable.
- **Colored card border in admin panel**: each bot's `BOT_LABEL_COLOR` is exposed via `/api/state` and propagated through `/api/bots` to `admin.html`, which renders it as `border-top` on the card. Makes N juniors visually identifiable at a glance without reading the label.

### Changed
- `send_telegram(msg, category="other")` — call sites now tag each message: `trade` (open/close/failures/kill-switch), `daily` (daily summary), `reconcile` (orphan/ghost/mismatch/drift), `security` (login OK/FAIL), `admin` (capital DCA), `system` (startup).

## [11.4.6] — 2026-04-17

### Added — security hardening (level 1)
- **Telegram alerts on login** (success + failure) with user, IP, mode label, and attempt count. Gives real-time visibility into access patterns.
- **HTTP security headers** on every response (applied before auth middleware): `X-Frame-Options: DENY` (blocks clickjacking), `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `Permissions-Policy` (disables geolocation/mic/camera), `Content-Security-Policy` (limits script/style/img sources), `Strict-Transport-Security` when served over HTTPS.
- **AUTH_SALT**: random salt mixed into the HMAC secret (`sha256(password + salt)`). A leaked session cookie no longer permits offline password brute-force without also leaking the salt. Stored in `.env`, generated once (48 chars, cryptographically random). **Changing the salt invalidates all existing sessions.**
- **Exponential backoff on failed logins**: replaces the flat rate limit. Each failure doubles the required delay (1s → 2s → 4s → ... → 300s cap) per IP. Counter resets on success or 1h idle. Correctly honors `X-Forwarded-For` when request comes via nginx proxy so real client IP is logged.

### Required user action
None — `AUTH_SALT` was auto-generated and added to `.env` during the release. All bot sessions need re-login once after the restart (expected, since the salt change invalidates old cookies).

## [11.4.5] — 2026-04-17

### Added
- **Release notes button** in dashboard header: opens a modal with the full `CHANGELOG.md` rendered (H1/H2/H3 headings, bullet lists, inline code, bold). New `/api/changelog` endpoint serves the raw markdown. Close with ESC or click outside. New constant `CHANGELOG_PATH` in `config.py`.

## [11.4.4] — 2026-04-17

### Added
- **Configurable bot label** (`BOT_LABEL`, `BOT_LABEL_COLOR` env vars): override the default PAPER/LIVE display label and top-border color. Used for Junior so the bot presents itself as JUNIOR everywhere: login page, browser tab title, dashboard border.
- Helpers `_mode_label()` and `_mode_color()` in `web.py` centralize the label/color resolution (env override → fallback to `EXECUTION_MODE`).

## [11.4.3] — 2026-04-17

### Added
- Junior bot now runs with isolated dashboard credentials and Telegram identity.
- Admin panel resolves per-bot credentials so switching between bots no longer requires re-login.

### Required user action
Add 4 lines to `.env`:
```
JUNIOR_USER=<username>
JUNIOR_PASS=<password>
JUNIOR_TG_BOT_TOKEN=<BotFather token, optional>
JUNIOR_TG_CHAT_ID=<chat_id, optional>
```
Then restart bots with `fuser -k 8097/tcp 8098/tcp 8099/tcp 8090/tcp && start_bots.sh`.

## [11.4.2] — 2026-04-17

### Fixed
- Reconcile size check no longer triggers false alerts when a position gains or loses value mid-hold (compared coin quantities instead of notional).

## [11.4.1] — 2026-04-14

### Added
- **Enhanced reconciliation**: detects direction mismatches between the bot's view and the exchange, triggering a Telegram alert.
- **Equity drift alert**: the live scan loop monitors the divergence between bot accounting and exchange equity, alerting when it exceeds a threshold.
- **Startup P&L sanity check**: on boot, the bot verifies stored P&L against the trade history and logs a warning on any discrepancy.

### Fixed
- Code review findings from the v11.4.0 audit addressed.

## [11.4.0] — 2026-04-13

### Added
- Exit optimization for one of the signal families, walk-forward validated. Locks in gains when a configured condition is met.
- Systematic backtest sweep of exit variants — most rejected, one retained.
- Token rotation analysis — kept the full universe.
- Live audit backtest comparing paper and live trades, analyzing cost structure.
- Open-interest sizing experiment — rejected.

### Changed
- Backtest engine tracks per-position MFE and applies the retained exit, matching live bot behavior.

## [11.3.7] — 2026-04-13

### Added
- **"Frais exchange" card** on live dashboard: shows total taker fees and funding paid, fetched from Hyperliquid fill and funding history. Hidden in paper mode.
- **P&L % and Capital stats** added to admin panel bot cards.

### Changed
- **"P&L cumulé" card** now uses real equity (spot USDC + unrealized) on live instead of the bot's accounting — shows true gain/loss including fees and funding.
- **Equity calculation** fixed: was using perps only, now correctly uses `spot USDC + unrealized perps`.
- **Utilization** now divides notional by leverage — no longer shows >100% with 2x leverage.
- Bot names: "Paper Bot" → **Paper**, "Live Bot" → **Live**, "Bot 2" → **Junior** (admin + supervisor).
- Live bot `HL_CAPITAL` corrected.

### Fixed
- Dashboard P&L % showed `++6.9%` — `fmt()` already prepends `+`, removed redundant prefix.

## [11.3.6] — 2026-04-12

### Changed
- Dashboard: replaced "Drawdown" card (distance from peak balance) with **"P&L %"** card showing `(balance − capital) / capital × 100` — directly shows whether you're above or below your initial capital. Sub-text displays the reference capital amount.

## [11.3.5] — 2026-04-11

### Added
- **Strategy health card** on the dashboard for one of the signal families, with coloured status dot (green/yellow/red/idle) over a recent rolling window.
- **LLM supervisor bot** (`/home/crypto/supervisor.py`, ~590 lines): standalone Python process run once a day via crontab. Pulls `/api/state`, `/api/trades`, `/api/health`, `/api/pnl` from each bot, assembles a static context (CLAUDE.md + docs, ~30 kB / ~7.5k tokens with `cache_control: ephemeral`), calls the Anthropic SDK (`claude-haiku-4-5` default, configurable via `SUPERVISOR_MODEL`), parses a strict JSON report and ships it as plain text via Telegram. Observation + suggestions only — never writes to the bot. Zero import from `analysis/bot/*` for runtime isolation. Configuration lives entirely in `.env`: `ANTHROPIC_API_KEY`, `SUPERVISOR_MODEL`, `SUPERVISOR_ENABLED`. Audit log in the `events` table (`event = 'SUPERVISOR_REPORT'`). Report language is French, focused on the live bot. Cost measured in practice: ~$0.017/run with prompt cache hit → ~$0.50/month at one run per day. Crontab entry installed:
  ```
  0 8 * * * /home/crypto/.venv/bin/python3 /home/crypto/supervisor.py >> /home/crypto/analysis/output/supervisor.log 2>&1
  ```

### Fixed
- Dashboard silently stopped refreshing any card after the v11.3.4 health card was added. Root cause: variable-name collision in the update loop caused a SyntaxError at script load time. All conflicting variables renamed.
- `supervisor.py` Telegram delivery: the original helper only checked HTTP status; Telegram returns `HTTP 200` with `ok: false` in the JSON body when Markdown parsing fails. Messages were accepted by the socket, logged as "Telegram sent", and never delivered. Helper now parses the response body, checks `ok == true`, and logs `description` on failure. Report format switched to pure plain text (no `parse_mode`).

### Changed
- `supervisor.py`: `BOTS` list gained a `notes` free-form field per bot, propagated into the Claude prompt so the model knows the operational status of each instance. System prompt mandates French output for every textual field.

## [11.3.4] — 2026-04-11

### Changed
- Walk-forward filters applied to one of the signal families, with train/test splits on historical data. Kill-switches preserved for rollback.

### Added
- Tooling to fetch and process derivatives market history from Hyperliquid S3 data. Hourly-downsampled SQLite store kept gitignored.
- Exploratory backtests of derivatives-based entry gates — all rejected under strict walk-forward criteria.
- `backtests/backtest_rolling.py` refactored to expose a skip hook so future gate hypotheses can be plugged in without forking the engine.

## [11.3.3] — 2026-04-10

### Fixed
- Price charts could only display ~24h even though the API returned 72h of data. Root cause was LightweightCharts silently clamping due to `minBarSpacing` constraints on narrower containers.

### Changed
- `/api/chart` downsamples to 200 points (was 600) — plenty for visual inspection and safer for rendering across all container widths.
- Chart `timeScale` uses `minBarSpacing: 0.001` and `rightOffset: 0` so the full data range always fits regardless of viewport.
- Chart x-axis labels use an explicit `tickMarkFormatter` showing `JJ/MM HHh` on every tick so the date is never ambiguous.

## [11.3.2] — 2026-04-10

### Fixed
- `/api/chart` downsamples ticks via SQLite `GROUP BY` bucketing so 72h windows stay under ~600 points; LightweightCharts could previously only render ~1 day of the 3 days returned.
- `build_pnl_curve` crashed with `NameError` on every P&L chart request; function now takes `capital` as a parameter so the curve stays consistent after DCA.
- `api_reset` logged the wrong capital value after DCA injection.
- Trades table on the dashboard renders exit time in the local browser timezone instead of UTC.

### Changed
- `db.py` docstring and imports cleaned up; CSV migration paths now derived locally in `migrate_csv_to_db`.
- `load_trades` documents that it must only be called at startup.
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
- P&L calculation bug corrected (fix propagated to dependent safeguards).
- Position table now distinguishes notional from margin.
- NaN display on dashboard during startup before first price fetch.
- Position marker placement on chart after zoom/pan.
- Admin panel crash when a bot is offline (graceful fallback).
- Orphan positions on crash by persisting state immediately after opening.
- Race conditions on `_failed_closes`, `api_reset`, and reconcile dict access via `_pos_lock`.
- SQLite thread safety via `_db_lock` serializing all writes.
- State file corruption on serialization error (keeps existing state file on failure).
- HTTP retry with exponential backoff on price, candle, and DXY fetches.
- File lock (`fcntl`) preventing two bot instances on the same state file.

### Changed
- Base sizing and slot allocation tuned based on backtest results.
- Per-signal exit behavior tuned based on backtest results.
- Auto-restart via `@reboot` crontab running `start_bots.sh`.
