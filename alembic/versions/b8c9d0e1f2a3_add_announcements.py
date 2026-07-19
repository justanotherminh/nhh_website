"""Add announcements and announcement_recipients.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "announcements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("subject", sa.String(length=300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False,
                  server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "announcement_recipients",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("announcement_id", sa.Integer(),
                  sa.ForeignKey("announcements.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("email", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False,
                  server_default="pending"),
        sa.Column("error", sa.String(length=300), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("announcement_id", "email", name="uq_announcement_email"),
    )
    # The sender polls for the next unsent recipient of an in-flight announcement.
    op.create_index(
        "ix_announcement_recipients_pending",
        "announcement_recipients",
        ["announcement_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_announcement_recipients_pending",
                  table_name="announcement_recipients")
    op.drop_table("announcement_recipients")
    op.drop_table("announcements")
