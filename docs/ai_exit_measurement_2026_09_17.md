# Mesure IA Live : défauts confirmés et remplacement requis

Audit en lecture seule depuis le 29 août 2026 : 336 événements LOCK en observation,
13 positions distinctes, aucun identifiant manquant. Toutes ces propositions portent
`note=lock_shadow`. Le filtre `note != "ok"` du scorecard les exclut avant rejeu.
Ce sont 336 observations répétées, pas 336 expériences indépendantes.

Le prompt `7193856e7d` est inchangé sur les versions 1.21.0/1.22.0/1.23.0 ;
les cohortes comptent respectivement 199/131/6 événements. Les positions traversant
les versions ne doivent pas être additionnées. Détails :
[rapport machine](ai_exit_coverage_2026_09_17.json).

## Pourquoi changer seulement le filtre serait incorrect

`replay_rules` reçoit un stop mais pas sa date de décision. Il applique donc ce stop
depuis l'entrée. Une baisse antérieure à la proposition peut déclencher une sortie
impossible dans le contrefactuel. En outre, `t > entry_ts_ms` saute la bougie contenant
l'entrée et `held=i+1` calcule l'âge par comptage des bougies plutôt que par horodatage.
Le rejeu utilise les paramètres actuels, les extrêmes OHLC, un BTC z figé, sans
funding ni reconstruction complète des planchers opposés. Il ne reproduit pas
fidèlement la gestion Live. Le libellé « preuve » n'est donc pas justifié.

Le scorecard existant pilote aussi un disjoncteur. Ce lot ne change pas ce dispositif
ni ne réarme l'IA. Le nouvel audit est indépendant, sans PNL contrefactuel inventé,
sans import de broker, sans Telegram, sans lecture de secrets ni écriture en base.

## Référence prospective : contrat d'implémentation

1. À l'entrée Live confirmée, enregistrer l'identité exacte, le prix et la taille
   exécutés, les frais, la configuration et son hash. Créer une branche règles seules.
2. Enregistrer les observations réellement fournies au moteur : timestamp, prix,
   funding, contexte BTC et signaux opposés ; maintenir séparément MAE/MFE,
   extensions et planchers de chaque branche.
3. À la décision IA, créer une branche intervention depuis l'état courant de la
   branche de référence. Un LOCK n'est actif qu'après cet instant, jamais depuis
   l'entrée. Identifier prompt, modèle, configuration et décision source.
4. Faire vivre les branches jusqu'à leur propre sortie, même après fermeture du
   trade réel. Une lacune d'observation rend le résultat incomplet ; aucune
   interpolation OHLC ne doit se transformer en résultat certain.
5. Publier le résultat local par position et les coûts modélisés séparément du
   PNL réel. Les effets sur slots, cooldowns, entrées et capital nécessitent un
   second rejeu de portefeuille : le résultat local ne les mesure pas.
6. Valider explicitement : stop non rétroactif, reprise sans doublons, lacunes,
   positions réouvertes du même symbole, funding et populations de prompts distinctes.

Cette référence n'est pas encore implémentée ni déployée. L'audit de couverture
constitue un diagnostic reproductible, pas son substitut.

Commande :
```sh
python3 audit_ai_exit_coverage.py --db /home/crypto/alfred/data/bots/live/bot.db --since 2026-08-29T00:00:00Z
```
