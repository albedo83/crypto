# Phase 6 preview — nouveaux chiffres de référence (mode aligned)

_Généré 2026-06-10T17:44Z — data through 2026-06-08T12:00 — capital $500 — `backtests/backtest_phase6_preview.py`_

**Statut : PREVIEW.** La sémantique legacy reste celle de `docs/backtests.md` 
tant que la phase 6 n'est pas actée. Ce dossier chiffre l'impact de l'acte.

Alignements inclus dans `aligned` (cf. `docs/alfred_divergences.md`) : 
#1 prix d'exit synthétiques · #2 stop-first · #3 prop_trail simulé · 
#5/6/7 sizing live (cap $500 post-modulateur, arrondi, floor $10) · 
#8 force S10 = 1000/range · #9 btc_z manquant → None · #10 fenêtre z = bot.
`aligned_noMKR` ajoute le retrait de MKR (mort depuis 2025-09, rebranding SKY).

## Résumé par fenêtre

| Fenêtre | Mode | P&L % | DD max | Trades | WR |
|---|---|---|---|---|---|
| 28m | legacy | +53267.1% | -56.8% | 1199 | 51% |
| 28m | aligned | +1571.8% | -49.8% | 1205 | 54% |
| 28m | aligned_noMKR | +1478.9% | -58.4% | 1190 | 53% |
| 12m | legacy | +5451.4% | -58.5% | 498 | 53% |
| 12m | aligned | +792.7% | -25.0% | 501 | 54% |
| 12m | aligned_noMKR | +708.9% | -44.8% | 498 | 53% |
| 6m | legacy | +166.3% | -61.7% | 264 | 48% |
| 6m | aligned | +206.9% | -55.2% | 266 | 50% |
| 6m | aligned_noMKR | +206.9% | -55.2% | 266 | 50% |
| 3m | legacy | -5.9% | -61.7% | 152 | 42% |
| 3m | aligned | +25.6% | -53.4% | 154 | 47% |
| 3m | aligned_noMKR | +25.6% | -53.4% | 154 | 47% |

## Δ (aligned_noMKR − legacy) — l'impact de l'acte phase 6

| Fenêtre | ΔP&L (pp) | ΔDD (pp) | ΔTrades |
|---|---|---|---|
| 28m | -51788.2 | -1.6 | -9 |
| 12m | -4742.5 | +13.6 | +0 |
| 6m | +40.6 | +6.5 | +2 |
| 3m | +31.5 | +8.3 | +2 |

## Breakdown par stratégie — cible (aligned_noMKR, 28m)

| Strat | Trades | WR | P&L |
|---|---|---|---|
| S1 | 112 | 57% | $+2,112 |
| S10 | 347 | 56% | $+873 |
| S5 | 449 | 45% | $+1,196 |
| S8 | 142 | 53% | $+1,525 |
| S9 | 140 | 66% | $+1,689 |

## Acte de la phase 6 (quand décidé)

1. `aligned=True` devient le défaut du run officiel (`main()` de backtest_rolling)
2. Retrait MKR de `Params.trade_symbols` + `Params.sectors` (alfred/settings.py)
3. Re-run `docs/backtests.md` + archivage des anciens chiffres
4. Mise à jour de `docs/alfred_divergences.md` (statut : alignées)
