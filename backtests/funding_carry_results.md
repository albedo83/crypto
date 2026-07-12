# Carry de funding cross-sectionnel dollar-neutral — placard (2026-07-12)

Validation adverse d'un carry perp-vs-perp ranké : **LONG** les noms à funding bas/négatif,
**SHORT** les noms à funding haut (crowdés), sizé dollar-neutral, hold lent pour amortir le
taker unique. Thèse : on récolte le **spread de funding**, pas la direction.

Scripts : `python3 -m backtests.backtest_funding_carry_gate0` (persistance + spread vs floor),
`…_edge` (edge net + décompo funding/prix + contrôle négatif), `…_beta` (beta résiduel confirmatoire).
Données : `funding_hourly` de `alfred/data/market.db` (**38 j seulement**, 36 symboles, horaire) +
bougies 4h pour la jambe prix. Coût/rebalance = 18 bps taker (2 jambes) + 8 bps slippage = 26 bps.

## Verdict pré-enregistré (ET logique)
spread persistant ET net IC>0 ET **funding domine la dérive prix** ET beta résiduel ~0 (y.c. crash)
ET corrélation ≤0 dans le tail ET Kelly monte → paper. **Un échec → placard.**

## Étape 0 — persistance + spread vs floor : **PASS marginal**
- Spread instantané top5−bottom5 = **0.74 bps/h** (médian 0.53).
- Persistance du rang (Spearman) : **+0.53 @24h, +0.43 @72h, +0.34 @168h** — réelle, lente. La
  différence revendiquée vs le cimetière S11 (rebalance lent viable, funding non fee-floor-tué) **tient sur cet axe.**
- Carry RÉALISÉ des jambes fixes (taxe de persistance : 45–75 % de l'ex-ante), net de coût :
  H=72h +8.5 bps · H=120h +21.4 bps · H=168h +17.4 bps. IC-basse net-de-coût clear le floor de
  **+2 bps** seulement, aux holds ≥120h.

## Étape 1 — décompo funding vs prix : **FAIL (tueur)**
| Hold | funding | **prix** | net | IC95(net) | funding domine ? |
|---|---|---|---|---|---|
| 48h | +19.8 | **+48.8** | +42.5 | [−45, +132] | NON |
| 72h | +34.2 | **+114.2** | +122.4 | [−12, +260] | NON |
| 120h | +46.2 | **+189.5** | +209.7 | [−6, +410] | NON |
| 168h | +41.9 | **+411.1** | +427.0 | [+193, +665] | NON |

Le prix pèse **3×–10× le funding** à tous les holds. La clause « le funding doit dominer » échoue :
le P&L est un **pari de prix relatif** (short crowded / long unloved = mean-reversion) qui a payé
sur CETTE fenêtre de 38 j. IC énorme (piloté par la variance prix, n=18) → une autre fenêtre inverse le signe.
Contrôle négatif : ranked bat le p95 random (+427 vs +132 @168h) mais **via la jambe prix**
(le random a funding≈0 ET prix≈0), pas via le carry.

## Étape 2 — confirmatoire (mécanisme, pas sauvetage)
- Beta résiduel book→BTC = **+0.27** (corr +0.20) : le « dollar-neutral » **fuit du beta** (les longs
  obscurs portent un beta différent des shorts crowdés).
- Profit concentré dans le tercile BTC-**hausse** (book +2.73 %), **plat dans le crash** (+0.02 %) →
  l'inverse d'un diversifiant de crise.
- Le pari de prix = short crowded / long unloved = **même famille que le book de fades** → corrélation
  attendue POSITIVE dans le tail, pas ≤0. Échoue aussi le gate décorrélation par construction.

## Cimetière S11
Ne revient PAS par le fee floor (la persistance tient, rebalance lent viable). Meurt d'une cause
**nouvelle** : en crypto la vol de prix (centaines de bps/semaine) écrase le funding (dizaines de
bps/semaine). Le funding est du bruit d'arrondi à côté du prix.

## Caveats
1. **Profondeur 38 j** vs 24 mois de l'audit signal — hors de portée d'un vrai walk-forward.
2. **Puissance** : n=18–27, fenêtres chevauchantes (step H/4) → n indépendant effectif ~8, IC optimistes.
3. **Un seul régime** de 38 j.
4. **Liquidité sur 4 ordres de grandeur** (BTC ~$2.2 Md vs TON/GALA/MINA ~$0.1 M day_ntl_vlm) — les
   longs obscurs sont intradeables à taille réelle ; le slippage 4 bps/jambe s'effondre.
5. DOF : K=5 jambes, rang sur funding brut.

**Bottom line** : funding HL persistant (Étape 0 vraie) mais **trop petit pour dominer** — ce qu'on
croit récolter en carry, on le récolte en beta caché + fade relatif corrélé au book existant. Placard.
