"""Admin invitation map + per-seat printable export (VIP tickets on demand)."""
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


def test_ensure_mints_tickets_without_email(blocked_seats, monkeypatch):
    sent = []
    monkeypatch.setattr(tickets, "send_ticket_email",
                        lambda db, code: sent.append(code) or True)
    db = SessionLocal()
    try:
        n = orders.ensure_reserved_tickets(db, blocked_seats)
        assert n == 3
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
    assert sent == []  # no email for exported tickets


def test_ensure_is_idempotent(blocked_seats, monkeypatch):
    monkeypatch.setattr(tickets, "send_ticket_email", lambda db, code: True)
    db = SessionLocal()
    try:
        assert orders.ensure_reserved_tickets(db, blocked_seats) == 3
        # second call: all already have tickets -> nothing minted
        assert orders.ensure_reserved_tickets(db, blocked_seats) == 0
        got = db.execute(
            select(func.count()).select_from(Ticket).where(Ticket.seat_id.in_(blocked_seats))
        ).scalar()
        assert got == 3
    finally:
        db.close()


def test_invitations_map_requires_auth():
    assert TestClient(app).get("/admin/invitations/map").status_code == 401


def test_invitations_map_returns_seats(admin_creds):
    c = TestClient(app)
    r = c.get("/admin/invitations/map", auth=admin_creds)
    assert r.status_code == 200
    data = r.json()
    assert "seats" in data and data["seats"]
    # every seat carries the admin annotations
    s0 = data["seats"][0]
    assert "vip" in s0 and "exported" in s0


def test_print_requires_auth():
    assert TestClient(app).post("/admin/invitations/print", data={"seat_ids": "1"}).status_code == 401


def test_print_only_touches_vip_seats(blocked_seats, admin_creds, monkeypatch):
    # These throwaway seats are NOT in the VIP CSV, so the print route must ignore
    # them (mint nothing) and render an empty sheet.
    monkeypatch.setattr(tickets, "send_ticket_email", lambda db, code: True)
    c = TestClient(app)
    ids = ",".join(str(i) for i in blocked_seats)
    r = c.post("/admin/invitations/print", auth=admin_creds, data={"seat_ids": ids})
    assert r.status_code == 200
    db = SessionLocal()
    try:
        minted = db.execute(
            select(func.count()).select_from(Ticket).where(Ticket.seat_id.in_(blocked_seats))
        ).scalar()
        assert minted == 0  # non-VIP seats never get tickets from this route
    finally:
        db.close()
