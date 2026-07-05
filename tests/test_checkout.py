"""Order lifecycle + webhook tests.

Hit the real Postgres (like the hold tests). Throwaway seats/tiers are created
and cleaned up per test, and payOS is stubbed so nothing touches the network.
"""
from __future__ import annotations

import types
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.main import app
from app.models import Order, OrderItem, PriceTier, Seat, Ticket
from app.routers import checkout as checkout_router
from app.services import orders, payos_client


@pytest.fixture()
def throwaway_seats():
    """Three throwaway available seats (+ tier). Cleans up everything after."""
    db = SessionLocal()
    tier = PriceTier(name="TEST", color_hex="#123456", price_vnd=100_000)
    db.add(tier)
    db.flush()
    ids = []
    for i in range(3):
        s = Seat(
            section="TEST", row_label="Z", seat_number=900 + i,
            label=f"TEST Z{900 + i}", tier_id=tier.id, status="available",
        )
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
def force_dev_pay(monkeypatch):
    """Route checkout through the in-app simulator regardless of local .env."""
    monkeypatch.setattr(checkout_router, "_use_dev_payments", lambda: True)


def test_dev_pay_flow_books_seats_and_makes_tickets(throwaway_seats, force_dev_pay):
    c = TestClient(app)
    # Hold two of the three seats (this also sets the cart cookie).
    for sid in throwaway_seats[:2]:
        assert c.post("/api/hold", json={"seat_id": sid}).status_code == 200

    assert c.get("/checkout").status_code == 200

    r = c.post(
        "/checkout",
        data={"buyer_name": "Nguyen A", "email": "a@example.com", "phone": "0900000000"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "/checkout/dev-pay?order=" in r.headers["location"]
    order_code = int(r.headers["location"].split("order=")[1])

    # Simulate a successful payment (same path the webhook drives).
    r = c.post("/checkout/dev-pay/confirm", data={"order": order_code},
               follow_redirects=False)
    assert r.status_code == 303

    assert c.get(f"/api/order/{order_code}/status").json()["status"] == "paid"

    db = SessionLocal()
    try:
        booked = db.execute(
            select(Seat.status).where(Seat.id.in_(throwaway_seats[:2]))
        ).scalars().all()
        assert booked == ["booked", "booked"]
        # Third seat untouched.
        assert db.execute(
            select(Seat.status).where(Seat.id == throwaway_seats[2])
        ).scalar() == "available"
        n_tickets = db.execute(
            select(Ticket).join(Order).where(Order.order_code == order_code)
        ).scalars().all()
        assert len(n_tickets) == 2
    finally:
        db.close()


def _fake_webhook_data(order_code, code="00"):
    return types.SimpleNamespace(orderCode=order_code, code=code)


def test_webhook_marks_paid_and_is_idempotent(throwaway_seats, monkeypatch):
    # Create a pending order directly from held seats.
    cart = uuid.uuid4()
    db = SessionLocal()
    from app.services import holds
    for sid in throwaway_seats:
        assert holds.acquire(db, sid, cart, 600)
    order = orders.create_order_from_holds(
        db, cart_id=cart, buyer_name="B", email="b@x.com", phone="0900000001",
        extend_seconds=900,
    )
    order_code = order.order_code
    db.close()

    # Stub signature verification to accept and return our order code.
    monkeypatch.setattr(
        payos_client, "verify_webhook",
        lambda body: _fake_webhook_data(order_code),
    )

    c = TestClient(app)
    body = {"code": "00", "data": {"orderCode": order_code}}
    assert c.post("/payos/webhook", json=body).json() == {"success": True}
    assert c.post("/payos/webhook", json=body).json() == {"success": True}  # re-fire

    db = SessionLocal()
    try:
        o = orders.get_order(db, order_code)
        assert o.status == "paid"
        # Idempotent: exactly one ticket per seat, not doubled by the re-fire.
        tickets = db.execute(
            select(Ticket).where(Ticket.order_id == o.id)
        ).scalars().all()
        assert len(tickets) == len(throwaway_seats)
    finally:
        db.close()


def test_webhook_rejects_bad_signature(throwaway_seats, monkeypatch):
    cart = uuid.uuid4()
    db = SessionLocal()
    from app.services import holds
    assert holds.acquire(db, throwaway_seats[0], cart, 600)
    order = orders.create_order_from_holds(
        db, cart_id=cart, buyer_name="C", email="c@x.com", phone="0900000002",
        extend_seconds=900,
    )
    order_code = order.order_code
    db.close()

    def _raise(body):
        raise payos_client.__dict__.get("InvalidSignatureError", ValueError)("bad sig")

    monkeypatch.setattr(payos_client, "verify_webhook", _raise)

    c = TestClient(app)
    r = c.post("/payos/webhook", json={"code": "00", "data": {"orderCode": order_code}})
    assert r.json() == {"success": False}

    db = SessionLocal()
    try:
        assert orders.get_order(db, order_code).status == "pending"  # unchanged
    finally:
        db.close()
