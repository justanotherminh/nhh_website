"""Capitalise the price tier names.

They're proper names — "Sông Trời" is already capitalised in the wordmark and on
the printed invitations, so the tiers should match. Keyed on ``price_vnd``
(stable) rather than the old names.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
"""
from __future__ import annotations

from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None

# price_vnd -> (new_name, old_name)
_TIERS = [
    (700_000, "Sông Trời", "sông trời"),
    (500_000, "Dòng Chảy", "dòng chảy"),
    (300_000, "Mạch Nguồn", "mạch nguồn"),
]


def upgrade() -> None:
    conn = op.get_bind()
    for price, name, _old in _TIERS:
        conn.exec_driver_sql(
            "UPDATE price_tiers SET name = %s WHERE price_vnd = %s", (name, price)
        )


def downgrade() -> None:
    conn = op.get_bind()
    for price, _name, old in _TIERS:
        conn.exec_driver_sql(
            "UPDATE price_tiers SET name = %s WHERE price_vnd = %s", (old, price)
        )
