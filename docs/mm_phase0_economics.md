# Market making sur Hyperliquid — Phase 0 : économie statique

> **⛔ VERDICT : STOP. La Phase 1 (collecteur 14 jours) n'est pas lancée.**
>
> La condition d'arrêt inscrite dans l'énoncé se déclenche — et une seconde,
> indépendante, se déclenche aussi. Les deux sont rendues séparément comme la
> grille l'exige.

**Étude menée le** : 2026-08-01
**Projet** : B (market making), jugé sur ses propres mérites — Alfred n'entre
dans aucune clause.
**Discipline respectée** : **aucun ordre n'a été placé**, ni mainnet ni testnet.
Toutes les données proviennent de lectures publiques (`/info`, websocket
public) et de la documentation Hyperliquid.

---

## 1. Barème maker — la condition d'arrêt

Source : [documentation Hyperliquid, *Perpetuals Fee Schedule*](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees),
consultée le **2026-08-01**.

### Paliers de volume (fenêtre glissante 14 jours, arrêtée en fin de journée UTC)

| palier | volume 14 j | taker | **maker** |
|---|---|---:|---:|
| **0** | base | 0,045 % | **+0,015 %** |
| 1 | > $5 M | 0,040 % | +0,012 % |
| 2 | > $25 M | 0,035 % | +0,008 % |
| 3 | > $100 M | 0,030 % | +0,004 % |
| 4 | > $500 M | 0,028 % | 0,000 % |
| 5 | > $2 Md | 0,026 % | 0,000 % |
| 6 | > $7 Md | 0,024 % | 0,000 % |

### Rebates maker — barème séparé, fondé sur la PART DE MARCHÉ

| palier | part du volume maker de la plateforme | rebate |
|---|---|---:|
| 1 | > 0,5 % | −0,001 % |
| 2 | > 1,5 % | −0,002 % |
| 3 | > 3,0 % | −0,003 % |

Ces rebates ne se gagnent pas au volume propre mais à la **fraction du volume
maker de tout Hyperliquid**. À l'échelle de la plateforme, 0,5 % du volume maker
se compte en dizaines de millions de dollars par jour.

### Ce que ça donne à la taille de Seb

| | |
|---|---|
| palier de volume | **0** (sans ambiguïté) |
| palier de rebate | **aucun** — hors d'atteinte de plusieurs ordres de grandeur |
| frais maker par fill | **+1,5 bps PAYÉS** |
| aller-retour maker/maker | **3,0 bps payés** |

**Le maker ne reçoit pas un rebate diminué : il paie.** La condition d'arrêt de
l'énoncé — « rebate < 0 après frais » — est remplie au sens strict.

---

## 2. Le barème confronté aux spreads réellement cotés

Un verdict fondé sur le seul barème serait légaliste : payer 1,5 bps n'est pas
disqualifiant en soi, si le spread capturé le couvre. Il faut donc le chiffre
d'en face.

Relevé de 20 snapshots L2 espacés de 2 s, le 2026-08-01, sur 11 tokens de
`Params.trade_symbols` répartis en trois strates de volume. La dernière colonne
est ce que gagne un maker qui capture **l'intégralité** du demi-spread, avant
toute sélection adverse et tout coût d'inventaire.

| token | strate | volume 24 h | spread médian | 1er niveau | profondeur ±5 bps | **demi-spread − 1,5 bps** |
|---|---|---:|---:|---:|---:|---:|
| SOL | liquide | $54,9 M | 0,14 bps | $28 031 | $1 455 520 | **−1,43 bps** |
| AAVE | liquide | $18,6 M | 1,09 bps | $250 | $72 998 | **−0,96 bps** |
| DOGE | liquide | $9,3 M | 0,14 bps | $917 | $337 456 | **−1,43 bps** |
| UNI | liquide | $9,0 M | 0,73 bps | $250 | $28 769 | **−1,13 bps** |
| LDO | moyen | $1,0 M | 0,61 bps | $375 | $10 840 | **−1,20 bps** |
| DOT | moyen | $0,9 M | 1,60 bps | $250 | $32 689 | **−0,70 bps** |
| INJ | moyen | $0,6 M | 1,93 bps | $250 | $9 561 | **−0,53 bps** |
| APT | moyen | $0,6 M | 1,79 bps | $719 | $17 839 | **−0,60 bps** |
| GALA | fin | $0,1 M | 11,20 bps | $291 | **$0** | +4,10 bps |
| COMP | fin | $0,1 M | 3,63 bps | $214 | $2 154 | +0,31 bps |
| BLUR | fin | $0,1 M | 3,57 bps | $13 | $812 | +0,29 bps |
| TON | fin | ~$0 M | — | — | — | aucun book exploitable |

**Sur 8 des 11 tokens, capturer la totalité du demi-spread ne paie même pas les
frais maker.** Sur SOL et DOGE, les frais valent **dix fois le spread entier**.

Les trois exceptions sont des tokens à $0,1 M de volume quotidien :

- **GALA** : +4,10 bps théoriques, mais **$0 de profondeur à ±5 bps** — le
  spread de 11 bps est celui d'un carnet vide.
- **COMP** (+0,31 bps) et **BLUR** (+0,29 bps) : marge brute inférieure au
  tiers d'un bps, sur des carnets de $2 154 et $812. Le premier niveau de BLUR
  vaut **$13**.

Sur ces trois-là, le maker serait seul dans le carnet — c'est-à-dire dans la
position d'exposition adverse maximale — pour un tiers de bps théorique.

Le verdict n'est donc pas légaliste : **le barème est structurellement perdant
à ce palier**, spreads observés à l'appui.

---

## 3. Benchmark passif — HLP

Source : API Hyperliquid, `vaultDetails` sur
`0xdfc24b077bc1425ad1dea75bcb6f8158e10df303`, interrogée le 2026-08-01.
Rendement approximé par *PnL cumulé de la période / valeur de compte moyenne
sur la période* — HLP subissant dépôts et retraits, ce n'est pas un rendement
par part, mais l'ordre de grandeur est bon.

| période | PnL | AV moyenne | rendement |
|---|---:|---:|---:|
| **12 mois glissants** | **+$64 829 640** | $392 672 376 | **+16,51 %** |
| T-3 · 2025-08 → 2025-11 | +$50 296 180 | $517 359 761 | **+9,72 %** |
| T-2 · 2025-11 → 2026-01 | +$1 471 778 | $384 752 559 | +0,38 % |
| T-1 · 2026-01 → 2026-05 | −$1 490 814 | $396 363 503 | **−0,38 %** |
| T-0 · 2026-05 → 2026-08 | +$377 115 | $286 281 042 | +0,13 % |

APR affiché par l'API à la date du relevé : **0,53 %**.

Le chiffre de 12 mois, +16,5 %, est porté **presque entièrement par un seul
trimestre**. Les neuf derniers mois cumulent environ **+0,13 %**.

Ce n'est pas qu'une barre basse à franchir. HLP est **le** teneur de marché de
référence sur Hyperliquid : palier de frais maximal, infrastructure dédiée,
$220 à $600 M de capital. Sa performance quasi nulle sur neuf mois est un
élément de preuve direct sur l'état de l'économie du maker sur cette
plateforme — et il va dans le même sens que le § 2.

*Réserve* : HLP n'est pas un pur teneur de marché (il absorbe aussi les
liquidations). C'est le proxy public le plus proche, pas un équivalent exact.

---

## 4. Latence de l'infrastructure existante

Machine : VPS OVH, **Dunkerque (FR)**, AS16276. Horloge **synchronisée NTP**
(vérifié).

### Le réseau n'est pas en cause

| mesure | valeur |
|---|---|
| ICMP vers `api.hyperliquid.xyz` | **3,0 ms** (min 2,99 / max 3,10 sur 8 paquets) |

### L'API l'est

REST `/info`, connexion **persistante** (le coût TLS a été isolé : premier appel
227-440 ms, puis keep-alive) :

| endpoint | médiane | p90 | p99 | plancher |
|---|---:|---:|---:|---:|
| `allMids` | 221,6 ms | 437,3 | 580,1 | 213,4 |
| `l2Book BTC` | 217,7 ms | 270,5 | 403,0 | 213,1 |

**Plancher de 213 ms avec un réseau à 3 ms** : les 210 ms restants sont du temps
applicatif côté plateforme, pas de la distance. Changer d'hébergeur n'y toucherait
pas.

### Le flux temps réel

Écoute passive de 120 s, 4 tokens, trois types de souscription. « Retard » =
horodatage de réception locale moins horodatage porté par le message.

| flux | token | msg/s | intervalle médian | **retard médian** |
|---|---|---:|---:|---:|
| `bbo` | BTC | 3,88 | **169 ms** | **293 ms** |
| `bbo` | SOL | 2,23 | 214 ms | 294 ms |
| `bbo` | GMX | 2,50 | 335 ms | 294 ms |
| `bbo` | BLUR | 0,54 | 342 ms | 291 ms |
| `l2Book` | tous | 0,19 | **5 386 ms** | 377 ms |
| `trades` | BTC | 0,53 | 1 011 ms | 333 ms |

Deux faits :

1. **`l2Book` est publié toutes les ~5,4 secondes**, à l'identique sur les
   quatre tokens — c'est une cadence de publication serveur, pas de l'activité
   de marché. Ce flux ne peut pas servir de base à une cotation.
2. **Le meilleur prix de BTC change toutes les 169 ms, et l'information arrive
   avec 293 ms de retard.** Au moment où le quoteur voit le carnet, celui-ci a
   déjà bougé environ deux fois.

Les 293 ms de retard WS et les 213 ms de plancher REST se corroborent : deux
mesures indépendantes situent la couche applicative Hyperliquid autour de
200-300 ms depuis cette machine.

### Ce qui n'a PAS été mesuré, et pourquoi

**La latence du chemin sortant — placement d'ordre — est inconnue.** La mesurer
exige d'envoyer un ordre, ce que la Phase 0 interdit absolument. Elle est donc
**déclarée manquante, pas estimée**. Le `/info` est un endpoint de requête et ne
constitue pas un proxy valide pour `/exchange`.

Cette lacune ne change pas le verdict : le chemin **entrant** suffit à le
rendre. Un quoteur qui reçoit un carnet vieux de 293 ms alors qu'il change
toutes les 169 ms est structurellement en retard, quelle que soit sa vitesse
d'émission.

---

## 5. Verdict, clause par clause

### NO-GO économique

> Le barème rend le maker structurellement perdant au palier de Seb.

Palier 0, **+1,5 bps payés** par fill, rebates hors d'atteinte de plusieurs
ordres de grandeur. Sur 8 tokens sur 11, la capture intégrale du demi-spread ne
couvre pas les frais. Sur les 3 restants, la marge brute théorique est de 0,3 à
4,1 bps dans des carnets de $0 à $2 154 de profondeur — avant sélection adverse.

**La condition d'arrêt inscrite dans l'énoncé est remplie.**

### NO-GO infrastructure — indépendant, rendu séparément

> La grille demandait que ce constat soit distinct du précédent. Il l'est : même
> avec un rebate positif, la latence resterait disqualifiante.

Carnet reçu avec 293 ms de retard, meilleur prix qui change toutes les 169 ms,
`l2Book` publié toutes les 5,4 s, plancher REST de 213 ms d'origine applicative
et non réseau. Le réseau, à 3 ms, n'est pas le facteur limitant — et ne peut
donc pas être amélioré par un déménagement d'hébergeur.

### Comparaison au benchmark passif

Un market maker actif doit battre « déposer dans le vault ». Le vault a rendu
**+16,5 % sur 12 mois, dont +0,13 % sur les neuf derniers**, tenu par
l'opérateur le mieux placé de la plateforme. Il n'existe aucune configuration
où un quoteur au palier 0, payant 1,5 bps et voyant le carnet avec 293 ms de
retard, dépasse ce résultat.

---

## 6. Cas non prévu par la grille — nommé comme tel

La grille plaçait le NO-GO infrastructure en **Phase 2**, jugé « incompatible
avec le τ où vit la capture » — donc conditionné à une mesure de markout que la
Phase 1 devait produire.

**La Phase 0 y répond sans avoir besoin de τ.** L'intervalle de changement du
meilleur prix (169 ms sur BTC) et le retard de réception (293 ms) se comparent
directement l'un à l'autre : l'information est périmée à l'arrivée,
indépendamment de l'horizon auquel la capture nette serait positive.

Conformément à l'invariant de grille, ce cas est **nommé** et ne fonde aucune
conclusion au-delà de ce qu'il mesure : il rend le NO-GO infrastructure
prononçable dès la Phase 0, il ne dit rien de plus.

---

## 7. Ce qui n'a pas été fait

- **Aucun ordre placé**, mainnet ou testnet.
- **Phase 1 non lancée** : pas de collecteur, pas de 14 jours d'enregistrement.
- **Phase 2 sans objet** : sélection adverse, markout et capture nette ne sont
  pas mesurés. Le STOP intervient avant.
- **Latence sortante non mesurée** — voir § 4.

Un NON documenté était un livrable de plein droit. C'en est un.
