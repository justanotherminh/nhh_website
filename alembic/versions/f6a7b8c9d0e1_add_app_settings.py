"""Add app_settings key/value table for runtime-editable config (early-bird promo).

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=50), primary_key=True),
        sa.Column("value", sa.String(length=200), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
