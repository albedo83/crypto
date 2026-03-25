# Multi-Signal Bot v10.1.0

## Qu'est-ce que c'est ?

Un bot de trading automatique qui surveille 28 cryptomonnaies sur Hyperliquid (un exchange décentralisé accessible depuis la France) et prend des positions quand il détecte des situations précises qui ont historiquement été rentables.

Le bot fonctionne en **paper trading** (argent virtuel) pour valider ses résultats avant tout investissement réel.

---

## Comment ça marche

Le bot vérifie le marché toutes les heures. Il cherche **5 situations** :

### Signal 1 — "BTC explose" (S1)
- **Condition** : Bitcoin a monté de plus de +20% sur les 30 derniers jours
- **Action** : Acheter des altcoins (LONG)
- **Logique** : Quand BTC pump fort, les alts suivent toujours avec du retard. On profite de ce retard.
- **Fréquence** : Rare — quelques fois par an
- **Fiabilité** : La plus élevée (z-score 6.42)
- **Mise** : $241 sur un capital de $1,000 (la plus grosse car le plus fiable)

### Signal 2 — "Les alts crashent" (S2)
- **Condition** : La moyenne des altcoins a baissé de plus de -10% en 7 jours
- **Action** : Acheter (LONG)
- **Logique** : Après un gros crash, les alts rebondissent. On achète la panique des autres.
- **Fréquence** : Quelques fois par mois
- **Fiabilité** : Bonne (z-score 4.00)
- **Mise** : $150

### Signal 3 — "Calme plat + dollar fort" (S4)
- **Condition** : La volatilité d'un altcoin est basse + la bougie est petite + le dollar américain monte
- **Action** : Vendre à découvert (SHORT)
- **Logique** : En crypto, quand c'est calme et que le dollar se renforce, les alts dérivent lentement vers le bas. On parie sur cette dérive.
- **Filtre DXY** : Ce signal est DÉSACTIVÉ quand le dollar baisse (pour éviter de shorter en bull market)
- **Fréquence** : Variable — dépend du dollar
- **Fiabilité** : Correcte (z-score 2.95)
- **Mise** : $111

### Signal 4 — "Un token casse de son groupe" (S5)
- **Condition** : Un token diverge de plus de 10% par rapport à la moyenne de son secteur (L1, DeFi, Gaming, etc.), avec un volume au-dessus de la normale
- **Action** : Suivre le mouvement (LONG si ça monte, SHORT si ça baisse)
- **Logique** : Quand un token casse de son secteur avec du volume, le mouvement continue. Ce n'est pas du bruit, c'est un vrai signal.
- **Fréquence** : 10-20 fois par mois
- **Fiabilité** : Bonne (z-score 3.67)
- **Mise** : $138

### Signal 5 — "Flush de liquidation" (S8)
- **Condition** : Un alt a perdu plus de -40% par rapport a son plus haut de 30 jours + le volume explose + le prix continue de baisser + BTC est aussi en baisse de -3% sur 7 jours
- **Action** : Acheter (LONG)
- **Logique** : Quand tout tombe en meme temps (alt crash + volume anormal + BTC faible), c'est un flush de liquidation force. Les traders en levier se font liquider, ce qui pousse le prix encore plus bas que sa valeur reelle. Le rebond qui suit est violent et rapide.
- **Frequence** : Rare — ~1 fois par mois en portfolio
- **Fiabilite** : Tres elevee (z-score 6.99, le plus haut de tous les signaux)
- **Mise** : $262 (la plus grosse car z-score le plus eleve)
- **Risque** : 30% des trades perdent (stop loss a -15%). En avril 2024, 7 pertes consecutives (-$265).

### Les 5 secteurs surveilles

| Secteur | Tokens |
|---|---|
| L1 (blockchains) | SOL, AVAX, SUI, APT, NEAR, SEI |
| DeFi | AAVE, MKR, CRV, SNX, PENDLE, COMP, DYDX, LDO, GMX |
| Gaming | GALA, IMX, SAND |
| Infra | LINK, PYTH, STX, INJ, ARB, OP |
| Meme | DOGE, WLD, BLUR, MINA |

BTC et ETH servent de référence mais ne sont pas tradés.

---

## Paramètres techniques

| Paramètre | Valeur | Explication |
|---|---|---|
| **Levier** | 2x | Chaque dollar travaille comme deux. Double les gains ET les pertes. |
| **Sizing** | 15% du capital | Chaque position utilise 15% du capital actuel, pondéré par la fiabilité du signal. |
| **Compounding** | Oui | Quand le capital monte, les mises augmentent. Quand il baisse, elles diminuent. |
| **Duree des trades** | 72h (S1/S2/S4), 48h (S5), 60h (S8) | Chaque position est fermee automatiquement apres ce delai. |
| **Stop loss** | -25% | Filet de sécurité en cas de crash extrême. Ne se déclenche presque jamais. |
| **Max positions** | 6 simultanées | Max 4 dans la même direction (4 LONG ou 4 SHORT). |
| **Exposition max** | 90% du capital | Le bot ne met jamais plus de 90% du capital en jeu. |
| **Frais simulés** | 24 bps par trade | 12 bps de frais réels × 2 pour le levier. Conservateur (les vrais frais Hyperliquid sont plus bas). |
| **Cooldown** | 24h par token | Après avoir fermé une position sur un token, le bot attend 24h avant de re-trader le même. |

---

## Ce que la recherche a montré

### La méthode
- **1500+ regles** testees systematiquement sur 24 indicateurs et 28 tokens
- **Algorithmes génétiques** et **programmation génétique** (évolution de formules mathématiques)
- **Machine Learning** (Random Forest, Gradient Boosting) avec validation walk-forward
- **Monte Carlo** : chaque signal comparé à du timing aléatoire (même nombre de trades, même direction, dates aléatoires)
- **Train/test split** : signaux trouvés sur 2024, validés sur 2025-2026 (pas sur les mêmes données)

### Ce qui a été éliminé
- Momentum (acheter les gagnants, vendre les perdants) → bruit
- Mean-reversion cross-sectionnelle → pire stratégie testée
- Effets calendaires (mardi meilleur que dimanche) → instable
- Carry (collecter le funding) → taux trop bas sur Hyperliquid
- Token unlocks → biais de marché bear
- Pairs trading → toutes les configs perdent
- On-chain (whales, stablecoins) → pas prédictif
- Programmation génétique → overfit systématique
- Liquidation bounce → perd en portfolio malgré un excellent score solo

### Ce qui a ete elimine (2eme vague de recherche)
- 6 nouvelles strategies multi-conditions testees (S7, S9, S10, SX, SY) → echouent train/test
- 8 strategies SHORT (378 variantes) → aucune ne depasse z=2.0
- Regime gating (activer signaux selon bull/bear) → degrade tous les signaux existants
- Signal de liquidation comme filtre (au lieu de signal) → pas d'amelioration

### Ce qui a survecu
5 signaux sur 1500+ testes passent tous les filtres (profit en train ET test, Monte Carlo significatif). Ce sont les 5 signaux du bot.

---

## Perspectives de gain

### Backtest (36 mois, données réelles Hyperliquid)

| Année | Contexte marché | Performance | Capital |
|---|---|---|---|
| 2023 (5 mois) | Bear | +8% | $1,000 → $1,081 |
| 2024 | Bull | +528% | $1,081 → $6,786 |
| 2025 | Bear/latéral | +145% | $6,786 → $16,646 |
| 2026 (3 mois) | Latéral | -33% | $16,646 → $11,214 |
| **Total** | **35 mois** | **+1,021%** | **$1,000 → $11,214** |

### Ce que ça veut dire selon le marché

| Scénario | Rendement annuel estimé | Ce qui se passe |
|---|---|---|
| **Bull market** | +200% à +500% | S1 (BTC rip) se déclenche, S5 (secteur) en soutien. Les gains composent vite. |
| **Bear market** | +50% à +150% | S4 (short) et S5 (secteur) portent les gains. S2 (alt crash) fournit des rebonds. |
| **Marché calme** | -10% à +20% | Peu de signaux, le bot dort. Petites pertes en frais sur les rares trades. |
| **Crash prolongé** | -20% à -50% | Les dips ne rebondissent pas (S2 perd), le calme plat ne mène pas à une baisse (S4 perd). |

### Les chiffres honnêtes

- **Rendement moyen estimé** : +50% à +150%/an (très variable selon le marché)
- **Bon mois** : +$500 à +$5,000 (sur un capital de $5,000+)
- **Mauvais mois** : -$200 à -$4,000
- **Pire drawdown observé** : -54% (tu perds la moitié avant que ça remonte)
- **Pire série** : 3 mois perdants consécutifs (Q1 2026)
- **Mois gagnants** : 57% du temps (20 sur 35)
- **Bot inactif** : ~26% du temps (aucun signal, pas de trade)

### Ce que le bot ne peut PAS faire

- Garantir un gain mensuel. 43% des mois sont perdants.
- Protéger contre un crash de -50% en quelques heures (le stop à -25% limite les dégâts mais pas à zéro).
- Performer dans un marché totalement plat pendant des mois.
- Prédire l'avenir. Il exploite des patterns statistiques qui ont fonctionné par le passé.

---

## Risques

### Risque de perte
Avec un drawdown max de -54%, un investissement de $1,000 peut temporairement descendre à **$460** avant de remonter. Il faut être psychologiquement prêt à voir ce chiffre et ne pas paniquer.

### Risque de modèle
Les 4 signaux ont été découverts sur des données passées. Le marché crypto évolue. Ce qui marchait en 2024-2025 pourrait ne plus marcher en 2027. C'est pour ça qu'on fait du paper trading d'abord.

### Risque de plateforme
Hyperliquid est un exchange décentralisé. Il pourrait avoir des bugs, des hacks, ou des problèmes de liquidité. L'argent sur Hyperliquid n'est pas protégé par une assurance.

### Risque technique
Le bot tourne sur un serveur. Si le serveur tombe ou si l'API Hyperliquid est indisponible, les positions restent ouvertes sans surveillance. Le stop à -25% est la dernière protection.

---

## Fonctionnement technique

### Architecture
```
Hyperliquid REST API
    ├── Prix (toutes les 60s)
    ├── Bougies 4h (toutes les heures, 30 tokens)
    └── Yahoo Finance DXY (toutes les 6h)
            │
            ▼
    reversal.py (processus unique)
    ├── Features (22 indicateurs par token)
    ├── 4 signaux (S1, S2, S4, S5)
    ├── Position manager (max 6, stop -25%)
    ├── State persistence (JSON + CSV)
    └── Dashboard FastAPI (:8097)
```

### Fichiers
| Fichier | Rôle |
|---|---|
| `analysis/reversal.py` | Le bot (code principal) |
| `analysis/reversal.html` | Dashboard web |
| `analysis/output/reversal_trades.csv` | Historique des trades |
| `analysis/output/reversal_state.json` | État des positions (pour les redémarrages) |
| `analysis/output/reversal_v10.log` | Logs |
| `docs/research_findings.md` | Journal de recherche complet |
| `docs/bot.md` | Ce fichier |

### Commandes
```bash
# Lancer le bot
nohup .venv/bin/python3 -m analysis.reversal > analysis/output/reversal_v10.log 2>&1 &

# Dashboard
# http://0.0.0.0:8097

# Arrêter
fuser -k 8097/tcp

# Logs en direct
tail -f analysis/output/reversal_v10.log

# Trades
cat analysis/output/reversal_trades.csv
```

### API
| Endpoint | Description |
|---|---|
| `GET /` | Dashboard HTML |
| `GET /api/state` | État du bot (balance, positions, signaux actifs) |
| `GET /api/signals` | Tous les tokens avec leurs signaux |
| `GET /api/trades` | Historique des trades |
| `GET /api/pnl` | Courbe de P&L |
| `POST /api/pause` | Ferme toutes les positions et met en pause |
| `POST /api/resume` | Reprend le trading |
| `POST /api/reset` | Remet tout à zéro |

---

## Prochaines étapes

1. **Paper trading 1-2 mois** — Vérifier que le comportement réel colle au backtest
2. **Comparer** — Le P&L réel doit être dans la fourchette du backtest (en tenant compte que les frais réels sont plus bas que simulés)
3. **Si ça colle** — Mettre un petit capital réel ($100-500) sur Hyperliquid et activer le trading réel
4. **Surveiller** — Vérifier le dashboard chaque jour, lire les logs en cas de trade

### Améliorations futures possibles
- **Ordres limit** (maker) au lieu de market (taker) → frais divisés par 5, mais risque de rater des trades
- **Bougies 1h** → plus de trades pour S4 et S5 (testé, prometteur mais pas assez d'historique pour valider)
- **Plus de tokens** → pas d'impact avec les signaux actuels (ils sont globaux, pas token-spécifiques)
- **Liquidation map Hyperliquid** → exploiter la transparence du DEX pour anticiper les cascades de liquidation
- **Signaux macro** → le DXY est déjà utilisé comme filtre, le VIX pourrait être ajouté
