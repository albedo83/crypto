# Re-validation des verdicts récents après réparation de la série OI

> **Étude de MESURE**, explicitement autorisée par la clause de clôture du
> 2026-08-02 (`docs/scan_sizing_verdict.md` § 6) : elle ne cherche aucun edge,
> elle vérifie que des verdicts déjà rendus survivent à une correction
> d'entrées.
>
> **UNE question, lecture unique : les verdicts comparatifs tiennent-ils ?**

**Généré le** : 2026-08-02 · empreinte git `e0d957e+` · inputs issus des
décisions 1 et 2 : comblage OI validé 33/33 (`docs/oi_backfill_protocol.md`
§ A5) et politique `block_stale` (`CLAUDE.md`).

## 0. Ce qui a changé dans les entrées

1. **Garde d'âge côté backtest** — `oi_delta_24h_bps` rend désormais `None`
   au-delà de 4 h d'obsolescence au lieu de la dernière valeur connue. Effet sur
   **toute la longueur** de la série, pas seulement après son arrêt : les trous
   internes étaient rebouchés en silence.
2. **Comblage** — 33 fichiers, +290 points, soudure au 2026-06-15 08:00,
   couverture jusqu'au 2026-08-02. TON intact.
3. **`block_stale`** — la gate OI LONG bloque sur source périmée, laisse passer
   au démarrage à froid.

## 1. (a) Référence régénérée

`docs/backtests.md` réémis. Anciens chiffres archivés et annotés dans
`docs/backtests_pre_oi_repair.md`.

| fenêtre | capital avant | **après** | Δ | **DD avant** | **DD après** | n avant | n après |
|---|---:|---:|---:|---:|---:|---:|---:|
| 28 mois | $15 463 | **$14 255** | −7,8 % | **−51,4 %** | **−51,4 %** | 1309 | 1308 |
| 12 mois | $3 425 | **$3 183** | −7,1 % | **−31,3 %** | **−31,3 %** | 565 | 568 |
| 6 mois | $1 339 | **$1 217** | −9,1 % | **−31,3 %** | **−31,3 %** | 316 | 315 |
| 3 mois | $1 215 | **$1 225** | +0,8 % | **−29,9 %** | **−29,9 %** | 168 | 169 |
| 1 mois | $1 135 | $964 | −15,1 % | −6,1 % | −9,2 % | 52 | 50 |

> ### Le drawdown n'a pas bougé d'un dixième de point sur quatre fenêtres sur cinq.
>
> **Troisième péremption d'absolus en un mois** — après la parité des secteurs
> et le booking des trails. Et pour la troisième fois, ce sont les **rendements
> terminaux** qui se déplacent pendant que les **drawdowns** restent en place.
>
> La doctrine « **on pilote au drawdown, pas au rendement terminal** » n'est pas
> une posture de prudence : c'est ce que les données font à chaque réparation.
> Un rendement de backtest long est un nombre fragile — un seul trade d'écart au
> départ le déplace de 17 % sur 28 mois (`docs/dd_anatomy.md` § 5). Le drawdown
> précoce, lui, ne bouge pas.
>
> Elle est rappelée **en tête** de l'archive, pas en note de bas de page.

Seule la fenêtre d'un mois voit son DD bouger (−6,1 → −9,2 %) — 50 trades, la
fenêtre où un trade pèse le plus.

## 2. (b) Re-jeu des études comparatives

### 2.1 Prémisse « agitation du scan » (Phase A0 du 2026-08-01)

| | avant | **après** |
|---|---|---|
| effet de même sens | 4/4 fenêtres | **4/4 fenêtres** |
| **verdict** | CONFIRMÉE | **CONFIRMÉE** |

**IDENTIQUE.**

### 2.2 S5 — retrait, sizing, quartiles (2026-07-31)

**Étude 1 — retrait** (P&L gagnés sur 4, slippage 4 bps) :

| variante | avant | **après** |
|---|---|---|
| S5 entier | 2/4 | **2/4** |
| S5 LONG | 1/4 | **1/4** |
| S5 SHORT | 3/4 | **3/4** |

Détail du retrait entier : avant `+282 / −110 / −204 / +185`, après
`+389 / −89 / −104 / +164`. Les montants bougent, **les signes et les comptes
non**.

**Étude 2 — sizing** :

| `signal_mult["S5"]` | P&L avant | **P&L après** | DD meilleur avant | **après** |
|---|---|---|---|---|
| 1.5 | 2/4 | **3/4** | 4/4 | **4/4** |
| 1.0 | 2/4 | **3/4** | 4/4 | **4/4** |

Le sizing réduit gagne **une fenêtre de plus** qu'avant — mais la clause exige
**4/4 en P&L ET DD meilleur partout** pour réveiller v1.17.0. 3/4 ne l'est pas.
La clause de repli s'applique inchangée : *« DD meilleur mais P&L perdu sur ≥ 1
fenêtre → statu quo à 3.0 »*.

**Étude 3 — quartiles de force** (net moyen, 28m / 12m / 6m) :

| quartile | avant | **après** |
|---|---|---|
| Q1 (force faible) | +99,3 / +78,4 / +91,3 | **+85,0 / +52,4 / +68,1** |
| Q2 | −8,9 / +104,6 / −82,5 | −4,3 / +104,3 / −56,8 |
| Q3 | −49,1 / −130,8 / −148,1 | −44,4 / −100,5 / −92,4 |
| Q4 (force élevée) | −72,0 / −18,3 / −94,8 | −76,3 / −46,7 / −119,2 |

**Q1 reste le seul quartile positif sur les trois fenêtres.** Le fait qui
justifiait le test du plafond de force survit.

> ## VERDICT S5 : statu quo à `signal_mult["S5"] = 3.0` — **IDENTIQUE**

### 2.3 Mission 2 — modulateur d'agitation du scan (2026-08-01)

Preuve d'inertie sur la référence régénérée : **les deux jambes passent au
centime** (Δ 0,0000 à 0,0044). La modification moteur reste inerte sous les
nouvelles entrées.

| fenêtre | ΔP&L avant | **ΔP&L après** | |
|---|---:|---:|---|
| OOS-0 | +$1,79 ✓ | **−$10,68** ✗ |
| OOS-6 | −$303,80 ✗ | **−$308,47** ✗ |
| OOS-12 | +$132,50 ✓ | **+$140,39** ✓ |
| OOS-18 | −$33,48 ✗ | **−$88,18** ✗ |
| **score** | **2/4** | **1/4** |

Asymétrie du cap : 85,6 à 91,8 % des positions boostées écrêtées — inchangé.

> ## VERDICT Mission 2 : REFUS — **IDENTIQUE**, et durci (1/4 au lieu de 2/4)

## 3. (c) La réponse à la question unique

| étude | verdict avant | verdict après | |
|---|---|---|---|
| Prémisse agitation du scan (A0) | CONFIRMÉE 4/4 | CONFIRMÉE 4/4 | **identique** |
| S5 — retrait | pas de retrait | pas de retrait | **identique** |
| S5 — sizing | statu quo 3.0 | statu quo 3.0 | **identique** |
| S5 — quartiles | Q1 seul positif | Q1 seul positif | **identique** |
| Mission 2 — modulateur | REFUS (2/4) | REFUS (1/4) | **identique** |

### **Aucun verdict ne bascule. Cinq sur cinq tiennent.**

**Aucun n'a donc à repasser par le processus standard**, et aucune
requalification n'a lieu — ni ici, ni ailleurs.

## 4. Pourquoi ils tiennent — et ce que ça ne prouve pas

Ce n'est pas une coïncidence heureuse : dans chacune de ces études, **les deux
jambes de la comparaison partageaient la même distorsion**. La gate OI figée
s'appliquait identiquement à la ligne testée et à sa référence, donc la
différence — la seule chose que la clause lisait — en était largement protégée.

C'est ce que la preuve d'inertie de la Mission 2 montrait déjà au centime, et
c'est ce qui vient d'être **vérifié plutôt que supposé**.

**Ce que ça ne prouve pas** : que tout verdict comparatif soit immunisé. Une
étude dont l'objet aurait été la gate OI elle-même, ou dont les deux jambes
auraient consommé la donnée OI différemment, n'aurait aucune raison de
survivre. La démonstration porte sur ces cinq études-là.

## 5. Ce qui reste périmé

Toute référence de capital antérieure au 2026-08-02 et non régénérée ici. Les
**drawdowns**, eux, restent valides — c'est le § 1.
