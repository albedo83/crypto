# Changelog

## [12.10.4] — 2026-05-31
- **Trading engine**: skips cooldown + already-in-position désormais loggés en SKIP events pour audit.

## [12.10.3] — 2026-05-30
- **Trading engine**: scan déclenché ~1-2 min après chaque candle close pour aligner le timing d'entrée sur le backtest (élimine le délai moyen ~30 min).

## [12.10.2] — 2026-05-30
- **Dashboard**: hotfix v12.10.1 — crash sur /api/state quand un scope perf-tracking est actif (sérialisation entry_time corrigée).

## [12.10.1] — 2026-05-30
- **Dashboard**: badge Strategy Performance scopable depuis une date custom (mirror du pattern fees window).

## [12.10.0] — 2026-05-30
- **Infra**: clôture de la série v12.9 (gate 4h, alerts hors gate, SDK patch, fees window, BT universe align).

## [12.9.7] — 2026-05-30
- **Infra**: alignement de l'univers backtest sur l'univers live (rattrapage v12.7.0 — 7 tokens manquaient au BT).

## [12.9.6] — 2026-05-30
- **Infra**: blindage du boot live contre les anomalies de metadata exchange.

## [12.9.5] — 2026-05-30
- **Telegram**: alertes giveback / lock-floor / WR / régime à nouveau évaluées chaque scan horaire (était gated par erreur sur la cadence 4h depuis v12.9.0).

## [12.9.4] — 2026-05-30
- **Dashboard**: rollback du regroupement v12.9.3, retour aux 10 cards plus lisibles.

## [12.9.3] — 2026-05-30
- **Dashboard**: 10 cards header consolidées en 5 pour gagner de la place sur mobile (P&L combinée, Activité combinée, Santé regroupe fees + S10).

## [12.9.2] — 2026-05-30
- **Dashboard**: badge fees configurable depuis une date custom + moyenne par jour.

## [12.9.1] — 2026-05-30
- **Infra**: timeout SDK relevé pour absorber les lenteurs intermittentes du compte exchange.

## [12.9.0] — 2026-05-30
- **Trading engine**: alignement des entrées sur la cadence naturelle de la stratégie pour mirror le backtest.

## [12.8.0] — 2026-05-28
- **Trading engine**: retrait du filtre d'entrée mean-reversion devenu redondant avec le mécanisme de cut introduit en v12.7.1.

## [12.7.14] — 2026-05-28
- **Telegram**: regime alert when cross-sectional 7d dispersion is elevated and recent win-rate on a tracked (strategy, direction) bucket is degraded. Observation-only, 24h cooldown.

## [12.7.13] — 2026-05-27
- **Position status badge** in dashboard cards — at-a-glance category for each open position. 5 tiers (priority order, one per position):
  - 🚨 **DANGER** — within 200 bps of catastrophe stop, urgent action
  - ⚡ **DECIDE** — actionable: GIVEBACK pattern fired / LOCK_FLOOR opportunity / pinned at MAE ≥ 4h. User decision recommended.
  - ⌛ **WAIT** — modest red, not at MAE, statistically +EV to hold (empirical 57% recover from -200/-500 bps zone)
  - 🟢 **PROFIT** — in profit, normal
  - 🕐 **EARLY** — < 1h held, too early to classify
  - Tooltip on hover explains the trigger condition. Computed in `web.py:build_state_response`, rendered in `reversal.html` m-header next to strategy.

## [12.7.12] — 2026-05-27
- **Dashboard button extended to all reconcile alerts**. Ghost / orphan / disputed / direction-mismatch / size-mismatch — all now carry the 📊 Dashboard button (`actionable=True`). Boot reconcile alerts (`main.py`) + hourly reconcile alerts (`exchange.py:reconcile`) covered. The 🔧 auto-sync alert stays informational (no user action needed — bot already corrected size_usdt).

## [12.7.11] — 2026-05-27
- **Dashboard button restored on actionable alerts**. v12.7.10 scoped to "daily" only was too restrictive — alerts that prompt user decision (GIVEBACK, LOCK_FLOOR, WR_ALERT, equity drift, PNL_DISCREPANCY, close/open failed) now carry the 📊 Dashboard button again. New `actionable=True` parameter on `send_telegram` attaches the button regardless of category. Informational messages (OPEN, CLOSE, daily summary already has button via category) stay clean. Tagged actionable: 3 alert types in `bot.py` (giveback, lock_floor, wr_alert) + 1 in `bot.py` (equity drift) + 3 in `trading.py` (PNL_DISCREPANCY, close failed, open failed).

## [12.7.10] — 2026-05-27
- **Dashboard button scoped to daily summary only** (category="daily"). v12.7.9 attached it to every Telegram message — too noisy. Trade alerts (OPEN/CLOSE), reconcile alerts, security alerts, etc. stay clean. Daily digest keeps the tappable button as it's the natural moment to check the dashboard.

## [12.7.9] — 2026-05-27
- **Telegram URL is now an inline-keyboard button** (`📊 Dashboard`) instead of plain-text URL appended to the body. Message text stays clean, button below opens the dashboard in one tap. Same URL, same security (`url` field of inline button can only OPEN a URL — Telegram refuses any action). Uses Bot API `reply_markup` field — no `parse_mode` change, so existing message content (underscores, asterisks, special chars in symbols) remains safe.

## [12.7.8] — 2026-05-27
- **Dashboard URL appended to ALL Telegram messages** (centralized in `net.send_telegram`), not just GIVEBACK/LOCK_FLOOR. Every notification (OPEN, CLOSE, daily summary, reconcile alert, capital adjusted, login attempts, drift, etc.) now includes `👉 https://echonym.fr/bot/` (live) or `👉 https://echonym.fr/junior/` (junior) — tap from notification → dashboard, mobile autofills password. Dedup if msg already contains the URL (v12.7.7 explicit appends in `bot.py` removed).
- **Security audit** : URL is base path only (no auth token, no query param, no secret). Landing = `/login`, all mutations are POST (auth-required). Same attack surface as posting the domain publicly. Telegram link-preview bot fetches the login page (no leak). Password remains the only gatekeeper.

## [12.7.7] — 2026-05-27
- **Telegram alerts now include a tappable dashboard URL**. New env var `BOT_PUBLIC_URL` (e.g., `https://echonym.fr/bot` for live, `https://echonym.fr/junior` for junior) is appended to GIVEBACK and LOCK_FLOOR alert messages: tap from notification → jumps to dashboard (login page, mobile autofills password). Empty `BOT_PUBLIC_URL` = no URL appended (back-compat). Configured per instance in `start_bots.sh`.

## [12.7.6] — 2026-05-27
- **Telegram alerts on open positions** — informational, no trading action. Two new alert types in `bot.py` (mirroring the `_check_wr_alerts` pattern from v12.4.0):
  - **GIVEBACK_ALERT** (S5 by default): fires once per position when `mfe_bps >= 500` AND `cur_bps <= -100` AND `time_since_mfe >= 4h`. Catches the NEAR/WLD/GALA/LDO pattern (April-May 2026 live, user manual_close +$28 net vs counterfactual). 4 R&Ds rejected mechanical exits on this pattern walk-forward (`backtest_s5_trailing`, `backtest_giveback`, `backtest_early_mfe_exit`, `backtest_s5_trail_bear`) — runners statistically identical to rollers at decision moment. Bot detects + alerts; user decides + acts.
  - **LOCK_FLOOR_ALERT** (all strategies): fires once per position when substantial unrealized profit accumulated (`unrealized_pnl >= $20` OR `unrealized_bps >= 600`), held ≥ 4h, no `manual_stop_usdt` already set. Suggests a concrete floor value (`cur_pnl - $5 buffer`) the user can apply via dashboard 🎯 or `POST /api/manual_stop/{sym}`. Pre-empts giveback without trailing's runner-amputation issue. Suppressed if user already set a manual_stop.
  - Both alerts dedup per position (set cleared on close). DB events `GIVEBACK_ALERT` / `LOCK_FLOOR_ALERT` for audit. Kill-switches: empty `GIVEBACK_ALERT_STRATEGIES` / `LOCK_FLOOR_ALERT_STRATEGIES` in `config.py`.

## [12.7.5] — 2026-05-26
- **Security**: refuse to start when admin password is empty; reject DCA withdrawal exceeding current capital.
- **Trading engine**: more accurate drawdown baseline after capital adjustments; internal cleanup.

## [12.7.4] — 2026-05-25
- **Admin**: aligned equity and P&L calculations with the per-bot dashboard. Equity now subtracts the estimated close-side fees (~9 bps × open notional) and P&L card uses `ea.equity − capital` (= realized + unrealized) instead of realized-only `total_pnl`. Eliminates the prior $7-ish equity gap and the misleading "+0%" P&L row.

## [12.7.3] — 2026-05-25
- **Trading engine**: Junior capital cap raised from $300 to $500 — DCA window now wider on the sub-account.

## [12.7.2] — 2026-05-25
- **Dashboard**: capital display next to DCA button — shows `$current / $max` on Junior (capped) and `$current` on Live/Paper (uncapped). New `capital_cap` field exposed in `/api/state`.

## [12.7.1] — 2026-05-20
- **Trajectory cut — regime-conditioned mid-trade exit** (S5 only). Codifies the user's manual_close intuition: cut a position whose curve is in steep decline from MFE, currently pinned near MAE, meaningfully losing, AND we're in a bear macro regime (`btc_z_30d < -0.5`). Discovery: live April-May 2026 user's manual_close on 6 trades saved 3 catastrophe_stops (+$28 vs counterfactual). v1 unconditioned variant failed walk-forward 1/4 — cut too many recoverable positions in choppy/bull. v2 regime-conditioned R1 PASSES strict 4/4 on `backtest_trajectory_cut_v2.py`: 28m +177 043pp, 12m +1 440pp, 6m +20pp, 3m +16pp, ΔDD avg +2.15pp DD improvement. 36 fires over 28m, all in bear regime. Null-shuffle on 13 trials: 0/13 random shuffles beat real (p<0.08, GENUINE). Rule placed in `trading.check_exits` between `s8_inlife` and `dead_timeout`. New exit reason: `traj_cut`. New `Position.mfe_at_h` field tracks when MFE was last set (persisted; default 0 = MFE at entry). Kill-switch: empty `TRAJ_CUT_STRATEGIES = set()` in `config.py`. Source: `backtests/backtest_trajectory_cut.py` (v1 failed), `backtests/backtest_trajectory_cut_v2.py` (R1/R2 GREEN), `backtests/backtest_trajectory_cut_r2_stability.py` (null-shuffle GENUINE).

## [12.7.0] — 2026-05-16
- **Trading engine**: universe expanded to 35 tokens (+6 curated additions). 2 new sectors created.

## [12.6.3] — 2026-05-16
- **Trading engine**: macro-slot allocation raised after walk-forward 4/4 strict sweep.

## [12.6.2] — 2026-05-16
- **Infra**: hotfix — `load_state` now exposes the persisted realign offset to startup so the drift check no longer spurious-warns post-realign.

## [12.6.1] — 2026-05-16
- **Infra**: equity realign tool to align `_total_pnl` to exchange truth and silence stale drift alerts.

## [12.6.0] — 2026-05-15
- **Trading engine**: new dead-in-water exit on amorphous S8 positions.

## [12.5.36] — 2026-05-15
- **Dashboard**: Open Positions rendered as cards on every screen size, laid out in a responsive grid (1 column on narrow, 2-3+ columns on wider screens). The 13-column desktop table view was retired.

## [12.5.35] — 2026-05-15
- **Dashboard**: price-chart now resizes its canvas to the container height — fixes mobile clipping the lowest values when the canvas overflowed the 200px container.

## [12.5.34] — 2026-05-15
- **Dashboard**: tighter Y-axis margins on the price chart so the lowest prices stop hiding at the bottom border.

## [12.5.33] — 2026-05-15
- **Dashboard**: mobile sparkline now fills the price zone; Trade History rows tinted by win/loss for faster scanning.

## [12.5.32] — 2026-05-15
- **Dashboard**: softer color palette on the hold-progress bar; mobile card sticks the win-prob emoji next to the P&L; larger price sparkline on mobile.
- **Dashboard**: Trade History column order — P&L now sits right after Side.

## [12.5.31] — 2026-05-15
- **Infra**: boot reconcile and SDK executor hardened against sustained Hyperliquid outages.
- **Security**: rate-limit memory accounting bounded to prevent slow growth.

## [12.5.30] — 2026-05-14
- **Trading engine**: new regime-aware in-life exit on S8 positions.
- **Dashboard**: P&L sign fix on the mobile card (negative P&L was displaying as positive).

## [12.5.29] — 2026-05-13
Hardening pass on bugs surfaced by a multi-agent code review. No strategy or sizing changes; only correctness/safety fixes. Backtests unaffected.
- **`avgPx > 0` validation on live order responses** (`exchange.py`). Previously a malformed `filled` block with `"avgPx": "0"` was cast to 0.0 and returned silently; downstream code booked it as `entry_price = 0` (divide-by-zero in `check_exits`) or `exit_price = 0` (synthetic `-10000 bps` gross). Both `execute_open` and `execute_close` now fall through to the `user_fills_by_time` lookup when the response is invalid.
- **Manual stop now compares NET P&L** (after `COST_BPS`) to the user-stated `stop_usdt`. The v12.5.25 check used gross unrealized × notional which over-shot by ~`COST_BPS` in dollars — a $40 stop locked $40 gross but only ~$39.50 net. The synthetic exit price reproduces the user's net dollar target precisely. Validation in `/api/manual_stop` aligned.
- **`send_telegram` now verifies `ok:true`** in the response body (`net.py`). urlopen returns 200 even when Telegram rejects the message (429 rate-limit, bad chat_id, markdown breakage). Silently losing alerts on rate-limits or template breakage was a regression introduced when the `urlopen(...)` return value stopped being read; now logged at WARNING with the API's `description` field.
- **Boot reconcile: two-attempt confirmation** (`main.py`). Previously a single transient HL response (cache lag, partial data, network glitch) was enough to silently drop a real position from `bot.positions`, leaving it open on the exchange outside the bot's stop-loss management. Now a position is dropped only if BOTH consecutive `user_state()` calls confirm its absence. Symbols missing from only one of the two attempts are flagged `DISPUTED` and KEPT.
- **Web API hardening** (`web.py`):
  - JSON body parse errors return 400 (not 500 stacktrace) on `/api/manual_stop` and `/api/capital`.
  - NaN/inf rejected on `stop_usdt` and `amount` (would otherwise corrupt `pos.manual_stop_usdt` / `bot._capital` and propagate to every P&L).
  - Symbol whitelisted against `TRADE_SYMBOLS` before logging on `/api/manual_stop` (log forgery protection).
  - `/logout` bumps a server-side revocation epoch — sessions issued before the epoch are invalidated. Without this, `delete_cookie` was client-side only and a stolen cookie remained valid for 30 days.
  - Per-IP rate-limit (30 / minute) on all mutating endpoints (POST/PUT/DELETE/PATCH). Prevents a stolen-cookie attacker from spamming `/api/reset`, `/api/close`, or `/api/capital`.
- **SDK timeout cap on Hyperliquid calls** (`exchange.py`). `market_open`, `market_close`, `user_state`, `user_fills_by_time`, `user_funding_history`, `spot_user_state` are now wrapped via `_sdk_call(..., timeout=)` (10-20s depending on call). The HL Python SDK doesn't expose timeouts on order calls — a hung HTTP request previously stalled `close_position` indefinitely with the `_closing` mutex held, locking the symbol out of management until reboot. Implementation uses `ThreadPoolExecutor.submit` + `future.result(timeout=)`; a global lock was deliberately NOT added (one hung call would paralyze all subsequent SDK ops). If torn-response races surface under load, a lock can be added later.

## [12.5.28] — 2026-05-13
- **Dashboard tech-English labels** (no more French on the UI):
  - "Si je ferme tout" → "Liquidation value"
  - "Frais exchange" → "Exchange fees"
  - Hold-progress bar: "X écoulé / Y total" → "X elapsed / Y total"
- **Reconciliation footer is now always visible** below the Open Positions section, not behind the strategy-performance toggle. It's part of the page baseline like Open Positions itself, not optional. Hidden only when there's no exchange_account (paper mode or pre-first-fetch).

## [12.5.27] — 2026-05-13
- **Dashboard: rename "Equity" card → "Si je ferme tout"**. The headline number now subtracts estimated close-side taker fees (`Σ size × 9 bps`) so it represents the actual liquidation value — what the user would receive in USDC if all positions closed at current price right now. Tooltip explains the math.
- **Reconciliation footer reorganized** to make "Si je ferme tout" the primary line, with the close-fees subtraction visible. HL raw equity is now demoted to an audit-only line at the bottom (smaller, dimmed) with a Δ vs bot to flag transient HL desync.

## [12.5.26] — 2026-05-13
- **Per-trade P&L coherence check** in `trading.close_position` (live mode). After computing the recorded `pnl`, compares against the coin-based ground truth (`coins × (exit_price − entry_price) × dir`) plus the expected fee / funding adjustments. If the discrepancy exceeds $1 OR 5% of expected P&L, logs a `PNL_DISCREPANCY` event in the events DB AND sends a Telegram alert (category `reconcile`). Catches future regressions where `size_usdt` or P&L math gets accidentally modified.
- **Mobile cards layout**: 9 visible cards (Equity, Drawdown, Total P&L, Unrealized, Positions, Trades, Total, Fees, S10 30d) → **3-column grid** (3 lignes). Uniform sizing, Utilization stays hidden.
- **Reconciliation footer clarification**: relabeled "Real equity (HL)" → "HL équity (raw)" and explicitly distinguishes from the dashboard Equity card (which since v12.5.13 shows the deterministic bot-internal equity). Previously the footer claimed both were the same — they no longer are by design.

## [12.5.25] — 2026-05-13
- **Accounting BUGFIX (P&L over-recording for winners)**: at close, `trading.close_position` used to overwrite `pos.size_usdt` with `sz × close_price` (the close-time notional). Since P&L uses `pos.size_usdt × net_bps / 1e4` and size_usdt represents the OPEN notional, the overwrite inflated winners and shrank losers by `(close/open − 1)`. Example surfaced on INJ today: stored P&L $47.56 vs real P&L ~$40. The reconcile now compares COIN count (not notional) and only scales `size_usdt` proportionally if the fill actually differs in coin quantity (partial fill case). Affects all live trades; backtest math was already correct (uses open notional).
- **Manual stop: dollar threshold, not bps** — `Position.manual_stop_usdt` field added as the source of truth. `trading.check_exits` compares current `pnl_usdt = size_usdt × unrealized_bps / 1e4` against `manual_stop_usdt` directly. The user's "stop at $40" now locks exactly $40 regardless of how the notional grew over the hold (previous behavior with the bps-based check could over- or under-lock). `manual_stop_bps` remains exposed for backward compat in `/api/state`.
- **Dashboard mobile cards (≤640px)**: uniform 2-column grid for ALL top stat cards (Equity, Drawdown, Total P&L, Unrealized, Positions, Trades, Total, Fees, S10) — no more full-width-then-grid mix. `Utilization` card hidden on mobile (`.card-util{display:none}` in the mobile media block).
- **Dashboard trade history → compact table** with sticky header in a scrollable container (max-height 520px). Limited to the **20 most recent trades** instead of the full list. Replaces the prior responsive table that converted to vertical cards on mobile.
- **Hold-progress bar label clarified** from `"24h / 48h"` (ambiguous) to `"24h écoulé / 48h total"`.

## [12.5.24] — 2026-05-13
- **Dashboard mobile**: fix the v12.5.23 top-cards layout. The legacy `@media(max-width:420px){.cards{grid-template-columns:1fr}}` rule was overriding the new 3-col grid on most phones (≤420px covers iPhone SE/12/13/14 widths). Replaced with a `@media(max-width:380px)` 2-col fallback for genuinely narrow viewports. Standard phones (390-414px) now render the intended 3-col grid for the small stat cards (Total P&L, Unrealized, Positions, Trades, Total, Utilization, Fees, S10) with Equity full-width above.

## [12.5.23] — 2026-05-13
- **Dashboard mobile top-bar hierarchy** (≤640px). The Equity card is now full-width, prominent (font-size 30px, gradient background, subtle shadow) — it's the headline number. The Drawdown card stays full-width on row 2 (still important context). The other 8 cards (Total P&L, Unrealized, Positions, Trades, Total, Utilization, Fees, S10 30d) shrink into a 3-column compact grid below with smaller fonts (label 9px, value 13px) — visible but discreet. Frees vertical space for what matters: scanning the equity at a glance and getting straight to the open positions section.

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
