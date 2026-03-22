"""Initial schema - reference tables and hypertables.

Revision ID: 001
Revises: None
Create Date: 2025-01-01
"""
from alembic import op

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    -- Reference tables
    CREATE TABLE IF NOT EXISTS venues (
        venue_id    SERIAL PRIMARY KEY,
        name        TEXT NOT NULL UNIQUE,
        ws_url      TEXT NOT NULL,
        rest_url    TEXT NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE TABLE IF NOT EXISTS instruments (
        instrument_id   SERIAL PRIMARY KEY,
        venue_id        INT NOT NULL REFERENCES venues(venue_id),
        symbol          TEXT NOT NULL,
        base_asset      TEXT NOT NULL,
        quote_asset     TEXT NOT NULL,
        instrument_type TEXT NOT NULL DEFAULT 'perpetual',
        tick_size       NUMERIC NOT NULL DEFAULT 0.01,
        lot_size        NUMERIC NOT NULL DEFAULT 0.001,
        is_active       BOOLEAN NOT NULL DEFAULT true,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (venue_id, symbol)
    );

    -- Time-series tables (will become hypertables in next migration)
    CREATE TABLE IF NOT EXISTS trades_raw (
        exchange_ts     TIMESTAMPTZ NOT NULL,
        recv_ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
        venue_id        INT NOT NULL,
        instrument_id   INT NOT NULL,
        agg_trade_id    BIGINT NOT NULL,
        price           NUMERIC NOT NULL,
        qty             NUMERIC NOT NULL,
        first_trade_id  BIGINT,
        last_trade_id   BIGINT,
        is_buyer_maker  BOOLEAN NOT NULL,
        notional        NUMERIC NOT NULL
    );

    CREATE TABLE IF NOT EXISTS book_tob (
        exchange_ts     TIMESTAMPTZ NOT NULL,
        recv_ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
        venue_id        INT NOT NULL,
        instrument_id   INT NOT NULL,
        update_id       BIGINT NOT NULL,
        bid_price       NUMERIC NOT NULL,
        bid_qty         NUMERIC NOT NULL,
        ask_price       NUMERIC NOT NULL,
        ask_qty         NUMERIC NOT NULL,
        mid_price       NUMERIC NOT NULL,
        spread_abs      NUMERIC NOT NULL,
        spread_bps      NUMERIC NOT NULL
    );

    CREATE TABLE IF NOT EXISTS book_levels (
        exchange_ts     TIMESTAMPTZ NOT NULL,
        recv_ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
        venue_id        INT NOT NULL,
        instrument_id   INT NOT NULL,
        first_update_id BIGINT NOT NULL,
        last_update_id  BIGINT NOT NULL,
        bids_price      NUMERIC[] NOT NULL,
        bids_qty        NUMERIC[] NOT NULL,
        asks_price      NUMERIC[] NOT NULL,
        asks_qty        NUMERIC[] NOT NULL
    );

    CREATE TABLE IF NOT EXISTS mark_index (
        exchange_ts     TIMESTAMPTZ NOT NULL,
        recv_ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
        venue_id        INT NOT NULL,
        instrument_id   INT NOT NULL,
        mark_price      NUMERIC NOT NULL,
        index_price     NUMERIC NOT NULL,
        est_settle_price NUMERIC,
        funding_rate    NUMERIC,
        next_funding_ts TIMESTAMPTZ,
        basis_abs       NUMERIC NOT NULL,
        basis_bps       NUMERIC NOT NULL
    );

    CREATE TABLE IF NOT EXISTS funding (
        exchange_ts     TIMESTAMPTZ NOT NULL,
        recv_ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
        venue_id        INT NOT NULL,
        instrument_id   INT NOT NULL,
        funding_rate    NUMERIC NOT NULL,
        mark_price      NUMERIC NOT NULL,
        index_price     NUMERIC NOT NULL
    );

    CREATE TABLE IF NOT EXISTS open_interest (
        exchange_ts     TIMESTAMPTZ NOT NULL,
        recv_ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
        venue_id        INT NOT NULL,
        instrument_id   INT NOT NULL,
        open_interest   NUMERIC NOT NULL
    );

    CREATE TABLE IF NOT EXISTS liquidations (
        exchange_ts     TIMESTAMPTZ NOT NULL,
        recv_ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
        venue_id        INT NOT NULL,
        instrument_id   INT NOT NULL,
        side            TEXT NOT NULL,
        order_type      TEXT NOT NULL,
        orig_qty        NUMERIC NOT NULL,
        price           NUMERIC NOT NULL,
        avg_price       NUMERIC NOT NULL,
        status          TEXT NOT NULL,
        filled_qty      NUMERIC NOT NULL,
        notional        NUMERIC NOT NULL
    );

    -- Operational tables
    CREATE TABLE IF NOT EXISTS heartbeat (
        ts              TIMESTAMPTZ NOT NULL,
        collector_id    TEXT NOT NULL,
        ws_connected    BOOLEAN NOT NULL DEFAULT false,
        streams_active  INT NOT NULL DEFAULT 0,
        queue_depths    JSONB,
        memory_rss_mb   REAL,
        cpu_percent     REAL
    );

    CREATE TABLE IF NOT EXISTS collector_events (
        ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
        collector_id    TEXT NOT NULL,
        event_type      TEXT NOT NULL,
        severity        TEXT NOT NULL DEFAULT 'info',
        message         TEXT,
        details         JSONB
    );

    CREATE TABLE IF NOT EXISTS session_gaps (
        detected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        collector_id    TEXT NOT NULL,
        stream_name     TEXT NOT NULL,
        gap_start_ts    TIMESTAMPTZ,
        gap_end_ts      TIMESTAMPTZ,
        gap_duration_ms BIGINT,
        reason          TEXT
    );

    CREATE TABLE IF NOT EXISTS symbol_status (
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        venue_id        INT NOT NULL,
        instrument_id   INT NOT NULL,
        is_collecting   BOOLEAN NOT NULL DEFAULT false,
        last_trade_ts   TIMESTAMPTZ,
        last_book_ts    TIMESTAMPTZ,
        last_mark_ts    TIMESTAMPTZ,
        msg_rate_1m     REAL DEFAULT 0,
        latency_p50_ms  REAL DEFAULT 0,
        PRIMARY KEY (venue_id, instrument_id)
    );

    -- Seed venue
    INSERT INTO venues (name, ws_url, rest_url)
    VALUES ('binance_futures', 'wss://fstream.binance.com', 'https://fapi.binance.com')
    ON CONFLICT (name) DO NOTHING;

    -- Seed instruments
    INSERT INTO instruments (venue_id, symbol, base_asset, quote_asset, instrument_type, tick_size, lot_size)
    VALUES
        (1, 'BTCUSDT', 'BTC', 'USDT', 'perpetual', 0.10, 0.001),
        (1, 'ETHUSDT', 'ETH', 'USDT', 'perpetual', 0.01, 0.001),
        (1, 'ADAUSDT', 'ADA', 'USDT', 'perpetual', 0.00010, 0.1)
    ON CONFLICT (venue_id, symbol) DO NOTHING;
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS symbol_status CASCADE;
    DROP TABLE IF EXISTS session_gaps CASCADE;
    DROP TABLE IF EXISTS collector_events CASCADE;
    DROP TABLE IF EXISTS heartbeat CASCADE;
    DROP TABLE IF EXISTS liquidations CASCADE;
    DROP TABLE IF EXISTS open_interest CASCADE;
    DROP TABLE IF EXISTS funding CASCADE;
    DROP TABLE IF EXISTS mark_index CASCADE;
    DROP TABLE IF EXISTS book_levels CASCADE;
    DROP TABLE IF EXISTS book_tob CASCADE;
    DROP TABLE IF EXISTS trades_raw CASCADE;
    DROP TABLE IF EXISTS instruments CASCADE;
    DROP TABLE IF EXISTS venues CASCADE;
    """)
