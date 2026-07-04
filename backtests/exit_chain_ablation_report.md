# Chaîne de sorties — rapport d'ablation (2026-07-04)

Question : combien des 14 règles sont de la stratégie, combien sont des
souvenirs de séries perdantes déguisés en logique. Trois volets (census
empirique / ablation BT canonique / fragilité ±10-20 % + honnêteté
statistique), sémantique v1.8.0. **Zéro auto-tuning — le juge parle en
dernière page, les findings sont l'annexe.**

Reproductible : `exit_census.py` · `backtest_rule_audit.py` ·
`exit_fragility.py` (+ dumps JSON dans `backtests/output/`).

---

## LA DERNIÈRE PAGE D'ABORD — table de condamnations

| # | Règle | Verdict | Attendu(s) |
|---|-------|---------|------------|
| 1 | catastrophe_stop | **GARDER** (backstop, hors débat) | sensibilité de chemin ±10 % = 42-61 % d'equity : constante porteuse, ne pas retoucher sans cérémonie |
| 2 | timeout / holds | **GARDER** | hold_S9 ±10 % → 106 % d'equity de swing (chemin) — même cérémonie |
| 3 | opp_floor | **GARDER (assurance DD)** | PnL erratique mais ΔDD **+19.6/+21.8 pp** sur 6m/3m ; lock_ratio fragile 6m → re-mesurer au trimestre |
| 4 | manual_stop | **GARDER** (canal user/LOCK, pas une règle) | — |
| 5 | s9_early | **RE-MESURER** | apport ≤0 sur 3/4 mais minuscule (−37/−15/0/0 $) ; retrait ne passe pas strict ; coût de garde ≈ 0 |
| 6 | s10_trail | **RE-MESURER post-v1.8.0** | coûtait +107/+236 $ (28m/12m) à l'ablation — mais c'était la version TICK ; la version close-4h est une autre règle, la re-juger avant sentence |
| 7 | s8_dead_in_water | **GARDER — PARITÉ CORRIGÉE (v1.8.0)** | la plus confirmée (+604/+412/+148/+203 $, DD mieux) et pourtant 0 tir live : condition MFE≤50 **inatteignable au tick** (16/16 gonflés >50 ; en close-4h : 4/16 = 25 % = taux BT exact). v1.8.0 lit le close-MFE aux frontières → doit se mettre à tirer. **Surveiller ses premiers tirs live.** |
| 8 | s8_inlife | **GARDER** | confirmée (+121/+71/+18/0) ; bucket bear fragile (act ET off) → ne pas retuner, re-mesurer |
| 9 | prop_trail | **GARDER** (cheval de trait) | 91 tirs live +521 $ ; arm fragile sur 12m ; v1.8.0 l'améliore |
| 10 | traj_cut | **GARDER mais 2 constantes SUSPECTES** | WF glissants antérieurs tiennent ; MAIS decline_rate et time_since_mfe **fragiles ±10 %** (363→137/67 $) et 2 autres params **no-op** (jamais réellement testés) : l'odeur du curve-fitting sur les constantes de temps. TRAJ_CUT_EFF hebdo surveille ; re-mesurer avant tout retune |
| 11 | s9_early_dead | **GARDER** | confirmée petite ; même famille de parité MFE que s8_dead (adoucie v1.8.0) ; basse fréquence réelle (9 tirs BT/28m) |
| 12 | btc_drop_cut | **RETRAIT CANDIDAT — au user** | le vrai pansement : −178/−290/**−825**/+209 $, DD **dégradé** 2 fenêtres, et la défense-assurance est réfutée par les faits : 10 bougies-krach sur le 6m (il y avait des incendies), 13/26 de ses coupes récupèrent au timeout sans elle (+69 $ direct/28m), chevauchement catastrophe 31 %, live 14 tirs −59 $ (même signe). Retrait = 3/4 (échoue 3m −209, fenêtre à 2 bougies-krach). Options : (a) retirer sur faisceau ; (b) attendre une 4ᵉ fenêtre glissante. |
| 13 | dead_timeout | **ENTERRÉE** (déjà retirée v1.4.0) | correction du chantier-4 du 02-07 : je l'avais déclarée « filet live réel » — faux, le tir PYTH précédait le retrait, et l'ablation Δ+0.0 comparait OFF vs OFF. Le doc ne peut plus mentir : bloc §8 **généré depuis le code** + hook pre-commit |
| 14 | runner_ext | **GARDER dormante** | 0 tir live depuis toujours ; tiroir : masquée (prop_trail sort les S9 avant le timeout) + ratio ur/MFE biaisé par le MFE tick (v1.8.0 l'adoucit → peut se réveiller) ; retrait déjà refusé au strict (2/4). Re-census au trimestre |

**Tiroirs des trois jamais-tirées** (aucun cadavre sans tiroir) :
s8_dead → *inatteignable-parité*, corrigée v1.8.0 ; s9_early_dead →
*parité partielle + basse fréquence* ; runner_ext → *masquée + biais de
ratio*, dormante assumée.

---

## Annexe A — Census empirique (volet 1)

377 trades clos, 2026-03-26 → 2026-07-03 (≈ 3,3 mois, PAS 6 — l'archive
live ne remonte pas plus loin). Détail complet : `python3 -m
backtests.exit_census`. Points saillants :
- prop_trail 91 tirs +521 $ (+206 bps/tir) ; manual_stop_set 38 tirs
  +378 $ (user era ≤17-06, puis LOCK IA) ; traj_cut 22 tirs −225 $ ;
  catastrophe 21 tirs −668 $ ; timeout 148 tirs −29 $.
- **0 tir live** : s8_dead_in_water (vs 42 tirs BT/28m, 25 % des S8 !),
  s9_early_dead (vs 9), runner_ext (0 event, toutes époques).
- BT même fenêtre (dumps base) : ordre de fréquence compatible sauf les
  règles à condition MFE≤X (parité tick, cf. verdict #7).

## Annexe B — Ablation BT (volet 2, config canonique re-datée)

Contribution marginale (stack − sans-la-règle), $ par fenêtre 28m/12m/6m/3m
+ ΔDD par fenêtre : `backtests/output/exit_ablation.json`. Verdicts bruts :
4 CONFIRMÉES (s8_dead, prop_trail, s9_early_dead, s8_inlife), 2 PANSEMENT?
(btc_drop_cut, s9_early), 4 neutres (s10_trail, traj_cut, runner_ext,
opp_floor). Rappel de méthode : l'ablation single-end-date DÉTECTE — la
sentence exige fenêtres glissantes + strict (leçon runner_ext 5/7 vs 2/4).

## Annexe C — Fragilité ±10/±20 % (volet 3)

36 scalaires × 4 facteurs × 4 fenêtres (576 runs, parité vérifiée).
- **9 fragiles** : s8_inlife[bear]×2, prop_trail[arm], s9_early_dead_t_h,
  btc_drop_cut_ret(3m), opp_floor_lock_ratio(6m), traj_cut_decline_rate,
  traj_cut_time_since_mfe, runner_ext_hours.
- **4 no-op intégraux** (perturbation sans AUCUN effet = seuil jamais testé,
  pas « robuste ») : s8_inlife[bull][off], traj_cut_at_mae_slack,
  traj_cut_min_loss, runner_ext_min_cur_to_mfe. +1 no-op à ±10 % seulement
  (s8_dead_mfe_max — quantisation de grille).
- Sensibilité de chemin (stops/holds, non ablatables) : stop_s8 ±10 % →
  61 % d'equity ; hold_S9 → 106 %. Ce n'est pas du curve-fit (c'est le chaos
  de chemin) mais ces constantes sont porteuses.

## Annexe D — Honnêteté statistique

| Fenêtre | n trades | n_eff (concurrence 1.9) | n_eff/DoF (38) | SR/trade | DSR N=44 | N=500 | N=2000 |
|---|---|---|---|---|---|---|---|
| 28m | 1352 | ≈697 | 18.3 | 0.146 | 1.00 | 0.99 | 0.98 |
| 12m | 573 | ≈304 | 8.0 | 0.140 | 0.91 | 0.63 | 0.44 |
| 6m | 281 | ≈154 | 4.1 | 0.098 | 0.28 | 0.08 | 0.03 |
| 3m | 158 | ≈82 | 2.1 | 0.102 | 0.17 | 0.04 | 0.01 |

Lecture : **l'edge est statistiquement réel sur 28 mois même en supposant
2000 configurations essayées (DSR 0.98)** ; sur 6m/3m, la performance est
indistinguable de la chance du multiple-testing (DSR ≤ 0.28) — l'époque
récente ne prouve NI ne réfute, elle manque de puissance. Cohérent avec le
forward flat et la doctrine « le futur est le seul OOS ». DoF : 36 scalaires
Params + 2 hardcodés (stop S9 adaptatif, signals.py:184).

## Réponse à la question posée

Le gros de la chaîne est de la stratégie : le squelette (stops, timeout,
prop_trail, s8_dead, s8_inlife) survit à l'ablation, aux perturbations et au
Sharpe déflaté sur le long terme. Les « souvenirs de séries perdantes » ont
des noms : **btc_drop_cut** (pansement du krach, coûteux et redondant à
31 % avec le stop), les **constantes de temps de traj_cut** (fragiles ou
jamais testées — la règle tient, ses chiffres exacts sont suspects), et
**dead_timeout** (déjà enterrée, mais qui hantait encore la documentation et
mon propre audit). La découverte structurelle du chantier : la famille
MFE≤X était **aveugle en live par le même bug de grille que les trails** —
et v1.8.0 l'a réparée avant même qu'on la diagnostique.
