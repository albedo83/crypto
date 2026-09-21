# Déploiement 1.28.0 — veille IA économique

Haiku 4.5 daté (claude-haiku-4-5-20251001) remplace Opus pour la recherche et
l'extraction externes. Aucun fallback vers Opus ; les modèles des autres rôles
ne changent pas. Une recherche maximum par actif, sorties bornées à 1800 tokens,
extraction limitée à six sources et 18000 caractères de citations originales.
Ces limites peuvent réduire la couverture : une recherche sans preuve exploitable
ne justifie jamais une intervention. Contrôles domaines, citations et dates conservés.

Un passage au plus toutes les deux heures, quatre actifs maximum et macro si due.
Actifs détenus et macro : six heures entre tentatives ; autres actifs : 24 heures.
La réservation est écrite avant les appels et survit aux redémarrages/crashes.
Une configuration de cadence illisible suspend les appels. Le cache existant compte
lors de la migration. Les dates d'expiration des faits ne sont jamais prolongées.
Le volume interne des résultats renvoyés par l'outil web n'est pas un plafond de
tokens garanti : la borne d'extraits concerne l'étape d'extraction.

Un refus de plafond mensuel arrête la suite du lot et persiste une suspension
jusqu'à la date du fournisseur. Le refus observé annonçait le 1er octobre 2026 à 00h UTC, mais des appels
réussis à 10h22 ont ensuite confirmé le rétablissement avant déploiement.
Aucun blocage périmé n'a été imposé après ces nouveaux succès.
Aucun quota augmenté. Si la limite du compte est relevée avant, cette suspension
devra être levée explicitement après vérification ; pas de sondage payant répété.

Comptabilité : tokens et recherches web dans AI_COST, identifiant unique de réponse
et reçu transactionnel empêchant les doublons. Archives historiques réintégrées
avec leurs dates d'origine ; seconde importation = zéro ajout. Les événements
incluent aussi les appels réussis suivis d'une erreur d'extraction. Les appels
perdus avant archivage et les autres applications restent hors de cette mesure :
il s'agit d'une reconstitution locale, pas de la facture fournisseur.

Validation : 140 tests Python et deux tests DOM. Contrôles de reprise, réservation
avant erreur, absence d'appel sans Live, cache récent même sans faits, plafond,
borne d'extraits, tarification et déduplication. Quatre bots redémarrés et OK ;
API vérifiée pour modèle prévu, suspension fournisseur et montant external intégré.
Campagne shadow-v2 conservée ; interruption visible. IA réservée à Live,
arbitres entry/CUT/LOCK toujours en observation ; aucune règle de trading modifiée.

Validation du modèle : essai réel limité archivé dans haiku_smoke.json après
rétablissement observé du fournisseur. Cela valide l'intégration sur un dossier,
pas la qualité sur tout le corpus ni l'utilité financière. Pas de projection
de coût journalier ni de promesse de gain financier.

Références officielles :
- https://platform.claude.com/docs/en/about-claude/pricing
- https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool
Preuves : deploiement_1_28_0.json.
