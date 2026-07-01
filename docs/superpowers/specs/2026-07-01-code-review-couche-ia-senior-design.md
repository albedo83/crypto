# Revue de code — couche IA de SENIOR (+ surfaces touchées)

**Date** : 2026-07-01 · **Type** : revue de code (rapport, pas de patch auto).

## Context

La couche IA de SENIOR (bot live, argent réel) a un pouvoir croissant : elle **véto/réduit
les entrées** (`ai_entry_arbiter`) et depuis aujourd'hui **agit sur les positions ouvertes**
(`ai_exit_arbiter` : LOCK d'un stop protecteur, CUT d'un perdant doomed). Ces décisions sont
un **jugement LLM inbacktestable** qui écrit sur le compte réel. Une moitié (arbitre de
sortie + scorecard) vient d'être écrite ce jour, par l'auteur de cette revue → **angle mort
sur son propre code** : d'où une vérification adversariale indépendante en complément.

Objectif : un **rapport classé par sévérité** (Critique/Haut/Moyen/Bas), chaque finding avec
`file:line`, un **scénario d'impact concret** (comment ça perd de l'argent ou casse) et un
**correctif proposé**. L'utilisateur décide quoi appliquer — aucun patch en aveugle sur du
code argent-réel.

## Périmètre (largeur B : couche IA + surfaces qu'elle touche)

Fichiers cœur (racine) :
- `ai_entry_arbiter.py` (322) — arbitre d'entrée (veto/haircut).
- `ai_exit_arbiter.py` (292, **neuf**) — arbitre de sortie (HOLD/LOCK/CUT).
- `ai_arbiter_scorecard.py` (311) — scorecard contrefactuel entrée + disjoncteur.
- `ai_exit_scorecard.py` (318, **neuf**) — scorecard contrefactuel sortie + disjoncteur.
- `position_review.py` (290) — reviewer observation-only (cron 2h).
- `supervisor.py` (603) — rapport LLM quotidien.
- `ai_notify.py` (74) — envoi Telegram canal SENIOR.
- `ai_doctrine.py` (40) — digest partagé injecté aux prompts.
- (`entry_judge.py` legacy hors-cron : survol seulement.)

Intégration + surfaces touchées :
- `alfred/botinstance.py` : `_rank_and_enter` (arbitre d'entrée), `_ai_exit_overlay` +
  `_protective_stop_ok` (arbitre de sortie, dans `on_tick`), état `_arbiter_last` /
  `_exit_arbiter_last` / `_exit_ai_last_ts`.
- Chemin de fermeture : `close_and_check` / `_close_inner`, mutex `_closing`, verrou
  `_pos_lock`, `_failed_closes`.
- `alfred/rules.py` : `evaluate_exit`, `effective_stop`, `candle_excursions`,
  `compute_trade_pnl` (rejoués par les scorecards / validation LOCK).
- Persistance : `_save_state` / `persistence.save_state` (le `manual_stop_usdt` posé par
  l'IA doit survivre au restart).

## Dimensions & hunt-list (les trois lentilles, findings classés par sévérité)

### 1. Correctness & sûreté-argent (priorité haute)
- **Concurrence** : le LOCK lit `pos` sous `_pos_lock`, relâche, ré-acquiert pour poser
  `manual_stop_usdt`, puis `_save_state()` hors lock — fenêtre de course avec un close
  concurrent (tick/dashboard) ? Le CUT via `close_and_check` respecte-t-il `_closing` ?
  Position fermée entre la construction du batch et l'application ?
- **Fail-safe / disjoncteur** : timeout ⇒ verdicts vides ⇒ aucune action (prouver). Trip-file
  lu à chaque batch ? Un trip coupe-t-il bien CUT **et** LOCK ?
- **Garde-fous IA** : « jamais couper un gagnant » (CUT gaté `ur < 0`) ; bornes LOCK
  (`_protective_stop_ok` réplique-t-il exactement `app.py` : `< pnl net`, `> catastrophe`,
  ne fait que relever) ; `conf_min` appliqué ; hystérésis anti flip-flop cohérente.
- **Contrefactuels (scorecards)** : matching décision↔trade (`_iso16(entry_ts_ms)` vs
  `entry_time[:16]`) — collisions / décalage ? Rejeu depuis l'entrée (mfe/mae repartent de 0
  → règles dépendantes du chemin faussées ?) ; `manual_stop` passé au rejeu LOCK ; signe des
  deltas ; pending vs résolu.
- **Throttle** : `_exit_ai_last_ts` mis à jour même quand batch vide / fail-open (pas de
  spam LLM à 20s) ; zone candidate correcte.

### 2. Sécurité
- **Secrets** : `ANTHROPIC_API_KEY`/`TG_*` jamais loggés, jamais commités (`.env` gitignored).
- **Injection de prompt** : `signal_info`, `reason`, champs marché passés au LLM — un contenu
  hostile/malformé peut-il détourner le JSON de sortie ou faire agir l'IA ? Le parse
  `re.search(r"\{.*\}")` + `_normalize` borne-t-il tout (action ∈ {HOLD,LOCK,CUT}, stop borné) ?
- **Confiance LLM** : une valeur `stop_usdt` absurde (négative, énorme) est-elle re-validée
  côté bot avant d'écrire (oui via `_protective_stop_ok`, à confirmer sur tous les chemins) ?
- **Intégrité kill-switch** : `AI_EXIT_ENABLED=0` coupe tout ; trip-file inviolable.

### 3. Architecture & maintenabilité
- Duplication forte entrée/sortie (arbiters + scorecards quasi-jumeaux : `_call_opus`,
  `arbitrate_safe`, trip-file, `load_*`, `replay_*`) — extraire un socle commun ?
- `botinstance.py` grossit (l'overlay + validation ajoutent ~150 lignes) — frontière nette ?
- Cohérence des events (`ARBITER_*` / `AI_*_SCORECARD`) et du `source=` Telegram.

## Méthode (hybride)

- **Phase 1 — passe manuelle** (auteur, contexte complet) : lecture dirigée par la hunt-list,
  rédaction du rapport classé par sévérité.
- **Phase 2 — vérification adversariale indépendante multi-agent** ciblée sur le code frais
  (`ai_exit_arbiter`, `_ai_exit_overlay`, `_protective_stop_ok`, `ai_exit_scorecard`) : des
  agents sceptiques (1) tentent de **réfuter** chaque finding de Phase 1 (défaut = réfuté si
  doute) et (2) chassent ce que l'auteur a **raté** (angle mort). Réconciliation → rapport
  final (findings confirmés seulement).

## Livrable & critères de succès

- Un rapport markdown : findings ordonnés par sévérité, chacun `{fichier:ligne, dimension,
  sévérité, scénario d'impact, correctif proposé}`. Zéro patch appliqué.
- Succès = tout finding **Critique/Haut** a un scénario concret reproductible et un correctif
  actionnable ; les findings Phase 1 ont survécu (ou été éliminés par) la vérification
  adversariale ; les angles morts sur le code neuf sont couverts par des reviewers indépendants.

## Hors-scope
- Le moteur de règles mûr (`rules.py` au-delà des fonctions rejouées), le web/dashboard, le
  reste d'`alfred/` non touché par la couche IA. Refactor non lié. Application des correctifs
  (décision utilisateur, séparée).
