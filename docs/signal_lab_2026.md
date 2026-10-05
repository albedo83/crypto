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

## Pistes externes testées (2026-10-05)

**Hedge bêta BTC du book** (`backtests/beta_hedge_2026.py`). Critère fixé avant :
Calmar amélioré sur les 3 trimestres 2026 sans perdre plus de 10 % du P&L.
Résultat : 2026 sans hedge +1 365 $, DD −20,4 %, Calmar 6,7 ; avec hedge
+911 $, DD −27,7 %, Calmar 3,3. Amélioration au T1 seulement. Corrélation
P&L book / BTC = +0,22 : le hedge ajoute du bruit BTC. **Clos.**

**Fade de la prime mark/oracle HL** (`backtests/premium_fade_hl.py`, ticks 60 s
depuis le 10 juin). Réversion réelle mais ~+5 bps brut à 15 min, stable sur les
deux périodes ; sous les 13 bps d'aller-retour taker, net négatif partout en
découverte (t jusqu'à −13). Les écarts aux horizons longs sont de la direction
de marché, pas de la prime. Edge de teneur de marché HF, hors de notre portée.
**Clos.**

**Netting d'un compte unique** : sans objet aujourd'hui (un compte HL par bot,
une position par coin, signal opposé refusé). À reprendre dès qu'une 2ᵉ couche
(hedge, autre stratégie) partage un compte : vecteur cible par coin + allocateur
+ exécution unique.

**Réserve sur le hedge — corrélation dans les creux.** Jours à > 10 % sous le
pic (77 j) : corrélation book/BTC +0,37, hedge +245 $. Toute l'année : −454 $.
Pire creux (22/03 → 14/04, book −325 $) : corrélation −0,25, hedge +57 $ —
creux de type D (faiblesse propre aux alts). Un hedge armé seulement au-delà de X % de
drawdown n'est **pas décidable statistiquement** (quatre creux de référence) :
c'est une décision de politique de risque, pas un test. Aucune action.

**Entrées maker (post-only au prix d'entrée)** — `backtests/maker_entry_estimate.py`,
entrées réelles depuis le 2026-07-09, franchissement strict du mark minute.
Remplissage 77-96 % selon le délai. Les trades **non remplis sont les gagnants**
sur les 4 bots (paper : +33 à +61 $) : quand le fade a raison tout de suite, le
prix ne revient pas. Repli taker : Δ −6 à −54 $, le glissement dépasse les
3 bps économisés à tous les délais. **Rejeté avant le live.**
