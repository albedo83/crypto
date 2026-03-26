# Multi-Signal Bot v10.3.1

Bot de trading automatique sur Hyperliquid (DEX, accessible depuis la France). Paper trading.

---

## Philosophie

Le bot combine deux approches : 3 signaux contrarians qui achetent les crashs et shortent le calme (S2, S4, S8), et 2 signaux de continuation qui suivent le momentum BTC→alts (S1) et les breakouts sectoriels (S5). La volatilite est le carburant des signaux contrarians — c'est pourquoi le sizing ATR (reduire les positions quand le marche bouge) a ete teste et rejete : il coupe les signaux exactement quand ils doivent tirer.

Tout a ete teste systematiquement : 1500+ regles, algorithmes genetiques, programmation genetique, machine learning, Monte Carlo. 5 signaux survivent. Le reste est du bruit.

---

## Les 5 signaux

### S1 — BTC explose (+20% sur 30 jours)

| | |
|---|---|
| **Condition** | `btc_30d > 2000 bps` (+20%) |
| **Action** | LONG altcoins |
| **Logique** | Momentum retarde / continuation BTC→alts : quand BTC pump de +20% sur un mois, les alts suivent avec un retard de quelques jours. On achete ce retard. |
| **Hold** | 72h |
| **Stop** | -25% (leveraged) |
| **z-score** | 6.42 |
| **Mise** | $241 (z-weighted, la 2eme plus grosse) |
| **Frequence** | Rare — quelques fois par an |
| **Backtest** | +$1,480 sur 208 trades |

### S2 — Les alts crashent (-10% en 7 jours)

| | |
|---|---|
| **Condition** | `alt_index_7d < -1000 bps` (moyenne des 28 alts < -10%) |
| **Action** | LONG |
| **Logique** | Apres un crash generalisé, les alts rebondissent. On achete la panique collective. Fonctionne en bull ET en bear — le regime gating a ete teste et degrade le signal. |
| **Hold** | 72h |
| **Stop** | -25% (leveraged) |
| **z-score** | 4.00 |
| **Mise** | $150 |
| **Frequence** | Quelques fois par mois |
| **Backtest** | +$1,706 sur 552 trades |

### S4 — Calme plat + dollar fort

| | |
|---|---|
| **Conditions** | `vol_ratio < 1.0` (volatilite 7j < 30j, marche calme) AND `range_pct < 200 bps` (petite bougie) AND `DXY 7d > +100 bps` (dollar en hausse) |
| **Action** | SHORT |
| **Logique** | En crypto, quand c'est calme et que le dollar se renforce, les alts derivent vers le bas. Le filtre DXY est critique : sans lui, le signal shorte en bull market et perd. |
| **Hold** | 72h |
| **Stop** | -25% (leveraged) |
| **z-score** | 2.95 |
| **Mise** | $111 (la plus petite, z-score le plus faible) |
| **Frequence** | Variable — depend de la force du dollar |
| **DXY** | Source : Yahoo Finance, cache dans `output/pairs_data/macro_DXY.json`. Cache frais < 6h, fallback stale 6-48h (bandeau jaune "DXY_STALE"), desactive > 48h (bandeau rouge "DXY"). S4 reste actif tant que le cache a < 48h. |
| **Backtest** | +$2,609 sur 1,185 trades |
| **Seul signal SHORT** | 378 variantes SHORT testees, aucune autre ne depasse z > 2.0. Shorter les alts est structurellement difficile. |

### S5 — Un token casse de son secteur

| | |
|---|---|
| **Conditions** | Divergence > 1000 bps (+10%) par rapport a la moyenne du secteur AND `vol_z >= 1.0` (volume au-dessus de la normale) |
| **Action** | FOLLOW (LONG si le token monte, SHORT si il baisse) |
| **Logique** | Quand un token diverge de son secteur avec du volume, le mouvement continue. C'est un vrai signal, pas du bruit. Le "fade" (jouer contre le mouvement) a ete teste et ne marche pas. |
| **Hold** | 48h (plus court, les rotations de secteur sont rapides) |
| **Stop** | -25% (leveraged) |
| **z-score** | 3.67 |
| **Mise** | $138 |
| **Frequence** | 10-20 fois par mois |
| **Backtest** | +$2,022 sur 467 trades (FOLLOW), +$470 (FADE — rejete) |

**Secteurs** :

| Secteur | Tokens |
|---|---|
| L1 | SOL, AVAX, SUI, APT, NEAR, SEI |
| DeFi | AAVE, MKR, CRV, SNX, PENDLE, COMP, DYDX, LDO, GMX |
| Gaming | GALA, IMX, SAND |
| Infra | LINK, PYTH, STX, INJ, ARB, OP |
| Meme | DOGE, WLD, BLUR, MINA |

### S8 — Flush de liquidation

| | |
|---|---|
| **Conditions** | `drawdown < -4000 bps` (-40% du plus haut 30j) AND `vol_z > 1.0` (volume anormal) AND `ret_24h < -50 bps` (le prix saigne encore) AND `btc_7d < -300 bps` (BTC aussi en baisse de -3%) |
| **Action** | LONG |
| **Logique** | Quand un alt a crash de 40%+, que le volume explose, que le prix continue de tomber, ET que BTC est aussi faible — c'est un flush de liquidation force. Les traders en levier se font liquider en cascade, poussant le prix bien en dessous de sa valeur. Le rebond est violent. |
| **Hold** | 60h |
| **Stop** | **-15%** (leveraged) — plus serre que les autres signaux car backteste avec ce stop. -15% leveraged = mouvement de prix de -7.5%. |
| **z-score** | 6.99 (le plus eleve de tous les signaux) |
| **Mise** | $262 (la plus grosse, z-score le plus haut) |
| **Frequence** | Rare — ~1/mois en portfolio, ~5-6/mois en solo |
| **Backtest** | +$1,984 sur 192 trades, 70% win rate, 16/18 mois gagnants |
| **Pire scenario observe** | 7 pertes consecutives en avril 2024, drawdown -$265 |
| **Risque de liquidite** | S8 achete exactement quand les carnets d'ordres se vident. Le slippage reel peut etre 5-10x plus eleve que les 3 bps simules. En production, utiliser des ordres limit (maker) pour etre le filet qui attrape les liquidations au lieu de subir le spread. |

---

## Parametres

| Parametre | Valeur | Detail |
|---|---|---|
| **Levier** | 2x | Optimal d'un sweep 1x-3x. 3x = ruine par effet de compounding des pertes. |
| **Sizing** | 12% base + 3% bonus, z-weighted, haircut | Base 12% du capital, +3% bonus si z > 4.0, haircut si capital > $5k (positions plafonnees). `size = capital * base_pct * min(2.0, max(0.5, z/4.0))`. Plus le signal est fiable, plus la mise est grosse. |
| **Compounding** | Oui | `capital_courant = $1000 + P&L cumule`. Apres des gains, les mises grossissent. Apres des pertes, elles retrecissent. |
| **Hold** | 72h (S1/S2/S4), 48h (S5), 60h (S8) | Timeout automatique. Pas de stop de profit (teste : degrade les resultats). |
| **Stop loss** | -2500 bps (S1/S2/S4/S5), -1500 bps (S8) | En leveraged. Soit -12.5% de mouvement de prix (defaut) ou -7.5% (S8). Filet de securite, ne se declenche que dans les crashs extremes. |
| **Max positions** | 6 simultanees, max 4 meme direction | Evite la concentration directionnelle. |
| **Exposition** | Max 90% du capital | `used_margin + new_size <= capital * 0.90`. |
| **Frais simules** | 12 bps × leverage = 24 bps/trade | 7 taker + 3 slippage + 2 funding. Conservateur (frais reels Hyperliquid plus bas). |
| **Cooldown** | 24h par token apres exit | Empeche de re-entrer immediatement sur le meme token. |
| **Scan** | Toutes les heures | Bougies 4h, mais scan horaire pour reagir plus vite. |

### Sizing par signal (capital = $1,000)

| Signal | z-score | weight | Mise |
|---|---|---|---|
| S8 | 6.99 | 1.75 | $262 |
| S1 | 6.42 | 1.61 | $241 |
| S2 | 4.00 | 1.00 | $150 |
| S5 | 3.67 | 0.92 | $138 |
| S4 | 2.95 | 0.74 | $111 |

Formule : `weight = clamp(z / 4.0, 0.5, 2.0)`, `base_pct = 12% + 3% si z > 4.0`, `size = capital * base_pct * weight`, avec haircut si capital > $5k.

### Ce qui a ete teste et rejete pour les parametres

| Idee | Resultat |
|---|---|
| Stop loss serre (-7% au lieu de -25%) | Detruit la valeur — les trades gagnants passent souvent par un drawdown temporaire avant de remonter |
| Trailing stop (activer a +25 bps, couper a -15 du peak) | Pire que timeout fixe |
| Signal exit (sortir quand le signal s'inverse) | Perd de l'argent |
| Sizing ATR (reduire la taille quand le marche est volatile) | P&L -27%, ratio gain/DD pire. La volatilite est le carburant des signaux S2/S8, pas un risque. |
| Regime gating (bull-only S2, bear-only S4) | Degrade tous les signaux. S2 marche en bull ET en bear. |
| HMM / Markov regimes | Meme logique que le regime gating — reduire l'exposition en crash = couper S2/S8 exactement quand ils doivent tirer |
| Leverage 3x | Ruine par compounding des pertes. 2x est le sweet spot. |
| Max 10 positions au lieu de 6 | Performance identique (pas assez de signaux simultanement) |
| Smart priority (scoring + reservation + remplacement) | Aucune amelioration |

---

## Recherche

### Methode de validation (4 filtres obligatoires)

Chaque signal doit passer les 4 :

1. **Train/test split** — Trouve sur 2024 (train), valide sur 2025-2026 (test). Profitable sur les deux periodes.
2. **Monte Carlo** — Compare a du timing aleatoire avec le meme nombre de trades, meme direction, meme distribution par token. z-score > 2.0 requis.
3. **Portfolio integration** — Ajoute aux signaux existants et verifie que le P&L total augmente (pas de cannibalisation).
4. **Walk-forward rolling** — Train 12 mois, test 3 mois, avancer de 3 mois. Le signal doit etre profitable dans > 50% des fenetres.

### Ce qui a ete teste et elimine

**1ere vague (700+ regles)** :
- Momentum (acheter les gagnants) — bruit
- Mean-reversion cross-sectionnelle — pire strategie testee
- Effets calendaires (mardi > dimanche) — instable, n'existe qu'en bear
- Carry / funding — taux Hyperliquid trop bas (0.001 bps vs 1.6 bps Binance)
- Token unlocks — biais bear
- Pairs trading — toutes les configs perdent
- On-chain (whales, stablecoins) — pas predictif
- Programmation genetique (expression trees) — overfit systematique sur train
- ML walk-forward (RF + GBT) — confirme les features mais n'ajoute aucun signal nouveau

**2eme vague (800+ regles multi-conditions)** :
- S7 BTC-Alt recouple (`alt_vs_btc_7d < -1500 AND btc_7d > 300`) — echoue train/test
- S9 Exhaustion reversal (`consec_dn >= 5`) — echoue train/test
- S10 Vol compression + recovery — echoue train/test
- SX BTC-ETH spread divergence — echoue train/test
- SY Dispersion warning — echoue train/test
- 8 strategies SHORT (378 variantes : alt fade, BTC weakness, dead cat, bubble top, etc.) — aucune z > 2.0
- Regime gating (btc_30d, alt_index, combinaisons) — degrade S1, S2, S4
- Liquidation comme filtre (au lieu de signal) — pas d'amelioration

**Resultat** : 5 signaux sur 1500+ testes passent les 3 filtres.

### 4eme filtre : Walk-forward rolling

Au-dela du train/test simple, chaque signal est teste en **walk-forward** : train 12 mois, test 3 mois, avancer de 3 mois. Si un signal ne gagne pas > 50% des fenetres, il est fragile.

| Signal | Fenetres gagnantes | Stabilite |
|---|---|---|
| S8 | 8/9 (89%) | Tres stable |
| S1 | 3/4 (75%) | Stable (peu de fenetres car signal rare) |
| S4 | 7/10 (70%) | Stable |
| S2 | 5/9 (56%) | Borderline — perd presque 1 trimestre sur 2, mais gains compensent |
| S5 | ~50% | Difficile a evaluer (le vrai calcul sectoriel n'est pas reproductible en walk-forward simple) |

S8 est le signal le plus robuste out-of-sample. S2 est le plus fragile.

Test complementaire **Leave-5-tokens-out** (exclure 5 tokens au hasard, 10 iterations) : aucun signal ne depend de tokens specifiques. La robustesse vient des patterns de marche, pas de coins individuels.

### Backtest (32 mois, donnees reelles Hyperliquid)

| Annee | Contexte | Performance | Capital |
|---|---|---|---|
| 2023 (5 mois) | Bear | +8% | $1,000 -> $1,081 |
| 2024 | Bull | +528% | $1,081 -> $6,786 |
| 2025 | Bear/lateral | +145% | $6,786 -> $16,646 |
| 2026 (3 mois) | Lateral | -33% | $16,646 -> $11,214 |
| **Total** | **32 mois** | **+1,021%** | **$1,000 -> $11,214** |

- 20/32 mois gagnants (63%)
- Drawdown max : -54%
- Meilleur mois : +$3,040 (nov 2024)
- Pire mois : -$4,432 (jan 2026)

### S8 en detail (le nouveau signal)

| Metrique | Valeur |
|---|---|
| P&L total | +$1,984 |
| Trades | 192 |
| Win rate | 70% |
| Gain moyen | +413 bps/trade |
| Gain median | +512 bps |
| Pire trade | -1512 bps (stop loss) |
| Meilleur trade | +3072 bps |
| Mois gagnants | 16/18 (89%) |
| Max drawdown | -$265 |
| Ratio gain/DD | 7.5x |
| Max pertes consecutives | 7 (avril 2024, crash crypto prolonge) |
| En portfolio (+S1/S2/S4) | +$442 additionnel (+10%), z passe de 5.38 a 6.07 |

---

## Estimations de gain (sur $1,000)

### Ce que dit le backtest (fait historique)

32 mois de donnees Hyperliquid (aout 2023 → mars 2026), sizing v10.3.1 :
- **$1,000 → ~$7,000-$9,000** avec compounding (mises ~20% plus petites que le backtest 15%)
- 20/32 mois gagnants (63%), 12 perdants (37%)
- Drawdown max : -54% du peak
- Meilleur mois : +$3,040 (nov 2024, bull). Pire mois : -$4,432 (jan 2026, lateral)
- Bot inactif ~26% du temps (aucun signal)

Ces chiffres viennent du passe. Ils incluent une periode exceptionnelle (2024, +528%). Rien ne garantit qu'ils se reproduiront.

### Projection prudente (scenario central)

En degradant le backtest de ~50% pour tenir compte du data snooping residuel, du slippage reel, et de l'incertitude :
- **Rendement annuel estime : +50% a +100%** dans des conditions normales (melange bull/bear)
- Sur $1,000 : +$500 a +$1,000/an
- C'est une estimation, pas une promesse

### Scenarios extremes (pas centraux)

| Scenario | P&L annuel | Quand ca arrive |
|---|---|---|
| Bull exceptionnel (comme 2024) | +$2,000 a +$5,000 | BTC +100%, alts suivent, S1 se declenche |
| Marche lateral prolonge | -$100 a +$200 | Peu de signaux, bot dort, frais grignottent |
| Crash qui ne rebondit pas | -$200 a -$500 | S2/S8 achetent les dips, les dips continuent |

### Ce qui n'est PAS dans les chiffres

- **Slippage reel S8** : peut manger 20-50 bps de plus que simule (carnets vides pendant les flushes)
- **Frais reels Hyperliquid** : meilleurs que simule (maker rebates). Les deux s'annulent partiellement.
- **Data snooping residuel** : 1500+ regles testees = certains faux positifs possibles malgre 4 filtres de validation. Le paper trading est le test ultime.
- **S2 est le signal le plus fragile** : 5/9 fenetres walk-forward gagnantes (borderline). Il gagne gros quand il gagne, mais perd presque 1 trimestre sur 2. A surveiller en priorite.

### Ne PAS s'attendre a

- Un gain chaque mois. 37% des mois sont perdants.
- Que le backtest se reproduise a l'identique. Les conditions de marche changent.
- Des gains en marche totalement plat pendant des mois.
- Que le bot protege contre un flash crash de -50% en quelques heures.

---

## Protections du portfolio (v10.3.1)

| Protection | Detail |
|---|---|
| **Max 6 positions** | Limite absolue, jamais depassee |
| **Max 4 meme direction** | Empeche d'etre 100% LONG ou 100% SHORT |
| **Max 2 par secteur** | Empeche 4 LONG DeFi deguises en "diversification" |
| **Exposition max 90%** | Le bot garde toujours 10% de cash |
| **Stop loss** | -25% leveraged (S1/S2/S4/S5), -15% (S8) |
| **Kill-switch** | Auto-pause si P&L total < -$300 (-30% du capital) |
| **Loss streak** | 3 pertes consecutives → sizing divise par 2 pendant 24h |
| **Signal quarantine** | Win rate < 20% sur 10 derniers trades → signal coupe (QUARANTINE). Win rate < 30% → sizing /2 (DEGRADED). Le bot sait se mettre en retrait quand un signal se degrade. |
| **Cooldown** | 24h par token apres exit |
| **Mode degrade DXY** | Cache frais < 6h (normal). Fallback stale 6-48h (bandeau "DXY_STALE", S4 actif avec donnees anciennes). Cache > 48h ou absent (bandeau "DXY", S4 desactive). Yahoo peut tomber 2 jours sans tuer S4. |

---

## Risques

### Perte en capital
Drawdown max observe : -54%. Un investissement de $1,000 peut tomber a **$460** avant de remonter. 37% des mois sont perdants.

### Risque de modele
Les 5 signaux viennent de donnees passees (2023-2026). Le marche evolue. Ce qui marchait pourrait cesser de marcher. Le paper trading valide le modele en conditions reelles avant tout capital reel.

### Risque de liquidite (S8)
S8 achete pendant les flushes de liquidation — exactement quand les carnets d'ordres se vident. Le slippage reel peut etre 5-10x plus eleve que les 3 bps simules. Un trade gagnant sur papier peut devenir perdant en reel. Mitigation en production : ordres limit (maker) au lieu de market (taker).

### Risque de plateforme
Hyperliquid est un DEX sans assurance. Risques : bugs smart contract, hack, perte de fonds.

### Risque technique
Si le serveur tombe ou l'API est indisponible, les positions restent ouvertes. Le stop loss est le dernier filet.

### Scenarios de perte prolongee
- **Marche lateral prolonge** : peu de signaux, les rares trades perdent en frais. Bot inactif ~26% du temps.
- **Crash qui ne rebondit pas** : S2 et S8 achetent les dips, mais si le dip continue pendant des semaines (bear market structurel), les stops se font toucher en serie.
- **Dollar faible prolonge** : S4 (le seul SHORT) est desactive quand le dollar baisse. Le bot devient 100% LONG.

---

## Architecture technique

```
Hyperliquid REST API
    ├── metaAndAssetCtxs (prix + OI, toutes les 60s)
    ├── candleSnapshot (bougies 4h, toutes les heures, 30 tokens)
    └── Yahoo Finance (DXY, toutes les 6h, cache local)
            │
            ▼
    reversal.py  (processus asyncio unique, ~1300 lignes)
    ├── 24 features calculees par token, 13 utilisees pour les signaux
    │   (returns, vol, drawdown, recovery, BTC/ETH relative, alt index, sector div, vol_z)
    ├── Collecte OI + funding + premium (toutes les 60s, observation)
    │     oi_delta_1h/4h, funding_bps, premium
    │     Crowding score 0-100 par token (mesure la surchauffe du levier)
    ├── 5 signaux (S1, S2, S4, S5, S8)
    │     S1: btc_30d > +20%              → LONG 72h
    │     S2: alt_index < -10%            → LONG 72h
    │     S4: vol_ratio < 1 + range < 2% + DXY > +1% → SHORT 72h
    │     S5: sector div > 10% + vol_z > 1 → FOLLOW 48h
    │     S8: drawdown < -40% + vol_z > 1 + ret_24h < -0.5% + btc_7d < -3% → LONG 60h
    │     Chaque entree logue OI delta + crowding score (pas utilise pour decisions, observation)
    ├── Position manager
    │     Max 6 positions, max 4 meme direction, max 2 par secteur
    │     Stop: -25% leveraged (S8: -15%)
    │     Kill-switch: auto-pause si P&L < -$300
    │     Loss streak: 3 pertes → sizing /2 pendant 24h
    │     Signal quarantine: win rate < 20% → signal coupe
    │     Timeout: 48-72h selon signal
    │     Cooldown: 24h par token apres exit
    │     Exposure: max 90% du capital
    ├── Monitoring
    │     Signal drift: win rate + avg bps rolling par signal (20 derniers trades)
    │     Market CSV: snapshot horaire OI/funding/premium/crowding pour les 28 tokens
    ├── State persistence
    │     JSON atomic writes (tmp + os.replace)
    │     CSV trades log + CSV market snapshots
    │     Positions survivent aux redemarrages
    └── Dashboard FastAPI (:8097)
          Point vert pulsant + countdown prochain scan
          Crowding score par token + OI delta
          Bandeau rouge/jaune mode degrade
```

### Cycle de scan (toutes les heures)

1. `_fetch_prices()` — prix + OI + funding + premium via `metaAndAssetCtxs`
2. `_fetch_candles(sym)` — bougies 4h pour les 30 tokens (28 traded + BTC + ETH)
3. `_refresh_feature_cache()` — calcule les 24 features + OI summary pour chaque token
4. `_check_exits()` — ferme les positions en timeout ou en stop loss
5. `_scan_signals()` — detecte S1/S2/S4/S5/S8, applique quarantaine, trie par z-score, ouvre les positions (chaque entree logue OI delta + crowding score)
6. `_save_state()` — sauvegarde atomique JSON
7. `_log_market_snapshot()` — 28 lignes dans `reversal_market.csv` (OI, funding, premium, crowding, vol_z)

Entre les scans : prix + OI + funding rafraichis toutes les 60s, exits verifies (stop loss peut declencher hors scan).

### Features calculees (24 calculees, 13 utilisees en production)

| # | Feature | Description | Utilise par |
|---|---|---|---|
| 1 | ret_24h | Retour 6 bougies = 24 heures (i-6 × 4h) | S8 |
| 2 | ret_42h | Retour 42 bougies (7 jours) | tous |
| 3 | vol_7d | Volatilite realisee 7 jours | S4 |
| 4 | vol_30d | Volatilite realisee 30 jours | S4 |
| 5 | vol_ratio | vol_7d / vol_30d | S4 |
| 6 | range_pct | (high - low) / close de la bougie courante | S4 |
| 7 | drawdown | Prix actuel vs plus haut 30j | S8 |
| 8 | vol_z | Z-score du volume (actuel vs moyenne 30j) | S5, S8 |
| 9 | btc_30d | Retour BTC 30 jours | S1 |
| 10 | btc_7d | Retour BTC 7 jours | S8 |
| 11 | alt_index_7d | Moyenne des retours 7j des 28 alts | S2 |
| 12 | sector_divergence | Retour du token - moyenne de son secteur | S5 |
| 13 | DXY 7d | Retour du dollar index 7 jours (Yahoo Finance) | S4 |

(Les features 14-24 sont calculees dans le backtest mais seules celles ci-dessus sont utilisees en production.)

### Fichiers

| Fichier | Role |
|---|---|
| `analysis/reversal.py` | Le bot (~1300 lignes) |
| `analysis/reversal.html` | Dashboard web (pulse, countdown, crowding scores) |
| `analysis/output/reversal_state.json` | Etat des positions (atomic writes) |
| `analysis/output/reversal_trades.csv` | Historique des trades (signal_info inclut OI delta + crowding) |
| `analysis/output/reversal_market.csv` | Snapshots horaires : OI, funding, premium, crowding par token (~15 MB/an) |
| `analysis/output/reversal_v10.log` | Logs |
| `analysis/output/pairs_data/macro_DXY.json` | Cache DXY (frais 6h, stale jusqu'a 48h) |
| `analysis/output/pairs_data/*.json` | Bougies 4h par token |
| `docs/research_findings.md` | Journal de recherche complet |
| `docs/bot.md` | Ce fichier |

### API

| Endpoint | Description |
|---|---|
| `GET /` | Dashboard HTML (cache en memoire, restart pour mise a jour) |
| `GET /api/state` | Balance, positions, signaux actifs, timing, signal_drift, degraded, OI summary |
| `GET /api/signals` | 28 tokens avec features, OI delta, crowding score, signaux declenches |
| `GET /api/trades` | Historique (deque maxlen=500, `list()` avant slicing) |
| `GET /api/pnl` | Courbe P&L cumulative |
| `POST /api/pause` | Ferme toutes les positions + pause |
| `POST /api/resume` | Reprend (force scan immediat via `_last_scan = 0`) |
| `POST /api/reset` | Reset capital, ferme tout, backup CSV |

### Commandes

```bash
# Lancer
nohup .venv/bin/python3 -m analysis.reversal > analysis/output/reversal_v10.log 2>&1 &

# Dashboard
# http://0.0.0.0:8097

# Arreter
fuser -k 8097/tcp

# Logs
tail -f analysis/output/reversal_v10.log
```

### Deploiement sur machine vierge

Le bot tient en **2 fichiers** :
- `analysis/reversal.py` (~1300 lignes) — tout le code : API Hyperliquid, signaux, positions, persistence, dashboard, monitoring
- `analysis/reversal.html` (~270 lignes) — interface web (optionnel, le bot genere un fallback basique sans)

```bash
# 1. Creer la structure
mkdir -p analysis/output/pairs_data

# 2. Copier les 2 fichiers
cp reversal.py analysis/
cp reversal.html analysis/
touch analysis/__init__.py

# 3. Installer les dependances (Python 3.11+)
python3 -m venv .venv
.venv/bin/pip install numpy orjson uvicorn fastapi

# 4. Lancer
nohup .venv/bin/python3 -m analysis.reversal > analysis/output/reversal_v10.log 2>&1 &
```

Pas de base de donnees, pas de message queue, pas de microservices. Un seul processus asyncio. Le state est un fichier JSON (~2 Ko), les trades un CSV. Demarre en 2 secondes, tourne sur un VPS a 5 euros.

Pour dupliquer avec une config differente (autre capital, autres tokens, autre port) : copier `reversal.py`, modifier les constantes en haut du fichier (`CAPITAL_USDT`, `TRADE_SYMBOLS`, `WEB_PORT`), lancer. Les deux instances sont independantes (fichiers state/trades/market separes).

Le reste du repo (`src/`, `migrations/`, `backtest_*.py`, `docs/`) c'est de la recherche et du legacy. Le bot n'en depend pas.

---

## Plan de production

### Phase 1 — Paper trading (en cours)

Le paper trading se mesure en **occurrences par signal**, pas en duree calendaire :

| Signal | Frequence estimee | Trades minimum | Duree estimee |
|---|---|---|---|
| S1 | ~2-3/an | Inestimable en paper court — accepter l'incertitude | - |
| S2 | ~5-10/mois | 15+ trades | 2-3 mois |
| S4 | Variable (depend DXY) | 30+ trades | 3-6 mois |
| S5 | ~10-20/mois | 30+ trades | 2-3 mois |
| S8 | ~1/mois en portfolio | 5+ trades | 5+ mois |

**Critere de passage** : minimum **3 mois ET 50 trades cumules**, le plus tard des deux. Comparer par signal : win rate reel vs backtest, P&L moyen reel vs simule. Ecart > 2x = signal a investiguer.

Alertes :
- 0 trades en 2 semaines → verifier que le bot scanne
- P&L < -20% du capital → verifier les signaux, pas forcement arreter
- S2 win rate < 40% sur 15+ trades → reduire la mise S2

### Phase 2 — Passage en reel

Capital initial : $100-500

**Politique d'execution (pas juste "ordres limit")** :

| Aspect | Regle |
|---|---|
| **Mode par defaut** | Limit order (maker), TTL 30 secondes |
| **Fallback** | Si non fill apres TTL → market order (taker). Pas de chasing. |
| **Partial fills** | Accepter si > 70% du fill. Annuler le reste. |
| **S2/S8 specifique** | Limit a -0.3% sous le mid (profiter du flush). TTL 60s. On est le filet qui attrape les liquides. |
| **S4 (short calme)** | Limit au-dessus du mid (+0.1%). Marche calme = liquidite dispo, pas de probleme. |
| **Annulation** | Si le prix s'eloigne de > 1% du prix cible pendant le TTL, annuler. |

**Metriques a tracker en production** :

| Metrique | Objectif |
|---|---|
| Fill rate (% ordres executes) | > 80% maker, 100% avec fallback taker |
| Slippage reel vs simule | < 10 bps ecart (sauf S8) |
| Slippage S8 specifique | Si > 20 bps consistamment → reduire mise S8 |
| MAE (Max Adverse Excursion) | Combien un trade perd avant de remonter — valide le stop loss |
| MFE (Max Favorable Excursion) | Combien un trade gagne avant de redescendre — valide le hold time |
| Temps de fill | < 30s pour maker, < 5s pour taker |

**Autres pre-requis** :
- Verification croisee : comparer `state.json` avec `clearinghouseState` de l'API Hyperliquid
- Alertes Telegram : notification a chaque entry/exit/erreur
- Persistence : SQLite au lieu de JSON pour la production (journalisation, reconciliation)

### Phase 3 — Scaling

- Minimum 2 mois reels coherents avec backtest avant d'augmenter
- Augmentation progressive : $500 → $1,000 → $2,000 → $5,000
- A chaque palier, re-evaluer : le slippage a-t-il augmente ? Les fills sont-ils bons ?
- Ne jamais mettre plus que ce qu'on est pret a perdre en totalite (drawdown -54% observe)
