# Audit Live / Paper

Généré : 2026-09-20T15:10:32+00:00
Cohorte d'entrées : 2026-09-17T00:00:00+00:00 → 2026-09-20T12:00:00+00:00

P&L réalisé uniquement ; les positions ouvertes ne sont pas valorisées ici.

| Mesure | Valeur |
|---|---:|
| P&L Paper | +84.25 $ |
| P&L Live | +100.59 $ |
| Écart Live − Paper | +16.34 $ |
| Trades clos appariés | 3 |

## Attribution comptable de l'écart

| Composante | Live − Paper |
|---|---:|
| Taille (rendement Paper de référence) | -12.36 $ |
| Rendement sur paires closes (taille Live) | -0.66 $ |
| Trades clos sans entrée appariée | +29.36 $ |
| Paires dont au moins une position reste ouverte | +0.00 $ |

Identité vérifiée : Δ = (L−P)×rP + L×(rL−rP), puis écarts hors paires closes.
Le rendement regroupe sorties, exécution, funding et comptabilité ; ce n'est pas une attribution causale à l'IA.

## Premières sorties divergentes

| Symbole | Entrée Paper | Sortie Paper | Sortie Live | Événements IA agis liés |
|---|---|---|---|---:|

## Jalons opérationnels

| Entrées depuis | Paires closes | Effet taille | Effet rendement |
|---|---:|---:|---:|
| 2026-08-29 | 3 | -12.36 $ | -0.66 $ |
| 2026-09-06 | 3 | -12.36 $ | -0.66 $ |
| 2026-09-16T17:47:40Z | 3 | -12.36 $ | -0.66 $ |

## Limites

- Read-only local snapshots per bot, not an atomic fleet snapshot or exchange reconciliation.
- Cohort by entry date; closed P&L excludes unrealized positions and pre-cohort entries.
- Available trades ledger only; no claim of complete pre-reset coverage.
- Pairing is approximate within a 4h period; entry/exit lags are supplied.
- Return component includes exits, fills, funding and accounting; it is NOT pure slippage.
- Size decomposition uses paper return as reference; attribution order is explicit.
- Unmatched P&L is descriptive, NOT recoverable profit or proof of a selection defect.
- AI events are evidence of an intervention, not its causal portfolio P&L.
- No significance test: correlated trades and repeated regimes are not independent draws.
