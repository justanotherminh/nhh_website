"""E-ticket tests: QR generation, the tokenized ticket page, and email delivery.

Hit real Postgres like the other suites. Email is redirected to a fake SMTP
sender (monkeypatch) so tests don't depend on Mailpit being up.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.main import app
from app.models import Order, OrderItem, PriceTier, Seat, Ticket
from app.services import orders
from app.services import tickets as ticket_svc


@pytest.fixture()
def throwaway_seats():
    db = SessionLocal()
    tier = PriceTier(name="TEST", price_vnd=100_000)
    db.add(tier)
    db.flush()
    ids = []
    for i in range(2):
        s = Seat(section="TEST", row_label="Z", seat_number=800 + i,
                 label=f"TEST Z{800 + i}", tier_id=tier.id, status="available")
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
def capture_email(monkeypatch):
    """Intercept the SMTP send so tests don't need a mail server, and let us
    assert an email was actually built and 'sent'."""
    sent = []
    monkeypatch.setattr(ticket_svc, "_send", lambda msg: sent.append(msg))
    return sent


def _paid_order(seat_ids):
    from app.services import holds
    cart = uuid.uuid4()
    db = SessionLocal()
    for sid in seat_ids:
        assert holds.acquire(db, sid, cart, 600)
    order = orders.create_order_from_holds(
        db, cart_id=cart, buyer_name="E Nguyen", email="e@example.com",
        phone="0900000009", extend_seconds=900,
    )
    code = order.order_code
    db.close()
    return code


def test_qr_png_is_valid_image():
    b = ticket_svc.qr_png_bytes("some-token-123")
    assert b[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic number
    assert len(b) > 100


def test_paid_order_sends_email_with_qr_per_seat(throwaway_seats, capture_email):
    code = _paid_order(throwaway_seats)
    db = SessionLocal()
    try:
        assert orders.mark_order_paid(db, code) is True
    finally:
        db.close()

    assert len(capture_email) == 1
    msg = capture_email[0]
    assert msg["To"] == "e@example.com"
    # One inline QR image per seat is embedded (related parts on the HTML alt).
    images = [p for p in msg.walk() if p.get_content_type() == "image/png"]
    assert len(images) == len(throwaway_seats)


def test_ticket_page_renders_for_valid_token(throwaway_seats, capture_email):
    code = _paid_order(throwaway_seats)
    db = SessionLocal()
    try:
        orders.mark_order_paid(db, code)
        token = db.execute(
            select(Ticket.qr_token).join(Order).where(Order.order_code == code).limit(1)
        ).scalar()
    finally:
        db.close()

    c = TestClient(app)
    page = c.get(f"/ve/{token}")
    assert page.status_code == 200
    assert "TEST Z80" in page.text  # the seat label shows on the ticket

    png = c.get(f"/ve/{token}/qr.png")
    assert png.status_code == 200
    assert png.headers["content-type"] == "image/png"
    assert png.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_ticket_page_404_for_unknown_token():
    c = TestClient(app)
    assert c.get("/ve/definitely-not-a-real-token").status_code == 404
    assert c.get("/ve/definitely-not-a-real-token/qr.png").status_code == 404
