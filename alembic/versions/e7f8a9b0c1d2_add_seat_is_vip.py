"""Add seats.is_vip — move VIP pool membership from the CSV into the database.

Membership used to be defined by ``scripts/data/vip_reserved_seats.csv``, re-applied
on every container boot. Managers can now edit the pool from ``/admin/vip-seats``, so
a file re-applied each deploy would silently revert their changes. The CSV survives
only as the first-boot seed; this column is the source of truth from here on.

The backfill preserves exactly what was true before: every seat named in the CSV
becomes VIP, whether it is still 'blocked' (invitation not yet exported) or already
'booked' (exported) — status and membership are independent. If the CSV can't be
read, it falls back to marking currently-'blocked' seats, which is what the team has
in practice been treating as the VIP pool.

Additive only: safe to run against the live database.

Revision ID: e7f8a9b0c1d2
Revises: a1b2c3d4e5f6
"""
from __future__ import annotations

import csv
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "e7f8a9b0c1d2"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None

# alembic/versions/<this file> -> repo root
_CSV = Path(__file__).resolve().parents[2] / "scripts" / "data" / "vip_reserved_seats.csv"


def _csv_rows() -> list[tuple[str, str, int]]:
    """The reserved seats named in the CSV, or [] if it can't be read.

    Deliberately forgiving: a missing or malformed file must not break a deploy, it
    just sends the backfill down the status-based fallback path.
    """
    try:
        with open(_CSV, newline="", encoding="utf-8") as fh:
            return [
                (r["section"], r["row_label"], int(r["seat_number"]))
                for r in csv.DictReader(fh)
            ]
    except Exception as exc:  # noqa: BLE001 - see docstring
        print(f"[migration] Could not read {_CSV.name} ({exc!r}); "
              "falling back to status='blocked'")
        return []


def upgrade() -> None:
    op.add_column(
        "seats",
        sa.Column("is_vip", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    conn = op.get_bind()
    rows = _csv_rows()

    if rows:
        # Tuple IN, so one statement covers the whole list in one round trip.
        params: dict[str, object] = {}
        tuples = []
        for i, (section, row_label, seat_number) in enumerate(rows):
            params[f"s{i}"] = section
            params[f"r{i}"] = row_label
            params[f"n{i}"] = seat_number
            tuples.append(f"(:s{i}, :r{i}, :n{i})")
        # Only seats actually held back ('blocked') or already issued ('booked').
        #
        # A CSV seat sitting 'available' is, by observable state, on public sale —
        # someone ran --unblock, or the boot seeder hasn't re-blocked it yet. Marking
        # it VIP without also blocking it would leave a seat that is simultaneously
        # buyable and an invitation, which is the one combination nothing else in the
        # codebase expects. On a normal production database no CSV seat is
        # 'available' (the old entrypoint re-blocked them every boot), so this
        # narrowing is a no-op there and insurance everywhere else.
        result = conn.execute(
            sa.text(
                "UPDATE seats SET is_vip = true "
                f"WHERE (section, row_label, seat_number) IN ({', '.join(tuples)}) "
                "AND status IN ('blocked', 'booked')"
            ),
            params,
        )
        print(f"[migration] Marked {result.rowcount} of {len(rows)} CSV seats as VIP.")
    else:
        result = conn.execute(
            sa.text("UPDATE seats SET is_vip = true WHERE status = 'blocked'")
        )
        print(f"[migration] Marked {result.rowcount} blocked seats as VIP (fallback).")


def downgrade() -> None:
    op.drop_column("seats", "is_vip")
