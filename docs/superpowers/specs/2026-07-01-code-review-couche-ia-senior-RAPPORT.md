# Rapport de revue — couche IA de SENIOR

**2026-07-01.** Méthode hybride : passe manuelle (auteur) + 2 reviewers indépendants
(réfutation adversariale + chasse aveugle). Findings réconciliés, classés par sévérité.
Aucun patch appliqué — décision utilisateur.

## Verdict d'ensemble

**Le chemin qui AGIT sur l'argent réel est bien gardé** : LOCK est validé sous lock contre
le prix frais (`_protective_stop_ok`, bornes identiques à `POST /api/manual_stop`), ne fait
que relever un plancher, `arbitrate_safe` est un vrai fail-safe (timeout/erreur ⇒ zéro
action), la concurrence close/manual_stop est re-checkée sous `_pos_lock`, le disjoncteur
coupe CUT **et** LOCK. C'est rassurant.

**Mais le SCORECARD est matériellement cassé** — or c'est LUI qui (a) décide si CUT passe de
shadow à `act` et (b) déclenche le disjoncteur censé attraper une IA destructrice. Trois bugs
indépendants font que **le filet de sécurité ne mesure pas ce qu'il prétend**. Conséquence
directe : tant que le scorecard n'est pas corrigé, on ne peut ni faire confiance à la décision
de graduation de CUT, ni au backstop de LOCK (qui agit immédiatement en s'appuyant dessus).

---

## HIGH

### H1 — Le rejeu contrefactuel est tronqué au moment de la décision (delta ≈ bruit)
`ai_exit_scorecard.py` (~188-216) passe `d.get("hold_hours")` comme **plafond de hold** au
rejeu (`replay_rules` → `max_hold = int(hold_hours // 4)`, force-exit `timeout` à
`held ≥ max_hold`). Or `botinstance.py:439` logge `hold_hours = âge de la position à la
décision`, pas le hold **cible**. Donc pour un CUT à 10h (cible 48h), le rejeu ne simule que
~8h depuis l'entrée puis « timeout » → `rules_pnl ≈ PnL au moment de la coupe` → `delta ≈ 0`
quasi toujours. Un CUT qui a évité un −$400 catastrophe paraît neutre ; un CUT destructeur
aussi. **Le disjoncteur ne peut ni se déclencher ni exonérer correctement.**
- **Fix** : logger le hold CIBLE (`(pos.target_exit − pos.entry_time)/3600`) comme champ
  distinct et le passer à `replay_rules` ; garder l'âge séparément.

### H2 — Multi-comptage des décisions horaires par position (n_resolved / delta_sum gonflés)
`score()` crée une ligne **par décision**, sans dédup. Une position doomed reste en zone
candidate et logge un CUT (shadow) **chaque heure** (throttle 1h) jusqu'à sa fermeture par les
règles → N lignes corrélées matchées au **même** trade clos. Idem LOCK (re-LOCK horaire).
Impact : `n_resolved` franchit le quorum `cb_min=20` avec une poignée de positions, et
`delta_sum` compte une même issue ~N× → **disjoncteur faux-positif ou masqué**. La « preuve »
statistique n'est pas valide (lignes non-indépendantes).
- **Fix** : dédup par `(symbol, entry_ts_ms)` — une décision représentative par position (la
  dernière actée si acted, sinon la dernière shadow) avant d'agréger.

---

## MEDIUM

### M1 — Les LOCK « non-actés » rejouent des stops que le système a REJETÉS
Quand un LOCK échoue `_protective_stop_ok` (`would_trigger_immediately` / `below_catastrophe`
/ `not_higher_than_existing`), la décision est quand même loggée `acted=False` avec le
`stop_usdt` brut. La branche LOCK-shadow du scorecard (`:209-220`) rejoue ce stop **rejeté**
comme s'il était viable → s'il était au-dessus du PnL courant, il « fire » à la bougie 1 du
rejeu (sortie immédiate impossible en vrai) → `lock_delta` arbitraire.
- **Fix** : ne scorer en LOCK-shadow que les décisions **trip-suppressed** (note = tripped),
  pas celles rejetées par la validation.

### M2 — Le garde CUT « jamais un gagnant » utilise un `ur` PÉRIMÉ (pré-appel LLM)
`botinstance.py:421` gate le CUT sur `s["ur"] < 0`, où `s["ur"]` est figé **avant** l'appel
opus (≤12s). La fermeture, elle, se fait au prix **frais** (`st.price`). Si le prix a rebondi
pendant les 12s et que la position est maintenant verte, le garde passe quand même → l'IA
**coupe un gagnant courant**, violant l'invariant, et book une clôture réelle.
- **Fix** : recalculer `ur` depuis `st.price` juste avant le CUT et ré-asserter `ur < 0`
  (idéalement encore en zone de coupe).

### M3 — Le contrefactuel « règles-seules » omet des sorties protectrices (biais pro-IA)
`replay_rules` construit `MarketCtx(btc_ret_4h_bps=None, disp_24h=None)` et un `PosView` sans
`opp_floor_bps`/`extended`. Donc `btc_drop_cut`, les règles disp-dépendantes et `opp_floor`
**ne peuvent jamais** se déclencher dans la baseline, même quand elles ont fait sortir en réel
→ la « ligne de base règles » manque des sorties qu'elle aurait vraiment prises → `delta`
biaisé **en faveur de l'IA**, dans le sens **non-sûr** (l'IA paraît meilleure). 
- **Fix** : persister `btc_ret_4h_bps` par bougie et le reconstruire ; a minima documenter que
  ces règles sont absentes de la baseline et ne pas s'appuyer sur ce scorecard pour ces cas.

### M4 — Rejeu MFE/MAE sur mèches vs live sur le mark (biais structurel)
`replay_rules` reconstruit mfe/mae depuis `candle["h"]/["l"]` (mèches) alors que le bot live
les suit sur le **mark horaire** ([[project_bt_mfe_wick_bias_2026_06]]). Les règles MFE-dépendantes
(prop_trail, dead_timeout…) firent à des points différents dans le contrefactuel → biais
structurel du delta, indépendant de la valeur réelle de l'IA. Inhérent à tout rejeu partagé,
pas un bug de code — à connaître en lisant le scorecard.

---

## LOW

- **L1** — `_protective_stop_ok` manque le garde `math.isfinite` qu'a `app.py:934`. Un verdict
  `{"stop_usdt": NaN}` (json.loads accepte NaN) passe `_normalize`, et **toutes** les
  comparaisons NaN étant False, les deux bornes passent → NaN écrit comme `manual_stop_usdt`
  → empoisonne `manual_stop_rule`. Peu probable (LLM devrait émettre NaN) mais défense triviale.
  Fix : ajouter `math.isfinite(stop_usdt)`.
- **L2** — `_normalize` (arbitre) ne clampe pas `confidence` à [0,1] ; conf=1.5 passe
  `conf_min`. Pas dangereux (bornes stop + garde CUT restent), mais gate peu robuste. Fix :
  `max(0, min(1, conf))`.
- **L3** — `close_and_check` renvoie `sym not in _failed_closes` ; si un close concurrent
  (dashboard) tient déjà le mutex `_closing`, `close_position` no-op mais renvoie True →
  l'arbitre logge `acted=True`, envoie un « 🧠 IA — CUT » et le scorecard attribue la clôture à
  `ai_exit` alors que la vraie sortie vient d'ailleurs. Rare ; corrompt 1 ligne + 1 alerte.
- **L4** — TOCTOU d'identité : le LOCK re-checke `sym in positions` sous lock mais pas que
  c'est le **même** objet position (close+reopen même symbole dans la fenêtre µs). Infime.
  Fix éventuel : `pos is self.positions.get(sym)`.
- **L5** — L'appel opus ≤12s sérialise les ticks des 4 bots 1×/h (retard ≤12s sur junior/
  baby/paper). `to_thread` ne gèle pas l'event loop → acceptable.

## Design / Archi

- **D1** — LOCK **agit immédiatement** sans warm-up shadow ; son seul backstop est le
  disjoncteur du scorecard — **qui est cassé** (H1/H2/M1/M3). Tant que le scorecard n'est pas
  réparé, LOCK agit sans filet mesuré fiable. Recommandation : **corriger le scorecard avant
  de se fier au disjoncteur**, et/ou gater LOCK sur un minimum de décisions résolues.
- **A1** — Duplication forte entrée/sortie (`ai_entry_arbiter`/`ai_exit_arbiter`,
  `ai_arbiter_scorecard`/`ai_exit_scorecard` : `_call_opus`, `arbitrate_safe`, trip-file,
  `load_*`, `replay_*`). Un fix (ex. H1/H2) doit être fait des deux côtés → risque de
  divergence. Envisager un socle commun sans coupler les kill-switches.
- **Note entrée** : le scorecard d'ENTRÉE peut aussi multi-compter des vetos persistants
  (même symbole véto-é à plusieurs scans) — même classe que H2, atténué par l'hystérésis.

## Vérifié — PAS des bugs
Matching décision↔trade OK (UTC ISO minute des deux côtés) · LOCK posé ne s'auto-déclenche pas
· pas de double-close CUT (boucle relit `positions`, mutex `_closing`) · symboles hallucinés
filtrés · pas de contamination `prior_decision` inter-position (`prior_ttl_h=12` < cooldown 24h)
· fail-safe timeout/tripped correct.

## Priorisation recommandée
1. **H1 + H2** (le scorecard doit mesurer juste avant de faire graduer CUT ou trip le disjoncteur).
2. **M2** (money-safety direct : ne pas couper un gagnant sur un `ur` périmé).
3. **M1, M3** (fiabilité du scorecard). **L1, L2** (durcissement trivial).
4. Le reste (L3-L5, A1) opportuniste.
