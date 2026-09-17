# Déploiement Alfred 1.24.0

Commit de livraison `7bb5795`. 61 tests Python passent sur les fichiers installés,
plus le test DOM JavaScript (valeurs absentes, texte non fiable, courbes, erreur
et reprise). Le contrôle documentaire de la chaîne des sorties passe.

Arrêt propre et redémarrage commun : Paper, Live, Junior et Baby vérifiés `ok`.
API authentifiée : version 1.24.0, quatre bots officiels, trois portefeuilles
virtuels `running`, référence et deux variantes, comparaison sans lacune au
contrôle initial. États séparés et absence d'événements de coût IA vérifiés.
Dashboard et notes de version accessibles. Première revue indiquée par
`review_at` dans les [preuves API](deploiement_1_24_0.json).

Les variantes n'envoient aucun ordre exchange. Aucune amélioration de
rentabilité n'est encore démontrée. Le correctif de borne conserve exactement
la référence historique vérifiée (500 → 811,3296886635744 ; 293 trades).
Voir le [protocole](experiments_shadow_v1.md) pour les limites de simulation.
