"""Early-bird discount: DB-backed window, rounding, order application, comps exempt."""
from __future__ import annotations

import datetime as dt
import uuid
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, select

from app.config import settings
from app.db import SessionLocal
from app.models import AppSetting, Order, OrderItem, PriceTier, Seat, Ticket
from app.services import holds, orders, pricing

_HANOI = ZoneInfo("Asia/Ho_Chi_Minh")


@pytest.fixture(autouse=True)
def _clean_promo():
    """Reset the promo config before and after each test (shared dev DB)."""
    def wipe():
        db = SessionLocal()
        db.execute(delete(AppSetting).where(AppSetting.key.like("earlybird_%")))
        db.commit()
        db.close()
    wipe()
    yield
    wipe()


def _set(enabled, percent, start, end):
    db = SessionLocal()
    pricing.set_earlybird(db, enabled=enabled, percent=percent, start=start, end=end)
    db.close()


def _hanoi(y, m, d, h=0):
    return dt.datetime(y, m, d, h, tzinfo=_HANOI).astimezone(dt.timezone.utc)


# ---------------------------------------------------------------- unit: helper
def test_percent_zero_when_disabled():
    _set(False, 10, "2026-08-01T00:00", "2026-09-01T00:00")
    db = SessionLocal()
    try:
        assert pricing.active_discount_percent(db, _hanoi(2026, 8, 15)) == 0
    finally:
        db.close()


def test_percent_active_only_within_window():
    _set(True, 10, "2026-08-01T00:00", "2026-09-01T00:00")
    db = SessionLocal()
    try:
        assert pricing.active_discount_percent(db, _hanoi(2026, 7, 20)) == 0   # before
        assert pricing.active_discount_percent(db, _hanoi(2026, 8, 15)) == 10  # inside
        assert pricing.active_discount_percent(db, _hanoi(2026, 9, 10)) == 0   # after
    finally:
        db.close()


def test_incomplete_window_is_off():
    _set(True, 10, "", "")
    db = SessionLocal()
    try:
        assert pricing.active_discount_percent(db, _hanoi(2026, 8, 15)) == 0
    finally:
        db.close()


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


def _wide_window(enabled=True, percent=10):
    """Active-now window (yesterday .. tomorrow, Hanoi)."""
    now = dt.datetime.now(_HANOI)
    s = (now - dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    e = (now + dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    _set(enabled, percent, s, e)


def test_order_applies_discount_and_reconciles(two_seats):
    _wide_window()
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
        assert [it.price_vnd for it in order.items] == [630_000, 630_000]
        assert order.amount_vnd == 1_260_000
        assert sum(it.price_vnd for it in order.items) == order.amount_vnd
    finally:
        db.close()


def test_order_full_price_when_window_closed(two_seats):
    _wide_window(enabled=False)  # promo off
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
    _wide_window()  # promo active
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
