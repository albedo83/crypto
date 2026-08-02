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

## 5. Résultat

> *À compléter après mesure. Vide à ce jour.*
