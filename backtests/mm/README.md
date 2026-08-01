# Sondes Phase 0 — projet B (market making)

Scripts de mesure **strictement passifs** ayant produit `docs/mm_phase0_economics.md`
(2026-08-01). Ils ne placent **aucun ordre** : lectures publiques `/info` et
websocket public uniquement.

| script | mesure |
|---|---|
| `lat.py` | RTT REST `/info`, connexion neuve à chaque appel |
| `lat2.py` | RTT REST `/info`, connexion persistante (isole le coût TLS) |
| `ws_probe.py` | cadence `l2Book` et spreads, écoute WS |
| `ws2.py` | comparaison `bbo` / `l2Book` / `trades` — cadence et retard |
| `spread.py` | snapshot spreads + profondeur, 3 strates de volume |

Verdict rendu : STOP en Phase 0. La Phase 1 (collecteur 14 jours) n'a pas été
lancée.
