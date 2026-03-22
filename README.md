# Crypto Microstructure Collector

Collecte de données de microstructure Binance Futures (trades, carnet d'ordres, funding, liquidations) + dashboard web temps réel.

## Services

```bash
# État des services
sudo systemctl status crypto-collector crypto-dashboard

# Logs en direct
journalctl -u crypto-collector -f
journalctl -u crypto-dashboard -f

# Redémarrer
sudo systemctl restart crypto-collector
sudo systemctl restart crypto-dashboard
```

## Base de données

```bash
# Connexion psql
sudo -u postgres psql -d crypto

# Migrations
cd /home/crypto && .venv/bin/alembic upgrade head
cd /home/crypto && .venv/bin/alembic history
```

### Requêtes utiles

```sql
-- Dernières données par table
SELECT 'trades_raw' AS t, count(*) FROM trades_raw WHERE exchange_ts > now() - INTERVAL '5 min'
UNION ALL SELECT 'book_tob', count(*) FROM book_tob WHERE exchange_ts > now() - INTERVAL '5 min'
UNION ALL SELECT 'book_levels', count(*) FROM book_levels WHERE exchange_ts > now() - INTERVAL '5 min'
UNION ALL SELECT 'mark_index', count(*) FROM mark_index WHERE exchange_ts > now() - INTERVAL '5 min'
ORDER BY 1;

-- Trades par symbole (dernière minute)
SELECT i.symbol, count(*) as trades, round(avg(t.price)::numeric, 2) as avg_price
FROM trades_raw t JOIN instruments i USING(instrument_id)
WHERE t.exchange_ts > now() - INTERVAL '1 minute'
GROUP BY i.symbol ORDER BY trades DESC;

-- Répartition BUY/SELL
SELECT i.symbol, t.aggressor_side, count(*)
FROM trades_raw t JOIN instruments i USING(instrument_id)
WHERE t.exchange_ts > now() - INTERVAL '5 min'
GROUP BY i.symbol, t.aggressor_side ORDER BY 1, 2;

-- Derniers snapshots REST du carnet
SELECT i.symbol, bl.exchange_ts, bl.last_update_id
FROM book_levels bl JOIN instruments i USING(instrument_id)
WHERE bl.is_snapshot = true
ORDER BY bl.exchange_ts DESC LIMIT 10;

-- Carnet normalisé (vue expanded)
SELECT side, level_idx, price, qty
FROM book_levels_expanded
WHERE instrument_id = 1
ORDER BY exchange_ts DESC, side, level_idx
LIMIT 20;

-- OHLCV 1 minute (matview)
SELECT bucket, close, volume, trade_count
FROM trades_1m
WHERE instrument_id = 1
ORDER BY bucket DESC LIMIT 10;

-- Spread moyen (matview)
SELECT bucket, avg_spread_bps, avg_mid_price
FROM book_tob_1m
WHERE instrument_id = 1
ORDER BY bucket DESC LIMIT 10;

-- Gaps de séquence détectés
SELECT * FROM session_gaps ORDER BY detected_at DESC LIMIT 10;

-- Événements collecteur
SELECT ts, event_type, severity, message FROM collector_events ORDER BY ts DESC LIMIT 20;

-- Taille de la base
SELECT pg_size_pretty(pg_database_size('crypto'));

-- Taille par table
SELECT hypertable_name, pg_size_pretty(hypertable_size(format('%I', hypertable_name)))
FROM timescaledb_information.hypertables ORDER BY hypertable_size(format('%I', hypertable_name)) DESC;

-- Latence WS par symbole
SELECT i.symbol,
       round(avg(extract(epoch from recv_ts - exchange_ts)*1000)::numeric, 1) as avg_ms,
       round(percentile_cont(0.99) WITHIN GROUP (ORDER BY extract(epoch from recv_ts - exchange_ts)*1000)::numeric, 1) as p99_ms
FROM trades_raw t JOIN instruments i USING(instrument_id)
WHERE t.exchange_ts > now() - INTERVAL '5 min'
GROUP BY i.symbol;
```

## Remise à zéro

```bash
# Arrêter les services
sudo systemctl stop crypto-collector crypto-dashboard

# Recréer la base
sudo -u postgres psql -c "DROP DATABASE crypto;"
sudo -u postgres psql -c "CREATE DATABASE crypto OWNER crypto;"
sudo -u postgres psql -d crypto -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"

# Rejouer les migrations
cd /home/crypto && .venv/bin/alembic upgrade head

# Redémarrer
sudo systemctl start crypto-collector crypto-dashboard
```

## Configuration

Fichier `.env` à la racine du projet. Variables principales :

| Variable | Description | Défaut |
|----------|-------------|--------|
| `SYMBOLS` | Paires à collecter (séparées par virgule) | `BTCUSDT,ETHUSDT,ADAUSDT` |
| `DB_HOST` / `DB_PORT` | Connexion PostgreSQL | `localhost` / `5432` |
| `HEARTBEAT_INTERVAL_S` | Intervalle heartbeat | `5` |
| `BATCH_FLUSH_MS` | Flush writer (ms) | `250` |
| `LOG_LEVEL` | Niveau de log | `INFO` |

## Architecture

```
Binance WS ──→ WSManager ──→ Dispatcher ──→ Handlers ──→ BatchWriter ──→ PostgreSQL
                                                              ↑
Binance REST ──→ OIPoller (5min) ───────────────────────────┘
             ──→ Book snapshots (5min + reconnect) ─────────┘

PostgreSQL ──→ Dashboard FastAPI ──→ htmx + lightweight-charts
           ←── PG NOTIFY (control commands)
```
