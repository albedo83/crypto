"""Compression and retention (placeholder for Community Edition).

Revision ID: 004
Revises: 003
Create Date: 2025-01-01

Note: Compression and retention policies require TimescaleDB Community Edition.
Under Apache Edition, retention is managed via scheduled DROP CHUNKS calls
in the collector's health monitor.
"""
from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Apache Edition: compression and retention policies not available.
    # Retention is handled by scheduled drop_chunks() calls from the collector.
    # See src/collector/health.py -> _run_maintenance()
    pass


def downgrade() -> None:
    pass
