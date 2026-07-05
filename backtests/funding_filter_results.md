# Funding-aware entry filter (S5/S9) — premise-EDA : FAIL, chantier classé (2026-07-05)

Script : `python3 -m backtests.eda_funding_filter`. Données : 1351/1352 trades
BT 28m (dump ablation) × funding horaire HL 2023-05→2026-07 (37 symboles).
Feature : `crowding_bps_h = −dir × funding_rate × 1e4` (positif = mon côté
paie la prime = côté crowdé). Hypothèse (revue 07-05) : fader en étant du
côté crowdé = file du squeeze → gate/haircut S5/S9.

## Critère PASS pré-enregistré (avant lecture des chiffres)

Relation monotone sur quartiles OU bucket toxique (net < 0, n ≥ 30) sur 28m,
signe cohérent sur 12m. **Aucune des 4 cibles ne passe.**

## Résultats par cible (crowding moy. 24h, quartiles 28m)

| Cible | n | Pattern | rho(crowding, net) | Verdict |
|---|---|---|---|---|
| S5 LONG | 398 | Q1 +117 → Q4 +71 bps, tous positifs, non monotone | −0.046 | FAIL |
| S5 SHORT | 200 | tous buckets +91 à +297 bps, zigzag | +0.090 | FAIL |
| S9 LONG | 28 | **signe INVERSE** (Q1 anti-crowdé = pire, −112) — n=7/bucket | +0.490 (bruit) | FAIL |
| S9 SHORT | 114 | Q4 crowdé −155 bps (n=29 < 30)… **+86 bps sur 12m** (signe flippé) | −0.028 | FAIL |

Le seul candidat apparent (S9 SHORT Q4 toxique sur 28m) a le signe inversé
sur les 12 derniers mois et une corrélation de rang nulle : bruit
régime-local, pas une mécanique. Contrôles S8/S10 : pas de pattern non plus.

## La découverte utile : le funding HL est ÉPINGLÉ au taux de base

Les bornes de quartiles dégénèrent massivement à **±0.125 bps/h** = le
funding par défaut HL (0.01 %/8h). En instantané, Q2/Q3 sont souvent VIDES
(égalités massives au taux de base). Autrement dit : à l'heure où nos fades
4h entrent, le funding du token est la plupart du temps à sa valeur par
défaut — la feature n'a presque pas de variance à offrir. C'est probablement
la raison structurelle de l'échec : sur HL, le funding extrême est trop rare
et trop bref pour conditionner des entrées 4h. (Cohérent avec
`eda_funding_persistence.py` 2026-05 : la persistance du funding est faible.)

## Décision

- **Pas de sweep** (premise-gate : compute non brûlé sur prémisse fausse).
- **Pas de hook** dans backtest_rolling (rien à tester).
- Le filtre funding S5/S9 rejoint disp_7d gate, regime filter, slow-bleed cut
  au cimetière des idées séduisantes tuées par les données. Le carry
  standalone était déjà mort (S11, fee floor). Il reste au BACKLOG les pistes
  reviewer non testées : OI quadrants (ΔOI × Δprix), premium z-score,
  calendrier macro, DVOL, momentum cross-sectionnel élargi.
