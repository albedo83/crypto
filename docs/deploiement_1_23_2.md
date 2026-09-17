# Déploiement Alfred 1.23.2

Commit de livraison `a7a4efe`. Les 25 tests passent, dont 40 scénarios shadow/off
avec exécutions identiques. Contrôle de documentation des sorties passé au commit.
Arrêt propre et redémarrage commun effectués ; Paper, Live, Junior et Baby
répondent `ok`. Version et notes du dashboard vérifiées.
Voir [preuves API et horodatages](deploiement_1_23_2.json).

Le filtre n'agit que sur les consultations de l'IA d'entrée en observation.
Signaux, seuils, sizing et noyau des sorties restent inchangés. Il ne réserve pas
de slots futurs et les contrôles d'ordre sont réévalués après chaque fill.
Les économies ne sont pas encore observées : les prochains scans 4h produiront
les événements ARBITER_ENTRY_PREFLIGHT. Aucune rentabilité supplémentaire
n'est attribuée à cette version avant observation.

Les 100 EUR/mois sont l'abonnement assistant, confirmé par l'utilisateur.
Les frais API du bot viennent en supplément et restent estimés en USD.
La référence virtuelle et le scorecard fiable restent à développer ; les
recherches sur les signaux et le moteur sont autorisées avec contre-vérification.
