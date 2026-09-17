# CASCADE-v1 — rebond après contraction brutale de l’OI

**Verdict : pas de mise en service.** Cette définition ne passe pas le filtre
exploratoire fixé avant calcul des rendements. Cela n’invalide pas toute recherche
sur les liquidations, mais interdit de présenter cette variante comme validée.

## Idée

Après une bougie baissière combinant contraction extrême de l’open interest,
volume élevé et forte amplitude, les ventes forcées pourraient être suivies d’un
rebond. Le proxy ne prouve pas que des liquidations ont eu lieu. Il diffère d’un
simple achat après baisse, d’où une référence comparable par token et mois.

L’ancienne phase0 comptait les événements avec des percentiles sur toute la
fenêtre. CASCADE-v1 utilise les 720 heures STRICTEMENT antérieures ; ses comptes
ne sont donc pas directement comparables. Entrée une heure après la clôture de
signal, funding inclus, coûts aller-retour de 13 puis 25 bps. L’amplitude est
(high-low)/close, pas la longueur d’une mèche. Aucun paramètre optimisé après coup.

## Résultats

243 événements retenus après dé-clustering, répartis sur 82 journées d’entrée.
Les résultats ci-dessous donnent un poids égal à chaque journée, après moyenne
des tokens de la journée. Ce ne sont ni des rendements de portefeuille ni des
prévisions sur les 500 engagés. Les horizons se chevauchent et ne sont pas des
expériences statistiquement indépendantes.

| Horizon | Moyenne nette, coûts 13 bps | Moyenne nette, coûts 25 bps | Médiane nette, coûts 25 bps | Excès moyen vs référence |
|---|---:|---:|---:|---:|
| 4h | +0,079 % | −0,041 % | −0,249 % | +0,203 % |
| **24h — principal** | **+0,842 %** | **+0,722 %** | **−0,086 %** | **+0,654 %** |
| 72h — descriptif | +1,340 % | +1,220 % | −0,374 % | +0,558 % |

Au principal 24h, l’excès mensuel sur les simples bougies baissières vaut :
février +4,409 %, mars −0,430 %, avril −0,269 %, mai +0,413 %.
Seulement 2 mois sur 4 sont positifs, contre au moins 2/3 requis. La médiane
stressée est négative. Les cinq meilleures journées représentent plus que la
somme totale des rendements journaliers : la moyenne dépend d’une queue de
rebonds exceptionnels. La médiane positive était une condition préalable, pas
une règle ajoutée pour rejeter ce résultat.

## Limites et contre-vérification

- Cache horaire janvier–juin 2026 ; après warm-up et exigence de 720 heures OI
  sans lacune, seules des journées de février à mai sont évaluables. Juin est
  exclu pour historique roulant incomplet, pas pour mauvais rendement. Le détail
  des lacunes est publié. Cela réduit fortement la portée de la conclusion.
- Aucun résultat d’événement manquant parmi les 243 événements retenus aux
  trois horizons ; 729 lignes calculées avec prix et funding complets.
- Cinq tests ciblent la fuite de futur, l’OI périmé, l’entrée retardée, le funding,
  les lacunes et le regroupement des tokens corrélés par jour.
- Une implémentation scalaire indépendante a revérifié les 729 lignes : seuils,
  chronologie d’entrée, prix, funding et résultat net. Aucun écart.
- Univers actuel, échantillon court et déjà exploré, baseline mensuelle
  descriptive (elle n’est pas un signal tradable), exécution aux opens modélisée,
  funding sur notionnel d’entrée constant, aucun stop ni simulation de capacité.
- Ni taux mensuel, ni levier, ni somme des rendements ne doivent être extrapolés
  en bénéfice Live. Le test 72h ne remplace pas opportunément l’horizon principal.

## Reproduction

Version de recherche **CASCADE-v1**. Version du bot inchangée : 1.23.2.
Aucun redémarrage, aucun appel IA, aucun ordre nécessaire.

```sh
python3 -m backtests.events.snapshot_cascade_v1 --source-root /home/crypto --work-dir /tmp/cascade-reproduction
cp docs/cascade_v1_protocol.md /tmp/cascade-reproduction/protocol.md
.venv/bin/python3 -m unittest backtests.events.test_cascade_v1
.venv/bin/python3 -m backtests.events.cascade_v1 --work-dir /tmp/cascade-reproduction
.venv/bin/python3 -m backtests.events.verify_cascade_v1 --work-dir /tmp/cascade-reproduction
```

Une nouvelle copie de sources vivantes peut différer du snapshot original ;
comparer les hashes avant de parler de reproduction exacte.

Données : [résultats](cascade_v1_results.json),
[vérification indépendante](cascade_v1_verification.json),
[protocole préétabli](cascade_v1_protocol.md).
