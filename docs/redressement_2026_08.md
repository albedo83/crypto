# Redresser la barre — état des lieux du 2026-08-29

> Rédigé en réponse à une question directe : *comment redresser la barre avant
> mise hors service de tout ?* Le document sépare ce qui est **mesuré** de ce qui
> est **supposé**, et nomme ce qui n'a pas été changé.

**Fenêtre** : reset du 2026-07-09 → 2026-08-29 (51 jours), capital de départ $518,34.

---

## 1. L'argent

| bot | solde | vs son backtest | DD | IA |
|---|---|---|---|---|
| **live (SENIOR)** | 459,11 $ | **−14,6 pp** | −14,3 % | oui |
| paper | 498,28 $ | −5,9 pp | −7,4 % | non |
| junior | 195,26 $ | −45,2 pp | −41,3 % | non |
| baby | 152,51 $ | −24,1 pp | −0,6 % | non |

Le backtest canonique sur la même fenêtre rend **−0,29 %**. C'est la seule
référence honnête, et elle fixe le plafond : à exécution parfaite, ce régime ne
paie rien.

---

## 2. L'écart live-vs-backtest se décompose en trois couches

### Couche 1 — la couche IA : ~8,7 pp, mesurée, retirée aujourd'hui

Le live et le paper tournent sur le **même code**, le **même capital de départ**,
le **même reset**. La seule différence est la couche IA, active sur SENIOR seul.

- **Décote d'entrée** : −16 $ mesurés en argent réel sur 28 trades appariés,
  entièrement concentrés sur S5 LONG. Retirée du prompt en v1.20.0.
  Détail : `docs/ai_haircut_verdict.md`.
- **Verrou de sortie (LOCK)** : Δ **−10,56 $** contre les règles seules sur 23
  verrous résolus, dont **−57,01 $** sur le seul bucket S5 LONG. Les 24 sorties
  en `manual_stop_set` ont toutes encaissé un gain — mais en rendant 128 à
  **955 bps** depuis leur pic, pendant que le paper laissait courir les mêmes
  signaux (MINA +16,50 $ contre +3,47 $, STX +24,79 $ contre 0, ENA +21,66 $).

**Le mode d'échec est le même dans les deux cas** : une protection *uniforme*
appliquée contre une queue droite épaisse. Le stop catastrophe borne déjà le
risque ; rien ne borne le gain. Protéger uniformément ne peut donc que raboter
la tête de la distribution en laissant la queue gauche intacte. C'est la
deuxième fois que cette erreur coûte de l'argent réel, sous deux formes
différentes.

### Couche 2 — paper contre son backtest : ~1,7 pp réalisés, et c'est du bruit

Décomposition du gap réalisé (−8,61 $) via `analysis/bt_divergence.py` :

| poste | n | P&L |
|---|---:|---:|
| trades pris par le paper que le BT n'endosse pas | 20 | −57,70 $ |
| trades du BT que le paper n'a pas pris (donc évités) | 16 | +55,36 $ |
| divergence sur trades appariés | 25 | −10,26 $ |

Les deux premiers postes **s'annulent** : c'est de la dépendance au chemin
(un slot pris ici décale tout le reste), pas un défaut.

Sur les trades appariés, le sous-ensemble propre — **même raison de sortie,
même heure à ±15 min, donc seule la source de prix diffère** :

```
n = 17 · somme −16,55 $ · médiane +1,86 $
négatifs 8/17 = 47 %  ·  test des signes p = 0,69
```

**Aucun biais directionnel.** La médiane est positive ; la somme est tirée par
trois valeurs extrêmes. Il n'y a donc **pas** de dérive systématique de prix de
sortie à corriger — l'hypothèse « le bot sort moins bien que le backtest » est
réfutée sur cet échantillon.

L'écart de 5,9 pp affiché par le tracker est un écart d'**equity** : il inclut
les positions ouvertes marquées à l'instant t. En réalisé, il vaut 1,7 pp.

### Couche 3 — le régime : la stratégie ne gagne plus depuis juin

Extrait de `docs/backtests.md` (fenêtres se terminant au 2026-08-02) :

| fenêtre | P&L % | trades |
|---|---:|---:|
| depuis 2026-03-01 | −9,2 % | 258 |
| depuis 2026-05-01 | +19,5 % | 171 |
| **depuis 2026-06-01** | **−10,5 %** | 117 |
| **depuis 2026-07-01** | **−12,9 %** | 54 |

Ce n'est **pas** un problème d'exécution : le backtest lui-même est négatif sur
tout ce qui démarre après le 1ᵉʳ juin.

**Mais ce n'est pas non plus anormal.** Le pire épisode de cette configuration
(`docs/dd_anatomy.md`) : **−51,4 %**, 95 jours de descente, 123 jours sous le
pic — entièrement récupéré. La mauvaise passe actuelle (~90 jours, −7,4 % sur
le paper hors IA) est d'un ordre de grandeur inférieur à ce que la stratégie a
déjà traversé et repris.

---

## 3. Ce qui a été changé le 2026-08-29 (v1.21.0)

1. **`AI_EXIT_LOCK_MODE`** — nouveau gate. Le LOCK agissait inconditionnellement
   dès `AI_EXIT_ENABLED=1` (choix hybride d'origine, jamais gaté). Défaut
   désormais `shadow`, comme `cut_mode`. Réglé sur `shadow`.
2. **`AI_ARBITER_MODE`** : `act` → `shadow`.
3. **`ai_entry_arbiter._max_tokens(n)`** — le budget de sortie était un forfait
   `1500`, calibré sur ~10 candidats. À 26-31 (univers élargi à 35 tokens), la
   réponse était tronquée **en plein JSON**, `json.loads` levait, le wrapper
   fail-open avalait : l'arbitre a été **débranché 6 fois par jour du 24 au 29
   août** — à chaque scan — sans que rien ne le signale. Le budget est
   maintenant proportionnel (`min(8000, 300 + 120·n)`).

Les deux arbitres restent **allumés**. Les deux scorecards notent explicitement
le mode shadow en contrefactuel (`ai_exit_scorecard.py` rejoue le stop,
`ai_arbiter_scorecard.py` rejoue le veto) : **on garde toute la mesure sans
engager un dollar.**

### Conséquence de méthode

La mesure pré-enregistrée du 25 août (`ai_haircut_verdict.md` § 8) est
**morte-née** : elle exigeait de comparer les tailles live/paper après le
retrait de la décote, or l'arbitre ne tournait plus du tout depuis le 24. Il n'y
a **qu'une seule paire appariée** sur la période. Le prompt v1.20.0 n'a jamais
été mesuré, et ne le sera pas avant une re-promotion en `act`.

---

## 4. Ce qui n'a PAS été changé, et pourquoi

- **Aucune règle de trading.** `alfred/rules.py`, les signaux, la chaîne de
  sorties, le sizing, les seuils : intacts. Rien de tout cela n'a de validation
  walk-forward dans cette session, et la doctrine du projet est explicite —
  le backtest **rejette**, il ne promet pas.
- **`signal_mult`, `strat_z`, le modulateur macro : inertes au capital actuel.**
  Constat structurel vérifié : le cap proportionnel `0.3 × equity` (≈ 138 $)
  mord sur **tous** les signaux. Tailles moyennes observées — S1 141,7 $ ·
  S10 147,1 $ · S9 115,2 $ — alors que `signal_mult` déclare S5 à 3× le poids de
  S1. `settings.py:117` le documentait déjà pour S5. Corollaire : **toute
  tentative future de « re-pondérer les signaux » est sans effet à ce capital**,
  et le modulateur macro n'agit plus que comme un frein à sens unique (ses
  amplifications sont mangées par le cap, ses réductions passent). Non corrigé :
  relever le cap relèverait le drawdown, et `0.3` est le genou anti-cascade
  validé en v1.13.0.
- **Junior (−41,3 %)** : piloté par un tiers, non touché, jamais redémarré par
  une autorisation générique.

---

## 5. Ce que ça donne, honnêtement

Retirer la couche IA fait converger le live vers le paper : de −14,6 pp à
environ −6 pp sous le backtest. **Ça ne rend pas le compte positif**, parce que
le backtest de cette fenêtre est plat. Il n'y a pas de rendement caché à
récupérer par une correction : les deux couches réparables valent ~8,7 pp et
~1,7 pp, et la troisième est le marché.

Les trois seules voies vers du positif sont, dans l'ordre de ce qu'on contrôle :

1. **Ne plus payer de taxe sur l'exécution** — fait aujourd'hui, mesuré.
2. **Attendre que le régime tourne** — précédent historique : 123 jours sous le
   pic, récupérés. On en est à ~90.
3. **Un nouvel edge** — 25+ tentatives refusées en walk-forward depuis mai
   (voir l'index mémoire). Aucune n'a passé la grille. Rien n'indique qu'une
   26ᵉ passerait.

---

## 6. Critères pré-enregistrés de la prochaine revue

Fixés **avant** observation, comme toujours.

1. **Convergence live/paper.** Sur les trades appariés post-restart, l'écart de
   P&L réalisé entre live et paper doit tomber **sous 2 $ en valeur absolue**
   (il est à ~40 $ sur la fenêtre). S'il persiste, la couche IA n'était pas la
   cause et il faut chercher ailleurs.
2. **LOCK en shadow.** `ai_exit_scorecard.py` doit continuer à produire un Δ
   pour le bucket S5 LONG. Re-promotion en `act` **uniquement** si ce bucket
   repasse positif sur n ≥ 20.
3. **Arbitre d'entrée.** Zéro `ARBITER_FAILOPEN` de type `JSONDecodeError` sur
   7 jours. Sinon le correctif `_max_tokens` est insuffisant.
4. **Le compte.** Si le paper (hors IA) passe sous **−25 %** de son pic, on est
   en territoire de deuxième pire fenêtre historique et la question de l'arrêt
   se pose sur des bases factuelles, pas sur l'humeur d'un mauvais mois.


---

## 7. Revue de la semaine 1 — 2026-09-06

**Le régime a tourné.** Le paper (aucune couche IA) est à **586,27 $**, son plus
haut, soit **+13,1 %** depuis le reset. baby aussi est à son pic. La thèse du § 5
— « attendre que le régime tourne » — s'est vérifiée en 8 jours. Le critère
d'arrêt (paper sous −25 %) n'a jamais été approché.

| bot | 29/08 | 06/09 | Δ |
|---|---:|---:|---:|
| paper | 498,28 | **586,27** | +88,0 |
| baby | 152,51 | **183,87** | +31,4 |
| junior | 195,26 | 213,63 | +18,4 |
| live | 459,11 | 473,19 | +14,1 |

### Les quatre critères du § 6

1. **Convergence live/paper — ÉCHEC.** L'écart s'est creusé. Cause identifiée
   ci-dessous : ce n'est plus la couche IA.
2. **LOCK en shadow — RÉUSSI.** 198 verdicts, tous `acted=False`, note
   `lock_shadow`, **0** sortie `manual_stop_set` depuis le restart.
3. **Fail-opens — symptôme corrigé, panne déplacée.** Zéro `JSONDecodeError`
   (le budget proportionnel a marché) mais **47 timeouts à 25 s et 0 succès** :
   multiplier `max_tokens` par 2,7 a allongé la génération au-delà du délai.
   Erreur de ma part, corrigée en v1.22.0 (`AI_ARBITER_TIMEOUT` 25 → 60).
4. **Paper sous −25 % — NON.** Il est à son pic.

### La cause du critère 1 : le miroir exchange recopiait un profit-taking

L'écart de la semaine tient à **un seul trade** :

```
live   ARB S1 LONG  02/09 16:03 → 04/09 12:55  44,9h  MFE 2444 bps  +13,00 $  exchange_stop
paper  ARB S1 LONG  03/09 08:03 → 06/09 08:03  72,0h  MFE 4843 bps  +73,46 $  timeout
```

Le live est sorti **à 12h55, en pleine bougie**, par le trigger résident.
Contrefactuel à qualité égale (son propre timeout, 05/09 16:03, clôture
0,14963) : **+38,04 $ contre +13,00 $ réalisés — le miroir a coûté 25,04 $.**

`hardstop.py` déclarait pourtant son périmètre : *« Les trails dynamiques
(s10/s8_inlife/prop_trail) ne sont PAS miroités (profit-taking, pas sécurité) »*.
`opp_floor` — cliquet à 0,80 × gain armé dès +300 bps — **est** du profit-taking,
et était miroité. Or `trail_eval_4h_close=True` fait que ces règles ne sont
évaluées qu'aux clôtures 4h (v1.8.0, prise parce que l'évaluation intra-bougie
était la cause n°1 de gagnants coupés). Sur ARB le trigger a été touché en mèche
**4 fois** alors qu'aucune clôture 4h n'est passée dessous.

La comptabilité le montre en creux : `opp_floor` évalué normalement est positif
partout (paper +71,64 $/n=5 · junior +34,08 $/n=6 · baby +10,46 $/n=1), et
SENIOR n'en enregistre **aucun** — le miroir tirait avant et bookait
`exchange_stop`. Les deux `exchange_stop` du live sont intra-bougie, sur des
gagnants (MFE 1298 et 2444 bps).

**Corrigé en v1.22.0** : `protective_level_bps` ne miroite plus que le stop
catastrophe et le `manual_stop` de l'utilisateur. Vérifié par reconstruction —
le trigger recalculé (0,100411) coïncide au chiffre près avec le `HARD_STOP_SET`
réel du 02/09. Réserve : **n = 2**, le chiffre ne prouve rien statistiquement ;
ce qui justifie le retrait est l'argument mécanique.

### Critères de la semaine 2

1. **Zéro sortie `exchange_stop` sur un gagnant** (MFE > 300 bps). Le filet ne
   doit plus tirer que sur des pertes, ou pas du tout.
2. **Convergence live/paper** : l'écart de P&L réalisé sur trades appariés doit
   passer sous 5 $. S'il persiste après le retrait du miroir ET de la couche IA,
   la cause restante est la dépendance au chemin, qui est irréductible.
3. **Arbitre d'entrée** : au moins un `ARBITER_DECISION` réussi. Sinon le
   problème n'est ni la troncature ni le délai.
4. **Le compte** : critère d'arrêt inchangé, paper sous −25 % de son pic.


---

## 8. Quand peut-on juger le moteur ? — cadre pré-enregistré du 2026-09-06

> Question posée : *si le moteur n'est pas gagnant, il est inutile — à partir de
> quand peut-on le réévaluer globalement ?* C'est un problème de puissance
> statistique. Réponse chiffrée sur les données réelles, écrite avant échéance.

### La voie directe est fermée

Distribution du P&L par trade du paper (ligne propre : ni IA, ni miroir) :
n=93, moyenne **+46,4 bps**, σ **832 bps**, asymétrie +2,28 → **σ/μ = 17,9**.

Nombre de trades pour établir que l'edge est > 0 :

| test | n requis | durée au rythme actuel (1,58 trade/j) |
|---|---:|---|
| unilatéral 95 % | 1 984 | **3,4 ans** |
| bilatéral 95 % | 2 518 | **4,4 ans** |

Prouver directement que le moteur gagne n'est donc pas un critère disponible.
Aucun bot de la flotte n'en est proche : t = +0,54 (paper) · −0,50 (live) ·
−0,90 (junior) · +0,40 (baby). **Tous indiscernables de zéro.**

Contexte à garder en tête : le backtest 28 mois lui-même n'a qu'un
**t = 2,80** sur 853 jours. La référence n'est pas non plus une certitude.

### La voie qui marche : comparer à la distribution du moteur, pas à zéro

Fenêtres glissantes du backtest 28 mois (853 j, rendements composés) :

| durée | p5 | p25 | médiane | p95 | **% de fenêtres négatives** |
|---|---:|---:|---:|---:|---:|
| 30 j | −21,1 % | −5,7 % | +10,9 % | +54,6 % | 31 % |
| 90 j | −26,8 % | +3,1 % | +31,4 % | +118,0 % | **23 %** |
| 180 j | **+4,7 %** | +45,9 % | +70,1 % | +239,9 % | **4 %** |
| 365 j | +137,6 % | +181,0 % | +213,9 % | +427,2 % | **0 %** |

Lecture : un moteur **en bonne santé** perd de l'argent sur près d'un quart des
fenêtres de 90 jours. À 90 jours, une perte ne prouve donc rien. À 180 jours
elle devient un événement à 1 sur 25. À 365 jours elle ne s'est jamais produite.

### Les dates (horloge = reset du paper, 2026-07-09)

| échéance | date | ce qu'on peut conclure |
|---|---|---|
| 90 j | **2026-10-07** | alerte seulement : sous **−26,8 %** = hors du p5, signal réel. Au-dessus, non concluant. |
| 180 j | **2027-01-05** | **VERDICT.** Un moteur sain est positif 96 % du temps. Négatif ⇒ le moteur ne fonctionne plus. |
| 365 j | 2027-07-09 | décisif. 0 fenêtre saine négative sur 489. |

La ligne d'évaluation du **moteur** est le **paper** : il n'a jamais porté ni la
couche IA ni le miroir de stops. Le live mesure l'**implémentation**, et son
horloge propre ne démarre qu'au 2026-09-06.

### Où on en est déjà

Paper à 59 jours : 518,34 → 586,27 $ = **+13,1 %**, soit le **43ᵉ percentile**
de la bande à 59 jours (p25 −2,2 % · médiane +20,8 %). Dans la bande, un peu
sous la médiane. Rien d'anormal, rien de probant.

### Deux résultats intermédiaires écartés

1. **La bande « petit capital » est circulaire.** Segmenter le backtest sur
   `equity < 2 000 $` donne 38 % de fenêtres 180 j négatives — mais ces 256
   jours sont exactement 2024-04 → 2024-12, c'est-à-dire le drawdown −51,4 %
   lui-même. Le segment est sélectionné sur le résultat. **Inutilisable.**
2. **Le cap n'est pas un effet de taille de compte.** `base_size` et le cap sont
   tous deux proportionnels au capital : le facteur `pct × poids_z ×
   signal_mult` vaut S1 0,341 · S5 0,473 · S8 0,459 · S9 0,840 · S10 0,315,
   tous > 0,30. Le cap mord **à tout niveau de capital**, backtest inclus.
   Corrige la note du 2026-08-29 qui suggérait de rouvrir le sujet « si le
   capital double » : doubler le capital n'y changerait rien.
