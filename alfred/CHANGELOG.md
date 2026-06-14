# Changelog — Alfred

Historique des versions d'Alfred. L'historique du bot précédent (v10–v12) est
archivé dans le `CHANGELOG.md` à la racine du dépôt.

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
