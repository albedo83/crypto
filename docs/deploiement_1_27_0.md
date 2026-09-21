# Déploiement Alfred 1.27.0 — 21 septembre 2026

Correction de cadence : le scan d’entrée unique attend les cooldowns des actifs
non détenus qui expirent au plus tard cinq minutes après la clôture 4h.
Le scheduler commence habituellement à +3 minutes : attente supplémentaire
bornée à deux minutes, puis reprise au prochain tick disponible (cadence 20 s,
latence effective variable). Tous les candidats du bot attendent ensemble.
Un cooldown plus long conserve son comportement normal ; aucun délai de 24 h
n’est raccourci. Il n’y a aucun rejeu d’un scan déjà consommé.

Le gate non consommé est sauvegardé pendant l’attente. Après expiration, prix
frais, bougie roulée, pause, frein de risque et contrôles du portefeuille restent
obligatoires. Aucun changement de taille ni de plafond. Aucun gain chiffré promis.

Validation : 115 tests Python et test DOM réussis avant et après installation.
Les tests ciblés couvrent expiration exacte, scan unique, reprise persistée,
absence de rejeu après reprise, cooldown long, plafond temporel, pause/frein,
prix périmés et bougie manquante. Ils reproduisent le défaut de cadence observé.

Les quatre bots (Paper, Live, Junior, Baby) ont été redémarrés ensemble et leur
API de santé indique OK sous 1.27.0. IA exclusivement Live ; modes entry/CUT/LOCK
shadow vérifiés. Veille external-v2 disponible.

Le changement du moteur invalide la comparabilité avec l’ancienne campagne :
shadow-v1 reste sur disque, son origine et son empreinte sont conservées ;
shadow-v2 démarre trois nouveaux portefeuilles à 500 USDT avec ses propres dates.
Les résultats ne sont pas fusionnés et la date de revue repart de ce démarrage.

Preuves : deploiement_1_27_0.json. Prochaine vérification utile : un événement
ENTRY_SCAN_DEFERRED suivi d’un seul scan après expiration. Sans occurrence réelle,
les contrôles de déploiement et tests ne prouvent pas un effet sur le rendement.
