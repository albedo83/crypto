# Déploiement Alfred 1.25.0

Commit `7972b17`. 88 tests Python sur les fichiers installés, test DOM et contrôle
documentaire des sorties réussis. Test web réel isolé, puis collecte effective
vérifiée en production via API admin (nombre de recherches strictement positif).

Paper, Live, Junior et Baby redémarrés proprement et vérifiés `ok`.
Arbitres entrée/CUT/LOCK maintenus en observation. L'IA et la nouvelle veille
web sont réservées à Live : ni Paper, ni Junior, ni Baby, ni les portefeuilles
expérimentaux ne les appellent. Le coût API est exclu du critère de performance,
ce qui ne supprime pas la restriction de périmètre demandée par l'utilisateur.

Les trois expériences reprennent leur historique, leurs positions, leur date
de départ et leur empreinte de moteur. L'interruption du redémarrage dépasse
120 secondes : elle reste signalée ; comparaison marquée non validée par le
contrôleur existant. Aucun effacement ni redémarrage de campagne.

Le contexte externe est visible dans Expériences. Une collecte réussie peut
ne fournir aucun fait admissible : ce cas conduit à GO/HOLD, pas à une preuve
que le marché est sans risque. Aucune amélioration de rentabilité démontrée.

Voir [preuves de santé](deploiement_1_25_0.json) et
[protocole, limites et sources](external_context_1_25_0.md).
