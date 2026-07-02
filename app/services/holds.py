"""Seat-hold logic: claim, release and inspect holds.

A hold lives directly on the ``seats`` row (``held_by_cart`` + ``hold_expires_at``)
and is claimed with a single atomic conditional UPDATE — no explicit row locks, and
stale holds expire lazily via the ``hold_expires_at < now()`` clause. A seat is:

* **held**   when ``status='available'`` AND ``hold_expires_at > now()``
* **booked** (permanent) when ``status='booked'``
"""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.models import Seat


def acquire(db: Session, seat_id: int, cart_id: uuid.UUID, ttl_seconds: int) -> bool:
    """Atomically claim (or refresh) a seat for ``cart_id``.

    Succeeds if the seat is available and either unheld, already held by this same
    cart, or its previous hold has expired. Returns True iff the hold was taken.
    """
    expires = func.now() + dt.timedelta(seconds=ttl_seconds)
    stmt = (
        update(Seat)
        .where(
            Seat.id == seat_id,
            Seat.status == "available",
            or_(
                Seat.held_by_cart.is_(None),
                Seat.held_by_cart == cart_id,
                Seat.hold_expires_at < func.now(),
            ),
        )
        .values(held_by_cart=cart_id, hold_expires_at=expires)
    )
    result = db.execute(stmt)
    db.commit()
    return result.rowcount == 1


def release(db: Session, seat_id: int, cart_id: uuid.UUID) -> bool:
    """Release a single seat, but only if this cart currently holds it."""
    stmt = (
        update(Seat)
        .where(
            Seat.id == seat_id,
            Seat.status == "available",
            Seat.held_by_cart == cart_id,
        )
        .values(held_by_cart=None, hold_expires_at=None)
    )
    result = db.execute(stmt)
    db.commit()
    return result.rowcount == 1


def release_all(db: Session, cart_id: uuid.UUID) -> int:
    """Release every seat held by this cart. Returns how many were freed."""
    stmt = (
        update(Seat)
        .where(Seat.status == "available", Seat.held_by_cart == cart_id)
        .values(held_by_cart=None, hold_expires_at=None)
    )
    result = db.execute(stmt)
    db.commit()
    return result.rowcount


def extend(db: Session, cart_id: uuid.UUID, ttl_seconds: int) -> int:
    """Push out the expiry on all live holds for this cart (e.g. at checkout)."""
    expires = func.now() + dt.timedelta(seconds=ttl_seconds)
    stmt = (
        update(Seat)
        .where(
            Seat.status == "available",
            Seat.held_by_cart == cart_id,
            Seat.hold_expires_at > func.now(),
        )
        .values(hold_expires_at=expires)
    )
    result = db.execute(stmt)
    db.commit()
    return result.rowcount


def own_held_seat_ids(db: Session, cart_id: uuid.UUID | None) -> list[int]:
    """Seat ids this cart currently holds (available + unexpired)."""
    if cart_id is None:
        return []
    rows = db.execute(
        select(Seat.id).where(
            Seat.status == "available",
            Seat.held_by_cart == cart_id,
            Seat.hold_expires_at > func.now(),
        )
    ).scalars().all()
    return list(rows)


def count_for_cart(db: Session, cart_id: uuid.UUID | None) -> int:
    return len(own_held_seat_ids(db, cart_id))


def taken_seat_ids(db: Session, cart_id: uuid.UUID | None) -> list[int]:
    """Seats unavailable to this cart: booked, or held (unexpired) by someone else."""
    held_by_other = (
        Seat.held_by_cart.is_not(None)
        & (Seat.hold_expires_at > func.now())
    )
    if cart_id is not None:
        held_by_other = held_by_other & (Seat.held_by_cart != cart_id)
    rows = db.execute(
        select(Seat.id).where(
            or_(Seat.status == "booked", held_by_other)
        )
    ).scalars().all()
    return list(rows)
