# Extension prop_trail à S5 — R&D (CANDIDAT SHIP, décision utilisateur)

**Date** : 2026-06-17 · Scripts : `backtests/backtest_s5_prop_trail.py`
· Logs : `analysis/output/s5_prop_trail.log`

## Origine
Live senior : S5 LONG WR 70 % mais **payoff 0.63** — les gagnants RENDENT leur MFE
(4 scratches montés à +265/+334 bps, finis à +15/+61 → ~250 bps rendus chacun). Or
`prop_trail` (verrou proportionnel du MFE) ne couvrait que **S9/bull** ; S5 n'avait
**aucun trailing**. Levier inexploré → attaquer le côté GAGNANTS, pas les perdants.

## Méthode
`prop_trail_rule` existe déjà (générique). On injecte une config S5 via
`prop_trail_override` (backtest), mode **aligned** (config live réelle). Verrou
proportionnel : stop = arm + (mfe − arm) × lock → laisse courir les gros (CRV +1990).
Baseline = défaut (S9 seul) → reproduit **exactement** 819.9/440.6/170.2/6.1 (zéro
régression). Grille arm∈{150,200,300} × lock∈{0.40,0.50,0.65}, tous régimes.

## Résultat — fort et robuste

### Grille (4 fenêtres glissantes 28/12/6/3m) : 6/9 configs STRICT 4/4
Plateau **contigu** (arm200 passe à tous les locks ; lock0.65 passe à tous les arms) —
pas un pic isolé. DD amélioré sur quasi toutes les cellules.

| config | PnL/4 | DD/4 | sumΔPnL | Δ28m | Δ12m | Δ6m | Δ3m |
|--------|:--:|:--:|--:|--:|--:|--:|--:|
| **a200_l65** (reco) | 4/4 | 4/4 | +495 | +289 | +54 | +57 | +95 |
| a300_l65 | 4/4 | 4/4 | +527 | +274 | +61 | +68 | +125 |
| a200_l50 | 4/4 | 4/4 | +435 | +251 | +42 | +49 | +93 |
| a150_l65, a200_l40, a150_l40 | 4/4 | 4/4 | +380…+412 | | | | |

### Effet sur le BOOK S5 (28m) — la cible
| | PnL S5 | n | WR |
|--|--:|--:|--:|
| baseline | 1510 | 463 | **47 %** |
| a200_l65 | **3914 (+159 %)** | 587 | **75 %** |
Le trailing convertit les scratches (qui rendaient leur MFE) en gains verrouillés →
WR 47→75 %, book ×2.6, **sans toucher les perdants**.

### Stabilité OOS (4 fenêtres NON-recouvrantes, a200_l65) : honnête
| split | ΔPnL | ΔDD |
|-------|--:|--:|
| 24→18m | +51.8 | **+29.7** (DD −58→−29) |
| 18→12m | +46.5 | +9.1 |
| 12→6m | **−14.7** | +9.0 |
| 6→0m | +56.6 | +2.1 |
**DD amélioré sur les 4 (massif). PnL positif sur 3/4** — la fenêtre négative est une
période de forte tendance où le trailing écrête un peu les gros (coût inhérent).

## Verdict & reco
**a200_l65 (arm 200 bps, lock 0.65, tous régimes)** — même lock_ratio que S9.
- ✅ DD amélioré sur **tous** les tests (grille 4/4 + OOS 4/4), souvent massivement.
- ✅ Book S5 transformé (WR 47→75, PnL ×2.6), payoff relevé, **perdants intacts**.
- ⚠️ Pas un strict-4/4 PnL en OOS (3/4) : −15pp sur une fenêtre de forte tendance.
- **Nature** : amélioration **risk-adjusted** — un peu moins de pic en tendance,
  beaucoup moins de drawdown partout. Exactement la cible (le user s'inquiète des DD
  −50 % et de l'asymétrie payoff). Bien plus robuste que le slow-bleed (réfuté).

## Pour shipper (décision + restart utilisateur)
Changement live = **1 ligne** dans `settings.py` :
`prop_trail_params = {"S9": {...}, "S5": {"bear": (200,0.65), "neutral": (200,0.65), "bull": (200,0.65)}}`
+ version bump + CHANGELOG + CLAUDE.md, puis restart Alfred (OK explicite requis).
Kill-switch : retirer l'entrée S5. (`prop_trail_override` ajouté au harness BT = R&D, OFF par défaut.)
