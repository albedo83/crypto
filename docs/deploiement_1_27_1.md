# Déploiement 1.27.1 — 21 septembre 2026

Le contrôle de revue IA ne considère plus l'âge du dernier appel facturé comme
un signal de panne. Le détecteur événementiel doit passer depuis moins de dix
minutes ; chaque demande lancée reçoit un identifiant, transmis jusqu'au résultat
POSITION_REVIEW persisté. Une demande sans résultat après cinq minutes, un échec
non suivi d'un succès, une source absente ou invalide restent signalés.
Les appels refusés par le plafond ne créent pas de demande en attente ; l'absence
de position ou une revue désactivée est un résultat explicitement ignoré.
Aucun appel IA supplémentaire ni message externe n'a été lancé pour cette validation.

Le tableau de bord distingue le rapport daté et le contrôle actuel déterministe.
L'alerte historique de 9 h 30 reste conservée ; le contrôle courant indique OK.
Elle n'a pas été effacée ou réécrite, et aucun nouvel audit LLM n'est fabriqué.

128 tests Python, dont 13 tests du cycle de revue, et deux tests DOM passent.
Cas vérifiés : marché calme, détecteur arrêté, horodatage invalide, base absente,
demande en cours puis expirée, résultats corrélés, erreur/rétablissement, timeout,
processus sans revue persistée, autre demande ne pouvant prouver le succès,
absence de position et plafond d'appels.

Les quatre bots ont redémarré et leur santé est OK sous 1.27.1. Les contrôles API
confirment l'affichage du rapport historique et du contrôle actuel. Les empreintes
des fichiers déployés et modes IA shadow sont vérifiés. Aucun changement de règles
de trading ni d'empreinte de la campagne shadow-v2 ; son origine et ses données
sont conservées, l'interruption du redémarrage reste comptée dans ses lacunes.

Limite externe observée pendant le contrôle : la veille external-v2 reçoit un
refus HTTP 400 du fournisseur IA (« You have reached your specified ... »),
et son état est error. Les anciennes preuves encore valides restent disponibles.
Cette indisponibilité est distincte du défaut de surveillance corrigé ; aucun
quota n'a été augmenté. La santé OK des bots ne signifie pas que l'API IA répond.

Preuves : deploiement_1_27_1.json. La télémétrie par identifiant couvre les nouvelles
demandes ; les anciens événements conservent leur format et leurs limites.
