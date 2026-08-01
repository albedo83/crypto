# Anatomie des régimes de creux

> **Diagnostic pur.** Aucune stratégie n'est simulée, aucun paramètre n'est
> ajusté, aucune recommandation n'est formulée. Le document rend des faits et
> applique la grille de lecture écrite avant eux.

**Généré le** : 2026-08-01T15:20Z
**Empreinte** : config `d7a415020619` · git `2f1b0e8+dirty` · données jusqu'au
2026-08-01T12:00, 36 symboles, fichiers du 2026-08-01T12:10Z
**Périmètre** : 853 jours × 34 tokens (`Params.trade_symbols`, gate de parité
passé), couverture 86,7 %
**Source** : `backtests/creux_anatomy.py` · `analysis/output/creux_anatomy.json`

## 0. Origine de l'étude

TREND-v0 a été rejeté (`docs/projet_a_trend_v0.md`). Sa clause C2 a révélé un
fait qui n'était pas anticipé : les creux d'Alfred tuent **aussi** le suivi de
tendance — les deux moteurs, aux hypothèses opposées, y perdent ensemble.

Hypothèse mise à l'épreuve ici : **ces fenêtres sont des régimes de chop
violent, pas des tendances.**

## 1. Grille de lecture — ÉCRITE AVANT LES CHIFFRES

> - efficacité basse + retournements élevés vs baseline ⇒ régime **CHOP
>   confirmé** ⇒ pistes à privilégier : monétisation de volatilité /
>   cross-sectionnelle market-neutral ; carry à examiner selon (5)
> - efficacité haute ⇒ **hypothèse chop réfutée** ⇒ l'échec de TREND-v0 vient
>   d'ailleurs (lookback, univers) — on le **CONSIGNE**, on ne re-teste pas
> - funding extrême et défavorable au receveur dans les creux ⇒ la piste carry
>   hérite d'un **malus** documenté ; favorable ⇒ d'un **bonus**
>
> Ambiguïtés : si les 4 fenêtres divergent entre elles, le rapport le dit tel
> quel — pas de moyenne qui fabrique un régime unique fictif.

## 2. Méthode

Une fenêtre de 30 jours ne se compare pas à une période de 24 mois :
l'efficacité directionnelle et le taux de retournement décroissent
mécaniquement avec la longueur de la fenêtre. Chaque creux est donc comparé à
la **distribution de toutes les fenêtres glissantes de même longueur** prises
hors des creux (442 fenêtres de référence pour A, 640 pour B, C et D), et
chaque mesure est rendue avec son **percentile** dans cette distribution.

La baseline exclut A ∪ D — B et C étant incluses dans A — soit 727 jours
sur 853.

## 3. Résultats

Percentile = position dans la distribution de référence. `p4` signifie que
96 % des fenêtres hors creux font davantage.

| mesure | **A** 96 j | **B** 30 j | **C** 30 j | **D** 30 j |
|---|---|---|---|---|
| efficacité directionnelle | 0,038 **p4** | 0,089 **p20** | 0,085 **p17** | 0,141 p43 |
| taux de retournement | 0,474 **p10** | 0,517 p47 | 0,483 **p20** | 0,379 **p0** |
| part de mèche (barres 4 h) | 0,530 **p0** | 0,553 p42 | 0,527 **p10** | 0,511 **p3** |
| ATR(14) relatif | 0,078 p32 | 0,069 p19 | 0,093 p66 | 0,078 p33 |
| dispersion cross-sectionnelle | 0,029 p74 | 0,032 p83 | 0,030 p72 | 0,026 p41 |
| corrélation intra-univers | 0,692 p35 | 0,568 **p13** | 0,761 p62 | 0,744 p57 |

### BTC contre alts

| | **A** | **B** | **C** | **D** |
|---|---|---|---|---|
| rendement BTC | **+24,6 %** | +6,0 % | −5,6 % | −3,9 % |
| rendement alt médian | **+14,8 %** | +5,4 % | −5,3 % | **−14,8 %** |
| corrélation alt-BTC | 0,773 **p97** | 0,683 p31 | 0,848 **p92** | 0,674 p27 |
| perte d'Alfred | −51,4 % | −29,5 % | −26,2 % | −25,2 % |

### Funding

Convention Hyperliquid : taux > 0 ⇒ les LONGs paient, **les SHORTs reçoivent**.
Référence sur toute la période : **+1,455 bps/jour**, 78,7 % d'échantillons
positifs.

| | **A** | **B** | **C** | **D** |
|---|---|---|---|---|
| taux moyen | +1,486 bps/j | **+2,785** | −0,017 | **+3,580** |
| vs baseline | ≈ baseline | ×1,9 | neutre | ×2,5 |
| échantillons positifs | 81 % | 90 % | 70 % | 94 % |
| encaissé par un SHORT de $1 000 | +$14,26 | +$8,35 | −$0,05 | +$10,74 |
| soit, en % de la poche | +1,4 % | +0,8 % | −0,0 % | +1,1 % |

Extrêmes par token : ENA paie systématiquement les SHORTs (jusqu'à −7,5 bps/j en
C) ; DOGE, APT, PYTH et CRV paient les LONGs (jusqu'à +6,7 bps/j pour APT en B).

## 4. Application de la grille

### L'hypothèse « chop violent » est RÉFUTÉE — et pas par la branche prévue

La grille exigeait, pour confirmer le chop, **efficacité basse ET retournements
élevés**. La première condition est remplie sur trois fenêtres sur quatre
(p4 / p20 / p17, normale en D). **La seconde ne l'est sur aucune** : les
retournements sont *inférieurs* à la référence partout (p10, p47, p20, **p0**).

Les trois autres marqueurs de violence vont dans le même sens :

- **la part de mèche est basse** (p0, p42, p10, p3) — les barres sont des corps,
  pas des rejets ;
- **l'ATR relatif est normal** (p32, p19, p66, p33) — ces fenêtres ne sont pas
  plus volatiles que la moyenne ;
- **la dispersion cross-sectionnelle est à peine élevée** (p74, p83, p72, p41).

Rien de violent, rien de plus volatil, moins de retournements que d'habitude et
des mèches plus courtes. **La branche CHOP de la grille ne se déclenche pas**,
et avec elle aucune des pistes qu'elle appelait.

### La grille n'avait pas prévu ce cas

Elle anticipait deux issues : *efficacité basse + retournements élevés*
(chop) ou *efficacité haute* (chop réfuté, échec de TREND-v0 venu d'ailleurs).
La réalité est une troisième : **efficacité basse ET retournements bas**.

C'est une signature distincte, et arithmétiquement cohérente : le chemin
parcouru dépasse largement le déplacement net (facteur ~26 en A), sans que le
signe change plus souvent qu'à l'ordinaire. Les mouvements **persistent** à
l'échelle du jour et **s'annulent** à l'échelle de la fenêtre.

Le défaut de la grille est consigné ici, comme l'a été celui du § 1.5 de
TREND-v0 : elle traitait « efficacité basse » et « retournements élevés » comme
deux faces d'une même chose. Ce sont deux mesures indépendantes, et elles se
sont séparées.

### Le funding est FAVORABLE au receveur — bonus documenté, et borné

Le taux est positif dans trois fenêtres sur quatre, et nettement au-dessus de sa
référence dans deux d'entre elles (B ×1,9, D ×2,5). Le côté receveur — les
SHORTs — n'a **jamais** saigné : le pire cas est C, à −0,05 $ pour $1 000, soit
zéro. Par la grille, **la piste carry hérite d'un bonus documenté.**

Ce bonus est à consigner avec sa taille : **+0,8 % à +1,4 % de la poche** sur des
fenêtres où Alfred perdait de 25 à 51 %. Le fait est réel, son ordre de grandeur
est de vingt à cinquante fois inférieur à ce qu'il faudrait pour compenser.

### Les quatre fenêtres divergent — pas de régime unique

La grille demandait de le dire tel quel plutôt que d'en fabriquer une moyenne.
Elles divergent, et sur l'axe le plus important :

- **A et B sont des marchés HAUSSIERS.** En A, Alfred perd 51,4 % pendant que
  BTC gagne 24,6 % et les alts 14,8 %, avec une corrélation alt-BTC au 97ᵉ
  percentile. En B, BTC +6,0 %, alts +5,4 %.
- **C et D sont baissiers**, et différemment l'un de l'autre : C est une baisse
  d'ensemble (BTC −5,6 %, alts −5,3 %, corrélation au 92ᵉ percentile) ;
  **D est une faiblesse propre aux alts** (BTC −3,9 %, alts −14,8 %, soit
  presque quatre fois plus, avec une corrélation au 27ᵉ percentile).

Un point de méthode sur A : la fenêtre commence le 2024-08-03, date du pic
d'equity d'Alfred, soit deux jours avant le décrochage du 5 août 2024. Elle
contient donc un krach puis sa récupération complète — d'où une efficacité
directionnelle au 4ᵉ percentile malgré un rendement net franchement positif.

### Le cas D mérite d'être isolé

D est la fenêtre où l'explication par le chop échoue le plus nettement :
**le taux de retournement y est le plus bas des 640 fenêtres de référence**
(p0), la part de mèche au 3ᵉ percentile, l'efficacité directionnelle normale
(p43), et les alts baissent de 14,8 % de façon persistante. C'est une tendance
baissière alt, sans ambiguïté de mesure.

Alfred y perd 25,2 %. TREND-v0 y perd aussi (−$31, 12 trades clos, cf. C2).

## 5. Ce que l'étude établit

1. Les creux d'Alfred ne sont **pas** des régimes de chop violent : moins de
   retournements que la normale, mèches plus courtes, volatilité normale.
2. Trois d'entre eux ont une **efficacité directionnelle basse** malgré cela —
   des mouvements persistants au jour le jour qui s'annulent sur la fenêtre.
3. Ces fenêtres **ne partagent pas de direction de marché** : deux haussières,
   une baissière d'ensemble, une baissière propre aux alts.
4. Le **funding est favorable au côté receveur** dans trois fenêtres sur quatre,
   d'une ampleur de +0,8 % à +1,4 % de poche.
5. La grille de lecture était **incomplète** : elle n'avait pas prévu la
   conjonction efficacité basse / retournements bas, qui est ce qu'on observe.
