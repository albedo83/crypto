# Bilan — Crypto Microstructure Data Collector

## Vue d'ensemble

Bot de collecte H24 de données microstructurelles crypto depuis Binance Futures (perpetuals).
Capture trades, orderbook, mark/index, funding, OI et liquidations pour BTC, ETH et ADA.
Dashboard web temps réel pour supervision et exploration des données.

---

## Architecture

```
Binance Futures WebSocket (1 connexion combinée, 15 streams)
        │
        ▼
┌─────────────────────────────────────────────┐
│  crypto-collector  (1 process asyncio)      │
│                                             │
│  WSManager ─── Dispatcher ─── Handlers (6)  │
│       │              │                      │
│       │              ▼                      │
│       │        BatchWriter (7 queues)       │
│       │              │                      │
│       │              ▼                      │
│  HealthMonitor    COPY bulk insert          │
│  OIPoller (REST)                            │
│  ControlListener (PG NOTIFY)                │
└─────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────┐
│  PostgreSQL 17 + TimescaleDB 2.18.2         │
│                                             │
│  9 hypertables (chunks 1 jour)              │
│  2 materialized views (OHLCV 1m, spread 1m) │
│  3 tables de référence                      │
│  4 tables opérationnelles                   │
└─────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────┐
│  crypto-dashboard  (FastAPI + uvicorn)      │
│                                             │
│  5 pages HTML (htmx + lightweight-charts)   │
│  9 endpoints API REST                       │
│  1 WebSocket live push                      │
└─────────────────────────────────────────────┘
        │
        ▼
   nginx reverse proxy (/crypto/)
```

---

## Flux Binance WebSocket

**URL unique (combined stream)** :
`wss://fstream.binance.com/stream?streams=...`

**15 streams** pour 3 symboles (5 par symbole) :

| Stream            | Description                  | Fréquence       |
|-------------------|------------------------------|-----------------|
| `aggTrade`        | Trades agrégés tick-by-tick  | Temps réel      |
| `bookTicker`      | Best bid/ask                 | Temps réel      |
| `depth10@100ms`   | Top 10 niveaux du carnet     | Toutes les 100ms|
| `markPrice@1s`    | Mark price, index, funding   | Toutes les 1s   |
| `forceOrder`      | Liquidations forcées         | Événementiel    |

**Open Interest** : poll REST toutes les 5 minutes (pas de WebSocket dispo).

---

## Composants du Collector

### WSManager (`ws_manager.py`)
- Connexion WebSocket unique via la librairie `websockets`
- Reconnexion automatique avec backoff exponentiel (1s → 60s max)
- Rotation proactive à 23h (avant la limite Binance de 24h)
- Ping/pong toutes les 20s pour détecter les déconnexions

### Dispatcher (`dispatcher.py`)
- Parse chaque message JSON via `orjson` (3-10x plus rapide que stdlib)
- Route selon le champ `e` (event type) vers le handler approprié
- Format combined stream : `{"stream": "...", "data": {...}}`

### Handlers (6 handlers dans `handlers/`)

| Handler           | Table cible     | Champs calculés                          |
|-------------------|-----------------|------------------------------------------|
| `TradesHandler`   | `trades_raw`    | `notional` = price × qty                 |
| `BookTobHandler`  | `book_tob`      | `mid_price`, `spread_abs`, `spread_bps`  |
| `BookLevelsHandler`| `book_levels`  | Arrays price/qty pour 10 niveaux         |
| `MarkIndexHandler`| `mark_index`    | `basis_abs`, `basis_bps` (mark - index)  |
|                   | + `funding`     | Détecte le changement de `next_funding_ts`|
| `LiquidationsHandler`| `liquidations`| `notional` = avg_price × filled_qty      |
| `OIPoller`        | `open_interest` | REST poll `/fapi/v1/openInterest`        |

### BatchWriter (`writer.py`)
- 1 `asyncio.Queue` par table cible (7 queues)
- Flush toutes les **250ms** : drain jusqu'à **500 records** par queue
- Écriture via `asyncpg` **COPY protocol** (`copy_records_to_table`) — 5-10x plus rapide qu'INSERT
- Backpressure : cap à **10 000 items** par queue, drop oldest si dépassé
- Sur erreur d'écriture : re-enqueue 1 fois, puis drop + log

### HealthMonitor (`health.py`)
- **Heartbeat** toutes les 5s : état WS, streams actifs, queue depths, mémoire RSS, CPU
- **Symbol status** toutes les 10s : dernier trade/book/mark par symbole, msg rate, latence
- **Refresh matviews** toutes les 60s : `trades_1m`, `book_tob_1m`
- **Retention** toutes les heures : `drop_chunks()` sur les hypertables

### ControlListener (`control.py`)
- Écoute le channel PostgreSQL `collector_control` via `LISTEN/NOTIFY`
- Permet au dashboard d'envoyer des commandes (restart WS, toggle symbole)
- Zero infrastructure supplémentaire (pas de Redis, pas de RabbitMQ)

---

## Schéma Base de Données

### Tables de référence
- `venues` — exchanges (1 row : binance_futures)
- `instruments` — symboles (3 rows : BTCUSDT, ETHUSDT, ADAUSDT)

### Hypertables (TimescaleDB, chunks 1 jour)

| Table            | Colonnes clés                                                   |
|------------------|-----------------------------------------------------------------|
| `trades_raw`     | exchange_ts, instrument_id, agg_trade_id, price, qty, is_buyer_maker, notional |
| `book_tob`       | exchange_ts, instrument_id, bid/ask price/qty, mid_price, spread_bps |
| `book_levels`    | exchange_ts, instrument_id, bids_price[], bids_qty[], asks_price[], asks_qty[] |
| `mark_index`     | exchange_ts, instrument_id, mark_price, index_price, funding_rate, basis_bps |
| `funding`        | exchange_ts, instrument_id, funding_rate, mark_price, index_price |
| `open_interest`  | exchange_ts, instrument_id, open_interest |
| `liquidations`   | exchange_ts, instrument_id, side, price, avg_price, filled_qty, notional |
| `heartbeat`      | ts, collector_id, ws_connected, queue_depths (JSONB), memory_rss_mb |
| `collector_events` | ts, event_type, severity, message, details (JSONB) |

### Tables opérationnelles
- `session_gaps` — trous détectés (déconnexion/reconnexion)
- `symbol_status` — état temps réel par symbole (last_trade_ts, msg_rate, latence)

### Materialized Views
- `trades_1m` — OHLCV + buy/sell volume + trade_count par minute
- `book_tob_1m` — avg/max/min spread_bps, avg mid_price par minute

### Retention
| Table            | Durée de rétention |
|------------------|--------------------|
| trades_raw       | 30 jours           |
| book_tob         | 30 jours           |
| book_levels      | 14 jours           |
| mark_index       | 30 jours           |
| funding          | 90 jours           |
| open_interest    | 30 jours           |
| liquidations     | 30 jours           |
| heartbeat        | 7 jours            |
| collector_events | 30 jours           |

---

## Dashboard Web

### Stack
- **FastAPI** + **Jinja2** (SSR) + **htmx** (refresh partiel) + **lightweight-charts** (graphiques)
- Pas de build JS, pas de framework frontend — ~14 KB de JS custom total
- Thème sombre type GitHub

### Pages

| Page       | URL           | Contenu                                              |
|------------|---------------|------------------------------------------------------|
| Overview   | `/`           | Prix temps réel, spread, health collector, volumes    |
| Streams    | `/streams`    | État des flux, toggle start/stop, restart WS          |
| Data       | `/data`       | Navigateur SQL (dernières lignes par table/symbole)   |
| Charts     | `/charts`     | Candlestick OHLCV, volume, spread, basis — 1h/6h/24h/7j |
| Alerts     | `/alerts`     | Événements collector + gaps de session                |

### API Endpoints
- `GET /api/status` — heartbeat + prix + spreads + volumes tables
- `GET /api/status/heartbeat` — dernier heartbeat
- `GET /api/streams` — état des flux par symbole
- `POST /api/streams/{symbol}/toggle` — activer/désactiver un symbole
- `POST /api/streams/restart-ws` — forcer reconnexion WS
- `GET /api/data/{table}` — browse données brutes
- `GET /api/metrics/ohlcv|spread|funding|oi|basis` — données pour charts
- `GET /api/alerts` — événements + gaps
- `WS /ws/live` — push heartbeat temps réel aux navigateurs

---

## Métriques Live (après ~23 minutes de collecte)

### Débits par symbole

| Symbole  | Trades/min | Latence P50 |
|----------|-----------|-------------|
| BTCUSDT  | ~983      | 135 ms      |
| ETHUSDT  | ~569      | 157 ms      |
| ADAUSDT  | ~39       | 248 ms      |

### Volumes collectés

| Table           | Rows      |
|-----------------|-----------|
| book_tob        | 633 467   |
| book_levels     | 34 021    |
| trades_raw      | 34 053    |
| mark_index      | 4 041     |
| heartbeat       | 273       |
| liquidations    | 23        |
| open_interest   | 18        |
| funding         | 0 (prochain event dans quelques heures) |

### Ressources

| Métrique                 | Valeur      |
|--------------------------|-------------|
| Mémoire collector        | 62 MB RSS   |
| Taille DB (23 min)       | 145 MB      |
| Estimation DB 24h        | ~9 GB       |
| Estimation DB 30j (avec retention) | ~35-45 GB |

---

## Stack Technique

| Composant       | Technologie                  | Version  |
|-----------------|------------------------------|----------|
| OS              | Ubuntu 25.04                 |          |
| Python          | 3.13.3                       |          |
| PostgreSQL      | 17.7                         |          |
| TimescaleDB     | 2.18.2 (Apache Edition)      |          |
| WebSocket       | `websockets`                 | ≥ 13.0   |
| DB driver       | `asyncpg` (COPY protocol)    | ≥ 0.30   |
| JSON            | `orjson`                     | ≥ 3.10   |
| REST client     | `aiohttp`                    | ≥ 3.10   |
| Web framework   | FastAPI + uvicorn             | ≥ 0.115  |
| Charts          | TradingView lightweight-charts| 4.2.1   |
| Frontend        | htmx + Jinja2 SSR            |          |
| Process manager | systemd                      |          |
| Reverse proxy   | nginx                        |          |
| Migrations      | Alembic (raw SQL)            | ≥ 1.14   |

---

## Déploiement

### Services systemd
- `crypto-collector.service` — restart automatique en 5s si crash, limit 512 MB RAM
- `crypto-dashboard.service` — restart automatique, limit 256 MB RAM, root-path `/crypto`

### Accès
- Dashboard : `https://echonym.fr/crypto/`
- API directe : `http://localhost:8090`

### Commandes
```bash
# Statut
systemctl status crypto-collector crypto-dashboard

# Logs temps réel
journalctl -u crypto-collector -f

# Arrêter/démarrer
systemctl stop crypto-collector
systemctl start crypto-collector

# Appliquer une migration
source .venv/bin/activate && alembic upgrade head
```

---

## Limitations actuelles (Apache Edition TimescaleDB)

- **Pas de compression** — les chunks ne sont pas compressés (feature Community Edition).
  Impact : la DB sera ~5-10x plus grosse qu'avec compression.
- **Pas de continuous aggregates** — les matviews `trades_1m` et `book_tob_1m` sont
  des materialized views standard, rafraîchies toutes les 60s par le collector.
- **Pas de retention policies automatiques** — gérées par le collector via `drop_chunks()` toutes les heures.

Pour débloquer ces features : installer TimescaleDB depuis le repo officiel timescale.com
(pas le paquet Ubuntu) pour obtenir la Community Edition.

---

## Structure du Projet

```
/home/crypto/
├── pyproject.toml              # Deps Python + entry points
├── alembic.ini                 # Config migrations
├── .env                        # Variables d'environnement
├── bilan.md                    # Ce fichier
├── migrations/
│   └── versions/
│       ├── 001_initial_schema.py
│       ├── 002_hypertables.py
│       ├── 003_continuous_aggregates.py
│       └── 004_compression_retention.py
├── src/
│   ├── config.py               # Pydantic settings
│   ├── collector/
│   │   ├── main.py             # Entry point
│   │   ├── engine.py           # Orchestrateur lifecycle
│   │   ├── ws_manager.py       # WebSocket connect/reconnect/rotation
│   │   ├── dispatcher.py       # Route messages → handlers
│   │   ├── writer.py           # BatchWriter (queues → COPY)
│   │   ├── health.py           # Heartbeat, matview refresh, retention
│   │   ├── control.py          # PG LISTEN pour commandes dashboard
│   │   └── handlers/
│   │       ├── trades.py       # aggTrade → trades_raw
│   │       ├── book_tob.py     # bookTicker → book_tob
│   │       ├── book_levels.py  # depth@100ms → book_levels
│   │       ├── mark_index.py   # markPrice → mark_index + funding
│   │       ├── liquidations.py # forceOrder → liquidations
│   │       └── open_interest.py# REST poll → open_interest
│   ├── dashboard/
│   │   ├── main.py             # uvicorn entry point
│   │   ├── app.py              # FastAPI factory + orjson serializer
│   │   ├── ws.py               # WebSocket live push
│   │   ├── routers/
│   │   │   ├── status.py       # /api/status
│   │   │   ├── streams.py      # /api/streams + toggle + restart
│   │   │   ├── data.py         # /api/data/{table}
│   │   │   ├── metrics.py      # /api/metrics/* (charts)
│   │   │   └── alerts.py       # /api/alerts
│   │   ├── templates/          # Jinja2 HTML (base + 5 pages)
│   │   └── static/             # CSS + JS (~14 KB)
│   └── shared/
│       ├── db.py               # asyncpg pool factory
│       ├── constants.py        # IDs, stream mappings
│       └── instruments.py      # Symbol → instrument_id registry
└── deploy/
    ├── crypto-collector.service
    ├── crypto-dashboard.service
    ├── nginx-crypto.conf
    └── setup.sh
```
