"""Early-bird discount: window activation, rounding, order application, comps exempt."""
from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import delete, select

from app.config import settings
from app.db import SessionLocal
from app.models import Order, OrderItem, PriceTier, Seat, Ticket
from app.services import holds, orders, pricing


# ---------------------------------------------------------------- unit: helper
def test_percent_zero_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "earlybird_percent", 0)
    monkeypatch.setattr(settings, "earlybird_until", None)
    assert pricing.active_discount_percent() == 0


def test_percent_needs_a_deadline(monkeypatch):
    # A percent with no deadline is treated as OFF (never an accidental forever-sale).
    monkeypatch.setattr(settings, "earlybird_percent", 10)
    monkeypatch.setattr(settings, "earlybird_until", None)
    assert pricing.active_discount_percent() == 0


def test_percent_active_before_and_inactive_after(monkeypatch):
    monkeypatch.setattr(settings, "earlybird_percent", 10)
    deadline = dt.datetime(2026, 8, 31, 23, 59, 59, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(settings, "earlybird_until", deadline)
    before = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
    after = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
    assert pricing.active_discount_percent(before) == 10
    assert pricing.active_discount_percent(after) == 0


def test_discounted_price_rounds_to_thousand():
    assert pricing.discounted_price(700_000, 10) == 630_000
    assert pricing.discounted_price(500_000, 10) == 450_000
    assert pricing.discounted_price(300_000, 15) == 255_000
    assert pricing.discounted_price(300_000, 0) == 300_000  # no discount = list price


# --------------------------------------------------- integration: order pricing
@pytest.fixture()
def two_seats():
    db = SessionLocal()
    tier = PriceTier(name="EB", price_vnd=700_000)
    db.add(tier)
    db.flush()
    ids = []
    for i in range(2):
        s = Seat(section="EB", row_label="Z", seat_number=950 + i,
                 label=f"EB Z{950 + i}", tier_id=tier.id, status="available")
        db.add(s)
        db.flush()
        ids.append(s.id)
    db.commit()
    tier_id = tier.id
    db.close()
    yield ids
    db = SessionLocal()
    oids = db.execute(select(OrderItem.order_id).where(OrderItem.seat_id.in_(ids))).scalars().all()
    if oids:
        db.execute(delete(Ticket).where(Ticket.order_id.in_(oids)))
        db.execute(delete(OrderItem).where(OrderItem.order_id.in_(oids)))
        db.execute(delete(Order).where(Order.id.in_(oids)))
    db.execute(delete(Seat).where(Seat.id.in_(ids)))
    db.execute(delete(PriceTier).where(PriceTier.id == tier_id))
    db.commit()
    db.close()


def test_order_applies_discount_and_reconciles(two_seats, monkeypatch):
    monkeypatch.setattr(settings, "earlybird_percent", 10)
    monkeypatch.setattr(
        settings, "earlybird_until",
        dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1),
    )
    cart = uuid.uuid4()
    db = SessionLocal()
    try:
        for sid in two_seats:
            assert holds.acquire(db, sid, cart, 600)
        order = orders.create_order_from_holds(
            db, cart_id=cart, buyer_name="EB", email="e@x.com",
            phone="0900000000", extend_seconds=900,
        )
        assert order.discount_percent == 10
        # 700k -> 630k each; total 1,260,000.
        assert [it.price_vnd for it in order.items] == [630_000, 630_000]
        assert order.amount_vnd == 1_260_000
        # Line items always sum to the charged amount (payOS reconciliation).
        assert sum(it.price_vnd for it in order.items) == order.amount_vnd
    finally:
        db.close()


def test_order_full_price_when_window_closed(two_seats, monkeypatch):
    monkeypatch.setattr(settings, "earlybird_percent", 10)
    monkeypatch.setattr(
        settings, "earlybird_until",
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1),  # already passed
    )
    cart = uuid.uuid4()
    db = SessionLocal()
    try:
        for sid in two_seats:
            assert holds.acquire(db, sid, cart, 600)
        order = orders.create_order_from_holds(
            db, cart_id=cart, buyer_name="EB", email="e@x.com",
            phone="0900000000", extend_seconds=900,
        )
        assert order.discount_percent == 0
        assert order.amount_vnd == 1_400_000
    finally:
        db.close()


def test_comp_order_is_never_discounted(two_seats, monkeypatch):
    # Even mid early-bird window, an invitation stays free (separate code path).
    monkeypatch.setattr(settings, "earlybird_percent", 10)
    monkeypatch.setattr(
        settings, "earlybird_until",
        dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1),
    )
    monkeypatch.setattr("app.services.tickets.send_ticket_email", lambda db, code: True)
    db = SessionLocal()
    try:
        order = orders.create_comp_order(
            db, seat_ids=two_seats[:1], guest_name="VIP", email="v@x.com",
        )
        assert order.amount_vnd == 0
        assert order.discount_percent == 0
        assert order.items[0].price_vnd == 0
    finally:
        db.close()
