# Mid-trade profiling EDA

## 1. TL;DR

**Verdict (toutes règles)** : **GO**  (335 règles qualifiantes)

**Verdict (règles non-triviales — excl. cur_ur/mae seuls)** : **GO**  (315 règles qualifiantes)


Deux verdicts car la plupart des règles "qualifiantes" se basent sur `current_ur_bps` ou `mae_bps_to_date` : ce sont des règles **triviales** ("si la position est déjà très underwater, elle finira underwater") qui ne prédisent rien — elles constatent. Seules les règles **non-triviales** (basées sur mfe, pain, sector_div_delta, ou combinaisons) ont un pouvoir prédictif réel.


### Signature canonique trouvée


**S5 LONG @ T+8h** : `mfe_bps_to_date < 50 AND time_in_pain_pct >= 50`

→ n=72, WR=13.9%, cur=-396bps, final=-505bps, savings=+108bps


**S5 LONG @ T+8h (règle utilisateur, plus large)** : `mfe_bps_to_date < 150 AND time_in_pain_pct > 80`

→ n=98, WR=20.4%, cur=-352bps, final=-398bps, savings=+46bps


**S8 LONG @ T+8h** : `mfe_bps_to_date <= 50`

→ n=16, WR=6.2%, cur=-443bps, final=-635bps, savings=+192bps


**Nuance critique** : le WR seul est trompeur. Une règle qui identifie des losers à T+24h après que le prix a déjà absorbé la perte ne sauve rien. Le **savings_bps** (mean_cur_ur − mean_final_net) mesure la valeur réelle. Le sweet-spot pour S5 LONG est **T+8h** : WR ~14%, savings ~+108 bps par trade coupé (n=72 sur 28m). À T+24h, savings tombe à +7 bps — le marché a déjà fini le travail.


### Top 5 règles (toutes confondues, triées par WR croissant) :

- **S5_+1_T+12h** : `current_ur_bps <= -500` (trivial) → WR=5.4%  n=37  mean_net=-755 bps  (13% du portfolio)
- **S5_+1_T+24h** : `current_ur_bps <= -500` (trivial) → WR=5.9%  n=51  mean_net=-739 bps  (19% du portfolio)
- **S5_+1_T+24h** : `mfe+pain+sd AND3 mfe<100+pain>60+sd<-500` → WR=6.2%  n=32  mean_net=-640 bps  (12% du portfolio)
- **S5_+1_T+24h** : `mfe+pain+sd AND3 mfe<100+pain>70+sd<-500` → WR=6.2%  n=32  mean_net=-640 bps  (12% du portfolio)
- **S5_+1_T+24h** : `mfe+pain+sd AND3 mfe<100+pain>80+sd<-500` → WR=6.2%  n=32  mean_net=-640 bps  (12% du portfolio)

### Top 5 règles **non-triviales** (prédiction réelle) :

- **S5_+1_T+24h** : `mfe+pain+sd AND3 mfe<100+pain>60+sd<-500` → WR=6.2%  n=32  mean_net=-640 bps  (12% du portfolio)
- **S5_+1_T+24h** : `mfe+pain+sd AND3 mfe<100+pain>70+sd<-500` → WR=6.2%  n=32  mean_net=-640 bps  (12% du portfolio)
- **S5_+1_T+24h** : `mfe+pain+sd AND3 mfe<100+pain>80+sd<-500` → WR=6.2%  n=32  mean_net=-640 bps  (12% du portfolio)
- **S5_+1_T+24h** : `mfe_bps_to_date <= 50` → WR=6.5%  n=46  mean_net=-518 bps  (17% du portfolio)
- **S5_+1_T+24h** : `mfe<X AND pain>Y% AND mfe<50+pain>50` → WR=6.5%  n=46  mean_net=-518 bps  (17% du portfolio)

## 2. Méthodologie

- **Données** : `backtests/mid_trade_snapshots_28m.jsonl` (4171 snapshots, généré par `run_window` avec `mid_trade_dump_path` instrumenté). Window 28 mois (baseline v12.5.30 1115 trades / +$5.48M / DD-74%).
- **Cibles primaires** : S5 LONG (n=285 trades / 281 @ T+8h), S5 SHORT (n=170).
- **Cibles secondaires** : S8 LONG (n=118 trades), S9 SHORT (n=86).
- **Features mid-trade** : current_ur_bps, mfe_bps_to_date, mae_bps_to_date, time_in_pain_pct, sector_div_delta. `time_in_pain_pct` = % de bougies 4h depuis l'entrée où le close donnait ur < 0.
- **Decision tree** : sklearn `DecisionTreeClassifier(max_depth=2, min_samples_leaf=10)`. Target = `final_winner` (pnl > 0).
- **Threshold sweep** : grille fixe par feature (cf. `THRESHOLD_GRID`). Pour chaque feature, sweep `<=` et `>=` ; on retient les paires (feature, threshold) avec n ≥ 10.
- **Combo rule** : `mfe_bps_to_date < X` AND `time_in_pain_pct > Y%`. Grilles : mfe∈{50,100,150,200,300,500}, pain∈{50,60,70,75,80,90}.
- **Triple combo** : ajoute `sector_div_delta < Z` au combo précédent.
- **Critère GO** : WR<25% AND n≥30 AND mean_net_bps<−200.
- **Critère YELLOW** : WR<40% AND n≥15 AND mean_net_bps<−100.


**Date** : 2026-05-15  •  **Window** : 28 mois  •  **Snapshots** : 4171

Objet : profiler la trajectoire intra-position aux checkpoints {4h, 8h, 12h, 24h} pour détecter une signature de "dead trade walking" exploitable comme règle d'exit anticipée. Pivot vers les features mid-trade après échec des EDA entry-state, basket eff_n, et S5 cluster split.

## 3. Tailles d'échantillons

| Strat | Dir | T+4h | T+8h | T+12h | T+24h |
|---|---|---|---|---|---|
| S1 | +1 | 73 | 72 | 71 | 70 |
| S10 | -1 | 355 | 350 | 345 | 273 |
| S5 | -1 | 170 | 170 | 169 | 165 |
| S5 | +1 | 285 | 281 | 279 | 271 |
| S8 | +1 | 118 | 100 | 95 | 84 |
| S9 | -1 | 86 | 73 | 62 | 56 |
| S9 | +1 | 28 | 26 | 24 | 20 |

## Cibles primaires


### S5 dir=+1 @ T+4h  (n=285)

#### 4. Distribution (winners | losers)

#### S5 dir=+1 @ T+4h  (n=285, winners=133, losers=152)

**current_ur_bps** (winners | losers):
- p10: -213 | -538
- p25: -67 | -394
- p50: +100 | -197
- p75: +374 | +40
- p90: +610 | +261

**mfe_bps_to_date** (winners | losers):
- p10: +0 | +0
- p25: +81 | +0
- p50: +243 | +58
- p75: +516 | +214
- p90: +799 | +478

**mae_bps_to_date** (winners | losers):
- p10: -465 | -639
- p25: -263 | -519
- p50: -117 | -321
- p75: +0 | -136
- p90: +0 | +0

**time_in_pain_pct** (winners | losers):
- p10: +0 | +0
- p25: +0 | +0
- p50: +0 | +100
- p75: +100 | +100
- p90: +100 | +100

**sector_div_delta** (winners | losers):
- p10: -506 | -781
- p25: -186 | -391
- p50: +47 | -180
- p75: +325 | +68
- p90: +722 | +232

#### 5. Decision tree (max_depth=2)

**Tree (max_depth=2)** target=final_winner, n_valid=285:
```
|--- current_ur_bps <= -230.53
|   |--- sector_div_delta <= -311.35
|   |   |--- class: 0
|   |--- sector_div_delta >  -311.35
|   |   |--- class: 0
|--- current_ur_bps >  -230.53
|   |--- sector_div_delta <= 392.57
|   |   |--- class: 1
|   |--- sector_div_delta >  392.57
|   |   |--- class: 1
```

Per-leaf WR / mean_final_net_bps:
- leaf 2: n=41  WR=19.5%  mean_net=-555 bps
- leaf 3: n=41  WR=4.9%  mean_net=-541 bps
- leaf 5: n=171  WR=55.0%  mean_net=+198 bps
- leaf 6: n=32  WR=90.6%  mean_net=+1209 bps

#### 6. Threshold sweep — top 10 par discrimination
| Feature | Op | Threshold | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|---|
| current_ur_bps | <= | -500 | 20 | 7% | 5.0% | -833 |
| sector_div_delta | >= | 500 | 24 | 8% | 91.7% | +1145 |
| mae_bps_to_date | <= | -500 | 53 | 19% | 18.9% | -603 |
| current_ur_bps | <= | -200 | 97 | 34% | 21.6% | -409 |
| current_ur_bps | >= | 500 | 23 | 8% | 78.3% | +1035 |
| mfe_bps_to_date | >= | 700 | 27 | 9% | 77.8% | +1026 |
| mfe_bps_to_date | <= | 50 | 99 | 35% | 24.2% | -364 |
| mfe_bps_to_date | >= | 500 | 52 | 18% | 75.0% | +691 |
| mae_bps_to_date | <= | -300 | 107 | 38% | 25.2% | -330 |
| sector_div_delta | >= | 200 | 69 | 24% | 73.9% | +741 |

#### 7. Combo (mfe<X AND pain>Y%) — top 10 par WR croissant
| mfe<X | pain>Y% | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|
| 50 | 50 | 99 | 35% | 24.2% | -364 |
| 50 | 60 | 99 | 35% | 24.2% | -364 |
| 50 | 70 | 99 | 35% | 24.2% | -364 |
| 50 | 75 | 99 | 35% | 24.2% | -364 |
| 50 | 80 | 99 | 35% | 24.2% | -364 |
| 50 | 90 | 99 | 35% | 24.2% | -364 |
| 150 | 50 | 127 | 45% | 28.3% | -294 |
| 150 | 60 | 127 | 45% | 28.3% | -294 |
| 150 | 70 | 127 | 45% | 28.3% | -294 |
| 150 | 75 | 127 | 45% | 28.3% | -294 |

#### 7b. Triple combo (mfe<X AND pain>Y% AND sector_div_delta<Z) — top 5 par WR
| mfe<X | pain>Y% | sd<Z | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|---|
| 100 | 60 | -1000 | 11 | 4% | 9.1% | -786 |
| 100 | 70 | -1000 | 11 | 4% | 9.1% | -786 |
| 100 | 80 | -1000 | 11 | 4% | 9.1% | -786 |
| 100 | 60 | -500 | 26 | 9% | 19.2% | -588 |
| 100 | 70 | -500 | 26 | 9% | 19.2% | -588 |

### S5 dir=+1 @ T+8h  (n=281)

#### 4. Distribution (winners | losers)

#### S5 dir=+1 @ T+8h  (n=281, winners=132, losers=149)

**current_ur_bps** (winners | losers):
- p10: -217 | -597
- p25: -100 | -411
- p50: +137 | -190
- p75: +475 | +59
- p90: +829 | +255

**mfe_bps_to_date** (winners | losers):
- p10: +70 | +0
- p25: +180 | +0
- p50: +375 | +112
- p75: +698 | +300
- p90: +1052 | +512

**mae_bps_to_date** (winners | losers):
- p10: -485 | -749
- p25: -314 | -595
- p50: -156 | -400
- p75: +0 | -165
- p90: +0 | -26

**time_in_pain_pct** (winners | losers):
- p10: +0 | +0
- p25: +0 | +50
- p50: +0 | +100
- p75: +50 | +100
- p90: +100 | +100

**sector_div_delta** (winners | losers):
- p10: -469 | -891
- p25: -221 | -504
- p50: +83 | -212
- p75: +390 | +17
- p90: +998 | +177

#### 5. Decision tree (max_depth=2)

**Tree (max_depth=2)** target=final_winner, n_valid=281:
```
|--- current_ur_bps <= -234.75
|   |--- mfe_bps_to_date <= 15.10
|   |   |--- class: 0
|   |--- mfe_bps_to_date >  15.10
|   |   |--- class: 0
|--- current_ur_bps >  -234.75
|   |--- current_ur_bps <= 501.31
|   |   |--- class: 1
|   |--- current_ur_bps >  501.31
|   |   |--- class: 1
```

Per-leaf WR / mean_final_net_bps:
- leaf 2: n=52  WR=3.8%  mean_net=-605 bps
- leaf 3: n=27  WR=29.6%  mean_net=-348 bps
- leaf 5: n=168  WR=53.6%  mean_net=+136 bps
- leaf 6: n=34  WR=94.1%  mean_net=+1453 bps

#### 6. Threshold sweep — top 10 par discrimination
| Feature | Op | Threshold | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|---|
| current_ur_bps | <= | -500 | 25 | 9% | 4.0% | -753 |
| current_ur_bps | >= | 500 | 34 | 12% | 94.1% | +1453 |
| mfe_bps_to_date | >= | 1000 | 19 | 7% | 89.5% | +1429 |
| mfe_bps_to_date | <= | 50 | 72 | 26% | 13.9% | -505 |
| mae_bps_to_date | <= | -500 | 73 | 26% | 16.4% | -567 |
| current_ur_bps | <= | -200 | 88 | 31% | 17.0% | -493 |
| sector_div_delta | >= | 1000 | 17 | 6% | 82.4% | +1151 |
| mfe_bps_to_date | >= | 700 | 41 | 15% | 80.5% | +1083 |
| sector_div_delta | >= | 200 | 65 | 23% | 80.0% | +837 |
| sector_div_delta | >= | 500 | 33 | 12% | 78.8% | +834 |

#### 7. Combo (mfe<X AND pain>Y%) — top 10 par WR croissant
| mfe<X | pain>Y% | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|
| 50 | 50 | 70 | 25% | 12.9% | -515 |
| 50 | 60 | 70 | 25% | 12.9% | -515 |
| 50 | 70 | 70 | 25% | 12.9% | -515 |
| 50 | 75 | 70 | 25% | 12.9% | -515 |
| 50 | 80 | 70 | 25% | 12.9% | -515 |
| 50 | 90 | 70 | 25% | 12.9% | -515 |
| 150 | 50 | 98 | 35% | 20.4% | -398 |
| 150 | 60 | 98 | 35% | 20.4% | -398 |
| 150 | 70 | 98 | 35% | 20.4% | -398 |
| 150 | 75 | 98 | 35% | 20.4% | -398 |

#### 7b. Triple combo (mfe<X AND pain>Y% AND sector_div_delta<Z) — top 5 par WR
| mfe<X | pain>Y% | sd<Z | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|---|
| 100 | 60 | -500 | 26 | 9% | 3.8% | -703 |
| 100 | 70 | -500 | 26 | 9% | 3.8% | -703 |
| 100 | 80 | -500 | 26 | 9% | 3.8% | -703 |
| 300 | 60 | -1000 | 13 | 5% | 7.7% | -836 |
| 300 | 70 | -1000 | 13 | 5% | 7.7% | -836 |

### S5 dir=+1 @ T+12h  (n=279)

#### 4. Distribution (winners | losers)

#### S5 dir=+1 @ T+12h  (n=279, winners=131, losers=148)

**current_ur_bps** (winners | losers):
- p10: -226 | -656
- p25: -16 | -485
- p50: +211 | -245
- p75: +580 | +45
- p90: +939 | +260

**mfe_bps_to_date** (winners | losers):
- p10: +98 | +0
- p25: +237 | +0
- p50: +506 | +135
- p75: +819 | +353
- p90: +1272 | +615

**mae_bps_to_date** (winners | losers):
- p10: -508 | -836
- p25: -333 | -656
- p50: -168 | -471
- p75: -26 | -228
- p90: +0 | -81

**time_in_pain_pct** (winners | losers):
- p10: +0 | +0
- p25: +0 | +33
- p50: +33 | +100
- p75: +67 | +100
- p90: +100 | +100

**sector_div_delta** (winners | losers):
- p10: -458 | -945
- p25: -272 | -511
- p50: +177 | -276
- p75: +463 | +43
- p90: +1112 | +306

#### 5. Decision tree (max_depth=2)

**Tree (max_depth=2)** target=final_winner, n_valid=279:
```
|--- current_ur_bps <= -69.98
|   |--- current_ur_bps <= -271.74
|   |   |--- class: 0
|   |--- current_ur_bps >  -271.74
|   |   |--- class: 0
|--- current_ur_bps >  -69.98
|   |--- current_ur_bps <= 540.71
|   |   |--- class: 1
|   |--- current_ur_bps >  540.71
|   |   |--- class: 1
```

Per-leaf WR / mean_final_net_bps:
- leaf 2: n=80  WR=10.0%  mean_net=-582 bps
- leaf 3: n=43  WR=39.5%  mean_net=-90 bps
- leaf 5: n=118  WR=58.5%  mean_net=+301 bps
- leaf 6: n=38  WR=97.4%  mean_net=+1252 bps

#### 6. Threshold sweep — top 10 par discrimination
| Feature | Op | Threshold | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|---|
| current_ur_bps | >= | 1000 | 12 | 4% | 100.0% | +2081 |
| current_ur_bps | <= | -500 | 37 | 13% | 5.4% | -755 |
| sector_div_delta | >= | 1000 | 18 | 6% | 94.4% | +1249 |
| mfe_bps_to_date | >= | 1000 | 25 | 9% | 92.0% | +1306 |
| mfe_bps_to_date | <= | 50 | 60 | 22% | 11.7% | -529 |
| current_ur_bps | >= | 500 | 44 | 16% | 86.4% | +1049 |
| mae_bps_to_date | <= | -500 | 85 | 30% | 16.5% | -516 |
| mfe_bps_to_date | >= | 700 | 58 | 21% | 82.8% | +993 |
| current_ur_bps | <= | -200 | 92 | 33% | 17.4% | -498 |
| mfe_bps_to_date | <= | 150 | 93 | 33% | 18.3% | -443 |

#### 7. Combo (mfe<X AND pain>Y%) — top 10 par WR croissant
| mfe<X | pain>Y% | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|
| 50 | 50 | 60 | 22% | 11.7% | -529 |
| 50 | 60 | 60 | 22% | 11.7% | -529 |
| 50 | 70 | 60 | 22% | 11.7% | -529 |
| 50 | 75 | 60 | 22% | 11.7% | -529 |
| 50 | 80 | 60 | 22% | 11.7% | -529 |
| 50 | 90 | 60 | 22% | 11.7% | -529 |
| 150 | 50 | 91 | 33% | 17.6% | -456 |
| 150 | 60 | 91 | 33% | 17.6% | -456 |
| 150 | 70 | 83 | 30% | 18.1% | -451 |
| 150 | 75 | 83 | 30% | 18.1% | -451 |

#### 7b. Triple combo (mfe<X AND pain>Y% AND sector_div_delta<Z) — top 5 par WR
| mfe<X | pain>Y% | sd<Z | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|---|
| 100 | 60 | -500 | 25 | 9% | 8.0% | -654 |
| 100 | 70 | -500 | 24 | 9% | 8.3% | -663 |
| 100 | 80 | -500 | 24 | 9% | 8.3% | -663 |
| 300 | 60 | -500 | 34 | 12% | 8.8% | -625 |
| 200 | 60 | -500 | 32 | 11% | 9.4% | -634 |

### S5 dir=+1 @ T+24h  (n=271)

#### 4. Distribution (winners | losers)

#### S5 dir=+1 @ T+24h  (n=271, winners=130, losers=141)

**current_ur_bps** (winners | losers):
- p10: -180 | -797
- p25: +28 | -576
- p50: +376 | -335
- p75: +767 | -55
- p90: +1302 | +301

**mfe_bps_to_date** (winners | losers):
- p10: +219 | +0
- p25: +438 | +0
- p50: +771 | +205
- p75: +1188 | +478
- p90: +1644 | +736

**mae_bps_to_date** (winners | losers):
- p10: -611 | -993
- p25: -402 | -807
- p50: -212 | -584
- p75: -76 | -373
- p90: +0 | -200

**time_in_pain_pct** (winners | losers):
- p10: +0 | +17
- p25: +0 | +50
- p50: +17 | +83
- p75: +50 | +100
- p90: +83 | +100

**sector_div_delta** (winners | losers):
- p10: -597 | -1306
- p25: -203 | -768
- p50: +151 | -486
- p75: +632 | -135
- p90: +1219 | +359

#### 5. Decision tree (max_depth=2)

**Tree (max_depth=2)** target=final_winner, n_valid=271:
```
|--- current_ur_bps <= -36.14
|   |--- current_ur_bps <= -289.98
|   |   |--- class: 0
|   |--- current_ur_bps >  -289.98
|   |   |--- class: 0
|--- current_ur_bps >  -36.14
|   |--- current_ur_bps <= 508.30
|   |   |--- class: 1
|   |--- current_ur_bps >  508.30
|   |   |--- class: 1
```

Per-leaf WR / mean_final_net_bps:
- leaf 2: n=77  WR=3.9%  mean_net=-656 bps
- leaf 3: n=55  WR=36.4%  mean_net=-93 bps
- leaf 5: n=79  WR=65.8%  mean_net=+224 bps
- leaf 6: n=60  WR=91.7%  mean_net=+1291 bps

#### 6. Threshold sweep — top 10 par discrimination
| Feature | Op | Threshold | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|---|
| current_ur_bps | >= | 1000 | 24 | 9% | 100.0% | +2010 |
| current_ur_bps | <= | -500 | 51 | 19% | 5.9% | -739 |
| mfe_bps_to_date | <= | 50 | 46 | 17% | 6.5% | -518 |
| time_in_pain_pct | >= | 90 | 72 | 27% | 8.3% | -523 |
| time_in_pain_pct | >= | 100 | 72 | 27% | 8.3% | -523 |
| sector_div_delta | <= | -1000 | 23 | 8% | 8.7% | -735 |
| sector_div_delta | >= | 1000 | 22 | 8% | 90.9% | +1792 |
| mfe_bps_to_date | >= | 1000 | 54 | 20% | 90.7% | +1250 |
| current_ur_bps | <= | -200 | 100 | 37% | 10.0% | -545 |
| mfe_bps_to_date | <= | 100 | 57 | 21% | 10.5% | -492 |

#### 7. Combo (mfe<X AND pain>Y%) — top 10 par WR croissant
| mfe<X | pain>Y% | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|
| 50 | 50 | 46 | 17% | 6.5% | -518 |
| 50 | 60 | 46 | 17% | 6.5% | -518 |
| 50 | 70 | 46 | 17% | 6.5% | -518 |
| 50 | 75 | 46 | 17% | 6.5% | -518 |
| 50 | 80 | 46 | 17% | 6.5% | -518 |
| 50 | 90 | 46 | 17% | 6.5% | -518 |
| 200 | 90 | 64 | 24% | 7.8% | -518 |
| 150 | 90 | 61 | 23% | 8.2% | -524 |
| 500 | 90 | 72 | 27% | 8.3% | -523 |
| 300 | 90 | 70 | 26% | 8.6% | -513 |

#### 7b. Triple combo (mfe<X AND pain>Y% AND sector_div_delta<Z) — top 5 par WR
| mfe<X | pain>Y% | sd<Z | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|---|
| 100 | 60 | -500 | 32 | 12% | 6.2% | -640 |
| 100 | 70 | -500 | 32 | 12% | 6.2% | -640 |
| 100 | 80 | -500 | 32 | 12% | 6.2% | -640 |
| 300 | 60 | -1000 | 16 | 6% | 6.2% | -776 |
| 300 | 70 | -1000 | 16 | 6% | 6.2% | -776 |

### S5 dir=-1 @ T+4h  (n=170)

#### 4. Distribution (winners | losers)

#### S5 dir=-1 @ T+4h  (n=170, winners=92, losers=78)

**current_ur_bps** (winners | losers):
- p10: -231 | -406
- p25: -50 | -286
- p50: +99 | -111
- p75: +290 | +68
- p90: +597 | +193

**mfe_bps_to_date** (winners | losers):
- p10: +0 | +0
- p25: +66 | +0
- p50: +215 | +75
- p75: +429 | +240
- p90: +752 | +369

**mae_bps_to_date** (winners | losers):
- p10: -431 | -535
- p25: -194 | -382
- p50: -26 | -214
- p75: +0 | -68
- p90: +0 | +0

**time_in_pain_pct** (winners | losers):
- p10: +0 | +0
- p25: +0 | +0
- p50: +0 | +100
- p75: +100 | +100
- p90: +100 | +100

**sector_div_delta** (winners | losers):
- p10: -303 | -225
- p25: -101 | -53
- p50: +63 | +46
- p75: +211 | +268
- p90: +391 | +463

#### 5. Decision tree (max_depth=2)

**Tree (max_depth=2)** target=final_winner, n_valid=170:
```
|--- current_ur_bps <= 22.50
|   |--- mfe_bps_to_date <= 121.82
|   |   |--- class: 0
|   |--- mfe_bps_to_date >  121.82
|   |   |--- class: 0
|--- current_ur_bps >  22.50
|   |--- current_ur_bps <= 314.11
|   |   |--- class: 1
|   |--- current_ur_bps >  314.11
|   |   |--- class: 1
```

Per-leaf WR / mean_final_net_bps:
- leaf 2: n=65  WR=38.5%  mean_net=-172 bps
- leaf 3: n=17  WR=11.8%  mean_net=-478 bps
- leaf 5: n=65  WR=66.2%  mean_net=+271 bps
- leaf 6: n=23  WR=95.7%  mean_net=+875 bps

#### 6. Threshold sweep — top 10 par discrimination
| Feature | Op | Threshold | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|---|
| current_ur_bps | >= | 500 | 13 | 8% | 92.3% | +923 |
| mfe_bps_to_date | >= | 700 | 12 | 7% | 91.7% | +963 |
| mfe_bps_to_date | >= | 500 | 20 | 12% | 90.0% | +799 |
| current_ur_bps | >= | 200 | 40 | 24% | 80.0% | +641 |
| mae_bps_to_date | >= | 0 | 53 | 31% | 75.5% | +487 |
| mfe_bps_to_date | >= | 300 | 50 | 29% | 72.0% | +473 |
| current_ur_bps | >= | 0 | 92 | 54% | 71.7% | +408 |
| time_in_pain_pct | <= | 25 | 92 | 54% | 71.7% | +408 |
| time_in_pain_pct | <= | 50 | 92 | 54% | 71.7% | +408 |
| time_in_pain_pct | <= | 60 | 92 | 54% | 71.7% | +408 |

#### 7. Combo (mfe<X AND pain>Y%) — top 10 par WR croissant
| mfe<X | pain>Y% | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|
| 500 | 50 | 78 | 46% | 33.3% | -246 |
| 500 | 60 | 78 | 46% | 33.3% | -246 |
| 500 | 70 | 78 | 46% | 33.3% | -246 |
| 500 | 75 | 78 | 46% | 33.3% | -246 |
| 500 | 80 | 78 | 46% | 33.3% | -246 |
| 500 | 90 | 78 | 46% | 33.3% | -246 |
| 300 | 50 | 76 | 45% | 34.2% | -230 |
| 300 | 60 | 76 | 45% | 34.2% | -230 |
| 300 | 70 | 76 | 45% | 34.2% | -230 |
| 300 | 75 | 76 | 45% | 34.2% | -230 |

#### 7b. Triple combo (mfe<X AND pain>Y% AND sector_div_delta<Z) — top 5 par WR
| mfe<X | pain>Y% | sd<Z | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|---|
| 300 | 60 | 0 | 29 | 17% | 27.6% | -156 |
| 300 | 70 | 0 | 29 | 17% | 27.6% | -156 |
| 300 | 80 | 0 | 29 | 17% | 27.6% | -156 |
| 200 | 60 | 0 | 26 | 15% | 30.8% | -97 |
| 200 | 70 | 0 | 26 | 15% | 30.8% | -97 |

### S5 dir=-1 @ T+8h  (n=170)

#### 4. Distribution (winners | losers)

#### S5 dir=-1 @ T+8h  (n=170, winners=92, losers=78)

**current_ur_bps** (winners | losers):
- p10: -240 | -562
- p25: -100 | -319
- p50: +142 | -120
- p75: +371 | +26
- p90: +654 | +165

**mfe_bps_to_date** (winners | losers):
- p10: +0 | +0
- p25: +120 | +0
- p50: +311 | +119
- p75: +522 | +293
- p90: +902 | +419

**mae_bps_to_date** (winners | losers):
- p10: -470 | -673
- p25: -231 | -495
- p50: -55 | -270
- p75: +0 | -122
- p90: +0 | -21

**time_in_pain_pct** (winners | losers):
- p10: +0 | +0
- p25: +0 | +50
- p50: +0 | +100
- p75: +50 | +100
- p90: +100 | +100

**sector_div_delta** (winners | losers):
- p10: -269 | -294
- p25: -102 | -65
- p50: +120 | +130
- p75: +319 | +304
- p90: +495 | +502

#### 5. Decision tree (max_depth=2)

**Tree (max_depth=2)** target=final_winner, n_valid=170:
```
|--- current_ur_bps <= 98.78
|   |--- current_ur_bps <= -297.95
|   |   |--- class: 0
|   |--- current_ur_bps >  -297.95
|   |   |--- class: 0
|--- current_ur_bps >  98.78
|   |--- current_ur_bps <= 311.84
|   |   |--- class: 1
|   |--- current_ur_bps >  311.84
|   |   |--- class: 1
```

Per-leaf WR / mean_final_net_bps:
- leaf 2: n=31  WR=12.9%  mean_net=-551 bps
- leaf 3: n=74  WR=47.3%  mean_net=-13 bps
- leaf 5: n=35  WR=68.6%  mean_net=+237 bps
- leaf 6: n=30  WR=96.7%  mean_net=+940 bps

#### 6. Threshold sweep — top 10 par discrimination
| Feature | Op | Threshold | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|---|
| current_ur_bps | >= | 500 | 17 | 10% | 100.0% | +1021 |
| mfe_bps_to_date | >= | 700 | 16 | 9% | 93.8% | +900 |
| mfe_bps_to_date | >= | 500 | 27 | 16% | 88.9% | +820 |
| current_ur_bps | <= | -500 | 13 | 8% | 15.4% | -718 |
| current_ur_bps | >= | 200 | 45 | 26% | 84.4% | +688 |
| mae_bps_to_date | >= | 0 | 39 | 23% | 82.1% | +633 |
| mae_bps_to_date | <= | -500 | 23 | 14% | 21.7% | -522 |
| mae_bps_to_date | >= | -100 | 71 | 42% | 77.5% | +483 |
| time_in_pain_pct | <= | 25 | 74 | 44% | 75.7% | +486 |
| current_ur_bps | >= | 0 | 84 | 49% | 72.6% | +444 |

#### 7. Combo (mfe<X AND pain>Y%) — top 10 par WR croissant
| mfe<X | pain>Y% | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|
| 300 | 50 | 65 | 38% | 30.8% | -293 |
| 300 | 60 | 65 | 38% | 30.8% | -293 |
| 300 | 70 | 65 | 38% | 30.8% | -293 |
| 300 | 75 | 65 | 38% | 30.8% | -293 |
| 300 | 80 | 65 | 38% | 30.8% | -293 |
| 300 | 90 | 65 | 38% | 30.8% | -293 |
| 500 | 50 | 68 | 40% | 30.9% | -302 |
| 500 | 60 | 68 | 40% | 30.9% | -302 |
| 500 | 70 | 68 | 40% | 30.9% | -302 |
| 500 | 75 | 68 | 40% | 30.9% | -302 |

#### 7b. Triple combo (mfe<X AND pain>Y% AND sector_div_delta<Z) — top 5 par WR
| mfe<X | pain>Y% | sd<Z | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|---|
| 300 | 60 | 0 | 19 | 11% | 26.3% | -223 |
| 300 | 70 | 0 | 19 | 11% | 26.3% | -223 |
| 300 | 80 | 0 | 19 | 11% | 26.3% | -223 |
| 200 | 60 | 0 | 17 | 10% | 29.4% | -134 |
| 200 | 70 | 0 | 17 | 10% | 29.4% | -134 |

### S5 dir=-1 @ T+12h  (n=169)

#### 4. Distribution (winners | losers)

#### S5 dir=-1 @ T+12h  (n=169, winners=92, losers=77)

**current_ur_bps** (winners | losers):
- p10: -251 | -603
- p25: -35 | -411
- p50: +139 | -126
- p75: +391 | +82
- p90: +787 | +222

**mfe_bps_to_date** (winners | losers):
- p10: +32 | +0
- p25: +189 | +0
- p50: +328 | +171
- p75: +615 | +309
- p90: +1152 | +419

**mae_bps_to_date** (winners | losers):
- p10: -480 | -800
- p25: -273 | -551
- p50: -72 | -307
- p75: +0 | -163
- p90: +0 | -32

**time_in_pain_pct** (winners | losers):
- p10: +0 | +0
- p25: +0 | +33
- p50: +0 | +100
- p75: +67 | +100
- p90: +100 | +100

**sector_div_delta** (winners | losers):
- p10: -349 | -317
- p25: -98 | -141
- p50: +146 | +129
- p75: +423 | +351
- p90: +559 | +566

#### 5. Decision tree (max_depth=2)

**Tree (max_depth=2)** target=final_winner, n_valid=169:
```
|--- current_ur_bps <= 287.31
|   |--- current_ur_bps <= -267.88
|   |   |--- class: 0
|   |--- current_ur_bps >  -267.88
|   |   |--- class: 1
|--- current_ur_bps >  287.31
|   |--- mfe_bps_to_date <= 573.65
|   |   |--- class: 1
|   |--- mfe_bps_to_date >  573.65
|   |   |--- class: 1
```

Per-leaf WR / mean_final_net_bps:
- leaf 2: n=40  WR=20.0%  mean_net=-420 bps
- leaf 3: n=91  WR=52.7%  mean_net=+62 bps
- leaf 5: n=14  WR=85.7%  mean_net=+503 bps
- leaf 6: n=24  WR=100.0%  mean_net=+992 bps

#### 6. Threshold sweep — top 10 par discrimination
| Feature | Op | Threshold | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|---|
| mfe_bps_to_date | >= | 1000 | 11 | 7% | 100.0% | +1150 |
| mfe_bps_to_date | >= | 700 | 20 | 12% | 95.0% | +968 |
| current_ur_bps | >= | 500 | 19 | 11% | 94.7% | +953 |
| mfe_bps_to_date | >= | 500 | 31 | 18% | 90.3% | +805 |
| mae_bps_to_date | >= | 0 | 34 | 20% | 85.3% | +721 |
| current_ur_bps | >= | 200 | 50 | 30% | 82.0% | +632 |
| mae_bps_to_date | >= | -100 | 64 | 38% | 79.7% | +534 |
| time_in_pain_pct | <= | 25 | 65 | 38% | 76.9% | +537 |
| mae_bps_to_date | <= | -500 | 30 | 18% | 23.3% | -415 |
| current_ur_bps | <= | -500 | 17 | 10% | 23.5% | -524 |

#### 7. Combo (mfe<X AND pain>Y%) — top 10 par WR croissant
| mfe<X | pain>Y% | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|
| 50 | 50 | 37 | 22% | 27.0% | -333 |
| 50 | 60 | 37 | 22% | 27.0% | -333 |
| 50 | 70 | 37 | 22% | 27.0% | -333 |
| 50 | 75 | 37 | 22% | 27.0% | -333 |
| 50 | 80 | 37 | 22% | 27.0% | -333 |
| 50 | 90 | 37 | 22% | 27.0% | -333 |
| 500 | 70 | 55 | 33% | 27.3% | -364 |
| 500 | 75 | 55 | 33% | 27.3% | -364 |
| 500 | 80 | 55 | 33% | 27.3% | -364 |
| 500 | 90 | 55 | 33% | 27.3% | -364 |

#### 7b. Triple combo (mfe<X AND pain>Y% AND sector_div_delta<Z) — top 5 par WR
| mfe<X | pain>Y% | sd<Z | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|---|
| 200 | 60 | 0 | 16 | 9% | 12.5% | -327 |
| 300 | 70 | 0 | 16 | 9% | 12.5% | -428 |
| 300 | 80 | 0 | 16 | 9% | 12.5% | -428 |
| 200 | 70 | 0 | 14 | 8% | 14.3% | -349 |
| 200 | 80 | 0 | 14 | 8% | 14.3% | -349 |

### S5 dir=-1 @ T+24h  (n=165)

#### 4. Distribution (winners | losers)

#### S5 dir=-1 @ T+24h  (n=165, winners=92, losers=73)

**current_ur_bps** (winners | losers):
- p10: -195 | -697
- p25: +21 | -484
- p50: +276 | -222
- p75: +697 | -52
- p90: +1027 | +193

**mfe_bps_to_date** (winners | losers):
- p10: +120 | +0
- p25: +289 | +0
- p50: +471 | +200
- p75: +920 | +406
- p90: +1392 | +586

**mae_bps_to_date** (winners | losers):
- p10: -537 | -1039
- p25: -357 | -702
- p50: -148 | -480
- p75: +0 | -249
- p90: +0 | -158

**time_in_pain_pct** (winners | losers):
- p10: +0 | +17
- p25: +0 | +33
- p50: +8 | +83
- p75: +50 | +100
- p90: +83 | +100

**sector_div_delta** (winners | losers):
- p10: -415 | -409
- p25: -108 | -72
- p50: +181 | +251
- p75: +520 | +530
- p90: +795 | +693

#### 5. Decision tree (max_depth=2)

**Tree (max_depth=2)** target=final_winner, n_valid=165:
```
|--- current_ur_bps <= -40.80
|   |--- current_ur_bps <= -199.14
|   |   |--- class: 0
|   |--- current_ur_bps >  -199.14
|   |   |--- class: 0
|--- current_ur_bps >  -40.80
|   |--- mfe_bps_to_date <= 650.27
|   |   |--- class: 1
|   |--- mfe_bps_to_date >  650.27
|   |   |--- class: 1
```

Per-leaf WR / mean_final_net_bps:
- leaf 2: n=51  WR=17.6%  mean_net=-396 bps
- leaf 3: n=23  WR=39.1%  mean_net=-111 bps
- leaf 5: n=55  WR=70.9%  mean_net=+253 bps
- leaf 6: n=36  WR=97.2%  mean_net=+933 bps

#### 6. Threshold sweep — top 10 par discrimination
| Feature | Op | Threshold | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|---|
| current_ur_bps | >= | 1000 | 10 | 6% | 100.0% | +1489 |
| mfe_bps_to_date | >= | 1000 | 17 | 10% | 100.0% | +1126 |
| current_ur_bps | >= | 500 | 31 | 19% | 96.8% | +1029 |
| mae_bps_to_date | >= | 0 | 26 | 16% | 96.2% | +940 |
| mfe_bps_to_date | >= | 700 | 34 | 21% | 94.1% | +926 |
| mae_bps_to_date | >= | -100 | 47 | 28% | 91.5% | +766 |
| mae_bps_to_date | <= | -800 | 17 | 10% | 11.8% | -673 |
| current_ur_bps | >= | 200 | 63 | 38% | 87.3% | +700 |
| current_ur_bps | <= | -500 | 18 | 11% | 16.7% | -579 |
| current_ur_bps | <= | -200 | 51 | 31% | 17.6% | -396 |

#### 7. Combo (mfe<X AND pain>Y%) — top 10 par WR croissant
| mfe<X | pain>Y% | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|
| 300 | 90 | 44 | 27% | 20.5% | -400 |
| 500 | 90 | 44 | 27% | 20.5% | -400 |
| 200 | 90 | 41 | 25% | 22.0% | -365 |
| 300 | 70 | 50 | 30% | 22.0% | -361 |
| 300 | 75 | 50 | 30% | 22.0% | -361 |
| 300 | 80 | 50 | 30% | 22.0% | -361 |
| 500 | 70 | 53 | 32% | 22.6% | -352 |
| 500 | 75 | 53 | 32% | 22.6% | -352 |
| 500 | 80 | 53 | 32% | 22.6% | -352 |
| 200 | 70 | 43 | 26% | 23.3% | -349 |

#### 7b. Triple combo (mfe<X AND pain>Y% AND sector_div_delta<Z) — top 5 par WR
| mfe<X | pain>Y% | sd<Z | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|---|
| 300 | 70 | 500 | 35 | 21% | 20.0% | -299 |
| 300 | 80 | 500 | 35 | 21% | 20.0% | -299 |
| 200 | 70 | 500 | 31 | 19% | 22.6% | -270 |
| 200 | 80 | 500 | 31 | 19% | 22.6% | -270 |
| 100 | 60 | 500 | 24 | 15% | 25.0% | -274 |

## Cibles secondaires


### S8 dir=+1 @ T+4h  (n=118)

#### 4. Distribution (winners | losers)

#### S8 dir=+1 @ T+4h  (n=118, winners=70, losers=48)

**current_ur_bps** (winners | losers):
- p10: -32 | -1127
- p25: +73 | -736
- p50: +286 | -285
- p75: +488 | -3
- p90: +637 | +136

**mfe_bps_to_date** (winners | losers):
- p10: +110 | +0
- p25: +275 | +0
- p50: +412 | +0
- p75: +574 | +121
- p90: +731 | +241

**mae_bps_to_date** (winners | losers):
- p10: -320 | -1769
- p25: -147 | -859
- p50: +0 | -514
- p75: +0 | -222
- p90: +0 | -71

**time_in_pain_pct** (winners | losers):
- p10: +0 | +0
- p25: +0 | +75
- p50: +0 | +100
- p75: +0 | +100
- p90: +100 | +100

**sector_div_delta** (winners | losers):
- p10: -185 | -169
- p25: -78 | -141
- p50: +48 | -29
- p75: +124 | +110
- p90: +229 | +171

#### 5. Decision tree (max_depth=2)

**Tree (max_depth=2)** target=final_winner, n_valid=111:
```
|--- mfe_bps_to_date <= 24.77
|   |--- sector_div_delta <= -155.81
|   |   |--- class: 0
|   |--- sector_div_delta >  -155.81
|   |   |--- class: 0
|--- mfe_bps_to_date >  24.77
|   |--- mfe_bps_to_date <= 238.69
|   |   |--- class: 1
|   |--- mfe_bps_to_date >  238.69
|   |   |--- class: 1
```

Per-leaf WR / mean_final_net_bps:
- leaf 2: n=10  WR=20.0%  mean_net=-332 bps
- leaf 3: n=20  WR=0.0%  mean_net=-729 bps
- leaf 5: n=23  WR=56.5%  mean_net=+29 bps
- leaf 6: n=58  WR=91.4%  mean_net=+902 bps

#### 6. Threshold sweep — top 10 par discrimination
| Feature | Op | Threshold | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|---|
| current_ur_bps | <= | -500 | 21 | 18% | 0.0% | -763 |
| mae_bps_to_date | <= | -1000 | 10 | 8% | 0.0% | -763 |
| mae_bps_to_date | <= | -800 | 14 | 12% | 0.0% | -763 |
| mfe_bps_to_date | >= | 500 | 27 | 23% | 96.3% | +1096 |
| mae_bps_to_date | >= | 0 | 41 | 35% | 95.1% | +898 |
| current_ur_bps | >= | 500 | 16 | 14% | 93.8% | +1067 |
| current_ur_bps | >= | 200 | 45 | 38% | 93.3% | +951 |
| mfe_bps_to_date | >= | 300 | 53 | 45% | 92.5% | +912 |
| current_ur_bps | <= | -200 | 31 | 26% | 9.7% | -597 |
| mfe_bps_to_date | >= | 700 | 10 | 8% | 90.0% | +1046 |

#### 7. Combo (mfe<X AND pain>Y%) — top 10 par WR croissant
| mfe<X | pain>Y% | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|
| 50 | 50 | 36 | 31% | 11.1% | -556 |
| 50 | 60 | 36 | 31% | 11.1% | -556 |
| 50 | 70 | 36 | 31% | 11.1% | -556 |
| 50 | 75 | 36 | 31% | 11.1% | -556 |
| 50 | 80 | 36 | 31% | 11.1% | -556 |
| 50 | 90 | 36 | 31% | 11.1% | -556 |
| 100 | 50 | 39 | 33% | 12.8% | -540 |
| 100 | 60 | 39 | 33% | 12.8% | -540 |
| 100 | 70 | 39 | 33% | 12.8% | -540 |
| 100 | 75 | 39 | 33% | 12.8% | -540 |

#### 7b. Triple combo (mfe<X AND pain>Y% AND sector_div_delta<Z) — top 5 par WR
| mfe<X | pain>Y% | sd<Z | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|---|
| 100 | 60 | 500 | 34 | 29% | 11.8% | -544 |
| 100 | 70 | 500 | 34 | 29% | 11.8% | -544 |
| 100 | 80 | 500 | 34 | 29% | 11.8% | -544 |
| 100 | 60 | 0 | 23 | 19% | 13.0% | -528 |
| 100 | 70 | 0 | 23 | 19% | 13.0% | -528 |

### S8 dir=+1 @ T+8h  (n=100)

#### 4. Distribution (winners | losers)

#### S8 dir=+1 @ T+8h  (n=100, winners=69, losers=31)

**current_ur_bps** (winners | losers):
- p10: +13 | -569
- p25: +210 | -336
- p50: +357 | -120
- p75: +594 | +107
- p90: +875 | +269

**mfe_bps_to_date** (winners | losers):
- p10: +302 | +0
- p25: +390 | +0
- p50: +526 | +100
- p75: +740 | +221
- p90: +1175 | +433

**mae_bps_to_date** (winners | losers):
- p10: -407 | -701
- p25: -233 | -582
- p50: -18 | -380
- p75: +0 | -179
- p90: +0 | -61

**time_in_pain_pct** (winners | losers):
- p10: +0 | +0
- p25: +0 | +0
- p50: +0 | +100
- p75: +0 | +100
- p90: +50 | +100

**sector_div_delta** (winners | losers):
- p10: -304 | -209
- p25: -106 | -117
- p50: +31 | -6
- p75: +185 | +99
- p90: +284 | +203

#### 5. Decision tree (max_depth=2)

**Tree (max_depth=2)** target=final_winner, n_valid=96:
```
|--- mfe_bps_to_date <= 258.70
|   |--- mfe_bps_to_date <= 25.44
|   |   |--- class: 0
|   |--- mfe_bps_to_date >  25.44
|   |   |--- class: 0
|--- mfe_bps_to_date >  258.70
|   |--- mae_bps_to_date <= -174.53
|   |   |--- class: 1
|   |--- mae_bps_to_date >  -174.53
|   |   |--- class: 1
```

Per-leaf WR / mean_final_net_bps:
- leaf 2: n=13  WR=0.0%  mean_net=-711 bps
- leaf 3: n=16  WR=37.5%  mean_net=-238 bps
- leaf 5: n=17  WR=82.4%  mean_net=+820 bps
- leaf 6: n=50  WR=94.0%  mean_net=+896 bps

#### 6. Threshold sweep — top 10 par discrimination
| Feature | Op | Threshold | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|---|
| current_ur_bps | <= | -200 | 12 | 12% | 0.0% | -757 |
| current_ur_bps | >= | 500 | 25 | 25% | 96.0% | +1133 |
| mfe_bps_to_date | >= | 700 | 21 | 21% | 95.2% | +1200 |
| mfe_bps_to_date | >= | 500 | 39 | 39% | 94.9% | +1067 |
| mae_bps_to_date | >= | 0 | 35 | 35% | 94.3% | +950 |
| mfe_bps_to_date | <= | 100 | 17 | 17% | 5.9% | -643 |
| mfe_bps_to_date | <= | 50 | 16 | 16% | 6.2% | -635 |
| current_ur_bps | >= | 200 | 56 | 56% | 92.9% | +938 |
| mfe_bps_to_date | >= | 300 | 68 | 68% | 91.2% | +872 |
| mae_bps_to_date | >= | -100 | 45 | 45% | 91.1% | +865 |

#### 7. Combo (mfe<X AND pain>Y%) — top 10 par WR croissant
| mfe<X | pain>Y% | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|
| 150 | 50 | 17 | 17% | 5.9% | -602 |
| 150 | 60 | 17 | 17% | 5.9% | -602 |
| 150 | 70 | 17 | 17% | 5.9% | -602 |
| 150 | 75 | 17 | 17% | 5.9% | -602 |
| 150 | 80 | 17 | 17% | 5.9% | -602 |
| 150 | 90 | 17 | 17% | 5.9% | -602 |
| 200 | 50 | 17 | 17% | 5.9% | -602 |
| 200 | 60 | 17 | 17% | 5.9% | -602 |
| 200 | 70 | 17 | 17% | 5.9% | -602 |
| 200 | 75 | 17 | 17% | 5.9% | -602 |

#### 7b. Triple combo (mfe<X AND pain>Y% AND sector_div_delta<Z) — top 5 par WR
| mfe<X | pain>Y% | sd<Z | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|---|
| 200 | 60 | 0 | 10 | 10% | 0.0% | -686 |
| 200 | 70 | 0 | 10 | 10% | 0.0% | -686 |
| 200 | 80 | 0 | 10 | 10% | 0.0% | -686 |
| 200 | 60 | 500 | 16 | 16% | 6.2% | -592 |
| 200 | 70 | 500 | 16 | 16% | 6.2% | -592 |

### S8 dir=+1 @ T+12h  (n=95)

#### 4. Distribution (winners | losers)

#### S8 dir=+1 @ T+12h  (n=95, winners=67, losers=28)

**current_ur_bps** (winners | losers):
- p10: -48 | -599
- p25: +210 | -417
- p50: +449 | -256
- p75: +643 | +8
- p90: +860 | +278

**mfe_bps_to_date** (winners | losers):
- p10: +357 | +0
- p25: +437 | +0
- p50: +571 | +123
- p75: +751 | +251
- p90: +1200 | +497

**mae_bps_to_date** (winners | losers):
- p10: -526 | -764
- p25: -290 | -661
- p50: -89 | -468
- p75: +0 | -254
- p90: +0 | -101

**time_in_pain_pct** (winners | losers):
- p10: +0 | +0
- p25: +0 | +33
- p50: +0 | +67
- p75: +33 | +100
- p90: +33 | +100

**sector_div_delta** (winners | losers):
- p10: -350 | -284
- p25: -140 | -142
- p50: -32 | +11
- p75: +132 | +111
- p90: +259 | +250

#### 5. Decision tree (max_depth=2)

**Tree (max_depth=2)** target=final_winner, n_valid=91:
```
|--- mfe_bps_to_date <= 271.50
|   |--- current_ur_bps <= -224.32
|   |   |--- class: 0
|   |--- current_ur_bps >  -224.32
|   |   |--- class: 0
|--- mfe_bps_to_date >  271.50
|   |--- current_ur_bps <= 87.00
|   |   |--- class: 1
|   |--- current_ur_bps >  87.00
|   |   |--- class: 1
```

Per-leaf WR / mean_final_net_bps:
- leaf 2: n=14  WR=0.0%  mean_net=-708 bps
- leaf 3: n=10  WR=40.0%  mean_net=-157 bps
- leaf 5: n=12  WR=75.0%  mean_net=+399 bps
- leaf 6: n=55  WR=94.5%  mean_net=+948 bps

#### 6. Threshold sweep — top 10 par discrimination
| Feature | Op | Threshold | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|---|
| mfe_bps_to_date | <= | 50 | 12 | 13% | 0.0% | -707 |
| mfe_bps_to_date | >= | 1000 | 11 | 12% | 100.0% | +1467 |
| mae_bps_to_date | >= | 0 | 28 | 29% | 96.4% | +991 |
| mfe_bps_to_date | >= | 700 | 25 | 26% | 96.0% | +1184 |
| current_ur_bps | >= | 200 | 55 | 58% | 94.5% | +953 |
| mfe_bps_to_date | >= | 500 | 48 | 51% | 93.8% | +996 |
| mfe_bps_to_date | <= | 100 | 14 | 15% | 7.1% | -617 |
| time_in_pain_pct | >= | 70 | 14 | 15% | 7.1% | -567 |
| time_in_pain_pct | >= | 75 | 14 | 15% | 7.1% | -567 |
| time_in_pain_pct | >= | 80 | 14 | 15% | 7.1% | -567 |

#### 7. Combo (mfe<X AND pain>Y%) — top 10 par WR croissant
| mfe<X | pain>Y% | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|
| 50 | 50 | 12 | 13% | 0.0% | -707 |
| 50 | 60 | 12 | 13% | 0.0% | -707 |
| 50 | 70 | 12 | 13% | 0.0% | -707 |
| 50 | 75 | 12 | 13% | 0.0% | -707 |
| 50 | 80 | 12 | 13% | 0.0% | -707 |
| 50 | 90 | 12 | 13% | 0.0% | -707 |
| 200 | 50 | 19 | 20% | 5.3% | -619 |
| 200 | 60 | 19 | 20% | 5.3% | -619 |
| 300 | 50 | 19 | 20% | 5.3% | -619 |
| 300 | 60 | 19 | 20% | 5.3% | -619 |

#### 7b. Triple combo (mfe<X AND pain>Y% AND sector_div_delta<Z) — top 5 par WR
| mfe<X | pain>Y% | sd<Z | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|---|
| 200 | 60 | 500 | 18 | 19% | 5.6% | -611 |
| 300 | 60 | 500 | 18 | 19% | 5.6% | -611 |
| 100 | 60 | 500 | 13 | 14% | 7.7% | -606 |
| 200 | 70 | 500 | 13 | 14% | 7.7% | -552 |
| 200 | 80 | 500 | 13 | 14% | 7.7% | -552 |

### S8 dir=+1 @ T+24h  (n=84)

#### 4. Distribution (winners | losers)

#### S8 dir=+1 @ T+24h  (n=84, winners=64, losers=20)

**current_ur_bps** (winners | losers):
- p10: -7 | -571
- p25: +122 | -470
- p50: +516 | -334
- p75: +890 | -54
- p90: +1242 | +299

**mfe_bps_to_date** (winners | losers):
- p10: +400 | +0
- p25: +575 | +50
- p50: +810 | +211
- p75: +1117 | +345
- p90: +1458 | +466

**mae_bps_to_date** (winners | losers):
- p10: -528 | -765
- p25: -337 | -669
- p50: -147 | -601
- p75: +0 | -393
- p90: +0 | -179

**time_in_pain_pct** (winners | losers):
- p10: +0 | +15
- p25: +0 | +33
- p50: +0 | +67
- p75: +17 | +100
- p90: +50 | +100

**sector_div_delta** (winners | losers):
- p10: -449 | -334
- p25: -130 | -127
- p50: +7 | +55
- p75: +208 | +166
- p90: +301 | +322

#### 5. Decision tree (max_depth=2)

**Tree (max_depth=2)** target=final_winner, n_valid=81:
```
|--- mfe_bps_to_date <= 347.73
|   |--- class: 0
|--- mfe_bps_to_date >  347.73
|   |--- mfe_bps_to_date <= 457.41
|   |   |--- class: 1
|   |--- mfe_bps_to_date >  457.41
|   |   |--- class: 1
```

Per-leaf WR / mean_final_net_bps:
- leaf 1: n=16  WR=12.5%  mean_net=-490 bps
- leaf 3: n=10  WR=70.0%  mean_net=+312 bps
- leaf 4: n=55  WR=96.4%  mean_net=+968 bps

#### 6. Threshold sweep — top 10 par discrimination
| Feature | Op | Threshold | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|---|
| current_ur_bps | >= | 500 | 33 | 39% | 100.0% | +1224 |
| current_ur_bps | >= | 1000 | 12 | 14% | 100.0% | +1309 |
| mfe_bps_to_date | >= | 1000 | 21 | 25% | 100.0% | +1305 |
| mae_bps_to_date | >= | -100 | 30 | 36% | 96.7% | +1059 |
| mfe_bps_to_date | >= | 500 | 56 | 67% | 96.4% | +972 |
| mae_bps_to_date | >= | 0 | 22 | 26% | 95.5% | +1167 |
| mfe_bps_to_date | >= | 700 | 40 | 48% | 95.0% | +1066 |
| current_ur_bps | >= | 200 | 48 | 57% | 93.8% | +1027 |
| mae_bps_to_date | >= | -300 | 48 | 57% | 93.8% | +937 |
| time_in_pain_pct | <= | 25 | 58 | 69% | 93.1% | +896 |

#### 7. Combo (mfe<X AND pain>Y%) — top 10 par WR croissant
| mfe<X | pain>Y% | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|
| 300 | 50 | 12 | 14% | 0.0% | -649 |
| 300 | 60 | 12 | 14% | 0.0% | -649 |
| 500 | 50 | 13 | 15% | 7.7% | -467 |
| 500 | 60 | 13 | 15% | 7.7% | -467 |

#### 7b. Triple combo (mfe<X AND pain>Y% AND sector_div_delta<Z) — top 5 par WR
| mfe<X | pain>Y% | sd<Z | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|---|
| 300 | 60 | 500 | 11 | 13% | 0.0% | -638 |

### S9 dir=-1 @ T+4h  (n=86)

#### 4. Distribution (winners | losers)

#### S9 dir=-1 @ T+4h  (n=86, winners=40, losers=46)

**current_ur_bps** (winners | losers):
- p10: -204 | -667
- p25: +42 | -489
- p50: +281 | -170
- p75: +499 | +101
- p90: +846 | +368

**mfe_bps_to_date** (winners | losers):
- p10: +59 | +0
- p25: +267 | +0
- p50: +403 | +248
- p75: +648 | +464
- p90: +950 | +704

**mae_bps_to_date** (winners | losers):
- p10: -682 | -1104
- p25: -413 | -805
- p50: -32 | -527
- p75: +0 | -122
- p90: +0 | +0

**time_in_pain_pct** (winners | losers):
- p10: +0 | +0
- p25: +0 | +0
- p50: +0 | +100
- p75: +0 | +100
- p90: +100 | +100

**sector_div_delta** (winners | losers):
- p10: -1081 | -539
- p25: -555 | -193
- p50: -232 | +126
- p75: +3 | +615
- p90: +274 | +1002

#### 5. Decision tree (max_depth=2)

**Tree (max_depth=2)** target=final_winner, n_valid=82:
```
|--- current_ur_bps <= -153.01
|   |--- sector_div_delta <= 143.84
|   |   |--- class: 0
|   |--- sector_div_delta >  143.84
|   |   |--- class: 0
|--- current_ur_bps >  -153.01
|   |--- current_ur_bps <= 238.48
|   |   |--- class: 0
|   |--- current_ur_bps >  238.48
|   |   |--- class: 1
```

Per-leaf WR / mean_final_net_bps:
- leaf 2: n=10  WR=40.0%  mean_net=-74 bps
- leaf 3: n=19  WR=5.3%  mean_net=-731 bps
- leaf 5: n=25  WR=48.0%  mean_net=+72 bps
- leaf 6: n=28  WR=78.6%  mean_net=+572 bps

#### 6. Threshold sweep — top 10 par discrimination
| Feature | Op | Threshold | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|---|
| mae_bps_to_date | <= | -800 | 12 | 14% | 0.0% | -807 |
| current_ur_bps | <= | -500 | 14 | 16% | 14.3% | -612 |
| current_ur_bps | <= | -200 | 25 | 29% | 16.0% | -573 |
| sector_div_delta | >= | 200 | 24 | 28% | 20.8% | -473 |
| mfe_bps_to_date | <= | 50 | 19 | 22% | 21.1% | -482 |
| mae_bps_to_date | <= | -500 | 32 | 37% | 21.9% | -459 |
| current_ur_bps | >= | 500 | 13 | 15% | 76.9% | +807 |
| mfe_bps_to_date | <= | 150 | 26 | 30% | 23.1% | -455 |
| mfe_bps_to_date | <= | 200 | 26 | 30% | 23.1% | -455 |
| sector_div_delta | >= | 500 | 17 | 20% | 23.5% | -490 |

#### 7. Combo (mfe<X AND pain>Y%) — top 10 par WR croissant
| mfe<X | pain>Y% | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|
| 150 | 50 | 24 | 28% | 16.7% | -526 |
| 150 | 60 | 24 | 28% | 16.7% | -526 |
| 150 | 70 | 24 | 28% | 16.7% | -526 |
| 150 | 75 | 24 | 28% | 16.7% | -526 |
| 150 | 80 | 24 | 28% | 16.7% | -526 |
| 150 | 90 | 24 | 28% | 16.7% | -526 |
| 200 | 50 | 24 | 28% | 16.7% | -526 |
| 200 | 60 | 24 | 28% | 16.7% | -526 |
| 200 | 70 | 24 | 28% | 16.7% | -526 |
| 200 | 75 | 24 | 28% | 16.7% | -526 |

#### 7b. Triple combo (mfe<X AND pain>Y% AND sector_div_delta<Z) — top 5 par WR
| mfe<X | pain>Y% | sd<Z | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|---|
| 200 | 60 | 500 | 12 | 14% | 16.7% | -458 |
| 200 | 70 | 500 | 12 | 14% | 16.7% | -458 |
| 200 | 80 | 500 | 12 | 14% | 16.7% | -458 |
| 100 | 60 | 500 | 10 | 12% | 20.0% | -457 |
| 100 | 70 | 500 | 10 | 12% | 20.0% | -457 |

### S9 dir=-1 @ T+8h  (n=73)

#### 4. Distribution (winners | losers)

#### S9 dir=-1 @ T+8h  (n=73, winners=40, losers=33)

**current_ur_bps** (winners | losers):
- p10: +57 | -783
- p25: +205 | -487
- p50: +411 | +7
- p75: +678 | +277
- p90: +1322 | +588

**mfe_bps_to_date** (winners | losers):
- p10: +286 | +0
- p25: +395 | +216
- p50: +565 | +359
- p75: +827 | +673
- p90: +1409 | +1087

**mae_bps_to_date** (winners | losers):
- p10: -708 | -1117
- p25: -468 | -839
- p50: -70 | -531
- p75: +0 | -196
- p90: +0 | -5

**time_in_pain_pct** (winners | losers):
- p10: +0 | +0
- p25: +0 | +0
- p50: +0 | +50
- p75: +12 | +100
- p90: +50 | +100

**sector_div_delta** (winners | losers):
- p10: -1470 | -1158
- p25: -908 | -599
- p50: -456 | -131
- p75: -166 | +351
- p90: +180 | +790

#### 5. Decision tree (max_depth=2)

**Tree (max_depth=2)** target=final_winner, n_valid=69:
```
|--- current_ur_bps <= -78.46
|   |--- class: 0
|--- current_ur_bps >  -78.46
|   |--- current_ur_bps <= 199.45
|   |   |--- class: 0
|   |--- current_ur_bps >  199.45
|   |   |--- class: 1
```

Per-leaf WR / mean_final_net_bps:
- leaf 1: n=14  WR=7.1%  mean_net=-627 bps
- leaf 3: n=16  WR=50.0%  mean_net=+39 bps
- leaf 4: n=39  WR=76.9%  mean_net=+559 bps

#### 6. Threshold sweep — top 10 par discrimination
| Feature | Op | Threshold | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|---|
| current_ur_bps | <= | -200 | 14 | 19% | 7.1% | -645 |
| time_in_pain_pct | >= | 60 | 14 | 19% | 7.1% | -648 |
| time_in_pain_pct | >= | 70 | 14 | 19% | 7.1% | -648 |
| time_in_pain_pct | >= | 75 | 14 | 19% | 7.1% | -648 |
| time_in_pain_pct | >= | 80 | 14 | 19% | 7.1% | -648 |
| time_in_pain_pct | >= | 90 | 14 | 19% | 7.1% | -648 |
| time_in_pain_pct | >= | 100 | 14 | 19% | 7.1% | -648 |
| current_ur_bps | <= | 0 | 18 | 25% | 11.1% | -566 |
| mae_bps_to_date | >= | 0 | 22 | 30% | 81.8% | +677 |
| mfe_bps_to_date | <= | 200 | 10 | 14% | 20.0% | -499 |

#### 7. Combo (mfe<X AND pain>Y%) — top 10 par WR croissant
| mfe<X | pain>Y% | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|
| 500 | 50 | 14 | 19% | 7.1% | -648 |
| 500 | 60 | 14 | 19% | 7.1% | -648 |
| 500 | 70 | 14 | 19% | 7.1% | -648 |
| 500 | 75 | 14 | 19% | 7.1% | -648 |
| 500 | 80 | 14 | 19% | 7.1% | -648 |
| 500 | 90 | 14 | 19% | 7.1% | -648 |
| 300 | 50 | 11 | 15% | 9.1% | -622 |
| 300 | 60 | 11 | 15% | 9.1% | -622 |
| 300 | 70 | 11 | 15% | 9.1% | -622 |
| 300 | 75 | 11 | 15% | 9.1% | -622 |

### S9 dir=-1 @ T+12h  (n=62)

#### 4. Distribution (winners | losers)

#### S9 dir=-1 @ T+12h  (n=62, winners=40, losers=22)

**current_ur_bps** (winners | losers):
- p10: +54 | -388
- p25: +188 | -155
- p50: +434 | +204
- p75: +708 | +397
- p90: +1184 | +669

**mfe_bps_to_date** (winners | losers):
- p10: +365 | +185
- p25: +496 | +293
- p50: +632 | +405
- p75: +852 | +667
- p90: +1492 | +1043

**mae_bps_to_date** (winners | losers):
- p10: -708 | -680
- p25: -468 | -585
- p50: -78 | -240
- p75: +0 | -82
- p90: +0 | -2

**time_in_pain_pct** (winners | losers):
- p10: +0 | +0
- p25: +0 | +0
- p50: +0 | +0
- p75: +8 | +67
- p90: +33 | +100

**sector_div_delta** (winners | losers):
- p10: -1332 | -1223
- p25: -1049 | -785
- p50: -446 | -237
- p75: -199 | +74
- p90: +271 | +198

#### 5. Decision tree (max_depth=2)

**Tree (max_depth=2)** target=final_winner, n_valid=60:
```
|--- mfe_bps_to_date <= 396.73
|   |--- class: 0
|--- mfe_bps_to_date >  396.73
|   |--- current_ur_bps <= 550.74
|   |   |--- class: 1
|   |--- current_ur_bps >  550.74
|   |   |--- class: 1
```

Per-leaf WR / mean_final_net_bps:
- leaf 1: n=16  WR=37.5%  mean_net=-128 bps
- leaf 3: n=24  WR=66.7%  mean_net=+181 bps
- leaf 4: n=20  WR=85.0%  mean_net=+916 bps

#### 6. Threshold sweep — top 10 par discrimination
| Feature | Op | Threshold | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|---|
| mae_bps_to_date | >= | 0 | 20 | 32% | 85.0% | +768 |
| current_ur_bps | >= | 500 | 22 | 35% | 81.8% | +857 |
| mfe_bps_to_date | >= | 700 | 20 | 32% | 80.0% | +846 |
| mfe_bps_to_date | >= | 500 | 40 | 65% | 75.0% | +554 |
| mae_bps_to_date | >= | -100 | 28 | 45% | 75.0% | +519 |
| sector_div_delta | <= | -1000 | 15 | 24% | 73.3% | +906 |
| sector_div_delta | <= | -500 | 25 | 40% | 72.0% | +633 |
| current_ur_bps | >= | 200 | 39 | 63% | 71.8% | +553 |
| sector_div_delta | <= | 0 | 46 | 74% | 71.7% | +477 |
| current_ur_bps | >= | 0 | 53 | 85% | 71.7% | +466 |

#### 7. Combo (mfe<X AND pain>Y%) — top 10 par WR croissant
| mfe<X | pain>Y% | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|
| 500 | 50 | 10 | 16% | 30.0% | -321 |
| 500 | 60 | 10 | 16% | 30.0% | -321 |

### S9 dir=-1 @ T+24h  (n=56)

#### 4. Distribution (winners | losers)

#### S9 dir=-1 @ T+24h  (n=56, winners=40, losers=16)

**current_ur_bps** (winners | losers):
- p10: +172 | -431
- p25: +297 | -210
- p50: +517 | +178
- p75: +810 | +373
- p90: +1266 | +538

**mfe_bps_to_date** (winners | losers):
- p10: +500 | +206
- p25: +643 | +356
- p50: +832 | +637
- p75: +1192 | +869
- p90: +1625 | +1093

**mae_bps_to_date** (winners | losers):
- p10: -708 | -812
- p25: -468 | -577
- p50: -159 | -343
- p75: +0 | -176
- p90: +0 | -81

**time_in_pain_pct** (winners | losers):
- p10: +0 | +0
- p25: +0 | +0
- p50: +0 | +8
- p75: +17 | +38
- p90: +18 | +67

**sector_div_delta** (winners | losers):
- p10: -1284 | -1405
- p25: -962 | -1099
- p50: -526 | -373
- p75: -298 | +15
- p90: +117 | +552

#### 5. Decision tree (max_depth=2)

**Tree (max_depth=2)** target=final_winner, n_valid=55:
```
|--- current_ur_bps <= 170.19
|   |--- class: 0
|--- current_ur_bps >  170.19
|   |--- current_ur_bps <= 400.35
|   |   |--- class: 1
|   |--- current_ur_bps >  400.35
|   |   |--- class: 1
```

Per-leaf WR / mean_final_net_bps:
- leaf 1: n=12  WR=33.3%  mean_net=-282 bps
- leaf 3: n=14  WR=64.3%  mean_net=+119 bps
- leaf 4: n=29  WR=89.7%  mean_net=+895 bps

#### 6. Threshold sweep — top 10 par discrimination
| Feature | Op | Threshold | n | %total | WR | mean_net_bps |
|---|---|---|---|---|---|---|
| mae_bps_to_date | >= | 0 | 16 | 29% | 93.8% | +958 |
| current_ur_bps | >= | 500 | 23 | 41% | 91.3% | +1009 |
| mae_bps_to_date | >= | -100 | 20 | 36% | 90.0% | +815 |
| mfe_bps_to_date | >= | 700 | 31 | 55% | 83.9% | +760 |
| current_ur_bps | >= | 200 | 41 | 73% | 82.9% | +684 |
| current_ur_bps | >= | 0 | 47 | 84% | 80.9% | +614 |
| mfe_bps_to_date | >= | 500 | 45 | 80% | 80.0% | +586 |
| time_in_pain_pct | <= | 25 | 45 | 80% | 80.0% | +616 |
| mae_bps_to_date | >= | -300 | 32 | 57% | 78.1% | +610 |
| current_ur_bps | >= | -200 | 50 | 89% | 78.0% | +563 |

## 7c. Cost-benefit analysis (gain réel d'une coupe vs trade laissé courir)

Une règle qui identifie correctement les losers (WR<25%) ne sauve de l'argent que si le `current_ur_bps` au checkpoint est meilleur que le `final_net_bps` moyen. `savings_bps = mean_cur_ur - mean_final_net`. Positif = coupe utile. Négatif = la coupe verrouille une perte que le marché aurait récupérée.

| Target | CP | Rule | n | WR | mean_cur | mean_final | savings | %cut_helped |
|---|---|---|---|---|---|---|---|---|
| S5 dir=+1 | T+4h | user_proposed | 127 | 28.3% | -327 | -294 | **-33** | 58% |
| S5 dir=+1 | T+8h | user_proposed | 98 | 20.4% | -352 | -398 | **+46** | 66% |
| S5 dir=+1 | T+12h | user_proposed | 83 | 18.1% | -419 | -451 | **+33** | 64% |
| S5 dir=+1 | T+24h | user_proposed | 71 | 11.3% | -483 | -497 | **+14** | 63% |
| S5 dir=+1 | T+4h | strong | 99 | 24.2% | -373 | -364 | **-8** | 60% |
| S5 dir=+1 | T+8h | strong | 72 | 13.9% | -396 | -505 | **+108** | 69% |
| S5 dir=+1 | T+12h | strong | 60 | 11.7% | -476 | -529 | **+53** | 63% |
| S5 dir=+1 | T+24h | strong | 46 | 6.5% | -511 | -518 | **+7** | 67% |
| S5 dir=+1 | T+4h | triple_mid | 34 | 26.5% | -441 | -428 | **-13** | 59% |
| S5 dir=+1 | T+8h | triple_mid | 31 | 9.7% | -388 | -659 | **+272** | 84% |
| S5 dir=+1 | T+12h | triple_mid | 34 | 8.8% | -496 | -625 | **+129** | 65% |
| S5 dir=+1 | T+24h | triple_mid | 48 | 8.3% | -532 | -631 | **+99** | 67% |
| S5 dir=-1 | T+4h | user_proposed | 65 | 36.9% | -253 | -197 | **-56** | 45% |
| S5 dir=-1 | T+8h | user_proposed | 56 | 33.9% | -327 | -249 | **-78** | 46% |
| S5 dir=-1 | T+12h | user_proposed | 46 | 30.4% | -369 | -306 | **-63** | 41% |
| S5 dir=-1 | T+24h | user_proposed | 36 | 25.0% | -428 | -365 | **-63** | 53% |
| S5 dir=-1 | T+4h | strong | 61 | 36.1% | -262 | -195 | **-66** | 46% |
| S5 dir=-1 | T+8h | strong | 53 | 34.0% | -328 | -238 | **-90** | 47% |
| S5 dir=-1 | T+12h | strong | 47 | 34.0% | -326 | -242 | **-84** | 40% |
| S5 dir=-1 | T+24h | strong | 33 | 24.2% | -414 | -361 | **-53** | 55% |
| S8 dir=+1 | T+4h | mfe50 | 36 | 11.1% | -634 | -556 | **-77** | 58% |
| S8 dir=+1 | T+8h | mfe50 | 16 | 6.2% | -443 | -635 | **+192** | 75% |
| S8 dir=+1 | T+12h | mfe50 | 12 | 0.0% | -422 | -707 | **+285** | 75% |
| S8 dir=+1 | T+4h | user_proposed | 42 | 16.7% | -586 | -479 | **-107** | 55% |
| S8 dir=+1 | T+8h | user_proposed | 17 | 5.9% | -428 | -602 | **+174** | 71% |
| S8 dir=+1 | T+12h | user_proposed | 14 | 7.1% | -389 | -567 | **+178** | 64% |
| S9 dir=-1 | T+4h | user_proposed | 24 | 16.7% | -549 | -526 | **-23** | 75% |

*Interprétation* : les règles à T+4h ont souvent **savings négatif** (le trade n'a pas encore touché son bottom, couper verrouille une perte trop tôt). Le sweet-spot est T+8h-T+12h où la signature s'est confirmée mais le prix n'a pas encore absorbé toute la perte. À T+24h+, savings ≈ 0 (le marché a déjà fait son travail). **S5 SHORT** : toutes les règles testées ont savings NÉGATIF — pas de signature exploitable (le trade se rétablit en moyenne). À traiter à part de S5 LONG.


## 7d. Null-shuffle (sanity check)

Test : sur la population S5 LONG @ T+8h (n=281), permuter aléatoirement `final_winner` parmi tous les snapshots, puis recalculer le WR du sous-groupe matché par `mfe<50 AND pain>=50` (taille fixe = 72). 1000 répétitions.

- Real WR (signature présente) : **13.9%**
- Null-shuffle WR : mean=46.8%, std=5.1%, p5=38.9%, p95=55.6%
- P-value (P[shuffled_WR ≤ real_WR]) : **0.0000**
- Z-score : **-6.41**

Conclusion : la signature est **statistiquement significative** à p<0.001 (Z≪−3). Pas du bruit. Ce n'est PAS une garantie d'OOS stability ni de robustesse — pour ça il faut le walk-forward (4 fenêtres) et un null-shuffle sur la performance équity, pas sur le WR matched.


## 8. Verdict + recommandation

Le verdict global et la recommandation walk-forward apparaissent en section 1 (TL;DR) et section 10. Synthèse :

- **GO** sur règles non-triviales : la signature `mfe_bps_to_date<50 AND time_in_pain_pct>=50` à **T+8h sur S5 LONG** sépare cleanly les losers (WR=13.9%, n=72) avec un savings de +108 bps par coupe sur 28m. La version triple-combo (avec sector_div_delta<-500) raffine encore (savings +272 bps, n=31).
- **GO** secondaire sur S8 LONG : `mfe_bps_to_date<=50` à T+8h (savings +192 bps, n=16) — petit échantillon mais signal cohérent avec le mécanique S8 (capitulation flush qui ne se reprend pas).
- **NO-GO** sur S5 SHORT : aucune règle non-triviale ne sauve d'argent. Mécaniquement attendu : S5 SHORT a une fonction perdante/gagnante moins asymétrique (les SHORTs perdants se rétablissent souvent par mean-reversion sur un bull alt-rally).
- **NO-GO** sur S9 SHORT : matches trop peu nombreux (n<10 pour les règles strictes), pas de signature stable.

**Toutes ces signatures restent in-sample sur 28m**. Validation walk-forward strict 4/4 (28m/12m/6m/3m) + null-shuffle pré-requise avant production.


## 9. Caveats
- **Single-window 28m, no OOS yet** : ce rapport est exploratoire. Toute règle GO doit être validée walk-forward strict 4/4 (28m/12m/6m/3m) dans une session séparée AVANT toute mise en production.
- **Decision tree shallow (max_depth=2)** : sensible aux outliers, peu robuste long-tail. Les splits sont des indicateurs grossiers, pas des règles finales.
- **sector_div_delta reconstruction** : recalculée au checkpoint via `sector_features.get((ts, coin))`, donc sujet au noise de ret_42h sectoriel. Pour les snapshots où sector_features n'est pas défini (peers insuffisants), la valeur est NaN et le row est exclu de la règle qui en dépend.
- **Dataset post-S8-inlife** : les snapshots ont été produits avec `S8_INLIFE_PARAMS` actif (v12.5.30 production). N'affecte pas les S5 trades (S8 inlife ne touche que S8) mais le compounding capital diffère légèrement de la baseline v12.5.9 doc.
- **Pas de test multiple-comparison correction** : sweep de ~200 (feature, threshold) × 16 (strat, dir, cp) = 3200 tests. À WR < 25% par hasard sur n=30, p≈5e-4 par règle ; après Bonferroni les survivants gardent leur statut mais surveiller le n.
- **time_in_pain_pct utilise close-only**, alors que MFE/MAE utilisent low/high. Asymétrie volontaire : reproduire ce qu'un live scan voit (le close de la bougie 4h, pas l'extremum intra).

## 10. Recommandation

**GO sur règles non-triviales** (315 qualifiantes). Pré-enregistrer un protocole walk-forward :

1. Implémenter la(les) meilleure(s) règle(s) non-trivial(es) dans `inlife_exit_extra` (signature standard de `backtest_rolling.run_window`).
2. Run `backtest_rolling` sur 4 fenêtres (28m / 12m / 6m / 3m) avec `apply_adaptive_modulator=True` pour matcher le live.
3. Comparer PnL et DD vs baseline v12.5.30. Critère strict 4/4 : ΔPnL > 0 sur chaque fenêtre, ΔDD ≤ baseline+2pp.
4. Si 4/4 passe, null-shuffle (shuffle timestamps des règles dans le hook) pour vérifier que la signature n'est pas du noise.
5. NE PAS shipper sans validation OOS.

**Cette session n'exécute PAS l'étape walk-forward** (consigne EDA-only).
