"""Add refunds ledger + tickets.voided_at (manual per-seat refunds).

Revision ID: a1b2c3d4e5f6
Revises: d0e1f2a3b4c5
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Voiding rather than deleting a refunded ticket keeps its check-in history
    # and lets the door distinguish "refunded" from a forged QR.
    op.add_column(
        "tickets",
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "refunds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("seat_id", sa.Integer(), sa.ForeignKey("seats.id"), nullable=False),
        sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id"), nullable=True),
        sa.Column("amount_vnd", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("refunded_by", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("note", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        # Refunding the same seat of the same order twice is a mistake, not a
        # second refund — the constraint is what makes the operation idempotent.
        sa.UniqueConstraint("order_id", "seat_id", name="uq_refund_order_seat"),
    )


def downgrade() -> None:
    op.drop_table("refunds")
    op.drop_column("tickets", "voided_at")
