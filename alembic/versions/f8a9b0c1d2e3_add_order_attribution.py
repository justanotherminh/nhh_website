"""Add traffic-attribution columns to orders.

Records where a buyer came from, so ad spend can be judged against our own data
rather than Meta's self-reported conversions. See app/services/attribution.py.

All columns are nullable: most visitors arrive with no campaign parameters, and
NULL means "direct or unknown" rather than missing data.

Additive only: safe to run against the live database.

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f8a9b0c1d2e3"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("source", sa.String(length=60), nullable=True))
    op.add_column("orders", sa.Column("utm_campaign", sa.String(length=120), nullable=True))
    op.add_column("orders", sa.Column("utm_content", sa.String(length=120), nullable=True))
    op.add_column("orders", sa.Column("fbc", sa.String(length=255), nullable=True))
    op.add_column("orders", sa.Column("fbp", sa.String(length=120), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "fbp")
    op.drop_column("orders", "fbc")
    op.drop_column("orders", "utm_content")
    op.drop_column("orders", "utm_campaign")
    op.drop_column("orders", "source")
