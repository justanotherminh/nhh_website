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


# States a payOS payment can still be applied to. 'cancelled' is included on
# purpose — a payment can land after its order was cancelled; see mark_order_paid.
PAYABLE_STATUSES = ("pending", "cancelled", "expired")


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
    lang: str = "vi",
    attribution: dict | None = None,
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

    # A cart has at most one pending order. A buyer who goes Back from payOS and
    # submits again gets a fresh order for the same held seats, and the earlier
    # one is cancelled here (its link voided below) rather than left live beside
    # it: two payable links for one set of seats is how a buyer pays twice. Holds
    # aren't touched — they carry over to the new order.
    superseded = db.execute(
        update(Order)
        .where(Order.cart_id == cart_id, Order.status == "pending")
        .values(status="cancelled")
        .returning(Order.order_code, Order.payos_payment_link_id)
    ).all()

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
        lang=lang,
        amount_vnd=amount,
        discount_percent=percent,
        status="pending",
        items=items,
        # Where this buyer came from, read off the first-party cookie by the caller
        # (see services/attribution.py). Stamped at creation rather than at payment
        # because the cookie belongs to the buyer's browser, and the payment is
        # confirmed later by a webhook from payOS, which has no cookies of theirs.
        **(attribution or {}),
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    _void_payment_links(superseded, "Đơn được thay bằng đơn mới")
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


def ensure_reserved_tickets(db: Session, seat_ids: list[int]) -> int:
    """Make sure each given seat has a printable QR ticket, minting any that are
    missing (no email). Used when a VIP seat's ticket is exported on demand.

    Idempotent: seats that already have a ticket are left as-is; only seats that are
    still available/blocked get a comp ticket minted (and booked). Returns how many
    new tickets were minted.
    """
    seat_ids = list(dict.fromkeys(seat_ids))
    if not seat_ids:
        return 0
    have = set(
        db.execute(
            select(Ticket.seat_id).where(Ticket.seat_id.in_(seat_ids))
        ).scalars().all()
    )
    need = db.execute(
        select(Seat.id).where(
            Seat.id.in_([s for s in seat_ids if s not in have]),
            Seat.status.in_(("available", "blocked")),
        )
    ).scalars().all()
    if not need:
        return 0
    create_comp_order(
        db, seat_ids=list(need), guest_name="Vé mời (in sẵn)", email="", send_email=False,
    )
    return len(need)


def get_order(db: Session, order_code: int) -> Order | None:
    return db.execute(
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.order_code == order_code)
    ).scalar_one_or_none()


def mark_order_paid(db: Session, order_code: int) -> bool:
    """Apply a confirmed payment: book the seats and mint tickets. Idempotent.

    Returns True if the order is paid (now, or by an earlier delivery), False if
    there is no such order or the payment couldn't be honoured.

    payOS may deliver the same payment more than once, concurrently, and late.
    Two things make that safe:

    * The order row is locked (``FOR UPDATE``) before its status is read. Two
      deliveries arriving together are serialised: the second waits, then sees
      'paid' and does nothing. Without the lock both read 'pending' and each
      mints a full set of tickets.
    * Seats are *claimed* with a guarded UPDATE, never simply assigned. A payment
      can arrive after its order was cancelled — voiding the payOS link is best
      effort, and a transfer can complete in the seconds after the window closes
      — and by then the seats may have been sold to someone else. Paid money
      beats any unpaid hold, so a seat that is still available is taken; one
      that is booked, or blocked back into the VIP pool, is not. If any seat
      can't be had, none are: the order becomes 'needs_refund' with no tickets,
      for a manager to pay back by hand (the dashboard lists these).
    """
    order = db.execute(
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.order_code == order_code)
        .with_for_update(of=Order)
        # The caller may already hold this order in the session (the webhook
        # reads it first); make sure what we act on is what we just locked.
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if order is None:
        return False

    was = order.status
    if was not in PAYABLE_STATUSES:
        db.rollback()  # release the lock
        # 'paid' / 'refunded': an earlier delivery already processed it.
        # 'needs_refund': already found unfulfillable; retrying won't change that.
        return was in ("paid", "refunded")

    seat_ids = {it.seat_id for it in order.items}
    savepoint = db.begin_nested()
    claimed = set(db.execute(
        update(Seat)
        .where(Seat.id.in_(list(seat_ids)), Seat.status == "available")
        .values(status="booked", held_by_cart=None, hold_expires_at=None)
        .returning(Seat.id)
    ).scalars().all())
    if claimed != seat_ids:
        savepoint.rollback()  # un-book whatever did match; the order lock stays
        order.status = "needs_refund"
        db.commit()
        log.error(
            "Order %s was paid while '%s', but seat(s) %s are no longer available. "
            "Marked needs_refund: the buyer must be paid back by hand.",
            order_code, was, sorted(seat_ids - claimed),
        )
        return False
    savepoint.commit()

    order.status = "paid"
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
    if was != "pending":
        log.warning("Order %s paid after it was '%s'; its seats were still free, "
                    "so the payment was honoured", order_code, was)

    # Deliver e-tickets. Email failure must NOT undo the confirmed payment, so we
    # log and move on.
    try:
        from app.services import tickets as ticket_svc

        ticket_svc.send_ticket_email(db, order_code)
    except Exception:
        log.exception("Failed to email e-tickets for order %s", order_code)

    return True


def cancel_order(db: Session, order_code: int, reason: str = "") -> bool:
    """Cancel a *pending* order and release the seats its cart still holds.

    Only 'pending' moves. Paid and refunded orders are never touched, and neither
    is 'needs_refund' — a flag a manager has to act on, which anyone holding the
    order's cancel link could otherwise erase. The release is scoped to the
    order's own cart, so it can't wipe a hold another buyer has since taken on a
    seat this order once had.
    """
    row = db.execute(
        update(Order)
        .where(Order.order_code == order_code, Order.status == "pending")
        .values(status="cancelled")
        .returning(Order.id, Order.cart_id)
    ).first()
    if row is None:
        db.rollback()
        return False
    _release_own_holds(db, row.id, row.cart_id)
    db.commit()
    return True


def _release_own_holds(db: Session, order_id: int, cart_id) -> None:
    """Clear the holds an order's cart still has on that order's seats.

    Never a bare "clear holds on these seats": by the time an order is cancelled
    or expires, its holds may have lapsed and the seat been picked up by another
    buyer, whose hold this must leave alone.
    """
    db.execute(
        update(Seat)
        .where(
            Seat.id.in_(select(OrderItem.seat_id).where(OrderItem.order_id == order_id)),
            Seat.status == "available",
            Seat.held_by_cart == cart_id,
        )
        .values(held_by_cart=None, hold_expires_at=None)
    )


def _void_payment_links(rows, reason: str) -> None:
    """Best effort: cancel payOS links so they can no longer be paid.

    ``rows`` are ``(order_code, payos_payment_link_id)`` pairs. A failure is
    logged, not raised — mark_order_paid copes with a payment that gets through
    anyway.
    """
    if not rows or not payos_client_configured():
        return
    from app.services import payos_client

    for order_code, link_id in rows:
        if not link_id:
            continue
        try:
            payos_client.cancel_payment_link(order_code, reason)
        except Exception:
            log.warning("Could not void payOS link for order %s", order_code)


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
        .returning(Order.id, Order.cart_id, Order.order_code, Order.payos_payment_link_id)
    ).all()
    db.commit()

    if not claimed:
        return 0

    # Free the seats these orders' carts were still holding (never booked seats,
    # and never a hold some other cart has taken since).
    for row in claimed:
        _release_own_holds(db, row.id, row.cart_id)
    db.commit()

    # Best effort: void the payOS links, so the buyer can't pay at all rather than
    # pay and need a refund (mark_order_paid handles it if they get through).
    _void_payment_links(
        [(row.order_code, row.payos_payment_link_id) for row in claimed],
        "Hết hạn thanh toán",
    )

    log.info("Expired %d stale pending order(s)", len(claimed))
    return len(claimed)


def payos_client_configured() -> bool:
    """True only when a real payOS link exists to void (skips dev/sandbox-off)."""
    from app.services import payos_client

    return payos_client.is_configured() and not settings.payments_dev_mode
