"""Add vip_tickets (generated VIP invitation tickets + delivery state).

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vip_tickets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("seat_id", sa.Integer(), sa.ForeignKey("seats.id"),
                  nullable=False, unique=True),
        sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id"),
                  nullable=False),
        sa.Column("recipient_name", sa.String(length=200), nullable=False),
        sa.Column("pdf_filename", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("vip_tickets")
