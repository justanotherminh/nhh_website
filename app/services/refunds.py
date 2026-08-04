"""Per-seat refunds: reconcile the database after a human has moved the money.

payOS has no refund API, so a refund is two separate things:

1. A fund manager transfers the money back from the organisers' own banking.
   That happens outside this system entirely and **nothing here moves money**.
2. This module makes the database agree with what they did.

Step 2 is per-seat rather than per-order, because an order can hold up to eight
seats and a buyer may hand back only some of them. For one seat it does four
things in a single transaction:

* **Voids the ticket** — sets ``voided_at``, so the buyer's QR stops admitting
  anyone. The row is kept (not deleted) so its check-in history survives and the
  door can say "refunded" rather than the "invalid" it shows a forged QR.
* **Releases the seat** — always straight back to ``available``, on sale again.
  A refunded seat is never retired: taking one out of the pool is a separate
  decision, and ``scripts/block_seats.py`` already does it if it's ever needed.
* **Records a** :class:`~app.models.Refund` — the ledger row the managers
  reconcile against the bank: seat, amount, who, when, and a free-text note.
* **Flips the order to ``refunded``** once its last live seat is gone. That is
  what drops a fully-refunded order out of the revenue total *and* out of the
  announcement mailing list, both of which count only ``status='paid'``. A
  partially refunded order stays ``paid`` — the buyer is still coming, and should
  still get the emails.

The refund amount is always the seat's ``OrderItem.price_vnd`` — what that seat
was actually charged, early-bird discount included — never the tier list price.
"""
from __future__ import annotations

import logging

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from app.models import Order, OrderItem, Refund, Seat, Ticket

log = logging.getLogger("refunds")


class RefundError(Exception):
    """A refund was refused. The message is shown to the admin verbatim."""


def refundable_order(db: Session, order_code: int) -> Order | None:
    """Load an order with everything the refund screen needs."""
    return db.execute(
        select(Order)
        .options(
            selectinload(Order.items).selectinload(OrderItem.seat).selectinload(Seat.tier),
            selectinload(Order.tickets).selectinload(Ticket.seat),
        )
        .where(Order.order_code == order_code)
    ).scalar_one_or_none()


def refunded_seat_ids(db: Session, order_id: int) -> set[int]:
    return set(
        db.execute(
            select(Refund.seat_id).where(Refund.order_id == order_id)
        ).scalars().all()
    )


def refund_seat(
    db: Session,
    *,
    order_code: int,
    seat_id: int,
    operator: str = "",
    note: str = "",
) -> Refund:
    """Refund one seat of one order. Raises :class:`RefundError` if refused.

    Refuses rather than half-completes: every guard runs before anything is
    written, and the whole thing commits once.
    """
    order = refundable_order(db, order_code)
    if order is None:
        raise RefundError(f"Không tìm thấy đơn {order_code}.")

    # Comp/invitation seats carry no money and their tickets may be referenced by
    # a VipTicket row (whose PDF is already printed), so they are not refundable
    # here. Revoking an invitation is a different job with a different audit trail.
    if order.kind != "sale":
        raise RefundError(
            "Đơn này là vé mời, không phải vé bán — không hoàn tiền được. "
            "Dùng trang Vé mời để xử lý."
        )
    if order.status != "paid":
        raise RefundError(
            f"Chỉ hoàn tiền được đơn đã thanh toán (đơn này đang '{order.status}')."
        )

    item = next((it for it in order.items if it.seat_id == seat_id), None)
    if item is None:
        raise RefundError("Ghế này không thuộc đơn hàng.")

    if seat_id in refunded_seat_ids(db, order.id):
        raise RefundError("Ghế này đã được hoàn tiền rồi.")

    ticket = next((t for t in order.tickets if t.seat_id == seat_id), None)
    # An attendee who already walked in must not be refunded by a single click —
    # that is a judgement call for a human, made deliberately and off this path.
    if ticket is not None and ticket.checked_in_at is not None:
        when = ticket.checked_in_at.strftime("%H:%M %d/%m/%Y")
        raise RefundError(
            f"Vé này đã được soát vào cửa lúc {when} — không hoàn tiền tự động được."
        )

    # --- all guards passed; write everything in one transaction ---------------
    if ticket is not None:
        # Conditional so a concurrent check-in can't slip in between the guard
        # above and this write: if the door admitted them a moment ago, this
        # matches nothing and we abort rather than void a ticket already used.
        voided = db.execute(
            update(Ticket)
            .where(
                Ticket.id == ticket.id,
                Ticket.checked_in_at.is_(None),
                Ticket.voided_at.is_(None),
            )
            .values(voided_at=func.now())
        ).rowcount
        if not voided:
            db.rollback()
            raise RefundError(
                "Vé vừa được soát hoặc đã hoàn ở nơi khác — hãy tải lại trang."
            )

    # Only ever release a seat this order actually holds as booked.
    db.execute(
        update(Seat)
        .where(Seat.id == seat_id, Seat.status == "booked")
        .values(status="available", held_by_cart=None, hold_expires_at=None)
    )

    refund = Refund(
        order_id=order.id,
        seat_id=seat_id,
        ticket_id=ticket.id if ticket is not None else None,
        amount_vnd=item.price_vnd,
        refunded_by=operator or "",
        note=(note or "").strip()[:300],
    )
    db.add(refund)
    db.flush()

    # Last live seat gone -> the order as a whole is refunded, which is what drops
    # it out of revenue and the mailing list.
    if len(refunded_seat_ids(db, order.id)) >= len(order.items):
        order.status = "refunded"

    db.commit()
    db.refresh(refund)
    log.info(
        "Refunded seat %s of order %s (%s đ, back on sale) by %r",
        seat_id, order_code, item.price_vnd, operator,
    )
    return refund


# ------------------------------------------------------------------ reporting

def refunded_total(db: Session) -> int:
    """Every đồng recorded as refunded — what the managers should have paid back."""
    return db.execute(
        select(func.coalesce(func.sum(Refund.amount_vnd), 0))
    ).scalar_one()


def refunded_on_live_orders(db: Session) -> int:
    """Refunds against orders still counted as 'paid'.

    Revenue is ``sum(amount_vnd)`` over paid orders, so a *partial* refund is
    still sitting inside that total and has to be netted off. A *fully* refunded
    order has already left the paid bucket entirely, so subtracting its refunds
    too would double-count them — hence the join.
    """
    return db.execute(
        select(func.coalesce(func.sum(Refund.amount_vnd), 0))
        .select_from(Refund)
        .join(Order, Refund.order_id == Order.id)
        .where(Order.status == "paid")
    ).scalar_one()


def recent_for_order(db: Session, order_id: int) -> list[Refund]:
    """This order's refund ledger, oldest first — shown on the order screen."""
    return list(
        db.execute(
            select(Refund)
            .options(selectinload(Refund.seat))
            .where(Refund.order_id == order_id)
            .order_by(Refund.created_at, Refund.id)
        ).scalars().all()
    )


def count(db: Session) -> int:
    return db.execute(select(func.count()).select_from(Refund)).scalar_one()


def recent(db: Session, limit: int = 200) -> list[Refund]:
    """The whole refund ledger, newest first, capped so the page can't blow up.

    ``count()`` is reported alongside it so a truncated view says so rather than
    quietly looking complete — this table is what gets reconciled against the
    bank, and a silently missing row is the one failure that matters here.
    """
    return list(
        db.execute(
            select(Refund)
            .options(selectinload(Refund.seat), selectinload(Refund.order))
            .order_by(Refund.created_at.desc(), Refund.id.desc())
            .limit(limit)
        ).scalars().all()
    )
