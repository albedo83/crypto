"""Paper trading tables for virtual bot.

Revision ID: 007
Revises: 006
Create Date: 2026-03-22
"""
from alembic import op

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS paper_trades (
        id              SERIAL PRIMARY KEY,
        ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
        symbol          TEXT NOT NULL,
        direction       TEXT NOT NULL,        -- LONG / SHORT
        entry_time      TIMESTAMPTZ NOT NULL,
        exit_time       TIMESTAMPTZ NOT NULL,
        entry_price     DOUBLE PRECISION NOT NULL,
        exit_price      DOUBLE PRECISION NOT NULL,
        hold_seconds    DOUBLE PRECISION,
        composite_score DOUBLE PRECISION,
        gross_pnl_bps   DOUBLE PRECISION,
        net_pnl_bps     DOUBLE PRECISION,
        cost_bps        DOUBLE PRECISION,
        reason          TEXT,
        filters         JSONB
    );

    CREATE TABLE IF NOT EXISTS paper_state (
        id              SERIAL PRIMARY KEY,
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        bot_running     BOOLEAN NOT NULL DEFAULT false,
        total_trades    INT DEFAULT 0,
        gross_pnl_bps   DOUBLE PRECISION DEFAULT 0,
        net_pnl_bps     DOUBLE PRECISION DEFAULT 0,
        win_rate        DOUBLE PRECISION DEFAULT 0,
        positions       JSONB DEFAULT '[]'::jsonb,
        signals         JSONB DEFAULT '{}'::jsonb
    );

    -- Seed initial state row
    INSERT INTO paper_state (bot_running) VALUES (false);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS paper_state CASCADE")
    op.execute("DROP TABLE IF EXISTS paper_trades CASCADE")
