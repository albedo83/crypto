# Multi-Signal Bot v11.3.3

Bot de trading automatique sur 28 altcoins Hyperliquid. Paper ou live trading. 12 modules Python dans `analysis/bot/` + SQLite tick database.

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

### S10 — Squeeze + faux breakout (config gelee)

**Quoi** : compression de range → faux breakout → reintegration → fade le breakout.
**Pourquoi** : le faux breakout piege les traders, le vrai mouvement va dans l'autre sens.
**Type** : contrarien / pattern. Mode B (fade).
**Frequence** : ~50/mois sur 28 tokens.
**Hold** : 24h. **Stop** : -12.5% de mouvement de prix. **Capital** : part entiere (pocket separe supprime — backtest +48% P&L).
**z-score** : 3.66. Config gelee (ne pas re-optimiser).
**Params** : squeeze_window=3 (12h), vol_ratio<0.9, breakout 50% du range, reintegration dans 2 candles.

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
| **Frais simules** | 12 bps par leg (24 bps round-trip) | 7 taker + 3 slippage + 2 funding. |
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
- Tableau P&L par strategie (signal_drift)
- Regime de marche (BULL/BEAR/NEUTRAL)
- Endpoint `/api/health` (status, price_age, scan_age, exchange_ok)
- Resume quotidien Telegram a minuit UTC
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
TG_BOT_TOKEN= TG_CHAT_ID= \
  nohup .venv/bin/python3 -m analysis.reversal > analysis/output/reversal_v10.log 2>&1 &

# Live (:8098, $260 reel)
HL_MODE=live HL_CAPITAL=260 WEB_PORT=8098 HL_OUTPUT_DIR=analysis/output_live \
  nohup .venv/bin/python3 -m analysis.reversal > analysis/output_live/reversal_v10.log 2>&1 &
```

Auto-restart : `@reboot /home/crypto/start_bots.sh`. Accessible via `https://echonym.fr/bot/` (live), `https://echonym.fr/paper/` (paper), `https://echonym.fr/crypto/` (admin panel multi-bots).

---

## Recherche (26 backtests)

### Methode de validation

Chaque signal doit passer : (1) train/test split, (2) Monte Carlo z > 2.0, (3) portfolio integration, (4) walk-forward > 50%.

### Ce qui a ete teste et rejete

1500+ regles testees. Rejetes : regime gating, trailing stop, signal exit, 378 variantes SHORT, pairs trading, funding carry, premium mean reversion, sessions, correlation breakdown, genetic programming, ML, weekend effects, dispersion, volume exhaustion, cross-momentum.

### Backtests d'optimisation portfolio (cette session)

| Backtest | Resultat |
|---|---|
| `backtest_slot_reservation.py` | **Macro 2 / Token 3** optimal (DD -32% vs -44%) |
| `backtest_signal_boost.py` | S2 early exit +87% P&L, S9/S10 threshold inchanges |
| `backtest_signal_boost2.py` | **S9 adaptive stop +54%**, S2 retire, token slots 3→4 (+157%) |
| `backtest_short_search2.py` | 6 SHORT ideas, 150+ variants — **nothing z>2.0** |
| `backtest_1h_fast.py` | 1h signals: **S9-fast (fade ±3% 2h)** seul survivant, 588t +88bps |
| `backtest_1h_fast2.py` | 6 more 1h patterns — nothing passes train+test |
