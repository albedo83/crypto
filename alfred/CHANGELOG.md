# Changelog — Alfred

Historique des versions d'Alfred. L'historique du bot précédent (v10–v12) est
archivé dans le `CHANGELOG.md` à la racine du dépôt.

## v1.8.0 — 2026-07-04

- **Trading engine**: les règles de sortie « sur le pic » (trails et planchers de gains) s'évaluent désormais à la clôture des bougies 4h — la granularité exacte de leur validation — au lieu du tick continu, dont le bruit gonflait le pic et coupait les gagnants trop tôt (mesuré sur l'ensemble des sorties réelles de la flotte, validé en isolation backtest 7/7 fenêtres et en contrefactuel sur trades réels). Les coupe-pertes, stops et le filet restent au tick.

## v1.7.6 — 2026-07-03

- **Admin**: les horodatages de la page de supervision (revues IA, verdicts, superviseur, journal d'audit, downtime) s'affichent en heure locale du navigateur au lieu d'UTC.

## v1.7.5 — 2026-07-02

- **Telegram**: le digest quotidien surveille l'expiration des clés agent (JUNIOR/BABY) — avertissement à J−21, alerte urgente à J−7, critique si dépassée. Les dates vivent dans bots.json.

## v1.7.4 — 2026-07-02

- **Infra**: le nettoyage horaire des stops résidents relit l'état de l'exchange après un resserrage dans le même cycle — supprime des fausses alertes « trigger étranger » (ordres tout juste remplacés), détectées au déploiement v1.7.3.

## v1.7.3 — 2026-07-02

- **Trading engine**: le stop résident suit désormais le plancher de protection le plus serré de la position (stop manuel, verrou IA, plancher de signal opposé) — le nouveau stop est posé avant que l'ancien soit retiré, la position n'est jamais sans filet pendant la bascule.

## v1.7.2 — 2026-07-02

- **Trading engine**: le stop résident peut désormais exécuter à travers un trou de prix profond (analyse de marge pire-cas : l'ancienne borne annulait l'ordre pile dans les scénarios pour lesquels le filet existe) ; les fermetures par auto-deleveraging de l'exchange sont reconnues et étiquetées à la comptabilisation.

## v1.7.1 — 2026-07-02

- **Trading engine**: filet de sécurité exchange-side — chaque position du bot SENIOR porte désormais un stop résident sur Hyperliquid, qui protège même quand le process est indisponible (crash, redémarrage). Le moteur de décision reste l'exécuteur normal ; le stop résident n'agit qu'en dernier recours.
- **Trading engine**: une position fermée côté exchange pendant une indisponibilité (stop résident, liquidation, fermeture manuelle) est désormais comptabilisée au prix réel des fills au retour du bot — avant, son P&L était perdu.

## v1.7.0 — 2026-07-01

- **Trading engine**: jalon 1.7 — la couche de décision IA de SENIOR est en place (arbitrage des entrées et des sorties, avec contexte de marché).
- **Trading engine**: les arbitres IA reçoivent un indice de capitulation marché-large (breadth sur tous les perps) — signal de contexte pour se méfier d'un LONG frais quand le marché entier décroche.

## v1.6.11 — 2026-07-01

- **Trading engine**: corrections issues de la revue de code de l'arbitre IA de sortie — fiabilité du scorecard (dédup, contrefactuel), sûreté du CUT (re-vérification au prix frais) et durcissement des bornes.

## v1.6.10 — 2026-06-30

- **Telegram**: retrait des nudges « Consider manual close/stop » (WR/giveback/lock-floor) — désormais redondants avec l'arbitre IA de sortie qui agit. Les events restent pour le dashboard.

## v1.6.9 — 2026-06-30

- **Trading engine**: l'IA peut désormais intervenir sur une position ouverte (SENIOR) — poser un stop protecteur sur un gagnant, ou couper un perdant en trajectoire désespérée — bornée par scorecard, disjoncteur, kill-switch et fail-safe.

## v1.6.8 — 2026-06-30

- **Telegram**: l'alerte de régime par-bot est désactivée (le nudge de régime reste sur le canal principal).

## v1.6.7 — 2026-06-30

- **Trading engine**: retrait de la blacklist de tokens — l'univers négociable est élargi.

## v1.6.6 — 2026-06-29

- **Trading engine**: l'arbitre IA d'entrée (SENIOR) tient compte de sa décision précédente sur un même setup pour éviter les revirements d'un scan à l'autre.

## v1.6.5 — 2026-06-26

- **Admin**: le classement et les courbes de performance de la flotte se calculent désormais sur le capital investi (capital de départ + apports DCA), pour qu'un apport de capital ne soit plus comptabilisé comme un gain.

## v1.6.4 — 2026-06-24

- **Trading engine**: `traj_cut` passe en **LONG-only** (`traj_cut_long_only=True`). On ne coupe plus jamais un S5 SHORT pinné. Motivation : audit live tous bots (−$233 / 2 sem, effet direct par trade négatif) + EDA 28m sur la population complète des positions « cuttables » — les S5 SHORT pinnés mean-revertent (77% de récupération si gardés, couper coûte −140 bps moy), alors que `btc_z` ne discrimine pas (AUC 0.50) et qu'aucun indicateur externe décorrélé ne sépare. Walk-forward 4 fenêtres : PnL ≥ base partout (28m +161$, 12m +86$, 6m/3m neutres car aucun SHORT cut récent), DD ≤ base partout. Kill-switch : `traj_cut_long_only=False`.
- **Supervision**: `analysis/strategy_review.py` gagne un détecteur `TRAJ_CUT_EFF` — récap hebdo par bot du Δ réalisé-vs-contrefactuel des traj_cut (contrefactuel reconstruit sur candles 4h), pour mesurer en continu l'effet direct par trade que le backtest masque (son gain = compounding non réalisable sur petits books).

## v1.6.3 — 2026-06-23

- **Admin**: nouveau classement de la flotte sur la page de supervision — equity de chaque bot rapportée à son capital de départ (latent inclus), trié par performance.

## v1.6.2 — 2026-06-21

- **Trading engine**: l'arbitre IA reçoit le momentum récent du token et applique une règle ferme — veto par défaut d'un SHORT qui combat une hausse alignée token+BTC (et symétrique pour un LONG contre une baisse alignée), sauf preuve claire d'essoufflement.

## v1.6.1 — 2026-06-21

- **Dashboard**: l'equity et le P&L (page bot + admin) repassent sur la comptabilité interne du bot (capital + réalisé + latent), stable que des positions soient ouvertes ou non. L'equity Hyperliquid, qui sous-compte la marge tant qu'une position est ouverte, n'est plus la référence affichée (gardée en cross-check). Corrige l'equity qui semblait baisser à l'ouverture de positions alors que le bot est gagnant.

## v1.6.0 — 2026-06-21

- **Trading engine**: l'IA arbitre désormais les entrées du bot SENIOR (annulation ou réduction de taille), en un appel par scan, avec timeout et repli automatique sur les règles si indisponible. Démarre en mode observation (décide et mesure sans agir) ; bascule en mode actif par configuration.
- **Admin**: nouvelle section « Arbitrage IA » sur la page de supervision — décisions récentes et scorecard mesurant en continu l'apport de l'IA vs les règles seules, avec disjoncteur automatique. Noyau de backtest inchangé (overlay live-only).

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
