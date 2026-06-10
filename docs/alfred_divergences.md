# Alfred phase 1 — divergences bot vs backtest (audit du câblage)

Recensement exhaustif des divergences de sémantique d'exécution entre le bot
live (`analysis/bot/trading.py`) et le moteur backtest officiel
(`backtests/backtest_rolling.py`) découvertes lors du câblage des deux côtés
sur le noyau pur `alfred/rules.py` (2026-06-10).

**Statut : PHASE 6 ACTÉE le 2026-06-10.** Les divergences #1/2/3/5/6/7/8/9/10
sont alignées sur la sémantique live dans le run officiel (`run_window(aligned=True)`,
défaut de `main()`). MKR retiré de l'univers (settings.py + backtest_genetic.TOKENS).
Anciens chiffres archivés dans `docs/backtests_legacy_pre_phase6.md` ; attribution
complète de l'impact (~34× d'inflation sur 28m, driver = cap notionnel #5) dans
`docs/alfred_phase6_preview.md`. Échappatoire d'archéologie :
`BACKTEST_LEGACY_SEMANTICS=1`. Les divergences #4 (manual_stop — action utilisateur,
non simulable), #12 (coûts — granularité documentée) et #13 (MFE/MAE ticks vs
candles — granularité) restent ouvertes par nature.

Historique phase 1 : sémantique legacy conservée pour la validation iso-résultat
(32/32 fenêtres identiques trade-à-trade à ε=$0.01, `backtests/compare_trade_dumps.py`).

| # | Divergence | Live | Backtest (legacy, conservé) | Où c'est encodé |
|---|---|---|---|---|
| 1 | Prix d'exit des règles trail/early (`s9_early_exit`, `s10_trailing`, `s8_inlife`) | Prix synthétique du niveau de déclenchement | Close de la bougie | flag `synthetic=False` dans les appels de `backtest_rolling` |
| 2 | Priorité stop vs timeout dans la même période | `timeout` testé avant `catastrophe_stop` (à 20s, indistinguable) | stop d'abord (le franchissement intra-bougie précède le tick de timeout) | ordre des appels dans chaque chaîne ; `rules.evaluate_exit` canonique adopte stop-d'abord via `worst_bps` (granularité-aware) |
| 3 | `prop_trail` (v12.11.0, S9 bull) | Actif | **Absent du run officiel** — `main()` ne passait pas `proportional_trail` ; la règle n'a jamais été simulée dans `docs/backtests.md` | non appelé dans la chaîne legacy ; présent dans `evaluate_exit` canonique |
| 4 | `manual_stop_usdt` | Actif (override utilisateur) | Absent (pas d'action utilisateur simulée) | non appelé dans la chaîne legacy — voulu, pas un bug |
| 5 | Cap notionnel | `MAX_NOTIONAL_PER_TRADE=500` appliqué **après** le modulateur | `BACKTEST_MAX_NOTIONAL=20000` appliqué **avant** le modulateur (le modulateur ×2.5 peut donc dépasser le cap) ; le cap live $500 n'est pas simulé | `strat_size()` de backtest_rolling (cap dans `base_size`) vs `rules.position_size` |
| 6 | Arrondi du sizing post-modulateur | `round(size × mult, 2)` | pas d'arrondi | commentaire dans le bloc modulateur de backtest_rolling |
| 7 | Floor $10 post-modulateur | SKIP `modulator_floor` si size < $10 | entre quand même | `check_size_floor=False` dans l'appel `entry_skip_reason` du BT |
| 8 | Force de ranking S10 | `1000 / squeeze_range` (squeeze serré prioritaire) | constante `1000` | override dans la construction des candidats du BT |
| 9 | Sémantique btc_z manquant | `None` → règles régime sautées | map non-vide + ts absent → `z=0.0` (bucket neutral actif) | construction du `MarketCtx` côté BT (`btc_z_map.get(ts, 0.0) if btc_z_map else None`) |
| 10 | Calcul du btc_z | `features.compute_btc_z` à chaque scan (fenêtre glissante sur le deque) | map vectorisée précalculée dans `run_window` (+ variantes v12.18.x) | **RÉSOLU 2026-06-10** (`backtests/test_btc_z_parity.py`) : cause = off-by-one de fenêtre — le BT slice `rets[j-n_zw : j+1]` = n_zw+1 observations, le bot en prend n_zw. Fenêtre corrigée → parité EXACTE (Δ=0 sur 500 candles). Écart legacy max ~2.4e-03, 0/500 > 0.05 → immatériel. Alignement (slice `j-n_zw+1 : j+1` côté BT) à acter en phase 6 |
| 11 | Garde-fou taille historique squeeze | `len < W+R+2` → None | `idx < W+R+2` → None (off-by-one) | `signals.detect_squeeze_at` utilise la garde BT (ne se déclenche jamais en pratique : historiques ≫ 7 bougies) |
| 12 | Coûts | 10 bps flat (taker 9 + funding 1), funding réel swappé en live | 13 bps (taker 9 + slippage 4) + funding intégral historique par trade | `COST` dans backtest_rolling vs `Params.cost_bps` |
| 13 | MFE/MAE | ticks 20s (mark) | high/low de bougie 4h | `rules.update_excursions` vs `rules.candle_excursions` — granularité, pas un bug |
| 14 | Hooks R&D du moteur | n/a | les hooks (`giveback`, `trailing_extra`, `early_mfe_exit`, `inlife_exit_extra`…) restent interposés aux mêmes points de la chaîne | inchangé |

## Décisions d'alignement proposées (à acter avant la phase 6)

- **#1, #2** : adopter la sémantique canonique (`rules.evaluate_exit`) dans le
  BT — prix synthétiques + stop-d'abord. Plus fidèle à l'exécution réelle.
- **#3** : activer `prop_trail` dans le run officiel (la règle tourne en live
  depuis v12.11.0, le BT de référence doit la simuler).
- **#5, #6, #7** : adopter `rules.position_size` (cap $500 post-modulateur,
  arrondi, floor $10) — le BT simulera enfin le cap live.
- **#8** : adopter la force live (1000/range).
- **#9, #10** : unifier sur `features.compute_btc_z` + test de parité.
- **#12, #13** : conserver (différences de granularité documentées, pas des bugs).

Chaque alignement change les chiffres → ils seront actés ensemble lors de la
remise à zéro (phase 6), avec re-run complet et archivage des anciens chiffres.

## Validation phase 1 (2026-06-10)

- **Iso-résultat** : `python3 -m backtests.compare_trade_dumps
  backtests/output/alfred/reference_trades_pre_alfred.json
  backtests/output/alfred/iso_trades_alfred.json` → 32/32 fenêtres identiques
  (mêmes trades, mêmes raisons, mêmes P&L à $0.01 près, même DD).
- **Parité features** : `python3 -m backtests.test_feature_parity 800` →
  800/800 tirages identiques entre `backtests.backtest_genetic.build_features`
  et `alfred.features.compute_features` sur les champs consommés par les
  signaux (ret_24h/ret_42h/drawdown/vol_z/vol_ratio/range_pct/vol_7d/vol_30d).
- **Parité settings/sizing** : assertions `alfred.settings`/`alfred.rules` vs
  `analysis.bot.config` (valeurs, `strat_size`, `get_adaptive_alpha`).
