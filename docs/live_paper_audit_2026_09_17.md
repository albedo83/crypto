# Audit Live / Paper

Généré : 2026-09-17T09:17:37+00:00
Cohorte d'entrées : 2026-07-09T14:02:44+00:00 → 2026-09-17T08:10:01+00:00

P&L réalisé uniquement ; les positions ouvertes ne sont pas valorisées ici.

| Mesure | Valeur |
|---|---:|
| P&L Paper | +61.17 $ |
| P&L Live | -20.56 $ |
| Écart Live − Paper | -81.73 $ |
| Trades clos appariés | 79 |

## Attribution comptable de l'écart

| Composante | Live − Paper |
|---|---:|
| Taille (rendement Paper de référence) | -25.35 $ |
| Rendement sur paires closes (taille Live) | -9.88 $ |
| Trades clos sans entrée appariée | -46.50 $ |
| Paires dont au moins une position reste ouverte | +0.00 $ |

Identité vérifiée : Δ = (L−P)×rP + L×(rL−rP), puis écarts hors paires closes.
Le rendement regroupe sorties, exécution, funding et comptabilité ; ce n'est pas une attribution causale à l'IA.

## Premières sorties divergentes

| Symbole | Entrée Paper | Sortie Paper | Sortie Live | Événements IA agis liés |
|---|---|---|---|---:|
| ARB S5 | 2026-07-09T20:03:16.819337+00:00 | prop_trail · 2026-07-10T16:00:06.070205+00:00 | manual_stop_set · 2026-07-10T09:54:45.213375+00:00 | 5 |
| OP S5 | 2026-07-16T12:03:13.733982+00:00 | prop_trail · 2026-07-17T12:00:13.328338+00:00 | manual_stop_set · 2026-07-17T10:08:33.060509+00:00 | 3 |
| BLUR S10 | 2026-07-18T04:03:17.068586+00:00 | timeout · 2026-07-19T04:03:20.696706+00:00 | manual_stop_set · 2026-07-18T20:14:39.575444+00:00 | 3 |
| ARB S10 | 2026-07-19T20:03:10.834662+00:00 | timeout · 2026-07-20T20:03:12.813711+00:00 | manual_stop_set · 2026-07-20T09:24:11.299862+00:00 | 3 |
| PYTH S10 | 2026-07-20T16:03:13.795928+00:00 | timeout · 2026-07-21T16:03:22.183815+00:00 | manual_stop_set · 2026-07-21T03:00:57.149475+00:00 | 4 |
| GMX S5 | 2026-07-23T16:03:16.105902+00:00 | prop_trail · 2026-07-24T16:00:15.104328+00:00 | manual_stop_set · 2026-07-24T12:57:14.670454+00:00 | 6 |
| STX S5 | 2026-07-24T08:03:15.620489+00:00 | timeout · 2026-07-26T08:03:25.027448+00:00 | manual_stop_set · 2026-07-25T07:10:17.276042+00:00 | 6 |
| UNI S5 | 2026-07-29T12:03:09.622204+00:00 | timeout · 2026-07-31T12:03:13.495879+00:00 | manual_stop_set · 2026-07-31T03:06:07.063282+00:00 | 9 |
| ADA S5 | 2026-08-02T08:03:08.816449+00:00 | timeout · 2026-08-04T08:03:16.394963+00:00 | manual_stop_set · 2026-08-03T14:45:51.759763+00:00 | 2 |
| COMP S5 | 2026-08-18T00:03:17.179297+00:00 | timeout · 2026-08-20T00:03:26.431285+00:00 | manual_stop_set · 2026-08-19T05:47:02.013716+00:00 | 2 |
| GALA S5 | 2026-08-18T20:03:26.353337+00:00 | catastrophe_stop · 2026-08-20T15:32:10.790172+00:00 | manual_stop_set · 2026-08-19T11:33:42.819460+00:00 | 3 |
| LINK S5 | 2026-08-19T04:03:03.276319+00:00 | timeout · 2026-08-21T04:03:14.535774+00:00 | manual_stop_set · 2026-08-20T01:09:06.587045+00:00 | 5 |

## Jalons opérationnels

| Entrées depuis | Paires closes | Effet taille | Effet rendement |
|---|---:|---:|---:|
| 2026-08-29 | 7 | -7.07 $ | +0.62 $ |
| 2026-09-06 | 3 | -6.91 $ | +0.12 $ |
| 2026-09-16T17:47:40Z | 0 | +0.00 $ | +0.00 $ |

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
