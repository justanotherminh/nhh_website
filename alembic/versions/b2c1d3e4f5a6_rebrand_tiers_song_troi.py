"""Rebrand price tiers to the Sóng Trời water theme (rename only).

Renames the three tiers, keyed on ``price_vnd`` (stable) rather than the old names.
Colour is deliberately NOT stored — it's a front-end concern derived from price rank.

Revision ID: b2c1d3e4f5a6
Revises: 911a6c45e35b
"""
from __future__ import annotations

from alembic import op

revision = "b2c1d3e4f5a6"
down_revision = "911a6c45e35b"
branch_labels = None
depends_on = None

# price_vnd -> (new_name, old_name)
_TIERS = [
    (700_000, "sông trời", "Loại 1"),
    (500_000, "dòng chảy", "Loại 2"),
    (300_000, "mạch nguồn", "Loại 3"),
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
