# Alfred 1.26.0 — veille externe exploitable

## Défauts observés le 21 septembre

Le contrôle de 05:52 UTC avait trouvé 14 collectes / 97 recherches, zéro fait
retenu. Principaux rejets : sources secondaires, dates absentes, événements
hors fenêtre. Les domaines n'étaient que des suggestions ; huit actifs et la
macro se partageaient quelques recherches. Le filtre de publication à sept
jours rejetait aussi une annonce ancienne portant sur un événement futur.

## Correction

Chaque actif a désormais son propre dossier de recherche, son identité de
projet et ses domaines autorisés dans l'outil web Anthropic. Registre des 34
actifs de l'univers ; domaines vérifiés aussi côté Python après extraction.
GitHub limité au chemin de releases NEAR, jamais accepté globalement. Des
rubriques d'annonces de plateformes complètent les sites des projets ; leur
qualification primaire reste dépendante de l'extraction (pas leurs éditoriaux).
MACRO est un dossier distinct : lecture HTTPS directe des quatre calendriers
officiels Fed, BLS, BEA, BCE (URL fixes, sans redirection, délais et taille
bornés), puis extraction IA. Une page inaccessible est signalée, pas assimilée
à une absence d’événement. Le calendrier annuel BLS suit l’année courante.
Trois dossiers simultanés au maximum ; trois recherches demandées par dossier,
limite technique quatre, huit actifs au maximum plus la macro. Les limites
ne garantissent pas une couverture exhaustive des incidents ou plateformes.

L'extraction doit fournir un passage copié exactement d'une citation API.
Contrôle du domaine, de l'URL citée ou lue directement, de l'applicabilité,
du passage exact et de la présence de la date de l’événement dans ce passage.
Le contrôleur de date reconnaît ISO, dates numériques et mois anglais explicites ;
un autre format peut être rejeté même s’il est légitime. Les détails d’une même
annonce datée ne deviennent pas des événements indépendants. Les grands
calendriers restent dans les archives ; seuls les extraits utiles vont aux arbitres.
Une citation exacte n'est pas une preuve automatique de son interprétation :
le sens et la conversion de date restent extraits par IA et non certifiés.

Deux traitements temporels :
- `recent` : publication connue dans les sept jours, événement survenu dans
  les 72 dernières heures ; pas de recyclage d'un vieux récit.
- `scheduled` : événement explicitement programmé dans les sept jours à venir,
  publication ancienne ou absente autorisée. Une programmation expire au plus
  tard à son heure prévue : elle ne devient jamais automatiquement une preuve
  de réalisation. Une heure sans fuseau vérifiable dans l’extrait est ramenée à la journée ;
  les conversions UTC/ET/CET reconnues sont contrôlées. Une journée sans heure
  reste une journée, pas minuit UTC
  présenté comme une heure certaine. L'intervalle de journée utilise UTC pour
  son bornage ; les limites du fuseau d'origine restent une incertitude.

TTL maximal six heures. Les arbitres reçoivent l'heure actuelle, le type de
fait et la précision temporelle. Leur prompt exige de relier le fait au sens,
à la durée et au risque du trade. Calendrier macro = volatilité potentielle,
pas direction. Mise à niveau ordinaire ≠ motif automatique de sortie.
Nouveaux hashes de prompt, cohorte external-v2 séparée de v1.

Les positions Live sont prioritaires (jusqu'à six places), puis les actifs
les moins récemment recherchés, selon un état persistant. Deux places au moins
restent disponibles pour la rotation de l'univers. Une erreur sur un dossier
n'efface pas les résultats des autres ; ses anciens faits encore frais peuvent
rester utilisables jusqu'à expiration. État global `partial` si certains
échouent, `error` si tous échouent ; collecte horaire si au moins un dossier réussit, retry 15 minutes si tout échoue.
Chaque dossier archive la réponse web avant l'extraction et son résultat final.
Le dashboard affiche les sources/faits et les erreurs par actif.

## Périmètre et validation

IA et collecte exclusivement pour Live. Entrée, CUT et LOCK restent shadow.
Pas de modification des règles, tailles, fonds, signaux ou paramètres des bots.
Aucun apport au PNL n'est encore démontré. Les coûts API sont exclus de cette
évaluation. Ne pas comparer les PNL sur des contextes inventés ou historiques
récupérés après coup comme s'ils avaient été disponibles à la date du trade.

106 tests Python : preuve, domaines et chemins trompeurs, citation inventée,
publication ancienne et calendrier sans publication, précision journalière,
expiration à l'événement, identité de preuve au rafraîchissement, archives
sur échec, panne partielle, rotation complète, absence d'appel sans Live,
arbitres et moteur/expériences/API. Test DOM sur données non fiables et erreurs.
Essai API réel isolé avant installation ; résultat consigné dans le rapport de
déploiement. Toutes les preuves d'essai restent hors cache de production.
Redémarrage commun des quatre bots ; aucune lacune expérimentale effacée.

## Références vérifiées

- [Contrôle de domaines de l'outil web Anthropic](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool)
- [Annonce officielle Avalanche Helicon](https://docs.avax.network/blog/helicon-upgrade) : publiée le 8 septembre, activation programmée le 22 septembre 2026 à 15:00 UTC ; cas réel exclu par l'ancien filtre de publication.
- [Calendrier BEA](https://www.bea.gov/news/schedule), [BLS](https://www.bls.gov/schedule/2026/home.htm), [Fed](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm)
- [Releases NEAR](https://github.com/near/nearcore/releases), [Mina](https://minaprotocol.com/blog), [Synthetix](https://synthetix.io/)
