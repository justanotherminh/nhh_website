"""Add orders.lang (buyer's checkout language, for the e-ticket email).

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default backfills every existing order to Vietnamese, matching prior
    # behaviour (all confirmation emails were Vietnamese before this change).
    op.add_column(
        "orders",
        sa.Column("lang", sa.String(length=5), nullable=False, server_default="vi"),
    )


def downgrade() -> None:
    op.drop_column("orders", "lang")
