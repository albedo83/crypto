"""Convert tables to TimescaleDB hypertables.

Revision ID: 002
Revises: 001
Create Date: 2025-01-01
"""
from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    SELECT create_hypertable('trades_raw',    'exchange_ts', chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);
    SELECT create_hypertable('book_tob',      'exchange_ts', chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);
    SELECT create_hypertable('book_levels',   'exchange_ts', chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);
    SELECT create_hypertable('mark_index',    'exchange_ts', chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);
    SELECT create_hypertable('funding',       'exchange_ts', chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);
    SELECT create_hypertable('open_interest', 'exchange_ts', chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);
    SELECT create_hypertable('liquidations',  'exchange_ts', chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);
    SELECT create_hypertable('heartbeat',     'ts',          chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);
    SELECT create_hypertable('collector_events', 'ts',       chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);

    -- Indexes for common query patterns
    CREATE INDEX IF NOT EXISTS idx_trades_raw_inst_ts ON trades_raw (instrument_id, exchange_ts DESC);
    CREATE INDEX IF NOT EXISTS idx_book_tob_inst_ts ON book_tob (instrument_id, exchange_ts DESC);
    CREATE INDEX IF NOT EXISTS idx_book_levels_inst_ts ON book_levels (instrument_id, exchange_ts DESC);
    CREATE INDEX IF NOT EXISTS idx_mark_index_inst_ts ON mark_index (instrument_id, exchange_ts DESC);
    CREATE INDEX IF NOT EXISTS idx_funding_inst_ts ON funding (instrument_id, exchange_ts DESC);
    CREATE INDEX IF NOT EXISTS idx_oi_inst_ts ON open_interest (instrument_id, exchange_ts DESC);
    CREATE INDEX IF NOT EXISTS idx_liquidations_inst_ts ON liquidations (instrument_id, exchange_ts DESC);
    CREATE INDEX IF NOT EXISTS idx_heartbeat_collector ON heartbeat (collector_id, ts DESC);
    CREATE INDEX IF NOT EXISTS idx_events_collector ON collector_events (collector_id, ts DESC);
    """)


def downgrade() -> None:
    # Cannot easily un-hypertable; drop and recreate would be needed
    pass
