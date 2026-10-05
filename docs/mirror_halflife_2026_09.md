# Demi-vie de la divergence — grille pré-enregistrée (2026-09-27)

**Grille écrite et committée AVANT le lancement.** Aucune mesure n'existe à
l'heure où ce document est figé.

## La question

L'audit du 2026-09-27 (`docs/`, mémoire `project-live-paper-attribution-2026-09`)
a établi que l'écart live/paper de −135,71 $ est **entièrement attribuable** :
9 % d'exécution sur les 78 trades appariés, 96 % dans les trades que les deux
bots n'ont pas pris ensemble, et sur les 35 j postérieurs au 2026-08-23
`compare_bots` ne trouve **aucune divergence de noyau**.

Il reste une inconnue : **à quelle VITESSE deux moteurs identiques se
séparent-ils ?** Sans ce nombre, un écart futur n'a aucune référence. « L'écart
n'est pas distinguable du hasard » est un constat statistique ; la demi-vie de
la divergence en serait la mesure mécanique.

## Le dispositif

Un 5ᵉ bot **`mirror`**, mode paper, **forké de live** au T0 :
`state.json` (positions, cooldowns, `signal_first_seen`, borne de scan 4 h, pic,
tampon du frein equity) **et** `bot.db` copiés à l'identique. Aucune clé, aucun
argent, aucun appel IA.

**Le paper n'est pas touché** : il reste la série de référence, avec son ancrage
tracker au 2026-07-09 et le seuil d'arrêt à −25 %.

Ce que le montage isole : le mirror clone les **décisions** de live avec une
exécution idéalisée (fills au mark, `paper_slippage_bps = 0`). Toute divergence
vient donc de l'exécution réelle de live ou de ses mécanismes propres — au
premier rang le **hard-stop côté exchange** (`hard_stop_enabled` est un override
de live ; le paper ne peut pas armer de trigger HL). Une coupe du trigger sur une
mèche que le mirror aurait gardée est un résultat ATTENDU et à compter, pas un
bug.

**T0 = le redémarrage qui charge le mirror.** Vérifié dans le code : `bots.json`
ne prend effet qu'au restart (`/api/botsconfig` : « effet au prochain restart »),
donc le T0 est net et daté.

## Mesures (fixées à froid)

1. **Date et heure de la première divergence** — premier `OPEN` non partagé, ou
   première raison de sortie différente sur une position commune.
2. **Sa catégorie**, par `python3 -m alfred.tools.compare_bots --a live --b mirror`.
3. **Le trade déclencheur** et le mécanisme exact.
4. **Délai T0 → première divergence**, en jours.
5. **Écart d'equity à J+7 / J+14 / J+30.**

## Prédiction pré-enregistrée

La première divergence viendra d'une **décision à cheval sur un seuil** (stop,
trail, ou borne du cap de sizing 0,3 × equity), **pas** d'une différence de fill,
et tombera en **jours, pas en semaines**.

**Réfutation :** si la première divergence est un écart de taille suffisant pour
libérer ou bloquer un slot, ou si les deux bots restent identiques au-delà de
14 jours, la prédiction est fausse.

## Protocole de répétition

**Re-synchroniser le mirror à CHAQUE divergence constatée** (même procédure de
fork). Une exécution donne n=1 ; une demi-vie est une variable aléatoire. Seule
la **série** de demi-vies et d'écarts a une valeur.

## Seuils d'arrêt — pré-enregistrés le 2026-10-05, avant toute nouvelle divergence

Calibrés sur le bruit d'exécution mesuré de l'époque 1 (15 trades appariés, même
raison de sortie) : **−0,11 $/trade en moyenne (−7 bp), écart-type 0,51 $ (34 bp)**.
Vérifiés par `python3 -m alfred.tools.mirror_guard` (lecture seule, code 2 si STOP).

| seuil | règle | justification |
|---|---|---|
| **S1 noyau** | ≥ 1 divergence `LOGIC` de `compare_bots` dans l'époque | live n'exécute plus le système validé |
| **S2 exécution** | moyenne ≤ −0,50 $/trade sur les 30 derniers appariés (n ≥ 20) | ≈ 4 erreurs-types sous le bruit mesuré |
| **S3 chemin** | réalisé live − mirror ≤ −40 $ dans une époque | ≈ 8 % du capital ; l'incident du 02/10 valait −29 $ |
| **S4 fréquence** | plus de 2 re-synchronisations en 14 jours | divergences trop fréquentes pour être accidentelles |

**Action, sans débat le jour J** : pause des **entrées** live
(`POST /bot/live/api/pause`) ; les positions ouvertes gardent leurs règles de
sortie ; aucune liquidation. **Reprise** : uniquement après attribution écrite
de la cause dans ce journal. Les seuils ne se modifient pas après coup ; un
changement = nouvelle version datée de cette section, avant observation.

## Ce que cette expérience ne fera PAS

- Elle ne récupère pas les 130 $.
- Elle ne tranche pas à elle seule entre « le live a eu un mauvais tirage » et
  « le live est structurellement désavantagé » : il y faudra la série.
- Elle ne dit rien des règles elles-mêmes — `rules.py` est commun aux deux.

## Journal

| Date | Événement |
|---|---|
| 2026-09-27 | Grille figée et committée. Mirror configuré, non démarré. |
| **2026-09-27 11:04 UTC** | **T0.** Fork de live : 4 positions, capital $518,34, P&L réalisé $17,67, 139 trades. Mirror chargé au démarrage de 11:06. |
| 2026-09-27 11:10 UTC | Contrôle de T0 : `compare_bots --a live --b mirror --hours 168` → **0 divergence**, 15/15 sorties appariées, taille ×1,00 pour un ratio de soldes ×1,00. Clone exact confirmé. |
| **2026-10-02 16:03 UTC** | **1ʳᵉ divergence, J+5,2.** Catégorie STATE. Déclencheur : UNI et NEAR (échéance timeout 16:03:43). Mirror les ferme à 16:03:49 puis scanne à 16:03:53 → entre IMX S5 SHORT et INJ S10 SHORT. Live, scan différé (cooldown), scanne à 16:03:51 **sans repasser par les sorties** → `max_token` ; UNI/NEAR fermés à 16:04:05. Cascade : live entre IMX à 20h → exchange_stop −23,57 $ (mirror IMX : −1,30 $). Écart réalisé au 10-05 : live −61,35 $, mirror −32,01 $. **Prédiction confirmée** (décision à cheval sur un seuil, en jours). Ce n'est pas du hasard mais un défaut de séquencement présent sur ~25 % des timeouts de chaque bot depuis juillet → corrigé en v1.30.0. Re-synchronisation du mirror au redémarrage qui charge la v1.30.0. |
| **2026-10-05 11:24 UTC** | **T0 de l'époque 2.** Re-synchronisation au redémarrage qui charge la v1.30.0 : 3 positions, capital $518,34, P&L réalisé −$43,70. |
