# Spectre de persistance

> **Diagnostic pur.** Aucune stratégie simulée, aucun paramètre ajusté, aucune
> recommandation formulée.
>
> **Statut initialement prévu pour cette étude** : entrée de conception de
> XSMOM-v0 — le choix d'horizon aurait été informé par l'historique, soit un
> **méta-fit assumé**, borné par le walk-forward à venir. Ce méta-fit **n'a pas
> été contracté** : la quatrième branche de la grille de lecture se déclenche,
> aucun horizon n'est retenu, et la piste s'arrête ici.

**Généré le** : 2026-08-01T15:33Z
**Empreinte** : config `d7a415020619` · git `ae2b1c3+dirty` · données jusqu'au
2026-08-01T12:00, 36 symboles, fichiers du 2026-08-01T12:10Z
**Périmètre** : 853 jours × 34 tokens (`Params.trade_symbols`, gate de parité
passé) · rendements **log** journaliers reconstruits des bougies 4 h
**Source** : `backtests/persistence_spectrum.py` ·
`analysis/output/persistence_spectrum.json`

## 0. Origine

`docs/creux_anatomy.md` a réfuté l'hypothèse du chop et mis au jour la vraie
signature des creux d'Alfred : persistance au jour le jour, annulation à
l'échelle de la fenêtre. Hypothèse de travail testée ici : **l'ennemi vit à
l'échelle du swing** — quelques jours à deux semaines, entre le hold 48 h
d'Alfred et le lookback 20 j de TREND-v0.

## 1. Grille de lecture — ÉCRITE AVANT LES CHIFFRES

> - Persistance cross-sectionnelle élevée dans les creux **ET** dans la
>   référence à un horizon h commun aux 4 fenêtres ⇒ **signal vert XSMOM**,
>   horizon de conception = h.
> - Persistance cross-sectionnelle élevée dans la référence mais **ABSENTE** des
>   creux ⇒ **signal rouge** : XSMOM gagnerait en temps calme et lâcherait
>   pendant les creux — exactement le défaut de TREND-v0. À consigner comme
>   contre-indication majeure avant toute grille XSMOM.
> - Les 4 fenêtres divergent sur l'horizon ⇒ le rapport le dit tel quel, fenêtre
>   par fenêtre. Implication pré-écrite : pas d'horizon unique défendable ⇒
>   XSMOM-v0 prendrait la valeur canonique de littérature (7 j), pas un h choisi
>   dans ces chiffres — ou la piste s'arrête là.
> - Persistance faible partout (temporelle **ET** cross-sectionnelle, creux
>   **ET** référence) ⇒ l'hypothèse du swing est **réfutée** ⇒ on consigne et on
>   **NE lance PAS** XSMOM. Un diagnostic a le droit de tuer la piste qu'il
>   devait nourrir.
> - Cas non prévu par cette grille ⇒ le **nommer** comme tel, décrire, ne rien
>   conclure d'opportuniste.

## 2. Planchers d'effectif — fixés avant exécution

| mesure | plancher | conséquence |
|---|---|---|
| VR(k) | ≥ 4 blocs de k jours dans la fenêtre | k = 10 et k = 20 **non mesurables** sur les fenêtres de 30 jours |
| Spearman cross-sectionnel | ≥ 5 paires de périodes adjacentes | k = 10 et k = 20 **non mesurables** sur 30 jours ; k = 20 non mesurable même sur A (3 paires) |
| toute mesure cross-sectionnelle | ≥ 10 tokens présents | — |

Une cellule sous le plancher n'est pas émise. Elle apparaît comme
**NON ÉMISE**, avec le nombre de paires disponibles.

## 3. La référence — 28 mois hors creux (727 jours, 3 segments)

C'est le seul bloc de mesures correctement doté en effectif. Il porte le
verdict.

### Ratio de variance — VR(k) = Var(rₖ) / (k · Var(r₁))

VR > 1 = persistance · VR < 1 = réversion · VR(1) ≡ 1 par construction.

| | 3 j | 5 j | 10 j | 20 j |
|---|---:|---:|---:|---:|
| BTC | 0,905 | 0,858 | 0,791 | 0,780 |
| indice alt équipondéré | 0,943 | 0,880 | 0,805 | **0,711** |
| médiane des tokens | 0,967 | 0,919 | 0,835 | **0,703** |
| Q1 des tokens | 0,896 | 0,831 | 0,760 | 0,593 |
| Q3 des tokens | 1,030 | 1,017 | 0,950 | 0,859 |
| part des tokens à VR > 1 | 31,7 % | 28,0 % | 12,2 % | 12,2 % |

**Tous les VR sont inférieurs à 1, à tous les horizons, sur les trois objets**
— et l'écart se creuse avec l'horizon. À 20 jours, seuls 12 % des tokens
dépassent 1 et le troisième quartile est encore à 0,86.

### Autocorrélation de rang cross-sectionnelle (Spearman)

Le token relativement fort sur k jours reste-t-il relativement fort sur les k
suivants ? Périodes adjacentes non chevauchantes.

| horizon | ρ moyen | erreur-type | t | paires | périodes à ρ > 0 |
|---|---:|---:|---:|---:|---:|
| 3 j | +0,0093 | 0,0149 | **+0,62** | 238 | 49 % |
| 5 j | −0,0237 | 0,0213 | **−1,12** | 141 | 45 % |
| 10 j | −0,0247 | 0,0263 | **−0,94** | 68 | 48 % |
| 20 j | −0,0201 | 0,0401 | **−0,50** | 32 | 47 % |

**Aucun horizon n'atteint |t| = 2.** La part de périodes à corrélation positive
est de 45 à 49 % — un tirage à pile ou face. Il n'y a **pas de persistance
cross-sectionnelle dans la référence**, à aucune des quatre échelles testées.

## 4. Les fenêtres de creux

Percentile = position dans la distribution des fenêtres glissantes de même
longueur hors creux (442 pour A, 640 pour B, C, D).

### Ratio de variance

| | 3 j | 5 j | 10 j | 20 j |
|---|---|---|---|---|
| **A** BTC | 0,997 p69 | 0,724 p41 | 0,506 p19 | 0,364 **p8** |
| **A** indice alt | 1,053 p84 | 0,865 p65 | 0,632 p34 | 0,408 p26 |
| **A** médiane tokens | 1,019 p79 | 0,858 p60 | 0,632 p23 | 0,397 p19 |
| **B** BTC | **1,268 p94** | **1,335 p94** | non mesurable | non mesurable |
| **B** indice alt | **1,315 p97** | **1,251 p91** | non mesurable | non mesurable |
| **B** médiane tokens | 1,129 p93 | 1,034 p84 | non mesurable | non mesurable |
| **C** BTC | 0,997 p71 | 0,604 p32 | non mesurable | non mesurable |
| **C** indice alt | 1,116 p84 | 0,829 p66 | non mesurable | non mesurable |
| **C** médiane tokens | 1,124 p93 | 0,856 p69 | non mesurable | non mesurable |
| **D** BTC | 0,741 p27 | 0,801 p60 | non mesurable | non mesurable |
| **D** indice alt | 0,865 p42 | 0,808 p63 | non mesurable | non mesurable |
| **D** médiane tokens | 0,809 p29 | 0,721 p49 | non mesurable | non mesurable |

### Autocorrélation de rang cross-sectionnelle

| fenêtre | 3 j | 5 j | 10 j | 20 j |
|---|---|---|---|---|
| **A** | −0,0736 ± 0,040 · t=−1,85 · n=31 · p6 | −0,0219 · t=−0,35 · n=18 | +0,0309 · t=+0,28 · n=8 | **NON ÉMISE** (3 paires) |
| **B** | **−0,2851 ± 0,080 · t=−3,55 · n=9 · p0** | −0,0635 · t=−0,40 · n=5 | **NON ÉMISE** (2) | **NON ÉMISE** (0) |
| **C** | +0,0197 · t=+0,43 · n=9 | −0,0276 · t=−0,19 · n=5 | **NON ÉMISE** (2) | **NON ÉMISE** (0) |
| **D** | +0,0885 · t=+1,02 · n=9 · p82 | +0,0846 · t=+0,87 · n=5 · p78 | **NON ÉMISE** (2) | **NON ÉMISE** (0) |

## 5. Application de la grille

### Branches 1 et 2 : ne se déclenchent pas

Toutes deux exigent une **persistance cross-sectionnelle élevée dans la
référence**. Elle y est nulle : |t| ≤ 1,12 aux quatre horizons, sur 238 à 32
paires. Ni le signal vert, ni le signal rouge.

### Branche 3 : les fenêtres divergent, mais son issue est sans objet

Elles divergent bel et bien, et sur le signe :

| | temporel (VR à 3-5 j) | cross-sectionnel (3 j) |
|---|---|---|
| **A** | ≈ neutre puis réversion marquée à 10-20 j | **négatif** (t = −1,85) |
| **B** | **persistance forte** (p94-p97) | **très négatif** (t = −3,55) |
| **C** | ≈ neutre | ≈ nul |
| **D** | réversion | légèrement positif (t = +1,02) |

L'implication pré-écrite de cette branche était de retomber sur l'horizon
canonique de littérature (7 j) « ou la piste s'arrête là ». Elle est **sans
objet** : la branche 4 tranche en amont, puisqu'il n'y a rien à construire à
**aucun** horizon dans le seul bloc correctement doté.

### Branche 4 : ELLE SE DÉCLENCHE — l'hypothèse du swing est réfutée

Persistance faible dans la référence, temporelle **et** cross-sectionnelle.
Faible également dans les creux, à une exception près qui ne joue pas en faveur
de XSMOM : **B**, où l'indice persiste fortement à 3-5 jours (p94-p97) alors
que c'est précisément la fenêtre où le classement se **retourne** le plus
violemment de toute l'étude (ρ = −0,285, t = −3,55, 11 % de périodes
positives). Un marché qui bouge d'un bloc pendant que les rangs s'inversent :
c'est l'environnement le plus défavorable qui soit à une stratégie
cross-sectionnelle.

**Conclusion de la grille : XSMOM n'est pas lancé.** L'étude tue la piste
qu'elle devait nourrir, ce que la grille autorisait explicitement.

### Ce que la grille n'avait pas anticipé — troisième fois

Son vocabulaire (« faible » / « élevée ») place la question sur un axe de
**magnitude**. Les données répondent sur l'axe du **signe** : ce n'est pas une
persistance faible, c'est une **réversion**, cohérente sur les trois objets et
d'autant plus marquée que l'horizon s'allonge (indice alt : 0,943 à 3 j →
0,711 à 20 j).

La distinction ne change pas le verdict — les deux lectures excluent XSMOM —
mais elle est plus forte que celle qui était prévue. C'est le troisième écart
de vocabulaire de grille en trois études : le § 1.5 de TREND-v0 confondait
invariance d'échelle isolée et combinée, `creux_anatomy` traitait « efficacité
basse » et « retournements élevés » comme une seule chose, et celle-ci demande
une magnitude là où le signe est l'information.

### Une réserve de puissance, à ne pas escamoter

Les cellules des creux reposent sur **5 à 9 paires** de périodes, sauf A à
3 jours (31 paires). Les lectures positives de D (+0,089 et +0,085) sont à
t ≈ 1 : c'est du bruit, et il ne faut pas les lire comme une pousse verte.
Sept cellules sur seize n'ont pas pu être émises du tout.

Le verdict repose sur la référence, où l'effectif est de 32 à 238 paires.

## 6. Ce que l'étude établit

1. **Aucune persistance cross-sectionnelle dans la référence**, à 3, 5, 10 ou
   20 jours : |t| ≤ 1,12, périodes positives entre 45 et 49 %.
2. **Le régime temporel est la réversion, pas la persistance** — VR < 1 sur
   BTC, sur l'indice alt et sur la médiane des tokens, à tous les horizons, en
   s'accentuant avec l'horizon.
3. **Les quatre creux divergent** en régime temporel comme en régime
   cross-sectionnel ; B et D sont opposés sur les deux axes.
4. **B est le contre-exemple apparent et n'en est pas un** : sa persistance
   temporelle s'accompagne du retournement de rangs le plus violent mesuré.
5. **Les fenêtres de 30 jours ne portent pas les horizons longs.** Sept
   cellules sur seize n'ont pas été émises, plutôt qu'estimées.
