"""Pre-generated (printable) VIP tickets: bulk mint without email, and the print sheet."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Order, OrderItem, PriceTier, Seat, Ticket
from app.services import orders, tickets


@pytest.fixture()
def blocked_seats():
    """Three reserved (blocked) seats in a throwaway tier; cleaned up after."""
    db = SessionLocal()
    tier = PriceTier(name="PR", price_vnd=100_000)
    db.add(tier)
    db.flush()
    ids = []
    for i in range(3):
        s = Seat(section="PR", row_label="Z", seat_number=960 + i,
                 label=f"PR Z{960 + i}", tier_id=tier.id, status="blocked")
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


@pytest.fixture()
def admin_creds(monkeypatch):
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password", "s3cret-test")
    return ("admin", "s3cret-test")


def test_generate_mints_tickets_without_email(blocked_seats, monkeypatch):
    sent = []
    monkeypatch.setattr(tickets, "send_ticket_email",
                        lambda db, code: sent.append(code) or True)
    db = SessionLocal()
    try:
        n = orders.generate_reserved_tickets(db)
        assert n >= 3
        # our 3 blocked seats are now booked with tickets
        statuses = db.execute(
            select(Seat.status).where(Seat.id.in_(blocked_seats))
        ).scalars().all()
        assert all(s == "booked" for s in statuses)
        got = db.execute(
            select(func.count()).select_from(Ticket).where(Ticket.seat_id.in_(blocked_seats))
        ).scalar()
        assert got == 3
    finally:
        db.close()
    assert sent == []  # no email sent for pre-generated tickets


def test_generate_is_idempotent(blocked_seats, monkeypatch):
    monkeypatch.setattr(tickets, "send_ticket_email", lambda db, code: True)
    db = SessionLocal()
    try:
        orders.generate_reserved_tickets(db)
        # second run: our seats are already booked, so no new tickets for them
        orders.generate_reserved_tickets(db)
        got = db.execute(
            select(func.count()).select_from(Ticket).where(Ticket.seat_id.in_(blocked_seats))
        ).scalar()
        assert got == 3
    finally:
        db.close()


def test_print_page_renders_qr_cards(blocked_seats, admin_creds, monkeypatch):
    monkeypatch.setattr(tickets, "send_ticket_email", lambda db, code: True)
    db = SessionLocal()
    try:
        orders.generate_reserved_tickets(db)
    finally:
        db.close()
    c = TestClient(app)
    r = c.get("/admin/invitations/print", auth=admin_creds)
    assert r.status_code == 200
    assert "Kính mời:" in r.text
    assert "data:image/png;base64," in r.text  # embedded QR
    for sid_label in ("PR Z960", "PR Z961", "PR Z962"):
        assert sid_label in r.text


def test_print_page_requires_auth():
    assert TestClient(app).get("/admin/invitations/print").status_code == 401
