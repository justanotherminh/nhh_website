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
from app.services import holds, pricing

log = logging.getLogger("orders")


class NoSeatsHeld(Exception):
    """The cart has no live holds, so there's nothing to check out."""


class SeatsNotBookable(Exception):
    """One or more requested seats can't be booked (already sold, or unknown)."""


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

    # Apply the early-bird discount (if any) per seat, so the line items sum exactly
    # to amount_vnd — the payOS charge and its item breakdown always reconcile.
    percent = pricing.active_discount_percent(db)
    items = [
        OrderItem(seat_id=s.id, price_vnd=pricing.discounted_price(s.tier.price_vnd, percent))
        for s in seats
    ]
    amount = sum(it.price_vnd for it in items)
    order = Order(
        order_code=_unique_order_code(db),
        kind="sale",
        cart_id=cart_id,
        buyer_name=buyer_name,
        email=email,
        phone=phone,
        amount_vnd=amount,
        discount_percent=percent,
        status="pending",
        items=items,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def create_comp_order(
    db: Session,
    *,
    seat_ids: list[int],
    guest_name: str,
    email: str = "",
    phone: str = "",
    send_email: bool = True,
) -> Order:
    """Issue a free invitation (``comp``) order for the given seats.

    Unlike a sale, there's no cart, no payment and no pending state: the seats are
    booked immediately and the same QR ``Ticket`` rows a buyer gets are minted.
    All-or-nothing — if any seat isn't bookable (already sold, or unknown), nothing
    changes and ``SeatsNotBookable`` is raised.

    The e-ticket is emailed only when ``send_email`` is set and an ``email`` is
    given — pre-generated tickets for printout (no address on file) pass
    ``send_email=False``.

    Seats may come from the invitation pool (``status='blocked'``) or be otherwise
    available; a seat that's already ``booked`` is never taken.
    """
    seat_ids = list(dict.fromkeys(seat_ids))  # dedupe, keep order
    if not seat_ids:
        raise SeatsNotBookable("Chưa chọn ghế nào.")

    # Atomically claim the seats. The status guard means two admins issuing at once,
    # or a seat that just got sold, can't double-book: only truly bookable seats flip.
    booked = db.execute(
        update(Seat)
        .where(Seat.id.in_(seat_ids), Seat.status.in_(("available", "blocked")))
        .values(status="booked", held_by_cart=None, hold_expires_at=None)
        .returning(Seat.id)
    ).scalars().all()
    if len(booked) != len(seat_ids):
        db.rollback()  # undo the partial booking above
        raise SeatsNotBookable("Một số ghế không còn trống để phát vé mời.")

    order = Order(
        order_code=_unique_order_code(db),
        kind="comp",
        cart_id=None,
        buyer_name=guest_name,
        email=email,
        phone=phone or "",
        amount_vnd=0,
        status="paid",
        items=[OrderItem(seat_id=sid, price_vnd=0) for sid in seat_ids],
    )
    db.add(order)
    db.flush()  # assign order.id before minting tickets
    for sid in seat_ids:
        db.add(
            Ticket(
                order_id=order.id,
                seat_id=sid,
                ticket_code=secrets.token_hex(8).upper(),
                qr_token=secrets.token_urlsafe(32),
            )
        )
    db.commit()
    db.refresh(order)

    # Same delivery path as a paid order; failure must not undo the booking.
    if send_email and order.email:
        try:
            from app.services import tickets as ticket_svc

            ticket_svc.send_ticket_email(db, order.order_code)
        except Exception:
            log.exception("Failed to email invitation e-tickets for order %s", order.order_code)

    return order


def generate_reserved_tickets(db: Session, holder: str = "Vé mời (in sẵn)") -> int:
    """Mint printable QR tickets for every reserved (blocked) seat, no email.

    For old-school guests without an address on file: books each blocked seat into a
    single comp order and mints its ``Ticket`` (scannable at the door), so the seats
    can be printed and handed out. Idempotent — a seat already booked/ticketed is
    skipped, so re-running only covers newly-reserved seats. Returns how many were
    generated.
    """
    blocked = db.execute(
        select(Seat.id).where(Seat.status == "blocked").order_by(Seat.id)
    ).scalars().all()
    if not blocked:
        return 0
    create_comp_order(
        db, seat_ids=list(blocked), guest_name=holder, email="", send_email=False,
    )
    return len(blocked)


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
