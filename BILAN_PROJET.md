# Projet Crypto — Bilan complet

## Ce qui tourne

**Un seul process** : `analysis/livebot.py` (PID sur le serveur)

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

### Le leverage dynamique

| Signaux confirmés | Leverage |
|-------------------|----------|
| 1 seul (ex: OI divergence) | 1x |
| 2 (ex: OI + funding) | 2x |
| 3 (OI + funding + BTC lead) | 3x |

### Les paramètres

| Paramètre | Valeur | Pourquoi |
|-----------|--------|----------|
| Horizon | 2 heures | Les frais (4 bps) sont négligeables face aux mouvements de 20-40 bps |
| Stop loss | -100 bps | Limite les pertes sur un trade |
| Coût simulé | 4 bps roundtrip | Fees maker Binance (0.02% × 2 côtés) |
| Entry threshold | score > 0.3 | Au moins 1 signal actif |

### La gestion du capital

| Paramètre | Valeur |
|-----------|--------|
| Capital initial | **$1000** (fictif, paper trading) |
| Max positions simultanées | **4** |
| Marge par position | **$250** (25% du capital — full Kelly) |
| Max capital exposé | **90%** ($900) |
| Taille position 1x | **$250** |
| Taille position 2x | **$500** |
| Taille position 3x | **$750** |

Si 10 signaux arrivent en même temps, le bot **trie par force du signal** et prend les 5 meilleurs. Les autres sont ignorés.

Les frais Binance réels par trade (maker, VIP 0) :

| Taille | Fee aller | Fee retour | Total |
|--------|-----------|------------|-------|
| $250 (1x) | $0.050 | $0.050 | **$0.100** |
| $500 (2x) | $0.100 | $0.100 | **$0.200** |
| $750 (3x) | $0.150 | $0.150 | **$0.300** |

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
        │   - Max 1 pos/sym  │  Entry : score > 0.3, ≥ 1 signal
        │   - Hold 2h max    │  Exit : timeout / reversal / stop loss
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

## Décisions prises et justifications

| Décision | Pourquoi |
|----------|----------|
| Session européenne exclue | Le signal OI divergence s'inverse (backtest : -32 bps/trade) |
| Session overnight incluse | Même profil de faible liquidité que l'Asia, couvre le pre-funding 00h UTC |
| Full Kelly (25% par trade) | Critère de Kelly optimal avec 54% win rate et ratio gain/perte 1.6 |
| Horizon 2h (pas 30s) | Les frais (4 bps) mangent l'edge micro (1-2 bps) mais pas l'edge swing (20+ bps) |
| ADA comme symbole principal | Meilleur edge backtesté (+36 bps Asia), ratio OI/volume favorable |
| 15 altcoins ajoutés | Signaux indépendants (corr ~0.07), même profil OI/volume qu'ADA |
| Pas de collecteur DB | Inutile — le bot lit Binance directement, économise 2 GB/jour de disque |
| Leverage dynamique 1-3x | Amplifie les trades à haute confiance sans risquer sur les faibles |
