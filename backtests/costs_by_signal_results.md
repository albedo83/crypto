# Coûts réels par signal vs modèle BT — résultats (2026-07-02)

Script : `python3 -m backtests.measure_costs_by_signal` (reproductible).
Données : 168 trades à fills réels (live 56 + junior 54 + baby 34 + legacy
post-v12.9.0 24) + 54 paper, entrées alignées ≤10 min d'un close 4h.

## Verdict : écart NON matériel — le modèle de coût du BT tient. Pas de
modèle par signal, pas de re-chiffrage (doctrine premise-gate).

## (a) Slippage d'entrée (avgPx vs close de la bougie signal, + = adverse)

Modèle BT : 4 bps round-trip. Mesuré (fills réels) :

| Signal | n | mean | med | p90 |
|---|---|---|---|---|
| S10 | 21 | **−10.6** | −2.9 | +12.2 |
| S5 | 119 | +1.1 | −5.1 | +70.0 |
| S8 | 21 | +0.8 | +8.9 | +27.2 |
| S9 | 7 | +13.7 | −0.8 | +171.6 |
| ALL | 168 | **+0.1** | −2.5 | +60.8 |

- Le drift post-close est légèrement **favorable** (paper, drift pur : −9.5 bps
  moyen — les entrées mean-reversion profitent de la continuation courte).
- S10 entre à contre-drift (fade) → slippage négatif structurel.
- **S9 : seul drapeau** (+13.7 moyen porté par une queue p90 +171, n=7) —
  échantillon trop mince pour modéliser, à re-regarder à n≥20.
- **Test apparié live vs junior (43 paires, mêmes signaux)** : Δ = +0.4 bps
  ($0.48 cumulés). La latence de l'arbitre IA (appel synchrone SENIOR) et la
  taille (336 vs 139 $) ne coûtent **rien de mesurable**. L'écart brut par bot
  (+5.9 vs −7.3) = biais de composition.
- Corrobore la mesure legacy RT +0.15 bps / 119 trades
  (`backtests/measure_live_slippage.py`).

## (b) Funding réel par trade (+ = payé)

Modèle bot (ledger) : 1 bps flat. Modèle BT : intégrale horaire historique.

| Signal | n | mean bps | bps/h | note |
|---|---|---|---|---|
| S10 | 21 | +1.0 | +0.04 | |
| S5 | 119 | +1.7 | +0.10 | LONG +1.3 / SHORT +2.4 |
| S8 | 21 | **−1.3** | −0.07 | S8 **reçoit** du funding |
| S9 | 7 | −2.4 | −0.18 | |

- **L'intégrale du BT est exacte** : Δ(BT − réel) mean = 0.0 bps, p90 = 0.2,
  par signal identique à ±0.1. Rien à changer.
- Le flat 1 bps du ledger bot est dans ±3 bps de la vérité — immatériel.
- **Réponse à « S8 vit-il à crédit ? » : NON.** Ni slippage caché (+0.8 bps)
  ni funding (il encaisse −1.3 bps). L'edge S8 du BT n'est pas subventionné
  par un coût invisible.

## Limites honnêtes
- S9 n=7 : la queue (+171 p90) peut être réelle (entrées post-move ±20% =
  books volatils) — re-mesurer à n≥20 avant tout modèle.
- Mesure valide aux tailles actuelles (≤$500 notionnel). Ne teste PAS le
  plafond de slippage à capital ×20 (cf. mémoire slippage-ceiling ~$15k).
- ~~Côté exit non ventilé ici~~ → mesuré, voir section (c) ci-dessous.

## (c) Colonne SORTIE (ajout 2026-07-02, demande revue)

`python3 -m backtests.measure_costs_by_signal --exit-side` — 150 sorties
Alfred (live+junior+baby), référence mark au moment de la sortie (ticks 60s).

**(c1) Slippage d'exécution** (fill vs mark, + = payé) :

| Groupe | n | mean | med | p90 |
|---|---|---|---|---|
| stops (règles à seuil) | 117 | **+8.9** | +5.5 | +27.5 |
| timeouts | 29 | +2.7 | +1.6 | +19.2 |

⚠️ Borne SUPÉRIEURE : le mark de référence précède le fill de ≤60-180s — en
marché rapide (précisément quand les stops sortent), une partie du +8.9 est
du drift intra-tick, pas du spread. Le vrai coût d'exécution est entre +2.7
(timeouts, marché calme = plancher) et +8.9.

**(c2) Overshoot de niveau** (gross réel − niveau de déclenchement, − = au-delà) :

| Règle | n | mean | med |
|---|---|---|---|
| catastrophe_stop | 6 | **−117** | −132 |
| s10_trailing | 3 | −13 | −13 |
| prop_trail (niveau reconstruit) | 62 | +2.4 | +12.9 |

**Bilan coûts complet** : entrée +0.1 (BT charge 4 → conservateur) ; sortie
exécution +3 à +9 (BT charge 0 explicite côté sortie mais les 4 bps RT
couvrent partiellement) ; overshoot catastrophe ~−120 bps × ~4 % des trades
≈ 5 bps/trade équivalent — LE poste non modélisé, désormais **borné à ~−200**
par le filet hard-stop (v1.7.1). Net-net : le modèle 13 bps du BT est
approximativement juste, légèrement optimiste sur les sorties au stop en
marché rapide. Dossier coûts clos.

## (d) Stress S9 (+30/+50 bps d'entrée adverse) — luciole confirmée

`python3 -m backtests.stress_s9_entry_cost` (hook `entry_slip_bps_by_strat`
dans run_window — décale le prix d'entrée, propage au stop-hit). À +50 bps
sur TOUTES les entrées S9 (3.6× la moyenne mesurée, pire que la queue) :
S9 reste net-positif sur 28m/12m/6m (−13 à −23 % relatif), impact total
−28 pp sur +881 % (28m), **stop-hits INCHANGÉS 4 fenêtres** (48/19/10/6),
DD intact. Le drapeau n=7 est de la comptabilité — re-mesure à n≥20, pas de
modèle. (3m : S9 déjà négatif en base, régime bear courant.)
