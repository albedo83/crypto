# Slow-bleed cut S5 LONG — R&D (NON SHIPPÉ : réfuté en aligned)

**Date** : 2026-06-17 · **Statut final : RÉFUTÉ, rien shippé.**
· Scripts : `backtests/backtest_s5_slow_bleed.py` (legacy, rejouable)
· Logs : `analysis/output/s5_slow_bleed.log` (legacy), `analysis/output/s5_slow_bleed_aligned.log` (aligned)

## Origine
Live senior : S5 LONG = 70 % WR mais **payoff 0.63**, book net +$25 porté uniquement
par 2 outliers (CRV +$48, UNI +$18). Pire perdant WLD à MAE **−974 bps**, échappé à
traj_cut (chute brutale) ET dead_timeout (MFE 160 > cap 150) → hémorragie LENTE de 42 h,
coupée à la main. Hypothèse : règle « S5 LONG tenu ≥ X h ET cur ≤ −Y bps » qui rattrape
les bleeds lents sans toucher les runners.

## Étape 1 — walk-forward LEGACY (incrémental au-dessus de traj_cut) : PROMETTEUR
Méthode : mode legacy + hook (comme traj_cut a été validé). Baseline = legacy + dead_timeout
+ traj_cut ; variante = + slow_bleed. Grille hold×loss×{bear,uncond}.
Résultat : **uncond_h36_l500 STRICT 4/4** (ΔPnL +2250/+1728/+372/+127 pp, DD intact).
Lecture : le hold 36 h semblait être le filtre clé, régime-agnostique.

## Étape 2 — confirmation ALIGNED (gate de ship) : RÉFUTÉ ❌
Implémenté `slow_bleed_rule` dans rules.py + Params kill-switch (OFF défaut), testé dans
la VRAIE config live (aligned : prop_trail + s8 rules + dead_timeout + traj_cut + sizing
aligned). **Baseline aligned reproduit exactement la config canonique (zéro régression).**

Verdict (Δpp vs baseline aligned, base 28m = +819.9 %) :

| config | PnL/4 | sumΔPnL | note |
|--------|:--:|--:|------|
| **abl_hold0_l500** (couper à −500 SANS attendre) | **4/4** | **+177.6** | ablation null — le PLUS gros gain |
| h30_l600 / h36_l600 / h42_l600 | 4/4 | +54 / +16 / +5 | l600 = quasi no-op (12-51 fires, gain ~0) |
| **h36_l500 (gagnant legacy)** | **3/4** | +13.3 | **échoue 3m** ; gain = bruit |
| abl_h36_l50 (couper à 36h à −50) | 3/4 | +59.7 | échoue 12m |

**Conclusions** :
1. **L'hypothèse est réfutée** : h36_l500 ne passe pas (3/4) dans la vraie config.
2. **Le null-test détruit l'hypothèse du hold** : `abl_hold0_l500` (AUCUN hold) passe 4/4
   avec le plus gros gain → le « tenu ≥ 36 h » **ne porte aucun signal positif** en aligned.
3. **Magnitudes minuscules** (+32 pp sur base +820 % ≈ +4 % relatif) : la chaîne d'exit
   aligned actuelle (prop_trail, s8, dead_timeout, traj_cut) **capte déjà** ces perdants.
   Le legacy sur-promettait (+2250 pp) parce que sa base était plus pauvre.
4. Pas de plateau cohérent — les rares pass sont des no-ops (l600) ou le null (hold0).

→ **Rien shippé.** Code de prod reverté (rules.py / settings.py / backtest_rolling.py
vierges). C'est précisément le rôle du gate aligned : un edge legacy peut être une
illusion créée par une baseline incomplète.

## Indice résiduel → BACKLOG
`abl_hold0_l500` (= stop S5 LONG serré à −500 bps, immédiat) passe 4/4 en aligned, mais
c'est un **pick post-hoc parmi 11 configs** (faible preuve, magnitude faible). NE PAS
adopter tel quel. Si on veut le creuser : test DÉDIÉ pré-enregistré du stop catastrophe
S5 LONG (sweep −400/−500/−600) + null-shuffle, comme tout stop. Consigné au BACKLOG.

## Méthodo retenue (réutilisable)
- **Le mode aligned ignore les hooks `inlife_exit_extra`** (sentinelle `__aligned_hold__`)
  → tester une NOUVELLE règle « au-dessus de la config live » exige de l'ajouter à
  rules.py derrière un kill-switch Params (OFF défaut), PAS via le hook.
- Toujours confirmer en aligned avant ship : le legacy gonfle et change les interactions.
