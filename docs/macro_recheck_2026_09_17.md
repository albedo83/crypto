# Contre-vérification du plafond macro — 17 septembre 2026

Protocole écrit avant exécution : [reprise](reprise_2026_09_17.md).
Base c69131b, noyau de trading inchangé. Quatre semestres disjoints, capital initial 1 000 $ par semestre. Bornes UTC à 12:00, fin exclue.

**Résultat : la robustesse du passage de 3 à 4 slots n’est pas confirmée par ces fenêtres.** Deux semestres se dégradent nettement, le plus récent est quasi neutre en rendement avec un drawdown plus profond, un semestre est identique (aucun S1). Aucun rollback automatique.

| Semestre | Capital 3 slots, slip 4 | Capital 4 slots, slip 4 | Δ rendement (pp) | Δ capital, slip 6 | DD 3 / 4, slip 4 |
|---|---:|---:|---:|---:|---:|
| 2024-09-16 → 2025-03-16 | 1763.35 $ | 1622.03 $ | -14.13 | -139.92 $ | -37.48 / -37.48 % |
| 2025-03-16 → 2025-09-16 | 1492.18 $ | 1414.09 $ | -7.81 | -77.28 $ | -31.49 / -31.49 % |
| 2025-09-16 → 2026-03-16 | 3487.15 $ | 3487.15 $ | +0.00 | +0.00 $ | -23.26 / -23.26 % |
| 2026-03-16 → 2026-09-16 | 1048.64 $ | 1048.13 $ | -0.05 | -1.10 $ | -31.31 / -32.62 % |

## Portée et limites

- Le résultat ne contredit pas arithmétiquement le tableau 28/12/6/3 mois du 16 septembre : les fenêtres et leurs états initiaux sont différents. Il contredit une interprétation générale de robustesse fondée uniquement sur ce tableau.
- Les quatre semestres sont disjoints, mais déjà explorés par la recherche du projet ; ils ne sont pas une nouvelle validation hors sélection.
- Les variations proches de zéro ne constituent pas une preuve de supériorité. Le Δ récent de −0,51 $ pour 1 000 $ est faible.
- Le drawdown est la métrique native du backtest, pas un nouvel audit de risque marqué au marché haute fréquence.
- Augmenter les slots n’a pas été testé ici comme moyen de rapprocher Live et Paper ; aucune conclusion causale à ce sujet.
- Les heures-position sans échantillon funding récent représentent au maximum 1.78 % selon le run. Leur impact n’est pas neutralisé ; le verdict conserve cette réserve.
- OI : 34 séries chargées ; TON s’arrête au 15 juin 2026 ; la politique de donnée périmée du moteur reste inchangée. Les ranges et empreintes sont publiées dans le JSON.

## Contrôles

- Configuration identique entre variantes, sauf max_macro_slots.
- Sources bougies/OI/funding identiques entre premier run invalidé et run corrigé (hashes vérifiés).
- La répétition à 4 bps reproduit exactement les premiers résultats.
- Coût effectif hors funding = 13 puis 15 bps ; les résultats changent effectivement à 6 bps de slippage.
- Aucun appel exchange, aucun ordre, aucune modification des règles ni de la production.

Données détaillées : [JSON](macro_recheck_2026_09_17.json). Premier run avec alias de coût inerte conservé dans [archive invalidée](macro_recheck_2026_09_17_invalid_cost_alias.json).

## Recommandation

Déclasser « amélioration robuste validée » en « modification à preuve mixte et dépendante de la fenêtre ». Ne pas augmenter davantage l’exposition. Présenter séparément à l’utilisateur toute proposition de retour à trois ; le présent lot 1.23.1 conserve quatre et ne change aucune règle de trading.
