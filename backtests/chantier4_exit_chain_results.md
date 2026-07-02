# Chantier 4 — audit chaîne de sorties : résultats (2026-07-02)

Script : `python3 -m backtests.chantier4_exit_chain_audit` (+ strict 4 fenêtres
en scratchpad). Suite des follow-ups de l'ablation 2026-06-11
(`backtest_rule_audit.py`). Config canonique (aligned + margin + mfe_on_close).

## Verdicts : les 3 suspects SURVIVENT — aucune règle retirée

### 1. S10 whitelist (v11.3.4) — KEEP
Retrait (SHORT-only conservé, univers élargi) : gagne **3/7** tranches OOS 6m
glissantes, dispersion violente (+42.3 → **−152.4pp**). Le quasi-PASS de
l'ablation de juin (+610$ sur 28m) était un **mirage single-end-date** —
3ᵉ confirmation du pattern MINA ([[project_drop_mina_walkforward]]).

### 2. dead_timeout (v11.7.2) — KEEP (filet live réel)
- **BT : morte.** 0 tir sur 28m dans le stack actuel — entièrement shadowée
  par s9_early_dead (12h/150), s8_dead (8h/50), traj_cut, btc_drop_cut,
  arrivés APRÈS elle dans l'histoire. Δretrait = +0.0pp sur 7/7 tranches.
  (L'ablation de juin la donnait non-nulle : sémantique pré-mfe_on_close.)
- **Live : vivante.** PYTH 2026-06-19 (S5 LONG, MFE=0, MAE=−1000, 36h,
  répliqué 3 bots) — un cas réel que ni traj_cut (déclin 20 bps/h < 100)
  ni les autres cuts ne couvraient. Coût de la garder = complexité seule.

### 3. runner_ext (v11.7.32) — KEEP, mais **dormante** (le photon pur)
- **Jamais tirée en réel** : 0 event RUNNER_EXT sur les 4 bots Alfred ET tout
  l'historique legacy. Validée au ship en sémantique legacy (+1790pp de
  l'époque, monnaie ~34× gonflée). Explication : MFE live mark-based bruité
  + prop_trail/early-exits sortent les S9 avant le timeout — les conditions
  (MFE≥1200 encore à 30% au timeout) ne se réalisent pas en réel.
- **Retrait : 5/7 glissant** (+70pp/28m, DD meilleur 6/7) MAIS **2/4 strict**
  (12m **−57.5pp** — la tranche bull-explosive fin-2026-01 porte les
  extensions). Substitution 28m : 37 S9 modifiés, la règle les aide en direct
  (+51$) ; le gain du retrait = libération de slots (compounding), régime-
  dépendant. → même classe que bear-derisk/cooldown-variants : pari de
  régime, pas un edge. La doctrine (strict 4/4 pour TOUT changement, retrait
  inclus) dit KEEP.

## Le recensement de la preuve (état des photons)

243 sorties réelles (4 bots + legacy v12.9.0+) : prop_trail 85 (+484$),
timeout 49, manual_stop 36 (+306$), traj_cut 22 (−225$, surveillé
TRAJ_CUT_EFF), catastrophe 12, s10_trail 6, dead_timeout 3 (=1 événement),
opp_floor 3, s8_inlife 2, s9_early 2, **s8_dead_in_water / s9_early_dead /
runner_ext : 0**. → **7/14 règles portent ≤3 observations live** : la moitié
basse-fréquence de la chaîne n'a pour preuve que le BT. Pas un défaut
actionnable (basse fréquence ≠ overfit), mais une réalité à garder en tête :
le BT REJETTE, ne promet pas.

## Conclusion

La chaîne n'est pas « bâtie en fittant des photons » au sens destructeur :
10 règles confirmées à l'ablation de juin + les 3 suspects survivent à
l'examen dédié. Les vrais constats photon : runner_ext ne s'est jamais
matérialisée (dormante, inoffensive), dead_timeout vit sur UN cas live
(mais le bon), et la moitié de la chaîne reste BT-only en preuve.
Re-audit trimestriel (`backtest_rule_audit.py`) : prochain ~2026-09.
