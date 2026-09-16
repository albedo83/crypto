# max_macro_slots — re-sweep sur le moteur actuel (2026-09-16)

_Moteur Alfred v1.22.0, sémantique aligned, données jusqu'au 2026-09-16.
Capital $1 000. Baseline = 3 (production depuis le 2026-05-16)._

## Pourquoi re-mesurer

Le sweep du 2026-05-16 (`max_macro_sweep_results.md`) a fait passer le cap de
2 à 3. Sa base de preuve sur les fenêtres récentes était **deux trades** :

```
fenêtre 6 mois  →  S1 n = 2
fenêtre 3 mois  →  S1 n = 2
fenêtre 12 mois →  S1 n = 6
```

Depuis, quatre changements invalident cette base : univers élargi à 35 tokens
(v12.7.0), parité des secteurs corrigée (v1.17.1 — 8 tokens étaient invisibles
au backtest), cap notionnel proportionnel (v1.13.0), booking réaliste des
trails (v1.15.5). Et surtout : **S1 est devenu le signal dominant**. Mesuré
sur le live du 24/08 au 16/09, `max_macro` bloque **85 % des scans**
(121/143 côté SENIOR, 122/143 côté PAPER — à l'identique).

Conséquence opérationnelle : avec 3 places pour ~30 candidats S1 toutes les
4 h, le sous-ensemble retenu dépend uniquement de **quand une position se
libère**. Deux bots au code identique divergent alors fortement — écart
mesuré 117 $ sur deux mois, dont 86 % vient de trades non partagés
(`docs/bilan_2026_09.md` § 9).

## Critère

Repris **mot pour mot** du sweep de mai, sans renégociation :
- ΔPnL_pct > 0 sur **chacune** des 4 fenêtres
- avg ΔDD ≤ +1 pp

## Résultats

| slots | 28 mois | 12 mois | 6 mois | 3 mois | avg ΔDD | verdict |
|---:|---:|---:|---:|---:|---:|---|
| 2 | −563,7 | +48,1 | +8,2 | −2,5 | −1,38 pp | ✗ FAIL (2/4) |
| **3** | base | base | base | base | — | baseline |
| **4** | **+918,0** | **+5,9** | **+1,0** | **+13,0** | **+0,00 pp** | **✓ PASS 4/4** |
| 5 | +918,0 | +5,9 | +1,0 | +13,0 | +0,00 pp | identique à 4 |
| 6 | +918,0 | +5,9 | +1,0 | +13,0 | +0,00 pp | identique à 4 |

Nombre de trades S1 capturés :

| slots | 28 mois | 12 mois | 6 mois | 3 mois |
|---:|---:|---:|---:|---:|
| 3 | 87 | 28 | 28 | 25 |
| 4 | 112 | 36 | 36 | **32 (+28 %)** |

## Pourquoi le plateau à 4

`max_same_direction = 4` devient la contrainte mordante. Diagnostic direct —
ouvrir les deux verrous simultanément :

| config | 28 mois | 3 mois | S1 n (28m) |
|---|---:|---:|---:|
| slots=4 · same_dir=4 | +2645,3 % | **−8,3 %** | 112 |
| slots=6 · same_dir=6 | +3128,6 % | **−28,8 %** | 164 |

Le 28 mois s'améliore, le 3 mois se dégrade nettement : **`max_same_direction`
échoue le critère 4/4 et n'est donc pas touché.** Le plateau 4/5/6 n'est pas
une propriété de `max_macro_slots` mais l'ombre de cette seconde contrainte —
ce qui rend le choix de 4 non seulement parcimonieux mais mécaniquement
déterminé.

## Ce que ça corrige, et ce que ça ne corrige pas

**Corrige** : le bot capture ~28 % de signaux S1 en plus sur la fenêtre
récente, et la sélection devient moins arbitraire — le goulot passe de 3 à 4
places pour un flux inchangé.

**Ne corrige PAS** : la dépendance au chemin. Deux bots continueront de
diverger, moins fortement. Le plancher de bruit de la comparaison live-vs-paper
(±121 $ sur deux mois) baisse, il ne disparaît pas.

**Réserve honnête** : le régime actuel fait tirer S1 bien plus souvent que la
moyenne des 28 mois (10 trades en 10 jours sur le live, contre ~4/mois dans le
backtest). Le cap à 4 pourrait donc continuer à mordre en live plus que le
backtest ne le suggère. Le gain réel sera probablement supérieur au +13 pp du
3 mois — mais ce n'est pas mesuré, c'est déduit.
