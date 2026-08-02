# Basis & différentiels de funding inter-venues — Phase 0 : faisabilité DONNÉES

> **Phase 0 = faisabilité des données, avant toute conception** (leçon du projet
> market making). Ce document n'établit aucun basis, ne calcule aucun
> différentiel, ne conclut sur aucun edge. Il répond à quatre questions et
> s'arrête.
>
> **⛔ STOP après ce document. GO explicite de Seb requis pour la Phase 1.**

**Étude menée le** : 2026-08-02
**Discipline** : lectures publiques uniquement — `/info` Hyperliquid,
`api.binance.com`, `fapi.binance.com`, `api.bybit.com`. **Aucune clé, aucun
compte, aucun ordre.**
**Sources** : `backtests/basis/phase0_inventory.py` ·
`analysis/output/basis_phase0.json`
**Livrable de la Phase 1** (non produit à ce jour) : `docs/basis_feasibility.md`

---

## 1. Joignabilité

Depuis le VPS (OVH, Dunkerque, FR) — toutes les API publiques répondent :

| endpoint | statut | RTT |
|---|---|---:|
| `api.binance.com` spot klines | 200 | 234 ms |
| `fapi.binance.com` fundingRate | 200 | 242 ms |
| `api.bybit.com` spot / linear kline | 200 | 217 / 178 ms |
| `api.bybit.com` funding history | 200 | 190 ms |
| `www.okx.com` history-candles | 200 | 236 ms |

Aucun blocage géographique. La collecte historique est possible.

## 2. Univers — **32 tokens sur 34** exploitables des deux côtés

Critère : Hyperliquid dispose du mark **et** du funding, **et** la venue B
(Binance ou Bybit) a un spot **et** un perpétuel en statut *TRADING*.

### Les deux exclusions, vérifiées ticker par ticker

| token | Binance | Bybit | Hyperliquid | verdict |
|---|---|---|---|---|
| **TON** | spot `BREAK`, perp `SETTLING` | linear `Closed`, spot absent | bougies **s'arrêtent au 2026-06-15** | retrait de cote en cours sur les trois venues |
| **XMR** | spot `BREAK`, perp `TRADING` | idem | complet | **pas de jambe spot** — aucun basis possible |

Les symboles `TONUSDT` et `XMRUSDT` **existent** dans les métadonnées : ce ne
sont pas des ratés de correspondance de ticker, ce sont des statuts non
négociables. La vérification a été faite explicitement, parce qu'une exclusion
pour cause de mauvais ticker aurait sous-estimé l'univers sans bruit.

### Couverture du funding Hyperliquid — 32 tokens retenus

| | |
|---|---|
| médiane | **100,0 %** |
| minimum | **98,0 %** |
| tokens sous 100 % | 14 / 32 |
| plancher de la grille (95 %) | **aucun token en dessous** |

Pires trous : ARB, AVAX, DOGE, DYDX — 83 interruptions de plus de 2 h chacun,
écart maximal **8,4 h**. Ces trous existent et sont datables ; ils devront être
journalisés trou par trou en Phase 1, pas moyennés.

## 3. Découverte structurelle — Hyperliquid n'a pas de spot sur cet univers

`spotMeta` renvoie **480 tokens spot** sur Hyperliquid. L'intersection avec
`Params.trade_symbols` est **VIDE** : le spot HL est composé d'actifs natifs et
de mèmes (PURR, HFUN, JEFF, LICK…), pas des majors que trade Alfred.

**Conséquence sur l'espace de conception** — un basis perpétuel/spot *à
l'intérieur* d'Hyperliquid est **impossible** pour ces 32 tokens. Toute
structure est nécessairement **inter-venues** : la jambe spot vient forcément de
Binance ou Bybit, avec ce que cela implique de capital immobilisé des deux
côtés, de deux barèmes de frais et de friction de transfert.

Ce fait n'est pas une conclusion sur la rentabilité. C'est une contrainte de
structure, à connaître avant d'écrire une spec.

## 4. Sémantique du funding — le piège, et il n'est pas uniforme

| venue | intervalle | base du taux | source |
|---|---|---|---|
| **Hyperliquid** | **1,00 h** (espacement médian **mesuré**) | **par heure** | `funding_history.db`, 937 k échantillons |
| **Binance** | 8 h ou **4 h** | par période | `fapi/v1/fundingInfo.fundingIntervalHours` |
| **Bybit** | 8 h ou **4 h** | par période | `v5/instruments-info.fundingInterval` (minutes) |

Le facteur de conversion **n'est pas un facteur 8 uniforme**. Il vaut
l'intervalle de la venue B, qui varie par token **et entre venues pour le même
token** :

| groupe | n | tokens |
|---|---:|---|
| BN 8 h · BY 8 h | 28 | AAVE ADA APT ARB AVAX BCH COMP CRV DOGE DOT DYDX GALA GMX INJ LDO LINK NEAR OP PENDLE SAND SEI SNX SOL STX SUI UNI WLD XMR |
| BN 4 h · BY 4 h | 3 | ENA IMX PYTH |
| **BN 4 h · BY 8 h** | 1 | **BLUR** |
| **BN 8 h · BY 4 h** | 1 | **MINA** |

> **Réserve méthodologique pour la Phase 1.** Ces intervalles **changent dans le
> temps** — Binance a basculé plusieurs paires de 8 h à 4 h — et `fundingInfo`
> ne renvoie que la valeur **courante**. Une étude historique qui appliquerait
> l'intervalle d'aujourd'hui à trois ans d'historique se tromperait d'un facteur
> 2 sur les périodes concernées. La Phase 1 devra **dériver l'intervalle de
> l'espacement des horodatages** enregistrement par enregistrement, jamais du
> champ de métadonnées.

## 5. Alignement horaire — **ALIGNABLE**

| série | granularité | frontières observées |
|---|---|---|
| bougies Hyperliquid | 4 h | multiples de 4 h UTC |
| funding Hyperliquid | 1 h | multiples de 1 h UTC |
| klines spot Binance / Bybit | 1 h | **00:00, 01:00, 02:00…** exactes |
| funding Binance / Bybit | 8 h | **00:00, 08:00, 16:00** exactes |

Vérifié sur SOL, AAVE et GMX : aucun décalage, aucun horodatage hors frontière.
Une grille horaire commune UTC est constructible sans interpolation.

## 6. Profondeur d'historique — contrainte selon l'étude

Les deux études que la mission envisage n'ont pas la même profondeur exploitable.

### Différentiel de funding (HL funding × venue B funding)

Profond. Début commun le plus tardif : **2024-12-04** (SAND), puis 2024-04-02
(ENA). L'essentiel de l'univers remonte à 2023.

### Basis perpétuel-spot (mark HL × spot venue B)

**C'est le mark Hyperliquid qui borne**, pas la venue B — dont le spot remonte à
2018-2021 pour tout l'univers.

| début commun | tokens |
|---|---|
| 2025-12-23 | DYDX |
| 2025-10-28 | UNI, DOT, BCH |
| 2025-02-16 | SOL, SAND, IMX |
| 2023-12-11/12 | le reste (~25 tokens) |

Quatre tokens n'offriraient que 7 à 9 mois d'historique de basis. C'est une
contrainte d'effectif à porter dans la grille de Phase 1, pas un obstacle.

## 7. Frais des DEUX jambes — barèmes datés

Palier applicable : **le plus bas de chaque venue** (VIP 0 / non-VIP).

| venue · marché | maker | taker | source |
|---|---:|---:|---|
| Hyperliquid perp | **+0,015 %** (1,5 bps **payés**) | **0,045 %** (4,5 bps) | [docs HL](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees), 2026-08-01 |
| Binance spot | 0,100 % (10 bps) | 0,100 % (10 bps) | [barème Binance](https://www.binance.com/en/fee/schedule), 2026-08-02 — 7,5 bps avec remise BNB 25 % |
| Binance USDⓈ-M perp | 0,0200 % (2 bps) | 0,0500 % (5 bps) | barème Binance VIP 0, 2026-08-02 |
| Bybit spot | 0,10 % (10 bps) | 0,10 % (10 bps) | [barème Bybit](https://www.bybit.com/en/announcement-info/fee-rate/), 2026-08-02 |
| Bybit USDT perp | 0,02 % (2 bps) | 0,055 % (5,5 bps) | barème Bybit non-VIP, 2026-08-02 |

### Coût aller-retour complet — quatre fills (ouverture 2 jambes, clôture 2 jambes)

| structure | en taker des deux côtés | **seuil de rentabilité Phase 1** (2 × AR) |
|---|---:|---:|
| HL perp × Binance perp | (4,5 + 5) × 2 = **19 bps** | **38 bps** |
| HL perp × Bybit perp | (4,5 + 5,5) × 2 = **20 bps** | **40 bps** |
| HL perp × Binance spot | (4,5 + 10) × 2 = **29 bps** | **58 bps** |
| Binance spot × Binance perp | (10 + 5) × 2 = **30 bps** | **60 bps** |

Variante **maker des deux côtés**, à titre indicatif : (1,5 + 2) × 2 = **7 bps**
d'aller-retour, soit un seuil de 14 bps. Elle suppose des exécutions passives
sur les deux jambes ; `docs/mm_phase0_economics.md` a mesuré la veille que le
carnet Hyperliquid arrive avec 293 ms de retard depuis cette machine. Le
chiffre est donné pour que la Phase 1 puisse rapporter les deux, **sans qu'il
soit conclu quoi que ce soit ici**.

## 8. Volumétrie de la collecte Phase 1

| série | volume estimé | appels |
|---|---:|---:|
| funding Binance, 32 tokens × ~3 ans | ~35 k enregistrements | ~40 |
| funding Bybit, idem | ~35 k | ~180 (pagination 200) |
| klines spot 1 h, 32 tokens × 3 ans, une venue | ~840 k bougies | ~840 |

Collecte modeste, réalisable en une passe avec pauses de politesse. Aucun
obstacle de volumétrie.

## 9. Deux mesures fausses attrapées pendant cette phase

Consignées parce que le mode de défaillance est celui que le projet traque
depuis une semaine : **un nombre plausible sort au lieu d'une erreur**.

1. **`startTime=0` est silencieusement ignoré par Binance.** Le premier passage
   datait l'origine du funding de **tous** les tokens au 2026-08-02 —
   c'est-à-dire aujourd'hui. Détecté parce que le minimum et le maximum de la
   colonne étaient identiques et valaient la date du run. Corrigé en ancrant la
   requête au lancement de Binance Futures (2019-09).
2. **L'avertissement « facteur 8 » du script était faux**, contredit par ses
   propres données : quatre tokens sont à 4 h, et deux d'entre eux ont un
   intervalle **différent selon la venue**. Message corrigé pour imprimer les
   groupes réels.

Aucune des deux n'aurait planté. Les deux auraient produit un rapport crédible.

## 10. Cas non prévus par la mission — nommés, sans conclusion

1. **La mission demandait « spot + funding Binance/Bybit » et un « basis
   perp-spot ».** Elle n'anticipait pas que Hyperliquid n'a **aucun** spot sur
   cet univers (§ 3) : le basis ne peut donc pas être intra-venue. Le fait est
   décrit ; il ne fonde ici aucune préférence de structure.
2. **L'hétérogénéité des intervalles de funding** (4 h/8 h, et divergents entre
   venues pour BLUR et MINA) n'était pas anticipée, non plus que leur
   **variation dans le temps** (§ 4). C'est une contrainte de méthode pour la
   Phase 1, pas un résultat.

## 11. Verdict de faisabilité

| question | réponse |
|---|---|
| les API sont-elles joignables ? | **oui**, les trois |
| combien de tokens des deux côtés ? | **32 sur 34** |
| la couverture est-elle suffisante ? | **oui** — médiane 100 %, minimum 98 %, aucun sous le plancher de 95 % |
| les séries sont-elles alignables ? | **oui**, grille horaire UTC commune sans interpolation |
| les frais des deux jambes sont-ils établis ? | **oui**, datés et sourcés — seuil de rentabilité de 38 à 60 bps selon la structure |

**La Phase 1 est réalisable sur le plan des données.** Rien dans ce document ne
dit qu'elle trouvera quoi que ce soit.

**⛔ STOP.**
