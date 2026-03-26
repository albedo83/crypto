# Multi-Signal Bot v10.3.4

Bot de trading automatique sur 28 altcoins Hyperliquid. Paper trading. Un seul fichier Python, pas de base de donnees.

---

## En une phrase

Le bot achete les crashs (S2, S8), suit le momentum BTC→alts (S1), shorte les marches calmes quand le dollar monte (S4), et suit les breakouts sectoriels (S5). Il logue tout ce qu'il voit (OI, funding, premium, crowding, trajectoire) pour pouvoir s'ameliorer plus tard sans avoir triche sur les donnees.

---

## Les 5 signaux

### S1 — BTC explose (+20% sur 30 jours)

**Quoi** : si BTC a monte de +20% sur un mois, acheter des alts.
**Pourquoi** : quand BTC pump fort, les alts suivent avec du retard. On achete ce retard.
**Type** : continuation / momentum retarde.
**Frequence** : rare, quelques fois par an.
**Mise** : $241 (2eme plus grosse). **Hold** : 72h. **Stop** : -25% leveraged.
**z-score** : 6.42. **Walk-forward** : 3/4 (75%).
**Backtest** : +$1,480 sur 208 trades.

### S2 — Les alts crashent (-10% en 7 jours)

**Quoi** : si la moyenne des 28 alts a baisse de plus de -10% en 7 jours, acheter.
**Pourquoi** : apres un crash generalise, les alts rebondissent. On achete la panique.
**Type** : contrarien / mean-reversion.
**Frequence** : quelques fois par mois.
**Mise** : $150. **Hold** : 72h. **Stop** : -25% leveraged.
**z-score** : 4.00. **Walk-forward** : 5/9 (56%) — **le signal le plus fragile**. Perd presque 1 trimestre sur 2, mais gagne gros quand il gagne. A surveiller en priorite.
**Backtest** : +$1,706 sur 552 trades.
**Note** : fonctionne en bull ET en bear. Le regime gating (activer seulement en bull) a ete teste et degrade le signal.

### S4 — Calme plat + dollar fort

**Quoi** : si la volatilite d'un alt est basse, la bougie est petite, et le dollar monte → shorter.
**Pourquoi** : en crypto, quand c'est calme et que le dollar se renforce, les alts derivent vers le bas.
**Type** : contrarien / derive.
**Frequence** : variable, depend du dollar.
**Mise** : $111 (la plus petite). **Hold** : 72h. **Stop** : -25% leveraged.
**z-score** : 2.95. **Walk-forward** : 7/10 (70%).
**Backtest** : +$2,609 sur 1,185 trades.
**Conditions exactes** : `vol_ratio < 1.0 AND range_pct < 200 bps AND DXY_7d > +100 bps`.
**Le filtre DXY est critique** : sans lui, S4 shorte en bull market et perd. Seul signal SHORT du bot (378 variantes SHORT testees, aucune autre ne depasse z > 2.0).
**DXY source** : Yahoo Finance, cache local. Frais < 6h = normal. Stale 6-48h = S4 actif avec donnees anciennes (bandeau jaune). Expire > 48h = S4 desactive (bandeau rouge).

### S5 — Un token casse de son secteur

**Quoi** : si un token diverge de +10% par rapport a la moyenne de son secteur, avec du volume anormal → suivre le mouvement.
**Pourquoi** : quand un token casse de son secteur avec du volume, le mouvement continue. Le "fade" (jouer contre) a ete teste et ne marche pas.
**Type** : continuation / breakout sectoriel.
**Frequence** : 10-20 fois par mois.
**Mise** : $138. **Hold** : 48h (plus court, les rotations sont rapides). **Stop** : -25% leveraged.
**z-score** : 3.67. **Walk-forward** : ~50%.
**Backtest** : +$2,022 sur 467 trades.

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
**Pourquoi** : quand tout tombe en meme temps, c'est un flush de liquidation force. Les traders en levier se font liquider en cascade. Le prix passe en dessous de sa valeur. Le rebond est violent.
**Type** : contrarien / capitulation.
**Frequence** : rare, ~1/mois en portfolio.
**Mise** : $262 (la plus grosse). **Hold** : 60h. **Stop** : **-15%** leveraged (plus serre que les autres, backteste avec ce stop).
**z-score** : 6.99 (le plus eleve). **Walk-forward** : 8/9 (89%) — **le signal le plus robuste**.
**Backtest** : +$1,984 sur 192 trades. 70% win rate. 16/18 mois gagnants.
**Conditions exactes** : `drawdown < -4000 bps AND vol_z > 1.0 AND ret_24h < -50 bps AND btc_7d < -300 bps`.
**Pire scenario** : 7 pertes consecutives en avril 2024 (crash prolonge), drawdown -$265.
**Risque de liquidite** : S8 achete exactement quand les carnets d'ordres se vident sur un DEX. Le slippage reel peut etre 5-10x plus eleve que les 3 bps simules. En production : ordres limit (maker) pour etre le filet qui attrape les liquidations.

---

## Parametres

| Parametre | Valeur | Pourquoi |
|---|---|---|
| **Levier** | 2x | Sweep 1x-3x : 2x optimal. 3x = ruine par compounding des pertes. |
| **Sizing** | 12% base + 3% bonus (z>4), z-weighted | Plus le signal est fiable, plus la mise est grosse. S8 haircut ×0.8 (liquidite mince). |
| **Compounding** | Oui | Capital = $1000 + P&L cumule. Les mises suivent les gains et les pertes. |
| **Hold** | 72h (S1/S2/S4), 48h (S5), 60h (S8) | Timeout automatique. Stop de profit teste : degrade les resultats. |
| **Stop loss** | -2500 bps (S1/S2/S4/S5), -1500 bps (S8) | -25% leveraged = -12.5% mouvement de prix. S8 plus serre (-15% = -7.5%). |
| **Frais simules** | 12 bps × 2 = 24 bps/trade | 7 taker + 3 slippage + 2 funding. Conservateur. |
| **Cooldown** | 24h par token apres exit | Evite de re-entrer immediatement. |

### Sizing par signal ($1,000 de capital)

| Signal | z-score | Mise | Logique |
|---|---|---|---|
| S8 | 6.99 | $262 | Le plus fiable × haircut liquidite 0.8 |
| S1 | 6.42 | $241 | Tres fiable, rare |
| S2 | 4.00 | $150 | Bon mais borderline en walk-forward |
| S5 | 3.67 | $138 | Solide |
| S4 | 2.95 | $111 | Le moins fiable, mise la plus petite |

Formule : `size = capital × (12% + 3% si z>4) × clamp(z/4, 0.5, 2.0) × haircut`.

### Ce qui a ete teste et rejete

| Idee | Resultat | Pourquoi ca ne marche pas |
|---|---|---|
| Stop loss serre (-7%) | Detruit la valeur | Les gagnants passent souvent par un drawdown temporaire |
| Trailing stop | Pire que timeout | Coupe les gagnants trop tot |
| Signal exit | Perd de l'argent | Le signal s'inverse avant le rebond |
| Sizing ATR | P&L -27% | La volatilite est le carburant, pas le risque |
| Regime gating | Degrade tout | S2 marche en bull ET en bear |
| HMM / Markov | Meme probleme | Reduire l'exposition en crash = couper S2/S8 quand ils doivent tirer |
| 3x leverage | Ruine | Compounding des pertes |
| 10 positions | Egal | Pas assez de signaux simultanement |
| Smart priority | Egal | Aucune amelioration |
| OI comme filtre (actif) | Non teste en live | Pas de donnees historiques Hyperliquid. Teste sur Binance : degrade. Observation en cours. |
| Scoring de qualite de setup | Pas encore | Le crowding score est logue, pas utilise. Besoin de 50+ trades pour evaluer. |

---

## Protections

| Protection | Seuil | Ce que ca empeche |
|---|---|---|
| **Max 6 positions** | Absolu | Surexposition |
| **Max 4 meme direction** | 4 LONG ou 4 SHORT | Pari directionnel total |
| **Max 2 par secteur** | 2 par groupe (L1, DeFi, Gaming, Infra, Meme) | 4 LONG DeFi deguises en diversification |
| **Exposition 90%** | `margin + new_size <= capital × 0.90` | Toujours 10% de cash |
| **Stop loss** | -25% leveraged (S8: -15%) | Crash extreme |
| **Kill-switch** | P&L cumule < -$300 → auto-pause | Perte de 30% du capital |
| **Loss streak** | 3 pertes consecutives → sizing /2 pendant 24h | Serie noire |
| **Signal quarantine** | Win rate < 20% sur 10 trades → signal coupe | Signal mort qui continue a manger du capital |
| **Cooldown** | 24h par token apres exit | Re-entree impulsive |
| **Mode degrade DXY** | Stale 6-48h (jaune), expire >48h (rouge, S4 off) | Yahoo tombe, S4 disparait silencieusement |

---

## Observabilite (ce que le bot logue pour le futur)

Le bot logue beaucoup plus que ce qu'il utilise pour ses decisions. C'est delibere : on collecte maintenant, on analyse plus tard, on ne filtre jamais sans preuve.

### Dans chaque trade (signal_info)

Chaque entree enregistre :
- **OI delta 1h** : variation de l'open interest sur la derniere heure (% change)
- **Crowding score** : score 0-100 de surchauffe du levier (OI delta + funding + premium + vol_z)
- **Stress breadth** : `str=X/Y` — X tokens en stress global (vol_z>1.5 + drawdown<-15%), Y dans le meme secteur
- **Signal complet** : toutes les features du signal (drawdown, vol_z, BTC context, etc.)

### Trajectoire par trade

Chaque position enregistre son parcours heure par heure :
- `trajectory = [(heures_depuis_entree, unrealized_bps), ...]`
- MAE (pire moment) et MFE (meilleur moment) mis a jour toutes les 60s
- A la cloture, trajectoire ecrite dans `reversal_trajectories.csv`
- Permet de repondre a : "les bons S8 rebondissent-ils dans les 8-12h ?"

### Snapshots de marche horaires

Fichier `reversal_market.csv` — 28 lignes par heure :
- timestamp, symbol, price, OI, oi_delta_1h, funding (ppm), premium (ppm), crowding score, vol_z
- ~15 MB/an. Survit aux restarts. C'est la brique manquante pour backtester OI plus tard.

### Signal drift

`/api/state` expose `signal_drift` : pour chaque signal, les stats rolling sur les 20 derniers trades (win rate, avg bps, P&L total). Detecte la degradation silencieuse.

### Signaux refuses

Chaque signal valide mais refuse par le portfolio (quota, cooldown, sector cap, capital) est logue avec la raison. Permet de mesurer le cout d'opportunite des limites.

### Protocole OI pre-enregistre

Le protocole d'evaluation OI est verrouille AVANT les donnees (voir `memory/project_oi_filter_plan.md`) :
- **Buckets** : OI_1h <= 0% (purge) vs > 0% (accumulation)
- **Metrique** : net bps moyen par groupe
- **Seuils** : >50 bps d'ecart → filtre actif. 25-50 → moduler sizing. <25 → rien.
- **Echantillon** : 30 trades S2, 10 trades S8, minimum 10 par groupe
- **Interdit** : changer les regles apres avoir vu les resultats

---

## Recherche

### Methode de validation (4 filtres)

Chaque signal doit passer les 4 :
1. **Train/test split** — trouve sur 2024, valide sur 2025-2026. Profitable des deux cotes.
2. **Monte Carlo** — z-score > 2.0 vs timing aleatoire (meme nombre de trades, meme direction).
3. **Portfolio** — ajoute aux signaux existants sans degrader le total.
4. **Walk-forward** — 12 mois train, 3 mois test, avance de 3 mois. Profitable > 50% des fenetres.

### Walk-forward par signal

| Signal | Fenetres gagnantes | Stabilite |
|---|---|---|
| S8 | 8/9 (89%) | Tres stable |
| S1 | 3/4 (75%) | Stable (rare, peu de fenetres) |
| S4 | 7/10 (70%) | Stable |
| S2 | 5/9 (56%) | Borderline |
| S5 | ~50% | Difficile a evaluer (calcul sectoriel) |

Test Leave-5-tokens-out : aucun signal ne depend de tokens specifiques.

### Ce qui a ete teste et elimine (1500+ regles)

**1ere vague** : momentum, mean-reversion cross-sectionnelle, calendar, carry/funding, token unlocks, pairs trading, on-chain, programmation genetique (overfit), ML walk-forward (confirme les features, pas de nouveau signal).

**2eme vague** : S7 BTC-Alt recouple, S9 exhaustion, S10 vol compression, BTC-ETH spread, dispersion warning — tous echouent train/test. 8 strategies SHORT (378 variantes) — aucune z > 2.0. Regime gating — degrade tout. Liquidation comme filtre — pas d'amelioration.

### Backtest (32 mois)

| Annee | Contexte | Performance | Capital |
|---|---|---|---|
| 2023 (5 mois) | Bear | +8% | $1,000 → $1,081 |
| 2024 | Bull | +528% | $1,081 → $6,786 |
| 2025 | Bear/lateral | +145% | $6,786 → $16,646 |
| 2026 (3 mois) | Lateral | -33% | $16,646 → $11,214 |
| **Total** | **32 mois** | **+1,021%** | **$1,000 → $11,214** |

20/32 mois gagnants (63%). Drawdown max -54%. Bot inactif ~26% du temps.

---

## Estimations (sur $1,000)

### Backtest (fait historique)

$1,000 → ~$7,000-$9,000 sur 32 mois avec sizing v10.3.1. Inclut une periode exceptionnelle (2024, +528%). Rien ne garantit que ca se reproduira.

### Projection prudente

Backtest degrade de ~50% (data snooping, slippage, incertitude) : **+50% a +100%/an** en conditions normales. Sur $1,000 : +$500 a +$1,000/an. C'est une estimation, pas une promesse.

### Scenarios extremes

| Scenario | P&L | Quand |
|---|---|---|
| Bull exceptionnel | +$2,000 a +$5,000 | BTC +100%, S1 se declenche |
| Lateral prolonge | -$100 a +$200 | Bot dort, frais grignottent |
| Crash qui ne rebondit pas | -$200 a -$500 | S2/S8 achetent, les dips continuent |

### Ce qui n'est pas dans les chiffres

- Slippage reel S8 (20-50 bps possibles vs 3 simules)
- Frais reels Hyperliquid (meilleurs que simule, maker rebates)
- Data snooping residuel (1500+ regles testees = faux positifs possibles)
- S2 est le signal le plus fragile (5/9 walk-forward)

---

## Risques

**Perte en capital** : drawdown -54% observe. $1,000 peut tomber a $460. 37% des mois sont perdants.

**Risque de modele** : les signaux viennent du passe. Le marche evolue. Le paper trading valide avant l'argent reel.

**Risque de liquidite S8** : achete quand les carnets se vident. Slippage reel >> simule.

**Risque de plateforme** : Hyperliquid est un DEX sans assurance. Bugs, hacks, perte de fonds possibles.

**Risque technique** : si le serveur tombe, les positions restent ouvertes. Stop loss = dernier filet.

**Scenarios de perte prolongee** :
- Lateral : peu de signaux, frais grignottent.
- Crash qui dure : S2/S8 se font stopper en serie.
- Dollar faible : S4 desactive, bot 100% LONG.

---

## Architecture

```
Hyperliquid REST API (toutes les 60s)
    ├── metaAndAssetCtxs → prix, OI, funding, premium (28 tokens)
    ├── candleSnapshot → bougies 4h (30 tokens, toutes les heures)
    └── Yahoo Finance → DXY (toutes les 6h, cache 48h max)
            │
            ▼
    reversal.py  (~1400 lignes, processus asyncio unique)
    │
    ├── Features (24 calculees par token, 13 utilisees pour les signaux)
    │
    ├── Collecte OI/funding/premium (observation, pas de decision)
    │     Crowding score 0-100 (OI delta + funding + premium + vol_z)
    │     Stress breadth (combien de tokens en stress simultane)
    │
    ├── 5 signaux
    │     S1: btc_30d > +20%             → LONG 72h
    │     S2: alt_index < -10%           → LONG 72h
    │     S4: vol_ratio<1 + range<2% + DXY>+1% → SHORT 72h
    │     S5: sector div>10% + vol_z>1   → FOLLOW 48h
    │     S8: dd<-40% + vol_z>1 + ret_24h<-0.5% + btc_7d<-3% → LONG 60h
    │
    ├── Position manager
    │     6 max / 4 dir / 2 secteur / 90% expo
    │     Stop -25% (S8: -15%) / Kill-switch -$300 / Streak 3→/2
    │     Quarantine: win rate<20% → signal off
    │     MAE/MFE + trajectoire horaire par position
    │
    ├── Logging
    │     CSV trades (avec signal_info: OI, crowding, stress breadth)
    │     CSV trajectoires (unrealized bps heure par heure)
    │     CSV market (snapshot horaire 28 tokens: OI, funding, premium)
    │     Signaux refuses logges avec raison (SKIP)
    │     Signal drift rolling par signal
    │
    ├── Persistence
    │     JSON atomic (state + positions + MAE/MFE + trajectoire)
    │     Survit aux restarts (paused, loss_streak, cooldowns aussi)
    │
    └── Dashboard (:8097)
          Pulse vert / countdown scan / crowding scores
          OI delta par token / bandeau degrade DXY
```

### Cycle de scan (toutes les heures)

1. Fetch prix + OI + funding + premium (metaAndAssetCtxs)
2. Fetch bougies 4h (30 tokens)
3. Refresh features + OI summary
4. Check exits (timeout, stop loss, MAE/MFE update)
5. Scan signaux → quarantine → tri z-score → entree (log OI + crowding + stress)
6. Save state (JSON atomic)
7. Market snapshot (CSV 28 lignes)

Entre les scans : prix/OI/funding toutes les 60s, exits verifies, MAE/MFE mis a jour.

### Fichiers

| Fichier | Role |
|---|---|
| `analysis/reversal.py` | Le bot complet (~1400 lignes) |
| `analysis/reversal.html` | Dashboard web |
| `analysis/output/reversal_state.json` | Etat (positions, P&L, paused, loss streak, MAE/MFE, trajectoires) |
| `analysis/output/reversal_trades.csv` | Trades clotures (signal_info, MAE, MFE) |
| `analysis/output/reversal_trajectories.csv` | Parcours horaire de chaque trade |
| `analysis/output/reversal_market.csv` | Snapshots horaires marche (OI, funding, premium, crowding) |
| `analysis/output/reversal_v10.log` | Logs (entrees, sorties, SKIP, quarantine, kill-switch) |
| `analysis/output/pairs_data/` | Cache bougies + DXY |

### Deploiement

Le bot tient en **2 fichiers** + 4 dependances pip :

```bash
mkdir -p analysis/output/pairs_data
cp reversal.py analysis/
cp reversal.html analysis/
touch analysis/__init__.py
python3 -m venv .venv
.venv/bin/pip install numpy orjson uvicorn fastapi
nohup .venv/bin/python3 -m analysis.reversal > analysis/output/reversal_v10.log 2>&1 &
```

Pour dupliquer : copier reversal.py, changer `CAPITAL_USDT`, `TRADE_SYMBOLS`, `WEB_PORT`. Instances independantes.

---

## Plan de production

### Phase 1 — Paper trading (en cours)

Mesure en occurrences, pas en duree :

| Signal | Trades minimum | Duree estimee |
|---|---|---|
| S1 | Inestimable (rare) | Accepter l'incertitude |
| S2 | 15+ | 2-3 mois |
| S4 | 30+ | 3-6 mois |
| S5 | 30+ | 2-3 mois |
| S8 | 5+ | 5+ mois |

**Critere** : 3 mois ET 50 trades cumules, le plus tard des deux.

### Phase 2 — Reel

Capital : $100-500. Pre-requis :
- **Execution** : maker first TTL 30s, fallback taker. S2/S8 : limit -0.3% sous mid, TTL 60s. Partial fills >70%.
- **Reconciliation** : comparer state.json vs clearinghouseState Hyperliquid a chaque scan.
- **Persistence** : SQLite au lieu de JSON.
- **Alertes** : Telegram a chaque entry/exit/erreur.
- **Metriques** : fill rate, slippage par signal, MAE/MFE reel, temps de fill.

### Phase 3 — Scaling

$500 → $1,000 → $2,000 → $5,000. Minimum 2 mois coherents par palier. Ne jamais mettre plus qu'on peut perdre entierement.
