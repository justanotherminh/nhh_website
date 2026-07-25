"""Admin dashboard tests: auth gate, dashboard render, manual order cancel."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Order, OrderItem, PriceTier, Seat, Ticket
from app.services import holds, orders


@pytest.fixture()
def throwaway_seats():
    db = SessionLocal()
    tier = PriceTier(name="TEST", price_vnd=100_000)
    db.add(tier)
    db.flush()
    ids = []
    for i in range(2):
        s = Seat(section="TEST", row_label="Z", seat_number=700 + i,
                 label=f"TEST Z{700 + i}", tier_id=tier.id, status="available")
        db.add(s)
        db.flush()
        ids.append(s.id)
    db.commit()
    tier_id = tier.id
    db.close()

    yield ids

    db = SessionLocal()
    oids = db.execute(
        select(OrderItem.order_id).where(OrderItem.seat_id.in_(ids))
    ).scalars().all()
    if oids:
        db.execute(delete(Ticket).where(Ticket.order_id.in_(oids)))
        db.execute(delete(OrderItem).where(OrderItem.order_id.in_(oids)))
        db.execute(delete(Order).where(Order.id.in_(oids)))
    db.execute(delete(Seat).where(Seat.id.in_(ids)))
    db.execute(delete(PriceTier).where(PriceTier.id == tier_id))
    db.commit()
    db.close()


@pytest.fixture()
def admin_creds(monkeypatch):
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password", "s3cret-test")
    return ("admin", "s3cret-test")


def _pending_order(seat_ids) -> int:
    cart = uuid.uuid4()
    db = SessionLocal()
    for sid in seat_ids:
        assert holds.acquire(db, sid, cart, 600)
    order = orders.create_order_from_holds(
        db, cart_id=cart, buyer_name="Admin Test", email="t@x.com",
        phone="0900000000", extend_seconds=900,
    )
    code = order.order_code
    db.close()
    return code


def test_admin_requires_auth():
    c = TestClient(app)
    assert c.get("/admin").status_code == 401


def test_admin_rejects_wrong_password(admin_creds):
    c = TestClient(app)
    assert c.get("/admin", auth=("admin", "wrong")).status_code == 401


def test_admin_dashboard_loads(admin_creds):
    c = TestClient(app)
    r = c.get("/admin", auth=admin_creds)
    assert r.status_code == 200
    assert "Bảng quản trị" in r.text


def test_admin_can_cancel_pending_order(throwaway_seats, admin_creds):
    code = _pending_order(throwaway_seats)
    c = TestClient(app)
    r = c.post(f"/admin/orders/{code}/cancel", auth=admin_creds, follow_redirects=False)
    assert r.status_code == 303

    db = SessionLocal()
    try:
        assert orders.get_order(db, code).status == "cancelled"
        statuses = db.execute(
            select(Seat.status).where(Seat.id.in_(throwaway_seats))
        ).scalars().all()
        assert all(s == "available" for s in statuses)
    finally:
        db.close()


def _clear_promo():
    from sqlalchemy import delete
    from app.models import AppSetting
    db = SessionLocal()
    db.execute(delete(AppSetting).where(AppSetting.key.like("earlybird_%")))
    db.commit()
    db.close()


def test_early_bird_requires_auth():
    assert TestClient(app).get("/admin/early-bird").status_code == 401


def test_early_bird_save_and_read_back(admin_creds):
    _clear_promo()
    try:
        c = TestClient(app)
        r = c.post("/admin/early-bird", auth=admin_creds, follow_redirects=False,
                   data={"enabled": "1", "percent": "10",
                         "start": "2026-08-01T00:00", "end": "2026-09-01T00:00"})
        assert r.status_code == 303
        from app.services import pricing
        db = SessionLocal()
        try:
            cfg = pricing.get_earlybird(db)
            assert cfg["enabled"] and cfg["percent"] == 10
            assert cfg["start_raw"] == "2026-08-01T00:00"
        finally:
            db.close()
    finally:
        _clear_promo()


def test_early_bird_rejects_backwards_window(admin_creds):
    _clear_promo()
    try:
        c = TestClient(app)
        r = c.post("/admin/early-bird", auth=admin_creds, follow_redirects=False,
                   data={"enabled": "1", "percent": "10",
                         "start": "2026-09-01T00:00", "end": "2026-08-01T00:00"})
        assert r.status_code == 303
        assert "error=" in r.headers["location"]
    finally:
        _clear_promo()


# --------------------------------------------------------- orders pagination
@pytest.fixture()
def order_batch():
    """26 paid + 1 cancelled order, timestamped into the future so they sort to
    the front of the (shared) orders table regardless of other test data."""
    import datetime as dt
    db = SessionLocal()
    base = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)
    made = []
    # index 0 is oldest of the batch, 25 the newest paid one.
    for i in range(26):
        o = Order(order_code=orders._unique_order_code(db), kind="sale",
                  buyer_name="Pager Test", email="pager@x.com", phone="0900000000",
                  amount_vnd=100000, status="paid",
                  created_at=base + dt.timedelta(seconds=i))
        db.add(o); db.flush()
        made.append(o.order_code)
    cancelled = Order(order_code=orders._unique_order_code(db), kind="sale",
                      buyer_name="Pager Cancelled", email="pager@x.com",
                      phone="0900000000", amount_vnd=100000, status="cancelled",
                      created_at=base + dt.timedelta(seconds=100))  # newest of all
    db.add(cancelled); db.flush()
    made.append(cancelled.order_code)
    db.commit()
    db.close()
    yield {"paid": made[:26], "cancelled": made[26]}
    db = SessionLocal()
    db.execute(delete(Order).where(Order.order_code.in_(made)))
    db.commit()
    db.close()


def test_orders_are_paginated_25_per_page(order_batch, admin_creds):
    c = TestClient(app)
    page1 = c.get("/admin", auth=admin_creds).text
    # Exactly 25 of the 26 batch orders fit on page 1; the oldest spills to page 2.
    on_p1 = [code for code in order_batch["paid"] if str(code) in page1]
    assert len(on_p1) == 25
    oldest = order_batch["paid"][0]
    assert str(oldest) not in page1
    assert "Trang 1/" in page1 and "Sau →" in page1

    page2 = c.get("/admin?page=2", auth=admin_creds).text
    assert str(oldest) in page2


def test_cancelled_orders_hidden_by_default(order_batch, admin_creds):
    c = TestClient(app)
    cancelled_code = str(order_batch["cancelled"])
    default = c.get("/admin", auth=admin_creds).text
    assert cancelled_code not in default          # hidden even though it's newest

    shown = c.get("/admin?cancelled=1", auth=admin_creds).text
    assert cancelled_code in shown                # revealed by the toggle


def test_page_out_of_range_is_clamped(order_batch, admin_creds):
    c = TestClient(app)
    r = c.get("/admin?page=9999", auth=admin_creds)
    assert r.status_code == 200                   # clamped, not an error
    # Last page shows the oldest batch order, not an empty table.
    assert str(order_batch["paid"][0]) in r.text
