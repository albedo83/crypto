# Projet Crypto — Bilan complet

## Ce qui tourne

**Un seul process** : `analysis/livebot.py` — version courante : voir constante `VERSION` dans le code (affichée dans le dashboard et `/api/state`)

```bash
# Démarrer
nohup .venv/bin/python3 -m analysis.livebot > analysis/output/livebot.log 2>&1 &

# Dashboard
http://51.178.27.240:8095

# Logs
tail -f analysis/output/livebot.log

# Arrêter
fuser -k 8095/tcp

# Trades enregistrés
analysis/output/livebot_trades.csv
```

Le bot se connecte directement à Binance via WebSocket (pas besoin de base de données ni de collecteur intermédiaire). Il surveille **17 symboles** en temps réel et trade quand les conditions sont réunies.

### Services arrêtés (plus nécessaires)

- `crypto-collector` — désactivé (le bot lit Binance directement)
- `crypto-dashboard` — désactivé (le bot a son propre dashboard intégré)
- PostgreSQL — tourne encore (idle) pour consulter les anciennes analyses si besoin

---

## La stratégie

### Le signal : OI Divergence

**Open Interest (OI)** = le total des positions ouvertes sur le marché.

Quand le prix monte MAIS que l'OI baisse → les gens ferment leurs positions, le mouvement est faible → **le prix va redescendre** (on short).

Quand le prix baisse MAIS que l'OI monte → les shorts ouvrent, le mouvement est faible → **le prix va remonter** (on long).

C'est un signal de "qualité du mouvement". On trade contre les mouvements faibles.

### Les filtres

1. **Session** : on trade pendant Asia (0h-8h UTC), US (14h-21h UTC) et Overnight (21h-0h UTC). La session européenne (8h-14h) est exclue — le signal s'inverse pendant cette session (vérifié par backtest). L'overnight précède le settlement funding de 00h UTC = même dynamique que l'Asia.

2. **Funding** : quand le taux de funding est extrême (> 3 bps) et qu'on est à < 2h du settlement (00h, 08h, 16h UTC), le signal est renforcé.

3. **Lead-lag BTC** : quand BTC bouge fortement, les altcoins suivent avec quelques secondes/minutes de retard. Signal additionnel.

4. **Smart money** : Binance publie le ratio long/short des "top traders" (gros comptes) vs la foule. Quand les top traders divergent de la foule → suivre les top traders. rho = 0.43 sur SUI (le signal le plus fort trouvé dans toutes les études). Corrélation avec OI divergence = 0.002 (totalement indépendant).

### Le leverage dynamique (4 signaux)

| Signaux confirmés | Leverage |
|-------------------|----------|
| 1 seul | 1x |
| 2 (ex: OI + smart money) | 1.5x |
| 3 (ex: OI + smart + funding) | 2.5x |
| 4 (tous : OI + smart + funding + BTC) | 3x |

### Les poids du composite

| Signal | Poids | Source |
|--------|-------|--------|
| OI divergence | **35%** | REST polling OI toutes les 60s |
| Smart money | **30%** | REST polling L/S ratio toutes les 60s |
| Funding proximity | **20%** | WebSocket markPrice@1s |
| BTC lead-lag | **15%** | WebSocket bookTicker |

### Les paramètres

| Paramètre | Valeur | Pourquoi |
|-----------|--------|----------|
| Horizon | 2 heures | Les frais (4 bps) sont négligeables face aux mouvements de 20-40 bps |
| Stop loss | -100 bps | Limite les pertes sur un trade |
| Coût simulé | 4 bps roundtrip | Fees maker Binance (0.02% × 2 côtés) |
| Entry threshold | score > 0.25 (Asia) / 0.35 (US) | Adapté au edge par session (v5.0) |
| Hold minimum | 10 min | Pas de reversal check avant 10 min (v4.9) |
| Cooldown | 30 min | Bloque re-entry après exit — anti-whipsaw (v4.9) |
| OI requis | oui | Pas d'entrée sans OI divergence actif (v4.9) |
| Trailing stop | +25 bps / -15 bps | Active au peak, sort si recule de 15 bps (v5.0) |
| Filtre volatilité | vol 3min < 15 bps | Bloque entrées en marché chaotique (v5.0) |
| Funding grab | 30 min avant settlement | Seuil -20% près du funding (v5.0) |

### La gestion du capital

| Paramètre | Valeur |
|-----------|--------|
| Capital initial | **$1000** (fictif, paper trading) |
| Max positions simultanées | **4** |
| Marge par position | **$200-$300** (20-30%, proportionnel au score) |
| Max capital exposé | **90%** ($900) |
| Taille position 1x (score faible) | **$200** |
| Taille position 2.5x (score fort) | **$750** |
| Taille position 3x (max) | **$900** |

Si 10 signaux arrivent en même temps, le bot **trie par force du signal** et prend les 4 meilleurs. Les autres sont ignorés.

### Coûts simulés (v5.3 — réalistes pour le passage en prod)

Le bot simule maintenant **tous les coûts réels** pour que le P&L paper soit proche du P&L prod :

| Coût | Valeur | Explication |
|------|--------|-------------|
| **Fees maker** | 4 bps (0.02% × 2 côtés) | Ordres limit post-only en prod |
| **Slippage** | 1 bps | Écart entre mid price et prix d'exécution réel |
| **Funding** | Variable (toutes les 8h) | Long paie quand rate > 0, short paie quand rate < 0 |
| **Total par trade** | ~5 bps + funding | Avant : 4 bps. Maintenant : réaliste |

Exemple de coûts par taille de position :

| Taille | Fee | Slippage | Total (hors funding) |
|--------|-----|----------|---------------------|
| $250 (1x) | $0.100 | $0.025 | **$0.125** |
| $500 (2x) | $0.200 | $0.050 | **$0.250** |
| $750 (3x) | $0.300 | $0.075 | **$0.375** |

**Funding** : simulé en temps réel. Si le taux de funding est +5 bps et on est LONG $500, on paie $0.25 au settlement. Si on est SHORT, on reçoit $0.25. Visible dans les logs et le dashboard.

### Piège au passage en prod

| Risque | Impact | Prévention |
|--------|--------|------------|
| Ordres market (taker) au lieu de limit | Fees ×2.5 (10 bps au lieu de 4) | Utiliser post-only orders |
| Slippage réel > 1 bps | P&L réduit | Le 1 bps simulé est conservateur |
| Min notional ($5-20 selon symbole) | Ordres rejetés | Vérifier via exchangeInfo |
| Step size / tick size | Ordres rejetés | Arrondir prix/qty aux bons décimales |
| Leverage par défaut (souvent 20x) | Position trop grosse | Régler leverage par symbole avant 1er trade |
| Margin mode (Cross vs Isolated) | Liquidation surprise | Utiliser Isolated margin |

Le P&L est affiché en dollars sur le dashboard : balance courante, gain/perte par trade, frais déduits.

---

## Les 17 symboles

### Pourquoi ces symboles ?

Scan des 544 contrats perpétuels Binance Futures. Critères de sélection (même profil qu'ADA où le signal marche) :

- **Ratio OI/Volume élevé** : beaucoup de positions piégées par rapport au volume → plus de divergences
- **Volume moyen** : pas trop liquide (trop efficient, comme BTC) ni trop illiquide (introuvable)
- **Spread raisonnable** : < 20 bps pour que les frais restent gérables
- **Futures perpétuels USDT-M actifs**

### Liste

**Tier A** (score > 0.8) : ADAUSDT, BNBUSDT, BCHUSDT, TRXUSDT, HYPEUSDT, ZROUSDT, AAVEUSDT, LINKUSDT, SUIUSDT

**Tier B** (score 0.75-0.8) : AVAXUSDT, XRPUSDT, XMRUSDT, XLMUSDT, TONUSDT, LTCUSDT

**Référence** (pas tradés, servent au signal lead-lag) : BTCUSDT, ETHUSDT

### Indépendance des signaux

Corrélation des signaux OI entre symboles : **0.07 (quasi zéro)**. Les signaux se déclenchent à des moments différents → chaque symbole ajoute des trades indépendants → diversification.

---

## Les résultats du backtest (7 jours de données)

### Signal OI Divergence sur ADA (study_06)

| Session | Trades | Net/trade | Win | Total net |
|---------|--------|-----------|-----|-----------|
| Asia (0-8h) | 12 | **+36.4 bps** | 58% | +437 bps |
| US (14-21h) | 12 | **+27.0 bps** | 75% | +324 bps |
| Europe (8-14h) | 9 | -32.1 bps | 33% | -289 bps |
| **Total** | **37** | **+20.9 bps** | **54%** | **+773 bps** |

La session européenne est le piège — le signal s'inverse. D'où l'exclusion dans le bot.

### Estimation multi-symboles

Si la moitié des 15 symboles ont un edge similaire à ADA : ~40 trades/jour × ~15 bps net = ~600 bps/jour.

Estimation sur 1 mois :

| Capital | /jour | /mois |
|---------|-------|-------|
| 1000€ | ~6€ | ~180€ |
| 5000€ | ~30€ | ~900€ |

**ATTENTION** : ces estimations sont basées sur 7 jours de données et extrapolées à 15 symboles non backtestés. Les vrais résultats seront probablement inférieurs. Il faut 3-4 semaines de paper trading pour valider.

---

## Ce qu'on a exploré et éliminé

10 études, 260M+ lignes de données analysées.

### Signaux qui ne marchent PAS (après frais)

| Signal | Problème |
|--------|----------|
| Book imbalance (micro, 5-30s) | Edge +1.5 bps, frais 4+ bps → perd |
| OFI seul (1 minute) | Trop bruité, rho ~0.02 |
| Whale vs retail | Aucune différence, les whales ne sont pas plus informés |
| Liquidation chain surfing | Trop rapide, signal déjà intégré |
| Basis mean-reversion | Trop faible (rho ~0.03) |
| Basis velocity | rho ~0 |
| Pairs trading (ADA/ETH) | Signal fort (rho -0.19) mais 2× les frais |
| Correlation breakdown | Pas assez de données (n=123) |
| Book velocity / accélération | Moins bon que le niveau simple |
| VPIN | Prédit la volatilité (-0.28) mais pas la direction |

### Signaux qui marchent

| Signal | Horizon | Edge net | Utilisé ? |
|--------|---------|----------|-----------|
| **OI divergence** | 2-4h | +21 bps/trade | **OUI — signal principal** |
| **Funding pre-settlement** | 1-2h | +13 bps (63% hit) | **OUI — signal secondaire** |
| **BTC lead-lag** | minutes | +3 bps (faible seul) | **OUI — signal additionnel** |
| Book imbalance (low vol) | 30s | +2 bps gross | Non (frais > edge) |
| VPIN (vol prediction) | 30s-2min | rho -0.28 | Non (pas directionnel) |

### Découvertes clés

1. **L'edge est en session Asia** : ADA en Asia = +36 bps/trade net. L'explication : faible liquidité (3.5× moins de volume) + settlement funding à 00h UTC (début Asia) → mouvements amplifiés que le marché met plus longtemps à corriger.

2. **La session européenne inverse le signal** : ne PAS trader entre 8h-14h UTC.

3. **Les signaux entre symboles sont indépendants** (corrélation ~0.07) → la diversification fonctionne.

4. **Le régime de volatilité compte** : tous les signaux micro marchent 2.7× mieux en marché calme. Le bot utilise ça comme filtre.

---

## Architecture technique

```
Binance Futures
    │
    ├── WebSocket (68 streams, 1 connexion)
    │     ├── bookTicker × 17 (prix, spread, imbalance)
    │     ├── aggTrade × 17 (volume, direction)
    │     └── markPrice@1s × 17 (basis, funding, mark/index)
    │
    └── REST API (polling toutes les 60s)
          └── openInterest × 17 symboles
                │
                ▼
        ┌───────────────────┐
        │   analysis/       │
        │   livebot.py      │  ← UN SEUL PROCESS
        │                   │
        │   SignalEngine     │  Calcul toutes les 10s :
        │   - OI divergence  │    OI change vs price change
        │   - Funding prox   │    Taux + countdown settlement
        │   - BTC lead-lag   │    Retour BTC → prédire altcoins
        │                   │
        │   TradingLogic     │  Filtres : session + score + leverage
        │   - Max 1 pos/sym  │  Entry : score > 0.3, OI requis, cooldown 30m
        │   - Hold 2h max    │  Exit : timeout / reversal (>10m) / stop loss
        │   - Stop -100 bps  │
        │                   │
        │   Dashboard        │  FastAPI sur :8095
        │   - Ticker live 1s │  Prix, spread, imbalance, sparklines
        │   - Signaux 5s     │  Score composite, OI, funding, BTC
        │   - Positions      │  P&L non réalisé, leverage
        │   - P&L courbe     │  Gross, net, leveraged
        │   - Trades table   │  Historique avec détails
        │                   │
        │   CSV logger       │  analysis/output/livebot_trades.csv
        └───────────────────┘
```

---

## Fichiers importants

```
analysis/
    livebot.py          ← LE BOT (tout-en-un)
    livebot.html         ← Template du dashboard
    output/
        livebot.log      ← Logs du bot
        livebot_trades.csv ← Historique des trades
        *.png            ← Graphiques des études
        *.csv            ← Données des études
    study_01_ofi.py      à study_10_symbol_scan.py  ← Études d'analyse
    backtest.py          ← Backtest historique
    db.py                ← Helper DB (pour les études, pas le bot)
    utils.py             ← Helpers communs
```

---

## Pour reprendre

### Vérifier que le bot tourne
```bash
curl -s http://localhost:8095/api/state | python3 -m json.tool | head -10
tail -20 analysis/output/livebot.log
```

### Relancer le bot s'il est arrêté
```bash
fuser -k 8095/tcp 2>/dev/null
nohup .venv/bin/python3 -m analysis.livebot > analysis/output/livebot.log 2>&1 &
```

### Voir les résultats
```bash
# Nombre de trades
wc -l analysis/output/livebot_trades.csv

# Derniers trades
tail -5 analysis/output/livebot_trades.csv

# Dashboard
http://51.178.27.240:8095
```

### Relancer une étude
```bash
.venv/bin/python3 -m analysis.study_06_swing   # Backtest swing
.venv/bin/python3 -m analysis.study_07_asia_edge  # ADA Asia
.venv/bin/python3 -m analysis.study_10_symbol_scan  # Scanner symboles
```

### Si tu veux passer en production (vrai argent)

1. Attends d'avoir 3-4 semaines de paper trading positif
2. Il faut ajouter l'API key Binance et remplacer le paper trading par des vrais ordres
3. Commencer avec un petit capital (100-200€)
4. Utiliser des ordres limit (maker) pour réduire les frais

---

## Historique des versions

| Version | Date | Changements |
|---------|------|-------------|
| v4.0.0 | 2026-03-22 | LiveBot initial — 17 symboles, OI divergence, 4 signaux |
| v4.1.0 | 2026-03-22 | Fix 6 bugs critiques (1ère code review) |
| v4.2.0 | 2026-03-22 | Full Kelly 25%, max 4 positions |
| v4.3.0 | 2026-03-22 | Session overnight + fixes dashboard |
| v4.4.0 | 2026-03-22 | CSV logging signaux (toutes les 60s) |
| v4.5.0 | 2026-03-22 | CarryBot ajouté (funding carry, port 8096) |
| v4.6.0 | 2026-03-22 | Smart money = 4ème signal dans le composite |
| v4.7.0 | 2026-03-22 | 2ème code review : qualité signaux, rotation WS 23h, shutdown propre |
| v4.7.2 | 2026-03-22 | VERSION affichée dans dashboard + API |
| v4.8.0 | 2026-03-22 | Restauration trades CSV au redémarrage (plus de perte de données) |
| v4.9.0 | 2026-03-22 | **Anti-whipsaw** : hold min 10m, cooldown 30m, OI requis pour entrer |
| v5.0.0 | 2026-03-22 | **Version majeure** : filtre volatilité, trailing stop, sessions asymétriques, sizing proportionnel, funding grab |
| v5.0.1 | 2026-03-22 | Fix code review : funding grab break, margin cap, trailing stop raw bps |
| v5.1.0 | 2026-03-22 | OI lookback 60s→180s, cross-symbol filter, spread filter, streak disable |
| v5.2.0 | 2026-03-22 | Boutons STOP et RAZ sur le dashboard |
| v5.3.0 | 2026-03-22 | Simulation réaliste : slippage 1 bps + funding en temps réel |
| v5.3.1 | 2026-03-22 | BNB fee discount (4→3 bps maker roundtrip) |
| v5.4.0 | 2026-03-22 | Persistance des positions ouvertes au redémarrage (JSON) |
| v5.5.0 | 2026-03-23 | **Post-analyse 129 trades** : stop loss -100→-40 bps, disable LINK/HYPE |

### Leçons de la v4.8 → v4.9 (premiers trades live)

5 trades sous v4.8, résultat : **-$0.68** (archivés dans `livebot_trades_v4.8.csv`).

**Problèmes identifiés :**
1. Hold time moyen = 5.9 min pour un signal swing 2h → scalping involontaire
2. 100% des exits par reversal (aucun timeout/stop loss atteint)
3. Whipsaw BNB : LONG → SHORT immédiat → 2 pertes consécutives (-$1.86)
4. BCH/BNB entrés sans signal OI (uniquement BTC lead-lag) → perdants

**Corrections v4.9 :**
- Hold minimum 10 min avant vérification reversal → laisser le signal travailler
- Cooldown 30 min après exit → empêcher les allers-retours destructeurs
- OI divergence requis pour entrer → plus de trades sur signal secondaire seul

Compteurs remis à zéro pour la v4.9.

### Améliorations v5.0 (analyse approfondie du bot)

Après la v4.9, analyse systématique de ce que le bot pouvait faire mieux :

**1. Filtre de volatilité** — Les études montraient 2.7× meilleur edge en marché calme, mais le bot n'avait aucun filtre. Ajout : mesure de la vol réalisée sur 3 min (std des returns sur 18 ticks de 10s). Si vol > 15 bps → pas d'entrée. Le signal OI divergence a besoin de marchés ordonnés pour fonctionner.

**2. Trailing stop** — Le bot laissait les gains s'évaporer : une position à +40 bps pouvait retomber à +5 bps avant de sortir par reversal. Maintenant : si le P&L atteint +25 bps, un trailing stop s'active et sort si ça recule de 15 bps depuis le peak.

**3. Sessions asymétriques** — Backtest : Asia = +36 bps/trade, US = +27 bps. Avant : même seuil partout (0.3). Maintenant :
- Asia/Overnight : seuil 0.25, leverage 100% → plus agressif (meilleur edge)
- US : seuil 0.35, leverage 80% → plus prudent (edge moindre)

**4. Sizing proportionnel au score** — Avant : marge fixe 25% ($250). Maintenant : 20% à 30% selon la force du signal. Score 0.3 → $200, score 0.6+ → $300. Kelly dit : miser proportionnellement à l'avantage.

**5. Funding grab** — L'edge funding est concentré dans les 30 dernières minutes avant le settlement (00h/08h/16h UTC). Dans cette fenêtre, le seuil d'entrée baisse de 20% pour capturer plus d'opportunités.

Compteurs remis à zéro pour la v5.0.

### Première nuit live v5.4 (22-23 mars 2026) — 129 trades

**Résultat : -$10.55 (-1.05%)** sur $1000, 129 trades en ~14h (overnight + asia + us).

| Métrique | Valeur |
|----------|--------|
| Trades | 129 |
| Win rate | 56% |
| Hold moyen | 18 min |
| Gross moyen | +1.7 bps |
| Max drawdown | $16.35 |

**Par session :**

| Session | Trades | Win | P&L |
|---------|--------|-----|-----|
| Overnight | 34 | 56% | -$1.04 |
| Asia | 57 | 60% | -$3.43 |
| US | 38 | 50% | -$6.08 |

**Par type de sortie :**

| Exit | Trades | Win | Total P&L | Commentaire |
|------|--------|-----|-----------|-------------|
| **trail_stop** | 50 | **98%** | **+$34.12** | Machine à cash — fonctionne parfaitement |
| reversal | 69 | 33% | -$16.33 | Signal se retourne → pertes |
| **stop_loss** | 10 | **0%** | **-$28.34** | Trop large à -100 bps → grosses pertes |

**Le problème principal** : le stop loss à -100 bps leviérés perd en moyenne -$2.83 par trade. 10 stop loss = -$28, ce qui mange les +$34 du trailing stop.

**Ratio gain/perte : 0.65x** — les pertes sont 50% plus grosses que les gains. Le trailing coupe les gagnants à +25 bps peak mais le stop loss laisse courir jusqu'à -100 bps.

**Top symboles :** AVAX (+$5.65), XMR (+$4.29), XLM (+$3.29)
**Flop symboles :** LINK (-$5.90, 0% win), HYPE (-$3.57, 17% win), SUI (-$3.78)

**Corrections v5.5 :**
1. Stop loss resserré de **-100 bps à -40 bps** leviérés → coupe les pertes 2.5× plus tôt
2. **LINKUSDT désactivé** — 0% win rate sur 3 trades, -$5.90
3. **HYPEUSDT désactivé** — 17% win rate sur 6 trades, -$3.57

**Impact estimé si appliqué aux 129 trades** : ~+$20 (bot serait à +$10 au lieu de -$10).

Compteurs remis à zéro pour la v5.5.

---

## Décisions prises et justifications

| Décision | Pourquoi |
|----------|----------|
| Session européenne exclue | Le signal OI divergence s'inverse (backtest : -32 bps/trade) |
| Session overnight incluse | Même profil de faible liquidité que l'Asia, couvre le pre-funding 00h UTC |
| Full Kelly (25% par trade) | Critère de Kelly optimal avec 54% win rate et ratio gain/perte 1.6 |
| Pas d'auto-optimisation pour l'instant | Attendre 2-3 semaines de données live avant d'automatiser les ajustements |
| Hold minimum 10 min (v4.9) | Le signal OI divergence est calibré sur 2h — sortir en 2 min ne lui laisse aucune chance |
| Cooldown 30 min (v4.9) | Évite le whipsaw LONG→SHORT→2 pertes (observé sur BNB en live) |
| OI requis pour entrer (v4.9) | Trades sans OI = 100% perdants en live. BTC lead-lag seul = insuffisant |
| Filtre vol 15 bps (v5.0) | Les études montrent 2.7× meilleur edge en marché calme — vol élevée = bruit |
| Trailing stop +25/-15 bps (v5.0) | Protège les profits acquis au lieu de les rendre par reversal tardif |
| Asia agressif / US prudent (v5.0) | Asia = +36 bps vs US = +27 bps — adapter le risque au edge réel |
| Sizing 20-30% par score (v5.0) | Kelly : miser proportionnellement à l'avantage, pas forfaitaire |
| Funding grab -20% seuil (v5.0) | Edge funding concentré dans les 30 min avant settlement |
| Stop loss -100→-40 bps (v5.5) | 10 stop loss = -$28 en une nuit. Pertes 2.5× plus grosses que gains → resserrer |
| LINKUSDT désactivé (v5.5) | 0% win rate, -$5.90 sur 3 trades en live |
| HYPEUSDT désactivé (v5.5) | 17% win rate, -$3.57 sur 6 trades en live |
| 15→13 symboles (v5.5) | Mieux vaut moins de symboles rentables que plus de symboles qui saignent |

## Prochaines étapes

### Après 2-3 semaines de paper trading

1. **Analyser les résultats** : charger `livebot_signals.csv` + `livebot_trades.csv`
   - Quels symboles gagnent, lesquels perdent → virer les mauvais
   - Quel seuil de score est optimal → ajuster `MIN_SCORE`
   - Le Kelly 25% est-il trop agressif → ajuster
   - Heures exactes qui gagnent → affiner les sessions

2. **Auto-optimisation** : le bot relit ses propres trades chaque nuit à 8h UTC et ajuste :
   - Symbole avec 5 pertes d'affilée → désactivé 24h
   - Seuil d'entrée basé sur le score moyen des trades gagnants
   - Win rate par session → ajuster les heures
   - Ne pas implémenter avant d'avoir des données live suffisantes

3. **CarryBot** (actif, port 8096) :
   - Funding carry trade market-neutral : long le plus bas funding, short le plus haut
   - 19 symboles surveillés, max 3 paires simultanées, $150/leg
   - Rebalance tous les 3 jours (9 settlements)
   - Backtest : +6.9 bps/trade net, 75% win rate, +760 bps sur 2 mois
   - XMR = le plus stable (flip rate 1%), toujours short XMR
   - Dashboard : http://51.178.27.240:8096
   - Logs : `analysis/output/carrybot.log` + `carry_trades.csv` + `carry_signals.csv`
   - Commandes : `python3 -m analysis.carrybot`

4. **Passage en production** (si paper trading positif) :

   **⚠ Binance Futures interdit aux résidents français** depuis 2022 (AMF).
   Le paper trading actuel utilise des données publiques (pas de compte) = pas de problème.

   **Option A : Résidence Suisse** (recommandé si possible)
   - Binance Futures **autorisé** — pas de restriction FINMA sur les dérivés crypto
   - Le bot fonctionne tel quel, aucune migration nécessaire
   - KYC Binance avec adresse suisse → accès complet Futures
   - Fiscalité très avantageuse :

   | | France | Suisse |
   |---|---|---|
   | Binance Futures | Interdit (AMF) | **Autorisé** (FINMA) |
   | Impôt gains crypto | 30% flat (PFU) | **0%** (particulier) |
   | Seuil requalification pro | Dès le 1er € | Volume très élevé |
   | Déclaration | Obligatoire (3916-bis) | Fortune uniquement |

   Gains en capital = **exonérés** tant qu'on n'est pas qualifié "trader professionnel"
   (critères AFC : volume très élevé + levier + activité principale + emprunt).
   Un bot sur $1-10k en gestion privée → aucun risque de requalification.

   **Option B : DEX depuis la France**

   | Plateforme | Type | Perpétuels | OI | Funding | KYC | Adaptation |
   |---|---|---|---|---|---|---|
   | **Hyperliquid** | DEX | Oui | Oui (API) | Oui | Non | Moyenne |
   | **DYDX v4** | DEX | Oui | Oui (API) | Oui | Non | Moyenne |
   | **Bybit** | CEX | Oui | Oui (API) | Oui | Oui | Facile |

   Recommandation DEX : **Hyperliquid** — le plus liquide, pas de KYC, API REST+WS.

   **Plan de migration :**
   1. Valider le paper trading sur Binance (2-3 semaines)
   2. Si Suisse : créer compte Binance avec KYC suisse → prod directe
   3. Si France : adapter endpoints à Hyperliquid → paper 1 semaine → prod
   4. Commencer avec petit capital ($100-200)
   5. Monter progressivement si rentable
   - Commencer avec $100-200 de capital réel
   - Garder le paper trading en parallèle pour comparer
| Horizon 2h (pas 30s) | Les frais (4 bps) mangent l'edge micro (1-2 bps) mais pas l'edge swing (20+ bps) |
| ADA comme symbole principal | Meilleur edge backtesté (+36 bps Asia), ratio OI/volume favorable |
| 15 altcoins ajoutés | Signaux indépendants (corr ~0.07), même profil OI/volume qu'ADA |
| Pas de collecteur DB | Inutile — le bot lit Binance directement, économise 2 GB/jour de disque |
| Leverage dynamique 1-3x | Amplifie les trades à haute confiance sans risquer sur les faibles |
| Signal OI gradué (v4.7) | Graduation [0.3-1.0] au lieu de binaire ±1 — proportionnel à la divergence |
| Smart money buffer 30 (v4.7) | z-score sur 5 points = bruit pur, 30 points = 5 min de données L/S distinctes |
| Rotation WS 23h (v4.7) | Reconnexion proactive avant la limite Binance 24h |
| Restauration CSV (v4.8) | Plus de perte d'historique au redémarrage du bot |
