# Cadence de sortie : horaire (live) vs 4h (backtest) — étude 1h

**2026-06-30.** Question (utilisateur) : le bot live évalue ses **sorties toutes les
heures** alors que le backtest canonique ne les évalue qu'aux **closes 4h**. Si le BT 4h
« gagne » toujours, le bot fait-il une erreur en sortant à l'heure ? Et : ça devrait se
backtester avec des bougies horaires.

## Dispositif

Trois configs, toutes en sémantique Alfred (`aligned`, `mfe_on_close`, `margin_check`,
modulateur), fin commune 2026-06-30, fenêtres limitées par l'archive 1h (~200 j) :

- **A** = 4h tout (référence canonique).
- **B** = grille 1h, **entrées gatées 4h** (`entry_align_hours=4`), **sorties horaires** =
  miroir exact du bot live.
- **C** = grille 1h, tout horaire (entry_align_hours=0) — montre le confound d'entrée.

Nouvelle capability : `run_window(entry_align_hours=N)` (défaut 0 = parité). Données 1h via
`fetch_1h_candles`, features via `backtest_rolling_1h.build_features_1h`.

## Bug trouvé et corrigé (sinon toute conclusion 1h est fausse)

`build_features_1h` calculait le **momentum 24h** (`ret_6h`, input principal de S5/S9) sur
**96 h (4 jours)** au lieu de 24 h : `LB_RET_24H = 24*SCALE = 96` au lieu de `6*SCALE = 24`
(le commentaire disait pourtant « 1h grid: 24 »). Effet : S9 (fade move extrême) se
déclenchait **5-6× trop souvent** en 1h → entrées polluées. Corrigé. Audit des autres
lookbacks (7d/14d/30d/vol/consec) : **tous corrects**, un seul bug.

Après fix, les compteurs S9 de A et B matchent (3/3, 7/6, 10/10, 14/12, 21/18) → vraie
isolation de la cadence de sortie.

## Résultats (Δ B−A = effet de la cadence de sortie horaire)

| Fenêtre | A 4h | B (live) | Δ PnL | Δ DD |
|---|---|---|---|---|
| Alfred (06-10) | +6.5% / DD −18.3 | +22.4% / −9.8 | **+15.9pp** | **+8.5** |
| 1 mois | +13.6% / −26.2 | +57.0% / −20.0 | **+43.4pp** | **+6.2** |
| 2 mois | +65.2% / −19.1 | +74.7% / −17.7 | **+9.5pp** | **+1.4** |
| 3 mois | +79.8% / −17.7 | +83.3% / −16.8 | +3.5pp | +0.9 |
| 5 mois | +142.4% / −31.2 | +119.4% / −35.0 | **−22.9pp** | −3.8 |

**Positif PnL ET DD sur 4 fenêtres / 5.** Négatif seulement sur la plus longue (5 m).

Moteur du gain = **S5** (la strat qui saigne en bear), en **per-trade** (pas en volume) :
Alfred S5 A −$232 (−$10.5/trade) → B −$9 (−$0.3/trade) ; 2 m S5 A +$2.7/trade → B
+$5.8/trade. Les sorties horaires **coupent les perdants S5 plus vite** avant que le close
4h ne les laisse saigner → d'où le DD meilleur. CRV (winner coupé tôt, −$96 dans btlive)
était une **queue non-représentative** : sur le net, l'horaire sauve plus sur les perdants
qu'il ne lâche sur les gagnants.

## Conclusion

1. **Sortir à l'heure n'est pas une erreur — c'est un léger atout** (S5, DD). Passer le bot
   en sorties 4h **dégraderait** (4/5 fenêtres). **Ne pas toucher la cadence de sortie.**
2. **L'écart live-vs-BT n'est donc PAS la cadence** (elle est même un tailwind) — il vient
   de l'arbitre, des stops manuels, des fills réels et de la variance des quelques trades.
   Le BT 4h **sous-estime** légèrement ce que la cadence horaire délivre.

## Réserves

- **5 mois négatif** : compounding-path (B a un PnL S5 $ plus haut mais un % géométrique
  plus bas — mix/séquence différents). « Horaire mieux » n'est pas bulletproof long-terme.
- **Confound résiduel** : B prend +5 à +22 S5 et moins de S10 que A — pas un bug, effet
  réel de la grille fine (`range_pct`, squeeze, dispersion sur bougies 1h). Isolation
  très bonne mais pas parfaite.
- Archive 1h limitée à ~200 j (pas de plein-cycle 28 m), donc pas de walk-forward strict.

Sources : `backtests/backtest_rolling_1h.py`, `run_window(entry_align_hours=…)`,
scripts de comparaison dans le scratchpad de session.
