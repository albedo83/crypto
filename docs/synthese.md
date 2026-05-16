# Synthèse complète du bot

Document de référence unique. État au 2026-05-16, **bot v12.7.0**.

Lecture recommandée dans l'ordre. Sections indépendantes — utilise la table des matières pour piocher.

---

## Table des matières

1. [Vue d'ensemble](#1-vue-densemble)
2. [Comment le bot décide de trader](#2-comment-le-bot-décide-de-trader)
3. [Les 5 signaux détaillés](#3-les-5-signaux-détaillés)
4. [Le sizing — comment la taille est calculée](#4-le-sizing--comment-la-taille-est-calculée)
5. [Le modulator macro adaptatif (v11.10 → v12.2)](#5-le-modulator-macro-adaptatif-v1110--v122)
6. [Les exits — comment le bot ferme une position](#6-les-exits--comment-le-bot-ferme-une-position)
7. [Monitoring & alertes](#7-monitoring--alertes)
8. [Sécurité & kill-switches](#8-sécurité--kill-switches)
9. [Historique de la recherche](#9-historique-de-la-recherche)
10. [Pourquoi on n'ajoute plus de filtres](#10-pourquoi-on-najoute-plus-de-filtres)
11. [État actuel — paramètres en vigueur](#11-état-actuel--paramètres-en-vigueur)
12. [Comment lire le dashboard](#12-comment-lire-le-dashboard)
13. [Glossaire](#13-glossaire)

---

## 1. Vue d'ensemble

### En une phrase

Bot de trading **mean-reversion + momentum** sur Hyperliquid (DEX, accessible depuis la France), opérant sur **35 altcoins** (v12.7.0 : 29 base + 6 ajouts curés) avec 5 stratégies indépendantes, **2x de levier**, paper/live/junior en parallèle.

### Les 3 bots en parallèle

```
Paper  :8097  $1000 simulé     — pas de Telegram, validation tests
Live   :8098  $500 réel        — wallet HL_PRIVATE_KEY
Junior :8099  $300 réel        — API agent wallet (master + signer séparés)
Admin  :8090  panel agrégé
```

Tous tournent **le même code** mais sur des wallets / capitaux distincts. Permet d'isoler le live de l'expérimentation.

### Architecture du système

```
Hyperliquid REST API (read)
    ├── prices + OI + funding (toutes les 60s)
    ├── 4h candles (au scan, toutes les ~heures)
    └── Yahoo Finance (DXY, cached 48h)
            │
            ▼
    analysis/bot/ (12 modules, asyncio process)
    ├── config.py     — constantes, env, paramètres signaux
    ├── models.py     — dataclasses SymbolState/Position/Trade
    ├── net.py        — HTTP retry, fetchers, Telegram
    ├── features.py   — features techniques, OI, btc_z, crowding
    ├── signals.py    — détection S1/S5/S8/S9/S10
    ├── db.py         — schéma SQLite, événements
    ├── persistence.py — state.json save/load
    ├── exchange.py   — SDK Hyperliquid (live)
    ├── trading.py    — entries (ranking, limits), exits, P&L
    ├── web.py        — FastAPI dashboard + /api/*
    ├── bot.py        — orchestrateur principal
    └── main.py       — entry point
            │
            ▼ (live mode uniquement)
Hyperliquid SDK (write)
    ├── market_open / market_close
    └── reconcile (synchronisation positions)
```

### Mode papier vs live

```
Paper (HL_MODE=paper, défaut):
  - Aucune écriture exchange
  - Lit les prix réels via API publique HL
  - Simule positions en mémoire + state.json
  - Useful pour tester nouvelle config sans risque
  
Live (HL_MODE=live):
  - Place vraies orders via SDK
  - Reconcile avec exchange à chaque scan
  - Stop loss et exits réels
```

---

## 2. Comment le bot décide de trader

### Cycle de scan (toutes les ~heures par défaut)

```
┌─ Fetch candles 4h pour 35 tokens (BTC/ETH refs en sus)
│
├─ Calculer features:
│   - btc_z (z-score 6m du return BTC 30j)
│   - Per-token: ret_24h, drawdown, vol_z, etc.
│   - Sector divergence (token vs sector mean)
│   - Cross-market dispersion
│   - OI delta 24h
│
├─ Pour chaque token, détecter signaux S1/S5/S8/S9/S10
│
├─ Filtrer signaux:
│   - TRADE_BLACKLIST (SUI, IMX, LINK exclus globalement)
│   - Dispersion gate (S5/S9 skipés si dispersion > 700bps)
│   - OI gate LONG (skip LONG si OI tombé -10%/24h)
│   - max_per_sector, max_macro, max_token slots
│
├─ Pour chaque signal restant:
│   - Calculer size baseline (strat_size)
│   - Appliquer modulator adaptatif si applicable
│   - Skip si size < $10 (modulator_floor)
│   - Exécuter order (paper: simulation, live: SDK)
│
└─ Check exits sur positions ouvertes:
    - Catastrophe stop (-1250 bps)
    - Trailing stop (S10 seulement)
    - Dead-timeout (-500 bps + MFE ≤ 150 à T-12h)
    - S9 early exit (-500 bps après 8h)
    - Runner extension (S9 winners avec MFE > 1200)
    - Timeout naturel (hold time épuisé)
```

### Limites portfolio

```
MAX_POSITIONS         = 6   positions simultanées max
MAX_SAME_DIRECTION    = 4   max 4 LONG ou 4 SHORT
MAX_PER_SECTOR        = 2   max 2 positions par secteur
MAX_MACRO_SLOTS       = 3   max 3 trades macro (S1)
MAX_TOKEN_SLOTS       = 4   max 4 trades token-level (S5/S8/S9/S10)
LEVERAGE              = 2x  optimal cross-margin
```

### Ordre de priorité des signaux

Quand plusieurs signaux fire simultanément :
```
1. Priorité par STRAT_Z (statistical strength):
   S9 (z=8.71) > S8 (z=6.99) > S1 (z=6.42) > S5 (z=3.67) > S10 (z=3.66)
2. Au sein du même z, priorité par strength (magnitude du signal)
```

Donc S9 prend les premiers slots si plusieurs signaux fire ensemble.

---

## 3. Les 5 signaux détaillés

### S1 — BTC momentum LONG alts

**Concept** : quand BTC explose (+20% en 30 jours), les altcoins ont tendance à suivre. Le bot LONG les alts pour capter la vague.

**Déclencheur** : `btc_30d > +2000 bps` (BTC a fait +20% en 30 jours).

**Direction** : toujours LONG sur les alts.

**Hold** : 72h.

**Stop** : -1250 bps (-12.5% de mouvement de prix).

**Modulator** : `α = +0.5` → amplifié en bull (×1.5 typique).

**Profil** : trend-follow. Quand BTC monte fort, S1 fire sur les alts qui montrent du momentum.

**WR live ~45j** : 100% (2 trades, échantillon trop petit pour conclure).

### S5 — Sector divergence (suivi du momentum sectoriel)

**Concept** : quand un token diverge fortement de son secteur (out- ou under-performance), le bot **suit la divergence** (trend-follow).

**Déclencheur** :
```
|divergence sector| ≥ S5_DIV_THRESHOLD (1000 bps = ±10%)
ET vol_z ≥ S5_VOL_Z_MIN (1.0 = volume au-dessus de la moyenne)
ET dispersion_24h < DISP_GATE_BPS (700 — sinon skip)
```

**Direction** :
- `divergence > 0` (token outperform) → LONG (suit la hausse)
- `divergence < 0` (token underperform) → SHORT (suit la baisse)

**Hold** : 48h.

**Stop** : -1250 bps.

**Modulator** :
- S5 LONG : pas de modulator (LONG dans tous régimes)
- S5 SHORT : `α = -0.5` → réduit en bull (×0.3-0.5 typique), amplifié en bear (×1.5)

**Profil** : trend-follow, mais asymétrique. Les LONGs gagnent net, les SHORTs perdent en bull.

**SIGNAL_MULT** : **3.25** (relevé de 2.50 en v11.9.2 pour compenser partial fills observés en live).

**WR live ~45j** : 53% (R/R 0.93 — juste-positif).

### S8 — Capitulation flush LONG

**Concept** : quand un token a chuté brutalement + volume extrême + BTC aussi en baisse → c'est la "capitulation finale" avant rebound.

**Déclencheur** :
```
drawdown > S8_DRAWDOWN_THRESH (-3000 bps = -30% from 30d high)
ET vol_z > S8_VOL_Z_MIN (1.0)
ET ret_24h < S8_RET_24H_THRESH (-50 = encore en baisse)
ET btc_7d < S8_BTC_7D_THRESH (-300 bps = BTC down 3%+)
```

**Direction** : toujours LONG (bet sur le rebond).

**Hold** : 60h.

**Stop** : -750 bps (plus serré que les autres car risque de chute supplémentaire).

**Modulator** : `α = -0.5` → amplifié en bear (régime où S8 fonctionne).

**Profil** : capitulation buy. Rare mais profitable quand fire.

**WR live ~45j** : 0% (1 trade seul, sample insuffisant).

### S9 — Fade extreme ±20% / 24h

**Concept** : quand un token a fait +20% (ou -20%) en 24h, c'est souvent un excès → bot fade dans la direction opposée.

**Déclencheur** : `|ret_24h| ≥ S9_RET_THRESH (2000 bps = ±20%)`.

**Direction** :
- `ret_24h > 0` (pump) → SHORT (fade la hausse)
- `ret_24h < 0` (dump) → LONG (fade la baisse)

**Hold** : 48h.

**Stop** : adaptatif via `S9_ADAPTIVE_STOP=True`. Plus le move est extrême, plus le stop est tight (formule `max(-1250, -500 - abs(ret_24h)/8)`).

**S9 early exit** : si pnl < -500 bps après 8h → exit immédiat (winners reverten vite, losers persistent).

**Runner extension** (v11.7.32) : si à 48h timeout, MFE ≥ +1200 bps et current ≥ 30% du MFE → extend hold de 12h pour capter la continuation.

**Modulator** : `α = -0.5` → amplifié en bear (mean-reversion fonctionne mieux en bear).

**Profil** : mean-reversion sur excès extrêmes.

**WR live ~45j** : 25% (4 trades, sample insuffisant mais drain de -$39).

### S10 — Squeeze + faux breakout

**Concept** : quand un token est dans un range serré (Bollinger narrow) pendant 3 candles 4h, puis breakout brusque suivi de retour dans le range → c'est un "faux breakout", parie sur retour à la moyenne.

**Déclencheur** :
```
3 candles 4h consécutives avec range_pct < S10_VOL_RATIO_MAX (0.9)
PUIS breakout > S10_BREAKOUT_PCT (0.5% du range)
PUIS retour dans le range en S10_REINT_CANDLES (2 candles)
```

**Direction** : opposite du faux breakout (mean-reversion).

**Hold** : 24h.

**Stop** : -1250 bps.

**Trailing stop natif** : si MFE > +600 bps → plancher à `MFE - 150 bps`. Verrouille les gains S10.

**Modulator** : **non actif** (S10 jugé regime-instable dans le sliding walk-forward).

**Filtres v11.3.4** :
- `S10_ALLOW_LONGS = False` (SHORT-only — LONGs perdaient en OOS)
- `S10_ALLOWED_TOKENS` : 13 tokens whitelistés (AAVE, APT, ARB, BLUR, COMP, CRV, INJ, MINA, OP, PYTH, SEI, SNX, WLD)

**Profil** : mean-reversion technique, le strategy le plus stable du bot.

**WR live ~45j** : 68% (gros winners + trailing protège).

---

## 4. Le sizing — comment la taille est calculée

### Formule de base (`strat_size`)

```python
size = capital × pct × z_weight × haircut × signal_mult

avec:
  capital      = _capital + _total_pnl (compounding inclus)
  pct          = 0.18 (SIZE_PCT) + 0.03 (BONUS si z>4)
  z_weight     = clamp(z/4, 0.5, 2.0)
  haircut      = LIQUIDITY_HAIRCUT[strat] (0.8 pour S8 seulement)
  signal_mult  = SIGNAL_MULT[strat]
  
  Floor: max(size, $10)
```

### SIGNAL_MULT par strat

```
S1   = 1.125
S5   = 3.25   (bumped de 2.50 en v11.9.2)
S8   = 1.25
S9   = 2.00
S10  = 2.00
```

### Sizes typiques à $500 capital, btc_z=+1.4 (bull marqué)

```
Strat   Baseline   × Modulator   Size finale
S1      $190       × 1.70        $323  (boosté)
S5 LONG $268       × 1.00        $268
S5 SHORT $268      × 0.30        $80   (réduit, floor)
S8      $183       × 0.30        $55
S9      $420       × 0.30        $126
S10     $165       × 1.00        $165
```

### Floor de sécurité

Si `size × modulator < $10`, le trade est skipé avec event `SKIP {reason: "modulator_floor"}`. Évite les orders sous le minimum exchange.

### Compounding

`capital` dans la formule inclut `_total_pnl`. Donc :
- Bot gagne → positions plus grosses
- Bot perd → positions plus petites
- Effet boule de neige

---

## 5. Le modulator macro adaptatif (v11.10 → v12.2)

### Le problème qu'il résout

Le bot trade 5 stratégies très différentes. Certaines marchent en bull, d'autres en bear :
```
S1     : LONG alts. Bull = profite, Bear = perte
S8/S9  : SHORT fade. Bull = perte (momentum continue), Bear = profite
S5 SHORT : SHORT outperformers. Bull = perte (momentum continue)
```

Avant v11.10.0, toutes les stratégies tournaient à pleine taille en permanence → exposition catastrophique en mauvais régime.

### La formule

```
multiplier = 1 + α × btc_z
size_finale = strat_size × clamp(multiplier, 0.30, 2.50)

où:
  btc_z = z-score rolling 6 mois du return BTC sur 30 jours
  α     = coefficient par stratégie (par direction depuis v12.2.0)
  clamp = limite entre 0.30 (floor) et 2.50 (ceiling)
```

### Le z-score BTC en pratique

```
btc_z = +1.5  → BTC nettement plus haussier que sa moyenne 6m  (bull marqué)
btc_z = +0.5  → BTC modérément bullish
btc_z =  0.0  → BTC dans sa moyenne (neutre)
btc_z = -0.5  → BTC modérément bearish
btc_z = -1.5  → BTC nettement plus baissier que sa moyenne 6m  (bear marqué)
```

Mise à jour à chaque scan via `features.compute_btc_z()`. Lookback 30 jours, rolling z-window 180 jours.

### Coefficients α actuels (v12.2.0)

```python
ADAPTIVE_ALPHA = {
    "S1": +0.5,   # bull = amplifié (momentum continue)
    "S8": -0.5,   # bear = amplifié (capitulation marche en bear)
    "S9": -0.5,   # bear = amplifié (mean-rev marche en bear)
}
ADAPTIVE_ALPHA_DIR = {
    ("S5", -1): -0.5,  # S5 SHORT seulement: bear = amplifié
}
```

**S5 LONG et S10 deliberately exclus** : sliding walk-forward OOS a montré qu'ils sont régime-instables (leur α flippe de signe d'un régime à l'autre).

### Exemple : SEI catastrophe (2026-05-08) avant v12.2.0

```
Setup: S5 SHORT sur SEI avec divergence -1600 bps
Date:   2026-05-08, btc_z = +1.4 (bull marqué)
Size baseline (sans modulator): $226
Résultat: catastrophe stop -1250 bps → perte -$28.64

Avec v12.2.0:
  multiplier = 1 + (-0.5)(1.4) = 0.30 (clipped au floor)
  Size finale: $226 × 0.30 = $68
  Perte: $68 × -1257/10000 = -$8.55 (au lieu de -$28.64)
  
  Économie: +$20 sur ce seul trade
```

### Validation walk-forward

Le modulator a été validé par **5 batteries de tests indépendantes** :
- `backtest_adaptive_macro.py` (33 configs, batterie de base)
- `backtest_adaptive_macro2.py` (62 configs, fine-grain α sweep)
- `backtest_adaptive_robustness.py` (in-sample/out-of-sample split, lookback sensitivity 15-90j, null shuffle 13× signal/noise, rolling z-score sans look-ahead)
- `backtest_adaptive_walkforward.py` (sliding 18m train / 6m test × 4 splits)
- `backtest_adaptive_recent.py` (focus 90j récent)

**Tous tests passés 4/4 strict** pour S1/S8/S9. **S5/S10** rejetés sur le sliding car régime-instables, **sauf S5 SHORT** qui est régime-stable et a été ajouté en v12.2.0.

### Cold-start

Au boot, le bot a besoin de **210 jours de candles BTC** pour calculer btc_z (30j lookback + 180j window). v11.10.2 a corrigé un bug où le bot ne chargeait que 45 jours → modulator ne s'activait jamais. Maintenant chargement 250 jours.

---

## 6. Les exits — comment le bot ferme une position

10 mécanismes différents, par ordre de fréquence en live :

### 1. Catastrophe stop loss (-1250 bps)

Le filet de sécurité ultime. Si une position perd plus de 12.5% de mouvement de prix vs entry, exit immédiat.

```
Effective stop:
  S1/S5/S9/S10:  -1250 bps
  S8:             -750 bps (S8 entré sur capitulation, plus serré)
  S9 adaptive:    max(-1250, -500 - abs(ret_24h)/8) → 800-1250 selon force du move
```

### 2. Timeout naturel

Chaque trade a un hold time fixe. À l'épuisement → close au prix courant.

```
S1:  72h
S5:  48h
S8:  60h
S9:  48h
S10: 24h
```

### 3. Dead-timeout (v11.7.2, v11.7.16, v12.5.0)

À **T-12h avant timeout** (donc à hour 36 pour S5), le bot check si le trade est mort :
```
Conditions (toutes doivent être vraies):
  - hours_held ≥ hold_target - DEAD_TIMEOUT_LEAD_HOURS (12h)
  - MFE ≤ DEAD_TIMEOUT_MFE_CAP_BPS (150) — jamais en profit +1.5%
  - MAE ≤ DEAD_TIMEOUT_MAE_FLOOR_BPS (-500 depuis v12.5.0) — a touché -5%
  - current ≤ MAE + DEAD_TIMEOUT_SLACK_BPS (300) — toujours collé au pire
  
Si toutes vraies → exit immédiat avec reason="dead_timeout"
```

**Historique du seuil MAE_FLOOR** :
```
v11.7.2:  -1000 bps (initial)
v11.7.16: -800 bps (tightening)
v12.5.0:  -500 bps (tightening 4/4 walk-forward strict)
```

### 4. S10 trailing stop natif (v11.4.0)

Si MFE > S10_TRAILING_TRIGGER (600 bps), exit quand current drops below `MFE - S10_TRAILING_OFFSET (150)`. Verrouille les gains S10.

### 5. S9 early exit (config existant)

Si unrealized < -500 bps après 8h de hold → exit immédiat (S9_EARLY_EXIT_BPS / S9_EARLY_EXIT_HOURS). S9 winners reverten vite, losers persistent.

### 6. Runner extension (v11.7.32)

À 48h timeout S9, si MFE ≥ 1200 bps et current ≥ 30% du MFE → push target_exit de 12h. Capture la continuation des gros winners S9. Fire 1-2× par mois.

### 7. Close manuel via API

Bouton "Close" sur le dashboard → `/api/close/{symbol}` → market close immédiat. Reason=`manual_close`.

### 8. Stop manuel par position (v12.5.10)

Le user fixe un seuil $ via le bouton 🎯 du dashboard → `POST /api/manual_stop/{symbol}` (`{"stop_usdt": X}`). Le bot ferme la position dès que le P&L net (après `COST_BPS`) descend à ou sous ce seuil. Reason=`manual_stop_set`.

Vérifié dans `check_exits` juste après la catastrophe et avant les exits stratégie-spécifiques. L'API rejette une valeur strictement entre la catastrophe et le P&L courant (rejet "redundant" ou "self-triggering"). Persisté dans `state.json` → survit aux restarts. Stratégie-agnostique : override utilisateur, n'impacte aucun backtest.

Clear : `POST /api/manual_stop/{symbol}` avec `{"clear": true}`.

### 9. S8 dead-in-water exit (v12.6.0, mid-trade signature)

Sur les positions S8 **LONG**, à T+8h après l'entrée, si `pos.mfe_bps ≤ 50` (la position n'a jamais respiré au-dessus de +0.5% de profit), exit immédiat. Reason = `s8_dead_in_water`.

**Mécanique** : un vrai bottom de capitulation génère un MFE immédiat (chasse aux liquidités + short cover). Pas de rebond à T+8h = la thèse de capitulation est invalidée — la pression vendeuse absorbe chaque tentative d'achat. Inutile d'attendre 52h supplémentaires de hold pour le résultat inévitable.

**Validation** : discovery via mid-trade profiling EDA (`backtests/mid_trade_profiling_eda.md` — null-shuffle z=−6.41, savings +192 bps/cut in-sample sur 28m). Walk-forward (`backtests/s8_dead_in_water_walkforward.md`) :
- 28m : +207 872pp ΔPnL, DD inchangé, 11 cuts (9 genuine + 2 stragglers, net +3 209 bps)
- 12m : +1 723pp ΔPnL, DD inchangé, 6 cuts (0 stragglers)
- 6m : +138pp ΔPnL, **DD amélioré +8.39pp** (−39.10 → −30.71), 3 cuts (0 stragglers)
- 3m : 0pp (null intersection — 5 S8 LONG dans la fenêtre, aucun n'a qualifié)

DD jamais dégradée, améliorée sur la fenêtre où elle pouvait l'être. Stragglers anticipés par le user (trades retardataires qui font +20% après végétation) : zéro sur 12m/6m/3m, 2 sur 28m noyés dans les 9 cuts genuine.

**Coexistence avec S8 in-life trail (#9)** : les deux blocks sont mécaniquement disjoints. Dead-in-water cible MFE ≤ 50 bps (queue gauche de la distribution = trades amorphes). In-life trail cible MFE ≥ 300 (neutral) ou ≥ 1500 (bear/bull) = queue droite = gros gagnants qui retracent. Aucun chevauchement possible.

**Idempotence** : `pos.mfe_bps` est monotone non-décroissant. Une fois MFE > 50 bps, la condition `mfe_bps ≤ 50` est définitivement fausse et la règle ne fire plus jamais pour cette position. Pas besoin de flag de state.

**Kill-switch** : `S8_DEAD_MFE_MAX_BPS = -99999` dans `config.py` → la rule ne fire jamais.

### 10. S8 in-life MFE trail (v12.5.30, régime-conditionné)

Trail spécifique à S8, conditionné par le **régime BTC** (`btc_z`). Pour une position S8 ouverte :

```
bucket = "bear"     si btc_z < −S8_INLIFE_Z_THRESHOLD (= 0.5)
       = "neutral"  si |btc_z| ≤ 0.5
       = "bull"     si btc_z > +0.5

(activation, offset) = S8_INLIFE_PARAMS[bucket]
  bear    → (1500, 100)   # MFE ≥ +15%, exit à MFE−1%
  neutral → ( 300, 300)   # MFE ≥ +3%,  exit à MFE−3%
  bull    → (1500, 100)

Si pos.mfe_bps ≥ activation ET unrealized ≤ pos.mfe_bps − offset
  → exit avec reason="s8_inlife"
```

Mécaniquement consistant avec le modulator (S8 α=−0.5 = bear-favored) : en régime bear, S8 a le plus de upside slippage à perdre, on serre. En neutral, on trail plus tôt mais plus large. En bull, S8 fire peu et le trail est essentiellement inactif (contribution +34pp avg en backtest vs +94 169pp pour bear).

**Validation R&D** : walk-forward strict 4/4 (28m / 12m / 6m / 3m), ΔPnL avg +111 209pp, null-shuffle z=+10.52 (12/13 randomisations du `btc_z` détruisent l'edge), cross-validation par 2 mécaniques indépendantes (percentile empirique + ML logit/GBM rediscovering MFE-trail). Source : `backtests/inlife_exit_results.md`.

**S5** non résolu — testé par les 3 familles, aucune n'a passé strict 4/4 (S5 winners bimodaux). Reste manuel.

**Kill-switch** : `S8_INLIFE_PARAMS = {}` dans `config.py` court-circuite tout (no-op gracieux).

### Vue d'ensemble

```
Position ouverte
    │
    ├─ Check toutes les minutes:
    │   ├─ Stop catastrophe (-1250 ou -750 ou adaptatif S9) ?
    │   ├─ Manual stop $ fixé par user (v12.5.10) ?
    │   ├─ S10 trailing actif ?
    │   ├─ S9 early exit conditions ?
    │   ├─ S8 dead-in-water (v12.6.0) — à T+8h, mfe ≤ 50 ? → cut
    │   ├─ S8 in-life MFE trail (v12.5.30, régime-conditionné) ?
    │   └─ Update MAE/MFE
    │
    ├─ À T-12h avant timeout:
    │   └─ Dead-timeout conditions ?
    │
    ├─ À timeout (T):
    │   └─ Runner extension condition (S9 only) ?
    │       ├─ Oui → extend de 12h
    │       └─ Non → timeout naturel
    │
    └─ User clique "Close" ou "🎯":
        ├─ Close → manual_close immédiat
        └─ 🎯 → manual_stop_set (seuil $ persisté)
```

---

## 7. Monitoring & alertes

### Daily supervisor (v11.3.5)

`supervisor.py`, cron quotidien 8h UTC. Appel à l'API Anthropic (claude-haiku-4-5 par défaut). Analyse l'état des bots + compare au backtest. Send Telegram en français avec :
- Bilan P&L (réalisé, vs backtest)
- Anomalies détectées
- Suggestions d'action (urgence: now/this_week/later)
- Régime macro (btc_z, multipliers du modulator)

**Coût ~$0.50/mois** (cache hits sur 10k tokens de contexte statique).

### Weekly drift monitor (v12.0.0 → v12.1.0)

`analysis/strategy_review.py`, cron hebdomadaire **lundi 8h UTC**. Pure stdlib (no LLM call). Analyse `reversal_ticks.db` et flag :
1. **STRAT_DRIFT** — WR d'une strat baisse >12pp vs lifetime
2. **TOKEN_TOXIC** — (token, dir, strat) avec sum < -$8 sur 90j
3. **TOKEN_REVIVAL** — pair précédemment toxic qui se redresse
4. **LIVE_VS_BT** — gap live/backtest > 25pp
5. **REGIME_SHIFT** — btc_z + multipliers actuels

Output : Telegram + event `STRATEGY_REVIEW` dans events DB. **Observation-only**, aucune action auto.

### Win probability estimator (v12.3.x → v12.5.6)

Pour chaque position ouverte affichée sur le dashboard, le bot calcule une probabilité que le trade finisse gagnant, basée sur des trades historiques similaires.

```
😀 65%+   très solide, laisse tourner
🙂 55-65% bon
😐 45-55% neutre
😕 35-45% préoccupant
😟 25-35% à surveiller
😱 < 25%  alarme, considère fermer
⌛  fresh, pas assez de hold pour analyser MAE/MFE
—   position currently in profit → indicator caché (v12.5.6) OU pas assez de données
```

**Calcul (v12.5.5)** :
```
1. Tier 1 : match (strat, token, direction) sur historique 6 mois.
            Min 5 trades (relevé depuis 3 en v12.5.5 — n=3 produit du 0%/100% statistiquement absurde).
2. Tier 2 si insuffisant : match (strat, direction). Min 8 trades.
3. Base WR = % wins dans l'échantillon.
4. Adjustments (si trade mature ≥ 2h) :
   - MAE conditionnel (< -200 bps) : "des trades qui ont aussi hit cette MAE, combien ont gagné ?"
     → v12.5.5 : appliqué UNIQUEMENT si position currently underwater (ur_bps ≤ 0).
     Si la position a déjà récupéré, ce penalty est ignoré (over-pessimise un trade revenu).
   - MFE pulse (≥ 200 bps) : ×1.10 (mean-reversion plausible)
   - Pas de pulse + late in hold : ×0.90
```

**Telegram alarme WR (v12.4.0 + v12.5.4)** : si une position passe en 😱 (< 25%) pour la première fois après maturité **ET qu'elle est actuellement en perte**, message Telegram envoyé. Un seul alert par position (anti-spam). Le gate "position currently profitable → silence" évite les fausses alarmes du genre "APT à +$3.68 considère fermer".

**Dashboard (v12.5.6)** : le smiley/% est caché sur les positions en profit (montre "—" dim) — la colonne devient un indicateur "should I worry?" qui ne parle que quand il y a lieu.

### Basket correlation observability (post-12.5.1, observation-only)

**La question qu'on essaie de répondre** : "j'ai 4 positions ouvertes, est-ce que ce sont 4 paris vraiment différents, ou est-ce qu'elles bougent toutes ensemble (panier concentré) ?"

**3 métriques calculées à chaque scan** (si ≥ 2 positions, sur returns 30j) :
- **`mean_corr_to_btc`** ∈ [-1, +1] : exposition signée du panier à BTC. `+0.7` = panier essentiellement long BTC. `-0.7` = essentiellement short BTC. `~0` = bien hedgé. Calculé comme `mean(direction_i × corr(alt_i, BTC))` sur les 30 jours rolling.
- **`max_pairwise_corr`** : la pire paire "same-trade" du panier. `+0.9` = deux positions font effectivement le même pari. Négatif = la meilleure paire hedgée.
- **`effective_n`** ∈ [1, n_positions] : nombre équivalent de positions vraiment indépendantes. Si `n_positions=4` et `effective_n=4.0` → bien diversifié. Si `effective_n=1.5` → tu fais en réalité ~1.5 pari, pas 4. Formule : `n² / sum(matrice corrélation sign-adjusted)`.

**Pourquoi c'est observation-only pour le moment** : les caps actuels (`MAX_SAME_DIRECTION=4`, `MAX_PER_SECTOR=2`) ne regardent que direction et secteur — ils sont aveugles au fait que 4 LONGs de 4 secteurs différents peuvent quand même tous être corrélés à BTC. La métrique répond au "vrai" risque. Mais pour ajouter un gate (skip si effective_n trop bas), il faut d'abord vérifier que les drawdowns observés en live corrèlent avec une concentration élevée — sinon c'est un nouveau filtre qui se ferait éjecter en walk-forward, comme tous les autres essais.

**Stockage** :
- Snapshot par scan (~1/h) dans la table SQLite `basket_snapshots` (ts, n_positions, mean_corr_to_btc, max_pairwise_corr, effective_n)
- Snapshot du panier au moment de chaque entrée logué dans l'event `OPEN` (champs `basket_mean_corr_btc, basket_max_pairwise, basket_effective_n, basket_n_positions`)
- Exposé dans `/api/state.basket_metrics` pour le dashboard

**Widget dashboard** (market-bar à côté de l'OI) :
```
Basket: +0.62 eff 2.4/4
```
- 1er nombre = `mean_corr_to_btc` (signé). Couleur : vert |x|<0.3, jaune 0.3-0.6, rouge >0.6.
- 2e nombre = `effective_n / n_positions`. Couleur : vert ratio≥0.7, jaune 0.5-0.7, rouge <0.5.
- Tooltip = les 3 métriques détaillées.

**Prochaine étape (1-2 mois post-instrumentation)** : query d'analyse pour joindre `OPEN.basket_*` (concentration à l'entrée) avec `trades.pnl_usdt` (outcome). Si on observe que les trades ouverts dans un panier "concentré" (effective_n − 1 < 2, par exemple) sous-performent, on a une motivation chiffrée pour un gate testé walk-forward 4/4 strict. Sinon → on garde la métrique comme widget observability et on ferme le ticket.

**Kill-switch (gate futur, pas actif)** : aucune action de trading n'utilise ces métriques aujourd'hui.

### État des features observation-only (tracking)

Pour éviter que ces métriques deviennent silencieusement de la télémétrie permanente, voici l'état au moment du code review (2026-05-11) :

| Feature | Où loggée | Depuis | Sample size live | Seuil analyse | Statut |
|---------|-----------|--------|------------------|---------------|--------|
| `entry_oi_delta` | trades + OPEN | v8.x | 87 trades | 50+ | ✅ prête, jamais analysée |
| `entry_crowding` | trades + OPEN | v8.x | 87 trades | 50+ | ✅ prête, jamais analysée |
| `entry_confluence` | trades + OPEN | v8.x | 87 trades | 50+ | ✅ prête, jamais analysée |
| `entry_session` | trades + OPEN | v8.x | 87 trades | 50+ | ✅ prête, jamais analysée |
| `S9F_OBS` events (±3% / 2h) | events table | v11.x | n/a | 6+ mois | ⏳ attente |
| `ETH_OBS` events | events table | v11.x | n/a | observation | ⏳ attente |
| `basket_metrics` (mean_corr, effective_n, max_pairwise) | basket_snapshots + OPEN | 2026-05-11 | début | 1-2 mois | ⏳ attente forward |
| `entry_side_imbalance` / `book_skew` / `book_spread_bps` | OPEN | 2026-05-11 | début | 3-6 mois | ⏳ attente forward |

**Action recommandée** : un script d'analyse rétrospective tous les 3 mois sur les features `entry_*` (qui ont déjà 87+ trades). Si une corrèle significativement avec pnl_usdt, candidate pour un gate validé walk-forward. Si après 200+ trades aucune ne sort du bruit → classer comme "instrumentation suffisante, pas de gate".

### Dashboard live (port 8098/bot/)

```
Cards principales:
  - Equity (HL real ou bot-implied)
  - Total P&L
  - Open positions (avec WR estimée + smiley)
  - Sector heatmap
  - Active signals (preview)
  - Recent trades + closed P&L
  - Backtest comparison
```

### Telegram channels

```
Live (TG_BOT_TOKEN/TG_CHAT_ID):
  - Trade open/close
  - Daily supervisor report (8h UTC)
  - Weekly drift monitor (lundi 8h UTC)
  - WR alarm alerts
  - Reconcile mismatches
  - Login alerts
  - Restart/error alerts

Junior (JUNIOR_TG_BOT_TOKEN/JUNIOR_TG_CHAT_ID):
  - Filtered: trade,daily,system uniquement (pas reconcile/login spam)
```

### Watchdog cron (toutes les 5 min)

`*/5 * * * * pgrep -cf analysis.reversal -ge 3 || ./start_bots.sh`

Si un bot meurt, redémarrage automatique. Évite que le bot soit offline trop longtemps après crash silencieux.

---

## 8. Sécurité & kill-switches

### Hierarchy des protections

```
Niveau 1 — Hard limits (exchange-side):
  - Cross margin 2x (Hyperliquid)
  - Spot/perps separation

Niveau 2 — Bot-side hard stops:
  - Catastrophe stop -1250 bps par position
  - max 6 positions concurrentes
  - max 4 même direction
  - max 2 par secteur
  - TRADE_BLACKLIST = {SUI, IMX, LINK}

Niveau 3 — Bot-side risk modulators:
  - Modulator macro (réduit en mauvais régime)
  - Dispersion gate (skip S5/S9 si dispersion p98+)
  - OI gate LONG (skip si OI -10%/24h)
  - Modulator floor $10 minimum
  - Slot reservation (macro vs token)

Niveau 4 — Manual intervention:
  - /api/close/{symbol} pour fermer immédiat
  - /api/manual_stop/{symbol} pour fixer un seuil $ (v12.5.10)
  - /api/pause pour stopper nouveaux trades
  - /api/reset (paper seulement, drop tout)
```

**Hardening pass (v12.5.29 → v12.5.31)** : validation `avgPx > 0` sur les fills HL (sinon `entry/exit_price=0` → P&L corrompu), validation NaN/inf sur les inputs API (`stop_usdt`, `amount`), boot reconcile à 2 attempts (1 cache lag ne drop plus un ghost), session revocation epoch côté serveur (`/logout` invalide les cookies stolen), rate-limit per-IP sur endpoints mutating (30/min), timeout cap sur tous les appels SDK HL (`_sdk_call` 10-20s + saturation warning à `max_workers−1`).

### Kill-switches disponibles

Pour neutraliser une feature sans redéployer entièrement, éditer `config.py` :

```python
# Désactiver le modulator entièrement:
ADAPTIVE_ALPHA = {}
ADAPTIVE_ALPHA_DIR = {}

# Désactiver le dispersion gate:
DISP_GATE_BPS = 99999

# Désactiver l'adaptive stop S9 (revenir à fixe):
S9_ADAPTIVE_STOP = False

# Désactiver dead_timeout (laisser tous les trades aller au timeout):
DEAD_TIMEOUT_MFE_CAP_BPS = -99999

# Désactiver le runner extension S9:
RUNNER_EXT_STRATEGIES = set()

# Restaurer ancien DT MAE floor:
DEAD_TIMEOUT_MAE_FLOOR_BPS = -800  # ou -1000 pour pré-v11.7.16

# Reactiver les S10 LONGs (déconseillé):
S10_ALLOW_LONGS = True

# Désactiver supervisor:
SUPERVISOR_ENABLED = 0 dans .env

# Désactiver drift monitor:
crontab -l | grep -v strategy_review | crontab -
```

### Kill-switches automatiques supprimés (v12.5.2)

Anciens : `LOSS_STREAK_THRESHOLD/MULTIPLIER/COOLDOWN`, `TOTAL_LOSS_CAP`. Backtest historique a montré que tout cap absolu ou pénalité loss-streak détruit le compounding (perte de −65 à −99% du P&L cumulé sur 28m). Les constantes étaient présentes mais désactivées (valeurs sentinelles infinies). Retirées du code dans le sweep v12.5.2 pour que la config arrête de mentir. L'observation `_consecutive_losses` reste calculée pour le dashboard, juste sans action automatique associée.

---

## 9. Historique de la recherche

### Méthode de validation

**Walk-forward strict 4/4** : un changement doit gagner du P&L sur **les 4 fenêtres** (28m / 12m / 6m / 3m) **simultanément** ET ne pas dégrader le DD de plus de +0.5pp en moyenne.

Logique : si une optimisation perd sur même UNE fenêtre, elle est trop régime-spécifique → overfit.

### Chronologie compacte

```
v8-v10        : prototype, 5 signaux découverts via genetic search
v11.3.0       : sizing 12% → 18%, fix double-leverage bug (P&L recalculé)
v11.3.4       : S10 SHORT-only + token whitelist
v11.4.0       : S10 trailing stop
v11.4.9       : OI gate LONG
v11.4.10      : TRADE_BLACKLIST (SUI, IMX, LINK)
v11.7.2       : Dead-timeout early exit
v11.7.5       : Per-trade funding tracking (live exact via HL history)
v11.7.16      : Dead-timeout MAE floor -1000 → -800
v11.7.20      : DCA rebase peak_balance
v11.7.28      : Dispersion gate S5/S9
v11.7.32      : Runner extension S9
v11.9.0       : Universe expanded TON
v11.9.1       : Equity formula fix (no double-count cross-margin)
v11.9.2       : S5 SIGNAL_MULT 2.50 → 3.25
v11.10.0      : Adaptive macro modulator S1/S8/S9
v11.10.2      : Fix BTC candle history pour activer modulator
v12.0.0       : Weekly drift monitor
v12.1.0       : S5 SHORT static blacklist (5 tokens)
v12.2.0       : Replace static blacklist par adaptive S5 SHORT modulator
v12.3.0       : Win prob estimator + dashboard smiley
v12.3.2       : Maturity gate (mute MAE noise on fresh)
v12.4.0       : Telegram WR alarm
v12.5.0       : Dead-timeout MAE floor -800 → -500
v12.5.1       : Fix /api/state crash (Position missing pnl_usdt)
v12.5.2       : Refactor sweep — kill dead code (CSV migration, dead toggles, _db_lock module)
v12.5.3       : Refactor — analytics.py extracted, signal_skip_reason shared, scan slimmed, funding fetch timeboxed
v12.5.4       : WR alarme gate on currently-profitable positions (faux positifs DOGE/APT)
v12.5.5       : WR estimator fix — skip MAE penalty when recovered, tier-1 min raised 3 → 5
v12.5.6       : Dashboard — Path column = courbe de prix, smiley caché si en profit
v12.5.10      : Manual per-position stop (bouton 🎯, persisté state.json)
v12.5.27-28   : Dashboard "Si je ferme tout" (liquidation value) + tech-EN labels
v12.5.29      : Hardening pass — fills validation, NaN guards, 2-attempt boot reconcile, web rate-limit, SDK timeout cap
v12.5.30      : S8 in-life MFE trail (régime-conditionné) — null-shuffle z=+10.52, walk-forward 4/4
v12.5.31      : Boot reconcile timeout wrap + SDK saturation warning + mut-log prune
v12.5.32-35   : Dashboard polish (couleurs sobres, smiley mobile, sparkline plein, price-chart Y margins + mobile clipping fix)
v12.5.36      : Open Positions rendues en cards sur tous les écrans (grid responsive)
```

### Backtests cette semaine (mai 2026) — résumé

| Fichier | Verdict | Note |
|---|---|---|
| backtest_partial_fills.py | ✓ S5×1.30 (=3.25 final) | +2681pp 28m, déployé v11.9.2 |
| backtest_adaptive_macro.py | ✓ S1+0.5 S8/S9-0.5 | +19340pp sum 4/4, déployé v11.10.0 |
| backtest_adaptive_macro2.py | ✓ fine-grain α confirmé | Validation supplémentaire |
| backtest_adaptive_robustness.py | ✓ IS/OOS robust, signal 13× noise | Multiple validation passes |
| backtest_adaptive_walkforward.py | ✓ S1/S8/S9 OOS stable, S5/S10 instable | Sliding 18m/6m × 4 splits |
| backtest_adaptive_recent.py | ✓ +12pp sur live 43j attendu | Focus régime récent |
| backtest_quality_filters.py | ✗ confluence + S9 stop tighter | Rejet 4/4 |
| backtest_s5_reinforce.py | ✗ aucune amélioration walk-forward | 87 configs |
| backtest_s5_creative.py | ✗ blacklist statique effet slot | 14 configs |
| backtest_s5_downsize.py | ✓ token downsize OK | Supérieur par adaptive après |
| backtest_universe_expand.py | ✓ TON ajouté | v11.9.0 |
| backtest_partial_fills.py | ✓ S5×1.30 | Validé |
| backtest_wr_autoclose.py | ✗ auto-close WR rejeté / ✓ DT -800→-500 | v12.5.0 bonus |
| backtest_s5_directional_flip.py | ✗ flip directionnel rejeté | -80k+ sur 28m |
| backtest_modulator_2d.py | ✗ 0/14 configs strict, retest 2026-08-11 | +59k pp en 28m mais 3m casse partout |
| backtest_l2_imbalance.py | ✗ rétro fort, gate destructeur live | OBI observation-only |
| backtest_we_oi_gates.py | ✗ WE skip S5 LONG à 3/4 (ΔDD +0.39) | Très proche, retest 2026-08-11 |
| backtest_crowding_confluence_gates.py | ✗ 0/6 configs strict | Crowding/confluence enterrés |
| backtest_obs_features (analytique rétro) | 2 signaux fort + 2 weak | Walk-forward a tué les 4 in fine |

### Pistes rejetées historiquement (ne pas re-tester sans raison)

```
- S5 LONG only (skip all S5 SHORT) : slot effect, perd massivement
- Tighter S5 stop (< -1250) : winners ont besoin de drawdown profond
- Confluence filter sur S5/S9 : retire les wins en même temps que losses
- Trailing stop sur S5/S9 : sample non walk-forward 4/4
- Auto-close sur WR alarm : retire 25% de wins en plus des losses
- Symmetric flip S5 (bull→LONG, bear→SHORT) : -333k pp sur 28m
- VZ blow-off filter : DD se dégrade
- BTC counter-trend gate sur S5 : aucune config 4/4
```

### Plateau d'optimisation

Après ~30 backtests divers cette semaine, **seuls les changements qui passent le 4/4 strict sont déployés**. Le bot a atteint un **plateau d'optimisation** sur l'architecture actuelle :
- Toutes les pistes de filtrage classiques rejetées (slot effect)
- Les améliorations restantes viennent de **régime-awareness** (adaptive modulator)
- Le drift monitor surveille en continu sans agir

Aller plus loin demanderait soit :
- Une nouvelle stratégie (S11+) — recherche genetic
- Un changement d'instrument (Bybit, etc.) — différent marché
- Un changement de timeframe (1h au lieu de 4h) — déjà testé, rejeté

---

## 10. Pourquoi on n'ajoute plus de filtres

### Le piège du slot

Le bot a **6 slots de positions maximum**. Chaque filter qui SKIP un signal :
1. Libère un slot
2. Le slot est pris par un AUTRE signal (parfois pire)
3. Net effect : pas forcément un gain

C'est pourquoi tous les tests "skip S5 SHORT en bull" ont échoué — les slots libérés étaient pris par des trades qui perdaient autant ou plus.

### La solution : downsize plutôt que skip

Au lieu de skip, on **réduit la taille** :
- Slot consommé (pas réalloué)
- Exposition limitée
- Modulator macro = cette logique en système

C'est le breakthrough de v12.2.0 sur le S5 SHORT.

### Compounding sur 28 mois

Le baseline backtest sur 28 mois fait **+357,179%** en P&L. C'est massif. Toute modification qui retire 5-10% de trades détruit le compounding boule de neige → −100k+ pp 28m.

C'est pourquoi le strict 4/4 est si difficile à passer.

### Le paradoxe overfit

Plus on optimise un filter sur le passé, plus on risque de :
1. Capturer du bruit (random)
2. Perdre en généralisation future

Le strict 4/4 + sliding walk-forward OOS sont les défenses. Mais ils sont **conservateurs** — ils rejettent même de bonnes idées au profit de la stabilité.

---

## 11. État actuel — paramètres en vigueur

### Version

**v12.7.0** — déployée sur paper / live / junior (admin reste sur ancienne version, sans impact).

### Capitaux

```
Paper  : $1000 simulé (raz fait 2026-05-10)
Live   : $500 réel
Junior : $300 réel
```

### Règle restart (CLAUDE.md 2026-05-11)

Une autorisation générique "restart les bots" **ne couvre QUE paper (:8097) et live (:8098)**. Junior (:8099) nécessite un mot explicite ("restart junior", "les 3 bots", etc.). Le `start_bots.sh` ne fait pas la différence — donc on ne `fuser -k` que sur 8097 + 8098 quand junior n'est pas autorisé ; le `start_bots.sh` tentera de relancer junior mais fail-bindra silencieusement sur 8099.

### Paramètres clés (config.py)

```python
# Sizing
SIZE_PCT = 0.18 + 0.03 (z>4 bonus)
LEVERAGE = 2.0
SIGNAL_MULT = {S1: 1.125, S5: 3.25, S8: 1.25, S9: 2.00, S10: 2.00}
LIQUIDITY_HAIRCUT = {S8: 0.8}

# Adaptive macro modulator (v11.10.0 + v12.2.0)
ADAPTIVE_ALPHA = {S1: +0.5, S8: -0.5, S9: -0.5}
ADAPTIVE_ALPHA_DIR = {("S5", -1): -0.5}
MACRO_LOOKBACK_DAYS = 30
MACRO_Z_WINDOW_DAYS = 180
MACRO_Z_CLIP = 2.5
MACRO_MULT_MIN = 0.3
MACRO_MULT_MAX = 2.5

# Stops
STOP_LOSS_BPS = -1250    # S1/S5/S10
STOP_LOSS_S8 = -750
S9_ADAPTIVE_STOP = True   # max(-1250, -500 - abs(ret_24h)/8)
S9_EARLY_EXIT_BPS = -500, S9_EARLY_EXIT_HOURS = 8

# Dead-timeout (v12.5.0)
DEAD_TIMEOUT_LEAD_HOURS = 12.0
DEAD_TIMEOUT_MFE_CAP_BPS = 150.0
DEAD_TIMEOUT_MAE_FLOOR_BPS = -500.0
DEAD_TIMEOUT_SLACK_BPS = 300.0

# S8 in-life trail régime-conditionné (v12.5.30)
S8_INLIFE_Z_THRESHOLD = 0.5
S8_INLIFE_PARAMS = {
    "bear":    (1500, 100),   # MFE ≥ +15%, trail à MFE−1%
    "neutral": ( 300, 300),   # MFE ≥ +3%,  trail à MFE−3%
    "bull":    (1500, 100),
}

# S8 dead-in-water exit (v12.6.0)
S8_DEAD_T_H = 8.0             # checkpoint T+8h
S8_DEAD_MFE_MAX_BPS = 50.0    # si mfe ≤ 50 bps à T+8h → cut

# Hold times
HOLD_HOURS_DEFAULT = 72  # S1
HOLD_HOURS_S5 = 48
HOLD_HOURS_S8 = 60
HOLD_HOURS_S9 = 48
HOLD_HOURS_S10 = 24

# Portfolio limits
MAX_POSITIONS = 6
MAX_SAME_DIRECTION = 4
MAX_PER_SECTOR = 2
MAX_MACRO_SLOTS = 3
MAX_TOKEN_SLOTS = 4

# Filters
TRADE_BLACKLIST = {SUI, IMX, LINK}
DISP_GATE_BPS = 700
DISP_GATE_STRATEGIES = {S5, S9}
OI_LONG_GATE_BPS = 1000

# Costs
TAKER_FEE_BPS = 9.0 (round-trip)
SLIPPAGE_BPS = 0.0 (live: avgPx exact)
FUNDING_DRAG_BPS = 1.0 (estimate + real funding swap at close)

# Runner extension (v11.7.32)
RUNNER_EXT_STRATEGIES = {S9}
RUNNER_EXT_HOURS = 12
RUNNER_EXT_MIN_MFE_BPS = 1200
RUNNER_EXT_MIN_CUR_TO_MFE = 0.3

# Tokens (35 trading + 2 reference)
TRADE_SYMBOLS = [35 tokens: 29 base + v12.7.0 expansion BCH/DOT/ADA/XMR/ENA/UNI]
REFERENCE = [BTC, ETH]
S10_ALLOWED_TOKENS = {AAVE, APT, ARB, BLUR, COMP, CRV, INJ, MINA, OP, PYTH, SEI, SNX, WLD}
```

### Cron jobs

```
@reboot              start_bots.sh
0 8 * * *           supervisor.py (daily LLM analysis)
0 8 * * 1           strategy_review.py (weekly drift monitor)
30 23 * * *         memory sync
*/5 * * * *         watchdog (auto-restart si bot mort)
```

### Wallets HL

```
Live    : 0x6E2aE12f1F093CAA9710F15f933516B9b6fA2d5d (HL_PRIVATE_KEY)
Junior  : signer 0x4EAb0507...3F7e + master 0xb65d5e52...956Fe
Paper   : pas de wallet (simulation)
```

---

## 12. Comment lire le dashboard

### Header

```
Equity (HL)  $XXX  +$XX (+X%) on $cap         Total P&L  +$XX
                                              (sub: drawdown depuis peak)
```

### Card "Open positions"

Depuis v12.5.36, chaque position est rendue en **card**, sur tous les écrans, dans un **grid responsive** (1 colonne sur mobile, 2-3+ sur desktop selon largeur). Le tableau 13-colonnes a été retiré.

Anatomie d'une card :

```
┌──────────────────────────────────────────────────┐
│ SYM  LONG  S8                +$12.34 😀 +145 bps  │ ← header
│ ▓▓▓▓▓▓▓▓▓░░░░░░░░ 18h elapsed / 60h total       │ ← hold-progress
│ ┌─sparkline (prix)─┐ ┌─MAE/MFE─┐ ┌─🎯─┐         │
│ │      ╱╲          │ │  •━━●━━ │ │ ✕ │         │ ← m-mid
│ │ ╱╲╱     ╲        │ └─────────┘ └────┘         │
│ │ $0.4521          │                            │
│ └──────────────────┘                            │
│ pos $250  mgn $125  entry $0.448  held 18h  ... │ ← pills
└──────────────────────────────────────────────────┘
```

- **P&L** : ±$X.XX collé au **smiley** (😀🙂😐😕😟😱⌛, v12.5.32) + bps unrealized + badge 🎯 si manual stop actif. Smiley caché si la position est currently in profit.
- **hold-progress** : palette sobre slate-blue → ambre → rouge sourd (v12.5.32). Label "X elapsed / Y total" centré.
- **Sparkline (prix)** : courbe de prix sur les trajectory points. Ligne pointillée grise = entry, ligne pointillée colorée = current. v12.5.33 : viewBox + `preserveAspectRatio="none"` + `vector-effect="non-scaling-stroke"` pour remplir la zone sans déformer les traits.
- **MAE/MFE strip** : ●━━ avec bornes stop/MAE/current/MFE/trailing.
- **Boutons** : 🎯 (manual stop, v12.5.10) et ✕ (manual_close).
- **Pills** : pos / mgn / entry / held / rest / stop / mae / mfe — toute l'info de niveau 2.

### Card "Strats stats"

Tableau par stratégie : trades lifetime/recent, WR, avg bps, P&L lifetime/recent. Permet de voir si une strat dérive.

### Card "Active signals"

Preview des signaux en cours de détection sur tous les tokens, avec scores + raisons de skip si applicable.

### Card "Sector heatmap"

Mini-cards par secteur avec n positions, unrealized total, avg ret_24h. Visualise les concentrations.

### Card "Backtest comparison"

Compare la P&L live à ce qu'attendu par le backtest depuis le déploiement. Le ratio live/backtest et le gap sont mis en avant.

### Card "Recent trades + closed P&L"

Liste des 20 derniers trades fermés avec reason (`timeout`, `dead_timeout`, `catastrophe_stop`, `manual_close`, `manual_stop_set`, `runner_ext`, `s9_early_exit`, `trail_stop`, `s8_inlife`). Colonnes : Time / Sym / St / Side / **P&L** / Gross / Net / Hold / Exit (P&L promu juste après Side en v12.5.32). Fond vert/rouge subtil selon win/loss (v12.5.33).

---

## 13. Glossaire

| Terme | Définition |
|---|---|
| **bps** | basis points, 1bps = 0.01% (de mouvement de prix) |
| **MAE** | Max Adverse Excursion — pire P&L unrealized vu pendant le trade |
| **MFE** | Max Favorable Excursion — meilleur P&L unrealized vu pendant le trade |
| **btc_z** | z-score rolling 6m du return BTC 30j (régime macro) |
| **adaptive modulator** | mécanisme qui ajuste la taille selon btc_z par stratégie |
| **walk-forward 4/4 strict** | un changement doit gagner sur 4 fenêtres backtest (28m/12m/6m/3m) + DD ≤ +0.5pp avg |
| **dead-timeout** | exit anticipé pour trades "morts" (jamais profit, MAE profond, T-12h) |
| **trailing stop** | stop qui suit le MFE et verrouille les gains (S10 seulement) |
| **runner extension** | extension de hold pour S9 winners avec MFE fort |
| **OOS** | Out-Of-Sample — validation sur data non vue pendant l'optimisation |
| **dispersion gate** | skip S5/S9 si cross-sectional std des returns p98+ |
| **OI gate LONG** | skip LONG si OI tombé >10% en 24h (longs unwinding) |
| **catastrophe stop** | stop loss universel -1250 bps (ou -750 pour S8) |
| **manual stop** | seuil $ fixé manuellement par le user via le bouton 🎯 du dashboard (v12.5.10) |
| **s8_inlife** | trail spécifique à S8 conditionné par le régime BTC (v12.5.30) — exit quand MFE retrace de l'offset par bucket bear/neutral/bull |
| **s8_dead_in_water** | exit anticipé à T+8h sur S8 LONG si MFE n'a jamais dépassé +50 bps (v12.6.0) — la capitulation thesis est invalidée |
| **slot effect** | retirer un trade libère un slot pris par un autre signal — souvent fausse économie |
| **compounding** | capital = initial + P&L cumulé, donc positions scalent avec gains/pertes |
| **drift monitor** | script hebdo qui scanne pour dérives statistiques |
| **supervisor** | script quotidien qui appelle Anthropic API pour rapport |
| **WR estimator** | calcul WR pour position ouverte basé sur historique 6m + ajustements MAE/MFE/maturité |
| **regime-stable / regime-unstable** | strategy dont α reste positif/négatif consistent (stable) ou flip selon régime (instable) |
| **TRADE_BLACKLIST** | tokens exclus globalement (SUI/IMX/LINK) — losers structurels 28m |
| **ADAPTIVE_ALPHA** | dict coefficients α par strategy pour le modulator macro |
| **ADAPTIVE_ALPHA_DIR** | overrides directionnels (ex: ("S5", -1): -0.5) |
| **MACRO_MULT_MIN/MAX** | clip du multiplier du modulator [0.30, 2.50] |
| **Junior** | bot live secondaire sur master wallet séparé avec API agent signer |
| **DCA** | Dollar Cost Averaging — ajout de capital via /api/capital |

---

*Doc écrit le 2026-05-11, mis à jour le 2026-05-16 (v12.7.0). Mettre à jour à chaque commit majeur. Pour le détail technique destination Claude voir `CLAUDE.md`. Pour l'historique versions voir `CHANGELOG.md`. Pour les résultats backtests à jour voir `docs/backtests.md`.*
