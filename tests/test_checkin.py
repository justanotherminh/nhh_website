"""Door check-in: auth gate, first-scan valid, re-scan used, unknown token invalid."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Order, OrderItem, PriceTier, Seat, Ticket
from app.services import holds, orders, tickets


@pytest.fixture(autouse=True)
def _no_email(monkeypatch):
    monkeypatch.setattr(tickets, "send_ticket_email", lambda db, order_code: True)


@pytest.fixture()
def checkin_creds(monkeypatch):
    monkeypatch.setattr(settings, "checkin_username", "cua")
    monkeypatch.setattr(settings, "checkin_password", "door-secret")
    return ("cua", "door-secret")


@pytest.fixture()
def issued_ticket():
    """A real paid ticket (via a comp order); cleaned up after."""
    db = SessionLocal()
    tier = PriceTier(name="CHK", price_vnd=100_000)
    db.add(tier)
    db.flush()
    seat = Seat(section="CHK", row_label="Z", seat_number=900,
                label="CHK Z900", tier_id=tier.id, status="available")
    db.add(seat)
    db.flush()
    seat_id, tier_id = seat.id, tier.id
    order = orders.create_comp_order(
        db, seat_ids=[seat_id], guest_name="Scan Test", email="s@x.com",
    )
    token = db.execute(
        select(Ticket.qr_token).where(Ticket.order_id == order.id)
    ).scalar_one()
    db.close()

    yield token

    db = SessionLocal()
    db.execute(delete(Ticket).where(Ticket.seat_id == seat_id))
    db.execute(delete(OrderItem).where(OrderItem.seat_id == seat_id))
    oid = db.execute(select(Order.id).where(Order.buyer_name == "Scan Test")).scalars().all()
    if oid:
        db.execute(delete(Order).where(Order.id.in_(oid)))
    db.execute(delete(Seat).where(Seat.id == seat_id))
    db.execute(delete(PriceTier).where(PriceTier.id == tier_id))
    db.commit()
    db.close()


def test_checkin_requires_auth(issued_ticket):
    c = TestClient(app)
    assert c.get(f"/checkin/{issued_ticket}").status_code == 401


def test_first_scan_valid_then_second_scan_used(issued_ticket, checkin_creds):
    c = TestClient(app)
    r1 = c.get(f"/checkin/{issued_ticket}", auth=checkin_creds)
    assert r1.status_code == 200
    assert "HỢP LỆ" in r1.text and "ĐÃ CHECK-IN" not in r1.text

    r2 = c.get(f"/checkin/{issued_ticket}", auth=checkin_creds)
    assert r2.status_code == 409
    assert "ĐÃ CHECK-IN" in r2.text

    # The ticket now carries a check-in timestamp.
    db = SessionLocal()
    try:
        t = db.execute(
            select(Ticket).where(Ticket.qr_token == issued_ticket)
        ).scalar_one()
        assert t.checked_in_at is not None
    finally:
        db.close()


def test_checkin_home_requires_auth_then_shows_ready(checkin_creds):
    """Volunteers pre-authenticate here before doors open."""
    c = TestClient(app)
    assert c.get("/checkin").status_code == 401
    r = c.get("/checkin", auth=checkin_creds)
    assert r.status_code == 200
    assert "SẴN SÀNG" in r.text


def test_unknown_token_invalid(checkin_creds):
    c = TestClient(app)
    r = c.get(f"/checkin/{uuid.uuid4().hex}", auth=checkin_creds)
    assert r.status_code == 404
    assert "KHÔNG HỢP LỆ" in r.text


def test_qr_encodes_checkin_url(issued_ticket):
    # The emailed/displayed QR must point at the door endpoint, not the buyer page.
    assert tickets.checkin_url("abc").endswith("/checkin/abc")
    assert tickets.ticket_url("abc").endswith("/ve/abc")
