# Déploiement Alfred 1.26.1 — 21 septembre 2026

Commits `56acc75` (1.26.0) et `75237ba` (1.26.1). 106 tests Python sur les
fichiers installés, test DOM et contrôle documentaire des sorties réussis.
Tous les fichiers livrés contre-vérifiés par SHA-256. Les quatre bots ont été
redémarrés ensemble et répondent `ok` après reprise du marché.

La première vérification 1.26.0 a révélé un rejet injustifié d’une citation
Avalanche parce que l’extracteur copiait le résumé IA. Correction 1.26.1 :
seuls les textes sources lui sont transmis. Le même dossier réel a ensuite
produit la citation exacte et la bonne heure, sans relâcher la validation.
Les appels réels des arbitres ont aussi été testés sur un contexte isolé ;
GO/HOLD prudents. Ce test de raccordement n’est pas une mesure de performance.

## Première collecte effective de 1.26.1

État `partial`, 22 recherches, 2 faits retenus :
- AVAX : L'upgrade Helicon est prévu sur le mainnet Avalanche, activant le staking auto-renouvelé — 2026-09-22T15:00:00Z
- MACRO : Publication de la balance des transactions internationales et de la position d'investissement des États-Unis, 2e trimestre 2026 — 2026-09-24

Le calendrier BLS refuse l’accès HTTPS direct depuis le serveur (403 lors de
l’essai). La couverture incomplète est visible ; les autres calendriers et les
faits utilisables restent disponibles. Les erreurs ne prouvent pas l’absence
d’événement. Les heures non vérifiables sont ramenées à la journée.

IA exclusivement Live ; entrée/CUT/LOCK restent en observation. Pas de changement
des règles, du levier, du sizing ni des paramètres des portefeuilles.
Aucune rentabilité supplémentaire démontrée. Pas d’estimation du coût journalier.

Les expériences conservent leur date de départ, empreinte de moteur, états et
historique. Leurs trois checkpoints ont été contre-vérifiés. Les interruptions
liées aux deux redémarrages restent visibles : compteur 2 → 6 ;
comparaison toujours marquée non validée, sans remise à zéro.

Voir [preuves de contrôle](deploiement_1_26_1.json) et
[fonctionnement et limites](external_context_1_26_0.md).
