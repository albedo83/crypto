# Multi-Signal Bot v11.3.7

Bot de trading automatique sur 28 altcoins Hyperliquid. Paper ou live trading. 12 modules Python dans `analysis/bot/` + SQLite tick database. Un supervisor LLM (`supervisor.py`) tourne en plus via crontab et envoie un rapport quotidien en français sur Telegram.

---

## En une phrase

Le bot suit le momentum BTC→alts (S1), suit les breakouts sectoriels (S5), achete les flush de liquidation (S8), fade les mouvements extremes (S9), et fade les faux breakouts apres compression (S10). S2 (alt crash) retire, S4 (vol+DXY short) suspendu. Il logue tout ce qu'il voit (OI, funding, premium, crowding, trajectoire) pour pouvoir s'ameliorer plus tard sans avoir triche sur les donnees.

---

## Les 5 signaux actifs

### S1 — BTC explose (+20% sur 30 jours)

**Quoi** : si BTC a monte de +20% sur un mois, acheter des alts.
**Pourquoi** : quand BTC pump fort, les alts suivent avec du retard. On achete ce retard.
**Type** : continuation / momentum retarde.
**Frequence** : rare, quelques fois par an.
**Hold** : 72h. **Stop** : -12.5% de mouvement de prix.
**z-score** : 6.42. **Walk-forward** : 3/4 (75%).

### ~~S2 — Retire (alt crash)~~

**Retire en v10.8.0.** Alt crash mean-reversion (z=4.00) perd en portfolio. Prend des slots macro que S1/S8/S9 utilisent mieux. S8 (capitulation flush) couvre les crashs extremes plus efficacement. Voir backtest_signal_boost2.py.

### ~~S4 — Suspendu (calme plat + dollar fort)~~

**Suspendu en v10.8.1.** Vol compression + DXY SHORT (z=2.95) — seulement 2 trades en 32 mois de backtest, -$124. En live : -$5.09 (BLUR stop) des la premiere semaine. Code conserve commente pour reactivation si les conditions DXY changent.

### S5 — Un token casse de son secteur

**Quoi** : si un token diverge de +10% par rapport a la moyenne de son secteur, avec du volume anormal → suivre le mouvement.
**Pourquoi** : quand un token casse de son secteur avec du volume, le mouvement continue. Le "fade" a ete teste et ne marche pas.
**Type** : continuation / breakout sectoriel.
**Frequence** : 10-20 fois par mois.
**Hold** : 48h. **Stop** : -12.5% de mouvement de prix.
**z-score** : 3.67.

**Secteurs** :

| Secteur | Tokens |
|---|---|
| L1 | SOL, AVAX, SUI, APT, NEAR, SEI |
| DeFi | AAVE, MKR, CRV, SNX, PENDLE, COMP, DYDX, LDO, GMX |
| Gaming | GALA, IMX, SAND |
| Infra | LINK, PYTH, STX, INJ, ARB, OP |
| Meme | DOGE, WLD, BLUR, MINA |

### S8 — Flush de liquidation

**Quoi** : si un alt a perdu -40% depuis son plus haut de 30 jours, que le volume explose, que le prix continue de baisser, et que BTC est aussi en baisse de -3% → acheter.
**Pourquoi** : quand tout tombe en meme temps, c'est un flush de liquidation force. Le rebond est violent.
**Type** : contrarien / capitulation.
**Frequence** : rare, ~1/mois en portfolio.
**Hold** : 60h. **Stop** : **-7.5%** de mouvement de prix (plus serre, backteste). **Haircut** : 0.8x (liquidite mince).
**z-score** : 6.99.
**Conditions** : `drawdown < -4000 bps AND vol_z > 1.0 AND ret_24h < -50 bps AND btc_7d < -300 bps`.

### S9 — Fade extreme (+20% en 24h)

**Quoi** : si un token a bouge de plus de ±20% en 24h, prendre la position inverse (fade). Si +20% → SHORT, si -20% → LONG.
**Pourquoi** : les mouvements extremes individuels revertent.
**Type** : contrarien / mean reversion individuelle.
**Frequence** : ~12/mois au seuil 20%.
**Hold** : 48h. **Stop** : adaptatif (`max(-1250, -500 - abs(ret_24h)/8)`) — plus le move est gros, plus le stop est serre. Early exit : coupe si unrealized < -500 bps apres 8h.
**z-score** : 8.71 (MC). Le signal le plus fort du bot.
**Conditions** : `abs(ret_24h) >= 2000 bps`.

### S10 — Squeeze + faux breakout (detection gelée + filtres walk-forward v11.3.4)

**Quoi** : compression de range → faux breakout → reintegration → fade le breakout.
**Pourquoi** : le faux breakout piege les traders, le vrai mouvement va dans l'autre sens.
**Type** : contrarien / pattern. Mode B (fade).
**Frequence** : ~50/mois sur 28 tokens bruts, **réduits à ~25/mois** après les filtres v11.3.4.
**Hold** : 24h. **Stop** : -12.5% de mouvement de prix. **Capital** : part entiere (pocket separe supprime — backtest +48% P&L).
**z-score** : 3.66. Detection gelée (ne pas re-optimiser les params du squeeze).
**Params detection** : `S10_SQUEEZE_WINDOW=3` (12h), `S10_VOL_RATIO_MAX=0.9`, `S10_BREAKOUT_PCT=0.5` (50% du range), `S10_REINT_CANDLES=2`.

**Filtres walk-forward v11.3.4** (ajoutés au-dessus de la détection, pas de re-optimisation) :
- `S10_ALLOW_LONGS = False` — **les LONG fades sont bloqués**. Sur 28 mois de backtest : LONG 45% WR / −$4.8k, SHORT 58% WR / +$7.5k. Rationnel économique : fader un down-move revient à combattre du panic-selling en chaîne ; fader un up-move attrape l'exhaustion du top.
- `S10_ALLOWED_TOKENS` — **whitelist de 13 tokens** dont le S10 a été positif sur la fenêtre d'entraînement 2023-10 → 2025-02 : AAVE, APT, ARB, BLUR, COMP, CRV, INJ, MINA, OP, PYTH, SEI, SNX, WLD. Les 15 autres (AVAX, DOGE, DYDX, GALA, GMX, IMX, LDO, LINK, MKR, NEAR, PENDLE, SAND, SOL, STX, SUI) sont skippés à la source.
- **Impact mesuré sur test out-of-sample 12m (2025-02 → 2026-02)** : P&L S10 $4 278 → $9 545 (+123%), DD -41.3% → -32.6%. Caveats : 28m in-sample DD s'aggrave de 8.7pp (perte de diversification des LONGs), 1m post-test régresse −$181. C'est un pari sur le régime 2025-26 actuel, pas une loi universelle.
- **Trailing stop v11.4.0** (`backtest_exits.py` walk-forward, passe 4/4 fenêtres rolling) : quand un trade S10 atteint +600 bps de MFE, un plancher glissant est posé à MFE − 150 bps. Si le prix redescend sous ce plancher, sortie immédiate au lieu d'attendre le timeout 24h. S10 rendait 70% de son MFE en moyenne ; ce trailing protège les gros winners. Impact : 28m +$11 667 (+27%), 12m +$1 321, 6m +$1 121. Config : `S10_TRAILING_TRIGGER=600` et `S10_TRAILING_OFFSET=150` dans `config.py`. Note : le MFE backtest utilise les candle high/low (extremes intra-bougie 4h) alors que le live echantillonne toutes les 60s — le backtest peut voir des MFE que le live bot ne capture pas. Asymetrie mineure avec 150 bps d'offset, coherente avec le traitement du stop loss dans le moteur.
- **Kill-switch** : remettre `S10_ALLOW_LONGS = True` et `S10_ALLOWED_TOKENS = set(ALL_SYMBOLS)` dans `analysis/bot/config.py` puis restart bots → comportement pré-v11.3.4 restauré.
- **Monitoring** : la carte `S10 30d` sur le dashboard (alimentée par `compute_s10_health` dans `trading.py`) affiche un dot 🟢/🟡/🔴/⚫ selon P&L et avg bps des 30 derniers jours de S10. Passe en 🔴 si pnl<0 ET avg<−20 bps → flip du kill-switch à considérer.

---

## Parametres

| Parametre | Valeur | Pourquoi |
|---|---|---|
| **Levier** | 2x | Sweep 1x-3x : 2x optimal. 3x = ruine par compounding des pertes. |
| **Sizing** | 18% base + 3% bonus (z>4), z-weighted, mult S1×1.125 S5×2.50 S8×1.25 S9×2.00 S10×2.00 | Relevé de 12% à 18% (v11.3.0, +138% P&L backtest). Haircut S8 ×0.8. |
| **Compounding** | Oui | Capital = initial + P&L cumule. Les mises suivent les gains et les pertes. |
| **Hold** | 72h (S1), 48h (S5/S9), 60h (S8), 24h (S10) | Timeout automatique. |
| **Stop loss** | -1250 bps de mouvement de prix (S1/S5/S10), -750 bps (S8), adaptatif S9 | Valeurs halved en v11.3.0 apres fix du bug P&L double-leverage. S9 : `max(-1250, -500 - abs(ret_24h)/8)`. |
| **S9 early exit** | Coupe si unrealized < -500 bps apres 8h | Winners revertent vite, losers non. Non generalisable a S5/S8/S10 (testes, tous perdent en compounding). |
| **S10 trailing stop** | MFE > 600 bps → plancher a MFE − 150 bps | Verrouille les gains S10. Walk-forward 4/4. S10 rendait 70% de son MFE avant. |
| **Frais** | Live : **10 bps round-trip** fixe. Backtest : **14 bps** = 10 + 4 slippage moyen. | Calibrés depuis 80 fills live Hyperliquid en v11.3.4 : taker 4.50 bps/leg = 9 round-trip, funding ~0.5 bps/trade → 1 par sécurité. Slippage live = 0 (déjà dans `avgPx` de la réponse SDK). Backtest ajoute 4 bps car il utilise les closes 4h. |
| **Cooldown** | 24h par token apres exit | Evite de re-entrer immediatement. |
| **Slot reservation** | Max 2 macro (S1) + 4 token (S5/S8/S9/S10) | Token slots elargis a 4 (+157% P&L vs 3). |

P&L : `size_usdt` est le **notionnel** (deja leveraged). `pnl = notionnel × mouvement_prix`. Pas de multiplication par le levier en plus — c'etait le bug v11.3.0.

---

## Protections

### Actives
| Protection | Seuil | Ce que ca empeche |
|---|---|---|
| **Max 6 positions** | Absolu | Surexposition |
| **Max 4 meme direction** | 4 LONG ou 4 SHORT | Pari directionnel total |
| **Max 2 par secteur** | 2 par groupe | Concentration sectorielle |
| **Slot reservation** | 2 macro / 4 token | Macro limite, token elargi |
| **Stop loss** | -12.5% / -7.5% (S8) / adaptatif (S9) | Crash extreme sur un trade |
| **S9 early exit** | Coupe S9 si -500 bps apres 8h | Perte qui s'enkyste sur un S9 |
| **S10 trailing stop** | MFE > +600 bps → plancher MFE − 150 | Verrouille les gains S10 (rendait 70% du MFE) |
| **OI gate LONG** (v11.4.9) | Skip LONG si `Δ(OI,24h) < -10%` | Entrer LONG pendant que les longs se débouclent |
| **Trade blacklist** (v11.4.10) | Skip tout trade sur `{SUI, IMX, LINK}` | Tokens structurellement net-négatifs |
| **Dead-timeout early exit** (v11.7.2) | Sortie anticipée à T−12h si `MFE ≤ +150 bps` ET `MAE ≤ −1000 bps` ET `current ≤ MAE + 300 bps` | Cristalliser la perte d'un trade sans pouls au lieu d'attendre le timeout à la MAE |
| **Cooldown** | 24h par token apres exit | Re-entree impulsive |
| **Reconciliation** | Chaque scan horaire, bot vs exchange | Position orpheline ou fantome |
| **Telegram** | Entry, exit, erreur, reboot, resume quotidien | On sait toujours ce qui se passe |
| **Dashboard auth** | HTML login + HMAC sessions (survivent aux restarts) | Acces non autorise |
| **File lock** | fcntl sur STATE_FILE.lock | Deux instances sur le meme state |
| **Auto-restart** | Crontab @reboot + alerte Telegram | VPS reboot |

### Desactivees (v11.3.0) — toutes detruisaient le compounding en backtest
| Protection | Seuil initial | Pourquoi desactivee |
|---|---|---|
| **Kill-switch** | ~~P&L cumule < -$300 → auto-pause~~ | -65% a -99% P&L en backtest |
| **Loss streak cooldown** | ~~3 pertes consecutives → sizing /2 pendant 24h~~ | Idem |
| **Signal quarantine** | ~~Win rate < 20% sur 20 trades → signal coupe~~ | Idem |
| **Exposure cap 90%** | ~~Limite notionnel / balance~~ | Idem |

Les stops par trade + les limites de positions couvrent ce que ces protections pretendaient gerer, sans etouffer le compounding.

---

## Observabilite

### Dans chaque trade (champs structures)

- `entry_oi_delta` : OI delta 1h a l'entree (%)
- `entry_crowding` : score 0-100 de surchauffe du levier
- `entry_confluence` : nombre de features extremes (0-5)
- `entry_session` : Asia/EU/US/Night/WE
- `signal_info` : string complete avec stress, dispersion, shock, cleanliness, leadership

### Trajectoire par trade

Parcours heure par heure : `(heures, unrealized_bps)`. MAE/MFE mis a jour toutes les 60s.

### Snapshots marche horaires

Table SQLite `market_snapshots` dans `{OUTPUT_DIR}/reversal_ticks.db` — 28 lignes/heure : ts, symbol, price, oi, oi_delta_1h, funding_ppm, premium_ppm, crowding, vol_z.

### Tick database

Toutes les 60s, `db.log_ticks` ecrit dans la table `ticks` : mark_px, oracle_px, open_interest, funding, premium, day_ntl_vlm, impact_bid/ask. ~5 MB/jour, WAL mode. Source de verite pour les trades, trajectoires, events (v11.3.1, CSV supprimes).

### Dashboard

- Balance, P&L, drawdown peak, utilisation capital
- **Carte `S10 30d`** (v11.3.5) : dot colorée 🟢🟡🔴⚫ + P&L + trade count + WR + avg bps sur 30j glissants. Sert à monitorer si les filtres walk-forward S10 restent rentables dans le régime actuel.
- Tableau P&L par strategie (`signal_drift`, `/api/state.signal_drift`) — rolling 20 derniers trades par signal
- Regime de marche (BULL/MILD BULL/NEUTRAL/MILD BEAR/BEAR) calculé côté JS depuis `market.btc_30d/btc_7d/alt_index_7d`
- Endpoint `/api/health` (status, price_age, scan_age, exchange_ok, degraded, positions_count, paused)
- Resume quotidien Telegram a minuit UTC (code côté bot, `bot.py::_send_daily_summary`)
- **Rapport supervisor LLM quotidien à 08:00 UTC via Telegram** (crontab + `supervisor.py`) — observation + suggestions, n'écrit rien
- Responsive mobile

---

## Architecture

```
Hyperliquid REST API (toutes les 60s, avec retry 3x backoff)
    ├── metaAndAssetCtxs → prix, OI, funding, premium (28 tokens)
    ├── candleSnapshot → bougies 4h (30 tokens, toutes les heures)
    └── Yahoo Finance → DXY (toutes les 6h, cache memoire + disque 48h)
            │
            ▼
    analysis/bot/  (12 modules, processus asyncio unique)
    │
    ├── 5 signaux (S1, S5, S8, S9, S10) + S9-fast observation
    │     Slot reservation : 2 macro / 4 token
    │     Tri par z-score, puis force du signal
    │
    ├── Position manager
    │     6 max / 4 dir / 2 sect / 2 macro / 4 token
    │     Stop -12.5% (S8: -7.5%, S9: adaptatif max(-1250,-500-ret/8))
    │     S9 early exit a -500 bps apres 8h
    │     (kill-switch, streak, quarantine, expo cap tous desactives v11.3.0)
    │
    ├── Execution (live)
    │     market_open/close via SDK, fill price depuis avgPx reponse
    │     Account state refresh chaque 60s (equity, unrealized, margin)
    │     Reconciliation bot vs exchange chaque scan horaire
    │     Failed closes retries via _failed_closes set
    │     pause/reset en threadpool (non-bloquant event loop)
    │     Telegram alerts dans daemon thread
    │
    ├── Persistence
    │     JSON atomic (state + positions + feature cache + capital)
    │     SQLite source de verite (trades, trajectories, market, ticks, events)
    │     File lock fcntl evite deux instances sur meme state
    │     Survit aux restarts (feature cache < 2h restaure)
    │
    └── Dashboard (FastAPI)
          Paper :8097 / Live :8098 / Bot 2 :8099 / Admin :8090
          Auth HMAC signee, sessions 30j
          /api/health, /api/state, /api/signals, /api/trades, /api/pnl
          /api/chart/{sym}, /api/close/{sym}, /api/capital (DCA)
          /api/pause, /api/resume, /api/reset
```

---

## Deploiement

```bash
# Paper (:8097, $1000 simule)
TG_BOT_TOKEN= TG_CHAT_ID= HL_ROOT_PATH=/paper \
  nohup .venv/bin/python3 -m analysis.reversal > analysis/output/reversal_v10.log 2>&1 &

# Live (:8098, capital alloué via DCA, voir start_bots.sh pour HL_CAPITAL initial)
HL_MODE=live HL_CAPITAL=300 WEB_PORT=8098 HL_OUTPUT_DIR=analysis/output_live HL_ROOT_PATH=/bot \
  nohup .venv/bin/python3 -m analysis.reversal > analysis/output_live/reversal_v10.log 2>&1 &

# Junior (:8099, modèle agent wallet — voir section Junior plus bas)
HL_PRIVATE_KEY="$JUNIOR_HL_PRIVATE_KEY" HL_MODE=live \
HL_ACCOUNT_ADDRESS=0xb65d5e52f229B1dAA6534034d7805A82dB7956Fe \
HL_EQUITY_MODE=perps \
HL_CAPITAL=0 WEB_PORT=8099 HL_OUTPUT_DIR=analysis/output_live2 HL_ROOT_PATH=/junior \
  nohup .venv/bin/python3 -m analysis.reversal > analysis/output_live2/reversal_v10.log 2>&1 &
```

Auto-restart : `@reboot /home/crypto/start_bots.sh`. Accessible via `https://echonym.fr/bot/` (live), `https://echonym.fr/paper/` (paper), `https://echonym.fr/junior/` (Junior), `https://echonym.fr/crypto/` (admin panel multi-bots).

### Junior — modèle API agent wallet (v11.7.17+)

Junior tourne en LIVE mais avec une architecture de wallet **différente du live** :

| | Live (`:8098`) | Junior (`:8099`) |
|---|---|---|
| Variable env clé | `HL_PRIVATE_KEY` | `JUNIOR_HL_PRIVATE_KEY` |
| Adresse dérivée | `0x6E2a…2d5d` (= wallet maître) | `0x4EAb…3F7e` (= API agent, sans fonds) |
| Adresse maître (où sont les USDC) | `0x6E2a…2d5d` (la même) | `0xb65d…956Fe` (séparée, autorisée par le master via HL Settings → API) |
| `HL_ACCOUNT_ADDRESS` | non défini | `0xb65d5e52…956Fe` (dans `start_bots.sh`) |
| Modèle d'équité | `HL_EQUITY_MODE` vide → `spot.total + unrealized` (le hold spot inclut la marge perps) | `HL_EQUITY_MODE=perps` → `marginSummary.accountValue + spot_usdc` (tous les fonds sont en perps, spot=$0) |

**Pourquoi deux formules d'équité ?** Hyperliquid a un modèle où le spot USDC sert de collatéral aux positions perps via un champ `hold` sur le solde spot. Pour le live (USDC d'origine en spot, partiellement bloqué pour les perps), `spot.total + unrealized` donne l'équité totale correctement. Pour Junior (l'utilisateur a transféré tout le spot vers le perps via HL UI à la création), `spot=0` et la formule legacy retournerait $0 — d'où la branche `perps` qui lit `marginSummary.accountValue` directement.

**Renouvellement de clé Junior** : les API agent wallets HL ont une expiration paramétrable à la création (typiquement 90-180j). À l'expiration : générer une nouvelle clé, mettre à jour `JUNIOR_HL_PRIVATE_KEY` dans `.env`, **autoriser la nouvelle adresse comme agent du master `0xb65d…956Fe`** dans HL Settings → API, restart Junior.

**Supervisor LLM** (rapport quotidien Telegram) : `0 8 * * * /home/crypto/.venv/bin/python3 /home/crypto/supervisor.py >> /home/crypto/analysis/output/supervisor.log 2>&1`. Config via `.env` (`ANTHROPIC_API_KEY`, `SUPERVISOR_MODEL=claude-haiku-4-5`, `SUPERVISOR_ENABLED=1`). Coût mesuré : ~$0.017/run avec cache hit → ~$0.50/mois.

Resultats rolling simules avec les parametres actuels : voir `docs/backtests.md` (a regenerer apres tout changement de parametres avec `python3 -m backtests.backtest_rolling`).

---

## Recherche (26 backtests)

### Methode de validation

Chaque signal doit passer : (1) train/test split, (2) Monte Carlo z > 2.0, (3) portfolio integration, (4) walk-forward > 50%.

### Ce qui a ete teste et rejete

1500+ regles testees. Cette section sert de **registre anti-reprise** : toute hypothese listee ici a ete testee, a echoue au walk-forward (au moins une fenetre 28m/12m/6m/3m ne passe pas, ou DD degrade substantiellement), et ne doit pas etre resuggeree sans nouvelle donnee. **Avant de proposer un filtre / gate / exit / sizing, verifier ci-dessous qu'il n'est pas deja dans ce registre.**

Format : `Hypothese — Backtest source → Verdict court`.

**Exits (stops, trailing, flats)**
- Trailing stop global (toutes strategies) — `backtest_exits.py` → rejete : signaux mean-reversion oscillent, le trailing coupe les gagnants qui passent par une pause temporaire.
- Breakeven stop apres MFE > X — `backtest_exits.py` → rejete : coupe des gagnants qui repartent.
- Flat exit (apres N bougies) — `backtest_exits.py` → rejete : aucune config ne bat la baseline 4/4.
- ATR stops adaptatifs — `backtest_exit_battery.py` → rejete 0/4.
- OI exit miroir (sortie si OI se retourne) — `backtest_exit_battery.py` → rejete 0/4.
- MAE cry-uncle (sortie si MAE atteint X) — `backtest_exit_battery.py` → rejete 0/4.
- Inverse-exit sur signal oppose — `backtest_exit_battery.py` → +$20k sur 28m mais perd sur 12m/6m/3m (overfit).
- Exit dynamique sur erosion divergence — `backtest_div_erosion_exit.py` → rejete : 10 variantes (drop absolu, ratio, flip), toutes negatives ≥2 fenetres. Divergence est momentum, pas reversal.
- Trailing stop S5 (symetrique au S10 trailing) — `backtest_s5_trailing.py` → rejete : 11 variantes (trigger 600/800/1000/1200/1500/2000, offset 150-800), toutes 4/4 negatives. Les S5 qui atteignent MFE +2000 bps continuent generalement → lock = amputer les runners.
- Momentum reversal exit (adverse price move sur 1/2/3 bougies pendant en profit) — `backtest_reversal_exit.py` → rejete : 11 variantes (lookback 1-3c × adverse 300-1500 × gain 300-1500), toutes 4/4 negatives. Un move adverse intra-trade est du bruit, pas un top signal.
- ML entry filter multi-feature sur S5 (logistic regression, 7 features entree) — `backtest_ml_entry_filter.py` → rejete : au seuil optimal (0.65) ne filtre que 2-6 trades/window avec precision 100%, mais gains relatifs (<$2k) sont dans la variance d'echantillonnage ; 28m window n/a (donnees de training insuffisantes). Audit rollbacks vs kept (`backtest_mfe_rollback_audit.py`) : btc30 entree +168 vs -68 median, Δ divergence peak→exit -462 vs -21, mais overlap ~70% rend inexploitable.
- Early MFE absence exit (cut si MFE < threshold a T+H heures) — `backtest_early_mfe_exit.py` → rejete : 13 variantes (H=8/12/16/24/32h × MFE<100/200/300/500 × all/S5/non-macro), aucune 4/4. E4 (H=12h, MFE<200) gagne +$30k sur 28m mais perd sur 12m/6m → curve-fit. Le pattern apparent en live (big losers n'ont jamais MFE > +303 bps sur 4 trades / 30j) est de la variance statistique : sur 1100+ trades walk-forward, beaucoup de trades MFE-faibles-a-12h finissent runners plus tard. Cutter tot ampute autant de winners que de losers. Le ratio asymetrique avg_win/avg_loss = 0.69x est intrinseque a une strategie momentum-tolerante.
- Trailing stop generalise S5/S8/S9 (miroir per-strat du trailing S10) — `backtest_trailing_sweep.py` → rejete : 45 combos (3 strats × 5 triggers {300,400,500,600,800} × 3 offsets {100,150,200}), 0 passe le 4/4. S5 catastrophique (avg ΔPnL -1800 a -2200pp), S8 mauvais (-400 a -1400), S9 le moins pire (-50 a -700) avec quelques 1/4 isolees. Le pattern observe en LIVE 60-trades (trailing semble sauver $50-300) ne tient pas sur 1100+ trades : le bot tire son edge des timeouts patients qui passent par des dips (MFE +400 → dip → MFE +800 → timeout +500). Trailing coupe au premier dip et rate la vraie sortie. Confirme que l'asymetrie loss/win est structurelle.

**Entrees (filtres, gates)**
- Filtre regime BTC 30d sur S5 LONG (seuils 200/500/1000/1500 bps) — `backtest_entry_filters.py` → rejete 0/4, curve-fit du regime recent.
- Filtre OI delta a l'entree sur S5 (seuils 400/600/800/1000 bps) — `backtest_entry_filters.py` → rejete 4/4 tres negatif.
- Combo filtre BTC + OI sur S5 — `backtest_entry_filters.py` → rejete : echecs cumulatifs.
- OI gates externes (7 singles + 3 combos : funding abs/dir, OI delta abs/align long/short, premium abs, BTC vol high/low, n_signals, sessions) — `backtest_external_gates.py`, `backtest_oi_gates.py` → rejetes, seul **OI gate LONG** (`oi_delta_24h < -10%`) passe → retenu en v11.4.9.
- vol_z min filter — rejete 0/4.

**Sizing**
- Sizing adaptatif WR / Sharpe / rolling P&L — `backtest_adaptive_sizing.py` → degrade partout.
- Reduction sizing S9 (multipliers ×0.00 a ×0.75) — `backtest_s9_sizing.py` 5 multipliers × 5 windows (28m/12m/6m/3m/YTD 2026), tous 5/5 negatifs. Reduction lineaire monotone : plus on reduit, plus on perd. Baseline backtest S9 contribue +$20 372 sur 130 trades en 28m (27% du P&L total). YTD 2026 : 14 trades S9 = +$400 avg +$29/trade. Live a vu 3 trades S9 -$33 (avg -$11/trade) = ~+3σ ecart par variance extreme sur petit sample, pas drift.
- Extreme-condition entry filters (S9 r24h cap a 1500/2000/2500 + S5 vol_z cap a 4/5/6 + 2 combos) — `backtest_extreme_filters.py` 8 variantes × 5 windows, aucune ne passe 5/5. Patterns visibles dans live big losers (BLUR S9 r24h=2325 -$20, LDO S5 vol_z=7.2 -$21) ne se generalisent pas : les S9 a r24h>2000 et S5 a vol_z>5 contiennent autant de winners futurs que de losers actuels. Confirme l'overlap ~70% du `backtest_mfe_rollback_audit.py`.
- Kill-switch drift par strategie (pause si WR < X sur N trades) — `backtest_drift_killswitch.py` → aucune config N/seuil ne bat la baseline 4/4.
- OI sizing continu (alpha 0.01-0.20 × lookback 6h/24h) — `backtest_oi_sizing.py` → meme pattern que les gates, rejete.
- Vol_z conditionnel sizing reduction (×0.5 / ×0.7 sur S5/S9/S5+S9 quand entry vol_z ≥ {1.5, 2.0, 2.5, 3.0}) — `backtest_volz_sizing.py` → 24 combos × 4 windows, 0/24 passe 4/4. Tous deltas negatifs partout. Confirme que les entrees high-vol_z incluent autant de big winners que de big losers : reduire le size ampute le compounding sur les memes trades qui produisent l'edge. Le sizing-reduction n'est pas une issue, comme deja vu pour `Sizing adaptatif WR/Sharpe` et `OI sizing`.
- S10 pocket (capital dedie S10) — commentaire `S10_CAPITAL_SHARE = 0` dans `config.py` : "no pocket — backtest: +48% P&L vs 15%". Tester un pocket = deja teste, perd 48%.

**Signaux / familles rejetees**
- OI divergence S11 (6 variantes A-F) — rejete.
- 378 variantes SHORT explorees (momentum, mean-reversion, volume, squeeze adverse, pairs) — aucune ne passe, seul S10 SHORT (squeeze fade) retenu apres walk-forward.
- S14 dispersion collapse breakout (cross-sectional std(ret_42h) p<X + vol_ratio<Y + |ret_6h|>500) — `backtest_s14_dispersion.py` 9 variantes, toutes 4/4 negatives. Bonnes performances en isolation (`backtest_new_signals.py` : +$1538 sur 426 trades, +220 bps avg), echec en portfolio par effet de substitution : prend 280-488 slots aux S5 ($+8/trade) pour ~$0.5/trade S14. Confirme le pattern S2/S6 (isolation OK, portfolio KO).
- Block opposite-direction entries dans le meme secteur (anti-pair-trade) — `backtest_block_opposite_sector.py` rejete 4/4 sur 3 windows recentes. Observation : 29% des trades S5 backtest sont des pair-legs (LONG+SHORT meme secteur concurrents) et contribuent +20-40% du P&L total. Bloquer les paires supprime autant de gros gagnants (e.g. COMP+CRV +$29) que de variantes perdantes (e.g. GALA+SAND −$14 en live). Le pair-trade intra-secteur est une source de P&L, pas un bug.
- Pairs trading — rejete.
- Funding carry — rejete.
- Premium mean reversion — rejete.
- Correlation breakdown — rejete.
- Weekend effects — rejete.
- Cross-momentum — rejete.
- Volume exhaustion — rejete.
- Sessions Asia/EU/US — rejete.
- Skip S5 weekend / Sunday-only (filtre per-strat × session) — `backtest_session_filter.py` 10 variantes (S5/S8/S9/S10 × {weekend, Fri21+WE, Sat, Sun, Night}, plus skip-all). Meilleurs : `skip S5 weekend` et `skip S5 Sun only` font 3/4 (28m +12000pp, 6m+3m positifs) mais 12m negatif (-127pp). Le pattern observe en LIVE+PAPER (S5-WE WR=28%, total -$215 sur 25 trades) est cale sur de vieilles dynamiques (28m beneficie, 12m casse). Rejet : pas robuste a la fenetre recente. Skip ALL weekend / Night = 0/4 partout (les autres strats gagnent le weekend).
- Token rotation (retirer les tokens less-performant mensuellement) — rejete : la performance tourne trop vite, le retrait des pires casse le compounding.
- Regime gating general (pause selon BTC regime) — rejete.

**Meta (ML, GP)**
- Genetic programming — rejete.
- Machine learning classification / regression — rejete.

**Blacklist etendue (session 2026-04-19)**
- Blacklist de WLD, DOGE-SHORT, BLUR, OP LONG, COMP SHORT, MINA LONG, APT LONG, SNX SHORT, CRV SHORT, DOGE LONG — `backtest_blacklist_candidates.py` → **effet de substitution** : chaque token est 4/4-negatif individuellement MAIS son retrait libere un slot qui va vers un candidat encore pire. Ex retirer COMP SHORT coute −$61 264 sur 28m. La blacklist actuelle `{SUI, IMX, LINK}` est un optimum local pour la structure de ranking actuelle.

---

**Passent le walk-forward** (retenu en production) :
- **Trailing stop S10-specifique** (v11.4.0)
- **OI gate LONG** (v11.4.9) — seul gate externe parmi 12 testes
- **Trade blacklist SUI/IMX/LINK** (v11.4.10)
- **Dead-timeout early exit D2** (v11.7.2)
- **Dispersion gate S5+S9** (v11.7.28) — `DISP_GATE_BPS=700`, `DISP_GATE_STRATEGIES={S5,S9}`

### Trade blacklist (v11.4.10)

Après autopsie des 50 pires perdants sur 28m (`backtest_worst_losers.py`), quatre tokens sont apparus net-négatifs sur **toutes les fenêtres walk-forward** (28m/12m/6m/3m) : SUI, IMX, MINA, LINK.

Test walk-forward (`backtest_loser_filters.py`) sur chaque sous-ensemble :

| Blacklist | 28m | 12m | 6m | 3m | Verdict |
|---|---|---|---|---|---|
| `{SUI}` | +$35 603 | +$3 578 | +$567 | +$63 | 4/4 ✓ |
| `{SUI, IMX}` | +$48 586 | +$5 979 | +$951 | +$146 | 4/4 ✓ |
| **`{SUI, IMX, LINK}`** | **+$49 687** | **+$5 704** | **+$1 077** | **+$207** | **4/4 ✓ — retenu** |
| `{SUI, IMX, MINA, LINK}` | +$9 839 | +$3 758 | +$1 093 | +$126 | 4/4 ✓ mais moins bon |

Ajouter MINA réduit paradoxalement le gain (path-dependence : libérer un slot quand MINA est bloquée permet à un autre trade moins bon de prendre sa place). On s'arrête à 3 tokens.

Interprétation causale :
- **SUI** : L1 au comportement atypique (gros gainers suivis de retournements persistants). Les signaux mean-reversion se font piéger par les tendances longues.
- **IMX** : gaming peu liquide → whipsaw sur les stops.
- **LINK** : blue-chip mais dynamique OI très différente. Les signaux calibrés sur des alts plus volatiles ne matchent pas.

Implémentation : `TRADE_BLACKLIST = {"SUI", "IMX", "LINK"}` dans `analysis/bot/config.py`, enforced dans `trading.rank_and_enter()`. Les tokens restent dans `TRADE_SYMBOLS` pour continuer la collecte de données (dispersion, ré-activation future). Kill-switch : vider le set.

### Dead-timeout early exit (v11.7.2)

Quand un trade entre dans les 12 dernières heures de son hold, **s'il n'a jamais rien donné** (MFE ≤ +150 bps) **ET s'il est déjà loin sous l'eau** (MAE ≤ −1000 bps) **ET s'il est toujours scotché près de son plus bas** (current ≤ MAE + 300 bps), on le ferme immédiatement plutôt que d'attendre le timeout qui ferme en général à la MAE.

**Logique causale** : ces conditions isolent un trade "mort" — il n'a jamais révélé d'upside (MFE plafonnée), il est déjà loin dans le rouge (MAE profonde), et il n'a montré aucun signe de reprise dans les derniers ticks (current ≈ MAE). Statistiquement, il ne rebondira pas d'ici 12h. On cristallise la perte à la valeur courante (souvent meilleure que le timeout).

**Aucun gagnant n'est touché par construction** : un trade qui finira positif aura, par définition, montré un MFE > 0 à un moment — si à T−12h son MFE est ≤ +150 bps, ce n'est pas un gagnant déguisé.

**Walk-forward** (`backtest_early_exit_d.py`, 7 variantes D1–D7, data 2026-04-17) :

| Variante | 28m Δ | 12m Δ | 6m Δ | 3m Δ | 4/4 positif | DD stable |
|---|---|---|---|---|---|---|
| D1 (mfe≤100, mae≤−800, slack=200) | +$116 513 | +$1 952 | −$39 | −$27 | ✗ (bruit) | ✓ (DD +14pp) |
| **D2 (mfe≤150, mae≤−1000, slack=300)** | **+$49 322** | **+$1 405** | **+$46** | **+$21** | **✓** | **✓ (inchangé)** |
| D3 (mfe≤200, mae≤−600, slack=300) | +$51 577 | +$1 792 | +$170 | +$41 | ✓ | DD 6m/3m +2pp |
| D4 lead=6h | −$4 682 | −$2 869 | −$514 | −$31 | ✗ | ✗ |
| D5 lead=2h | +$90 350 | −$114 | −$71 | −$37 | ✗ | ✓ |
| D6 mfe≤0 | +$53 353 | +$767 | −$84 | −$47 | ✗ | ✓ |
| D7 mae≤−500 | +$29 058 | −$465 | −$732 | −$373 | ✗ | ✗ |

**D2 retenue** : seule variante strictement 4/4 positive avec DD inchangé. Le gain est linéaire avec la fenêtre (+$21 sur 3m → +$49 322 sur 28m), signature d'un vrai filtre et non d'un fit.

**Constantes** : `DEAD_TIMEOUT_LEAD_HOURS=12`, `DEAD_TIMEOUT_MFE_CAP_BPS=150`, `DEAD_TIMEOUT_MAE_FLOOR_BPS=-800` (resserré de −1000 en v11.7.16), `DEAD_TIMEOUT_SLACK_BPS=300` dans `analysis/bot/config.py`. Check placé dans `trading.check_exits()` après stops/trailing, avant `close_position`. Kill-switch : `DEAD_TIMEOUT_MFE_CAP_BPS = -99999`.

**Resserrement MAE_FLOOR (v11.7.16)** : passage de −1000 à −800 bps. Motivation : les pertes S5 récentes (PENDLE, DYDX) atteignaient MAE −1000 à −1229 bps avant déclenchement. Walk-forward `backtest_s5_stops` variante V4 : +$9 554 sur 28m (S5 isolé +$6 278), bruit mineur sur 12m/6m/3m (−$198/−$104/−$48), DD inchangé ou amélioré. Pas strictement 4/4 mais profil risque/reward asymétrique — le long terme gagne, le court terme est au niveau du bruit. Kill-switch : remettre à −1000.

### Plateau d'optimisation (post session 2026-04-19)

Après D2 (v11.7.2), le bot est sur un plateau : les gisements visibles à l'EDA (erosion divergence, filtres entree BTC/OI, blacklist etendue) ne survivent pas au walk-forward pour cause d'**effet de substitution** (un slot libere va vers un candidat plus mauvais) ou d'**overlap statistique** trop large entre les groupes gagnant/perdant. Chaque tentative de cette session est listee dans le registre de rejets ci-dessus avec son backtest source.

### OI gate LONG (v11.4.9)

Skip une entree LONG si l'OI du token a chute de plus de 10% sur les dernieres 24h. Intuition causale : OI qui baisse = longs qui sortent = pression vendeuse encore active, entrer en LONG = couteau qui tombe. Le gate aide surtout **S8 (capitulation LONG)** et **S5 LONG** — causal : une flush de capitulation sur fond de delevering non termine n'est pas une opportunite.

**Walk-forward** (`backtest_external_gates.py`, `backtest_oi_gate_validate.py`, data jusqu'au 2026-04-16) :

| Fenetre | Baseline | Avec gate | Delta | DD |
|---|---|---|---|---|
| 28m | +$54 389 | +$56 887 | **+$2 498** | inchange |
| 12m | +$9 005 | +$9 821 | **+$816** | inchange |
| 6m | +$3 190 | +$3 570 | **+$380** | inchange |
| 3m | +$1 178 | +$1 430 | **+$252** | inchange |

4/4 fenetres positives, zero impact DD, plateau stable de threshold 1000-1200 bps. Parametre `OI_LONG_GATE_BPS = 1000`. Skip rate faible (~3%). Evalue sur 12 gates externes testes (funding absolu, funding directionnel, OI delta absolu, OI alignement long/short, premium, BTC vol high/low, number of concurrent signals, sessions Asia/EU/US) : **seul gate a passer 4/4**. Implementation : fail-open pendant les 23 premieres heures apres un restart (insufficient OI history), gate active des que `len(oi_history) >= 23h`.

### Backtests d'optimisation portfolio (cette session)

| Backtest | Resultat |
|---|---|
| `backtest_slot_reservation.py` | **Macro 2 / Token 3** optimal (DD -32% vs -44%) |
| `backtest_signal_boost.py` | S2 early exit +87% P&L, S9/S10 threshold inchanges |
| `backtest_signal_boost2.py` | **S9 adaptive stop +54%**, S2 retire, token slots 3→4 (+157%) |
| `backtest_short_search2.py` | 6 SHORT ideas, 150+ variants — **nothing z>2.0** |
| `backtest_1h_fast.py` | 1h signals: **S9-fast (fade ±3% 2h)** seul survivant, 588t +88bps |
| `backtest_1h_fast2.py` | 6 more 1h patterns — nothing passes train+test |
