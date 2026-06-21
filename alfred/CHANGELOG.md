# Changelog — Alfred

Historique des versions d'Alfred. L'historique du bot précédent (v10–v12) est
archivé dans le `CHANGELOG.md` à la racine du dépôt.

## v1.5.2 — 2026-06-20

- **Dashboard**: le tableau de bord par bot et la page de supervision affichent désormais la même référence — l'equity réelle Hyperliquid (live) — pour l'equity et le P&L, avec la comptabilité du bot en cross-check. Fin des écarts d'affichage entre les deux pages.

## v1.5.1 — 2026-06-20

- **Dashboard**: les cartes P&L (Realized / Unrealized / Total) dérivent toutes de la comptabilité live du bot et se réconcilient entre elles ; la carte P&L ne dépend plus du cache exchange (qui pouvait être périmé juste après un redémarrage et faire diverger l'affichage). L'equity Hyperliquid reste affichée en cross-check.

## v1.5.0 — 2026-06-20

- **Admin**: nouvelle section « Analyses IA » sur la page de supervision — synthèse du superviseur, revue des positions ouvertes et verdicts d'entrée (observation, bot SENIOR).
- **Infra**: superviseur et nouvelles analyses IA remontés dans l'admin au lieu de Telegram ; sortie du superviseur condensée.

## v1.4.0 — 2026-06-20

- **Trading engine**: retrait d'une règle de sortie anticipée dont le réglage reposait sur une mesure trop optimiste du backtest ; revue confirmée en validation glissante.
- **Infra**: le backtest de référence mesure désormais la performance des sorties sur le prix réellement observé par le bot (et non les extrêmes de bougie), rapprochant le backtest du comportement live ; `docs/backtests.md` régénéré sur cette base.

## v1.3.3 — 2026-06-18

- **Dashboard**: la ligne verticale d'entrée de position sur le graphique de prix est désormais positionnée à l'instant exact de l'entrée (interpolée entre les bougies) au lieu d'être calée sur la bougie la plus proche.

## v1.3.2 — 2026-06-18

- **Infra**: correction d'une lecture de base non sérialisée dans la page de supervision qui pouvait la faire échouer par intermittence (accès concurrent à la base d'un bot). Détecté en production.

## v1.3.1 — 2026-06-18

- **Admin**: la date d'expiration de la clé agent (bots en modèle agent) est affichée sur la page de supervision et le tableau de bord de chaque bot, avec alerte couleur à l'approche de l'échéance.

## v1.3.0 — 2026-06-17

- **Trading engine**: le verrou de gains proportionnel est étendu à une seconde stratégie (protège les gains des positions gagnantes).
- **Admin**: ajout d'un 4e bot (BABY, petit capital, opéré par une tierce personne).

## v1.2.11 — 2026-06-14

- **Trading engine**: à l'ouverture live, si la confirmation du fill est introuvable, l'entrée est annulée proprement au lieu de booker un prix fictif (la réconciliation récupère tout ordre réellement passé) ; un échec d'écriture d'un trade en base déclenche désormais une alerte au lieu d'être silencieux. Détecté en revue de code.

## v1.2.10 — 2026-06-14

- **Dashboard**: le tableau d'impact des interventions sépare désormais l'impact des stops manuels (contrôlable) de celui des règles automatiques et du stop catastrophe, et isole les positions encore en cours (provisoire) du total finalisé. Colonne « CF » renommée « Au timeout ».

## v1.2.9 — 2026-06-14

- **Telegram**: dans le digest quotidien, le lien du dashboard est désormais placé sous la ligne de chaque bot (lien direct vers le bot concerné), plutôt que regroupé en pied de page.

## v1.2.8 — 2026-06-14

- **Infra**: les écritures SQLite de bougies sont sorties de la boucle WebSocket (flush en thread) — évite tout blocage de l'ingestion marché lors des rolls 4h, quand de nombreux symboles basculent en même temps.

## v1.2.7 — 2026-06-14

- **Infra**: correction de deux références mortes laissées par le retrait de la phase parallel-run (page de supervision et digest quotidien) qui provoquaient une erreur — détectées en revue de code.

## v1.2.6 — 2026-06-12

- **Telegram**: le digest quotidien remplace l'ancien suivi parallel-run (legacy décommissionné) par un résumé de flotte — balance, P&L réalisé/latent et positions de chaque bot.

## v1.2.5 — 2026-06-12

- **Telegram**: le digest quotidien inclut des liens cliquables vers la page de supervision et le dashboard de chaque bot.

## v1.2.4 — 2026-06-12

- **Dashboard**: nouvelle table « Impact des interventions » — pour chaque trade clos avant son terme naturel, estime le P&L qu'aurait eu la position tenue jusqu'au bout et affiche l'écart, pour mesurer l'effet des sorties anticipées (rafraîchissement manuel).

## v1.2.3 — 2026-06-12

- **Dashboard**: l'historique des trades affiche désormais l'heure d'entrée (en plus de l'heure de sortie) et la valeur en $ d'entrée et de sortie de chaque position.

## v1.2.2 — 2026-06-12

- **Security**: double authentification TOTP optionnelle par compte sur le login web (apps standard type Google Authenticator), automatisations locales exemptées ; durcissement de la détection d'IP locale contre le spoofing d'en-têtes proxy.

## v1.2.1 — 2026-06-12

- **Trading engine**: le sizing live se cale sur la marge réellement disponible du compte — réduction ou passe propre au lieu d'un rejet d'ordre par l'exchange.
- **Infra**: les reconnexions WebSocket silencieuses (fermeture propre côté serveur) sont désormais comptées et déclenchent la réparation de données, comme les coupures réseau.

## v1.2.0 — 2026-06-11

- **Trading engine**: nouveau mécanisme de protection des gains — quand le marché contredit objectivement une position gagnante, un plancher automatique verrouille l'essentiel du gain acquis sans plafonner la suite.

## v1.1.0 — 2026-06-11

- **Admin**: authentification par rôles (accès limité par bot pour les opérateurs non-admin), supervision unifiée sur un seul écran `/master` (vue globale + système + flotte + admin), migration du bot JUNIOR.
- **Infra**: surveillance externe (rapports quotidiens et hebdomadaires, alertes de conflit entre bots) raccordée à Alfred ; compteurs de frais remis à zéro à la migration de chaque bot.

## v1.0.0 — 2026-06-11

- **Trading engine**: première version de production — moteur unifié multi-bots, exécution live migrée depuis le bot historique.
- **Infra**: flux de marché temps réel partagé (WebSocket unique), couche de données persistante avec reprise automatique après coupure, page de supervision `/master`.
