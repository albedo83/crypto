# Comblage de la série OI du backtest — protocole

> **⚠ SEUILS D'ACCEPTATION ÉCRITS ET COMMITTÉS AVANT TOUTE MESURE DE
> RECOUVREMENT.** Aucune comparaison entre les deux sources n'a été faite au
> moment où ces lignes sont écrites. L'historique git en fait foi.
>
> **Rédigé le** : 2026-08-02

## 0. Le problème

`backtests/output/pairs_data/*_oi_4h.json` s'arrête au **2026-06-15** (48 jours).
Ces fichiers alimentent `load_oi()` → `oi_delta_24h_bps` → **gate OI LONG**, une
règle de production.

L'amont d'origine — `s3://hyperliquid-archive/asset_ctxs/` — **ne publie plus
après le 2026-06-29** (vérifié : 1137 dates, la dernière est `20260629`). Il n'y
a donc pas de reprise possible depuis cette source.

**Remplaçant** : `market_snapshots` de `alfred/data/market.db`, écrit par le
MarketDataMaster à chaque poll REST — horaire, 35 symboles, depuis le
2026-06-10. Il couvre le trou et **recouvre l'ancienne source sur 19 jours**
(2026-06-10 → 2026-06-29), ce qui permet de valider la soudure au lieu de
l'espérer.

## 1. Méthode

1. **Ré-échantillonnage** : `market_snapshots` (horaire) est ramené à la grille
   4 h de la série existante — pour chaque frontière 4 h, on retient le relevé
   **le plus proche dans un rayon de 2 h**. Au-delà, le point n'est pas produit.
2. **Validation sur recouvrement** : sur 2026-06-10 → 2026-06-15 (fin réelle des
   fichiers), les deux sources sont comparées point à point, par token.
3. **Soudure** : les points issus du live sont ajoutés **après** le dernier
   point existant de chaque fichier. Aucun point historique n'est réécrit.
4. **Traçabilité dans les données** : chaque enregistrement ajouté porte
   `"src": "live"`. Les enregistrements existants restent inchangés — leur
   absence de champ `src` vaut `"s3"`. Le point de soudure est journalisé par
   token dans `analysis/output/oi_backfill.json`.

## 2. SEUILS D'ACCEPTATION — pré-enregistrés

Écart relatif point à point : `|oi_live − oi_s3| / oi_s3`.

Un token est **accepté** si, sur le recouvrement :

| critère | seuil |
|---|---|
| points comparables | **≥ 20** |
| **médiane** de l'écart relatif | **< 1,0 %** |
| **p95** de l'écart relatif | **< 5,0 %** |

Le comblage est **appliqué globalement** si **≥ 90 % des tokens éligibles**
passent les trois critères. Sinon : aucun fichier n'est modifié, et le rapport
dit pourquoi.

Un token qui échoue individuellement **n'est pas comblé** — son fichier reste
tronqué — et il est **nommé** dans le rapport.

### Cas tranchés d'avance

| cas | décision |
|---|---|
| **TON** | **EXCLU du comblage.** Son arrêt n'est pas un trou : le token est en retrait de cote (Phase 0 de l'étude d'événements — Binance `BREAK`, Bybit `Closed`, bougies HL arrêtées au 2026-06-15). Son OI doit **rester** arrêté |
| token absent de `market_snapshots` | non comblé, nommé |
| moins de 20 points comparables | non comblé, nommé — pas de validation possible |
| écart médian ≥ 1 % mais p95 < 5 % | **échec** — les trois critères sont conjonctifs |
| taux de réussite global < 90 % | **aucun fichier modifié**, protocole rendu au rapport |

## 3. Ce qui n'est PAS fait ici

- **Aucun point historique n'est réécrit.** Le comblage est purement additif.
- **Aucun re-run de verdict** n'est déclenché par ce document ; la
  re-validation est un chantier distinct.
- **Aucune modification du bot live** — il ne lit pas ces fichiers.

## 4. Suite : `market_snapshots` devient la source pérenne

L'amont S3 étant mort, `market_snapshots` est désormais **la** source d'OI de
recherche. Elle entre dans `data_freshness.py` au statut le plus strict (3 h,
chemin chaud) — elle y est déjà.

---

## 5. Résultat — **COMBLAGE REFUSÉ PAR SON PROPRE PROTOCOLE**

**Run** : 2026-08-02 · `backtests/oi_backfill.py --dry-run` ·
`analysis/output/oi_backfill.json` · **aucun fichier modifié**.

| | |
|---|---|
| tokens éligibles | 33 (TON exclu d'avance) |
| **tokens réussissant les trois critères** | **16 — 48,5 %** |
| seuil global requis | **90 %** |
| **verdict** | **REFUS** |

Tokens en échec, non comblés : ADA, APT, ARB, CRV, DOGE, ENA, GALA, GMX, INJ,
NEAR, PYTH, SAND, SEI, SNX, STX, SUI, WLD.

### Ce qui a échoué, et ce qui n'a pas échoué

**Les deux sources mesurent bien la même grandeur.** Ratio live/S3 apparié à la
minute près, médiane sur 31 points :

| token | médiane | p5 | p95 |
|---|---:|---:|---:|
| AAVE | **0,9999** | 0,9937 | 1,0090 |
| WLD | 0,9975 | 0,9503 | 1,0401 |
| PYTH | 1,0113 | 0,8475 | 1,0847 |
| SOL | 1,0041 | 0,9668 | 1,0230 |

Pas d'erreur d'unité, pas de décalage de niveau. **C'est le critère p95 qui
casse**, pas la médiane : sur 17 tokens la médiane est sous 1,3 % mais la queue
monte à 5-15 %.

Deux causes, mesurées :

1. **Décalage d'échantillonnage** : les relevés live tombent à **:03** après
   l'heure, les points S3 à **:00** pile. Trois minutes d'écart sur une grandeur
   dont la variation horaire a un p95 de 1 à 3 % selon le token.
2. **Recouvrement trop mince** : les fichiers s'arrêtant au **2026-06-15** et la
   télémétrie live démarrant au **2026-06-10**, le recouvrement réel est de
   **5 jours — 30 points**, pas 19 jours. Le § 1 de ce protocole disait 19
   jours : c'était le recouvrement avec `oi_history.db`, pas avec les fichiers
   à combler. **Erreur de rédaction, corrigée ici.** Sur 30 points, une poignée
   d'heures agitées suffit à faire sauter un p95.

### Ce qui n'est PAS fait

Un protocole révisé validant contre `oi_history.db` — même amont S3 que les
fichiers, recouvrement de 19 jours, ~114 points — donnerait une base
statistique quatre fois plus large. **Ce n'est pas appliqué** : changer la base
de validation après avoir vu l'échec serait exactement le déplacement de poteau
que ce protocole existe pour empêcher. C'est une proposition, à décider.

---

## 6. Ce qui a été corrigé indépendamment du comblage

### 6.1 Parité de l'obsolescence — bug réel, corrigé

`backtests.oi_delta_24h_bps` utilisait `bisect_right - 1` sans garde d'âge :
au-delà des données il retenait le dernier point connu et rendait un delta
**figé**. Le live, lui, rend `None` dès que l'échantillon s'éloigne de plus de
4 h de la cible.

Garde ajoutée côté backtest (`OI_MAX_STALE_H = 4.0`), identique au live.

**Impact mesuré, à politique inchangée (`open`)** — les quatre fenêtres de
doctrine bougent :

| fenêtre | référence (avant) | après correctif | Δ | n trades |
|---|---:|---:|---:|---:|
| OOS-0 | $1 386,72 | $1 223,52 | **−163,20** | 315 |
| OOS-6 | $2 271,84 | $2 363,21 | **+91,37** | 256 |
| OOS-12 | $1 611,15 | $1 572,14 | **−39,01** | 275 |
| OOS-18 | $1 369,42 | $1 447,35 | **+77,93** | 280 |

> **Fait inattendu, nommé** : OOS-6, OOS-12 et OOS-18 sont **entièrement
> antérieures** au 2026-06-15. Qu'elles bougent signifie que la garde mord aussi
> sur les **trous internes** de la série OI, pas seulement sur sa fin. Le
> problème n'était donc pas « 48 jours de données manquantes » mais « une série
> trouée sur toute sa longueur, dont les trous étaient comblés en silence par
> la dernière valeur connue ».

### 6.2 Sémantique de l'absence — implémentée, défaut INCHANGÉ

`Params.oi_missing_policy`, appliqué dans le noyau partagé `alfred/rules.py`
(donc bot **et** backtest — vérifié : le backtest appelle bien
`_rules.entry_gate`) :

| valeur | comportement |
|---|---|
| **`open`** *(défaut actuel)* | gate inapplicable, le LONG passe |
| `block` | pas de LONG sans donnée OI |
| `block_stale` | bloque si la **source est morte**, laisse passer si l'historique **se constitue** (démarrage à froid) |

**Coût mesuré de la politique retenue** contre la référence d'avant correctif.
⚠ La colonne était étiquetée `block` : elle mesure en réalité **`block_stale`**,
car le backtest transmet `oi_stale=False` sur les débuts de série. Étiquette
corrigée le 2026-08-02 ; les chiffres, eux, sont bien ceux de la politique
retenue. (`block` et `block_stale` étaient alors identiques dans le code — un
défaut latent corrigé depuis : `block` bloque désormais aussi à froid.)

| fenêtre | `open` | `block_stale` | Δ | n `open`/`block_stale` |
|---|---:|---:|---:|---:|
| OOS-0 | $1 223,52 | $1 371,11 | −15,61 | 315 / 290 |
| OOS-6 | $2 363,21 | $2 822,36 | **+550,52** | 256 / 250 |
| OOS-12 | $1 572,14 | $1 365,42 | **−245,73** | 275 / 267 |
| OOS-18 | $1 447,35 | $1 494,14 | +124,72 | 280 / 272 |

Effet **non systématique** : deux fenêtres gagnent, deux perdent.

> **⚠ CAS NON PRÉVU PAR L'ÉNONCÉ — la raison pour laquelle je n'ai pas tranché.**
> Côté **live**, `oi_delta_24h_bps` rend `None` tant que l'historique OI en
> mémoire fait moins de 23 h. Sous la politique `block`, **tout LONG serait
> bloqué pendant ~23 h après CHAQUE redémarrage du bot**. Ce n'est pas « ne pas
> trader sur une jauge débranchée » — c'est ne pas trader pendant que la jauge
> chauffe. D'où la troisième valeur, `block_stale`, qui distingue les deux cas.
>
> Le défaut reste `open` : **rien ne change en silence sur de l'argent réel**.
> Le choix entre `block` et `block_stale` appartient à Seb, et il est désormais
> outillé et chiffré.

### 6.3 Conséquence sur les références publiées

`docs/backtests.md` et toutes les références de capital antérieures au
2026-08-02 ont été produites **avec la valeur OI figée**. Elles sont
**périmées en absolu**. Les verdicts **comparatifs** ne sont pas mécaniquement
invalidés — les deux jambes de chaque comparaison partageaient la distorsion —
mais cela reste à **vérifier**, pas à supposer.

La re-validation n'est pas lancée : l'énoncé la prévoyait « sur inputs comblés
+ sémantique None », or le comblage est refusé. Sa prémisse n'est pas réunie.

---

# AMENDEMENT — tir unique (2026-08-02)

> **Écrit et committé AVANT le run amendé.** Aucune mesure sur la nouvelle base
> n'a été faite au moment où ces lignes sont écrites.

Décidé par Seb au checkpoint, à visage découvert : le protocole initial a échoué
sur une base mal spécifiée par **erreur de rédaction documentée** (§ 5 —
recouvrement réel de 5 j / 30 points au lieu des 19 j annoncés) et sur un
**artefact d'horodatage** (:03 live contre :00 S3, sur une grandeur qui bouge).

## A1. Ce qui change

| | avant | **amendé** |
|---|---|---|
| base de validation | fichiers `*_oi_4h.json` (fin 2026-06-15) | **`oi_history.db`** — même amont S3, fin 2026-06-29 |
| recouvrement | 5 jours, ~30 points | **19 jours, ~114 points** par token |
| appariement | plus proche dans un rayon de 2 h | **tolérance d'alignement ±5 min**, spécifiée ici avant le run |

## A2. Ce qui NE change PAS

Les **trois critères et leurs seuils sont inchangés**, ainsi que le taux de
réussite global :

| critère | seuil |
|---|---|
| points comparables | ≥ 20 |
| médiane de l'écart relatif | < 1,0 % |
| p95 de l'écart relatif | < 5,0 % |
| **tokens passant les trois** | **≥ 90 %** |

TON reste exclu (retrait de cote = arrêt légitime, pas un trou).

## A3. TIR UNIQUE

> **Un nouvel échec clôt le sujet.** Le trou devient un **trou définitif
> assumé** : la série reste honnêtement trouée — la garde d'âge du § 6.1 rend
> désormais l'absence **visible** au lieu de la reboucher en silence — et
> `market_snapshots` ne sert que de **source pérenne POST-juin**, sans backfill.

Aucun troisième protocole ne sera écrit.

## A4. En cas de succès

Comblage appliqué, point de soudure et source marqués **dans** les données
(`"src": "live"`), TON exclu, et `market_snapshots` inscrit dans
`data_freshness.py` au statut le plus strict — il y est déjà (3 h, chemin
chaud).

## A5. Résultat de l'amendement — **SUCCÈS, 33/33**

**Run** : 2026-08-02 · `backtests/oi_backfill.py` · tir unique.

| | protocole initial | **amendé** |
|---|---:|---:|
| points comparables par token | 30 | **117** |
| tokens réussissant les trois critères | 16/33 — 48,5 % | **33/33 — 100,0 %** |
| écart médian, plage sur l'univers | 0,10 % à 2,94 % | **0,003 % à 0,104 %** |
| p95, plage sur l'univers | 0,49 % à 15,42 % | **0,06 % à 1,37 %** |

**L'appariement était bien l'artefact.** Passer d'un rayon de 2 h à ±5 min
divise l'écart médian par un facteur **~30**. Les deux sources ne divergeaient
pas : on comparait des relevés distants de deux heures sur une grandeur qui
bouge de 0,1 à 0,3 % par heure.

Les seuils n'ont pas bougé d'un centième — c'est la base de comparaison qui
était fausse, et elle l'était par une erreur de rédaction documentée au § 5.

### Comblage appliqué

| | |
|---|---|
| fichiers comblés | **33** |
| points ajoutés par fichier | **290** |
| point de soudure | **2026-06-15 08:00** |
| couverture après comblage | jusqu'au **2026-08-02 16:00** |
| marquage dans les données | `"src": "live"` sur chaque point ajouté |
| sauvegardes | `*_oi_4h.json.pre_backfill` (33) |
| **TON** | **intact** — 273 points, 0 ajouté, s'arrête toujours au 2026-06-15 |

Aucun point historique n'a été réécrit : le comblage est purement additif.

### Un dernier défaut du garde, corrigé

Après comblage, `data_freshness` restait au rouge : le contrôle retient le
fichier **le plus vieux** des tokens tradés — et TON, gelé **à raison**, tirait
toute la source en alarme permanente.

C'est le même défaut que celui que le statut `FROZEN` corrige au niveau de la
source, mais au niveau du **token**. TON est désormais exclu du contrôle avec
son motif inscrit dans le code. Une alarme qui hurle pour une raison légitime
finit par ne plus être lue — c'est ainsi qu'on arrive au 8ᵉ incident.

**Contrôle de fraîcheur : 0 anomalie.**
