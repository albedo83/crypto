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

## Pré-enregistrement — lecture du re-MC arrondi (écrit AVANT les résultats, 07-05 ~07:45)

Fenêtres déjà connues (test 1) : 12m −7.8 % (sous la base ✓ prédit), 3m
+27 % (au-dessus ✓ prédit), 28m +12 % ET DD meilleur (prédit « plat » —
dépassé dans le bon sens), 6m −6.6 %. Signature directionnelle du vernis
12m : conforme sur 3/4, meilleure que prédite sur 28m.

**Reste à lire (re-MC, PAS ENCORE SORTI au moment d'écrire ces lignes)** :
1. Rang 12m du centre arrondi : la théorie du vernis prédit une DESCENTE
   vers p70-85 (moins de mémorisation).
2. **La moitié falsifiable que personne ne surveille : le rang 3m doit
   REMONTER vers p50** (sortir de sous sa boule). C'est le critère dur.
3. Tout dans le bruit partout = les décimales ne portaient rien → surface
   réduite gratis, victoire aussi.
4. Seul verdict qui invalide la théorie : un arrondi qui dégrade AUSSI le
   3m (rang 3m qui descend encore) — là le « vernis 12m » a un problème.

**Instrument prioritaire en cas de désaccord (décidé maintenant)** : le
test (2) — re-MC apparié mêmes seeds/mêmes boules — PRIME sur le test (1)
(Δ fenêtres vs bande du MC joint : mètre-étalon généreux, l'arrondi S1 est
un choc −11 % sur son axe, une vraie dégradation peut s'y planquer). Si (1)
dit « dans le bruit » et (2) dit « le spread ne bouge pas », c'est (2) qui
parle.

## Verdict re-MC arrondi (2026-07-05, lecture contre le pré-enregistrement)

**Ligne 1 — le chiffre qui requalifie l'opération : +1012 $ ET −3 points de
DD sur 28m.** Les décimales ne gonflaient pas seulement la mesure sur la
fenêtre-miroir : elles bavaient en négatif sur le reste du champ. Pas un
pixel chaud — du BLOOMING (saturation locale qui détruit les colonnes
voisines). L'arrondi est une RÉPARATION, pas du ménage : rendement et
drawdown améliorés ensemble sur la pleine période, Calmar gagnant des deux
côtés (la base exacte payait +14 % de DD pour +3 % de rendement vs sa
médiane). Nuance d'usage : Δ mesuré exactement (BT déterministe, apparié)
mais valeur = une DIRECTION, pas une promesse annualisable.

Résultats appariés (mêmes seeds/boules — l'instrument de précision) :

| Sweep | Rang base exacte | Rang centre ARRONDI | Note |
|---|---|---|---|
| 12m ±10 % | p98 | **p86** | une seule mesure lue en |
| 12m ±20 % | p95 | **p80** | deux binnings — UNE voix* |
| 3m ±10 % | p38 | **p32** | plat (bruit de rang) |
| 3m ±20 % | p30 | **p40** | +10 pts, loin de p50 |

\* Épistémique (revue) : le rang d'un centre ne mesure que son Δ$ projeté
sur la CDF de sa boule — une fois le −301 $ connu, p86/p80 sont quasi
mécaniques. Les deux rayons valident la MÉTROLOGIE, pas deux fois la
théorie.

Spreads à rayon fixe (convention verrouillée) : ±10 % : 60 → **54** ;
±20 % : 65 → **40**. Cible < 30 : non atteinte.

**Lecture des branches pré-enregistrées** :
1. Vernis 12m : CONFIRMÉ (le centre recule vers le peloton, médianes de
   boule immobiles — le paysage n'a pas bougé, deux rayons cohérents).
   En dollars : −301 $ sur 12m = du backtest gonflé qui n'a jamais été
   dans le ciel.
2. Moitié falsifiable : NON validée — le 3m ne remonte pas vers p50. Le
   tilt 3m ne vivait PAS dans les décimales → il vit dans la STRUCTURE du
   sizing (pile multiplicative) ou les params non arrondis → **le
   vol-targeting passe d'« acté » à « désigné coupable »**.
3. Branche invalidante (3m dégradé en dollars) : PAS ouverte — 3m +27 %.

**Verrou décisionnel (posé avant le verdict 3m)** : le 3m ne jugeait PAS
l'arrondi — il jugeait où vit le tilt. Le dossier de l'arrondi était déjà
complet : rang rendu aux deux rayons (12m), dollars + DD rendus (28m),
3m amélioré en dollars. La barre pour ne PAS l'embarquer était un 3m
franchement dégradé en dollars — c'est l'inverse. **Set arrondi = prêt à
embarquer** (signal_mult S1 1.125→1.0, S5 3.25→3.0, strat_z → grille 0.5).
Timing du ship (restart dédié maintenant vs release batchée de fin de gel) :
décision utilisateur — le frein, lui, est déjà à bord (v1.11.0, 07:26).

## Archive — re-MC 28m ±20 % centré arrondi (100 draws)

```
centre arrondi : 9480 → rang p99 | boule : med 8204, p5 7492, p95 9274
centre exact   : 8451 → rang p65 | boule : med 8208 (quasi IDENTIQUE)
```

**Lecture honnête (la photo complique le tableau, elle ne le décore pas)** :
1. **Le paysage n'a pas bougé d'un pixel** : médianes de boule 8204 vs 8208.
   L'arrondi n'a pas remodelé la surface 28m — il a déplacé le centre vers
   un point plus haut de la MÊME surface.
2. **Le p99 n'apporte AUCUN bit nouveau** : par le modèle épistémique de la
   revue (rang d'un centre = son Δ$ projeté sur la CDF de sa boule), un
   centre à +15.6 % de sa médiane de boule est mécaniquement p99. C'est le
   +1012 $ déjà connu, exprimé en unités de rang — même mesure, troisième
   binning, toujours une seule voix.
3. **Angle mort du capteur, à graver** : la convention « p95+ = pic fitté »
   présuppose un centre dont les valeurs ont été CHOISIES sur les données.
   Le centre arrondi a été choisi par une règle aveugle (grille simple,
   curation AVANT tout run). Un p99 de provenance-règle ≠ un p98 de
   provenance-fit : le rang seul ne distingue pas réparation et re-fit —
   c'est la PROVENANCE du centre qui le fait. Le capteur spread garde sa
   valeur pour surveiller des params fittés ; il lit de travers un point
   choisi à l'aveugle qui a la chance (ou la structure) d'être haut.
4. **Correction de métrologie (revue)** : mon « p99−p40 = 59 » changeait la
   définition du capteur en douce — le spread officiel est **12m − 3m**
   (références 60/65). Chiffre officiel ±20 % du set arrondi : **p80−p40 =
   40**. Le 28m reste une ANNEXE annotée provenance (il mesure la hauteur
   d'un centre règle-choisi, pas la danse de mémorisation).
   Aliasing n°2 gravé pour le chantier VT : les fenêtres n'ont pas la même
   puissance statistique — une amélioration RÉELLE sort du bruit sur 28m et
   s'y noie sur 3m, donc rangs mécaniquement différents, spread gonflé,
   zéro pathologie. Le spread confond « le paysage change » et « le n
   change » → instrument successeur : **concordance des draws entre
   fenêtres, centre exclu** (Spearman des classements des 200 MÊMES points
   sur 12m vs 28m vs 3m — immune à la provenance et à la hauteur ; mesure
   si le PAYSAGE est le même, la vraie question depuis le début). Run en
   cours, `mc_concordance.json`.
5. Caveat de mesure : une bougie 4h a clôturé entre les deux runs 28m
   (end_ms décalé) — les médianes de boule quasi identiques montrent que
   c'est immatériel, mais c'est noté.

Note d'honnêteté (revue) : la curation n'était pas parfaitement aveugle —
le choix de la grille et de la liste des params touchés portait des priors
(je savais où vivait le baroque). Mais en bits : une calibration continue
de 35 scalaires = des dizaines de bits pris en regardant les données ; la
curation = 2-3 bits pris avant tout run. Contamination epsilon, notée, pas
crainte. Épistémique du rang formalisée (revue) : le rang est une p-value,
l'hypothèse nulle est « centre échangeable avec les draws » — un centre
calibré la viole par le haut (HARKing, le p98 12m était un aveu) ; un
centre règle-choisi ne teste plus rien, il DÉCRIT.

Reste vrai et inchangé : Pareto-positif (PnL + DD + 7 DoF de moins), le
+1012 $ est une direction pas une promesse, et la question « chance ou
structure » du point haut est indécidable à n=1 — c'est le forward qui
tranchera, comme toujours.
