# MC joint des seuils de sortie — lecture de forme (2026-07-05)

Script : `python3 -m backtests.exit_joint_montecarlo` (35 scalaires actifs
perturbés ENSEMBLE, U(1±δ) i.i.d., seed 42). Résolution : 200 draws (12m/3m),
100 (28m) — à p95 sur 200 draws, le rang se joue à ~10 gagnantes (sensible au
bruit) ; les écarts 3m (p30-38) sont hors de portée du bruit binomial.

| Sweep | Base | Rang base | Méd. perturbée | Gap méd. | p5 | Perdants | DD base / méd |
|---|---|---|---|---|---|---|---|
| 12m ±10 % | 3851 | **p98** | 3287 | −15 % | 2656 | 0 % | −46.5 / −45.5 |
| 12m ±20 % | 3851 | **p95** | 3163 | −18 % | 2445 | 0 % | −46.5 / −45.9 |
| 3m ±10 % | 620 | **p38** | 655 | +6 % | 542 | 1 % | −27.6 / −33.9 |
| 3m ±20 % | 620 | **p30** | 750 | +21 % | 486 | 6 % | −27.6 / −30.7 |
| 28m ±20 % | 8451 | **p65** | 8208 | −3 % | 7485 | 0 % | −39.7 / −34.8 |

## Verdict (grille verrouillée AVANT les derniers sweeps)

**Branche 2 : le rang danse selon la fenêtre → du RÉGIME dans les
paramètres, pas une mémorisation structurelle de toute la période.**

- La mémorisation vit dans le **12m** (p98/p95) — précisément la fenêtre où
  la quasi-totalité des walk-forwards récents ont été calibrés (le MC
  retrouve la scène du crime).
- Le **28m** est un plateau bosselé (p65, gap méd. −3 %) : sur la pleine
  période, le fin-réglage ne porte quasi rien.
- Le **3m** est SOUS la médiane de sa propre boule (p30-38) : sur le régime
  récent, la config exacte fait légèrement MOINS bien que le centre de son
  voisinage — le fin-réglage y est contre-productif. Seule fenêtre où la
  boule touche le rouge (6 % de draws perdants à ±20 %) — cohérent avec le
  DSR ≤ 0.28 du chantier ablation (3m sans puissance).
- Bénin dans l'absolu : aucun draw perdant sur 12m/28m, plancher p5 à
  63-89 % de la base, « meilleures voisines » à +1 % (bruit, pas trésor).

## Conséquences actées

1. **Espérance de PLAN = médiane perturbée**, pas la meilleure brute :
   ~3200 sur 12m (haircut fin-réglage ~15-18 %), ~8200 sur 28m (−3 %).
2. **Interdiction de re-centrer** les paramètres vers la médiane (re-fit au
   carré). Remèdes = soustraction : vol-targeting (tue des axes entiers),
   arrondi des constantes baroques (au BACKLOG — la décimale de 3.25 n'a
   jamais rien porté si ±20 % joint laisse 6.5× sans perdant).
3. La config 3892 (12m ±20 %) n'a RIEN de spécial — un glint de tirage, on
   ne recentre pas la monture dessus.
4. **Calmar 28m (la trouvaille la plus méchante, revue)** : la base gagne
   +3 % de rendement sur sa médiane perturbée en payant +14 % de drawdown
   (−39.7 vs −34.8). En risk-adjusted, la médiane perturbée BAT la base de
   ~10 % : le vernis de calibration n'est pas seulement inutile sur la
   pleine période, il est Calmar-négatif.
5. **Métrique de succès (verrouillée, remplace « rang 12m vers p70-85 »)** :
   le SPREAD des rangs inter-fenêtres — être sous sa boule (3m p30) est
   aussi pathologique qu'au sommet (12m p98), c'est le même tilt dans
   l'autre sens. Aujourd'hui : p98 − p30 = **68 points**. Cible : **< 30**
   avec médianes tenues. (Analogie actée : on ne juge pas une collimation
   sur la FWHM au centre, on la juge sur des étoiles rondes jusque dans les
   coins.)
6. **Ordre des chantiers (acté)** : arrondi AVANT vol-targeting — l'arrondi
   préserve la topologie (re-MC 12m avant/après directement comparable =
   lecture propre de ce que portaient les décimales) ; le vol-targeting
   change la dimensionnalité (les boules ne sont plus le même objet).
   Séquence : arrondi → re-MC 12m (200 draws) → vol-targeting → re-MC
   complet contre la grille. Test en cours : `round_constants_test.py`.
7. Rappel de priorité (revue) : le MC mesure l'ESTIMATION, pas la SURVIE —
   0 % de perdants dans la boule dit que la crête est large, mais le risque
   de ruine vit hors échantillon (flush corrélé, gap au-delà des stops).
   Le frein portefeuille (v1.11.0, committé, EN ATTENTE DE RESTART) vaut
   plus que les 15 % de vernis.
