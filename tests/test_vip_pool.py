"""Editing the VIP / invitation pool from the admin UI.

Membership lives in ``seats.is_vip``. The two operations are deliberately narrow:
a seat may join the pool only while it is genuinely unsold and unheld, and it may
leave the pool only while no invitation PDF has been generated for it — once a
ticket with a live check-in QR is out in the world, the seat must not be sellable
again.
"""
from __future__ import annotations

import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Order, OrderItem, PriceTier, Seat, Ticket, VipTicket
from app.services import tickets as tickets_svc
from app.services import vip as vip_svc


@pytest.fixture(autouse=True)
def _no_email(monkeypatch):
    monkeypatch.setattr(tickets_svc, "send_ticket_email", lambda db, order_code: True)


@pytest.fixture()
def pool_seats():
    """Four throwaway seats: two on sale, two already in the VIP pool."""
    db = SessionLocal()
    tier = PriceTier(name="POOL", price_vnd=400_000)
    db.add(tier)
    db.flush()
    ids = []
    for i in range(4):
        on_sale = i < 2
        s = Seat(
            section="POOL", row_label="Z", seat_number=700 + i,
            label=f"POOL Z{700 + i}", tier_id=tier.id,
            status="available" if on_sale else "blocked",
            is_vip=not on_sale,
        )
        db.add(s)
        db.flush()
        ids.append(s.id)
    db.commit()
    tier_id = tier.id
    db.close()

    yield {"on_sale": ids[:2], "vip": ids[2:], "all": ids}

    db = SessionLocal()
    db.execute(delete(VipTicket).where(VipTicket.seat_id.in_(ids)))
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


def _seat(db, seat_id) -> Seat:
    return db.get(Seat, seat_id)


# ----------------------------------------------------------------- mark_vip

def test_marking_an_available_seat_takes_it_off_sale(pool_seats):
    seat_id = pool_seats["on_sale"][0]
    db = SessionLocal()
    try:
        vip_svc.mark_vip(db, seat_id)
        s = _seat(db, seat_id)
        assert s.is_vip is True
        assert s.status == "blocked"        # gone from the public map and hold API
    finally:
        db.close()


def test_marking_refuses_a_sold_seat(pool_seats):
    seat_id = pool_seats["on_sale"][0]
    db = SessionLocal()
    try:
        db.execute(update(Seat).where(Seat.id == seat_id).values(status="booked"))
        db.commit()
        with pytest.raises(vip_svc.PoolChangeRefused, match="đã được bán"):
            vip_svc.mark_vip(db, seat_id)
        assert _seat(db, seat_id).is_vip is False
    finally:
        db.close()


def test_marking_refuses_a_seat_held_by_a_shopper(pool_seats):
    """A seat someone is mid-checkout on must not be yanked into the pool."""
    seat_id = pool_seats["on_sale"][0]
    db = SessionLocal()
    try:
        db.execute(
            update(Seat).where(Seat.id == seat_id).values(
                held_by_cart=uuid.uuid4(),
                hold_expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5),
            )
        )
        db.commit()
        with pytest.raises(vip_svc.PoolChangeRefused, match="đang được khách giữ"):
            vip_svc.mark_vip(db, seat_id)
        s = _seat(db, seat_id)
        assert s.is_vip is False and s.status == "available"
    finally:
        db.close()


def test_marking_accepts_a_seat_whose_hold_has_lapsed(pool_seats):
    """Holds expire lazily, so an expired hold is just a stale column value."""
    seat_id = pool_seats["on_sale"][0]
    db = SessionLocal()
    try:
        db.execute(
            update(Seat).where(Seat.id == seat_id).values(
                held_by_cart=uuid.uuid4(),
                hold_expires_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1),
            )
        )
        db.commit()
        vip_svc.mark_vip(db, seat_id)
        s = _seat(db, seat_id)
        assert s.is_vip is True and s.held_by_cart is None
    finally:
        db.close()


def test_marking_an_already_vip_seat_is_refused(pool_seats):
    db = SessionLocal()
    try:
        with pytest.raises(vip_svc.PoolChangeRefused, match="đã là ghế vé mời"):
            vip_svc.mark_vip(db, pool_seats["vip"][0])
    finally:
        db.close()


# --------------------------------------------------------------- release_vip

def test_releasing_puts_the_seat_back_on_sale(pool_seats):
    seat_id = pool_seats["vip"][0]
    db = SessionLocal()
    try:
        vip_svc.release_vip(db, seat_id)
        s = _seat(db, seat_id)
        assert s.is_vip is False
        assert s.status == "available"      # sellable again
    finally:
        db.close()


def test_releasing_refuses_a_seat_whose_invitation_was_exported(pool_seats, tmp_path, monkeypatch):
    """The printed ticket's QR stays live, so the seat must not go back on sale."""
    monkeypatch.setattr(settings, "vip_depot_dir", str(tmp_path))
    seat_id = pool_seats["vip"][0]
    db = SessionLocal()
    try:
        vip_svc.export_seat(db, seat_id, "Khách VIP")
        with pytest.raises(vip_svc.PoolChangeRefused, match="đã xuất vé mời"):
            vip_svc.release_vip(db, seat_id)
        s = _seat(db, seat_id)
        assert s.is_vip is True and s.status == "booked"
    finally:
        db.close()


def test_releasing_refuses_a_non_vip_seat(pool_seats):
    db = SessionLocal()
    try:
        with pytest.raises(vip_svc.PoolChangeRefused, match="không phải ghế vé mời"):
            vip_svc.release_vip(db, pool_seats["on_sale"][0])
    finally:
        db.close()


def test_released_seat_can_then_be_held_by_a_buyer(pool_seats):
    """The point of releasing: the seat re-enters the real sale flow."""
    from app.services import holds

    seat_id = pool_seats["vip"][0]
    db = SessionLocal()
    try:
        vip_svc.release_vip(db, seat_id)
        assert holds.acquire(db, seat_id, uuid.uuid4(), 600) is True
    finally:
        db.close()


def test_marked_seat_can_no_longer_be_held(pool_seats):
    """And the converse: marking one pulls it out of reach of the hold API."""
    from app.services import holds

    seat_id = pool_seats["on_sale"][0]
    db = SessionLocal()
    try:
        assert holds.acquire(db, seat_id, uuid.uuid4(), 600) is True  # sellable now
        holds.release_all(db, db.get(Seat, seat_id).held_by_cart)

        vip_svc.mark_vip(db, seat_id)
        assert holds.acquire(db, seat_id, uuid.uuid4(), 600) is False
    finally:
        db.close()


# ------------------------------------------------------------------- the route

def test_apply_endpoint_marks_and_releases_in_one_call(pool_seats, admin_creds):
    c = TestClient(app)
    r = c.post(
        "/admin/vip-seats/apply",
        auth=admin_creds,
        json={"add": [pool_seats["on_sale"][0]], "release": [pool_seats["vip"][0]]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["added"] == 1 and body["released"] == 1 and body["errors"] == []

    db = SessionLocal()
    try:
        assert _seat(db, pool_seats["on_sale"][0]).is_vip is True
        assert _seat(db, pool_seats["vip"][0]).is_vip is False
    finally:
        db.close()


def test_apply_reports_refusals_without_discarding_the_rest(pool_seats, admin_creds):
    """One bad seat in a batch must not cost the manager the good ones."""
    sold, good = pool_seats["on_sale"]
    db = SessionLocal()
    try:
        db.execute(update(Seat).where(Seat.id == sold).values(status="booked"))
        db.commit()
    finally:
        db.close()

    c = TestClient(app)
    body = c.post(
        "/admin/vip-seats/apply", auth=admin_creds, json={"add": [sold, good]}
    ).json()
    assert body["added"] == 1
    assert len(body["errors"]) == 1 and "đã được bán" in body["errors"][0]

    db = SessionLocal()
    try:
        assert _seat(db, good).is_vip is True
        assert _seat(db, sold).is_vip is False
    finally:
        db.close()


def test_apply_requires_admin_auth(pool_seats):
    r = TestClient(app).post(
        "/admin/vip-seats/apply", json={"add": [pool_seats["on_sale"][0]]}
    )
    assert r.status_code == 401


def test_pool_page_renders(pool_seats, admin_creds):
    r = TestClient(app).get("/admin/vip-seats", auth=admin_creds)
    assert r.status_code == 200
    assert 'data-mode="pool"' in r.text


def test_map_flags_held_seats_so_they_are_not_offered(pool_seats, admin_creds):
    seat_id = pool_seats["on_sale"][0]
    db = SessionLocal()
    try:
        db.execute(
            update(Seat).where(Seat.id == seat_id).values(
                held_by_cart=uuid.uuid4(),
                hold_expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5),
            )
        )
        db.commit()
    finally:
        db.close()

    data = TestClient(app).get("/admin/invitations/map", auth=admin_creds).json()
    row = next(s for s in data["seats"] if s["id"] == seat_id)
    assert row["held"] is True
    assert row["vip"] is False
