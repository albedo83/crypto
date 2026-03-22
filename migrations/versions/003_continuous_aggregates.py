"""Materialized views for dashboard (replaces continuous aggregates for Apache edition).

Revision ID: 003
Revises: 002
Create Date: 2025-01-01
"""
from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Regular materialized views instead of continuous aggregates
    # These will be refreshed by a scheduled task
    op.execute("""
    CREATE MATERIALIZED VIEW IF NOT EXISTS trades_1m AS
    SELECT
        time_bucket('1 minute', exchange_ts) AS bucket,
        venue_id,
        instrument_id,
        first(price, exchange_ts)   AS open,
        max(price)                  AS high,
        min(price)                  AS low,
        last(price, exchange_ts)    AS close,
        sum(qty)                    AS volume,
        sum(notional)               AS notional_volume,
        sum(CASE WHEN is_buyer_maker THEN qty ELSE 0 END)  AS sell_volume,
        sum(CASE WHEN NOT is_buyer_maker THEN qty ELSE 0 END) AS buy_volume,
        count(*)                    AS trade_count
    FROM trades_raw
    GROUP BY bucket, venue_id, instrument_id
    WITH NO DATA;

    CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_1m_pk ON trades_1m (bucket, venue_id, instrument_id);

    CREATE MATERIALIZED VIEW IF NOT EXISTS book_tob_1m AS
    SELECT
        time_bucket('1 minute', exchange_ts) AS bucket,
        venue_id,
        instrument_id,
        avg(spread_bps)     AS avg_spread_bps,
        max(spread_bps)     AS max_spread_bps,
        min(spread_bps)     AS min_spread_bps,
        avg(mid_price)      AS avg_mid_price,
        count(*)            AS update_count
    FROM book_tob
    GROUP BY bucket, venue_id, instrument_id
    WITH NO DATA;

    CREATE UNIQUE INDEX IF NOT EXISTS idx_book_tob_1m_pk ON book_tob_1m (bucket, venue_id, instrument_id);
    """)


def downgrade() -> None:
    op.execute("""
    DROP MATERIALIZED VIEW IF EXISTS book_tob_1m CASCADE;
    DROP MATERIALIZED VIEW IF EXISTS trades_1m CASCADE;
    """)
