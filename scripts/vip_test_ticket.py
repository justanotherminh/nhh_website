"""Create a throwaway VIP ticket for an end-to-end check-in test — no real seat.

It mints a comp ticket on a disposable seat (marked so it can be purged), runs the
real export path so a PDF lands in the depot and the ticket appears on the admin
"Vé đã tạo" page, and prints the check-in URL its QR encodes. Scan the QR from that
downloaded PDF (or open the printed URL) to exercise the real door check-in.

Run it on the SERVER, so the QR's URL (built from BASE_URL) is reachable from your
phone — a localhost URL won't resolve on a phone.

Usage (inside the app container):
    python -m scripts.vip_test_ticket            # create a test ticket + print details
    python -m scripts.vip_test_ticket --cleanup  # remove every test ticket this made

The disposable seat sits at an existing seat's coordinates, so it never expands or
shifts the public seat map while it exists; --cleanup removes it entirely.
"""
from __future__ import annotations

import secrets
import sys

from sqlalchemy import delete, select

from app.config import settings
from app.db import SessionLocal
from app.models import Order, OrderItem, PriceTier, Seat, Ticket, VipTicket
from app.services import tickets as tickets_svc
from app.services import vip

# Everything this script creates is tagged with these markers so --cleanup can find
# and remove it without touching any real data.
TEST_SECTION = "KIỂM TRA E2E"
TEST_TIER = "KIỂM TRA E2E"


def _throwaway_tier(db) -> int:
    tier = db.execute(
        select(PriceTier).where(PriceTier.name == TEST_TIER)
    ).scalar_one_or_none()
    if tier is None:
        tier = PriceTier(name=TEST_TIER, price_vnd=0)
        db.add(tier)
        db.flush()
    return tier.id


def create() -> int:
    db = SessionLocal()
    try:
        # Borrow a real seat's map coordinates so the throwaway seat overlaps it
        # rather than expanding the public map's bounds.
        coords = db.execute(select(Seat.svg_x, Seat.svg_y).limit(1)).first()
        sx, sy = (coords[0], coords[1]) if coords else (0, 0)

        num = 100000 + secrets.randbelow(900000)
        seat = Seat(
            section=TEST_SECTION, row_label="T", seat_number=num,
            label=f"{TEST_SECTION} · T{num}", tier_id=_throwaway_tier(db),
            status="blocked", svg_x=sx, svg_y=sy,
        )
        db.add(seat)
        db.flush()
        seat_id = seat.id

        vt = vip.export_seat(db, seat_id, "Vé kiểm tra E2E")
        ticket = db.get(Ticket, vt.ticket_id)
        qr_token = ticket.qr_token
        pdf = vt.pdf_filename
    finally:
        db.close()

    url = tickets_svc.checkin_url(qr_token)
    print("✅ Created a throwaway VIP test ticket.\n")
    print(f"  Seat (disposable): {TEST_SECTION} · T{num}")
    print(f"  Depot PDF:         {pdf}")
    print(f"  Check-in URL (QR): {url}\n")
    print("To test end-to-end:")
    print("  1. Open /admin/invitations/tickets and download this ticket's PDF")
    print(f'     (recipient \"Vé kiểm tra E2E\"), or just open the URL above.')
    print("  2. Scan the QR with your phone. You'll be asked for the door login")
    print(f"     (CHECKIN_USERNAME / CHECKIN_PASSWORD), then see a green VALID result.")
    print("  3. Scan again — it should now read USED (already checked in).\n")
    if settings.base_url.startswith("http://localhost") or "127.0.0.1" in settings.base_url:
        print("  ⚠️  BASE_URL points at localhost — run this on the server, or your")
        print("      phone won't be able to reach the check-in URL.\n")
    print("When done:  python -m scripts.vip_test_ticket --cleanup")
    return 0


def cleanup() -> int:
    db = SessionLocal()
    try:
        seat_ids = db.execute(
            select(Seat.id).where(Seat.section == TEST_SECTION)
        ).scalars().all()

        # Delete the depot PDFs first (DB rows are the only pointer to them).
        pdfs = db.execute(
            select(VipTicket.pdf_filename).where(VipTicket.seat_id.in_(seat_ids))
        ).scalars().all() if seat_ids else []
        removed_files = 0
        for name in pdfs:
            p = vip.depot_file(name)
            if p is not None:
                p.unlink()
                removed_files += 1

        order_ids = db.execute(
            select(OrderItem.order_id).where(OrderItem.seat_id.in_(seat_ids))
        ).scalars().all() if seat_ids else []

        if seat_ids:
            db.execute(delete(VipTicket).where(VipTicket.seat_id.in_(seat_ids)))
        if order_ids:
            db.execute(delete(Ticket).where(Ticket.order_id.in_(order_ids)))
            db.execute(delete(OrderItem).where(OrderItem.order_id.in_(order_ids)))
            db.execute(delete(Order).where(Order.id.in_(order_ids)))
        if seat_ids:
            db.execute(delete(Seat).where(Seat.id.in_(seat_ids)))
        db.execute(delete(PriceTier).where(PriceTier.name == TEST_TIER))
        db.commit()
        print(f"Removed {len(seat_ids)} test ticket(s) and {removed_files} depot PDF(s). "
              "No real data touched.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(cleanup() if "--cleanup" in sys.argv[1:] else create())
