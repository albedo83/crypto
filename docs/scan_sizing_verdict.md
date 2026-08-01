# Mission 2 — sizing modulé par l'agitation du scan : spécification et grille

> **⚠ ÉCRIT ET COMMITTÉ AVANT TOUTE EXÉCUTION.** Aucun backtest de modulateur
> n'a été lancé au moment où ces lignes sont écrites. L'historique git en fait
> foi : cette grille précède le premier run.
>
> **Rédigé le** : 2026-08-01 · **Phase A** · en attente du **GO explicite** de
> Seb pour la Phase B.

## 0. Statut : c'est un pari d'edge

Ce n'est pas une correction de mesure ni une réparation. C'est un **pari sur un
edge**, et la doctrine récente lui applique une barre haute sans indulgence :
3/4 est un refus.

Ce qui le justifie : l'effet « agitation du scan » est le plus fort et le plus
robuste sorti de la campagne de juillet, il vient d'être **re-confirmé 4/4 sur
base propre et en fenêtres glissantes** (`docs/scan_activity_premise.md`), et il
n'a jamais été testé avec le seul levier qui ait survécu à la campagne : **la
taille**. Son unique test antérieur en faisait un **filtre binaire** — qui
coupait des trades et cassait le chemin de compounding, l'échec logique de toute
cette famille.

## 1. Variable — UNE seule, désignée avant exécution

**`n_cands_at_open`** — le nombre de candidats présents au scan qui a ouvert la
position.

Désignée par la Mission 1 (`docs/dispersion_conditioning.md`), et par
**élimination** : la dispersion cross-sectionnelle n'est pas une jauge
(relation plate, 2 semestres monotones sur 5 et de sens opposés), et les deux
variables se sont révélées **distinctes** (ρ ≈ 0,30 par scan, effondrée à 0,09
dans les creux) — donc non substituables.

Aucune seconde variable ne sera introduite, quel que soit le résultat.

## 2. Modulateur — forme FIGÉE

Facteur multiplicatif sur la taille de position, **linéaire par morceaux**,
points d'ancrage pris sur les bornes de buckets publiées en Phase A0 :

```
mult(n) = 0,50                     si n ≤ 1
        = 0,50 + 0,25 × (n − 1)    si 1 < n < 5
        = 1,50                     si n ≥ 5
```

| n_cands | 1 | 2 | 3 | 4 | ≥5 |
|---|---:|---:|---:|---:|---:|
| multiplicateur | **×0,50** | ×0,75 | ×1,00 | ×1,25 | **×1,50** |

- **Aucune variante.** Pas d'autre plancher, pas d'autre plafond, pas d'autre
  point d'ancrage, pas de forme alternative (marche, logistique, racine).
- **Aucune grille de paramètres.** Une spec, un run.

> **Tension consignée, non résolue.** La Phase A0 montre que l'effet est une
> **marche** au bucket ≥5 (buckets 1/2/3-4 à +33/+16/+20 bps, bucket ≥5 à
> +369 bps) et non une pente. La rampe linéaire dépense donc sa plage
> intermédiaire sur des distinctions que les données ne soutiennent pas. La
> forme reste celle imposée par l'énoncé : la changer après avoir vu A0
> reviendrait à ajuster la spec sur les données, ce qui viderait la grille de sa
> valeur. Le fait est écrit ici pour qu'il ne soit pas re-découvert après le
> verdict et transformé en excuse.

## 3. Ordre d'application — le cap vient APRÈS

**Le plafond notionnel proportionnel (0,3 × equity) s'applique APRÈS
modulation.** Un trade en régime agité peut donc être plafonné ; un trade en
régime calme n'est jamais gonflé par le plafond.

Tous les autres plafonds de risque sont **inchangés** : `max_positions`,
`max_per_sector`, levier, `margin_check`, plancher de taille à $10.

> **Réserve d'asymétrie, consignée avant exécution.** Le cap s'appliquant après
> modulation, le boost ×1,5 est **absorbé** pour toute position dont la taille
> non modulée atteint déjà 0,3 × equity : elle est ramenée au plafond et ne
> gagne rien. La pénalité ×0,5, elle, **mord toujours** — diviser par deux
> n'amène jamais au plafond.
>
> En pratique, ce modulateur est donc surtout une **pénalité de scan désert**,
> et non un dispositif symétrique. Cela concerne au premier chef les signaux à
> `signal_mult` élevé (S5 à 3,0, S9 et S10 à 2,0), les plus souvent plafonnés.
>
> Cette asymétrie est une **conséquence de l'ordre imposé au § 3**, pas un
> défaut à corriger : appliquer le cap avant modulation laisserait le boost
> franchir le plafond de risque, ce que l'énoncé exclut. Elle est écrite ici
> pour que le verdict soit lu comme celui d'une pénalité asymétrique, et non
> comme celui d'une modulation symétrique.
>
> **La Phase B chiffrera la part des trades boostés effectivement rognés par le
> cap.**

### Modification moteur autorisée — une seule, et bornée

Le point d'accroche existant (`size_fn`) s'applique *après* le cap, pas avant :
en l'état, le moteur ne sait pas faire l'ordre demandé. La Phase B est donc
autorisée à modifier `backtests/backtest_rolling.py` pour permettre au cap
proportionnel d'être appliqué après `size_fn`.

**Cette modification est soumise à une preuve d'inertie** : avec `size_fn=None`,
le moteur doit reproduire **exactement** les chiffres de référence actuels sur
les 4 fenêtres de doctrine. Un écart, même d'un dollar, invalide le run — ce
serait le retour du défaut de la semaine, une mesure qui change sans le dire.

## 4. Tout le reste est identique

| | |
|---|---|
| configuration | celle **en service**, sans dérogation |
| univers | `Params.trade_symbols`, gate de parité |
| coûts | 13 bps round-trip **+ funding réalisé intégré** |
| sémantique | ALIGNED, booking réaliste des trails, `mfe_on_close` |
| empreinte | `backtests/fingerprint.py`, publiée au rapport |
| garde-fous | `measure_guards`, cellules sous-dotées NON ÉMISES |
| capital de départ | $1 000 par fenêtre |

## 5. GRILLE DE VERDICT — PRÉ-ENREGISTRÉE

### Clause unique : walk-forward glissant non chevauchant

Fenêtres de doctrine, décalages 0 / 6 / 12 / 18 mois depuis la fin des données,
6 mois chacune, **non chevauchantes**. Comparaison : P&L du moteur **avec**
modulateur contre P&L du moteur **sans**, même fenêtre, même capital.

| résultat | verdict |
|---|---|
| **4/4 en P&L** | **CANDIDAT À DÉPLOIEMENT** — ce qui signifie éligible à une *décision* de Seb, séparée. Rien n'est déployé automatiquement |
| **3/4** | **REFUS.** Un pari d'edge n'a pas d'indulgence |
| **≤ 2/4** | refus |

### Cas ambigus, tranchés d'avance

| cas | verdict |
|---|---|
| ΔP&L exactement nul sur une fenêtre | **compte comme ÉCHEC** — un modulateur qui ne change rien n'améliore rien |
| gain sur 4/4 mais inférieur à $1 sur une fenêtre | **compte comme réussite** ; l'ampleur est rapportée, le seuil reste le signe |
| le modulateur ne mord sur aucune fenêtre (n_cands constant) | **run NUL**, pas de verdict — défaut d'instrumentation |
| preuve d'inertie en échec (§ 3) | **run NUL**, correction, re-run, les deux empreintes au rapport |

### Ce qui est rapporté mais NE participe PAS au verdict

- **le drawdown**, sur chaque fenêtre et sur 28 mois ;
- **le comportement dans les 4 fenêtres de creux** — d'autant que A0 a montré
  que le régime agité y est quasi absent (cellule ≥5 non émise partout, nulle
  en fenêtre B), donc que le modulateur y sera presque inerte.

**Aucun rattrapage par le risque.** Un modulateur à 3/4 en P&L mais qui
améliorerait le drawdown reste un refus. La clause est écrite ici pour que
l'argument ne puisse pas être avancé après coup.

### Invariant

Tout cas non prévu par cette grille est **nommé comme tel**, décrit, et ne fonde
aucune conclusion opportuniste. Clauses de repli conservatrices par défaut.

## 6. Clause de clôture

**Un échec ferme le dataset historique jusqu'aux données prospectives de
mi-septembre.** Aucune autre étude d'edge sur ces 28 mois, quelle qu'elle soit,
quelle qu'en soit l'idée, quel qu'en soit le demandeur.

La clause s'applique à l'échec du backtest **et** se serait appliquée à l'échec
de la prémisse. Elle ne s'applique pas aux études de **mesure** (corrections de
biais, contrôles de parité, régénérations de référence), qui ne cherchent pas
d'edge.

## 7. Interdictions explicites

1. **Aucune variante de forme** après lecture du résultat — ni marche, ni autre
   plancher, ni autre plafond.
2. **Aucune restriction de domaine** — pas de « modulateur seulement sur S5 »,
   pas de « seulement en bull », pas de « seulement au-dessus de 3 candidats ».
3. **Aucune seconde variable** — la dispersion a été éliminée en Mission 1 et
   n'est pas repêchable.
4. **Aucune lecture partielle** : les 4 fenêtres sont lues en une fois, après le
   run complet.
5. **Aucun re-run modifié** en cas d'échec. Le seul re-run autorisé est celui
   qui suit une invalidation technique (§ 5), à spec inchangée.

---

## 8. Résultat

> *À compléter en Phase B, après GO explicite. Vide à ce jour.*
