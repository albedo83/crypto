# Alfred 1.25.0 — mission IA : contexte externe sourcé

## Constat et changement

Les appels des arbitres d'entrée/sortie n'envoyaient aucun outil de recherche
à l'API. Ils exploitaient surtout les indicateurs Hyperliquid ; la disponibilité
générale du web dans un assistant n'impliquait pas son activation dans le bot.

Une veille asynchrone utilise désormais l'outil web serveur Anthropic. Elle
cherche des incidents, annonces officielles, déblocages, cotations/délistings,
changements de gouvernance et événements macro dans un horizon de 72 heures.
Positions Live prioritaires, puis rotation de six actifs ; lot limité à huit,
recherche globale limitée à huit appels, couverture explicitement partielle.
Sources officielles ciblées pour OP, SEI, SNX et macro ; découverte pour les autres.
La collecte se fait au démarrage puis chaque heure, ou après 15 minutes en cas
d'erreur. Elle n'est pas exécutée dans le chemin d'ouverture/fermeture d'ordres.

Une seconde passe extrait les faits en JSON. URL obligatoirement présente dans
une citation web structurée réellement retournée ; source qualifiée primaire,
publication dans les sept derniers jours, événement entre J−3 et J+3, dates
UTC explicites. Données sans date, secondaires, hors fenêtre ou sans citation
refusées. Qualification primaire et dates restent des extractions par IA,
**pas une certification de véracité**. Les contenus web restent non fiables et
ne sont jamais traités comme instructions ou autorisations.

Les arbitres changent de mission : évaluer l'effet possible de ces faits sur
le risque du trade, plutôt que refaire les signaux techniques. Toute proposition
VETO/réduction/CUT/LOCK doit citer un identifiant frais applicable à l'actif ou
MACRO. Absence de preuve -> GO taille pleine / HOLD, sans appel arbitre inutile.
Les données du marché servent à contextualiser l'impact, jamais à inventer une
information externe. Les anciennes décisions ne sont pas réinjectées comme
prior pour ce nouveau rôle. Les hashes de prompt changent.

## Observation et mesure

Modes d'entrée, CUT et LOCK maintenus en shadow. Aucune activation réelle,
aucun changement de signaux, sizing ou règles de sortie. Les anciens avis et
ceux de cette mission constituent des cohortes différentes : ne pas les agréger
pour conclure à la valeur de l'IA. Comparer ensuite gains évités, gains sacrifiés
et risques, en conservant frais de trading/slippage/funding ; les coûts API
sont exclus du critère de décision conformément à la consigne utilisateur.

État, couverture et sources visibles dans l'onglet Expériences. Les trois
portefeuilles virtuels continuent sans recevoir ces informations externes.
Le redémarrage de toute la flotte conserve leur historique et leurs positions ;
une interruption supérieure à 120 secondes est signalée par leur contrôleur
existant. Aucune réinitialisation de campagne ni effacement de lacune.

## Traçabilité et exploitation

`alfred/data/external_context/latest.json` : cache public du contexte, TTL six
heures par fait. Un fait expiré est retiré à la lecture et au contrôle du verdict.
`batches/` : réponses brutes web et extraction, même recherche tronquée archivée.
`decisions.jsonl` : contexte exact, identifiants, candidats/positions et verdicts
normalisés par appel. Une panne de journalisation fait échouer cet appel sans
agir. Aucun ordre ni notification dans le collecteur.

`AI_EXTERNAL_ENABLED=0` désactive la collecte et le contexte au redémarrage.
`AI_EXTERNAL_MODEL` permet de choisir le modèle ; défaut modèle d'entrée existant.
Les erreurs réseau, quotas et réponses incomplètes sont visibles ; elles ne
remplacent pas le cache par un résultat artificiellement rassurant. Des faits
antérieurs encore frais peuvent rester disponibles pendant une erreur de collecte.
Pas de résultat ne signifie jamais absence de risque. Le suivi n'est ni exhaustif
ni instantané et ne couvre pas toutes les nouvelles ou calendriers connus anciens.

## Validation et références

88 tests Python couvrant les contrôles de preuve, péremption, mauvais actif,
URL non citée, neutralisation d'avis non fondés, API admin et non-régressions.
Tests DOM : texte non fiable, refus des liens javascript, effacement après erreur.
Test API web réel isolé : recherches réellement effectuées, citations reçues,
extraction exécutée ; résultats secondaires refusés, sans forcer l'existence
d'un fait exploitable. Les tests ne prouvent pas une amélioration de rentabilité.

Documentation officielle : [outil web Anthropic](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool).
Sources de départ vérifiées : [Optimism](https://www.optimism.io/blog/),
[Sei](https://blog.sei.io/), [Synthetix](https://blog.synthetix.io/).
