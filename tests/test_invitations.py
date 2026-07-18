"""Invitation (comp) ticket tests: issuance, all-or-nothing booking, pool reservation."""
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
    """Don't hit SMTP during tests; issuance treats email as best-effort anyway."""
    monkeypatch.setattr(tickets, "send_ticket_email", lambda db, order_code: True)


@pytest.fixture()
def seats3():
    """Three fresh available seats in a throwaway tier; cleaned up after."""
    db = SessionLocal()
    tier = PriceTier(name="INV", price_vnd=200_000)
    db.add(tier)
    db.flush()
    ids = []
    for i in range(3):
        s = Seat(section="INV", row_label="Z", seat_number=800 + i,
                 label=f"INV Z{800 + i}", tier_id=tier.id, status="available")
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


def test_create_comp_books_seats_and_mints_tickets(seats3):
    db = SessionLocal()
    try:
        order = orders.create_comp_order(
            db, seat_ids=seats3[:2], guest_name="Khách Mời",
            email="guest@example.com", phone="",
        )
        assert order.kind == "comp"
        assert order.status == "paid"
        assert order.amount_vnd == 0
        assert len(order.items) == 2

        statuses = db.execute(
            select(Seat.status).where(Seat.id.in_(seats3[:2]))
        ).scalars().all()
        assert all(s == "booked" for s in statuses)

        n_tickets = db.execute(
            select(Ticket).where(Ticket.order_id == order.id)
        ).scalars().all()
        assert len(n_tickets) == 2
        # The third seat is untouched.
        assert db.get(Seat, seats3[2]).status == "available"
    finally:
        db.close()


def test_comp_from_blocked_pool(seats3):
    """A reserved (blocked) seat can be issued as an invitation."""
    db = SessionLocal()
    try:
        db.execute(
            Seat.__table__.update().where(Seat.id == seats3[0]).values(status="blocked")
        )
        db.commit()
        order = orders.create_comp_order(
            db, seat_ids=[seats3[0]], guest_name="VIP", email="vip@example.com",
        )
        assert order.status == "paid"
        assert db.get(Seat, seats3[0]).status == "booked"
    finally:
        db.close()


def test_comp_is_all_or_nothing(seats3):
    """If any seat is already booked, nothing is booked and it raises."""
    db = SessionLocal()
    try:
        # Sell the middle seat first.
        orders.create_comp_order(db, seat_ids=[seats3[1]], guest_name="A", email="a@x.com")
        with pytest.raises(orders.SeatsNotBookable):
            orders.create_comp_order(
                db, seat_ids=[seats3[0], seats3[1]], guest_name="B", email="b@x.com",
            )
        # seats3[0] must NOT have been booked by the failed call.
        assert db.get(Seat, seats3[0]).status == "available"
    finally:
        db.close()


def test_blocked_seat_cannot_be_held_publicly(seats3):
    """Reserving a seat removes it from public sale (hold is refused)."""
    db = SessionLocal()
    try:
        db.execute(
            Seat.__table__.update().where(Seat.id == seats3[0]).values(status="blocked")
        )
        db.commit()
        assert holds.acquire(db, seats3[0], uuid.uuid4(), 600) is False
    finally:
        db.close()


def test_public_hold_endpoint_refuses_blocked_seat(seats3):
    """The buyer-facing /api/hold endpoint rejects a VIP (blocked) seat."""
    db = SessionLocal()
    db.execute(
        Seat.__table__.update().where(Seat.id == seats3[0]).values(status="blocked")
    )
    db.commit()
    db.close()
    c = TestClient(app)
    r = c.post("/api/hold", json={"seat_id": seats3[0]})
    assert r.status_code == 409  # cannot hold -> cannot buy


def test_admin_cannot_lock_seats():
    """Seat reservation is single-source (the CSV importer); the admin has no
    block/unblock endpoints, so those routes must not exist."""
    c = TestClient(app)
    assert c.post("/admin/invitations/block", data={"identifiers": "1"}).status_code == 404
    assert c.post("/admin/invitations/unblock/1").status_code == 404
