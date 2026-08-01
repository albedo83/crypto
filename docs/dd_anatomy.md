# Anatomie du drawdown −51,4 %

> **Diagnostic pur — aucun paramètre n'a été touché.** Ce document décrit le pire drawdown de la configuration **en service**, sur la base corrigée. Il ne conclut à aucune action.

**Généré le** : 2026-08-01T14:55Z  
**Empreinte** : config `d7a415020619` · git `6dd46b2+dirty` · données jusqu'au 2026-08-01T12:00 (36 symboles, fichiers 2026-08-01T12:10Z)  
**Fenêtre** : 2024-04-01 → 2026-08-01 (28 mois), capital de départ $1,000  
**Sémantique** : ALIGNED · booking réaliste des trails · cap notionnel proportionnel · `signal_mult` = {'S1': 1.0, 'S10': 2.0, 'S5': 3.0, 'S8': 1.25, 'S9': 2.0}

**Contrôle** : DD reconstruit -51.39 % vs DD moteur -51.39 % — écart 0.0000 pp · 1308 trades.


## 1. L'épisode

| | |
|---|---|
| pic | **2024-08-03** — equity $1,900 |
| creux | **2024-11-06** — equity $923 |
| amplitude | **-51.4 %** (−$976) |
| descente | **95 jours** |
| récupération | **28 jours** — pic repris le 2024-12-04 |
| immersion totale | **123 jours** sous le pic |

## 2. Qui a produit ce drawdown

Sur les **140 trades** clôturés entre le pic et le creux (P&L net **$-897**) :

| signal | n | P&L | WR |
|---|---:|---:|---:|
| S5 | 74 | $-505 | 41 % |
| S8 | 11 | $-294 | 9 % |
| S10 | 43 | $-225 | 51 % |
| S9 | 12 | $128 | 58 % |

### Par direction

| signal · direction | n | P&L |
|---|---:|---:|
| S5 LONG | 58 | $-318 |
| S8 LONG | 11 | $-294 |
| S10 SHORT | 43 | $-225 |
| S5 SHORT | 16 | $-187 |
| S9 SHORT | 10 | $20 |
| S9 LONG | 2 | $107 |

### Par mois

| mois | n | P&L |
|---|---:|---:|
| 2024-08 | 56 | $-441 |
| 2024-09 | 42 | $-55 |
| 2024-10 | 37 | $-296 |
| 2024-11 | 5 | $-105 |

## 3. Les 3 pires fenêtres de 30 jours — références du Projet A

> Non chevauchantes. **Ces trois fenêtres, plus celle du drawdown maximal, constituent les « fenêtres de creux » sur lesquelles la clause C2 du Projet A sera évaluée.**

| # | début | fin | P&L | % de l'equity | trades |
|---:|---|---|---:|---:|---:|
| 1 | 2024-09-22 | 2024-10-21 | $-419 | **-29.5 %** | 41 |
| 2 | 2024-08-03 | 2024-09-01 | $-477 | **-26.2 %** | 57 |
| 3 | 2025-07-21 | 2025-08-19 | $-1,209 | **-25.2 %** | 41 |

Fenêtre du drawdown maximal : **2024-08-03 → 2024-11-06**.


## 4. Monte Carlo — le drawdown tient-il à l'ordre d'arrivée ?

10 000 réordonnancements des 1308 trades (rendements recomposés, graine `20260801`).

| quantile de sévérité | DD max simulé |
|---|---:|
| P50 (médian) | -37.2 % |
| P75 | -42.7 % |
| P90 | -48.1 % |
| P95 | -51.8 % |
| P99 | -58.7 % |
| pire tirage | -70.1 % |
| meilleur tirage | -20.3 % |

DD **observé** : -51.4 % — **5.5 %** des réordonnancements font pire.

**Lecture.** Un DD observé proche du médian signifie que l'ordre réel n'a rien eu d'exceptionnel : le drawdown est une propriété de la **population de trades**, pas de la malchance de séquence. Un DD observé au-delà du P95 signifierait l'inverse. Dans les deux cas la queue simulée (P95/P99) est le chiffre à retenir comme pire cas plausible d'une re-exécution — et elle reste un **plancher** optimiste, puisque la population de trades elle-même est tenue fixe.


## 5. Note de lecture — sensibilité de la date de départ

Le capital final de ce run est **$12,872**, contre $15 463 publiés dans `docs/backtests.md` le 2026-07-31. Même configuration, même code : **la fenêtre a glissé d'un jour aux deux bouts** (2024-03-31→2026-07-31 devient 2024-04-01→2026-08-01, les fichiers de données étant rafraîchis toutes les 4 h). 1308 trades contre 1309 : **un seul trade d'écart**, et le compounding l'amplifie pendant 28 mois.

Le **drawdown**, lui, est identique au centième — l'épisode est précoce (août-novembre 2024), donc antérieur à la divergence des chemins. C'est la raison pour laquelle ce document mesure un risque et pas un rendement : sur 28 mois de compounding, le rendement final d'un backtest est un nombre fragile, le drawdown précoce ne l'est pas.


## 6. Série de référence

`data/alfred_daily_pnl.csv` — P&L quotidien UTC, jours sans trade inclus, sur toute la fenêtre. Colonnes : `date, n_trades, pnl_usd, equity, ret_pct, dd_pct`. C'est la série contre laquelle se mesurera la corrélation C1 du Projet A.

