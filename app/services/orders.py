"""Order lifecycle: build an order from a cart's held seats, confirm payment,
and cancel. Payment confirmation (``mark_order_paid``) is idempotent because the
payOS webhook may fire more than once.
"""
from __future__ import annotations

import datetime as dt
import logging
import secrets
import time
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.models import Order, OrderItem, Seat, Ticket
from app.services import holds

log = logging.getLogger("orders")


class NoSeatsHeld(Exception):
    """The cart has no live holds, so there's nothing to check out."""


def _unique_order_code(db: Session) -> int:
    """A unique numeric code for payOS (ms-since-epoch + a little randomness)."""
    for _ in range(10):
        code = int(time.time() * 1000) * 100 + secrets.randbelow(100)
        if not db.execute(
            select(Order.id).where(Order.order_code == code)
        ).first():
            return code
    raise RuntimeError("could not allocate a unique order_code")


def create_order_from_holds(
    db: Session,
    *,
    cart_id: uuid.UUID,
    buyer_name: str,
    email: str,
    phone: str,
    extend_seconds: int,
) -> Order:
    """Create a pending order for exactly the seats this cart currently holds.

    The client is never trusted for *which* seats — they come from the server-side
    holds. Holds are pushed out to the payment window so they don't lapse mid-pay.
    """
    seat_ids = holds.own_held_seat_ids(db, cart_id)
    if not seat_ids:
        raise NoSeatsHeld()

    seats = db.execute(
        select(Seat).options(selectinload(Seat.tier)).where(Seat.id.in_(seat_ids))
    ).scalars().all()

    holds.extend(db, cart_id, extend_seconds)

    amount = sum(s.tier.price_vnd for s in seats)
    order = Order(
        order_code=_unique_order_code(db),
        kind="sale",
        cart_id=cart_id,
        buyer_name=buyer_name,
        email=email,
        phone=phone,
        amount_vnd=amount,
        status="pending",
        items=[OrderItem(seat_id=s.id, price_vnd=s.tier.price_vnd) for s in seats],
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def get_order(db: Session, order_code: int) -> Order | None:
    return db.execute(
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.order_code == order_code)
    ).scalar_one_or_none()


def mark_order_paid(db: Session, order_code: int) -> bool:
    """Confirm payment: book the seats and mint tickets. Idempotent.

    Returns True if the order is paid (now or already), False if no such order.
    """
    order = get_order(db, order_code)
    if order is None:
        return False
    if order.status == "paid":
        return True  # already processed — webhook re-fire, do nothing

    order.status = "paid"
    seat_ids = [it.seat_id for it in order.items]
    # Payment succeeded, so the seats are now permanently booked regardless of
    # hold state; clear any hold bookkeeping on them.
    db.execute(
        update(Seat)
        .where(Seat.id.in_(seat_ids))
        .values(status="booked", held_by_cart=None, hold_expires_at=None)
    )
    # Mint one ticket per seat (QR image + email come in the e-ticket step).
    for it in order.items:
        db.add(
            Ticket(
                order_id=order.id,
                seat_id=it.seat_id,
                ticket_code=secrets.token_hex(8).upper(),
                qr_token=secrets.token_urlsafe(32),
            )
        )
    db.commit()

    # Deliver e-tickets. Email failure must NOT undo the confirmed payment, so we
    # log and move on — the buyer can still view tickets via the success page.
    try:
        from app.services import tickets as ticket_svc

        ticket_svc.send_ticket_email(db, order_code)
    except Exception:
        log.exception("Failed to email e-tickets for order %s", order_code)

    return True


def cancel_order(db: Session, order_code: int, reason: str = "") -> bool:
    """Cancel a pending order and release its still-held seats. Idempotent-ish:
    a paid order is never cancelled here."""
    order = get_order(db, order_code)
    if order is None or order.status == "paid":
        return False
    order.status = "cancelled"
    seat_ids = [it.seat_id for it in order.items]
    db.execute(
        update(Seat)
        .where(Seat.id.in_(seat_ids), Seat.status == "available")
        .values(held_by_cart=None, hold_expires_at=None)
    )
    db.commit()
    return True


def expire_stale_orders(db: Session, older_than_seconds: int | None = None) -> int:
    """Cancel pending orders whose payment window has elapsed with no payment.

    Catches buyers who reach the payOS page and just close it (no explicit cancel,
    no webhook). Their held seats have already lapsed lazily; this tidies the order
    row to 'cancelled' and voids the payOS link so it can't be paid late.

    Race-safe across workers: the single ``UPDATE ... WHERE status='pending'
    RETURNING`` atomically claims each stale order, so if several schedulers run at
    once, each order is cancelled exactly once (only one worker gets it back).
    """
    window = settings.payment_window_seconds if older_than_seconds is None else older_than_seconds
    cutoff = func.now() - dt.timedelta(seconds=window)

    claimed = db.execute(
        update(Order)
        .where(Order.status == "pending", Order.created_at < cutoff)
        .values(status="cancelled")
        .returning(Order.id, Order.order_code, Order.payos_payment_link_id)
    ).all()
    db.commit()

    if not claimed:
        return 0

    # Free any seats these orders were still holding (never touch booked seats).
    order_ids = [row.id for row in claimed]
    seat_ids = select(OrderItem.seat_id).where(OrderItem.order_id.in_(order_ids))
    db.execute(
        update(Seat)
        .where(Seat.status == "available", Seat.id.in_(seat_ids))
        .values(held_by_cart=None, hold_expires_at=None)
    )
    db.commit()

    # Best effort: void the payOS link so a late payment can't book a freed seat.
    if payos_client_configured():
        from app.services import payos_client

        for row in claimed:
            if not row.payos_payment_link_id:
                continue
            try:
                payos_client.cancel_payment_link(row.order_code, "Hết hạn thanh toán")
            except Exception:
                log.warning("Could not void payOS link for order %s", row.order_code)

    log.info("Expired %d stale pending order(s)", len(claimed))
    return len(claimed)


def payos_client_configured() -> bool:
    """True only when a real payOS link exists to void (skips dev/sandbox-off)."""
    from app.services import payos_client

    return payos_client.is_configured() and not settings.payments_dev_mode
