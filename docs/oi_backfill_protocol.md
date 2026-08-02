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

**Coût mesuré de `block`** contre la référence d'avant correctif :

| fenêtre | `open` | `block` | Δ`block` | n `open`/`block` |
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
