# Déploiement Alfred 1.23.1 — 17 septembre 2026

Livraison : commit `2de2c53`. Autorisation utilisateur : tous les bots suivent ;
« restart » couvre toujours Paper, Live, Junior et Baby.

Arrêt propre constaté le 17 septembre à 12:43:07 UTC ; nouveau processus
1610521 (ancien 1556449). Données restaurées et web disponible à
12:45:43 UTC. Initialisation totale proche de 2 min 36 s ; la première vérification
web a expiré pendant le démarrage puis a réussi une fois la restauration terminée.

Contrôles :
- 16 tests ciblés réussis dans la copie préparée et dans la production.
- Contrôle de cohérence de la documentation des sorties passé au commit.
- API maître : version 1.23.1, quatre bots, WebSocket connecté.
- Santé Paper/Live/Junior/Baby : `ok`, sans dégradation, non pausés.
- Positions restaurées : Paper 4, Live 3, Junior 5, Baby 3.
- Notes v1.23.1 effectivement servies par le dashboard.
- Configuration IA : entrée shadow ; sortie CUT/LOCK shadow.
- Aucun changement de sizing, des quatre slots macro ou des seuils de trading.
- La référence virtuelle et le remplacement du scorecard restent à implémenter.

L'application initiale du patch a été interrompue par des permissions filesystem.
L'installation a ensuite été terminée avec droits approuvés, empreintes de tous les
fichiers vérifiées et originaux sauvegardés avant le redémarrage.

Sauvegarde des sources précédentes :
`/tmp/alfred-next-20260917/production_before_1.23.1` ; base Git `c69131b`.
Les bases et états de trading n'ont pas été remplacés par le déploiement.
