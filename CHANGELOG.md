# Changelog

## [12.5.22] — 2026-05-13
- **Dashboard mobile open-positions: restore position / margin / entry info pills** (≤640px). The v12.5.21 mobile card rebuild dropped these three values (they used to be inside the desktop expanded detail rows on the prior layout). Re-added as additional labeled pills in the `m-info` row: `pos $230`, `mgn $115`, `entry $4.8751`, followed by the existing `held / rest / stop / mae / mfe`. Wrap to a second line if narrow.

## [12.5.21] — 2026-05-13
- **Dashboard mobile open-positions: true iOS-style card** (≤640px). The JS render now branches on `matchMedia('(max-width:640px)')` and emits a single `<td colspan="13" class="m-card-wrap">` containing a free-form `.m-card` div for the mobile case — completely independent of the table's column-based positioning that was being fought via CSS in prior iterations. Layout: header (Symbol · Side · Strat) + right-aligned P&L (with bps inline + optional 🎯 stop badge) → full-width hold progress bar → 3-cell mid section (sparkline+price 36% | MAE/MFE strip flex | 🎯/✕ buttons stacked vertically) → labeled info pills (held/rest/stop/mae/mfe) on a single wrapping line. All visible sections render reliably regardless of viewport since they're not bound to table column widths. Desktop layout (≥641px) untouched.

## [12.5.20] — 2026-05-13
- **Dashboard mobile open-positions: 3-row card** (≤640px).
  - Row 1: identity (Symbol · Side · Strat) + P&L (with bps inline + optional 🎯 stop badge), all on one line.
  - Row 2: shared row with three elements — **sparkline (with current price below)** at left ~35%, **positionStrip MAE/MFE** in the middle, and the **two action buttons stacked vertically** (🎯 on top, ✕ below) at the right.
  - Row 3: textual info on one line with small uppercase labels — `held 24.4h`, `rest 23h36m`, `stop -1250`, `mae -575`, `mfe +2009`. Replaces the visual hold-progress bar on mobile (which moved to desktop-only) and consolidates the stop-meta text.
- Desktop layout (≥641px) unchanged.

## [12.5.19] — 2026-05-13
- **Dashboard mobile open-positions: sparkline + stop-info share a row** (≤640px). The price sparkline (with the current price label moved BELOW it instead of beside) now takes ~42% of the card width on the left; the stop info (positionStrip + stop/MAE meta) takes the remaining width on the right — same row. Trims another ~30px of card height. Desktop layout untouched.

## [12.5.18] — 2026-05-13
- **Dashboard mobile open-positions: space-optimized layout** (≤640px). Sharing rows for compactness: (a) the two action buttons (🎯, ✕) now sit **next to** the hold progress bar (same row, right corner) instead of a dedicated row — saves ~40px height. (b) The price sparkline and the current-price label are now on the **same row** (sparkline left at ~65% width, price right-aligned). P&L row stays compact with bps inline AND 🎯 stop badge inline (instead of the badge wrapping to a second line). Resulting card ~25% shorter than v12.5.17. Desktop layout untouched.

## [12.5.17] — 2026-05-13
- **Dashboard mobile open-positions: true card design** (≤640px). The `<tr>` is now a real card with rounded corners, padding, subtle shadow, and color-tinted border on profit/loss — instead of the prior grid-of-columns that mimicked a table. Header strip (Symbol big · Side pill · Strat) + right-aligned big P&L (with bps inline and optional 🎯 stop badge). Subsequent rows below: hold progress bar, **price sparkline + current price** label (sparkline was hidden in v12.5.16, now restored on mobile per user request), **positionStrip restored** (MAE/MFE dots + entry baseline + current line — the visualization that was removed in v12.5.15), and a compact action row with two **icon-only buttons** (🎯 set/edit stop, ✕ close) right-aligned, ~44px square each — far less space than the prior 50/50 split. Desktop layout untouched.

## [12.5.16] — 2026-05-13
- **Dashboard mobile open-positions redesign** (≤640px): 4-row layout per card. Row 1 = symbol/side/strat + P&L (with bps inline AND a 🎯 badge showing the active manual-stop value if set, addresses "active stop next to P&L"). Row 2 = full-width hold-progress bar with held/total label. Row 3 = compact stop info (stopBar + stop/MAE/trail text). Row 4 = 50/50 split big tap-targets: **🎯 Stop** (yellow, opens prompt) and **✕ Close** (red). Button padding 10px (was 3px), font 13px (was 11px) — meets Apple HIG 44pt tap target. Caret moved from absolute-positioned right of row to a `▾`/`▴` next to the symbol. The duplicate "🎯 manual stop @ $X" line in the stop-meta block is now hidden on mobile (the badge in row 1 is the canonical display). Desktop layout untouched.

## [12.5.15] — 2026-05-13
- **Dashboard**: removed the dense `positionStrip` SVG (the strip in the Unrealized cell with two MAE/MFE dots, an entry baseline, a current-price line and a trailing dashed marker — too much information packed into ~20px). Fall back to the simple `stopBar` (horizontal distance-to-stop indicator). Reverted v12.5.14's removal of the Path (price) sparkline column — that wasn't the confusing one.

## [12.5.14] — 2026-05-13
- **Dashboard**: removed the "Path (price)" sparkline column from the open-positions table. The tiny price sparkline next to the Remaining-time progress bar was visually noisy and hard to interpret at the size shown. Header column and per-row cell both removed. The `sparkline()` JS helper stays available for potential reuse elsewhere.

## [12.5.13] — 2026-05-13
- **Dashboard equity card switched to deterministic formula**. The "Equity" card now displays `capital + realized + sum(positions.unrealized_at_current_price)` — computed at every `/api/state` request from the latest market prices. Replaces the previous `bot._exchange_account.equity` which depends on HL's two info APIs (`user_state` + `spot_user_state`) and can be transiently incorrect when those APIs return desynchronised data. The internal formula is stable, doesn't depend on HL data races, and updates in real-time as prices move (5s dashboard refresh × per-position price snapshot). HL equity is still computed in the background (10s refresh, down from 15s in v12.5.12) and exposed as `s.hl_equity` in /api/state for cross-checking by the drift-alert system.

## [12.5.12] — 2026-05-13
- **Live exchange refresh**: dedicated 15s loop refreshes the displayed Equity, Unrealized, Margin and Available fields. Only the cheap `user_state` + `spot_user_state` calls run on this fast path; the expensive `user_fills_by_time` + `user_funding_history` calls (taker fees, funding paid, closed PnL) stay on the 60s main loop. Eliminates the ±$15-30 jumps users saw at restart when a stale 60s-old `spot.hold` value snapped to fresh. New helper `exchange.fetch_equity_only()`; new method `bot.equity_refresh_loop()`; new task spawned in `main.py` for live bots only (paper / Junior-with-no-key skip it).

## [12.5.11] — 2026-05-13
- **Dashboard**: redesign of the per-position "remaining time" indicator. The thin 3px hold bar is replaced by a 14px progress bar with a color gradient (blue → yellow → red as the position nears timeout) and the elapsed/total hours displayed inside (e.g. "23.6h / 48h"). The redundant "held Xh" subtitle is removed; the "X restant" hint moves below the bar in dim style. Pure visual change — no API or strategy logic touched.

## [12.5.10] — 2026-05-13
- **Trading engine**: per-position manual stop. New field `Position.manual_stop_bps` (Optional). When set, `trading.check_exits` closes the position with reason `manual_stop_set` as soon as unrealized falls at or below the threshold — checked right after the catastrophe stop, before strategy-specific exits (S9 early, S10 trailing, dead_timeout). Persisted across restarts via `persistence.save_state`/`load_state`.
- **Web API**: new endpoint `POST /api/manual_stop/{symbol}` accepting `{"stop_usdt": X}` (set) or `{"clear": true}` (remove). Validates that the threshold is strictly between the catastrophe stop and the current unrealized — rejects redundant or self-triggering values. `/api/state` now exposes `manual_stop_bps` and `manual_stop_usdt` per position.
- **Dashboard**: each position row gets a 🎯 button next to Close that opens a prompt to set/clear the stop in dollars. Active stop shown inline in the existing stop-meta block ("🎯 manual stop @ $40 (+1739 bps)"). Mobile layout pushes both UI bits into the compact close cell; no other change.
- **Why**: gives the user a guardrail to lock partial gains on outlier winners (e.g. an S5 at +$45 unrealized) without forcing an immediate full close. Manual override only — strategy logic and backtest results are unchanged.

## [12.5.9] — 2026-05-12
- **Dashboard**: regime badge now reflects the same rolling z-score the adaptive modulator already uses for sizing — label and trading logic finally agree instead of giving "CHOPPY" while the bot actively trades a bull regime.

## [12.5.8] — 2026-05-12
- **Trading engine**: structurally weak short directions are now sized down much more aggressively in the regimes where they historically bleed — walk-forward validated, drawdown unchanged.

## [12.5.7] — 2026-05-12
- **Dashboard (bug fix)**: fresh positions (< 2h) now correctly show the hourglass marker even when slightly in profit — the v12.5.6 "hide on profit" check was firing before the freshness check.

## [12.5.6] — 2026-05-11
- **Dashboard**: open-position "Path" column now plots price instead of P&L, more intuitive to read against the live ticker. Win-probability smiley is hidden on positions currently in profit — the indicator now only surfaces when there is something to worry about.

## [12.5.5] — 2026-05-11
- **Dashboard**: win probability estimator no longer over-pessimises positions that have already recovered from a deep adverse excursion, and ignores tier-1 token samples that are too thin to be statistically meaningful (e.g. 3 prior trades all losing).

## [12.5.4] — 2026-05-11
- **Telegram (bug fix)**: WR drift alarm no longer fires on positions that are already profitable or have shown strong mean-reversion — was sending misleading "consider manual close" pings on winning trades whose historical pattern was thin (e.g. n=3 all losers).

## [12.5.3] — 2026-05-11
- **Trading engine**: scan orchestrator slimmed (per-token signal loop and ETH observation extracted to dedicated methods); close path no longer hangs if the exchange funding endpoint slows down (5s timeout, fail-open preserved).
- **Infra**: shared close helper consolidates the manual-close / pause flows around a single success/failure signal.

## [12.5.2] — 2026-05-11
- **Trading engine**: internal refactor — extraction of read-only analytics into its own module, shared skip-reason helper between scan and dashboard preview, removal of dead kill-switch toggles and the one-time CSV migration. No behavior change.
- **Infra**: shared SQLite write-lock moved to a dedicated concurrency module.

## [12.5.1] — 2026-05-11
- **Dashboard (bug fix)**: `/api/state` crashed since v12.3.0 because the win probability estimator referenced a field that doesn't exist on open positions — dashboard now loads again.

## [12.5.0] — 2026-05-10
- **Trading engine**: dead-trade exit threshold tightened so the bot crystallizes losses on pinned positions earlier, walk-forward validated.

## [12.4.0] — 2026-05-10
- **Telegram**: alerts when an open position's estimated win probability drifts into the alarm zone — gives the user a chance to act before the trade goes catastrophic.
- **Dashboard**: win probability lookback narrowed to recent history so older market regimes don't pollute the estimate.

## [12.3.2] — 2026-05-10
- **Dashboard**: win probability estimator now mutes early-hold MAE noise — fresh positions show base WR with an hourglass marker until they have enough maturity to interpret reliably.

## [12.3.1] — 2026-05-10
- **Dashboard**: open-position win probability now displayed with a smiley for at-a-glance read.

## [12.3.0] — 2026-05-10
- **Dashboard**: each open position now shows an estimated win probability based on historical patterns and current MAE/MFE — helps distinguish noise from real concern at a glance.

## [12.2.0] — 2026-05-10
- **Trading engine**: per-direction adaptive sizing replaces the static directional blacklist introduced in v12.1.0 — broader regime coverage, walk-forward validated.

## [12.1.0] — 2026-05-09
- **Trading engine**: per-strategy directional blacklist applied to a small set of historically losing patterns, walk-forward validated.
- **Infra**: drift monitor cadence tightened from monthly to weekly to react faster to pattern shifts.

## [12.0.0] — 2026-05-09
- **Infra**: monthly drift monitor analyzes trade history and flags pattern shifts via Telegram, no auto-action — informs manual parameter review.

## [11.10.2] — 2026-05-08
- **Trading engine (bug fix)**: macro modulator was inactive in live because the candle history loaded into memory was too short — fetch and retain enough history so the rolling window can compute.

## [11.10.1] — 2026-05-08
- **Trading engine**: cleaner skip event when adaptive sizing falls under the live exchange minimum, audit context now persisted on every entry.
- **Infra**: removed unused legacy env var from launch script.

## [11.10.0] — 2026-05-08
- **Trading engine**: per-strategy adaptive sizing now scales selected signals by a rolling macro modulator, walk-forward strict + sliding out-of-sample validated.

## [11.9.2] — 2026-05-08
- **Trading engine**: S5 position sizing increased to compensate for partial fills observed at the live slippage cap, walk-forward validated.

## [11.9.1] — 2026-05-08
- **Dashboard (bug fix)**: live equity card was inflated by the unrealized P&L of open positions because the legacy formula double-counted spot collateral; replaced with a unified formula that gives the correct mark-to-market value for both wallet topologies.

## [11.9.0] — 2026-05-07
- **Trading engine**: universe expanded with one new L1 token, walk-forward validated.

## [11.8.5] — 2026-05-07
- **Trading engine + Dashboard**: every position open/close now writes a structured event for the dashboard timeline and audit queries; reconcile auto-corrects the tracked size when the bot diverges from the exchange instead of alerting indefinitely.
- **Infra**: supervisor compares the live bot's run-since-deployment against the matching backtest window and flags persistent divergence in the daily report.

## [11.8.4] — 2026-05-06
- **Trading engine (bug fix)**: live order placement now reads the actual filled quantity from the exchange response instead of the requested size — partial fills no longer inflate tracked notional and stop generating recurring size-mismatch alerts.

## [11.8.3] — 2026-05-04
- **Dashboard (bug fix)**: trade history endpoint returned 500 after the first new trade close post-restart — numpy-typed entry context now coerced to native types at the source and at the Position boundary.

## [11.8.2] — 2026-05-03
- **Trading engine**: persist state immediately when the conditional hold rule fires so a crash before the next scan-loop save can't drop the marker.
- **Dashboard**: sanitizer for the Backtests modal now drops the strat-attribution column by header position instead of by content match — robust to future strategy names.

## [11.8.1] — 2026-05-01
- **Trading engine (bug fix)**: persisted state retains the new strategy marker across restarts; live and backtest exit-check ordering re-aligned.
- **Dashboard**: Backtests modal renders markdown tables.

## [11.8.0] — 2026-05-01
- **Trading engine**: strategy update.

## [11.7.33] — 2026-05-01
- **Dashboard**: new "Backtests" button next to "Release notes" — opens a modal with the sanitized rolling-window summary.

## [11.7.32] — 2026-05-01
- **Trading engine**: strategy update.

## [11.7.31] — 2026-05-01
- **Admin**: Junior bot now correctly shows the LIVE badge — admin reads each bot's actual execution mode from its `/api/state` (with `admin_config.json` as offline fallback) instead of trusting the static config alone.

## [11.7.30] — 2026-05-01
- **Admin**: STOP / RESUME / RAZ buttons moved off the per-bot cards into a dedicated "Controles" screen at the end of the carousel (with a warning banner), so they can't be tapped by accident while scanning bots.

## [11.7.29] — 2026-05-01
- **Admin**: fix the "Open" button on mobile — pre-open the tab synchronously in the click handler so mobile popup blockers don't reject the post-fetch redirect.

## [11.7.28] — 2026-05-01
- **Trading engine**: new entry filter for mean-reversion strategies during regime breakdowns — walk-forward validated 4/4 with no DD penalty, fires on rare extreme events (~6× per year).

## [11.7.27] — 2026-04-30
- **Trading engine (bug fix)**: equity drift comparator now subtracts taker fees from the exchange-side realized total — the previous formula compared the bot's net (cost-deducted) P&L with the exchange's gross (pre-fees) P&L and fired spurious EQUITY_DRIFT alerts on otherwise-aligned books.

## [11.7.26] — 2026-04-30
- **Admin**: position rows now tinted green/red on win/loss for at-a-glance scanning, and the remaining-time column is bolder + color-coded (red in the last 6h, yellow in the last 12h).

## [11.7.25] — 2026-04-30
- **Admin**: mobile-first redesign — horizontal swipe carousel between bots with clickable tabs at top, each card embeds a compact open-positions list (with mini stop/MAE/current/MFE strip) plus richer stats; desktop keeps the multi-column grid layout.

## [11.7.24] — 2026-04-30
- **Dashboard**: P&L sparkline reference lines (entry baseline + current level) now actually visible against the dark background — bumped color contrast and opacity.

## [11.7.23] — 2026-04-30
- **Dashboard**: open-positions P&L sparkline now draws a faint colored horizontal line at the current level alongside the existing dashed entry baseline — drift-from-entry readable at a glance.

## [11.7.22] — 2026-04-30
- **Dashboard**: drop the "Alt (price)" sparkline column from open-positions — it duplicated the "Path (P&L)" sparkline (mathematically equivalent shape for LONG, mirror for SHORT).

## [11.7.21] — 2026-04-30
- **Dashboard**: open-positions row gets a visual stop/MAE/current/MFE/trailing strip; compact scan-preview pill bar above the table (always visible) showing what the bot would do on the next scan.
- **Trading engine**: equity drift, ghost/orphan boot reconcile, kill-switch and close-failed now write structured events to the events table — surfaces in the dashboard event-timeline alongside trades and SKIPs.

## [11.7.20] — 2026-04-30
- **Trading engine**: serialize concurrent close requests on the same symbol with a mutex (prevents duplicate exchange orders when timeouts and manual closes race), and reconcile at boot in live mode to drop ghost positions left from offline manual closes.
- **Infra (bug fixes)**: DCA now rebases the drawdown baseline so capital flows don't surface as drawdown on the Drawdown card; startup P&L sanity check now sums all trades (was filtering out manual stops and resets, producing false drift warnings).

## [11.7.19] — 2026-04-29
- **Dashboard**: new "Drawdown" card next to Equity. Shows current % from peak balance, color-coded (green at peak, dim for ≤−1%, yellow ≤−10%, red ≤−25%) with a horizontal progress bar capped at 50% for visual scale. Reads `s.drawdown_pct` and `s.peak_balance` already exposed by `/api/state`.
- **Persistence (bug fix)**: `load_state()` now returns the saved `capital` field, restoring `bot._capital` from the state file across restarts. Previously the env value (`HL_CAPITAL`) silently overrode the state, which caused DCA tracking to be lost on every restart — visible only on Junior (env=0) and on the live bot when env mismatched the state. Paper unaffected (env matched state).

## [11.7.18] — 2026-04-29
- **Junior bot live activation** with API agent wallet model. New env vars: `HL_ACCOUNT_ADDRESS` (master wallet that holds funds, when separate from the signer key) and `HL_EQUITY_MODE` (`"perps"` for the perps-only setup like Junior, default empty = legacy spot+unrealized for live). `analysis/bot/exchange.py:init_exchange()` now accepts an optional `account_address` parameter. Junior signs with API wallet `0x4EAb…3F7e` and trades on master `0xb65d…956Fe`. Live and Paper unchanged.
- **Dashboard**: open-positions table redesigned for mobile (≤640px). Each position becomes a 2-line compact card with caret expand for Entry/Current/Margin details; sparklines hidden on mobile; bps-inline shown next to P&L. CSS-only changes scoped via `[data-block="open-positions"]`, plus a click-delegation handler on `#pos-body` that toggles `tr.expanded` when `matchMedia(max-width:640px)`. Desktop layout untouched.

## [11.7.17] — 2026-04-29
- **Internal**: `init_exchange()` accepts an optional `account_address` parameter so the bot can sign with an API agent wallet but trade on a separate master wallet. Required for Junior, opt-in for live (defaults to wallet-from-key behavior).

## [11.7.16] — 2026-04-22
- **Trading engine**: `DEAD_TIMEOUT_MAE_FLOOR_BPS` tightened from −1000 → −800 bps. Stuck S5 losers pinned near their low are now crystallized ~200 bps sooner. Walk-forward: +$9.5k on 28m (S5 alone +$6k), minor noise (~−$350 cumulated) on 12m/6m/3m, DD unchanged or better.

## [11.7.15] — 2026-04-21
- **Dashboard**: open-positions column order tweaked (P&L moved next to Side).

## [11.7.14] — 2026-04-21
- **Dashboard**: open-positions rows now tinted green/red by unrealized P&L for at-a-glance scanning.

## [11.7.13] — 2026-04-21
- **Dashboard**: open-positions table now shows a second sparkline per row — raw alt price path alongside the existing P&L path (direction-agnostic, useful for SHORT positions whose P&L path is inverse to the alt chart).

## [11.7.12] — 2026-04-20
- **Infra**: Junior bot served via HTTPS subpath (nginx `/junior/`), aligned with Paper and Live.

## [11.7.11] — 2026-04-20
- **Admin + Dashboard**: STOP / RESUME / RAZ moved out of the bot dashboard into the admin panel (one per bot card).

## [11.7.10] — 2026-04-20
- **Security**: suppress Telegram login alerts from internal localhost calls (admin panel auto-auth noise).

## [11.7.9] — 2026-04-20
- **Telegram**: daily summary now mirrors the Equity + P&L cards with a per-position brief; colored squares on close notifications.

## [11.7.8] — 2026-04-20
- **Telegram**: `OPEN` / `CLOSE` labels in trade notifications for unambiguous entry vs exit.

## [11.7.7] — 2026-04-20
- **Infra**: supervisor prompt tightened (anti-reprise registry, no hallucinated figures); DB backfill scripts for funding + net_bps reconciliation.

## [11.7.6] — 2026-04-20
- **Infra**: backtests now use real hourly funding history per token (calibrated 100% vs live); replaces the flat estimate.

## [11.7.5] — 2026-04-20
- **Trading engine**: per-trade funding now deducted from each close (live), replacing the flat estimate.

## [11.7.4] — 2026-04-20
- **Dashboard**: Strategy Performance footer reconciles bot accounting with real exchange equity on live (explains the gap: real fees + funding + slippage).

## [11.7.3] — 2026-04-20
- **Dashboard**: Strategy Performance now ends with a reconciling Total row so the math adds up.

## [11.7.2] — 2026-04-19
- **Trading engine**: new exit safeguard for stale trades.

## [11.7.0] — 2026-04-18
- **Dashboard**: redesign with left sidebar, per-block toggles, mobile drawer, responsive card-mode tables.

## [11.6.2] — 2026-04-18
- **Trading engine**: audit fixes (stop fill price, filled-size tracking, concurrency).
- **Dashboard**: small heatmap, preview, and Telegram-flag fixes; layout tweaks.

## [11.6.0] — 2026-04-18
- **Dashboard**: Strategy Performance now shows lifetime + recent 20, with trend arrows and "show more" toggle.

## [11.5.0] — 2026-04-18
- **Dashboard**: new indicator batches — inline position bars, regime badge, sector grid, signal-proximity heatmap, capital-flow bars, event timeline, next-scan preview.
- **Admin**: auto-login fixed.

## [11.4.10] — 2026-04-17
- **Trading engine**: new entry filter.

## [11.4.9] — 2026-04-17
- **Trading engine**: new entry filter.

## [11.4.8] — 2026-04-17
- **Telegram**: Junior now also receives startup notifications.

## [11.4.7] — 2026-04-17
- **Telegram + Admin**: multi-instance identification (category filter, label prefix in messages, colored border in admin panel).

## [11.4.6] — 2026-04-17
- **Security**: login alerts, HTTP security headers, HMAC salt, exponential backoff on failed logins.

## [11.4.5] — 2026-04-17
- **Dashboard**: "Release notes" button + modal.

## [11.4.4] — 2026-04-17
- **Dashboard/Admin**: configurable bot label and color via env vars (used by Junior).

## [11.4.3] — 2026-04-17
- **Infra**: Junior bot now has its own dashboard credentials and Telegram identity; admin panel resolves per-bot auth.

## [11.4.2] — 2026-04-17
- **Trading engine**: reconcile size check no longer triggers false alerts on price moves.

## [11.4.1] — 2026-04-14
- **Trading engine**: enhanced reconciliation (direction mismatches), equity drift alert, startup P&L sanity check.

## [11.4.0] — 2026-04-13
- **Trading engine**: exit optimization on one signal family.

## [11.3.7] — 2026-04-13
- **Dashboard**: real equity on live (spot USDC + perps unrealized), fees card, P&L % formatting, bot renamed to Paper/Live/Junior.

## [11.3.6] — 2026-04-12
- **Dashboard**: replaced Drawdown card with P&L % card.

## [11.3.5] — 2026-04-11
- **Dashboard**: new strategy health card.
- **Infra**: LLM supervisor bot (daily cron, Telegram report, observation-only).

## [11.3.4] — 2026-04-11
- **Trading engine**: walk-forward filters on one signal family.

## [11.3.3] — 2026-04-10
- **Dashboard**: price chart fix (72h data now fully displayed).

## [11.3.2] — 2026-04-10
- **Dashboard**: chart downsampling, P&L curve fix, timezone, reset-log fix.
- **Infra**: cleanup.

## [11.3.1] — 2026-04-10
- **Infra**: SQLite-only persistence (CSV writes removed); bots/admin mountable behind nginx subpaths.

## [11.3.0] — 2026-04
- **Dashboard/Admin**: admin panel (:8090), DCA from dashboard, real Hyperliquid equity on live, per-position close, HTML login, HMAC sessions.
- **Trading engine**: P&L calculation fix and related safeguards re-tuned; sizing and slot allocation tuned from backtests.
- **Infra**: race-condition and state-persistence fixes; SQLite thread safety; file lock; HTTP retry with exponential backoff.
