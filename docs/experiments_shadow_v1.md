# Expériences prospectives — shadow-v1, version 1.24.0

Trois portefeuilles vides démarrent ensemble avec 500 USDT virtuels et les
paramètres de Live. Le témoin conserve les règles ; S1 prolonge une seule fois
de 24 h un S1 encore gagnant à son expiration, sous réserve des protections ;
cap3 refuse une quatrième position de même stratégie et même sens.
Chaque portefeuille gère son propre capital, ses positions et ses places libres.
Les expériences sont exclues du registre officiel, des ordres exchange, de
Telegram et des appels IA. Live et Paper continuent leurs opérations habituelles.

## Mesure

L'onglet Expériences affiche capital net simulé, réalisé/latent, baisse maximale,
positions, trades et écart au témoin. Les références Live/Paper utilisent leur
comptabilité interne avec latent brut ; elles partent de positions et capitaux
différents, donc leur écart ne mesure pas causalement l'effet des variantes.
L'abonnement assistant en EUR n'est pas soustrait à des comptes en USDT.

Frais 9 bps et glissement adverse 4 bps aller-retour. Funding : intégration du
taux précédent observé sur le notionnel d'entrée ; il s'agit d'une estimation,
pas du relevé des règlements exchange. Intervalles absents >120 s non imputés,
prix périmés signalés et comparaison invalidée. Liquidité illimitée, absence
de liquidation simulée, marge approchée : ces résultats ne sont pas exécutables.
Courbes échantillonnées à cinq minutes (12 000 points maximum).

## Exploitation

États et bases séparés : `alfred/data/experiments/shadow-v1/`. Le manifeste
`experiment.json` conserve dates, courbes, références et checkpoints. Une
reprise incohérente ou une modification des paramètres/code gèle uniquement
les expériences ; ne jamais effacer les états pour masquer cette alerte.
`ALFRED_EXPERIMENTS_ENABLED=0` désactive l'exécution au prochain redémarrage.
Les redémarrages incluent toujours Paper, Live, Junior et Baby.

Première revue calendaire à 28 jours : vérifier couverture, coûts, nombre de
trades, drawdown et contribution des entrées/sorties. Ni 28 jours ni un écart
positif isolé ne suffisent à valider un avantage durable. Aucun passage Live
automatique ; conserver et analyser aussi les campagnes défavorables.
