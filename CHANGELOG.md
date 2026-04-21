# Changelog

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
