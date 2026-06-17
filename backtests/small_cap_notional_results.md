# Cap notionnel pour petits comptes (<$100) — walk-forward

**Date** : 2026-06-17 · **Script** : `backtests/backtest_small_cap_notional.py`
· **Log** : `analysis/output/small_cap_sweep.log` · **Backlog** : clôt `c1f4901`.

## Contexte

Nouveau bot `baby` à capital <$100 (modèle agent API, tierce personne). Mécaniquement
viable (min ordre HL $10, `base_size` planché à $10). Mais le cap `max_notional_per_trade`
($500 dans `Params`) est **dormant** sous ~$850 → à $80 un seul S5/S9 sature la marge
(2× capital) avant de remplir les 6 slots. Hypothèse : un cap **bas** écrête ces gros
trades → libère des slots → meilleure capture S8/S9.

## Méthode

Sémantique **aligned** (appel canonique identique à `backtest_rolling.py` : `aligned=True`,
toutes les règles d'exit courantes via `evaluate_exit`, `apply_adaptive_modulator=True`,
`margin_check=True`). Le param `max_notional_per_trade` de `run_window` s'applique
**par-dessus** le cap interne $500 (dormant à <$100) — c'est lui qui mord. Baseline =
`None` (config live actuelle à $80). 4 splits 6m non-recouvrants. Capitaux $80 et $100.

**Critère strict 4/4** : vs baseline, ΔPnL ≥ 0 **ET** ΔDD ≥ −2pp (DD pas pire de +2pp)
sur **les 4 splits**. `max_dd_pct` est signé négatif (−42% = −42.0).

## Résultats (@ $80 ; $100 quasi identique car sizing proportionnel)

| Config | ΔPnL+ /4 | ΔDD ok /4 | Tot margin-skips | Tot S8+S9 | Verdict |
|--------|:--:|:--:|:--:|:--:|--------|
| A_baseline (None) | — | — | **4328** | **155** | référence |
| B_cap50 | 2 | 4 | 1350 | 213 | FAIL (2/4 PnL) |
| C_cap40 | 1 | 3 | 825 | 224 | FAIL (1/4 PnL) |
| D_cap30 | 1 | 4 | 121 | 232 | FAIL (1/4 PnL) |

@ $100 : B 1/4 PnL, C 1/4, D 1/4 — même conclusion.

### Lecture par split (cap50 @ $80, ΔPnL pp / DD baseline→cap50)
- split_1 (24m→18m) : **−98,6pp** PnL / DD −55,4→−56,2 (≈flat). Fenêtre de gros gagnants écrêtés.
- split_2 (18m→12m) : **+100,8pp** PnL / DD **−42,7→−22,3** (très amélioré). Chop : le cap protège.
- split_3 (12m→6m)  : **−271pp** PnL / DD −49,9→−36,2 (amélioré). Fenêtre haussière, upside écrêté.
- split_4 (6m→now)  : **+4,4pp** PnL / DD −30,4→−29,0 (≈flat).

## Conclusions

1. **Mécanique validée** : le cap bas effondre les rejets de marge (4328 → 121 à cap30)
   et augmente la capture S8+S9 (155 → 232). La libération de slots est **réelle**.
2. **DD** : le cap bas est **neutre-à-meilleur** — cap50 et cap30 passent le critère DD
   sur les 4 splits. Il réduit nettement le drawdown en marché choppy/baissier.
3. **PnL** : il **sacrifie le PnL** sur les fenêtres de tendance (split_1, split_3) où les
   gros gagnants non écrêtés courent. C'est l'arbitrage exact anticipé par `c1f4901`.
4. **Verdict strict** : **aucune config ne passe 4/4** (PnL bloquant). Per doctrine projet,
   pas de déploiement d'override sur cette seule base.

## Décision (proposée)

- **Défaut rigoureux** : déployer `baby` **sans override** (`overrides: {}`, cap $500 interne
  dormant = config live standard). Mécaniquement viable, conforme à la doctrine 4/4.
- **Alternative risk-managed (choix utilisateur)** : `max_notional_per_trade: 50` est un
  **réducteur de drawdown** assumé (DD 4/4 ok, PnL trimé sur 2 fenêtres de tendance).
  Défendable pour un compte tiers <$100 qui priorise la préservation du capital sur la
  maximisation du compounding. À trancher par l'utilisateur — appétit au risque du tiers.

## ⚠ Réalité « viabilité » à signaler

À <$100, la suite complète de stratégies encaisse des **drawdowns de −30% à −55%** selon
la fenêtre, quel que soit le cap. Le cap les atténue (jusqu'à −17% en chop) mais ne les
supprime pas. « Viable » = trade correctement et rend bien en % sur l'ensemble des fenêtres,
mais avec des DD élevés inhérents au petit capital + 2× levier.
