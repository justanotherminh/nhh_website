"""Pre-flight check before deploying the VIP-pool change. READ ONLY — changes nothing.

The migration that adds ``seats.is_vip`` backfills membership from
``scripts/data/vip_reserved_seats.csv``. That is exactly right if production's VIP
pool still matches the CSV, and this script is how you find out whether it does.

Run it BEFORE bringing the new containers up. ``entrypoint.sh`` applies migrations
on boot, so a rebuilt container has already migrated by the time you could exec into
it — the check has to run against the *currently running* (old) container.

That container doesn't have this file, so pipe it in over stdin. From the repo root
on the server, after ``git pull``:

    docker compose -f docker-compose.prod.yml exec -T app \
        python - < scripts/check_vip_migration.py

(``-T`` disables TTY allocation, without which the redirect won't attach.)

Deliberately compatible with a pre-migration database and an older app image: it
selects explicit columns rather than the Seat entity, so it never references the
is_vip column it exists to ask about, and it locates the CSV without ``__file__``.

Every section prints either OK or a list of seats needing a decision. Nothing here
blocks a deploy on its own — it tells you what the migration will and won't pick up.
"""
from __future__ import annotations

import csv
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Order, Seat, Ticket, VipTicket

_REL = Path("scripts") / "data" / "vip_reserved_seats.csv"


def _csv_path() -> Path:
    """Locate the reserved-seat CSV.

    Normally next to this file. The fallback is relative to the working directory
    (WORKDIR is /app in the image) so the script still works when it's piped in over
    stdin, where ``__file__`` isn't defined at all.
    """
    try:
        here = Path(__file__).resolve().parent / "data" / "vip_reserved_seats.csv"
        if here.is_file():
            return here
    except NameError:       # running from stdin
        pass
    return _REL


def _csv_keys() -> set[tuple[str, str, int]]:
    with open(_csv_path(), newline="", encoding="utf-8") as fh:
        return {
            (r["section"], r["row_label"], int(r["seat_number"]))
            for r in csv.DictReader(fh)
        }


def main() -> None:
    db = SessionLocal()
    try:
        want = _csv_keys()
        # Explicit columns, never `select(Seat)`: this script's whole purpose is to
        # run BEFORE the migration, against a database that has no is_vip column.
        # Selecting the ORM entity would emit it and fail on exactly the databases
        # we most need to inspect.
        seats = db.execute(
            select(Seat.id, Seat.section, Seat.row_label, Seat.seat_number,
                   Seat.label, Seat.status)
        ).all()
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

        # Seats a real buyer paid for; the migration skips these (see section 4).
        sold_ids = set(db.execute(
            select(Ticket.seat_id).join(Order, Ticket.order_id == Order.id)
            .where(Order.kind == "sale", Order.status == "paid")
        ).scalars())

        # 1. What the migration will actually mark.
        will_mark = [
            s for s in csv_seats
            if s.status in ("blocked", "booked") and s.id not in sold_ids
        ]
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
            select(VipTicket.pdf_filename, Seat.section, Seat.row_label,
                   Seat.seat_number, Seat.label)
            .join(Seat, VipTicket.seat_id == Seat.id)
        ).all()
        orphan_vip = [
            v for v in vip_rows
            if (v.section, v.row_label, v.seat_number) not in want
        ]
        print(f"[3] Generated invitation PDFs: {len(vip_rows)}")
        if orphan_vip:
            print(f"    ! {len(orphan_vip)} of them are on seats not in the CSV (e.g. from")
            print("      scripts/vip_test_ticket.py). They keep working and stay listed on")
            print("      'Vé đã tạo', but won't count as VIP seats:")
            for v in orphan_vip:
                print(f"      {v.label} -> {v.pdf_filename}")
        print()

        # 4. CSV seats booked by a real sale rather than an invitation.
        csv_sold = [s for s in csv_seats if s.id in sold_ids]
        print(f"[4] CSV seats sold to a real buyer: {len(csv_sold)}")
        if csv_sold:
            print("    These were sold before they were ever reserved, so the CSV entry is")
            print("    vestigial. The migration SKIPS them — they stay ordinary sold seats,")
            print("    and the buyer's ticket, check-in and refund path are untouched:")
            for s in csv_sold:
                print(f"      {s.label}")
            print("    -> Worth knowing operationally: someone intended these as invitation")
            print("       seats, so that guest may still need a seat found for them.")
        print()

        print("Dashboard note: the 'Ghế giữ cho vé mời' card counts VIP+blocked after")
        print(f"this deploy instead of every blocked seat. Expect it to read "
              f"{sum(1 for s in will_mark if s.status == 'blocked')}, "
              f"not {sum(1 for s in seats if s.status == 'blocked')}.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
