# Research Findings — Strategy Backtests (24 mars 2026)

## Context
Backtest sur données historiques Binance. Klines 1 an, OI/LS 27j, Funding 1 an.
13 symboles altcoins + BTC/ETH référence. Coût simulé: 4 bps roundtrip (3 bps fees BNB + 1 bps slippage).

## OI Divergence (stratégie originale) — INVALIDÉE
- Backtest 27j: **-$202 sur $1000** (1491 trades, 46% win)
- Signal quasi-aléatoire sur données récentes (gross +1.4 bps, net -2.6)
- Study_06 trouvait +21 bps sur 7j d'ADA → artefact statistique
- 42% des trades touchent le stop loss → le signal entre dans le mauvais sens

## Stratégies testées (classement)

### GAGNANTES

| Stratégie | Net/trade | Trades/90j | Win% | Robustesse |
|---|---|---|---|---|
| **Funding Sniper (entry-1h, hold 2h)** | +24.8 bps | 85 | 52% | ✓ 3 mois, mécanique |
| **Funding Sniper (entry-2h, hold 30m)** | +19.9 bps | 85 | 52% | ✓ 3 mois |
| **Funding Momentum (3× consécutif)** | +17.0 bps | 90 (27j) | 53% | ✓ edge structurel |
| **Extreme Reversion (>150bps/1h)** | +9.8 bps | 16567 (27j) | 55% | ⚠ beaucoup de trades |
| OI velocity >1% follow (120m) | +4.9 bps | 279 (27j) | 54% | ⚠ marginal |
| Volume Spike z>4 (30m) | +0.9 bps | 3025 (27j) | 54% | ⚠ marginal |

### PERDANTES
- Momentum Cascade (BTC → alts): -0.6 à -5.7 bps
- Smart Money seul: -4.0 bps (49% win = aléatoire)
- Cross-symbol lag: -4.0 bps
- Fixed-time momentum: -3.3 à -17.4 bps
- Bollinger Squeeze: -4.9 bps
- Crowd Capitulation: -0.6 bps
- Post-settlement reversion: -10.1 bps

### TROP BEAU (biais look-ahead)
- Multi-TF Follow (1h>100, 4h>150): +206 bps, 95% win → entre au milieu du move, pas exploitable tel quel

## Funding Sniper — Analyse détaillée (90 jours)

**Config optimale**: fund > 3 bps, entry 1h avant settlement, hold 2h

**Par mois**:
- Déc 2025: -22 bps (15 trades)
- Jan 2026: +24 bps (22 trades)
- Fév 2026: +100 bps (22 trades)
- Mar 2026: -11 bps (26 trades)

**Par session**:
- Asia: +41 bps ✓
- Overnight: +86 bps ✓
- US: -21 bps ✗ → ne pas trader

**Par symbole (top)**:
- ZROUSDT: +65 bps (29 trades) ✓
- XMRUSDT: +20 bps (18 trades) ✓
- XRPUSDT: +65 bps (2 trades) ✓
- AVAXUSDT: +19 bps (3 trades) ✓

**Symboles perdants**: TRX (-18 bps), BCH (-29 bps), TON (-50 bps)

**P&L simulation**: +$53 sur 90j à $250/trade = +$17.6/mois

## Backtest 1 AN — Extreme Reversion (signal principal validé)

**Période** : 23 mars 2025 → 23 mars 2026 (365 jours, 10 symboles)

### Résultats bruts signal

| Signal | Net/trade | Trades/jour | Win% | Robustesse |
|---|---|---|---|---|
| **Extreme Reversion >150bps** | **+7.5 bps** | 85 | 53% | ✓ 1 an, 31182 trades |
| Funding Sniper >3bps | +1.2 bps | 0.8 | 54% | ⚠ fragile sur 1 an |
| Combiné | +7.4 bps | 86 | 53% | ✓ |

### Funding Sniper : edge instable dans le temps

| Période | Net/trade |
|---|---|
| 27 jours | +24.8 bps |
| 90 jours | +19.9 bps |
| **1 an** | **+1.2 bps** ← quasi-nul |

Le funding sniper surperformait sur la période récente mais ne tient pas sur 1 an. L'extreme reversion est le vrai moteur.

### Par mois (combiné, hold 120m)

| Mois | Trades | Net bps | Résultat |
|---|---|---|---|
| 2025-03 | 839 | -9.7 | ✗ |
| 2025-04 | 3142 | +6.4 | ✓ |
| 2025-05 | 3426 | -6.9 | ✗ |
| 2025-06 | 2002 | -8.4 | ✗ |
| 2025-07 | 3068 | -14.8 | ✗ |
| 2025-08 | 2292 | +14.3 | ✓ |
| 2025-09 | 1275 | -14.3 | ✗ |
| 2025-10 | 2889 | +52.8 | ✓✓ |
| 2025-11 | 3565 | +10.7 | ✓ |
| 2025-12 | 1612 | -22.1 | ✗ |
| 2026-01 | 2469 | -9.2 | ✗ |
| 2026-02 | 3618 | +38.2 | ✓✓ |
| 2026-03 | 1263 | +17.1 | ✓ |

**6 mois perdants / 13** — mais les gagnants gagnent plus que les perdants perdent.

### Par symbole (1 an)

Tous positifs sauf AVAXUSDT (-1.5 bps). BNB est le meilleur (+23 bps).

### Estimation P&L réaliste

- Max ~22 trades/jour (4 positions × 11h / 2h hold)
- 22 × $250 × 7.4 bps = **~$4/jour = $123/mois = 12%/mois**
- Max drawdown : important (>$1000 en simulation brute)
- **6 mois perdants sur 13** — nécessite un circuit breaker ou filtre de régime

## Delta-Neutral Funding Carry — Analyse 1 an

### Sans basis risk (funding seul)
- 1 pair × 3x : **+3.6%/mois, 0 mois perdants** (trop beau)
- Le funding est mécanique : XMR (mean +1.62 bps) + ZRO (mean -0.89 bps)

### Avec basis risk (réaliste)
- Carry seul : **+$232/an = +1.8%/mois** mais **7 mois perdants**
- La divergence de prix entre les deux legs détruit les gains de funding
- Le basis risk est le vrai problème du carry delta-neutral

### Combo Carry + Extreme Reversion (simulation complète)
- **Résultat : -$782 sur 1 an** → le combo ne marche pas
- Le carry fait +$232, l'extreme reversion fait -$1014
- L'extreme reversion perd en simulation avec hold fixe 2h (pas de trailing stop)

### Leçon clé
Le carry sans gestion du basis risk promet 3.6%/mois mais livre 1.8%.
L'extreme reversion a un signal valide (+7.5 bps) mais la gestion de position doit
inclure un trailing stop pour capturer l'edge.

## Extreme Reversion avec gestion de position réelle — 1 an, 9 configs

**Résultat : TOUTES les configs perdent.**

| Config | Trades | Win% | P&L | %/mois | Losing months |
|---|---|---|---|---|---|
| Baseline (trail 25/15, sl -50, 150bps) | 4018 | 45% | -$360 | -2.8% | 9/13 |
| Tight (trail 15/10, sl -40) | 4413 | 43% | -$317 | -2.4% | 9/13 |
| Wide (trail 35/20, sl -60) | 3638 | 47% | -$362 | -2.8% | 8/13 |
| **Thresh 200 (meilleur)** | **2334** | **46%** | **-$173** | **-1.3%** | **8/13** |
| No trail, sl only | 2456 | 29% | -$169 | -1.3% | 8/13 |

### Pattern identique dans toutes les configs :
- Trail stop : **84% win, +$4348** → le trailing stop capture bien
- Stop loss : **0% win, -$5116** → les stop loss mangent TOUT le profit
- Le stop loss perd plus que le trailing stop gagne → net négatif

### Par symbole (config thresh 200) :
- Gagnants : XLM (+$84), SUI (+$28), ADA (+$23)
- Perdants : ZRO (-$241), XMR (-$231), AAVE (-$186)

### Conclusion
L'extreme reversion mean-reversion **ne fonctionne pas sur 1 an** même avec trailing stop.
Le signal raw (+7.5 bps) ne survit pas à la gestion de position (stop loss trop fréquents).
Le marché trend plus qu'il ne mean-revert sur ces timeframes.

## OI comme filtre — 3 hypothèses testées (27 jours)

### Hypothèse originale : OI dropping = exhaustion → FAUX
L'intuition était : prix crash + OI baisse = liquidations terminées = rebond.
**Résultat inverse** : OI qui MONTE pendant un move extrême donne de meilleurs résultats.

| Filtre OI | Trades | Win% | Net/trade | P&L |
|---|---|---|---|---|
| Baseline (pas de filtre) | 666 | 57% | -3.2 bps | -$54 |
| OI dropping (hypothèse) | 423 | 54% | -5.5 bps | -$58 |
| **OI rising >1% (inverse)** | **62** | **61%** | **+8.1 bps** | **+$13** |

**Mécanisme** : OI qui monte = nouveaux shorts/longs s'empilent → doivent déboucler → "rubber band".

### Autres tests OI (tous perdants sur 27j)
- OI z-score regime (z>0.5 à z>1.5) : ne filtre pas les mauvais trades
- OI velocity momentum (suivre le surge OI) : perd (-13 à -26 bps)
- OI squeeze breakout (OI élevé + vol basse) : perd (-2 à -9 bps)
- Funding + OI double exhaustion : trop peu de trades (2-47)
- OI comme SL dynamique : quasi-aucun impact
- Smart OI (rev + OI rising + funding aligned) : ajouter le funding EMPIRE les résultats

### Conclusion OI
L'OI est utile uniquement comme filtre "rubber band" (OI rising >1% pendant move extrême).
Mais 27 jours = échantillon trop court pour valider.

## Volume comme proxy de l'OI — Validation 1 an

### Cross-validation 27 jours : Volume z-score > OI rising

| Filtre | Trades | Win% | Net/trade | P&L | Trail/SL ratio |
|---|---|---|---|---|---|
| OI rising >1% (référence) | 62 | 61% | +8.1 bps | +$13 | 1.95x |
| **Vol z-score > 1.5** | **201** | **66%** | **+6.9 bps** | **+$35** | **2.47x** |
| Vol z-score > 2.0 | 155 | 66% | +8.3 bps | +$32 | 2.78x |

Le volume z-score est un meilleur proxy : plus de trades, meilleur ratio trail/SL.

### Backtest 1 AN — Extreme Reversion + Volume z-score

**Période** : 23 mars 2025 → 23 mars 2026 (365 jours, 13 symboles)

| Config | Trades | Win% | Net/trade | P&L 1 an | Max DD | Mois perdants |
|---|---|---|---|---|---|---|
| Baseline Rev>150 (pas de filtre) | 8693 | 53% | -1.8 bps | **-$382** | $529 | 7/13 |
| Vol z>1.0 | 3516 | 52% | -3.6 bps | -$315 | $346 | 7/13 |
| Vol z>1.5 | 2656 | 51% | -3.4 bps | -$225 | $307 | 9/13 |
| **Vol z>2.0** | **2065** | **52%** | **+1.7 bps** | **+$87** | **$111** | **9/13** |
| **Vol z>2.5** | **1601** | **51%** | **+3.6 bps** | **+$144** | **$163** | **9/13** |
| **Vol z>2 + SL tight (-30/20/12)** | **2128** | **44%** | **+3.0 bps** | **+$159** | **$118** | **8/13** |
| Vol z>2 + SL -40/25/15 | 2069 | 47% | -0.6 bps | -$30 | $239 | 9/13 |
| Vol z>2 + SL wide (-70/35/20) | 2012 | 56% | -1.5 bps | -$76 | $223 | 9/13 |
| Vol z>2 + NO SL | 1782 | 67% | -5.8 bps | -$260 | $339 | 8/13 |

### Meilleure config : Rev>150 + Vol z>2.0 + SL tight

- **+$159/an = +1.3%/mois** à $250/trade, 7 trades/jour
- 9 symboles gagnants / 13
- Max drawdown $118 (11.8%)
- **8 mois perdants sur 13** — gains concentrés sur octobre 2025 (+$218)

### Par mois (meilleure config C1)

| Mois | Trades | P&L | $/jour |
|---|---|---|---|
| 2025-03 | 41 | -$12 | -$1.77 ✗ |
| 2025-04 | 160 | -$17 | -$0.79 ✗ |
| 2025-05 | 230 | -$14 | -$0.49 ✗ |
| 2025-06 | 204 | -$25 | -$0.86 ✗ |
| 2025-07 | 148 | -$7 | -$0.28 ✗ |
| 2025-08 | 161 | +$2 | +$0.08 ✓ |
| 2025-09 | 160 | +$15 | +$0.59 ✓ |
| **2025-10** | **194** | **+$218** | **+$9.08** ✓✓ |
| 2025-11 | 187 | +$29 | +$1.02 ✓ |
| 2025-12 | 142 | -$27 | -$1.27 ✗ |
| 2026-01 | 169 | -$33 | -$1.33 ✗ |
| 2026-02 | 230 | -$2 | -$0.09 ✗ |
| 2026-03 | 102 | +$34 | +$1.98 ✓ |

### Conclusion volume proxy
Le filtre volume z>2.0 transforme une stratégie perdante (-$382) en stratégie marginalement profitable (+$159).
C'est le **meilleur résultat sur 1 an** de toute la recherche, mais **+1.3%/mois n'est pas suffisant**
pour justifier le risque (8 mois perdants, drawdown 12%).

## Bilan global — Toutes stratégies testées sur 1 an

| Stratégie | P&L 1 an | %/mois | Mois perdants | Verdict |
|---|---|---|---|---|
| OI divergence (originale) | -$202 (27j) | - | - | ✗ Invalidée |
| Extreme reversion raw | -$382 | -3.1% | 7/13 | ✗ |
| Extreme reversion + trailing stop | -$173 à -$360 | -1.3 à -2.8% | 8-9/13 | ✗ |
| Funding sniper >3bps | +1.2 bps/t | ~0% | - | ✗ Quasi-nul |
| Carry delta-neutral | +$232 | +1.8% | 7/13 | ⚠ Fragile |
| **Rev + Vol z>2 + SL tight** | **+$159** | **+1.3%** | **8/13** | ⚠ Meilleur mais insuffisant |

**Aucune stratégie testée n'atteint 8-10%/mois sur 1 an.**
Le maximum réaliste est ~1-2%/mois avec des drawdowns importants et beaucoup de mois perdants.

## Carry Funding — Binance vs Hyperliquid

### Binance (inaccessible depuis France)
- XMR seul 3x : **+4.1%/mois**, 1/13 mois perdants
- Top 3 (XMR+LTC+AAVE) 3x : +2.1%/mois, **0 mois perdants**
- Le carry Binance marche grâce aux funding rates non capés (XMR mean +1.62 bps/8h)

### Hyperliquid
- Funding capé à ~0.125 bps sur la plupart des tokens
- Carry dynamique top 3 : **+0.55%/mois brut** → insuffisant après frais
- Les pics temporaires (MON -0.9, LIT -0.8) ne sont pas persistants (moyenne 90j ~0.2 bps)

## Stratégies testées sur Hyperliquid (pistes explorées)

| Stratégie | Résultat | Verdict |
|---|---|---|
| Carry funding | +0.5%/mois | ✗ Trop faible (funding capé) |
| Token unlock sniping | +$784 mais biais bear | ✗ Non significatif (z<1 vs random) |
| Stablecoin flow on-chain | Contemporain pas prédictif | ✗ Dead |
| Whale deposit tracking | 0 signal (besoin Nansen) | ✗ Impraticable |
| Pairs trading stat arb | Toutes configs perdent | ✗ Corrélations instables |
| Cycle 8h settlement | Toutes configs perdent | ✗ Edge < frais |
| Beta catch-up (résidual) | +$1,736 | ⚠ Marginal |

## DÉCOUVERTE : Multi-Day Reversal — VALIDÉ (z-score > 10)

### Le signal
Quand un altcoin sur-réagit sur 3-7 jours (>5% de mouvement), il corrige les 3-5 jours suivants.
- Prix chute >5% en 3-7j → LONG (acheter le creux)
- Prix monte >5% en 3-7j → SHORT (vendre le sommet)
- Bidirectionnel : les deux côtés fonctionnent

### Validation statistique — Backtest v1 (INVALIDÉ)

Le premier backtest montrait +$57,080 / 14 mois. **Résultat invalide** à cause de :
1. **Trades qui se chevauchent** — pas de limite 1 position/token → inflation ×11.5
2. **Look-ahead bias** — entry au même candle que le signal au lieu du next open
3. **Pas de position limits** — le backtest avait des centaines de positions simultanées

### Backtest v2 (CORRIGÉ) — Résultats réels

Corrections appliquées : 1 position/token, entry au next candle open, max 6 positions,
max 4 même direction, stop loss -15%, cooldown 6h.

**500 Monte Carlo direction-matched (même ratio long/short, dates aléatoires) :**

| Config | P&L réel | P&L random | Z-score | Signal ? |
|---|---|---|---|---|
| **Rev 7d>1000 hold 3d** | **+$1,404** | -$416 | **2.58** | **✓✓ Significatif** |
| **Rev 3d>750 hold 3d** | **+$1,036** | -$309 | **2.01** | **✓✓ Significatif** |
| Rev 3d>1000 hold 3d | +$459 | -$137 | 0.96 | ✗ Non significatif |

### Résultats détaillés — Config retenue (7d>1000 hold 3d)

**Période** : 14 mois (fév 2025 → mars 2026), 4h candles, 40 tokens Hyperliquid

| Métrique | Valeur |
|---|---|
| Trades | 752 |
| Win rate | 54% |
| Gross moyen | +82 bps/trade |
| Coûts réalistes | 12 bps (7 fees + 3 slippage + 2 funding drag) |
| **Net moyen** | **+70 bps/trade** |
| **P&L total (14 mois)** | **+$1,404** |
| **P&L mensuel** | **+$88/mois** (~8.8% sur $1000) |
| Mois gagnants | **10/14** |
| Max drawdown | $671 |
| Long P&L | +$158 (marginal) |
| Short P&L | +$1,245 (porte 90%) |
| Stop loss trades | 105 → -$3,956 |
| Timeout trades | 647 → +$5,360 |

### Par mois

| Mois | P&L |
|---|---|
| 2025-02 | +$89 ✓ |
| 2025-03 | -$28 ✗ |
| 2025-04 | +$230 ✓ |
| 2025-05 | +$489 ✓ |
| 2025-06 | +$18 ✓ |
| 2025-07 | -$73 ✗ |
| 2025-08 | +$518 ✓ |
| 2025-09 | -$17 ✗ |
| 2025-10 | +$15 ✓ |
| 2025-11 | +$47 ✓ |
| 2025-12 | +$23 ✓ |
| 2026-01 | -$155 ✗ |
| 2026-02 | +$133 ✓ |
| 2026-03 | +$116 ✓ |

### Par token : 23/39 profitables
Top : COMP +$300, GALA +$229, PYTH +$221, GMX +$155, EIGEN +$152
Flop : JUP -$115, ENA -$95, SAND -$92

### Risk tuning testé

| Config | $/mois | Max DD | Mois gagnants |
|---|---|---|---|
| **Baseline (SL -15%, 6pos)** | **+$88** | **$671** | **10/14** |
| Shorts only | +$69 | $413 | 10/14 |
| Max 3 positions | +$33 | faible | 9/14 |
| SL -10% | +$2 | modéré | 4/14 |
| SL -7% | +$6 | modéré | 6/14 |
| NO stop loss | +$10 | ? | 7/14 |

**Paradoxe** : resserrer le SL empire les résultats. Les trades multi-jours oscillent avant
de converger — un SL serré coupe des gagnants prématurément.

### Pourquoi hold = 72h exactement (et pas 60h ou 84h)

Testé chaque incrément de 4h entre 48h et 96h :

| Hold | $/mois | Win% | Mois gagnants |
|---|---|---|---|
| 48h (2.0d) | -$7 | 52% | 6/14 |
| 60h (2.5d) | -$20 | 51% | 4/14 |
| 64h (2.7d) | +$35 | 51% | 8/14 |
| 68h (2.8d) | +$37 | 51% | 7/14 |
| **72h (3.0d)** | **+$100** | **54%** | **10/14** |
| 76h (3.2d) | +$47 | 52% | 4/14 |
| 84h (3.5d) | +$16 | 51% | 5/14 |
| 96h (4.0d) | -$26 | 49% | 4/14 |

72h est un **pic net** (2-3× mieux que ses voisins, pas un plateau).
Explication probable : 72h = 9 settlements funding = rythme naturel du marché crypto.
Les sur-réactions altcoins se corrigent en 3 jours, au-delà un nouveau trend peut se former.

### Pourquoi ça marche
1. **Timeframe 7 jours** : les frais (12 bps) sont négligeables face aux moves (1000+ bps)
2. **Les altcoins sur-réagissent** : panic selling / FOMO buying crée des excès qui se corrigent en 3j
3. **Validé statistiquement** : z=2.58 direction-matched Monte Carlo
4. **Bidirectionnel** : long + short fonctionnent, mais short domine sur cette période

### Limitations connues (14 mois)
- **Short porte 90% du P&L** — période bearish altcoins
- **Stop losses destructeurs** : 105 trades SL = -$3,956 (mangent 74% des gains du timeout)

### INVALIDATION : Backtest 3 ans (avril 2023 → mars 2026)

Le backtest étendu à 3 ans (couvrant un bull ET un bear market complet) **invalide la stratégie**.

| Période | P&L | $/mois | Mois gagnants |
|---|---|---|---|
| 14 mois (fév 2025 → mars 2026) | +$1,048 | +$75 | 10/14 |
| **36 mois (avr 2023 → mars 2026)** | **-$512** | **-$14** | **17/36** |

**Par année :**
| Année | P&L | Régime de marché |
|---|---|---|
| 2023 | -$222 | Calme → peu de trades |
| 2024 | **-$966** | **Bull market → les trends écrasent la mean-reversion** |
| 2025 | +$578 | Bear market → la mean-reversion fonctionne |
| 2026 (3 mois) | +$98 | Bear → fonctionne |

**Diagnostic** : la multi-day reversal ne marche qu'en bear market.
En bull market (2024), les altcoins qui montent de 10% continuent de monter.
Le short se fait écraser par le trend. Résultat : 290 stop losses = -$8,770 vs timeouts = +$8,257.

**Le z-score de 2.58 (14 mois) était réel mais spécifique à la période bearish.**
Sur un cycle complet, la stratégie n'a pas d'edge.

### Autres tests effectués (tous négatifs)
- **3 bots décalés 24h** : même P&L par bot, pas de gain de diversification temporelle
- **Pipeline roulant (15-18 positions)** : perd (corrélation entre tokens le même jour)
- **Circuit breaker** (pause après N pertes) : rate les rebonds, empire le P&L
- **Max perte/jour** : réduit le P&L plus que le drawdown
- **Kelly sizing** : augmente le P&L mais aussi le drawdown
- **Take-profit / trailing stop** : coupe les gagnants trop tôt
- **Hold 2.5d ou 3.5d** : 72h est un pic net, les voisins perdent
- **Sorties anticipées** : toutes configurations inférieures au timeout brut 72h

### Bot actuel (v8.1.1) — en paper trading, résultats à surveiller

| Paramètre | Valeur |
|---|---|
| Version | 8.1.1 |
| Exchange | Hyperliquid (DEX, pas de KYC) |
| Lookback | 168h (7 jours) |
| Seuil | 1000 bps (10%) |
| Hold | 72h (3 jours) |
| Coûts simulés | 12 bps (fees 7 + slippage 3 + funding 2) |
| Max positions | 6 |
| Max même direction | 4 |
| Stop loss | -1500 bps (-15%) |
| Size/trade | $200 |
| Tokens | 23 |
| Dashboard | http://51.178.27.240:8095 |
| **Statut** | **Paper trading — résultats non concluants sur 3 ans** |

## Bilan final — Toutes stratégies testées

| Stratégie | Exchange | Période | P&L | Validé ? | France ? |
|---|---|---|---|---|---|
| OI divergence | Binance | 27j | -$202 | ✗ | Non |
| Extreme reversion 1h | Binance | 1 an | -$382 | ✗ | Non |
| Rev + Vol z>2 + SL tight | Binance | 1 an | +$159 | ⚠ Marginal | Non |
| Carry XMR 3x | Binance | 1 an | +$533 | **✓ Seul validé** | **Non** (AMF) |
| Carry dynamique | Hyperliquid | 90j | +$66 | ✗ Trop faible | Oui |
| Pairs trading stat arb | Hyperliquid | 6 mois | -$53 | ✗ | Oui |
| Token unlocks | Hyperliquid | 1 an | biais bear | ✗ | Oui |
| Stablecoin flow on-chain | DeFiLlama | 1 an | pas prédictif | ✗ | - |
| Whale tracking on-chain | Etherscan | 90j | 0 signal | ✗ Besoin Nansen | - |
| Cycle 8h settlement | Binance | 1 an | toutes perdent | ✗ | Non |
| Multi-Day Reversal | Hyperliquid | **14 mois** | +$1,048 | ⚠ Bear only | Oui |
| Multi-Day Reversal | Hyperliquid | **3 ans** | **-$512** | **✗ Invalidé** | Oui |

## Algo-Generated Rules — Recherche exhaustive

### Méthode
Recherche exhaustive de règles simples (feature + seuil + direction) sur 6 features :
ret_7d, ret_14d, ret_30d, vol_ratio, btc_7d, btc_30d.

**Train** : 2023-2024 (59,960 samples) | **Test** : 2025-2026 (73,449 samples)
Seules les règles profitables sur BOTH train ET test sont retenues.

### Règles découvertes (profitables bull + bear, sans limites de position)

| Règle | Logique | Train $/mo | Test $/mo |
|---|---|---|---|
| btc_30d > +20% → LONG | BTC rip → alts suivent | +$786 | +$243 |
| btc_7d < -5% → LONG | BTC dip → rebond alts | +$298 | +$227 |
| ret_14d < -30% → LONG | Alt crash extrême → rebond | +$115 | +$223 |
| ret_7d < -20% → LONG | Alt dip → rebond | +$208 | +$128 |

Note : toutes les règles rentables sont LONG. Les shorts ne fonctionnent qu'en bear.

### S6 Combo — Résultats initiaux puis CORRIGÉS

Trois signaux OR :
1. **btc_7d < -500 bps** → LONG (acheter le BTC dip)
2. **ret_14d < -3000 bps** → LONG (acheter le crash altcoin)
3. **btc_30d > +2000 bps** → LONG (acheter les alts quand BTC rip)

**⚠ Résultats initiaux surestimés.** Vérification indépendante a trouvé :

| | Annoncé | Vérifié |
|---|---|---|
| P&L total | +$1,753 | **+$753 à +$1,066** |
| %/mois | +6.5% | **+2.8 à 3.9%** |
| Période | 3 ans | **27 mois** (data réelle) |
| 2025 (bear) | +$880 | **+$179** |
| 2024 (bull) | +$923 | ~confirmé |

**Bugs trouvés lors de la vérification :**
1. **MAX_DIR non respecté** — jusqu'à 6 longs au lieu de 4 → -29% de P&L
2. **Règle alt crash (ret_14d<-30%) perd -$590** — dilue les résultats, à retirer
3. **Période réelle = 27 mois** (pas 36, data tokens commence fin 2023)

**Ce qui EST confirmé :**
- Z-score = **4.09** vs random longs (500 simulations) → le timing a un vrai alpha
- Conditions actives **43% du temps** (pas "toujours long")
- Les 2 signaux BTC (dip + rip) fonctionnent. Le signal alt crash non.
- L'altcoin market était bearish sur la période (random long = -$833) → le +$753 est vrai

### Stratégie corrigée recommandée : S4+S1 (BTC dip + BTC rip, sans alt crash)

Deux signaux OR :
1. **btc_7d < -500 bps** → LONG alts
2. **btc_30d > +2000 bps** → LONG alts

**Résultats corrigés réalistes : ~+3-4%/mois sur 27 mois**, principalement porté par le bull 2024.
En bear 2025 : modeste (+$179). Le signal est réel mais le rendement est plus faible qu'annoncé.

### Pourquoi ça marche
1. En **bull** : le signal "BTC rip" domine → les altcoins suivent BTC avec du retard
2. En **bear** : le signal "BTC dip" domine → les bounces sont violents
3. **Tout est LONG** : pas de short = pas de risque en bull market
4. **Conditionnel** (43% du temps) : pas du "buy and hold"

### Limitations honnêtes
- 27 mois de data (pas un cycle complet)
- Rendement surestimé dans les annonces initiales (vérifié à +3-4%, pas +6.5%)
- Fonctionne surtout en bull (+$923 en 2024 vs +$179 en 2025)
- 2026 démarre négatif (-$110 sur 3 mois)
- 100% LONG → en bear prolongé sans bounces BTC, ça perdra
- Win rate ~51-53% = fragile

---

## Recherche génétique multi-facteurs (24 mars 2026)

### Méthode
Recherche exhaustive + algorithme génétique sur **22 features** × **28 tokens** × **3 ans** de data 4h Hyperliquid.
Features : returns (6h/7d/14d/30d), volatilité (7d/30d/ratio), drawdown, recovery, range, consec_up/dn,
btc_7d, btc_30d, eth_7d, btc_eth_spread, alt_vs_btc, alt_index_7d, dispersion_7d, alt_rank_7d, vol_z.

Train: 2023-2024 | Test: 2025-2026 | Coût: 12 bps | Max 6 positions | Stop -15%

### Résultats : 4 signaux validés (z > 2.5, profitables train ET test)

| # | Signal | Dir | Z-score | P&L 3 ans | Trades | Breakeven cost | Fréquence |
|---|---|---|---|---|---|---|---|
| S1 | btc_30d > +20% → LONG | LONG | **6.42** | +$2,195 | 101 | 881 bps | 11% |
| S2 | alt_index_7d < -10% → LONG | LONG | **4.00** | +$1,706 | 552 | 136 bps | 19% |
| S3 | btc_7d < -5% AND ret_42h < -20% → LONG | LONG | **3.58** | +$1,435 | 285 | 213 bps | 4% |
| S4 | vol_ratio < 1.0 AND range_pct < 200 → SHORT | SHORT | **2.95** | +$2,609 | 1,185 | 100 bps | 24% |

Note: S3 est redondant avec S2 (15% d'overlap, ρ=+0.61). En portfolio combiné S3 ne prend que 10 trades.

### Algorithme génétique : OVERFIT
Le meilleur individu génétique (drawdown < -500 + vol_z < 2 + consec_dn < 6 → LONG) :
+$2,376 en train, **-$1,641 en test**. Classique overfitting. Rien de nouveau vs scan exhaustif.

### Corrélation entre stratégies (mensuelle)
- S1 × S4 : ρ = **-0.43** (anti-corrélé → excellent pour portfolio)
- S1 × S3 : ρ = -0.02 (indépendant)
- S2 × S3 : ρ = +0.61 (corrélé → redondant)
- S2 × S4 : ρ = -0.37 (anti-corrélé → bon)

### Sensibilité des seuils

**S1 btc_rip** — ROBUSTE :
- btc_30d > 1500: +$1,604 | > 2000: +$2,195 ◄ | > 2500: +$1,687 | > 3000: +$1,184
- Le signal marche de 1500 à 3000. Pas fragile.

**S2 alt_crash** — FRAGILE au seuil :
- alt_idx < -500: **-$944** | < -750: +$18 | < -1000: +$1,706 ◄ | < -1500: +$810
- En dessous de -1000, ça marche. Au-dessus, ça perd. Seuil critique.

**S4 vol_short** — STABLE :
- vol_ratio < 0.7: +$652 | < 0.9: +$1,470 | < 1.0: +$2,609 ◄ | < 1.2: +$1,901
- range < 150: +$1,704 | < 200: +$2,609 ◄ | < 250: +$2,315 | < 300: +$1,531

**Coûts** — Marge confortable :
- À 20 bps de coût total : +$7,521 (au lieu de +$7,946 à 12 bps). Le edge survit largement.

### Portfolio combiné (backtest avec position limits)

| Portfolio | Capital final | Mois gagnants | Max DD | Annualisé |
|---|---|---|---|---|
| **Combo A : S1+S2+S3+S4** (25% sizing) | $20,340 | 20/35 (57%) | -52.8% | +181% |
| **Combo B : S1+S2+S3 LONG only** | $5,725 | 15/26 (58%) | -60.4% | +124% |
| **Combo C : S1+S2+S3+S4** (15% sizing) | $8,662 | 21/35 (60%) | -35.4% | +110% |

⚠ Ces résultats INCLUENT le compounding (sizing en % du capital). Les P&L en $ sont gonflés par les mois où le capital était déjà élevé. Le sizing amplifie gains ET pertes.

**Monte Carlo portfolio :**
- Combo A (All 4) : z = **+2.18** (significatif, p=0.011)
- Combo B (LONG only) : z = **+2.85** (fortement significatif, p=0.001)

### Problèmes identifiés

1. **Stop loss détruit de la valeur** : -$53,735 en stops vs +$73,075 en timeouts (Combo A). Les stops à -15% mangent le profit. Le edge est dans le hold-to-timeout, pas dans le stop.
2. **S2 alt_crash perd gros dans le combo A** (-$5,066) car les pertes arrivent quand le capital est élevé.
3. **2026 Q1 est négatif** (-$520 à -$3,400 selon combo). Pas de tendance positive actuelle.
4. **Les SHORT signals (S4) sont actifs 24% du temps** — presque "toujours short". Monte Carlo dit que le timing compte (z=2.95), mais c'est le signal le plus susceptible de mourir dans un bull puissant.

### Par période (Combo C, sizing 15%)

| Période | P&L | Contexte |
|---|---|---|
| 2023-H2 | -$111 | Bear, S4 SHORT seul, peu de data |
| 2024-H1 | +$882 | Bull début, S1+S2 dominent |
| 2024-H2 | +$3,680 | Bull fort, TOUT marche |
| 2025-H1 | +$3,618 | Bear, S4 SHORT domine |
| 2025-H2 | +$133 | Latéral, marginal |
| 2026-Q1 | -$520 | Latéral/bear, perd |

### Recommandation : 3 stratégies (sans S3)

**S1 (btc_rip)** + **S2 (alt_crash)** + **S4 (vol_short)** = les 3 stratégies validées non-redondantes.

### Configuration finale (v9.0.0, corrigée après code review)

- S1 : btc_30d > 2000 bps → LONG, hold **3j** (corrigé : hold=7j était optimisé sur données test)
- S2 : alt_index_7d < -1000 bps → LONG, hold 3j
- S4 : vol_ratio < 1.0 AND range_pct < 200 bps → SHORT, hold 3j
- Max 6 positions, max 4 même direction
- **Sizing : compounding 15% du capital, z-weighted** (S1 ×1.6, S2 ×1.0, S4 ×0.74)
- **Stop loss : -25% catastrophe uniquement** (pas de stop normal, le backtest montre que ça détruit la valeur)
- Coût : 12 bps (7 taker + 3 slippage + 2 funding)

À $1,000 de capital : S1=$241, S2=$150, S4=$111 par trade.

### Corrections issues du code review (24 mars 2026)

1. **Hold S1 ramené à 3j** — le hold=7j était optimisé sur données test (data leakage)
2. **Stop catastrophe -25%** ajouté — pas de stop normal mais protection flash crash
3. **Z-scores corrigés** : de z=7.41 à **z=5.39** (toujours fortement significatif)
4. **P&L corrigé sans compounding** : $+3,876 → **$+3,704** (hold=18 pour tout)

### Backtest compounding 15% (chiffres vérifiés)

$1,000 → **$9,035** sur 27 mois (1405 trades, 54% win, max DD -30.8%)

| Période | Contexte | P&L | Capital moyen | Rendement réel |
|---|---|---|---|---|
| 2023-H2 | Bear, peu de data | -$68 | $1,000 | -7% |
| 2024-H1 | Bull début | +$1,018 | $1,000 | +102% |
| 2024-H2 | **Bull fort** | **+$5,588** | $2,000 | **+279%** |
| 2025 | Bear/latéral | +$1,140 | $7,500 | **+15%** |
| 2026-Q1 | Latéral | +$357 | $8,000 | +18% ann. |

**⚠ Le résultat est gonflé par 3 mois exceptionnels de bull 2024 :**
- Nov 2024 : +$3,040 (S1 btc_rip, BTC passait +20% sur 30j)
- Déc 2024 : +$1,847
- Jan 2025 : +$1,438

Sans ces 3 mois, le reste = ~+$1,500 sur 24 mois = ~+$63/mois.

### Estimation réaliste annuelle sur $1,000

- **En bull market** : +100-300%/an (le compounding accélère fort)
- **En bear/latéral (le cas normal)** : **+15-50%/an**
- **Pire drawdown** : -30% du capital
- **Pire mois** : -$1,260 (avril 2025)
- **Projection moyenne honnête : +$750-1,500/an (+15-50%/an hors bull exceptionnel)**

Le +276%/an "moyen" sur 27 mois est trompeur — porté par 3 mois de bull. En conditions normales, tabler sur +15-50%/an.

### Ce qui ferait échouer

1. Marché latéral prolongé (pas de gros moves BTC, pas de dips alts)
2. Changement de régime alt/BTC (alts décorrélés de BTC)
3. Liquidité Hyperliquid → slippage > modèle
4. Trade saturé → tout le monde achète les dips → dips ne rebondissent plus
5. Bull violent continu → S4 SHORT perd, S1+S2 ne triggent pas

---

## Exploration 2 — Pistes supplémentaires testées (24 mars 2026)

### Cross-sectional momentum — INVALIDÉ
- Acheter les top 3 performers 7d, shorter les bottom 3, rebalancer tous les 2j
- P&L brut: +$1,496 (1553 trades, win 51%)
- **Monte Carlo: z = 0.68 → PAS SIGNIFICATIF**
- Le "edge" était un biais directionnel (plus de longs en bull, plus de shorts en bear)
- Le timing ne compte pas — random timing fait aussi bien

### Cross-sectional mean-reversion — PIRE STRATÉGIE
- Acheter les pires performers, shorter les meilleurs → perd systématiquement
- MeanRev 14d top5 : -$4,402 (pire config testée sur 84 configs)
- Confirmation : en crypto, le momentum domine la mean-reversion au niveau token

### Effets calendaires — NON FIABLE
- Mardi +36 bps (t=9.0), Dimanche -33 bps (t=-7.6) → statistiquement significatif
- Combo LONG Tue+Fri / SHORT Sun+Mon : z=2.84 MAIS **train perd** ($-39)
- Le signal n'existe qu'en 2025 (bear market) → biais temporel, pas exploitable

### Machine Learning (Random Forest + Gradient Boosting)
- Walk-forward validation : train 12 mois, test 3 mois, roll
- **Feature importance confirme nos choix** :
  1. btc_30d (17%) — notre S1
  2. dispersion_7d (13%) — testé, marginal seul
  3. alt_index_7d (11%) — notre S2
  4. dow (10%) — calendrier, pas fiable
  5. btc_eth_spread (8%) — testé, rien de nouveau
  6. btc_7d (7%) — notre S3
  7. vol_ratio (4%) — notre S4
- ML ne trouve rien de mieux que nos règles simples
- Les arbres de décision convergent vers les mêmes features que notre scan exhaustif

### Dispersion comme signal — MARGINAL
- disp_7d < 500 → LONG : z=2.29, +$890 (significatif mais faible)
- Comme filtre sur S2/S4 : **dégrade** les résultats (moins de trades, pas meilleur avg)
- Redondant avec alt_index_7d (quand la dispersion est basse et l'index baisse, c'est un crash corrélé)

### BTC-ETH spread — PAS DE SIGNAL
- Aucune configuration profitable sur train ET test
- Feature importante pour le ML mais pas exploitable comme règle simple

### Volatility regimes — PAS D'AMÉLIORATION
- S1, S2, S4 fonctionnent dans les DEUX régimes (low et high vol)
- Pas besoin de filtrer par régime de vol

### Algo génétique — OVERFIT
- Meilleur individu : +$2,376 train → -$1,641 test
- N'a trouvé aucune règle survivant la validation que le scan exhaustif n'avait pas déjà trouvée

### Funding rate Hyperliquid — DONNÉES INSUFFISANTES
- L'API `fundingHistory` existe et retourne 500 records max (~166 jours)
- Pas assez d'historique pour un backtest 3 ans significatif
- Les taux sont très faibles sur HL (mean ~0.001 bps vs ~1.6 bps sur Binance)

### Conclusion Exploration 2

Le ML, l'algo génétique, le momentum cross-sectionnel, le calendrier, la dispersion et le funding n'ajoutent aucun edge exploitable. Les stratégies S1-S4 restent les meilleures du scan exhaustif.

---

## Exploration 3 — Programmation génétique + Divergence sectorielle (24 mars 2026)

### Programmation génétique (GP) — OVERFIT

Évolution de **formules mathématiques libres** (arbres d'expressions) sur les 22 features.
Population: 200 individus, 60 générations, opérateurs: +, -, ×, ÷, min, max, abs, sqrt, inv.

Meilleure formule trouvée : `(btc_7d min 753) × (alt_index_7d min dispersion_7d)`
- Train: +$4,766 (584 trades)
- **Test: -$253 ✗**

Sur 20 formules testées, **aucune ne survit la validation train/test**. Toutes overfit sur 2024.

Conclusion : les formules complexes ne font pas mieux que les règles simples. La GP converge vers les mêmes features (btc_7d, alt_index_7d, dispersion_7d) mais les combine de façon instable.

### S5 Divergence sectorielle — VALIDÉ ✓

**Idée originale** : les tokens d'un même secteur (L1, DeFi, Gaming, Infra, Meme) sont corrélés. Quand un token diverge de son secteur avec du volume, le mouvement continue.

Secteurs :
- L1 : SOL, AVAX, SUI, APT, NEAR, SEI
- DeFi : AAVE, MKR, CRV, SNX, PENDLE, COMP, DYDX, LDO
- Gaming : GALA, IMX, SAND
- Infra : LINK, PYTH, STX, INJ, ARB, OP
- Meme : DOGE, WLD, BLUR, MINA

**Méthode** : quand un token diverge de >10% vs la moyenne de son secteur ET le volume z-score > 1.0 → suivre la divergence (LONG si diverge vers le haut, SHORT si vers le bas). Hold 48h.

**Résultats** (360 configs testées) :
- **FOLLOW fonctionne** : +$2,022 (830 trades, z=3.67, 16/27 mois gagnants) ✓
- **FADE ne fonctionne pas** : +$470 (marginal)
- L'hypothèse initiale "low vol = fade, high vol = follow" est partiellement confirmée : seul le FOLLOW survit.

**Par secteur** :
| Secteur | P&L | Trades |
|---|---|---|
| Meme | +$875 | 136 |
| DeFi | +$609 | 309 |
| L1 | +$458 | 181 |
| Infra | +$156 | 175 |
| Gaming | -$76 | 29 |

**Pourquoi c'est intéressant** :
- Indépendant de S1/S2/S4 (utilise les relations intra-secteur, pas BTC ni alt-index)
- Pertes mensuelles très faibles (max -$71 vs -$1,260 pour S1+S2+S4)
- 59% de mois gagnants (vs 54% pour S1+S2+S4)
- Approche originale (aucun bot crypto n'utilise cette logique)

**Config S5** : div > 1000 bps, vol_z > 1.0, hold 48h, follow only, z=3.67

### Exploration 3 — Liquidation, macro, offsets (24 mars 2026)

**S6 Liquidation bounce — VALIDÉ ✓**
- Quand une bougie 4h a un range 4× plus grand que la moyenne avec 30%+ de mèche → acheter le rebond
- **+$4,043** (447 trades), **z = 8.04** (le plus élevé de toute la recherche)
- Train: +$2,389, Test: +$1,654 ✓
- Avg +362 bps/trade — le signal le plus rentable par trade
- Mécanisme clair : les cascades de liquidation créent un excès → le prix rebondit

**DXY (dollar index) → filtre S4**
- DXY_7d > +1% → SHORT crypto : z=9.78, +$10,010 (1,801 trades)
- Plutôt qu'un signal autonome, utilisé comme **filtre** sur S4 :
  - DXY monte → S4 SHORT activé (dollar fort = bad pour crypto)
  - DXY baisse → S4 SHORT désactivé (pas de shorts en environnement dollar faible)
- Réduit le risque de S4 en bull market

**S&P 500** — Résultats suspects
- Les meilleurs signaux ont 10,000+ trades (= "presque toujours actif")
- SP500_30d < 200 → SHORT : z=6.97 mais probablement biais "always short"
- Non retenu comme signal autonome

**Candle offsets** — Marginal
- Décaler de 4-24h ne change presque rien pour S1/S2/S4
- Pas retenu

### Backtest combiné v9.2.0 → v9.3.0 (portfolio complet)

Backtest avec les 5 signaux (S1+S2+S4+S5+S6) + DXY filter, position limits, compounding 15%.

**Découverte critique : S6 perd dans le portfolio** malgré z=8.04 en isolation.
Le backtest standalone avait un backtester simplifié sans position limits correctes.

| Config | P&L | Trades | Mois gagnants | Max DD | S6 P&L |
|---|---|---|---|---|---|
| 6 pos | +$1,262 | 1049 | 24/36 | -$627 | **-$627** |
| 8 pos | +$2,210 | 1195 | 24/36 | -$1,299 | **-$735** |
| 10 pos | +$3,805 | 1328 | 25/36 | -$2,465 | **-$1,552** |

**Slot utilization** : avg 2.5/6 (42%), full seulement 7% du temps, vide 26%.
→ Le problème n'est pas les slots, c'est la fréquence des signaux.

**Résultat par signal (config 6 pos) :**
- S1 (BTC rip) : +$601 (116t) ✓
- S2 (alt crash) : +$250 (294t) ✓
- S4 (vol short + DXY) : +$535 (58t) ✓
- S5 (sector) : +$503 (459t) ✓
- S6 (liquidation) : **-$627** (122t) ✗ → **RETIRÉ**

**Bot v9.3.0 : S1+S2+S4+S5 + DXY filter, sans S6.**
Sans levier : +$1,262 sur 36 mois = +$35/mois (+3.5%/mois). Max drawdown : -$627.

### Optimisation des paramètres (boost)

Sweep systématique : levier (1-3x), sizing (10-40%), hold (×0.33-×2), positions (4-15).

| Paramètre | Impact | Résultat | DD |
|---|---|---|---|
| Levier 2x | **×6 sur P&L** | $1,000 → $17,768 | -54% |
| Levier 3x | Trop de risque | $1,000 → $4,898 | -68% |
| Sizing 25% | ×2.2 | $1,000 → $7,377 | -52% |
| 10 positions | ×2 | $1,000 → $6,739 | -41% |
| Hold ×0.5 | Moins bien | $1,000 → $3,181 | -30% |
| 2x + 25% | ×11 (dangereux) | $1,000 → $33,822 | **-77%** |
| ALL MAX (3x+30%+10pos) | **RUINE** | $1,000 → $0 | -110% |

**Levier 2x choisi** : meilleur rapport rendement/risque. Le compounding amplifie l'effet du levier.

### Bot v10.0.0 final — Configuration

| Paramètre | Valeur |
|---|---|
| Stratégies | S1 (BTC rip) + S2 (alt crash) + S4 (vol short + DXY) + S5 (sector) |
| Levier | **2x** |
| Sizing | 15% capital, z-weighted |
| Hold | 72h (S1/S2/S4), 48h (S5) |
| Stop | -25% catastrophe |
| Max positions | 6 / 4 même direction |
| DXY filter | S4 actif uniquement quand dollar monte > +1%/7j |
| Coût simulé | 14 bps (12 + 2 funding extra pour levier) |

**Backtest combiné v10.0.0** : $1,000 → **$17,768** (+$16,768) sur 36 mois.
- **~180%/an** composé
- 20/35 mois gagnants (57%)
- Max drawdown : **-54%**
- Train: +$5,336 | Test: +$10,384

**⚠ Avertissements** :
- DD -54% = tu peux perdre la moitié de ton capital avant que ça remonte
- Le résultat est porté par 2024 (bull) — Nov 2024 seul = ~+$5,000
- En 2025 bear/latéral : les gains sont modestes avec des mois à -$1,000+
- Paper trading recommandé pendant 1-2 mois avant argent réel
- Le levier 2x multiplie les gains ET les pertes

---

## Bilan final — Toutes stratégies testées

| Stratégie | Exchange | Période | P&L | %/mois | Validé ? | France ? |
|---|---|---|---|---|---|---|
| OI divergence | Binance | 27j | -$202 | - | ✗ | Non |
| Extreme reversion 1h | Binance | 1 an | -$382 | -3.1% | ✗ | Non |
| Rev + Vol z>2 | Binance | 1 an | +$159 | +1.3% | ⚠ | Non |
| Carry XMR 3x | Binance | 1 an | +$533 | +4.1% | ✓ | **Non** (AMF) |
| Carry dynamique | Hyperliquid | 90j | +$66 | +0.5% | ✗ | Oui |
| Pairs trading | Hyperliquid | 6 mois | -$53 | - | ✗ | Oui |
| Token unlocks | Hyperliquid | 1 an | biais bear | - | ✗ | Oui |
| Stablecoin flow | on-chain | 1 an | pas prédictif | - | ✗ | - |
| Whale tracking | on-chain | 90j | 0 signal | - | ✗ | - |
| Cycle 8h | Binance | 1 an | perd | - | ✗ | Non |
| Multi-Day Reversal | Hyperliquid | 14 mois | +$1,048 | +8.8% | ⚠ Bear only | Oui |
| Multi-Day Reversal | Hyperliquid | 3 ans | -$512 | -1.4% | ✗ Invalidé | Oui |
| Regime adaptive | Hyperliquid | 3 ans | perd | - | ✗ | Oui |
| Algo S4+S1 (BTC dip+rip) | Hyperliquid | 27 mois | +$753-1,066 | +3-4% | ⚠ z=4.09 | Oui |
| **S1 btc_rip (30d>20%)** | **Hyperliquid** | **3 ans** | **+$2,195** | **rare** | **✓✓ z=6.42** | **Oui** |
| **S2 alt_crash (idx<-10%)** | **Hyperliquid** | **3 ans** | **+$1,706** | **~$68** | **✓✓ z=4.00** | **Oui** |
| **S3 btc_dip+alt** | **Hyperliquid** | **3 ans** | **+$1,435** | **~$62** | **✓ z=3.58** | **Oui** |
| **S4 vol_short** | **Hyperliquid** | **3 ans** | **+$2,609** | **~$75** | **✓ z=2.95** | **Oui** |
| **Portfolio S1+S2+S4** | **Hyperliquid** | **35 mois** | **+$7,662** | **+$219** | **✓ z=2.18-2.85** | **Oui** |
| Momentum cross-sect 7d | Hyperliquid | 3 ans | +$293 | - | ✗ z=0.68 | Oui |
| Mean-reversion cross-sect | Hyperliquid | 3 ans | -$4,402 | - | ✗ Pire strat | Oui |
| Calendar (Tue L/Sun S) | Hyperliquid | 3 ans | +$918 | ~$26 | ✗ train perd | Oui |
| Dispersion < 500 → LONG | Hyperliquid | 3 ans | +$890 | ~$25 | ⚠ z=2.29, redondant S2 | Oui |
| Random Forest / GBT | Hyperliquid | 3 ans | confirme features | - | ML = nos règles | Oui |
| Programmation génétique | Hyperliquid | 3 ans | overfit | - | ✗ test perd | Oui |
| **S5 sector divergence** | **Hyperliquid** | **27 mois** | **+$2,022** | **~$75** | **✓ z=3.67** | **Oui** |
| ~~S6 liquidation bounce~~ | Hyperliquid | 27 mois | +$4,043 solo | - | **✗ PERD en portfolio** (-$627 à -$1,552) | Oui |
| DXY → SHORT crypto | Macro+HL | 27 mois | +$10,010 | - | ✓ z=9.78, filtre S4 | Oui |
| S&P 500 signals | Macro+HL | 27 mois | suspect | - | ⚠ trop de trades | Oui |
| Candle offsets | Hyperliquid | 27 mois | marginal | - | ✗ | Oui |
| Bot v9.3.0 (S1+S2+S4+S5+DXY) 1x | Hyperliquid | 36 mois | +$1,262 | ~$35 | ✓ 24/36 mois | Oui |
| **Bot v10.0.0 (idem + levier 2x)** | **Hyperliquid** | **36 mois** | **+$16,768** | **~$480** | **✓ 20/35 mois, DD-54%** | **Oui** |
