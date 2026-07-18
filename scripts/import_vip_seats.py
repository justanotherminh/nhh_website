"""Reserve the VIP / invitation seats listed in scripts/data/vip_reserved_seats.csv.

Those seats are set to ``status='blocked'`` — removed from public sale (the seat map
and the hold API both require 'available'), but still issuable as free invitations
via the admin "Vé mời" page. Run it after importing the seat map.

The reserved list was extracted from the greyed-out cells of the masterplan's
"Sơ đồ hạng vé" tab (145 seats). Regenerate it with --regen <file.xlsx> if that
map changes (dev only; the workbook is not committed).

Run inside the app container:
    python -m scripts.import_vip_seats            # block the listed seats
    python -m scripts.import_vip_seats --unblock  # release them back to sale
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

from sqlalchemy import select, update

from app.db import SessionLocal
from app.models import Seat

CSV_PATH = Path(__file__).resolve().parent / "data" / "vip_reserved_seats.csv"


def _load_list() -> list[tuple[str, str, int]]:
    with open(CSV_PATH, newline="", encoding="utf-8") as fh:
        return [
            (r["section"], r["row_label"], int(r["seat_number"]))
            for r in csv.DictReader(fh)
        ]


def reserved_seat_ids(db) -> set[int]:
    """Seat ids of the VIP-reserved seats (from the CSV), whatever their status.

    VIP membership is defined by the CSV, not by seat status — a reserved seat may
    be 'blocked' (not yet issued) or 'booked' (its ticket exported), and both count.
    """
    from sqlalchemy import select

    from app.models import Seat

    want = _load_list()
    if not want:
        return set()
    rows = db.execute(
        select(Seat.id, Seat.section, Seat.row_label, Seat.seat_number)
    ).all()
    wanted = set(want)
    return {sid for sid, sec, rl, num in rows if (sec, rl, num) in wanted}


def _regen(xlsx: str) -> None:
    """Dev-only: rebuild the CSV from a masterplan workbook's greyed cells."""
    import openpyxl

    from scripts.import_seatmap import AF, classify

    ws = openpyxl.load_workbook(xlsx, data_only=True)["Sơ đồ hạng vé"]
    row_letters = {
        r: ws.cell(r, AF).value.strip()
        for r in range(1, ws.max_row + 1)
        if isinstance(ws.cell(r, AF).value, str) and ws.cell(r, AF).value.strip()
    }
    seats = set()
    for row in ws.iter_rows():
        for cell in row:
            f = cell.fill
            rgb = f.fgColor.rgb if (f and f.fgColor and f.fgColor.type == "rgb") else None
            if rgb == "FFCCCCCC" and isinstance(cell.value, (int, float)):
                sec, rl = classify(cell.column, cell.row, row_letters)
                seats.add((sec, rl, int(cell.value)))
    rows = sorted(seats)
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["section", "row_label", "seat_number"])
        w.writerows(rows)
    print(f"Regenerated {CSV_PATH} with {len(rows)} reserved seats.")


def run(unblock: bool = False) -> None:
    reserved = _load_list()
    from_status, to_status = ("blocked", "available") if unblock else ("available", "blocked")

    db = SessionLocal()
    try:
        changed = booked = missing = 0
        for sec, rl, num in reserved:
            seat = db.execute(
                select(Seat).where(
                    Seat.section == sec, Seat.row_label == rl, Seat.seat_number == num
                )
            ).scalar_one_or_none()
            if seat is None:
                missing += 1
                print(f"  ! not found: {sec} – {rl} – {num}")
                continue
            if seat.status == "booked":
                booked += 1  # never touch a sold seat
                print(f"  ! already booked, skipped: {seat.label}")
                continue
            if seat.status == from_status:
                db.execute(
                    update(Seat).where(Seat.id == seat.id).values(
                        status=to_status, held_by_cart=None, hold_expires_at=None
                    )
                )
                changed += 1
        db.commit()
        verb = "Released" if unblock else "Reserved"
        print(f"{verb} {changed}/{len(reserved)} seats "
              f"({from_status} -> {to_status}).")
        if booked:
            print(f"  {booked} already booked (left untouched).")
        if missing:
            print(f"  {missing} not found in the seat map.")
    finally:
        db.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--regen":
        _regen(args[1])
    else:
        run(unblock="--unblock" in args)
