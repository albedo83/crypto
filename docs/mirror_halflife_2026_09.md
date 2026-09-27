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

## Ce que cette expérience ne fera PAS

- Elle ne récupère pas les 130 $.
- Elle ne tranche pas à elle seule entre « le live a eu un mauvais tirage » et
  « le live est structurellement désavantagé » : il y faudra la série.
- Elle ne dit rien des règles elles-mêmes — `rules.py` est commun aux deux.

## Journal

| Date | Événement |
|---|---|
| 2026-09-27 | Grille figée et committée. Mirror configuré, non démarré. T0 en attente du redémarrage. |
