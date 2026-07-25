# Biais de booking des trails — le BT surévalue ~50 % de son P&L (2026-07-25)

Le backtest (et le paper) bookent les sorties de trail à **leur niveau théorique**
(`_synth(trail)`), alors que le live exécute un **ordre marché à la clôture 4h
suivante**. Depuis les trails-sur-close (v1.8.0) les trails ne sont évalués qu'aux
clôtures 4h : si le prix a traversé le niveau **à l'intérieur** de la bougie, le
BT/paper créditent un prix qui n'était pas disponible.

Scripts : `python3 -m backtests.backtest_trail_booking_bias`. Config prod
(aligned, modulateur, margin_check, mfe_on_close). Le `catastrophe_stop` est
**exclu** : c'est un trigger reduce-only résident sur l'exchange, il s'exécute
réellement près de son niveau. Règles concernées : `prop_trail`, `s10_trailing`,
`s8_inlife`, `opp_floor`.

## Preuve live (le cas qui a déclenché l'enquête)

LDO S5 LONG, entré 2026-07-22 20:03 @ 0.401050, **même bougie de sortie** des deux côtés :

| | prix de sortie | gross | P&L |
|---|---|---|---|
| SENIOR (live, fill réel) | 0.391020 | **−250 bps** | −2.40 $ |
| PAPER (prix synthétique) | 0.409663 | **+232 bps** | +3.33 $ |

**Écart 482 bps.** La bougie 07-23 08:00 ouvre à 0.410 et s'effondre à 0.389 en son
sein (high +228 → low −288 bps) : le prix a traversé le trail *dans* la bougie. Les
ticks 11:00→12:30 cotent 0.390-0.392 — **personne ne pouvait vendre à 0.4097**.

## Contrôle naturel (23 trades SENIOR vs PAPER, mêmes entrées, depuis le reset 07-09)

| Type de sortie côté paper | n | écart moyen SENIOR − PAPER |
|---|---|---|
| Au **mark** (`timeout`, prix réel des deux côtés) | 18 | **+14 bps** (bruit) |
| **Synthétique** (`prop_trail`) | 5 | **−115 bps** (systématique) |

Quand paper booke au prix réel, les deux instances sont d'accord. L'écart n'existe
que sur les sorties à prix théorique → **c'est la mesure qui diverge, pas l'exécution.**

## Quantification sur le backtest

| Fenêtre | BT actuel | BT booking réaliste | Δ P&L | Δ DD | n trails |
|---|---|---|---|---|---|
| 28 m | +24 628.7 % | **+3 702.3 %** | −20 926 pp | −5.7 pp | 457 |
| OOS 0-6 m | +161.0 % | **+69.8 %** | −91.3 pp | −1.7 pp | 96 |
| OOS 6-12 m | +179.3 % | **+88.7 %** | −90.6 pp | −3.4 pp | 76 |
| OOS 12-18 m | +171.2 % | **+95.5 %** | −75.6 pp | −0.6 pp | 93 |
| OOS 18-24 m | +164.5 % | **+78.0 %** | −86.6 pp | −5.7 pp | 87 |

**Part du P&L du BT qui vient du booking optimiste** : −56.7 % / −50.5 % / −44.2 % /
−52.6 % sur les 4 fenêtres OOS ; −85 % sur 28 m (le compounding amplifie).

**~50 % du P&L annoncé, stable sur 4 fenêtres indépendantes.** Le drawdown se
dégrade aussi (−0.6 à −5.7 pp) : on ne récupère plus les jolies sorties de trail.

Cohérence des deux mesures : ~29 % des trades sortent en trail ; à −115 bps par
sortie de trail (mesure live), sur une espérance moyenne ~+100 bps/trade, on
attend ≈ −33 bps/trade, soit ~1/3 à 1/2 du P&L. Les deux approches concordent.

## Conséquences

1. **L'écart live-vs-BT est en partie un mirage.** Le live était comparé à un
   benchmark inatteignable. Le retard réel du live est plus faible qu'affiché.
2. **L'edge vrai de la stratégie est ~2× plus petit** que ce que `docs/backtests.md`
   annonce, sur la dimension trails.
3. **`prop_trail` (shippé v12.11.0)** a été validé avec ce biais dedans — sa valeur
   réelle est à re-mesurer (« WR 47→75, book ×2.6 en BT 28m » est surévalué).
4. Toute règle validée en walk-forward **dont la sortie est un trail** est candidate
   à re-validation en booking réaliste.

## Correctif appliqué

- **paper** : `paper_gap_fills=True` (v1.15.4) → le paper booke désormais le pire de
  (niveau, mark) = le prix réaliste. N'affecte QUE le bot paper (PaperBroker) ;
  live/junior/baby (LiveBroker) et le backtest ne lisent pas ce flag.
- **backtest** : NON modifié à ce stade — le changer invaliderait toutes les
  validations historiques d'un coup. Décision à prendre séparément (cf. §Suites).

## Suites à instruire

- Re-valider `prop_trail` S5/S9 en booking réaliste (garde/retrait/paramètres).
- Décider si le BT bascule en booking réaliste par défaut (= re-baseline complet de
  `docs/backtests.md`, comme la remise à zéro phase 6).
- Le hard-stop exchange-side (trigger reduce-only) n'a PAS ce biais : piste pour
  rendre les trails réellement exécutables à leur niveau plutôt que de renoncer
  au gain — à étudier (coût : un ordre résident par position, re-posé à chaque
  mise à jour du niveau).

*Source : `backtests/backtest_trail_booking_bias.py`, `scratchpad/trail_bias.json`.
Données live : `alfred/data/bots/{live,paper}/bot.db` depuis le reset 2026-07-09.*
