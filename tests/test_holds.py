"""Concurrency + lifecycle tests for seat holds.

These hit the real Postgres from docker-compose (the same DATABASE_URL the app
uses). Run with: .venv/bin/pytest -q
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import PriceTier, Seat
from app.services import holds


@pytest.fixture()
def seat():
    """A throwaway available seat (+ tier), cleaned up afterwards."""
    db = SessionLocal()
    tier = PriceTier(name="TEST", price_vnd=1000)
    db.add(tier)
    db.flush()
    s = Seat(
        section="TEST",
        row_label="Z",
        seat_number=999,
        label="TEST seat",
        tier_id=tier.id,
        status="available",
    )
    db.add(s)
    db.commit()
    seat_id, tier_id = s.id, tier.id
    db.close()
    yield seat_id
    db = SessionLocal()
    db.execute(delete(Seat).where(Seat.id == seat_id))
    db.execute(delete(PriceTier).where(PriceTier.id == tier_id))
    db.commit()
    db.close()


def test_two_carts_race_exactly_one_wins(seat):
    cart_a, cart_b = uuid.uuid4(), uuid.uuid4()
    db_a, db_b = SessionLocal(), SessionLocal()
    try:
        got_a = holds.acquire(db_a, seat, cart_a, ttl_seconds=600)
        got_b = holds.acquire(db_b, seat, cart_b, ttl_seconds=600)
        assert [got_a, got_b].count(True) == 1
    finally:
        db_a.close()
        db_b.close()


def test_release_frees_the_seat(seat):
    cart_a, cart_b = uuid.uuid4(), uuid.uuid4()
    db = SessionLocal()
    try:
        assert holds.acquire(db, seat, cart_a, 600) is True
        assert holds.acquire(db, seat, cart_b, 600) is False  # held by A
        assert holds.release(db, seat, cart_a) is True
        assert holds.acquire(db, seat, cart_b, 600) is True    # now free
    finally:
        db.close()


def test_expired_hold_can_be_reclaimed(seat):
    cart_a, cart_b = uuid.uuid4(), uuid.uuid4()
    db = SessionLocal()
    try:
        # Acquire with a negative TTL so the hold is already expired.
        assert holds.acquire(db, seat, cart_a, ttl_seconds=-1) is True
        assert holds.count_for_cart(db, cart_a) == 0  # expired -> not counted
        assert holds.acquire(db, seat, cart_b, 600) is True  # reclaimed
        assert cart_b in {
            db.execute(select(Seat.held_by_cart).where(Seat.id == seat)).scalar()
        }
    finally:
        db.close()


def test_release_only_by_holder(seat):
    cart_a, cart_b = uuid.uuid4(), uuid.uuid4()
    db = SessionLocal()
    try:
        assert holds.acquire(db, seat, cart_a, 600) is True
        assert holds.release(db, seat, cart_b) is False  # B can't free A's hold
        assert holds.release(db, seat, cart_a) is True
    finally:
        db.close()
