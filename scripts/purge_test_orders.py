"""Remove throwaway orders left behind by ``send_test_ticket --keep``.

Those orders are deliberately shaped like real ones (kind='sale', status='paid')
so the email they trigger is realistic. The cost is that, left in the database,
they inflate the admin dashboard's revenue and — more importantly — hold live
tickets for seats that are still marked available. If such a seat is later sold,
two valid QR codes exist for it and both scan green at the door.

Dry run by default; nothing is deleted without ``--yes``.

Usage (inside the app container):
    python -m scripts.purge_test_orders            # list what would go
    python -m scripts.purge_test_orders --yes      # actually delete
"""
from __future__ import annotations

import sys

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import SessionLocal
from app.models import Order, Seat, Ticket

# Orders minted by scripts/send_test_ticket carry this exact buyer name, and it
# is the only thing distinguishing them from a genuine purchase.
TEST_BUYER_NAME = "Kiểm tra vé điện tử"
TEST_KIND = "test"


def find(db) -> list[Order]:
    return db.execute(
        select(Order)
        .options(selectinload(Order.tickets).selectinload(Ticket.seat),
                 selectinload(Order.items))
        .where((Order.buyer_name == TEST_BUYER_NAME) | (Order.kind == TEST_KIND))
        .order_by(Order.created_at)
    ).scalars().all()


def main(apply: bool) -> int:
    db = SessionLocal()
    try:
        found = find(db)
        if not found:
            print("No test orders found — nothing to do.")
            return 0

        total = 0
        risky: list[str] = []
        print(f"Found {len(found)} test order(s):\n")
        for o in found:
            when = o.created_at.strftime("%Y-%m-%d %H:%M") if o.created_at else "?"
            print(f"  Order {o.order_code}  {when}  {o.email}  "
                  f"{o.amount_vnd:,} đ  ({len(o.tickets)} ticket(s))".replace(",", "."))
            total += o.amount_vnd
            for t in o.tickets:
                seat = t.seat
                flag = ""
                # A live ticket on a still-sellable seat is the dangerous case.
                if seat is not None and seat.status == "available":
                    flag = "  <-- seat is still on sale; double-booking risk"
                    risky.append(seat.label)
                print(f"      seat {seat.label if seat else '?'} "
                      f"[{seat.status if seat else '?'}]{flag}")
        print(f"\n  Inflating reported revenue by {total:,} đ".replace(",", "."))
        if risky:
            print(f"  {len(risky)} ticket(s) sit on seats that can still be sold.")

        if not apply:
            print("\nDry run — nothing deleted. Re-run with --yes to remove them.")
            return 0

        for o in found:
            # ORM delete cascades to tickets and order items (see models.Order).
            db.delete(o)
        db.commit()
        print(f"\nDeleted {len(found)} test order(s) and their tickets.")
        print("Seat inventory is untouched: these orders never changed seat status.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main("--yes" in sys.argv[1:]))
