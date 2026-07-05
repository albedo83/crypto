# Vol-targeting — REJETÉ (2026-07-05)

Chantier « désigné coupable » par le MC joint (tilt 3m structurel). Scripts :
`eda_vol_targeting.py` (premise), `backtest_vol_targeting.py` (sweep, baseline
= v1.12.0 arrondie). Hook harnais : `size_fn_keep_modulator` (size_fn EN PLUS
du modulateur, pas à sa place — piège du guard btc_z corrigé).

## Premise-EDA : la vol est le CARBURANT, pas seulement le risque

- P1 ✓ la feature respire (vol_7d aux entrées : p90/p10 = 2.9×, pas d'épinglage).
- P2 ✓ le sizing actuel ne compense pas (Spearman(size, vol) ≈ 0 ; dispersion
  du risque-proxy par trade : 3.0×). La prémisse STRUCTURELLE du VT est vraie.
- P3 ✗ MAIS l'attente naïve est retournée : le tercile volatil est le PLUS
  RENTABLE — T3 +182.8 bps/trade (48 % du PnL 28m) vs T1 +82.6, à WR égal,
  malgré 4.5× plus de catastrophe stops (19.8 % vs 4.4 %). Un bot qui fade
  les extrêmes gagne SA VIE sur les tokens qui bougent.

Prédiction pré-enregistrée avant sweep : le VT perd du PnL, ne peut gagner
que par le DD. Confirmée — et même le DD ne suit pas.

## Sweep (3 variantes × 4 fenêtres, critère : PnL ≥ −2 % ET DD ≤ base)

| Variante | 28m | 12m | 6m | 3m | Verdict |
|---|---|---|---|---|---|
| VT_full (med/vol, clip 0.5-2) | −18.6 % PnL, DD **−14.7 pp PIRE** | +16.7 %, +1.8 | −34.8 %, −5.8 | −33.5 %, −3.5 | **1/4** |
| VT_half (√, clip 0.6-1.5) | −10.0 %, −1.8 | +13.0 %, +5.9 | −22.4 %, +1.3 | −16.2 %, +1.8 | **1/4** |
| VT_shrink (min(1, ·), assurance pure) | **−23.0 %**, +0.2 | −4.1 %, **+10.5** | −2.2 %, +1.2 | +5.2 %, +5.2 | **1/4** |

Lectures :
- **VT_full dégrade MÊME le DD sur 28m** (−14.7 pp) : booster les tokens
  calmes 2× concentre du notionnel et déplace le risque au lieu de le
  réduire. La théorie « égaliser = plus robuste » est fausse ici des deux
  côtés du Calmar.
- **VT_shrink** est une assurance de régime récent (12m DD +10.5 pp, 3m
  +5.2 %/+5.2 pp) payée −23 % de PnL plein-cycle — le même profil que le
  levier C bear-derisk (rejeté 2026-06) : un pari de régime, pas un edge.
- Le seul +PnL consistant (12m sur full/half) est la fenêtre-miroir — on
  sait maintenant lire ce signal-là.

## Conclusion

**Vol-targeting rejeté comme remplacement du sizing** (0 variante ≥ 2/4).
La pile multiplicative survit parce que ses « fudge factors » encodent des
priors de risque par stratégie ALIGNÉS avec l'edge (la vol est le carburant
du fade) — l'égalisation uniforme détruit exactement cette asymétrie. Le
tilt 3m du MC joint n'est PAS soignable par le sizing vol-aware ; il reste
attribué au régime (3m sans puissance, DSR ≤ 0.28). La réduction de DoF
continue par d'autres voies (l'arrondi v1.12.0 en a retiré 7 sans toucher
aux allocations).

Hooks conservés pour re-test futur : `size_fn` + `size_fn_keep_modulator`
(re-tester si le régime devient durablement bear OU si capital ×3 rend le
DD dominant dans l'utilité).

## Tampon chronologique (revue) — runs POST-FIX, preuve par reproduction

Le flag `size_fn_keep_modulator` a été ajouté AVANT l'écriture du sweep (le
sweep le passe explicitement). Preuve empirique a posteriori : VT_half 12m
re-run AVEC modulateur = **4050** (= le chiffre du sweep, à l'unité) ; SANS
modulateur = 3153. Δ = +898 $ : le bug silencieux aurait pénalisé VT de ~25 %
sur 12m — le harnais s'est auto-audité pendant le chantier qu'il aurait pu
fausser, et la chronologie est au dossier.

## Raffinements de lecture (revue, une voix chacun)

1. **Edge par unité de vol** : 183/83 ≈ 2.2× d'écart d'edge pour 2.9× de
   vol — l'edge/vol est en fait légèrement PLUS FAIBLE sur le tercile chaud.
   En théorie pure, VT aurait dû être ~neutre en dollars ; s'il perd 3/4,
   c'est que l'IMPLÉMENTATION domine l'idéal : le boost des calmes se fait
   clipper par le cap $500 et la clampe de marge pendant que le shrink des
   chauds s'applique plein pot. Asymétrie de plomberie, pas de maths.
2. **Physique du DD dégradé (la vraie trouvaille du sweep)** : VT suppose
   crash-beta ∝ vol trailing. Sur les alts, le flush est un FACTEUR COMMUN
   qui ignore les terciles — le token « calme » gonflé 2× tombe aussi dur
   quand tout corrèle à 1. La vol trailing prédit le bruit de croisière,
   pas la tempête. C'est pourquoi VT marche sur un book de futures
   diversifiés et DÉPLACE le risque ici.

## Pré-enregistrement — lecture de la concordance (écrit à 150/200 draws)

Grille verrouillée AVANT le résultat :
- **ρ(12↔28) haut ET ρ(3↔reste) bas** → le régime est MESURÉ : l'attribution
  du tilt 3m passe d'« par élimination » à « instrumentée ». Le KPI
  spread < 30 part en retraite (sa prémisse — mémorisation dans les params —
  est falsifiée) ; le spread résiduel est du seeing, on ne collimate pas
  l'atmosphère.
- **ρ haut partout** → paysages partagés, le tilt redevient une énigme,
  dossier rouvert avec surprise.
- **ρ bas partout** → l'instrument est noyé dans le bruit de n.

Caveats de lecture (posés d'avance) : le 3m a peu de trades → son ρ est
mécaniquement atténué même à paysage identique ; et 12m ⊂ 28m gonfle leur
concordance par inclusion — le couple propre serait 12m vs les 16 mois
d'AVANT (témoin non contaminé, run de suivi si besoin).

## Prédiction falsifiable enregistrée pour le chantier OI quadrants (revue)

Posée AVANT l'histogramme : au moment où S8 tire (DD < −40 %, flush), ΔOI
est « longs liquidés » quasi par construction du signal → feature dégénérée
attendue façon funding sur la patte S8. La variance qui vaut peut-être de
l'argent vit côté S9 : « prix chute + OI monte » (shorts frais, carburant de
squeeze) vs « prix chute + OI se vide » (capitulation) divergent vraiment.
