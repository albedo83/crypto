# Laboratoire de signaux 2026 — verdict (2026-10-05)

Demande : trouver les signaux qui fonctionnent depuis le 1er janvier 2026,
sans s'appuyer sur l'historique 28 mois. Outil : `backtests/signal_lab_2026.py`.

## Méthode (fixée avant mesure)
- Trades isolés, entrée/sortie à la clôture 4h, coût 13 bps + funding réel.
- Découverte 2026-01-01 → 07-01 ; validation 07-01 → 10-05, lue une seule fois
  sur des finalistes figés et committés avant (`bb60491`).
- 252 variantes : momentum/retournement temporel, classement cross-sectionnel,
  rattrapage BTC, résiduel idiosyncratique, choc de volume, cassure de
  compression, mèches, funding extrême, OI + prix, écart à la moyenne mobile ;
  horizons 12/24/48 h.

## Résultat
Découverte : 1 variante à t ≥ 3, 2 à t ≥ 2. Le hasard seul en produit ~0,3 et ~6
sur 252 essais : le premier semestre ne contient presque rien au-delà du bruit.

| finaliste | jan → juin | juil → oct |
|---|---|---|
| F1_tsrev_1d_20, 24 h (≈ S9) | +288 bp, t 3,21 | −295 bp, t −0,57 |
| F2_xsmom_3d_short, 48 h | +72 bp, t 2,27 | −136 bp, t −2,31 |
| F2_xsmom_1d_short, 48 h | +54 bp, t 1,80 | −162 bp, t −3,47 |

**Aucun signal validé. Les gagnants du premier semestre s'inversent au second.**

## Conséquence
Les signaux de prix isolés sont instables d'un semestre à l'autre en 2026.
Choisir le meilleur sur une période et l'exploiter sur la suivante perd de
l'argent : c'est le mécanisme même qui rend un backtest prometteur et un live
décevant. À l'inverse, le portefeuille existant (backtest et paper) est
positif sur chaque trimestre de 2026 : sa valeur tient à la combinaison
signaux + sorties + rotation des slots, pas à un signal isolé.
