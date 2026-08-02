# Persistance de performance des wallets Hyperliquid — Phase 0 : faisabilité

> **Phase 0 = faisabilité des données, avant toute conception.** Ce document
> n'évalue aucune performance, ne classe aucun wallet et ne mesure aucune
> persistance. Il répond à quatre questions et s'arrête.
>
> **⛔ STOP après ce document. GO explicite de Seb requis pour la Phase 1.**

**Étude menée le** : 2026-08-02 · **empreinte** : git `c37a5ba+dirty` ·
run 2026-08-02T15:04Z
**Discipline** : lectures publiques uniquement — classement public
`stats-data.hyperliquid.xyz` et endpoint `info` d'Hyperliquid. **Aucune clé,
aucun ordre, aucune donnée privée.** Les adresses sont publiques par
construction.
**Sources** : `backtests/wallets/phase0_feasibility.py` ·
`analysis/output/wallet_phase0.json`

---

## 1. Le panel — constituable, et bien au-delà de 200

| | |
|---|---|
| classement public | **40 960 adresses** |
| accountValue max | $13 578 752 164 |
| accountValue médian | $662 |
| panel échantillonné | **250 adresses**, 250/250 exploitables |

**L'échantillonnage se fait à pas régulier dans le classement par TAILLE de
compte, pas par performance.** Ce choix est délibéré : prélever le sommet du
classement reviendrait à sélectionner sur la performance passée, c'est-à-dire à
introduire d'entrée le biais que la Phase 1 doit précisément éviter.

## 2. Profondeur récupérable — **GATE FRANCHI**

| percentile | span |
|---|---:|
| p0 | 18 j |
| p10 | 81 j |
| p25 | 270 j |
| **p50** | **494 j** |
| p75 | 620 j |
| p90 | 921 j |
| p100 | **1 166 j** |

**Médiane 487 jours (≈ 16 mois) ≥ 365 jours ⇒ GO.**

| seuil | adresses qualifiées |
|---|---|
| ≥ 180 j (2 trimestres) | **210/250 — 84,0 %** |
| ≥ 270 j (3 trimestres) | 182/250 — 72,8 % |
| ≥ 365 j (4 trimestres) | **160/250 — 64,0 %** |
| ≥ 540 j (6 trimestres) | 111/250 — 44,4 % |
| ≥ 730 j (8 trimestres) | 47/250 — 18,8 % |

Trimestres complets par adresse : **médiane 5,3**, p25 3,0, p75 6,8.

**Il n'y a pas de mur d'API.** La date de début la plus ancienne du panel est le
**2023-05-24**, et les spans s'étalent continûment jusqu'à 1 166 jours. Un
premier sondage sur 10 adresses avait suggéré un plafond vers 420 jours : c'était
un artefact d'échantillon — ces dix comptes avaient simplement été ouverts après
mi-2025.

## 3. Granularité — hebdomadaire

| | |
|---|---|
| points par adresse | min 32 · **médiane 72** · max 117 |
| pas médian | **7,00 jours** |

Le nombre de points est borné, donc **le pas s'élargit avec l'âge du compte**.
Un trimestre représente environ 13 points pour un compte récent, moins pour un
compte ancien. C'est une contrainte de résolution à porter dans la grille de
Phase 1, pas un obstacle.

## 4. Les fills historiques ne sont PAS servis

| endpoint | résultat |
|---|---|
| `userFills` | renvoie les ~2 000 derniers fills — soit **51 minutes** d'historique sur un compte actif |
| `userFillsByTime` | **n = 0 sur quatre fenêtres testées** (2025-09, 2026-01, 2026-05, 2026-07), sur l'adresse du panel à l'historique le plus profond |

**Conséquence sur l'espace de conception.** La mission envisageait
« userFills / positions par adresse ». Cette route est **fermée** pour une
adresse tierce : il ne reste que la série `portfolio`. La Phase 1 ne pourra donc
mesurer qu'un **rendement par période**, sans détail au trade, sans attribution
par token, sans horodatage d'entrée ou de sortie.

Ce fait est décrit ; il ne fonde aucune conclusion sur l'intérêt de la piste.

## 5. La bonne série est `pnlHistory`, pas `accountValueHistory`

Sur 12 adresses tirées au hasard : **12/12 disposent d'un `pnlHistory` non
constant**, aligné point à point sur `accountValueHistory`.

La distinction n'est pas cosmétique — `accountValue` bouge avec les **dépôts et
retraits**, `pnlHistory` est le P&L de trading. Mesurer une performance sur la
première confondrait un virement avec un gain. C'est exactement le piège relevé
sur HLP en Phase 0 du projet market making, où la valeur de compte a chuté de
$264 M à $222 M sur un mois **positif** en P&L.

## 6. Survivorship — le risque est réel mais faible, et mesuré

Une étude de persistance est ruinée si les perdants disparaissent du panel. Ce
n'est pas le cas ici :

| | |
|---|---|
| comptes à moins de $10 | **15 129 — 36,9 %** du classement |
| comptes à moins de $1 | 11 980 |
| p25 de l'accountValue | **$0,42** |
| sur 12 adresses tirées au hasard | **8 sont en P&L cumulé négatif** (jusqu'à −$548 562) |

Le classement public **conserve les comptes vidés et les comptes ruinés**. Il
n'est pas une liste de survivants.

**Risque résiduel, nommé sans être quantifié** : un compte entièrement fermé et
retiré du registre pourrait ne plus figurer au classement. Rien dans les données
publiques ne permet de mesurer cette population — c'est une réserve à porter au
rapport de Phase 1, pas un obstacle à la lancer.

## 7. Débit

| | |
|---|---|
| 250 appels `portfolio` | **109 s**, soit 2,3/s avec une pause volontaire de 0,12 s |
| latence | médiane 248 ms · p90 459 ms |
| erreurs 429 / 503 | **aucune** |
| extrapolation | un panel de 1 000 adresses ≈ **7 minutes** |

Aucune contrainte de débit ne s'oppose à la Phase 1.

## 8. Contrôle hérité — colonne temporelle

Le contrôle inscrit dans le cadre commun (min == max == date du run ⇒ mesure
fausse, incident `startTime=0` du projet basis) :

| colonne | valeurs distinctes | plage |
|---|---:|---|
| `first` | **114** | 2023-05-24 → 2026-07-15 |
| `last` | 1 | 2026-08-02 → 2026-08-02 |

**`first` n'est pas dégénérée** — c'est la colonne qui aurait trahi une API
renvoyant systématiquement la date du jour.

`last` vaut uniformément la date du run, et **c'est attendu** : toutes les séries
`portfolio` se terminent à l'instant de la requête. Ce n'est pas le mode de
défaillance visé par le contrôle, qui portait sur une **origine** faussement
récente. La distinction est écrite ici pour qu'un relecteur ne confonde pas les
deux.

## 9. Cas non prévu par la mission — nommé

La mission demandait « userFills / positions par adresse via API info ». Le § 4
établit que **les fills historiques tiers ne sont pas servis** — ni par
`userFills` (51 minutes de profondeur sur un compte actif), ni par
`userFillsByTime` (vide sur quatre fenêtres, y compris sur l'adresse la plus
ancienne du panel).

La Phase 1, si elle est lancée, portera donc sur des **rendements par période
issus de `pnlHistory`**, à granularité hebdomadaire, et pas sur le comportement
de trading. Conformément à l'invariant, ce fait est décrit et ne fonde aucune
conclusion opportuniste — ni sur l'intérêt de la piste, ni sur son abandon.

## 10. Verdict de faisabilité

| question | réponse |
|---|---|
| un panel de 200+ adresses est-il constituable ? | **oui** — 40 960 disponibles, 250 échantillonnées, 250 exploitables |
| la profondeur atteint-elle 12 mois ? | **oui** — médiane **487 j**, 64 % des adresses ≥ 365 j |
| les fills historiques sont-ils accessibles ? | **non** — seule la série `portfolio` l'est |
| la bonne série de performance existe-t-elle ? | **oui** — `pnlHistory`, non constante sur 12/12 |
| le panel est-il biaisé par la survie ? | **peu** — 36,9 % des comptes sont sous $10, les perdants sont présents |
| le débit est-il un obstacle ? | **non** — 250 adresses en 109 s, zéro erreur |

### **GATE : GO.**

Ce verdict porte uniquement sur la **disponibilité des données**. Rien dans ce
document ne dit que la Phase 1 trouvera une persistance, ni qu'elle n'en
trouvera pas.

**⛔ STOP.**
