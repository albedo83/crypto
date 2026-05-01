# Changelog

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
