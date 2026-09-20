# Notes des livraisons de recherche

Les versions de recherche identifient les protocoles et artefacts. Elles ne
changent pas la version du bot tant qu’aucun code d’exécution n’est déployé.

## AI-SHADOW-20260920-v1 — 2026-09-20

Nouvel audit IA en lecture seule : réductions de taille selon facteurs effectifs,
LOCK shadow rejoués après décision, CUT au premier tick suivant si encore perdant.
Un résultat par trajectoire de position ; lacunes et positions ouvertes exclues.
INJ/ARB : delta de taille +1,71 USDT ; ENA : delta de prix +1,67 USDT, sans
funding différentiel. Preuve insuffisante pour activation. Coûts API séparés,
14 tests avec l'audit de protection, données archivées et contre-calcul.
Rapport : `docs/ai_shadow_20260920.md`. Aucun changement du scorecard historique,
du disjoncteur ou des modes IA. Alfred reste en 1.24.0, aucun restart.

## EXIT-PROTECT-20260920-v1 — 2026-09-20

Protection après +10 % de mouvement favorable, restitution de 50 % du pic,
exécution au tick suivant. 86 trades S1/S5 examinés, 55 couverts, 31 exclus.
Delta de prix +3,52 USDT avant le 17 septembre, +10,24 sur un trade récent.
Effet portefeuille et funding différentiel non mesurés. Pas de déploiement.
Rapport : `docs/exit_protection_20260920.md`.

## ENGINE-20260917-v1 — 2026-09-17

Trois agents : sorties S1, classement par funding, plafond par stratégie et sens.
64 runs corrigés, huit témoins indépendants, 22 tests, rapprochement intégral des
24 ledgers témoins, contre-vérification comptable et du risque aux clôtures 4h.
Défaut de borne corrigé dans les copies de recherche et résultats initiaux
archivés comme invalides. S1+24h et plafond3 restent exploratoires avec compromis ;
aucun déploiement de stratégie. Alfred reste en 1.23.2.
Rapport : `docs/engine_research_20260917_v1.md`.

## CASCADE-v1 — 2026-09-17

Étude du rebond après contraction extrême d’OI, volume et amplitude élevés.
Seuils calculés sur le passé, cinq tests, 729 résultats revérifiés indépendamment.
Gate exploratoire échoué ; aucun signal déployé.
Rapport : `docs/cascade_v1_report.md`.
