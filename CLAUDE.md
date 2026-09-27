# CLAUDE.md

## Reprise du 2026-09-17 — périmètre utilisateur

Live est le seul bot officiel ; Paper est sa référence.
Consigne du 20 septembre : IA et veille web réservées à Live pour limiter les
coûts. Aucun appel IA pour Paper, Junior, Baby ou les portefeuilles expérimentaux. Junior/Baby sont hors
du périmètre d'analyse demandé. Capital engagé : 500 ; perte maximale acceptée :
250 (50 %). Priorité économique actualisée : couvrir progressivement les frais IA
annoncés de 100 EUR/mois ; en couvrir 50 % serait déjà utile. Les +250 de gain
personnel sur six mois deviennent un objectif ultérieur, pas une exigence.
Les 100 EUR/mois correspondent à l’abonnement assistant (confirmé). Les frais
API du bot et serveur s’ajoutent ; distinguer les coûts API estimés en USD ;
ne pas additionner EUR et USD sans conversion explicitée, ni doubler les coûts.
Précision utilisateur du 20 septembre 2026 : exclure les coûts API de
l'évaluation du moteur et de la décision d'activer une fonction IA. Comparer
le PNL de trading, le drawdown et la robustesse, IA ou non. Conserver les frais
de trading, le slippage et le funding dans les résultats. Les coûts API peuvent
rester journalisés, mais ne constituent ni une pénalité de performance ni un
motif de rejet. Cette précision remplace le critère de couverture des coûts API
dans les commentaires du rapport AI-SHADOW-20260920-v1 ; ses mesures historiques
restent inchangées. Ne pas en déduire une modification des règles Live ou une
activation immédiate de l'IA sans preuve de son apport au moteur.
L'utilisateur autorise les recherches et changements de moteur/backtests et
exige une contre-vérification systématique ; aucun rendement n'est promis.
Livraison 1.24.0 : expériences prospectives indépendantes (témoin,
S1 +24 h gagnant, plafond 3 par stratégie/sens), dashboard et correctif de borne
backtest. Voir `docs/experiments_shadow_v1.md`. Ne pas augmenter le risque
pour rattraper un objectif mensuel. « Restart » couvre tous les bots.
L'audit `audit_live_paper.py` ne calcule qu'une attribution comptable ; ne pas
transformer ses écarts en économies causales imputées à l'IA.


Livraison courante 1.26.1 : veille external-v2 par actif, domaines imposés,
extraction limitée aux textes sources sans résumé IA intermédiaire,
calendriers et annonces anciennes d’événements futurs traités séparément des
nouvelles récentes. Extrait exact exigé, précision journée conservée, erreurs
isolées et couverture visible. IA exclusivement Live, entrée/CUT/LOCK shadow ;
aucune rentabilité attribuable démontrée. Voir `docs/external_context_1_26_0.md`.
Les lacunes de la campagne shadow-v1 restent visibles ; ne jamais les effacer.
Consigne du 21 septembre : ne pas donner d’estimation du coût IA journalier.
Livraison 1.29.0 (22 septembre) : JEV (TypeSafe, `ai_jev.py`, clé
`TYPESAFE_API_KEY`, `AI_JEV_MODE=shadow|off`) juge en shadow, Live seulement,
les mêmes candidats/positions que les arbitres. Aucun mode d'action n'existe ;
scorecard contrefactuel dans /master (panneau ⚡ JEV) ou `python3 ai_jev.py`.
Promotion éventuelle = décision humaine, n ≥ 50 résolus et apport démontré.
Livraison 1.29.1 : TON retiré de l'univers (délisté HL, figé depuis le
2026-06-15) ; campagne d'expériences shadow-v3 (toute modif de botinstance.py,
settings.py, rules.py… gèle la campagne en cours). `docs/ton_delisting_2026_09.md`.
Vérifier `isDelisted` dans `metaAndAssetCtxs` avant d'accuser un « trou » de données.


Rule 1 — Think Before Coding.
No silent assumptions. State what you're assuming. Surface tradeoffs. Ask before guessing. Push back when a simpler approach exists.
Rule 2 — Simplicity First.
Minimum code that solves the problem. No speculative features. No abstractions for single-use code. If a senior engineer would call it overcomplicated — simplify.
Rule 3 — Surgical Changes.
Touch only what you must. Don't "improve" adjacent code, comments, or formatting. Don't refactor what isn't broken. Match existing style.
Rule 4 — Goal-Driven Execution.
Define success criteria. Loop until verified. Don't tell Claude what steps to follow, tell it what success looks like and let it iterate.


This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## ⚠ PROJET ALFRED — LA REFACTO EN COURS (lire en premier)

**Alfred (`alfred/`, web :8101, `python3 -m alfred`) est la refacto complète du bot
qui REMPLACE progressivement `analysis/bot/` + les 4 process legacy.**
Tout nouveau développement de trading se fait dans `alfred/`, PAS dans `analysis/bot/`
(legacy = maintenance minimale jusqu'à migration). Architecture : un seul process =
MarketDataMaster (1 connexion WS HL candle+trades pour tous les bots, REST résiduel,
snapshot marché horaire calculé 1×) + N BotInstances (max 8, config `alfred/bots.json`,
secrets = NOMS de variables .env) + web unifiée. Noyau de règles **partagé bot/backtest**
(`alfred/rules.py` — plus jamais de double implémentation d'une règle).

Phases : **1 ✓** noyau pur (iso-résultat BT 32/32 fenêtres, `backtests/compare_trade_dumps.py`) ·
**2 ✓** MarketDataMaster (observation, auto-audits CANDLE_AUDIT/GAP_REPAIR/WS_RECONNECT) ·
**3 ✓ (sans objet depuis le 2026-06-12)** paper $1000 en parallel-run vs legacy :8097 — gate
= 0 divergence INJUSTIFIÉE sur plusieurs jours. Doctrine : la référence est la cohérence
d'ALFRED, pas la duplication du legacy — une divergence justifiée par de meilleures données
est une amélioration, pas un bug. L'outil de cet audit a été **rebranché le 2026-09-27 sur
deux bots Alfred** (`python3 -m alfred.tools.compare_bots [--a live] [--b paper]`,
classification STATE/DATA/PREBOOT/CASCADE/MANUAL/HORS-FEN/ISO/LOGIC ; la saturation
de slots est attribuée en STATE car elle interrompt la boucle de candidats) : entre live
et paper la divergence est ATTENDUE (capitaux et slots différents), l'objet est de
l'ATTRIBUER, jamais de diagnostiquer par différence de soldes (`docs/bilan_2026_09.md` § 9).
La taille n'est pas un invariant — elle est comparée au ratio des soldes, ce qui a retrouvé
seul la décote IA d'entrée (28 entrées, 07-09 → 08-20, −15 à −50 % de notionnel, éteinte
depuis). **Verdict au 2026-09-27 sur les 35 j postérieurs : écart entièrement attribué,
0 divergence de noyau** ·
**4 ✓ LIVE MIGRÉ (2026-06-10)** : le live tourne dans Alfred (bot `live` de `bots.json`,
clé `HL_PRIVATE_KEY`, capital = equity $680.58, reset décidé par l'utilisateur sans attendre
la gate phase 3 — 0 position legacy à la bascule). **Legacy :8098 ARRÊTÉ**, bloc commenté
dans `start_bots.sh` — ne JAMAIS le relancer (même clé que Alfred live = double-trading +
conflits de nonce). supervisor.py/strategy_review.py lisent encore les DB legacy live (à porter) ·
**6 ✓** remise à zéro actée 2026-06-10 : BT aligned par défaut (`BACKTEST_LEGACY_SEMANTICS=1`
= échappatoire), MKR retiré, `docs/backtests.md` re-baseliné (28m +1436% — anciens chiffres
~34× inflatés archivés dans `docs/backtests_legacy_pre_phase6.md`) ·
**7** corrections paper engine (slippage 4 bps, gap fills — flags dans settings.py, OFF).

**JUNIOR migré le 2026-06-11** (bot `junior` de bots.json, modèle agent
JUNIOR_HL_PRIVATE_KEY → master 0xb65d…56Fe, capital = equity $332.76 au reset,
capital_cap $500 — legacy :8099 ARRÊTÉ, bloc commenté). **Auth par rôles** :
`admin` (DASHBOARD_USER → tout) et `bot:junior` (JUNIOR_USER → uniquement
/bot/junior/*, token `ts:rôle:sig`, mutations auditées dans admin_audit).
Les 4 sentinelles cron (supervisor, strategy_review, regime_alert,
hedge_monitor) sont portées sur Alfred le 2026-06-11.

**BABY ajouté le 2026-06-17** (4ᵉ bot `baby` de bots.json, géré par une tierce
personne distincte — modèle agent `BABY_HL_PRIVATE_KEY` → master
`0xda4008f9…b014c`, agent public `0x726E…25fA` expire le **2026-12-08** à
régénérer avant). Petit capital : `capital_initial` $57.84, `capital_cap` $400
(DCA prévu, montée progressive). Config de sizing **standard** (`overrides: {}`) :
le walk-forward d'un cap notionnel bas pour libérer les slots à <$100
(`backtests/backtest_small_cap_notional.py`, résultats
`backtests/small_cap_notional_results.md`) n'a passé AUCUNE config en strict 4/4
(PnL bloquant — le cap bas réduit le DD mais rogne les gros gagnants), donc pas
d'override déployé. Auth par rôle `bot:baby` (BABY_USER/BABY_PASS → uniquement
/bot/baby/*, même mécanique générique que junior, bloc dans `web/app.py`).
⚠️ À <$100 + levier 2× les drawdowns restent élevés (−30 à −55 % selon fenêtre) —
inhérent au petit capital. depuis la consigne utilisateur du 17 septembre 2026, « restart » inclut
**tous les bots Alfred : paper, live, junior et baby**.

Couche données (2026-06-10) : table `candles` persistée dans market.db (store canonique,
boot-reprise depuis la DB + event DOWNTIME + excursion catch-up des positions ouvertes),
`admin_audit`, export BT `alfred/tools/export_candles.py` (même source bot/BT).
Supervision : page **`/master`** (Système/Flotte/Admin — santé WS, exposition agrégée
tous bots, lifecycle par bot, éditeur bots.json avec validation, journal d'audit).

Mémoire long-terme : `memory/project_alfred_refacto.md`. Le restart d'Alfred suit la même
règle que les bots legacy : **jamais sans OK explicite** — d'autant plus critique depuis
le 2026-06-10 : Alfred porte le bot LIVE (argent réel) en plus du paper parallel-run.

## Project Overview

Crypto trading bot for Hyperliquid DEX (accessible from France). Paper/live trading on 28 altcoins.

**The bot is 12 modules** in `analysis/bot/` + `analysis/reversal.html` (dashboard). `analysis/reversal.py` is a 6-line backward-compat shim. Backtests are in `backtests/`.

Version in `analysis/bot/config.py` `VERSION` constant (currently 12.7.13). **TOUT LE STACK LEGACY EST DÉCOMMISSIONNÉ depuis le 2026-06-12** : paper :8097, apprenti :8100, admin :8090 arrêtés (en plus de live :8098 et junior :8099 déjà migrés). Tous les bots tournent désormais dans Alfred (:8101 — `paper`/`live`/`junior` dans `bots.json`). Les routes nginx legacy (`/paper/`, `/crypto/`, `/apprenti/`) redirigent vers Alfred (301) ou renvoient 410. Le code `analysis/bot/` reste pour référence/backtests mais aucune instance ne tourne. Le watchdog cron ne surveille plus qu'Alfred. Le tracker paper-vs-BT (`backtests/paper_vs_bt_tracker.py`) lit l'état du paper Alfred (re-baseliné sur son inception 2026-06-10).

### Execution Modes

- **Paper** (`HL_MODE=paper`, default): simulates positions in memory, reads prices from Hyperliquid public API
- **Live** (`HL_MODE=live`): places real orders via `hyperliquid-python-sdk`, reconciles with exchange every scan

Config in `.env` (gitignored): `HL_MODE`, `HL_PRIVATE_KEY`, `TG_BOT_TOKEN`, `TG_CHAT_ID`, `DASHBOARD_USER`, `DASHBOARD_PASS`. Junior adds `JUNIOR_HL_PRIVATE_KEY`, `JUNIOR_USER`, `JUNIOR_PASS`, `JUNIOR_TG_BOT_TOKEN`, `JUNIOR_TG_CHAT_ID`. Junior also passes `HL_ACCOUNT_ADDRESS` directly in `start_bots.sh` (not in `.env`).

### Junior wallet model (v11.7.17+)

Junior uses Hyperliquid's **API agent wallet** model — different from the live bot's single-key model:

- **Live** (`:8098`): `HL_PRIVATE_KEY` IS the wallet. The key signs orders AND holds funds. `init_exchange()` is called without `account_address`. Equity computed via legacy formula `spot.total + unrealized` (HL holds perps margin in spot, hence the formula).
- **Junior** (`:8099`): `JUNIOR_HL_PRIVATE_KEY` is just a signer (API agent wallet, no funds). `HL_ACCOUNT_ADDRESS` (in `start_bots.sh`) points to the master wallet that holds the actual USDC. The agent must be authorized by the master via Hyperliquid Settings → API. `init_exchange()` is called with `account_address=master`. Equity is computed via the unified formula `(spot_usdc - spot_hold) + accountValue` (v11.9.1+) — for Junior `spot_usdc≈0` so equity reduces to `marginSummary.accountValue`.

Concretely for the current Junior setup:
- API wallet (signer, derived from `JUNIOR_HL_PRIVATE_KEY`): `0x4EAb0507…3F7e`
- Master wallet (holds funds, hardcoded in `start_bots.sh`): `0xb65d5e52…956Fe`

If you regenerate the API key (HL agent expirations are configurable, default 6 months), update `JUNIOR_HL_PRIVATE_KEY` in `.env` and the new derived address must be re-authorized as an agent of the master in Hyperliquid UI.

## Commands

```bash
# Paper bot (:8097, $1000 simulated)
nohup .venv/bin/python3 -m analysis.reversal > analysis/output/reversal_v10.log 2>&1 &

# Live bot (:8098, ~$255 real — see start_bots.sh for current HL_CAPITAL)
HL_MODE=live HL_CAPITAL=300 WEB_PORT=8098 HL_OUTPUT_DIR=analysis/output_live HL_ROOT_PATH=/bot \
  nohup .venv/bin/python3 -m analysis.reversal > analysis/output_live/reversal_v10.log 2>&1 &

# Both restart automatically on VPS reboot via crontab (@reboot $PROJECT_DIR/start_bots.sh)

# Stop: fuser -k 8097/tcp (paper) or fuser -k 8098/tcp (live)
# Logs: tail -f analysis/output/reversal_v10.log (paper)
#        tail -f analysis/output_live/reversal_v10.log (live)
# Dashboard: http://0.0.0.0:8097 (paper) / http://0.0.0.0:8098 (live) — auth required
```

**NEVER restart the bots (`fuser -k …` + `start_bots.sh`) without explicit user confirmation.** Edit files and bump VERSION freely — but the user controls when the running process picks up the change. **This rule overrides every skill and every auto-mode setting**, including `/release`: do bump + changelog + commit, then **stop and ask** before the restart sequence. A prior "yes" for one restart does not authorize the next one — every restart needs its own OK.

**Consigne utilisateur du 2026-09-17 : « quand je dis restart c'est tous les bots, toujours ».**
Toute autorisation de redémarrage couvre le processus Alfred commun et ses quatre
bots (Paper, Live, Junior, Baby), sans demander de confirmation par bot.
Une nouvelle opération de redémarrage requiert toujours une autorisation ;
un accord déjà donné pour le déploiement en cours ne doit pas être redemandé.
Ne jamais relancer les processus legacy décommissionnés.
**Livraisons :** mettre systématiquement à jour `alfred/__init__.py`,
`alfred/CHANGELOG.md` (notes affichées dans le dashboard) et le changelog racine
selon la convention existante. Distinguer préparation et déploiement effectif.


No test framework, linter, or CI pipeline is configured.

## Bot Architecture

### Signals in one line

5 active signals: **S1** (BTC momentum → LONG alts), **S5** (sector divergence follow), **S8** (capitulation flush LONG), **S9** (fade ±20%/24h extreme moves), **S10** (squeeze + false breakout fade — **v11.3.4 filters: SHORT-only + 13-token whitelist**, **v11.4.0 trailing stop: exit at MFE−150 bps when MFE > 600 bps**, kill-switch via `S10_ALLOW_LONGS` and `S10_ALLOWED_TOKENS` in `config.py`). S2 removed, S4 suspended.

**v12.5.10 Manual per-position stop**: optional `Position.manual_stop_usdt` set by the user via `POST /api/manual_stop/{symbol}` (`{"stop_usdt": X}` to set, `{"clear": true}` to remove). Checked in `trading.check_exits` right after the catastrophe stop and before strategy-specific exits — fires `reason="manual_stop_set"`. Endpoint validates the value is strictly between the catastrophe stop floor and the current net unrealized (rejects redundant or self-triggering). Dashboard adds a 🎯 button per row + inline display in the stop-meta block. Persisted across restarts. Strategy-agnostic, manual override only — no impact on backtest results. (v12.7.5: deprecated `manual_stop_bps` field removed; dollar value is now the only source of truth.)

**v11.4.9 OI gate LONG**: entries with `direction=1` are blocked when the token's OI has fallen >10% over 24h (`OI_LONG_GATE_BPS=1000` in `config.py`). Inactive for the first ~23h after a restart (insufficient `oi_history`). Rationale: longs unwinding = bearish flow still active = LONG catches a falling knife. Walk-forward validated 4/4 on 28m/12m/6m/3m, zero DD penalty. Affects mostly S8 and S5-LONG. Helper: `features.oi_delta_24h_bps()`.

**v11.4.10 Trade blacklist**: `TRADE_BLACKLIST = {"SUI", "IMX", "LINK"}` in `config.py`. These tokens were net-negative on every walk-forward window (28m/12m/6m/3m). Enforced at entry in `trading.rank_and_enter` — SKIP logged with `reason=blacklist`. Tokens stay in `TRADE_SYMBOLS` to preserve data collection. Kill-switch: empty the set. Walk-forward impact (on backtest_rolling baseline): +91% on 28m, +63% on 12m, +34% on 6m, +18% on 3m.

**v11.7.2 Dead-timeout early exit**: at T−12h from hold expiry, if a position has never shown meaningful upside (`pos.mfe_bps ≤ DEAD_TIMEOUT_MFE_CAP_BPS=150`), is deeply underwater (`pos.mae_bps ≤ DEAD_TIMEOUT_MAE_FLOOR_BPS`) AND is still pinned near its low (`unrealized ≤ mae_bps + DEAD_TIMEOUT_SLACK_BPS=300`), exit immediately instead of waiting for timeout. New exit reason: `dead_timeout`. Rationale: a trade that's still at its worst within 12h of timeout has no pulse — crystallizing the loss now vs at MAE later is structurally safe (no kept winner has MFE ≤ +150 bps by definition). Walk-forward validated 4/4 on `backtest_rolling` via `backtests/backtest_early_exit_d.py` variant D2: +$49 322 on 28m, +$1 405 on 12m, +$46 on 6m, +$21 on 3m with DD unchanged. Check runs in `trading.check_exits` after stops/trailing, before `close_position`. Kill-switch: set `DEAD_TIMEOUT_MFE_CAP_BPS=-99999` (no trade will ever match).

**v11.7.16 Dead-timeout tightened**: `DEAD_TIMEOUT_MAE_FLOOR_BPS` from −1000 → −800 (`config.py`). Catches pinned S5 losers ~200 bps sooner. Motivated by recent S5 losers (PENDLE, DYDX) reaching MAE −1000 to −1229 bps before the v11.7.2 logic fired. Walk-forward on `backtest_s5_stops` variant V4: +$9 554 on 28m (S5 alone +$6 278), minor noise on 12m/6m/3m (−$198 / −$104 / −$48), DD unchanged or slightly better on all windows. Not strict 4/4 pass, shipped for asymmetric risk/reward. Kill-switch: set back to −1000.

**v12.5.0 Dead-timeout tightened (again)**: `DEAD_TIMEOUT_MAE_FLOOR_BPS` from −800 → −500 (`config.py`). Validated walk-forward 4/4 strict via `backtest_wr_autoclose.py`: +3244pp on 28m, +484 on 12m, +43 on 6m, +21 on 3m, ΔDD avg −0.82pp (DD improved). Catches the band of pinned losers between −500 and −800 MAE that the previous threshold left to die at natural timeout. Most impact on S5 LONG (deep-MAE-then-recover is rare; deep-MAE-then-die is common). Discovery as side-effect of testing whether WR-based auto-close would pass walk-forward (it didn't, but this MAE-floor tightening did). Kill-switch: set back to −800 or −1000.

**v11.7.5 Per-trade funding (live)**: at close, `trading.close_position` calls `exchange.fetch_position_funding()` to sum the exact `user_funding_history` deltas on that coin between `entry_time` and `exit_time`. The flat `FUNDING_DRAG_BPS=1` already baked into `net_bps` is swapped out for the real number: `pnl = size*(net_bps)/1e4 + funding_usdt - flat_funding_usdt`. New column `trades.funding_usdt` (SQLite auto-migrated). Paper mode unchanged (flat model). Rationale: funding is time-dependent (hourly accrual at floating rate) so entry-time estimation is imprecise; the flat 1 bps estimate was ~10× below real drag observed in live (~14 bps avg). Fail-open: HL API failure returns 0 and trade closes with the flat model. Backtests keep the flat model (no candle-level funding data).

**v11.7.28 Dispersion gate (S5+S9)**: before adding S5 or S9 candidates to the per-scan signal list, `bot._scan_and_trade` reads `cross_ctx["disp_24h"]` (cross-sectional std of 24h returns across all 28 tracked alts) and skips the entry when `disp_24h ≥ DISP_GATE_BPS=700` (`config.py`, `DISP_GATE_STRATEGIES = {"S5", "S9"}`). Rationale: S5 (sector divergence) and S9 (extreme-move fade) are mean-reversion strategies; when the cross-sectional distribution is itself broken (alts flying in all directions, p98+ event, ~1.4% of 4h candles in last 12 months), fades catch falling knives. S8 / S10 keep firing because their setup is single-token-mechanic. Walk-forward 4/4 on `backtest_dispersion_filter.py`: +6126pp / +865pp / +8pp / +0.5pp on 28m/12m/6m/3m, ΔDD avg +0.2pp (intact). Skip ~1.2% of entries (~6/year). Logs `SKIP` event with `reason=disp_gate`. Kill-switch: set `DISP_GATE_BPS=99999` or empty `DISP_GATE_STRATEGIES`.

**v11.7.32 Runner extension (S9)**: in `trading.check_exits`, when a S9 position reaches its natural timeout AND `pos.mfe_bps ≥ RUNNER_EXT_MIN_MFE_BPS=1200` AND `unrealized / mfe_bps ≥ RUNNER_EXT_MIN_CUR_TO_MFE=0.3` AND not already extended, push `pos.target_exit` forward by `RUNNER_EXT_HOURS=12` and set `pos.extended=True` so the rule fires only once per position. Logs `RUNNER_EXT` event. Mirror of `dead_timeout` but for winners — extends the strong-MFE trade past its 48h S9 hold to capture mean-reversion continuation. Walk-forward 4/4 on `backtest_runner_extension.py` with avg ΔPnL +1790pp and avg ΔDD −0.9pp (DD slightly better). Fires ~1-2× per month on S9 winners. Kill-switch: empty `RUNNER_EXT_STRATEGIES`. Position dataclass has new `extended: bool` field (default False, persisted in state.json).

**v11.10.0 Adaptive macro modulator (S1, S8, S9)**: `trading.rank_and_enter` scales the position size of S1, S8, and S9 entries by `1 + α × btc_z`, where `btc_z` = rolling 6-month z-score of BTC's 30-day return (computed each scan via `features.compute_btc_z` from `bot._scan_and_trade`). Coefficients in `config.py:ADAPTIVE_ALPHA = {"S1": +0.5, "S8": -0.5, "S9": -0.5}`. Multiplier clipped to `[MACRO_MULT_MIN=0.3, MACRO_MULT_MAX=2.5]`, btc_z clipped to `±MACRO_Z_CLIP=2.5` first. Logic: S1 (BTC momentum LONG alts) is amplified in bull regimes, S8/S9 (bear-favoring fade/capitulation) amplified in bear. **S5 LONG and S10 deliberately excluded** — sliding walk-forward OOS showed their α flipped sign across regimes (regime-unstable). Validated by `backtest_adaptive_macro.py`, `backtest_adaptive_macro2.py`, `backtest_adaptive_robustness.py` (IS/OOS split, lookback 15-90d, null-shuffle 13× signal-vs-noise, rolling z without look-ahead, per-window α stability), and `backtest_adaptive_walkforward.py` (4 sliding 18m-train/6m-test splits — S1/S8/S9 stable, OOS sums +942%/+670%/+391%). Conservative α=±0.5 chosen vs ±1.0 optimum to limit overfit risk. Exposed in `/api/state` as `btc_z_30d` for observability. Cold-start handled because the bot loads 30 months of 4h candles at boot, plenty for the 30d+180d window.

**v12.5.30 S8 in-life MFE trail (regime-conditioned)**: in `trading.check_exits`, S8 positions exit early when `pos.mfe_bps ≥ S8_INLIFE_PARAMS[bucket][0]` AND `unrealized ≤ pos.mfe_bps - S8_INLIFE_PARAMS[bucket][1]`, with the bucket chosen by `bot._btc_z` vs `S8_INLIFE_Z_THRESHOLD=0.5`. Three buckets: `bear` (z<−0.5) → (1500, 100), `neutral` (|z|≤0.5) → (300, 300), `bull` (z>0.5) → (1500, 100). New exit reason: `s8_inlife`. Walk-forward strict 4/4 + null-shuffle z=+10.52 (12/13 shuffles destroy the edge → genuinely regime-driven). Cross-validated by `backtest_inlife_exit` Family B (percentile empirique). S5 unsolved by every family — left untouched. Mechanically consistent with v11.10.0 modulator (S8 α=−0.5 = bear-favored): aggressive trail in bear regimes locks gains where the trade has the most upside slippage to give back. Block placed between `s10_trailing` and `dead_timeout`. Kill-switch: empty `S8_INLIFE_PARAMS={}` in `config.py` (graceful no-op via `.get(bucket, (99999, 0))`). Source: `backtests/inlife_exit_results.md`, full R&D `backtests/backtest_inlife_exit.py`.

**v12.6.0 S8 dead-in-water exit (mid-trade signature)**: in `trading.check_exits`, S8 LONG positions exit early when `hours_held ≥ S8_DEAD_T_H=8.0` AND `pos.mfe_bps ≤ S8_DEAD_MFE_MAX_BPS=50.0` — i.e., the position has never crossed +0.5% MFE after 8 hours. Exit reason: `s8_dead_in_water`. Mechanic: a real capitulation bottom generates an immediate MFE from liquidation hunts + short covers. No rebound by T+8h = thesis invalidated, every bid absorbed by sellers. Discovery: mid-trade profiling EDA (`backtests/mid_trade_profiling_eda.md`) identified the signature on 28m with WR=6.2%, savings=+192 bps/cut, null-shuffle z=−6.41. Walk-forward (`backtests/s8_dead_in_water_walkforward.md`): 28m +207 872pp / 12m +1 723pp / 6m +138pp ΔPnL, 0pp on 3m (rule never fired, 5 S8 LONGs but none qualified). DD never degraded (avg ΔDD = 0 over 28m/12m/3m, +8.39pp **improvement** on 6m). 11 cuts on 28m: 9 genuine (saved −400 to −690 bps losses) + 2 stragglers (GMX +57, CRV +607) for net +3 209 bps. Block placed between `s10_trailing` and `s8_inlife` (mechanically distinct: dead-in-water fires on MFE ≤ 50, S8 trail fires on MFE ≥ 300/1500 — strictly disjoint distributions). Since `pos.mfe_bps` is monotonically non-decreasing, the check is naturally idempotent: once MFE crosses the ceiling, the rule never fires for that position. Kill-switch: set `S8_DEAD_MFE_MAX_BPS=-99999` in `config.py`.

**v12.2.0 Adaptive modulator on S5 SHORT (per-direction extension)**: extends the v11.10.0 mechanism with a direction-specific override. `config.py:ADAPTIVE_ALPHA_DIR = {("S5", -1): -0.5}` plus the helper `get_adaptive_alpha(strat, dir)` that returns the directional value if present, else falls back to the strategy-wide `ADAPTIVE_ALPHA`. Replaces the static `S5_SHORT_BLACKLIST` from v12.1.0 (DOGE/SNX/LDO/AAVE/MINA, removed). Rationale: a static token list cannot catch SEI-type events (SEI was a historical S5 SHORT *winner* on 28m, but ended in catastrophe stop on 2026-05-08 in BULL regime). The regime-aware modulator reduces ALL S5 SHORTs in bull (where shorting outperformers loses to momentum) and amplifies in bear (where mean-reversion works). Walk-forward strict 4/4 (28m +10100pp, 12m +298pp, 6m +82pp, 3m +38pp, ΔDD +1.52pp avg) — better PnL AND smaller DD than the static v12.1.0 blacklist (+6558pp, ΔDD +5.07pp). Logic: same `1 + α × btc_z` formula, same clip bounds, same `_btc_z` source.

**v12.7.1 Trajectory cut (regime-conditioned mid-trade exit)**: in `trading.check_exits`, S5 positions exit early when ALL of: `hours_held - pos.mfe_at_h >= TRAJ_CUT_TIME_SINCE_MFE_MIN_H=4`, `unrealized_bps <= TRAJ_CUT_MIN_LOSS_BPS=-200`, `unrealized_bps - pos.mae_bps <= TRAJ_CUT_AT_MAE_SLACK_BPS=100` (pinned at MAE), `(pos.mfe_bps - unrealized_bps) / t_since_mfe >= TRAJ_CUT_DECLINE_RATE_MIN_BPS_PER_H=100` (steep fall since peak), AND `bot._btc_z < TRAJ_CUT_BTC_Z_THRESHOLD=-0.5` (bear regime). New exit reason: `traj_cut`. Codifies the user's manual_close intuition observed live April-May 2026: cutting positions with "courbe désespérée" saved 3 catastrophe_stops out of 5 cuts (+$28 vs counterfactual). v1 unconditioned (`backtest_trajectory_cut.py`) failed walk-forward 1/4 — cut too many recoverable positions in choppy/bull. v2 regime-conditioned R1 (`backtest_trajectory_cut_v2.py`) PASSES strict 4/4: 28m +177 043pp / 12m +1 440 / 6m +20 / 3m +16 ΔPnL, ΔDD avg +2.15pp (DD improved on 28m by 8.6pp). 36 fires over 28m, all in bear regime. Null-shuffle on 13 trials (`backtest_trajectory_cut_r2_stability.py`): 0/13 random shuffles of btc_z beat the real edge (p<0.08, GENUINE). New `Position.mfe_at_h` field tracks when MFE was last updated (persisted, default 0 = MFE at entry — safe boot recovery). Block placed between `s8_inlife` and `dead_timeout` in the elif chain. Kill-switch: `TRAJ_CUT_STRATEGIES = set()` in `config.py`.

**v12.9.0 Entry scan aligned to 4h candle close**: `bot._scan_and_trade` gates entry signal evaluation to 4h candle boundaries via `_last_entry_scan_4h_close` (persisted in `state.json`). Exits, MAE/MFE tracking, reconcile, and feature refresh continue at `SCAN_INTERVAL=3600` (hourly). Motivated by 65d live deployment audit (`backtests/intracandle_signal_test.py`, `backtests/measure_live_slippage.py`, `backtests/btlive_config_drift_test.py`) showing live fired 75/119 entries intra-candle with cumulative −$257 PnL — signals that didn't survive to the next 4h close (mean adverse drift −105 bps entry→candle_close vs −7 bps on matched-with-BT trades). Slippage measured separately (mean RT +0.15 bps over 119 trades) is NOT the cause ; BT's `BACKTEST_SLIPPAGE_BPS=4.0` model is correctly calibrated. Config drift (disp_gate, traj_cut, universe expansion) was tested via `backtests/btlive_config_drift_test.py` and found NOT to be the cause either (none of these features triggered on the live deployment window). The actionable cause is the granularity asymmetry : live scanned hourly (SCAN_INTERVAL=3600) while the BT engine scans once per 4h candle close. v12.9.0 aligns live with BT. Kill-switch : remove the 6-line gate at the top of `_scan_and_trade`. State persistence guards against duplicate entries within the same 4h period across restarts.

**v12.8.0 Dispersion entry gate retired**: `DISP_GATE_BPS=99999.0` désactive le filtre v11.7.28 (skip S5+S9 entries quand cross-sectional std(ret_24h) ≥ 700 bps). 2×2 backtest matrix (`backtests/discovery_bias_2x2.py`) montre que `traj_cut v12.7.1` + `disp_gate` produit strictement pire PnL+DD sur les 4 fenêtres que `traj_cut` seul (Pareto-dominé) : 28m −345 005pp, 12m −11 340pp, 6m+3m no-op (gate dormant en régime actuel). Audit live 52j (`analysis/output_live/reversal_ticks.db` + paper) : 0 SKIP `disp_gate` observés depuis l'existence de la table events. Mécanique : `traj_cut` capture déjà la même classe de trades catastrophiques en bear via trajectoire (mfe→cur déclin + ur≤−200 + bz<−0.5), donc `disp_gate` skip à l'entrée des trades que `traj_cut` aurait sauvé par cut intelligent. Kill-switch : `DISP_GATE_BPS = 700.0` ré-active. Note historique v11.7.28 préservée pour traçabilité. Sources : `backtests/discovery_bias_runtime.py`, `backtests/discovery_bias_2x2.py`.

**v12.7.14 Regime alert (S5 LONG, observation-only)**: at scan time, when cross-sectional 7d dispersion (`cross["disp_7d"]`) crosses `REGIME_ALERT_DISP_7D_BPS=700` AND the bot's last `REGIME_ALERT_LOOKBACK=10` closed S5 LONG trades show WR < `REGIME_ALERT_WR_PCT=35`, fire a Telegram alert (no trading action). 24h cooldown via in-memory `_regime_alert_last_ts` (restart clears — acceptable). Motivated by 2026-05 EDA showing S5 LONG losers concentrated above disp_7d=700 in current regime — both hard-skip and soft-haircut variants failed walk-forward strict (regime-local signal, anti-robust). Alert lets the user catch the shift and pause the (S5, LONG) bucket manually if the pattern persists. Block in `bot.MultiSignalBot._check_regime_alert`. Kill-switch: `REGIME_ALERT_DISP_7D_BPS = 99999`. Event `REGIME_ALERT` logged for audit. Source: `backtests/eda_s5_long_losers.py`, `backtests/backtest_disp7d_gate.py` (walk-forward 2/7 PASS), `backtests/backtest_disp7d_haircut.py` (walk-forward 0/7 PASS).

**v12.7.0 Universe expansion (+6 curated tokens, 29 → 35)**: `TRADE_SYMBOLS` extends with `BCH, DOT, ADA, XMR, ENA, UNI`, plus 2 new sectors in `SECTORS`: `L1-major = [BCH, DOT, ADA]` (Bitcoin-correlated mature L1s, distinct from emerging-L1 like SOL/AVAX/SUI), `Privacy = [XMR]`. DeFi sector extends with `UNI, ENA`. New sectors absorb the additions without saturating `MAX_PER_SECTOR=2`. Discovery via 3-phase R&D: Phase 1 screener (`analysis/universe_expansion_phase1.py`) filtered 230 HL perpetuals down to 137 candidates by strict liquidity/age/volatility cutoffs. Phase 2 tested Config A (+10 blue chips) — passed PnL 2/2 but DD avg +4.25pp ✗; Config B (+20 with narrative tokens) — failed 1/2 PnL + DD +16pp ✗; both showed BTC-correlated blue chips (XRP, HYPE, BNB, LTC) were net-negative contributors. Phase 3 curated set (Config C, +6 tokens dropping the BTC-correlated losers) PASSES 2/2: 6m +57.6pp, 12m +2266pp, avg DD +1.73pp (under +2pp gate). Phase 4 tested Config C-minus (drop ADA) — counter-intuitively WORSE (BCH stops trading without ADA's slot dynamics, +0.9pp 6m vs +57.6pp). Path dependence confirmed: removing a "loser" token can collapse a "winner" token's trade count. Source: `backtests/universe_expansion_results.md`, `backtests/backtest_universe_expansion.py`. The 6 new tokens use the same exit rules, modulator, sizing as the 29 existing — no code changes besides constants. Kill-switch: remove the 6 from `TRADE_SYMBOLS` and clear them from `SECTORS`.

For detailed conditions, parameters, and research behind each signal see **`docs/bot.md`** (French). For the history of changes see **`CHANGELOG.md`**.

## Gotchas that affect coding

Things that will bite you when modifying the code. For signal-specific details, backtest rationale, and parameter history see `docs/bot.md`.

### Versioning & deployment
- Bump `VERSION` in `config.py` for every code change and use `/release` skill (updates `CHANGELOG.md`, `docs/bot.md`, `CLAUDE.md`, commits).
- Restart bots after bumping — `VERSION` is only read at startup.
- Dashboard HTML is cached in memory on first request. Restart bot to pick up HTML changes.
- Paper (:8097) and Live (:8098) run in parallel from the same code, separate output dirs. Only DXY cache is shared.
- `@reboot` crontab runs `start_bots.sh` (paper + live + Junior + admin panel + Telegram alert).
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
- DCA (`/api/capital`) **rebases** `_peak_balance` to the post-DCA balance (`_capital + _total_pnl`) — capital injections and withdrawals are not trading P&L, so they should not surface as drawdown nor inflate the high-water mark. Pre-v11.7.20 used `max(peak, balance)` which made injections inflate the peak (creating phantom drawdown later) and withdrawals look like drawdown.
- Position table: `Position` column = notionnel (`size_usdt`), `Marge` column = notionnel/leverage.

### Concurrency & safety
- `bot._pos_lock` guards all `self.positions` mutations.
- `bot._closing` (set, guarded by `_pos_lock`) is a per-symbol mutex around `close_position`. Without it, two concurrent paths (e.g. `check_exits` timeout + `api_close_symbol` click) would each call `execute_close` and send duplicate orders to Hyperliquid. The second caller observes the symbol in `_closing` and returns silently; the in-flight call finishes.
- `db._db_lock` (in `analysis/bot/db.py`) serializes SQLite writes across scan, API, and collector threads.
- `load_trades` is called once at startup before the scan thread — no DB lock held.
- `api_pause`, `api_reset`, `api_close_symbol` are sync handlers (`def`, not `async def`) so FastAPI runs them in a threadpool. Prevents blocking the event loop during exchange close.
- DXY cached in memory (`self._dxy_cache`); API handlers never call Yahoo directly.
- `_http_fetch` retries 3× with exponential backoff on all price/candle fetches.

### Execution (live mode)
- SDK (`eth_account` + `hyperliquid`) is lazy-imported only when `HL_MODE=live`. Paper mode has zero SDK dependency.
- Order size must be in coin units: `sz = size_usdt / price`, rounded to `szDecimals` from exchange metadata.
- Fill price extracted from order response (`statuses[0]["filled"]["avgPx"]`), with fallback to `user_fills_by_time` + 500ms delay, then market price.
- Failed exchange closes tracked in `bot._failed_closes` and retried on the next scan. `api_close_symbol` and `api_pause` use `_failed_closes` (not `bot.positions`) to detect failures — checking position presence is unreliable now that closes are mutex-serialized (a concurrent in-flight close briefly leaves the position in place even though the call returned successfully).
- Reconciliation (hourly scan) compares bot positions vs `user_state()`. Mismatches trigger Telegram alert but NO auto-fix.
- Boot reconcile (live only, in `main.py:run`): on startup the bot fetches `user_state()` once and silently drops ghost positions (in `bot.positions` but absent on the exchange — typically a manual close via the HL UI while the bot was offline). Orphans (on the exchange but absent from the bot) are flagged via Telegram but not auto-imported.
- Every 60s the live bot refreshes `bot._exchange_account` from Hyperliquid (spot + perps). This is what the "Equity" card shows.

### Auth & UI
- Dashboard auth: HTML login form via `DASHBOARD_USER`/`DASHBOARD_PASS` in `.env`. HMAC-signed stateless session cookies (30-day expiry) survive restarts. 10 attempts/5min/IP rate limit.
- Admin panel on `:8090` (behind `/crypto/` on nginx). Aggregates all bots via `admin_config.json` and proxies with cached auth cookies.

### Sémantique de l'absence de donnée OI — `block_stale` (2026-08-02)

**Décision d'INTÉGRITÉ, pas d'edge.** L'effet chiffré est non systématique :
+550 sur OOS-6, −246 sur OOS-12, deux fenêtres gagnent, deux perdent. Elle est
prise parce qu'on ne trade pas sur une jauge débranchée, pas parce qu'elle
rapporte.

`Params.oi_missing_policy = "block_stale"` — appliqué dans le noyau partagé
`alfred/rules.py`, donc bot **et** backtest :

| situation | `oi_delta_24h_bps` | gate OI LONG |
|---|---|---|
| donnée fraîche | valeur | règle normale (< −1000 bps ⇒ skip) |
| **source périmée** (flux mort, trou > 4 h) | `None`, raison `stale` | **BLOQUE** — `oi_gate_no_data` |
| **démarrage à froid** (< 23 h d'historique) | `None`, raison `cold` | **laisse passer** — état connu, borné, déclaré |

`block` bloque aussi au démarrage à froid ; `block_stale` non — c'est toute
la différence, et elle a dû être corrigée : les deux étaient identiques à la
première écriture.

La distinction vient de `features.oi_absence_reason` / `backtest_rolling.oi_absence_reason`,
et **la parité est vérifiée par test** : `python3 -m backtests.test_oi_parity`
(6 cas, doit sortir « parité VÉRIFIÉE »). Sans ce test, trois divergences
seraient passées — dont une réelle : le live prenait son dernier échantillon
pour « maintenant » et **ne pouvait donc pas détecter que son propre flux
était mort**. Il reçoit désormais l'horloge réelle.

⚠ **Application AU PROCHAIN RESTART PLANIFIÉ**, pas de hotfix. Le process en
cours continue en `open` jusqu'à ce qu'il relise `settings.py`.

### Fraîcheur des données (2026-08-02, 7e incident silencieux)

`data_freshness.py` donne à chaque source un **âge maximal** ; dépassement ⇒
anomalie critique dans la revue quotidienne de `ai_system_audit.py`. Trois
statuts : `STALE` (retard), `INVALID` (horodatage aberrant — unité mal
déclarée), `FROZEN` (arrêt **déclaré avec son motif**, silencieux par
construction pour ne pas noyer les vraies alertes).

Le comblage OI est **planifié** depuis le 2026-08-08 :
```
20 0,4,8,12,16,20 * * * /home/crypto/.venv/bin/python3 /home/crypto/backtests/oi_backfill.py >> /home/crypto/analysis/output/oi_backfill_cron.log 2>&1
```
Idempotent (n'ajoute que les points postérieurs au dernier existant),
re-valide ses trois critères à chaque passage, et **écrit zéro fichier** si le
taux de réussite tombe sous 90 % — auquel cas la garde de fraîcheur reprend la
main. Appelé par **chemin absolu** : le script insère lui-même sa racine dans
`sys.path`, donc aucune dépendance au répertoire de travail de cron.
L'original reste reconstructible exactement en filtrant `src != "live"`.

Deux gels trouvés le 2026-08-02, tous deux **sans impact sur le bot live** —
Alfred lit son OI d'un poll REST `metaAndAssetCtxs` en mémoire et n'ouvre
aucun de ces fichiers :

- `backtests/output/oi_history.db` — figé au 2026-06-29 parce que **l'archive
  amont S3 ne publie plus**. Rien à redémarrer. Remplaçant disponible :
  `market_snapshots` de `market.db` (horaire, 35 symboles, depuis 2026-06-10).
- `backtests/output/pairs_data/*_oi_4h.json` — figés au **2026-06-15** (48 j).
  ⚠ **Ceux-là sont lus par `load_oi()` et alimentent la gate OI LONG.**
  `oi_delta_24h_bps` ne renvoie **pas** `None` au-delà des données : il rend la
  **dernière valeur connue, figée**. Conséquence sur tout backtest récent :
  4 tokens (SAND, SNX, STX, TON) voient leurs LONG bloqués sur les 48 derniers
  jours, les 30 autres jamais. Touche 26 % de la fenêtre OOS-0.

Deux pièges de conception du garde lui-même, corrigés : un âge **négatif**
passait « OK » (unité ms déclarée en s), et un contrôle au niveau du dossier
prenant le **max** des mtime masquait les fichiers en retard. Le garde retient
désormais le fichier **le plus vieux** des tokens réellement tradés.

### Observation-only data (don't use for decisions yet)
- OI / funding / premium / `entry_crowding` / `entry_confluence` / `entry_session` are logged in each trade and in hourly market snapshots — not used for signals until 50+ trades per pre-registered protocols.
- `/api/state.signal_drift` exposes rolling WR/avg bps/P&L for monitoring. Quarantine logic itself is disabled (protections list in `docs/bot.md`).
- `S9F_OBS` events (±3% / 2h) are logged but not traded — need 6+ months of live data.

### Supervisor (v11.3.5)
`supervisor.py` at the repo root is a standalone Python process (~590 lines) launched once a day by crontab (08:00 UTC = 10:00 Paris summer / 09:00 winter). **Depuis le 2026-06-11 il cible les bots Alfred** (`:8101/bot/<id>` : SENIOR/JUNIOR/PAPER-ALFRED) via HTTP authentifié sur `127.0.0.1`, assembles a static context from `CLAUDE.md`, `docs/bot.md` and `docs/backtests.md` (~30 kB / ~7.5k tokens, flagged `cache_control: ephemeral`), calls the Anthropic SDK (`claude-haiku-4-5` default), parses a strict JSON report and ships it as plain text via Telegram. **Observation + suggestions only — never writes to the bot's config or state.**
- Config in `.env`: `ANTHROPIC_API_KEY` (required), `SUPERVISOR_MODEL=claude-haiku-4-5` (default), `SUPERVISOR_ENABLED=1` (kill-switch)
- Zero runtime coupling: no imports from `analysis/bot/*`, only stdlib + `anthropic` SDK
- Bot-level scoping: `BOTS` list in `supervisor.py` with a `notes` field per instance. Junior is marked `DISABLED` (low capital, recent activation) — to enable, flip its flag in `supervisor.py`. Live is the primary target of the Telegram report; Paper is kept as a comparison baseline.
- Report language is French, format is strict JSON parsed into a plain-text Telegram message (no `parse_mode` — LLM content routinely contains underscores and asterisks that break Markdown parsing; `send_telegram` also now checks `ok: true` in the response body instead of trusting HTTP status alone).
- Kill-switch: `SUPERVISOR_ENABLED=0` in `.env` or `crontab -l | grep -v supervisor.py | crontab -`
- Audit: every run writes a `SUPERVISOR_REPORT` event (full JSON payload) into the `events` table of `alfred/data/market.db`. Query via `SELECT datetime(ts,'unixepoch'), json_extract(data,'$.health'), json_extract(data,'$.summary') FROM events WHERE event='SUPERVISOR_REPORT' ORDER BY ts DESC LIMIT 10;`
- Testing: `supervisor.py --dry-run` (context fetch + prompt assembly, no API), `--no-telegram` (real API, stdout), `--model X` (override default)
- Crontab line (installed, absolute paths so it runs correctly from any cwd):
  ```
  0 8 * * * /home/crypto/.venv/bin/python3 /home/crypto/supervisor.py >> /home/crypto/analysis/output/supervisor.log 2>&1
  ```
- Cost measured in practice: first run ~$0.036 (cache creation), subsequent runs ~$0.017 (cache hit, 10k cached tokens). Daily cadence ≈ **$0.50/month**.

### Strategy drift monitor (v12.0.0)
`analysis/strategy_review.py` is a stdlib-only script (no Anthropic API call) that runs **weekly on Monday at 8h UTC** via crontab. **Depuis le 2026-06-11 il lit `alfred/data/bots/<id>/bot.db`** (`--bot live` par défaut, capital depuis le state.json du bot), computes per-(strategy, token, direction) statistics over rolling 30/90/365-day windows, and flags 5 categories of drift:
1. **STRAT_DRIFT** — per-strategy WR drop ≥12pp recent vs lifetime (configurable `WR_DRIFT_PP`)
2. **TOKEN_TOXIC** — (token, direction, strategy) with recent sum < −$8 over 90d (configurable `RECENT_PNL_TOXIC_USD`)
3. **TOKEN_REVIVAL** — previously-bad pair showing recent positive WR ≥18pp gain
4. **LIVE_VS_BT** — live cumulative PnL deviates ≥25pp from `docs/backtests.md` expectation
5. **REGIME_SHIFT** — current btc_z_30d + the modulator multipliers it produces (informational)

Output: a French structured Telegram message + `STRATEGY_REVIEW` event in `events` DB for audit. **No auto-action** — alerts inform manual review of parameters (`SIGNAL_MULT`, `TRADE_BLACKLIST`, `ADAPTIVE_ALPHA`, etc.).

Crontab line:
```
0 8 * * 1 /home/crypto/.venv/bin/python3 /home/crypto/analysis/strategy_review.py >> /home/crypto/analysis/output/strategy_review.log 2>&1
```

Testing: `python3 -m analysis.strategy_review --dry-run` (console only, no DB log, no Telegram). `--no-telegram` keeps DB log.

Cost: zero (no LLM calls). Uses only stdlib + the trade DB.

## Related docs
- `docs/architecture.md` — **document de référence de l'architecture Alfred (runtime + signaux + cadences + exits + données + sécurité + supervision), à jour avec le code. À lire en premier pour une vue d'ensemble.**
- `docs/bot.md` — detailed bot description (French): signals, parameters, protections, research. NB : son cadrage *architecture* décrit le legacy `analysis/bot/` (périmé) ; la logique de trading reste valide. Voir `docs/architecture.md` pour le runtime Alfred.
- `docs/synthese.md` — pedagogical synthesis (French): "for-dummies" walkthrough of strategies, modulator, exits, observability features.
- `docs/backtests.md` — rolling backtest results for the current parameters, regenerated via `python3 -m backtests.backtest_rolling`.
- `docs/dd_anatomy.md` — anatomie du drawdown −51,4 % (pic/creux datés, décomposition par signal et par mois, 3 pires fenêtres 30 j, Monte Carlo). Série de référence : `data/alfred_daily_pnl.csv`. Régénéré via `python3 -m backtests.dd_anatomy`.
- `docs/projet_a_trend_v0.md` — Projet A : spécification gelée et **grille d'acceptation pré-enregistrée** d'une stratégie de suivi de tendance destinée à décorréler le portefeuille. Grille committée avant exécution. **Verdict : REJET** (décorrélation confirmée, véhicule non rentable).
- `docs/wallet_persistence.md` — Persistance de performance des wallets HL, Phase 0 : faisabilité **données** (panel de 40 960 adresses, profondeur médiane 487 j, granularité hebdomadaire, fills tiers NON servis). **Phase 1 exécutée** : les perdants persistent (4 strates sur 4, t de +2,91 à +6,68), les gagnants pas du tout (0 sur 4). Copier n'a aucune base, fader en a une — modeste.
- `docs/ai_haircut_verdict.md` — **la décote de l'arbitre IA d'entrée mesurée en argent réel** : elle explique l'INTÉGRALITÉ de l'écart live-vs-paper (−15,86 $ mesuré contre −16,17 $ constaté, résidu 0,31 $), et son coût est entièrement dans S5 LONG. La consigne visée avait déjà échoué en walk-forward 0/4 avant d'être réintroduite dans le prompt — **un prompt n'est pas une dérogation à la grille**. Retirée le 2026-08-22. Contrôles hebdo : `backtests/ai/`.
- `backtests/max_macro_sweep_2026_09_results.md` — **re-sweep du plafond de slots macro** : le réglage de mai reposait sur 2 trades S1 par fenêtre récente, alors qu'il bloque aujourd'hui 85 % des scans. 4 passe le strict 4/4 (DD inchangé, +28 % de trades S1 sur 3 mois) ; le plateau au-delà est l'ombre de `max_same_direction=4`, dont l'ouverture ÉCHOUE le critère. Shippé v1.23.0.
- `docs/bilan_2026_09.md` — **bilan des trois semaines de réparation (22/08 → 16/09)** : sept défauts corrigés, tous AUTOUR du moteur, jamais dans `rules.py`. Contient surtout le **§ 9 : l'écart live/paper de 117 $ n'est PAS distinguable du hasard** (t=1,23, bruit de fond ±121 $, `max_macro_slots=3` sature 85 % des scans) — donc **ne jamais diagnostiquer par différence de soldes**, seulement par contrefactuel apparié. Le paper est *compatible* avec son backtest, pas devant.
- `docs/redressement_2026_08.md` — **état des lieux du 2026-08-29 : décomposition en trois couches de l'écart live-vs-backtest** (couche IA ~8,7 pp mesurée et retirée · bruit de chemin ~1,7 pp, aucun biais de prix de sortie · le régime). Contient le constat que le BT lui-même est négatif depuis juin, le précédent historique de drawdown, et les critères pré-enregistrés de la prochaine revue. **À lire avant toute décision d'arrêt.**
- `docs/bilan_2026_08.md` — **bilan de la campagne du 1er au 14 août** : huit branches explorées, sept fermées, zéro edge, trois bugs de mesure trouvés. État de l'argent, registre des verdicts, cinq écarts de grille consignés. **À lire avant de rouvrir un dossier.**
- `docs/oi_revalidation.md` — re-validation des verdicts après réparation de la série OI. **Cinq verdicts sur cinq tiennent** ; les drawdowns ne bougent pas, les rendements terminaux si (3e péremption d'absolus en un mois).
- `docs/oi_backfill_protocol.md` — protocole et amendement du comblage OI (refus au 1er tir, succès 33/33 au tir unique amendé), plus la parité d'obsolescence et la sémantique de l'absence.
- `docs/event_phase0.md` — Étude d'événements (cascades de liquidations, listings), Phase 0 : faisabilité et comptage. 416 cascades brutes → **241 après dé-clustering** (194 agrégées), **97 listings** sur 28 mois. Détection limitée à **176 jours** — le 1 h n'est servi que sur 210 jours.
- `docs/fade_feasibility.md` — Fade des perdants persistants, étape 1 (in-data) : **BRANCHE CLOSE**. Trois quarts de leur perte est de la friction que la position inverse paie aussi, et 4 comptes sur 5 sont ruinés ou inactifs au trimestre suivant.
- `docs/basis_feasibility.md` — Projet basis inter-venues, Phase 1 : grille pré-enregistrée + résultat. **BRANCHE CLOSE** — différentiel réel (29 tokens/32 positifs, +3,9 %/an de notionnel) mais sous le benchmark HLP 12 mois, et vaut un bon du Trésor à la taille disponible. La clause d'épisodes a été jugée mal formée (sélection ex-post). Régénéré via `python3 -m backtests.basis.phase1_analysis`.
- `docs/basis_phase0_inventory.md` — Projet basis inter-venues, Phase 0 : faisabilité **données** (joignabilité, 32/34 tokens des deux côtés, alignement horaire, sémantique du funding par venue, frais des deux jambes). Aucun edge mesuré. `docs/basis_feasibility.md` sera le livrable de la Phase 1.
- `docs/mm_phase0_economics.md` — Projet market making HL, Phase 0 : **STOP**, deux NO-GO indépendants (barème maker à ce palier, latence du carnet). Aucun ordre placé.
- `docs/scan_activity_premise.md` — Phase A0 : la prémisse « agitation du scan » re-vérifiée sur base propre et en fenêtres glissantes. **Confirmée 4/4** — mais c'est une marche au bucket ≥5, pas une pente, et le régime agité est quasi absent des creux.
- `docs/scan_sizing_verdict.md` — Mission 2 : spécification gelée et **grille de verdict pré-enregistrée** du modulateur de taille par agitation du scan. Grille committée avant exécution.
- `docs/dispersion_conditioning.md` — le net d'Alfred dépend-il du régime de dispersion à l'entrée ? **Non : relation plate et instable entre semestres.** Contient aussi la corrélation dispersion ↔ agitation du scan (≈ 0,30, effondrée à 0,09 dans les creux). Régénéré via `python3 -m backtests.dispersion_conditioning`.
- `docs/persistence_spectrum.md` — spectre de persistance (ratio de variance temporel + autocorrélation de rang cross-sectionnelle, horizons 1/3/5/10/20 j), creux vs référence. **Conclusion : réversion à tous les horizons, aucune persistance cross-sectionnelle — la piste XSMOM n'a pas été lancée.** Régénéré via `python3 -m backtests.persistence_spectrum`.
- `docs/creux_anatomy.md` — caractérisation des régimes de creux d'Alfred (efficacité directionnelle, retournements, mèches, volatilité, corrélation intra-univers, funding, BTC vs alts), fenêtres comparées à leur distribution glissante de même longueur. Régénéré via `python3 -m backtests.creux_anatomy`.
- `CHANGELOG.md` — release history, maintained via `/release` skill.
- `BACKLOG.md` — tests/analyses/refactors différés. Check it when starting R&D or refactor work to remember what's pending.
