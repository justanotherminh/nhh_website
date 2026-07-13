"""Drop price_tiers.color_hex — colour is a front-end concern, not domain data.

Tier colours are now derived from price rank in the front-end palette (styles.css),
so the stored hex is redundant. Removing it means palette changes never touch the DB.

Revision ID: c3d4e5f6a7b8
Revises: b2c1d3e4f5a6
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c3d4e5f6a7b8"
down_revision = "b2c1d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("price_tiers", "color_hex")


def downgrade() -> None:
    op.add_column(
        "price_tiers",
        sa.Column("color_hex", sa.String(length=7), nullable=False, server_default="#cccccc"),
    )