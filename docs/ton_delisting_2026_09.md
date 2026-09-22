# TON retiré de l'univers — 2026-09-22 (Alfred 1.29.1)

## Constat

- Hyperliquid a **délisté TON** : `isDelisted: true`, volume et open interest nuls,
  aucune candle servie après le **2026-06-15 08:00 UTC**.
- Alfred le gardait dans `trade_symbols` et dans le secteur L1. Conséquences :
  - **3 132 réparations horaires** de gap depuis le 15 juin (rien à réparer) ;
  - **features figées** (ret_24h +567 bps, ret_42h +321 bps) ⇒ TON candidat
    **S1 LONG perpétuel** depuis le 2026-08-23 sur les 4 bots (~140 skips chacun,
    `max_macro` / `max_long`) ;
  - le **2026-09-14 00:03** sur Live, TON a passé les gates, reçu un GO de
    l'arbitre (shadow), puis aucune ouverture : ordre très probablement rejeté par
    l'exchange (log écrasé par un redémarrage depuis). Sur Paper, le broker simulé
    aurait rempli au prix figé — non survenu, uniquement parce que les slots
    étaient pleins ;
  - biais de la **moyenne du secteur L1** (entrée de S5) : 45 à 120 bps selon le
    token au 2026-09-22 (AVAX −952 avec TON, −1 071 sans, seuil 1 000) ;
  - un point figé sur 34 dans la **dispersion** transversale.
- Le délisting était connu depuis août côté données (`data_freshness.py`,
  `oi_backfill.py` excluent TON « retrait de cote, pas un trou ») mais n'avait
  jamais été répercuté sur l'univers de trading. Même panne silencieuse que MKR
  (retiré en phase 6).

## Décision

Retrait de `trade_symbols` et du secteur L1 (choix utilisateur, précédent MKR).
Décision d'**intégrité** : un token non négociable ne doit ni produire de signal
ni peser dans les features des autres. Ce n'est pas une décision d'edge.

## Impact backtest (moteur 1.29.1, fin des données 2026-09-22, départ 1 000)

| Fenêtre | Avec TON | Sans TON | DD avec → sans | Trades TON (avec) |
|---|---:|---:|---:|---:|
| 28 mois | +3 057 % | +5 967 % | −61,1 → −52,3 | 33 (+2 094) |
| 12 mois | +383 % | +732 % | −50,5 → −35,6 | 12 (+376) |
| 6 mois | +1,0 % | +43,5 % | −49,7 → −34,1 | 7 (+106) |
| 3 mois | +24,3 % | +24,3 % | identique | 0 |

**Lecture prudente.** Les trades TON eux-mêmes sont gagnants ; l'amélioration
vient de la **dépendance de chemin** (slots, secteur L1, compounding) sur une
période où TON était encore listé et négociable. Ce n'est donc pas un gain
attendu en live : c'est la preuve que la composition de l'univers déplace
fortement le résultat historique (cf. MKR, C-minus/ADA). La fenêtre 3 mois,
entièrement postérieure au délisting, est identique : le backtest n'avait déjà
plus TON sur cette période ; le live, lui, l'avait encore.

Parité : `backtests.test_oi_parity` VÉRIFIÉE, `audit_input_parity` 0 divergence.
`docs/backtests.md` régénéré sur l'univers sans TON.
Reproduction : scratchpad `ton_bt.py ton|no_ton` (patch de `DEFAULT_PARAMS`
avant import du backtest).

## Effet collatéral : campagne d'expériences

La 1.29.0 (JEV) a modifié `botinstance.py`, inclus dans l'empreinte des
expériences : **shadow-v2 est gelée depuis le 2026-09-22 05:21** (≈ 20 h de
données, conservées). La 1.29.1 modifie aussi `settings.py` ; elle ouvre donc
**shadow-v3**. Résultats non fusionnés entre campagnes.
