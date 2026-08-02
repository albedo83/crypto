# Étude d'événements — Phase 0 : faisabilité et définition

> **Phase 0 = faisabilité et comptage.** Aucun rendement post-événement n'est
> mesuré ici. Trois questions, puis arrêt.
>
> **⛔ STOP après ce document. GO explicite de Seb requis pour la Phase 1.**

**Étude menée le** : 2026-08-02 · **empreinte** : git `d486dbd+dirty` ·
run 2026-08-02T15:41Z
**Discipline** : lectures publiques uniquement — `candleSnapshot` et `meta`
d'Hyperliquid, base OI locale. Aucune clé, aucun ordre.
**Sources** : `backtests/events/phase0_inventory.py` ·
`analysis/output/event_phase0.json`

## 0. Destination, fixée avant les chiffres

> Un résultat positif en Phase 1 **ne fonde pas un second bot**. Le rebond
> post-cascade appartient à la famille **réversion** — celle d'Alfred. Il serait
> consigné comme **candidat signal pour la saison prospective de septembre**,
> rien d'autre.

Rappelé ici pour que la lecture du rapport de Phase 1 ne puisse pas requalifier
l'objet après coup.

## 1. Profondeur servie par l'API — mesurée par recherche binaire

| intervalle | profondeur | remonte à |
|---|---:|---|
| **1 h** | **210 jours** | 2026-01-04 |
| 4 h | 835 jours | 2024-04-19 |
| 1 j | 1 298 jours | 2023-01-12 |

Plafond mesuré : **5 000 bougies par requête** (une demande de 7 200 en rend
5 002).

## 2. OI horaire en base

| | |
|---|---|
| lignes | **767 383** |
| symboles | 37 |
| couverture | **2023-05-20 → 2026-06-29** |
| pas | 1,00 h |

> **À signaler** : la collecte d'OI **s'arrête au 2026-06-29**, soit 34 jours
> avant ce run. Ce n'est pas une limite d'API — c'est une collecte qui ne tourne
> plus. Elle tronque la fenêtre exploitable par la droite.

## 3. ⚠ La définition n'est calculable que sur 176 jours

La définition de cascade exige **trois** grandeurs à l'heure : ΔOI, volume,
amplitude de mèche.

- **ΔOI horaire** : disponible en base depuis 2023-05, mais s'arrête au
  2026-06-29.
- **Volume et mèche horaires** : **absents de la base** — les bougies locales
  sont en 4 h. Ils viennent de l'API, qui ne sert le 1 h que sur **210 jours**.

```
fenêtre où les TROIS conditions sont calculables
  2026-01-04  →  2026-06-29   =   176 jours
```

Bornée à gauche par la profondeur du 1 h, à droite par l'arrêt de la collecte
d'OI.

**Conséquence pour la Phase 1, nommée sans conclusion** : les événements ne
peuvent être **détectés** que sur 176 jours. En revanche, la **mesure** des
rendements post-événement (4 h / 24 h / 72 h) et la **baseline
inconditionnelle** restent calculables en 4 h sur 835 jours ≈ 27,5 mois. La
Phase 1 est donc réalisable, mais son échantillon d'événements est une tranche
de six mois, **pas de 28 mois**. Ce fait est décrit ; il ne fonde ici aucune
conclusion sur l'intérêt de la piste.

## 4. Cascades — comptage

**142 314 bougies 1 h** collectées sur **34 tokens** (`Params.trade_symbols`).

Définition appliquée sans variante : `ΔOI ≤ p5` **et** `volume ≥ p95` **et**
`mèche ≥ p95`, percentiles calculés **par token** sur son propre historique de
la fenêtre.

> **Garde-fou de dénominateur** (leçon « +264 Md % ») : **ΔOI est mesuré en
> unités absolues**, jamais en pourcentage. Les percentiles étant calculés
> token par token, aucune comparaison inter-token n'exige de normaliser — et le
> risque de division par un OI proche de zéro est ainsi supprimé, pas encadré.

| | n |
|---|---:|
| événements **bruts** | **416** |
| événements **après dé-clustering** (< 24 h absorbé) | **241** |
| facteur d'absorption | **1,73** — 42 % des détections étaient des répliques |
| tokens émettant | **34 / 34** |

Par token : minimum **1** (DOT), médiane **7** (SEI), maximum **12** (WLD).

Le facteur de 1,73 confirme la prémisse qui justifiait la règle : **sans
dé-clustering, une seule cascade aurait été comptée jusqu'à cinq fois** et le
n aurait été un mensonge.

### Chevauchement inter-tokens

| | |
|---|---:|
| heures distinctes portant au moins un événement | **194** |
| heures portant **plusieurs** tokens | **28 (14,4 %)** |

Une cascade sur sept est un événement de marché simultané sur plusieurs tokens.
En agrégé, la Phase 1 devra les compter **une fois** — soit **194 événements
agrégés** au lieu de 241.

## 5. Listings

| | n |
|---|---:|
| perps listés aujourd'hui | **232** |
| datés (première bougie 1 j retrouvée) | 231 |
| **listés dans les 28 derniers mois** | **97** |
| dont appartenant à `Params.trade_symbols` | **0** |

Répartition trimestrielle des 97 :

| 2024Q2 | 2024Q3 | 2024Q4 | 2025Q1 | 2025Q2 | 2025Q3 | 2025Q4 | 2026Q1 | 2026Q2 | 2026Q3 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 16 | 6 | 19 | 18 | 12 | 13 | 8 | 2 | 1 | 2 |

Les dix plus récents : CASHCAT (2026-07-11), GRAM (2026-07-02), CHIP
(2026-04-22), AZTEC (2026-02-12), SKR (2026-01-22), LIT (2025-12-22), FOGO
(2025-12-11), STABLE (2025-11-06), CC (2025-10-31), MEGA (2025-10-22).

**54 perps** ont leur première bougie **exactement sur la borne de l'API 1 j**
(2023-01-12) : leur listing réel est antérieur et inconnu. Ils sont hors des
28 mois et n'entrent pas dans le compte de 97 — intersection vérifiée à **0**.

Les bougies 4 h remontant à 2024-04-19, **les 97 listings de la fenêtre sont
tous couverts** pour la mesure des rendements post-listing.

## 6. n par type — la réponse demandée

| type | n brut | **n retenu** | seuil de la Phase 1 | statut |
|---|---:|---:|---:|---|
| **cascades** (par token) | 416 | **241** | 30 | au-dessus |
| **cascades** (agrégées inter-tokens) | — | **194** | 30 | au-dessus |
| **listings** | — | **97** | 30 | au-dessus |

## 7. Cas non prévu par la mission — nommé

La mission demandait les cascades « **proxy sur données en base** ». Le § 3
établit que **volume et mèche horaires ne sont pas en base** : il a fallu
collecter 142 314 bougies 1 h depuis l'API, et cette source plafonne à
210 jours.

L'écart n'est donc pas seulement de source, il est de **portée** : la fenêtre
de détection passe de 28 mois à **176 jours**. Conformément à l'invariant, ce
fait est décrit et ne fonde aucune conclusion — ni sur l'ouverture de la
Phase 1, ni sur son abandon.

## 8. Verdict de faisabilité

| question | réponse |
|---|---|
| la définition de cascade est-elle calculable ? | **oui**, mais sur **176 jours** seulement |
| combien d'événements, avant/après dé-clustering ? | **416 → 241** (194 agrégés) |
| les listings sont-ils datables ? | **oui** — 231/232, dont **97** dans les 28 mois |
| les seuils de n sont-ils atteints ? | **oui**, pour les deux types |
| la mesure des rendements est-elle possible ? | **oui** — 4 h sur 835 jours pour les horizons et la baseline |

Ce verdict porte uniquement sur la **disponibilité et le comptage**. Rien ici
ne dit que la Phase 1 trouvera un effet.

**⛔ STOP.**
