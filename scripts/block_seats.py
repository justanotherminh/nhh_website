"""Bulk-reserve (or release) seats for the invitation pool.

A reserved seat has ``status='blocked'``: it can't be held or bought by the public
(both the seat map and the hold API require ``status='available'``), but it can still
be handed out as an invitation via the admin "Vé mời" page.

Usage (from the repo root, or inside the app container):

    # reserve seats — pass seat ids or exact labels as args
    python -m scripts.block_seats block 123 456
    python -m scripts.block_seats block "Tầng 1 – Hàng A – Ghế 5"

    # …or one identifier per line on stdin
    python -m scripts.block_seats block - < seats.txt

    # return seats to public sale
    python -m scripts.block_seats unblock 123 456

Only currently-available seats are reserved and only reserved seats are released;
sold seats are never touched. Unknown identifiers are reported and skipped.
"""
from __future__ import annotations

import sys

from sqlalchemy import select, update

from app.db import SessionLocal
from app.models import Seat


def _read_tokens(args: list[str]) -> list[str]:
    if args == ["-"]:
        raw = sys.stdin.read()
        return [line.strip() for line in raw.splitlines() if line.strip()]
    return [a.strip() for a in args if a.strip()]


def _resolve(db, tokens: list[str]) -> tuple[set[int], list[str]]:
    ids: set[int] = set()
    missed: list[str] = []
    for tok in tokens:
        seat = db.get(Seat, int(tok)) if tok.isdigit() else None
        if seat is None:
            seat = db.execute(select(Seat).where(Seat.label == tok)).scalar_one_or_none()
        (ids.add(seat.id) if seat else missed.append(tok))
    return ids, missed


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] not in ("block", "unblock"):
        print(__doc__)
        return 2

    action = argv[0]
    tokens = _read_tokens(argv[1:])
    if not tokens:
        print("No seat identifiers given.")
        return 2

    from_status, to_status = ("available", "blocked") if action == "block" else ("blocked", "available")

    db = SessionLocal()
    try:
        ids, missed = _resolve(db, tokens)
        changed = 0
        if ids:
            res = db.execute(
                update(Seat)
                .where(Seat.id.in_(ids), Seat.status == from_status)
                .values(status=to_status, held_by_cart=None, hold_expires_at=None)
            )
            db.commit()
            changed = res.rowcount
        verb = "Reserved" if action == "block" else "Released"
        print(f"{verb} {changed} seat(s) (from {len(ids)} matched, {from_status} -> {to_status}).")
        if missed:
            print(f"Not found ({len(missed)}): " + ", ".join(missed))
        skipped = len(ids) - changed
        if skipped:
            print(f"Skipped {skipped} matched seat(s) not in '{from_status}' status.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
