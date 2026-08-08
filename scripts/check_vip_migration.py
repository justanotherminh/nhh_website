"""Pre-flight check before deploying the VIP-pool change. READ ONLY — changes nothing.

The migration that adds ``seats.is_vip`` backfills membership from
``scripts/data/vip_reserved_seats.csv``. That is exactly right if production's VIP
pool still matches the CSV, and this script is how you find out whether it does.

Run it on the server, against production, BEFORE deploying:

    docker compose -f docker-compose.prod.yml exec app python -m scripts.check_vip_migration

Every section prints either OK or a list of seats needing a decision. Nothing here
blocks a deploy on its own — it tells you what the migration will and won't pick up.
"""
from __future__ import annotations

import csv
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Order, Seat, Ticket, VipTicket

CSV_PATH = Path(__file__).resolve().parent / "data" / "vip_reserved_seats.csv"


def _csv_keys() -> set[tuple[str, str, int]]:
    with open(CSV_PATH, newline="", encoding="utf-8") as fh:
        return {
            (r["section"], r["row_label"], int(r["seat_number"]))
            for r in csv.DictReader(fh)
        }


def main() -> None:
    db = SessionLocal()
    try:
        want = _csv_keys()
        seats = db.execute(select(Seat)).scalars().all()
        by_key = {(s.section, s.row_label, s.seat_number): s for s in seats}
        csv_seats = [by_key[k] for k in want if k in by_key]

        print(f"Seats in the hall: {len(seats)}")
        print(f"Seats named in the CSV: {len(want)} ({len(csv_seats)} matched in the DB)")
        missing = want - set(by_key)
        if missing:
            print(f"  ! {len(missing)} CSV rows match no seat — they will simply be skipped:")
            for k in sorted(missing):
                print(f"      {k[0]} – {k[1]} – {k[2]}")
        print()

        # 1. What the migration will actually mark.
        will_mark = [s for s in csv_seats if s.status in ("blocked", "booked")]
        skipped_available = [s for s in csv_seats if s.status == "available"]
        print(f"[1] Will be marked is_vip: {len(will_mark)}")
        print(f"      blocked (not yet issued): {sum(1 for s in will_mark if s.status == 'blocked')}")
        print(f"      booked  (already issued): {sum(1 for s in will_mark if s.status == 'booked')}")
        if skipped_available:
            print(f"    ! {len(skipped_available)} CSV seats are currently ON SALE and will")
            print("      NOT be marked VIP (they look sold-through-able, not held back):")
            for s in skipped_available:
                print(f"      {s.label}")
            print("      -> If these should be invitations, re-mark them at /admin/vip-seats")
            print("         after deploying.")
        print()

        # 2. Blocked seats the CSV doesn't know about — the real gap.
        orphan_blocked = [
            s for s in seats
            if s.status == "blocked" and (s.section, s.row_label, s.seat_number) not in want
        ]
        print(f"[2] Blocked seats NOT in the CSV: {len(orphan_blocked)}")
        if orphan_blocked:
            print("    These stay off public sale but will NOT be VIP, so the new admin")
            print("    page can neither issue invitations for them nor release them to")
            print("    sale (it only releases VIP seats). They need a decision:")
            for s in orphan_blocked:
                print(f"      {s.label}")
            print("    -> To sell them:  python -m scripts.block_seats unblock <id...>")
        print()

        # 3. Exported invitations whose seat the CSV never listed.
        vip_rows = db.execute(
            select(VipTicket).join(Seat, VipTicket.seat_id == Seat.id)
        ).scalars().all()
        orphan_vip = [
            v for v in vip_rows
            if (v.seat.section, v.seat.row_label, v.seat.seat_number) not in want
        ]
        print(f"[3] Generated invitation PDFs: {len(vip_rows)}")
        if orphan_vip:
            print(f"    ! {len(orphan_vip)} of them are on seats not in the CSV (e.g. from")
            print("      scripts/vip_test_ticket.py). They keep working and stay listed on")
            print("      'Vé đã tạo', but won't count as VIP seats:")
            for v in orphan_vip:
                print(f"      {v.seat.label} -> {v.pdf_filename}")
        print()

        # 4. CSV seats booked by a real sale rather than an invitation.
        sold_ids = set(db.execute(
            select(Ticket.seat_id).join(Order, Ticket.order_id == Order.id)
            .where(Order.kind == "sale", Order.status == "paid")
        ).scalars())
        csv_sold = [s for s in csv_seats if s.id in sold_ids]
        print(f"[4] CSV seats sold to a real buyer: {len(csv_sold)}")
        if csv_sold:
            print("    ! These were sold before being reserved. They'll be marked VIP but")
            print("      are genuinely someone's paid seat — do not issue invitations for them:")
            for s in csv_sold:
                print(f"      {s.label}")
        print()

        print("Dashboard note: the 'Ghế giữ cho vé mời' card counts VIP+blocked after")
        print(f"this deploy instead of every blocked seat. Expect it to read "
              f"{sum(1 for s in will_mark if s.status == 'blocked')}, "
              f"not {sum(1 for s in seats if s.status == 'blocked')}.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
