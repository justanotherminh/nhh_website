"""Send a real e-ticket email to an address WITHOUT a purchase.

Exercises the exact production delivery path — same HTML template, inline QR codes,
and ``noreply`` From address a real buyer gets — by fabricating a throwaway paid
order, emailing it, then deleting it. No payment, no payOS, and the seat's inventory
status is never touched, so nothing lasting changes in the database.

The fake order spans several tiers, with more than one seat in the top tier, so the
email's grouped "Hạng ghế" line and its one-QR-per-seat block both get exercised —
a single-seat test would leave that formatting unverified.

Usage (inside the app container):
    python -m scripts.send_test_ticket you@example.com
    python -m scripts.send_test_ticket you@example.com --plan 3,2,1   # seats per
                                                                      # tier, dearest
                                                                      # tier first
    python -m scripts.send_test_ticket you@example.com --keep   # keep it so the
                                                                 # QR links resolve

Sending goes wherever SMTP_* in .env points — Mailpit locally, real SMTP on the
server. Run it on the server to test deliverability to a real inbox.
"""
from __future__ import annotations

import secrets
import sys

from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from app.db import SessionLocal
from app.models import Order, OrderItem, PriceTier, Seat, Ticket
from app.services import orders as orders_svc
from app.services import tickets as ticket_svc

DEFAULT_PLAN = (2, 1, 1)   # seats to take from each tier, most expensive first


def _pick_seats(db, plan: tuple[int, ...]) -> list[Seat]:
    """Take the requested number of seats from each tier, dearest tier first.

    Prefers seats that are actually available so a --keep run doesn't hand out a
    QR for a seat someone has already bought.
    """
    tiers = db.execute(
        select(PriceTier).order_by(PriceTier.price_vnd.desc())
    ).scalars().all()
    picked: list[Seat] = []
    for tier, want in zip(tiers, plan):
        if want <= 0:
            continue
        seats = db.execute(
            select(Seat)
            .options(selectinload(Seat.tier))
            .where(Seat.tier_id == tier.id, Seat.status == "available")
            .order_by(Seat.svg_y, Seat.svg_x)
            .limit(want)
        ).scalars().all()
        if len(seats) < want:
            print(f"⚠️  Only {len(seats)} available seat(s) in {tier.name}, wanted {want}.")
        picked.extend(seats)
    return picked


def main(email: str, keep: bool = False, plan: tuple[int, ...] = DEFAULT_PLAN) -> int:
    db = SessionLocal()
    try:
        seats = _pick_seats(db, plan)
        if not seats:
            print("No seats in the database — import the seat map first.")
            return 1

        amount = sum(s.tier.price_vnd for s in seats)
        order = Order(
            order_code=orders_svc._unique_order_code(db),
            kind="sale",                       # look like a real buyer's ticket
            cart_id=None,
            buyer_name="Kiểm tra vé điện tử",
            email=email,
            phone="",
            amount_vnd=amount,
            status="paid",
            items=[OrderItem(seat_id=s.id, price_vnd=s.tier.price_vnd) for s in seats],
        )
        db.add(order)
        db.flush()
        for s in seats:
            db.add(Ticket(
                order_id=order.id,
                seat_id=s.id,
                ticket_code=secrets.token_hex(8).upper(),
                qr_token=secrets.token_urlsafe(32),
            ))
        db.commit()
        code = order.order_code

        money = f"{amount:,.0f}".replace(",", ".")
        print(f"Fake order {code} — {len(seats)} seat(s), {money} đ")
        for s in seats:
            print(f"  · {s.tier.name}: {s.label}")

        sent = ticket_svc.send_ticket_email(db, code)
        print(f"✅ Test e-ticket sent to {email}" if sent else "⚠️  Nothing sent.")

        if keep:
            print(f"Kept throwaway order {code}; its QR links will resolve until you delete it.")
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
    plan = DEFAULT_PLAN
    for a in args:
        if a.startswith("--plan="):
            plan = tuple(int(n) for n in a.split("=", 1)[1].split(","))
        elif a == "--plan":
            plan = tuple(int(n) for n in args[args.index(a) + 1].split(","))
    addrs = [a for a in args if not a.startswith("--") and "@" in a]
    if not addrs:
        print("Usage: python -m scripts.send_test_ticket <email> [--plan 2,1,1] [--keep]")
        raise SystemExit(2)
    raise SystemExit(main(addrs[0], keep, plan))
