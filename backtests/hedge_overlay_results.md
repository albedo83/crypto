# Overlay de hedge conditionnel anti-crash-corrélé — placard (2026-07-12)

**VERDICT : PLACARD (échoue Étape 0).** Le drawdown corrélé n'est PAS la menace dominante.
On ne construit pas d'abri quand l'ouragan n'est pas la vraie menace.

Script : `python3 -m backtests.backtest_hedge_overlay`. Book = backtest production sur la fenêtre profonde.

Book = backtest production (aligned, modulateur, margin_check, mfe_on_close), fenêtre profonde
**2023-12-12 → 2026-07-12 (944 j, 1482 trades, pnl +225 884, maxDD −39.3 %)**. Equity
mark-to-market = capital + basket_unreal (5662 points 4h). Net-notionnel/brut moyen +0.14 → net-long confirmé.

## Étape 0 — caractériser l'ennemi

### (A) Couplage global book↔BTC (rendements 4h)
- beta = **+0.35**, corr = **+0.23**, **R² = 0.05**
- → seulement **5 %** de la variance du book est systématique-BTC. **95 % est idiosyncratique** (sélection de fades), qu'un hedge BTC ne peut PAS toucher.

### (B) Rendement du book par décile de rendement BTC
| décile BTC | BTC moy | book moy/candle |
|---|---|---|
| 0 (crash) | −1.86 % | **−0.36 %** |
| 5 | +0.09 % | +0.08 % |
| 9 (rally) | +1.84 % | **+0.78 %** |
Monotone, net-long. Le book saigne dans le décile-crash (−0.36 %) mais gagne PLUS dans le rally (+0.78 %) qu'il ne perd dans le crash → l'exposition BTC est un carry net-long, pas la source de risque.

### (C) Décomposition des épisodes de drawdown — les 10 pires
| rang | depth | BTC pdt | durée | type |
|---|---|---|---|---|
| 1 | **−40.9 %** | **+24.2 %** | 2632 h | **idiosync** |
| 2 | **−29.2 %** | +1.0 % | 1228 h | **idiosync** |
| 3 | −24.9 % | −13.9 % | 968 h | corrélé |
| 4 | −24.5 % | −19.5 % | 344 h | corrélé |
| 5 | −23.6 % | −2.4 % | 284 h | idiosync |
| 6 | −21.4 % | −10.7 % | 236 h | corrélé |
| 7 | −20.0 % | −14.4 % | 560 h | corrélé |
| 8 | −18.4 % | +0.6 % | 436 h | idiosync |
| 9 | −17.8 % | −1.0 % | 756 h | idiosync |
| 10 | −17.6 % | −5.2 % | 636 h | corrélé |

**Les 2 pires drawdowns du book sont IDIOSYNCRATIQUES — BTC était en HAUSSE (+24 %, +1 %).**
Le book se fait courir dessus par le momentum (fades écrasées), pas par un crash corrélé.

- Part du drawdown venant d'épisodes corrélés : **29 % (profondeur) / 43 % ($ pondéré)** — minorité.
- Pires 5 % candles du book → 67 % coïncident avec BTC en baisse (coïncidence de candle aiguë, PAS dominance de drawdown).

### (D) Sensibilité au seuil (anti-cherry-pick)
| seuil corrélé | part $ corrélée |
|---|---|
| BTC ≤ −1 % | 53 % |
| BTC ≤ −2 % | 46 % |
| BTC ≤ −3 % | 43 % |
| BTC ≤ −5 % | 35 % |
Seulement au seuil le plus laxiste (−1 %, à peine un « crash ») ça dépasse 50 %. À tout seuil de crash réaliste : **minorité**. Les 2 pires restent idiosync à TOUS les seuils.

### (E) Contre-feu : le hedge AGGRAVE la vraie menace
Un short-BTC armé pendant les 2 pires drawdowns (BTC monte) AJOUTE de la perte :
| taille hedge | épisode 1 (BTC +24 %) | épisode 2 (BTC +1 %) |
|---|---|---|
| 30 % brut | **−7.3 pp** | −0.3 pp |
| 50 % brut | **−12.1 pp** | −0.5 pp |
Le bouclier pointe dans le mauvais sens : il approfondit le drawdown #1 (−40.9 % → jusqu'à −53 %).

## Verdict (pré-enregistré, ET logique)
Étape 0 exige : drawdown corrélé DOMINANT. **NON** (43 % $, R² 5 %, 2 pires idiosync). → **PLACARD.**
Étapes 1 (matrice de détection VP/FP/latence) et 2 (backtest overlay net-de-whipsaw) **NON lancées** : un déclencheur parfait ne peut pas aider quand la menace dominante n'est pas le crash corrélé, et un short-BTC aggrave les pires drawdowns (idiosync, BTC up).

## Caveats
1. **Puissance** : ~5 crashs corrélés *matériels et profonds* (DD −18 à −25 %, BTC ≤ −3 %) sur 944 j. Efficacité réelle d'un hedge ⇒ puissance quasi-nulle même si on l'avait construit.
2. **Junior réel = 38 j, 0 crash** → impossible de caractériser sur le live ; caractérisation faite sur le backtest profond (seule fenêtre viable).
3. **Risque propre du short** démontré empiriquement (contre-feu E) : short pendant rebound/rally = whipsaw incarné, exactement le mode d'échec redouté.
4. **Forme du prochain crash** : hors sujet ici (mort avant la calibration), mais le point plus profond tient — la menace dominante du book n'est structurellement pas le beta-crash, c'est la sélection de fades idiosyncratique.

## Ce qui aurait un sens (hors périmètre, pas testé)
Le vrai risque dominant = **idiosyncratique** (fades écrasées par le momentum, BTC neutre/haussier). Un hedge BTC ne le couvre pas. Piste logique = réduction de BRUT conditionnelle (equity_brake, déjà shippé v1.11.0) plutôt qu'un short directionnel — mais ce n'est pas un hedge de crash corrélé, c'est autre chose.

*Sources : hedge_etape0.py, hedge_etape0.json. Backtest production run_window. 2026-07-12. Mesure seule, aucune modif.*
