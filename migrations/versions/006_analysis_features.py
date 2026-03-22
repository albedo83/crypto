"""Analysis features: order flow & book imbalance matviews + SQL functions.

Revision ID: 006
Revises: 005
Create Date: 2026-03-22
"""
from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. order_flow_1m : buy/sell notional per minute ──────────────
    op.execute("""
    CREATE MATERIALIZED VIEW IF NOT EXISTS order_flow_1m AS
    SELECT
        time_bucket('1 minute', exchange_ts) AS bucket,
        venue_id,
        instrument_id,
        sum(CASE WHEN aggressor_side = 'BUY'  THEN notional ELSE 0 END) AS buy_notional,
        sum(CASE WHEN aggressor_side = 'SELL' THEN notional ELSE 0 END) AS sell_notional,
        sum(notional) AS total_notional,
        CASE
            WHEN sum(notional) > 0
            THEN (sum(CASE WHEN aggressor_side = 'BUY' THEN notional ELSE 0 END)
                - sum(CASE WHEN aggressor_side = 'SELL' THEN notional ELSE 0 END))
                / sum(notional)
            ELSE 0
        END AS ofi_ratio,
        sum(CASE WHEN aggressor_side = 'BUY'  THEN notional ELSE 0 END)
          - sum(CASE WHEN aggressor_side = 'SELL' THEN notional ELSE 0 END) AS net_flow,
        last(price, exchange_ts) AS close_price,
        count(*) AS trade_count
    FROM trades_raw
    WHERE aggressor_side IS NOT NULL
    GROUP BY bucket, venue_id, instrument_id
    WITH NO DATA;

    CREATE UNIQUE INDEX IF NOT EXISTS idx_order_flow_1m_pk
        ON order_flow_1m (bucket, venue_id, instrument_id);
    """)

    # ── 2. book_imbalance_1s : TOB imbalance per 5 seconds ──────────
    op.execute("""
    CREATE MATERIALIZED VIEW IF NOT EXISTS book_imbalance_1s AS
    SELECT
        time_bucket('5 seconds', exchange_ts) AS bucket,
        venue_id,
        instrument_id,
        avg(bid_qty / NULLIF(bid_qty + ask_qty, 0)) AS tob_bid_ratio,
        avg(spread_bps) AS avg_spread_bps,
        last(mid_price, exchange_ts) AS close_mid,
        count(*) AS tick_count
    FROM book_tob
    GROUP BY bucket, venue_id, instrument_id
    WITH NO DATA;

    CREATE UNIQUE INDEX IF NOT EXISTS idx_book_imbalance_1s_pk
        ON book_imbalance_1s (bucket, venue_id, instrument_id);
    """)

    # ── 3. book_depth_imbalance() : multi-level depth imbalance ─────
    op.execute("""
    CREATE OR REPLACE FUNCTION book_depth_imbalance(
        p_instrument_id INT,
        p_start TIMESTAMPTZ,
        p_end TIMESTAMPTZ,
        p_levels INT DEFAULT 10
    )
    RETURNS TABLE (
        exchange_ts TIMESTAMPTZ,
        bid_depth DOUBLE PRECISION,
        ask_depth DOUBLE PRECISION,
        imbalance_ratio DOUBLE PRECISION
    )
    LANGUAGE SQL STABLE AS $$
        SELECT
            bl.exchange_ts,
            (SELECT coalesce(sum(v), 0)
             FROM unnest(bl.bids_qty[1:p_levels]) AS v) AS bid_depth,
            (SELECT coalesce(sum(v), 0)
             FROM unnest(bl.asks_qty[1:p_levels]) AS v) AS ask_depth,
            CASE
                WHEN (SELECT coalesce(sum(v), 0) FROM unnest(bl.bids_qty[1:p_levels]) AS v)
                   + (SELECT coalesce(sum(v), 0) FROM unnest(bl.asks_qty[1:p_levels]) AS v) > 0
                THEN
                    (SELECT coalesce(sum(v), 0) FROM unnest(bl.bids_qty[1:p_levels]) AS v)
                    / ((SELECT coalesce(sum(v), 0) FROM unnest(bl.bids_qty[1:p_levels]) AS v)
                     + (SELECT coalesce(sum(v), 0) FROM unnest(bl.asks_qty[1:p_levels]) AS v))
                ELSE 0.5
            END AS imbalance_ratio
        FROM book_levels bl
        WHERE bl.instrument_id = p_instrument_id
          AND bl.exchange_ts BETWEEN p_start AND p_end
        ORDER BY bl.exchange_ts
    $$;
    """)

    # ── 4. liquidation_clusters() : temporal clustering ─────────────
    op.execute("""
    CREATE OR REPLACE FUNCTION liquidation_clusters(
        p_instrument_id INT DEFAULT NULL,
        p_start TIMESTAMPTZ DEFAULT '-infinity',
        p_end TIMESTAMPTZ DEFAULT 'infinity',
        p_gap_seconds INT DEFAULT 60
    )
    RETURNS TABLE (
        cluster_id INT,
        instrument_id INT,
        cluster_start TIMESTAMPTZ,
        cluster_end TIMESTAMPTZ,
        liq_count BIGINT,
        total_notional DOUBLE PRECISION,
        buy_count BIGINT,
        sell_count BIGINT,
        dominant_side TEXT
    )
    LANGUAGE SQL STABLE AS $$
        WITH ordered AS (
            SELECT
                l.exchange_ts,
                l.instrument_id,
                l.side,
                l.notional,
                CASE
                    WHEN l.exchange_ts - lag(l.exchange_ts) OVER (
                        PARTITION BY l.instrument_id ORDER BY l.exchange_ts
                    ) > make_interval(secs => p_gap_seconds)
                    THEN 1 ELSE 0
                END AS new_cluster
            FROM liquidations l
            WHERE (p_instrument_id IS NULL OR l.instrument_id = p_instrument_id)
              AND l.exchange_ts BETWEEN p_start AND p_end
        ),
        clustered AS (
            SELECT *,
                sum(new_cluster) OVER (
                    PARTITION BY instrument_id ORDER BY exchange_ts
                ) AS cluster_grp
            FROM ordered
        )
        SELECT
            row_number() OVER (ORDER BY min(exchange_ts))::int AS cluster_id,
            instrument_id,
            min(exchange_ts) AS cluster_start,
            max(exchange_ts) AS cluster_end,
            count(*) AS liq_count,
            sum(notional) AS total_notional,
            count(*) FILTER (WHERE side = 'BUY') AS buy_count,
            count(*) FILTER (WHERE side = 'SELL') AS sell_count,
            CASE
                WHEN count(*) FILTER (WHERE side = 'SELL')
                   > count(*) FILTER (WHERE side = 'BUY')
                THEN 'SELL' ELSE 'BUY'
            END AS dominant_side
        FROM clustered
        GROUP BY instrument_id, cluster_grp
        HAVING count(*) >= 2
        ORDER BY min(exchange_ts)
    $$;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS liquidation_clusters CASCADE")
    op.execute("DROP FUNCTION IF EXISTS book_depth_imbalance CASCADE")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS book_imbalance_1s CASCADE")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS order_flow_1m CASCADE")
