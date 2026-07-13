"""Send a real e-ticket email to an address WITHOUT a purchase.

Exercises the exact production delivery path — same HTML template, inline QR codes,
and ``noreply`` From address a real buyer gets — by fabricating a throwaway paid
order, emailing it, then deleting it. No payment, no payOS, and the seat's inventory
status is never touched, so nothing lasting changes in the database.

Usage (inside the app container):
    python -m scripts.send_test_ticket you@example.com
    python -m scripts.send_test_ticket you@example.com --keep   # keep it so the
                                                                 # QR link resolves

Use it to confirm deliverability (does it arrive? spam folder? formatting?) before
opening real sales.
"""
from __future__ import annotations

import secrets
import sys

from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from app.db import SessionLocal
from app.models import Order, OrderItem, Seat, Ticket
from app.services import orders as orders_svc
from app.services import tickets as ticket_svc


def main(email: str, keep: bool = False) -> int:
    db = SessionLocal()
    try:
        seat = db.execute(
            select(Seat).options(selectinload(Seat.tier)).limit(1)
        ).scalars().first()
        if seat is None:
            print("No seats in the database — import the seat map first.")
            return 1

        price = seat.tier.price_vnd
        order = Order(
            order_code=orders_svc._unique_order_code(db),
            kind="sale",                       # look like a real buyer's ticket
            cart_id=None,
            buyer_name="Kiểm tra vé điện tử",
            email=email,
            phone="",
            amount_vnd=price,
            status="paid",
            items=[OrderItem(seat_id=seat.id, price_vnd=price)],
        )
        db.add(order)
        db.flush()
        db.add(Ticket(
            order_id=order.id,
            seat_id=seat.id,
            ticket_code=secrets.token_hex(8).upper(),
            qr_token=secrets.token_urlsafe(32),
        ))
        db.commit()
        code = order.order_code

        sent = ticket_svc.send_ticket_email(db, code)
        print(f"✅ Test e-ticket sent to {email}" if sent else "⚠️  Nothing sent.")

        if keep:
            print(f"Kept throwaway order {code}; its QR link will resolve until you delete it.")
        else:
            # The seat's status was never changed, so only the throwaway rows go.
            db.execute(delete(Ticket).where(Ticket.order_id == order.id))
            db.execute(delete(OrderItem).where(OrderItem.order_id == order.id))
            db.execute(delete(Order).where(Order.id == order.id))
            db.commit()
            print("Cleaned up the throwaway order — seat inventory untouched.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    keep = "--keep" in args
    addrs = [a for a in args if not a.startswith("--")]
    if not addrs:
        print("Usage: python -m scripts.send_test_ticket <email> [--keep]")
        raise SystemExit(2)
    raise SystemExit(main(addrs[0], keep))
