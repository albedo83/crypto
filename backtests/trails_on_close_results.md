# Chantier trails-sur-close — validation 3 phases (2026-07-04)

**Problème** (btlive + test qualité par trade) : gagnants réels +236/306 bps
vs BT +562 à WR égal/meilleur, pertes comparables — TOUT le déficit d'edge
est du côté des runners. Cause : les règles trail (opp_floor, s10_trail,
s8_inlife, prop_trail) validées sur grille 4h-close mais évaluées live toutes
les 20 s sur marks bruités → pic gonflé + croisements déclenchés par les wicks.

## Gates pré-enregistrées et résultats

### C — Prémisse (inflation du pic) : VERTE (limite)
`trades.mfe_bps` (tick) vs MFE recalculé sur closes horaires (ticks réels),
215 trades : inflation médiane +45.3 bps (famille trail +35.6, p90 +82-123).
Matérielle mais partielle — le 2ᵉ mécanisme (croisement par wicks) n'est
mesurable qu'en contrefactuel.

### B — Bénéfice réel (contrefactuel sur nos trades) : VERTE
98 sorties trail réelles (4 bots) rejouées via `rules.evaluate_exit` sur
barres construites depuis les ticks réels, stops aux extrêmes tick :
- **@1h-close : Δ +52.4 bps/sortie, IC95 [+10,+95]**, +120.89 $ flotte/3.5 sem
- **@4h-close (sémantique shippée, code `trail_gate` exercé) :
  Δ +97.9 bps, IC95 [+4,+217], +191.70 $** — 26 contrefactuels courent au
  timeout (vs 1 @1h) : les runners respirent.
- 4 bots positifs dans les deux variantes (paper-contrôle sans IA inclus).
- Honnête : pas un free lunch — UNI/DYDX rendent leurs gains en traj_cut
  (−670/−550 bps), MINA/AAVE courent à +810/+749. Le NET est positif.
- Caveats : btc_z gelé à l'entrée, pas d'effet slot/compounding, 3.5 sem de ticks.

### A′ — Non-nuisance BT (isolation propre) : VERTE, 7/7
L'A-bundle initial (grille 1h complète vs 4h) était inconclusif (3/7, ±47 pp
= bruit de chemin — le 4/5 de juin a flippé en ajoutant 3 semaines).
Isolation via `trail_eval_every` (même grille 1h, mêmes features, mêmes
entrées, SEULE la cadence trail varie) : **trails-4h-close bat trails-1h sur
7/7 fenêtres glissantes** (+5.8 à +222.8 pp) avec DD meilleur ou égal 6/7.
La monotonie « plus grossier = mieux » tient même sans bruit tick.

## Verdict : SHIP trails-aux-clôtures-4h (v1.8.0)

Convergence exacte vers la sémantique canonique (le BT évalue les trails
1×/bougie 4h sur closes depuis toujours — **zéro changement BT, zéro
re-baseline**). Implémentation :
- `rules.evaluate_exit(trail_gate=True)` — False saute les 4 règles trail
  (coupe-pertes/stops/timeout JAMAIS gatés). Défaut True = historique.
- Bot : `Position.mfe_trail_bps/_at_h` échantillonnés au 1er tick après
  chaque clôture 4h ; trails évalués à ce tick seulement, sur ce MFE.
  Boot mi-bougie → attend la prochaine clôture (= BT).
- Kill-switch : `trail_eval_4h_close=False` (settings/overrides).
- Hook BT : `run_window(trail_eval_every=N)` (R&D, défaut 1 = parité,
  vérifiée bit-identique +15.39 %).

**Métrique de succès forward** : la taille moyenne des gagnants réels doit
remonter de +236/306 vers +562 bps (suivi via `measure_costs_by_signal` /
btlive). Échec = kill-switch + retour tick.

## Ce que ça ne corrige PAS
La dispersion de chemin (±47 pp entre fenêtres BT adjacentes, ×7 sur départs
décalés) et l'artefact cap-$500×capital restent — le BT est une bande, pas
une promesse. Ce chantier ferme le biais unilatéral identifié, pas le gap
entier.
