# Objectif économique et première réduction de dépenses

Priorité utilisateur : couvrir progressivement 100 EUR/mois de dépenses IA ;
50 % de couverture serait déjà utile. Capital engagé 500, perte maximale
acceptée 250. Le gain personnel de 250 sur six mois devient secondaire.
Les 100 EUR/mois sont l’abonnement assistant, confirmé par l’utilisateur.
Les coûts API du bot s’ajoutent ; leur réduction ne diminue pas cet abonnement.
Le relevé API ci-joint est en USD, estimé selon les tarifs locaux enregistrés,
et ne constitue ni une facture exhaustive ni une vérification des prix actuels.

## Livraison 1.23.2

Les consultations IA d'entrée se faisaient avant les contrôles de portefeuille.
Le nouveau préfiltre ne s'applique qu'en mode shadow (y compris disjoncteur
forçant l'observation). Les signaux ne sont pas supprimés : la boucle normale
les traite et vérifie de nouveau les règles avec les compteurs actualisés.
Le mode act reste inchangé, car modifier le contexte de son lot peut changer
ses verdicts. Le préfiltre ne réserve pas de places à des ordres hypothétiques.
Les candidats passant le préfiltre peuvent encore être refusés plus tard.

L'événement ARBITER_ENTRY_PREFLIGHT compte les signaux considérés, les motifs
et les lots vides évitant un appel. Il n'attribue pas de dollars économisés.
Les comparaisons de verdicts doivent distinguer la nouvelle version/politique,
car enlever des candidats du contexte peut modifier les réponses shadow.

Validation : 25 tests, dont 40 scénarios d'exécution identique shadow/off,
cas de portefeuille plein, cooldown, pause, OI absent, secteur saturé,
veto shadow sans effet sur taille, ordre refusé puis candidat suivant,
et recontrôle de capacité après le premier fill. Aucun appel réseau dans les tests.

## Moteur et backtests autorisés

Les signaux et le moteur peuvent être revus. Pour toute nouvelle hypothèse :
consulter les recherches précédentes, décrire le mécanisme et le test avant
exécution, inclure frais/funding et variantes de coûts, publier toutes les
fenêtres et le risque. Une amélioration historique isolée n'autorise pas à
augmenter le risque Live. Les fenêtres déjà explorées ne sont pas hors sélection.
La référence virtuelle prospective et le remplacement du scorecard restent
nécessaires avant toute conclusion sur la contribution de l'IA aux gains.
