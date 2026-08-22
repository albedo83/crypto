# La décote de l'arbitre IA d'entrée — mesure et retrait

**Établi le** : 2026-08-22 · **portée** : SENIOR (argent réel), 77 trades clos
depuis le reset du 2026-07-09 · **décision** : retrait de la consigne `consec_up`
sur S5 LONG, coupe-circuit resserré. Aucune règle de `alfred/rules.py` touchée.

---

## 1. Le point de départ

Le compte affichait une ligne apparemment excellente :

| sortie | n | P&L | WR |
|---|---:|---:|---:|
| `manual_stop_set` (arbitre IA, LOCK) | 15 | **+84,26 $** | **100 %** |
| `timeout` | 54 | −99,55 $ | 33 % |
| `catastrophe_stop` | 5 | −55,72 $ | 0 % |

**Le 100 % est un artefact mécanique** : un LOCK pose un stop *au-dessus* du prix
d'entrée, donc il ne peut fermer qu'en gain. Il lui est structurellement impossible
d'afficher une perte. Le taux de réussite ne mesure rien.

## 2. Ce que la mesure a trouvé à la place

### 2.1 L'écart live-vs-paper est intégralement la décote

Live et paper tournent le **même code**, sur le **même capital** ($518,34), depuis
le **même reset**. Seule différence : la couche IA ne tourne que sur SENIOR.

| | |
|---|---:|
| P&L réalisé live | −66,52 $ |
| P&L réalisé paper | −50,35 $ |
| **écart** | **−16,17 $** |
| effet mesuré de la décote | **−15,86 $** |
| **résidu** | **0,31 $** |

Méthode : appariement des trades live et paper par (symbole, stratégie, direction,
±2 h). 70 trades appariés sur 77. Les ratios de taille ne sont pas du bruit — ils se
rangent en paliers discrets : 1.00, 0.85, 0.80, 0.75, 0.70, 0.60.

### 2.2 Deux méthodes indépendantes, le même chiffre

| méthode | périmètre | Δ |
|---|---|---:|
| appariement live/paper | 28 trades décotés, toutes stratégies | **−15,86 $** |
| `ai_arbiter_scorecard` (rejeu des règles) | 18 lignes S5 LONG hors veto | **−15,74 $** |

### 2.3 Le coût est **entièrement** dans S5 LONG

| bucket | n | Δ |
|---|---:|---:|
| décotes S5 LONG | 18 | **−16,32 $** |
| décotes hors S5 LONG (S5 SHORT, S10 SHORT) | 10 | **+0,46 $** |

Ce n'est donc pas la décote en soi qui est en cause : **c'est son motif.**

### 2.4 Le +12,62 $ du scorecard vient d'une configuration morte

Le scorecard global annonce « l'IA ajoute +12,62 $ ». Ce solde est porté par des
vetos à ±30-100 $ (AAVE −96,60, DYDX −38,72 et −29,25 côté perdant ; S5 SHORT
+110,96 côté gagnant). Or **les 9 vetos datent tous du 24/06 au 01/07** — avant le
reset, et avant que v1.15.0 ne convertisse veto → décote. **Zéro veto depuis.**

Sans le pire, le bucket S5 LONG vaut +0,69 $ ; sans les trois pires, +68,67 $. Un
solde que trois observations font basculer de ±100 $ ne mesure rien.

> Le scorecard n'était pas faux — il mélangeait deux régimes. Il faut le lire
> **par mécanisme actif**, pas en agrégat.

## 3. La semaine du bump BTC — la démonstration en trois trades

Du 19 au 22 août, BTC +20 %. Les trois meilleurs trades du déploiement :

| | paper (sans IA) | live (avec IA) | écart |
|---|---|---|---:|
| LINK S5 LONG | 140,6 $ · sortie 1305 b · **+18,35 $** | 84,2 $ (−40 %) · LOCK 864 b · **+7,29 $** | −11,06 |
| ARB S5 LONG | 138,7 $ · 989 b · **+13,72 $** | 97,8 $ (−30 %) · LOCK 600 b · **+5,83 $** | −7,89 |
| ENA S5 LONG | 133,9 $ · 2726 b · **+36,50 $** | 99,5 $ (−26 %) · LOCK 2420 b · **+24,02 $** | −12,48 |
| | | | **−31,43 $** |

Décotés à l'entrée, coupés à la sortie — **double peine sur les trois seuls trades
qui portaient la semaine**. Dans le même temps, GALA S5 SHORT, qui a perdu
−18,12 $, est passé à **taille pleine**.

## 4. La cause, et pourquoi elle était prévisible

La consigne retirée disait, mot pour mot, *« S5 LONG sans up-streak confirmé
(RÈGLE MESURÉE, haircut) … HAIRCUT par défaut (facteur ~0.5-0.7) »*, sur le critère
`consec_up` < 2.

**Cette règle avait déjà été testée en gate dur : walk-forward 0/4, REJETÉE**
(`memory/project_s5_long_reversal_2026_07.md`). Elle a ensuite été réintroduite dans
le prompt du LLM, où la grille de validation ne s'applique pas.

> ### Un prompt n'est pas une dérogation à la grille.
> C'est le vrai enseignement. Une règle refusée en walk-forward reste refusée quand
> on la reformule en consigne de langage naturel. Le chemin « le code l'a rejetée,
> mettons-la dans le prompt » doit être fermé explicitement.

**Le mécanisme, lui, vaut pour toute décote uniforme** : la distribution des trades
a une queue droite épaisse. Réduire linéairement la taille coupe l'espérance plus
vite que le risque, parce que **le risque est déjà borné par le stop catastrophe et
le gain ne l'est pas**. Sur les 28 décotes : +18,45 $ épargnés sur les perdants,
−34,31 $ abandonnés sur les gagnants.

## 5. Ce qui est changé

| | |
|---|---|
| `ai_entry_arbiter.py` | consigne `consec_up` **remplacée par une interdiction explicite** — supprimer le texte aurait laissé le modèle la réinventer. La décote reste légitime sur un **danger nommé** (force alignée token+BTC contre la position, book concentré, catalyseur), jamais sur l'absence d'une confirmation. |
| `.env` | `AI_ARBITER_CB_LOSS` −40 → **−20**. À −40 sur $518, le filet ne s'arme qu'après 7,7 % du capital ; il n'a pas bronché sur −15,86 $. |

`PROMPT_HASH` change → la modification est tracée dans chaque décision future.

**Rien d'autre.** `alfred/rules.py`, les backtests, le sizing, les signaux, la
chaîne de sorties : intacts. Paper, junior et baby : inchangés, et ils restent la
ligne de contrôle sans IA.

## 5 bis. Deux défauts trouvés en vérifiant, et réparés

La garde de fraîcheur a signalé l'arbitre d'entrée **BROKEN** au moment du
contrôle : dernier échec postérieur au dernier succès. **40 `ARBITER_FAILOPEN`
journalisés**, deux modes distincts :

| mode | occurrences | effet |
|---|---|---|
| `timeout>12.0s` | 6 depuis le 20/08 | l'arbitre n'est pas consulté → entrée à taille pleine |
| `BadRequestError 400` | 08-12 → 08-17 | idem |

1. **Aucun des 40 n'était diagnosticable.** La raison était tronquée à 80
   caractères, or un 400 Anthropic s'ouvre sur ~90 caractères de boilerplate :
   le message utile était **toujours** coupé. Porté à 300, dans les deux
   arbitres. C'est le même motif que les défauts silencieux de la campagne
   d'août — le dispositif journalisait consciencieusement une chaîne vide de
   sens.
2. **`AI_ARBITER_TIMEOUT` 12 → 25 s.** L'arbitre d'entrée batche ses candidats ;
   son prompt est plus lourd que celui de la sortie (qui, lui, ne time-out pas).

Le second point touche la mesure et mérite d'être dit : un arbitre à moitié
éteint produit des entrées à taille pleine **pour la mauvaise raison**. Sans ce
correctif, le contrôle du § 8 aurait pu afficher un bon ratio sans rien prouver.
Le relever rend le test plus sévère, pas moins.

## 6. Ce qui N'est PAS changé, et pourquoi

**L'arbitre de SORTIE reste en l'état.** Son bucket `LOCK S5 LONG` mesure −20,56 $
sur **n = 7** — même mécanisme, même direction, très probablement le même défaut.
Mais la grille de cette couche a été fixée à froid à **n ≥ 20**, et on ne s'accorde
pas une dérogation à une grille qu'on a soi-même posée. Elle est instrumentée, le
compteur monte, elle sera tranchée quand il atteindra 20.

**Une seule modification cette semaine**, aussi par hygiène de mesure : deux
changements simultanés rendraient le résultat inattribuable.

## 7. Ce que ça vaut, honnêtement

Le retrait rend au live la taille du paper. Sur les six semaines écoulées cela
aurait valu **+15,86 $** sur $518 — environ **+3 %**, soit ~11 $/mois au régime
actuel. Ce n'est pas un redressement : c'est l'arrêt d'une fuite identifiée, chiffrée
deux fois, et dont le mécanisme est compris.

Le paper, lui, reste **9,4 pp derrière son propre backtest** sur la même fenêtre.
Cet écart-là n'est PAS expliqué par la couche IA — il ne la subit pas. C'est le
dossier suivant, et il pèse plus lourd.

## 8. Le contrôle de la semaine prochaine

Critère, fixé **avant** d'observer quoi que ce soit :

- le ratio de taille médian live/paper sur les trades appariés doit remonter à
  **≥ 0,98** (il est à 0,998 hors décote, 0,60-0,85 sur les décotées) ;
- la part de décisions à facteur < 0,95 doit tomber sous **20 %** (elle est à 40 %) ;
- si le facteur reste bas malgré le retrait, c'est que le motif était ailleurs que
  dans la consigne — et il faudra passer `AI_ARBITER_MODE=shadow`.

Kill-switch complet, si besoin : `AI_ARBITER_MODE=shadow` dans `.env`
(l'arbitre continue de décider et de journaliser, sans agir).
Retour arrière intégral : `.env.bak.20260822` + `git revert`.
