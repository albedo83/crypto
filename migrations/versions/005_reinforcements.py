"""Reinforcements: aggressor_side, is_snapshot, book_levels_expanded view.

Revision ID: 005
Revises: 004
Create Date: 2026-03-15
"""
from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # D: aggressor_side on trades_raw (nullable, no backfill)
    op.execute("ALTER TABLE trades_raw ADD COLUMN aggressor_side TEXT")

    # C: is_snapshot on book_levels
    op.execute(
        "ALTER TABLE book_levels ADD COLUMN is_snapshot BOOLEAN NOT NULL DEFAULT false"
    )

    # E: normalized view for book_levels
    op.execute("""
        CREATE OR REPLACE VIEW book_levels_expanded AS
        SELECT
            bl.exchange_ts,
            bl.recv_ts,
            bl.venue_id,
            bl.instrument_id,
            bl.first_update_id,
            bl.last_update_id,
            bl.is_snapshot,
            'bid' AS side,
            ordinality::int AS level_idx,
            price,
            qty
        FROM book_levels bl,
             LATERAL unnest(bl.bids_price, bl.bids_qty)
                WITH ORDINALITY AS u(price, qty, ordinality)
        UNION ALL
        SELECT
            bl.exchange_ts,
            bl.recv_ts,
            bl.venue_id,
            bl.instrument_id,
            bl.first_update_id,
            bl.last_update_id,
            bl.is_snapshot,
            'ask' AS side,
            ordinality::int AS level_idx,
            price,
            qty
        FROM book_levels bl,
             LATERAL unnest(bl.asks_price, bl.asks_qty)
                WITH ORDINALITY AS u(price, qty, ordinality)
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS book_levels_expanded")
    op.execute("ALTER TABLE book_levels DROP COLUMN IF EXISTS is_snapshot")
    op.execute("ALTER TABLE trades_raw DROP COLUMN IF EXISTS aggressor_side")
