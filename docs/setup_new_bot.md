# Guide : préparer un nouveau compte Hyperliquid (débutant)

Étapes pour obtenir **toutes les informations** nécessaires à la configuration d'un nouveau bot. La config technique du bot (`.env`, `start_bots.sh`) sera faite après par toi.

**À la fin de ce guide tu auras collecté** :
- L'adresse de ton **master wallet** MetaMask
- La **private key** + adresse de l'**API agent wallet** HL
- Le **token** et **chat_id** d'un nouveau bot Telegram
- Le **montant USDC** déposé sur HL

---

## Vue d'ensemble du pattern de sécurité

Tu vas créer **deux adresses Ethereum** distinctes :
- **Master wallet** (MetaMask) → **détient les USDC**. Tu signes manuellement les retraits.
- **API agent wallet** (généré par HL) → **signe les ordres trading** du bot. **Ne détient PAS les fonds.** Si la clé fuite : zéro vol possible, juste révoque l'agent dans HL.

Cette séparation est ce qui rend le setup sûr : la clé sur le serveur ne permet QUE de trader, pas de vider le compte.

---

## Prérequis

- [ ] MetaMask installé (extension navigateur)
- [ ] Seed phrase MetaMask **sauvegardée hors-ligne** (papier dans un coffre, pas dans un fichier sur ton PC)
- [ ] **Ne réutilise PAS** un wallet qui contient déjà tes économies → crée un compte dédié au trading (Menu MetaMask → « Ajouter un compte »)
- [ ] Capital USDC que tu es prêt à risquer (minimum $50 pour que le bot puisse trader, recommandé $300+ pour avoir de la marge)

---

## Étape 1 — USDC + ETH sur Arbitrum

Hyperliquid tourne sur **Arbitrum One** (Layer 2 d'Ethereum). Tu as besoin de :
- **USDC sur Arbitrum** → ton capital de trading
- **~$5 d'ETH sur Arbitrum** → frais de gas pour 5-10 transactions

### Option A — Achat direct sur CEX (le plus simple)

1. Sur Binance / Coinbase / OKX / Kraken, achète tes USDC
2. Retire en sélectionnant **Réseau : Arbitrum One** (PAS Ethereum, PAS BSC)
3. Adresse de destination : ton adresse MetaMask (commence par `0x...`)
4. Frais de retrait : ~$0.10 à $1
5. Pour l'ETH : achète $5 d'ETH, retire pareil sur Arbitrum One

> ⚠️ **PIÈGE CRITIQUE** : si tu sélectionnes le mauvais réseau, **les fonds sont perdus**. Vérifie 3 fois : Arbitrum One, et que l'adresse copiée commence bien par `0x`.

### Option B — Bridge depuis Ethereum mainnet

Si tu as déjà des USDC sur Ethereum mainnet :
1. Va sur [bridge.arbitrum.io](https://bridge.arbitrum.io) (ou [hop.exchange](https://hop.exchange), [stargate.finance](https://stargate.finance))
2. Connecte MetaMask
3. Bridge USDC Ethereum → Arbitrum
4. Coût : ~$15-25 de gas + petit fee
5. Délai : 10-15 minutes

### Vérifier dans MetaMask

1. Ouvre MetaMask
2. Ajoute le réseau Arbitrum One si pas déjà fait :
   - **Nom** : Arbitrum One
   - **RPC URL** : `https://arb1.arbitrum.io/rpc`
   - **Chain ID** : `42161`
   - **Symbol** : `ETH`
   - **Block Explorer** : `https://arbiscan.io`
3. Bascule sur Arbitrum One dans MetaMask
4. Confirme que tu vois ton USDC et ton ETH

---

## Étape 2 — Connexion à Hyperliquid

1. Va sur [app.hyperliquid.xyz](https://app.hyperliquid.xyz)
2. Clique **« Connect »** en haut à droite
3. Sélectionne **MetaMask**
4. Vérifie que tu es sur le **bon compte MetaMask** (celui dédié au bot)
5. MetaMask te demande de signer un message → signe (gratuit, pas de gas)
6. Si c'est ton premier login, HL te fait signer une seconde transaction pour activer le compte

> 📝 **À NOTER** : ton adresse MetaMask devient ton **master wallet HL**. Note-la quelque part.
>
> → **Info collectée n°1** : **Adresse master wallet** (`0x...`)

---

## Étape 3 — Déposer les USDC sur HL

1. Sur app.hyperliquid.xyz, clique **« Deposit »** (en haut)
2. HL te montre une adresse de dépôt (généralement la même que ton wallet)
3. Sur MetaMask, envoie ton USDC depuis ton wallet **vers cette adresse de dépôt** sur Arbitrum
4. Confirme la transaction (~$0.10-0.50 de gas)
5. Attends 1-5 minutes — ton solde HL apparaît
6. Vérifie sur le dashboard HL : tu dois voir ton USDC dans **« Spot »** ou **« Perps »**

> ⚠️ Si HL te demande de bridger via leur portail (`bridge.hyperliquid.xyz`), suis leur process — ils gèrent eux-mêmes la connexion Arbitrum.

> 📝 → **Info collectée n°2** : **Montant USDC déposé** (en dollars, e.g. $300)

---

## Étape 4 — Créer l'API agent wallet (le cœur de la sécu)

Cette étape est ce qui distingue un bot sûr d'un bot dangereux. **Lis tout avant de cliquer.**

1. Sur app.hyperliquid.xyz, va dans **Settings** (icône engrenage en haut à droite)
2. Onglet **« API »**
3. Clique **« Generate API Wallet »** (ou « Create new API Agent », selon version)
4. HL génère **une private key** (commence par `0x` suivie de 64 caractères hex)
5. **COPIE LA TOUT DE SUITE** dans un endroit sûr :
   - 1Password / Bitwarden / KeePass (recommandé)
   - **PAS** dans un fichier texte ou un email
   - **PAS** dans le navigateur (le bloc-notes ne compte pas comme « sûr »)
6. HL te montre aussi **l'adresse publique** de cet API wallet (commence par `0x...`, différente du master wallet). Copie-la aussi (utile pour debug).
7. Choisis la **période de validité** : default 180 jours (max 1 an). Tu devras régénérer après expiration.
8. **Confirme** — signature MetaMask requise + petit gas (~$0.20)

> 🔒 **Sécurité de l'API key** :
> - Elle peut **trader** (ouvrir/fermer positions) mais **PAS retirer** les fonds
> - Si elle fuite : va dans Settings → API → révoque l'agent. C'est instantané.
> - Tu peux toujours en regénérer une nouvelle. La master wallet reste intouchable.

> 📅 **Rappel d'expiration** : note dans ton calendrier la date d'expiration. Si tu oublies, le bot s'arrêtera de trader (les ordres seront rejetés).

> 📝 → **Info collectée n°3** : **Private key API agent** (`0x...` 64 chars)
> → **Info collectée n°4** : **Adresse publique API agent** (`0x...`, optionnel mais utile pour audit)
> → **Info collectée n°5** : **Date d'expiration** (pour calendrier)

---

## Étape 5 — Créer un bot Telegram

Pour recevoir les alertes du bot.

1. Sur Telegram, cherche `@BotFather` (compte officiel avec badge bleu)
2. Démarre la conversation et envoie `/newbot`
3. BotFather demande un **nom** (affiché aux utilisateurs) → e.g. « Mon trading bot »
4. BotFather demande un **username** unique terminant par `bot` → e.g. `mon_trading_xyz_bot`
5. BotFather répond avec ton **token** au format :
   ```
   123456789:AAH-abcDEF1234ghIJKlmnopQRSTUVWXYZab
   ```
6. **Copie le token** dans ton password manager

> 📝 → **Info collectée n°6** : **Token bot Telegram**

### Récupérer ton chat_id

1. Sur Telegram, ouvre la conversation avec ton nouveau bot (clique le lien fourni par BotFather, ou cherche son username)
2. Envoie n'importe quel message (e.g. `hello`)
3. Ouvre dans ton navigateur :
   ```
   https://api.telegram.org/bot<TON-TOKEN>/getUpdates
   ```
   (remplace `<TON-TOKEN>` par celui de l'étape précédente)
4. Tu vois un JSON. Cherche le bloc `"chat":{"id":12345678, ...}` — le nombre est ton **chat_id**

> 📝 → **Info collectée n°7** : **Chat_id Telegram** (nombre entier, e.g. `123456789`)

---

## Récap des informations à transmettre

À la fin de ce guide, tu dois avoir collecté :

| # | Info | Format | Source |
|---|---|---|---|
| 1 | Adresse master wallet | `0x...` (40 chars hex) | MetaMask |
| 2 | Montant USDC déposé | Nombre en $ (e.g. 300) | Toi |
| 3 | Private key API agent | `0x...` (64 chars hex) | HL Settings → API |
| 4 | Adresse publique API agent | `0x...` (40 chars hex) | HL Settings → API |
| 5 | Date d'expiration API agent | Date | HL Settings → API |
| 6 | Token bot Telegram | `123:ABC...` | @BotFather |
| 7 | Chat_id Telegram | Nombre (e.g. 123456789) | `/getUpdates` |

---

## Vérification finale avant transmission

- [ ] La seed phrase MetaMask est sauvegardée **hors ligne** (papier dans un coffre)
- [ ] La private key API agent est dans un **password manager** (pas dans un .txt, pas dans un email)
- [ ] Les USDC sont bien visibles sur ton dashboard HL
- [ ] Tu as testé le bot Telegram (au moins le `/getUpdates` retourne ton chat_id)
- [ ] Tu as noté la date d'expiration de l'API agent dans ton calendrier

---

## Pièges courants à éviter

| Erreur | Conséquence |
|---|---|
| USDC envoyé sur Ethereum mainnet au lieu d'Arbitrum | Fonds perdus |
| Confondre master wallet et API agent | Bot ne trade pas |
| Stockage de la private key dans un .txt sur le PC | Vol si malware |
| Oublier la date d'expiration | Bot arrête de trader silencieusement |
| Utiliser ton wallet MetaMask principal au lieu d'un wallet dédié | Risque de mélange fonds personnels / bot |

---

## Ressources

- Hyperliquid docs : [hyperliquid.gitbook.io](https://hyperliquid.gitbook.io)
- Arbitrum bridge officiel : [bridge.arbitrum.io](https://bridge.arbitrum.io)
- Coût gas Arbitrum en temps réel : [arbitrum.gas.now](https://arbitrum.gas.now) (souvent < $0.50)

---

**Temps estimé** : 30-60 minutes pour un débutant MetaMask, 15 min si tu maîtrises déjà. Le plus long est de **bouger les USDC sur Arbitrum** (retrait CEX + confirmations).
