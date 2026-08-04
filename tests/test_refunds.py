"""Per-seat refunds: the ledger, the guards, and what a refund does to money,
seat inventory, the door and the mailing list.

Refunds follow a bank transfer a human already made, so the dangerous failures
are silent ones: a refunded QR that still opens the door, money that stays in the
revenue total, or a seat that is neither sold nor sellable.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Order, OrderItem, PriceTier, Refund, Seat, Ticket
from app.services import announcements, holds, orders, refunds, tickets


@pytest.fixture(autouse=True)
def _no_email(monkeypatch):
    monkeypatch.setattr(tickets, "send_ticket_email", lambda db, order_code: True)


@pytest.fixture()
def admin_creds(monkeypatch):
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password", "s3cret-test")
    return ("admin", "s3cret-test")


@pytest.fixture()
def checkin_creds(monkeypatch):
    monkeypatch.setattr(settings, "checkin_username", "cua")
    monkeypatch.setattr(settings, "checkin_password", "door-secret")
    return ("cua", "door-secret")


@pytest.fixture()
def paid_order():
    """A real 3-seat paid sale, built through the normal hold -> order -> paid path.

    Yields (order_code, [seat_ids]) and tears down everything afterwards.
    """
    db = SessionLocal()
    tier = PriceTier(name="RFND", price_vnd=200_000)
    db.add(tier)
    db.flush()
    seat_ids = []
    for i in range(3):
        s = Seat(section="RFND", row_label="Z", seat_number=800 + i,
                 label=f"RFND Z{800 + i}", tier_id=tier.id, status="available")
        db.add(s)
        db.flush()
        seat_ids.append(s.id)
    db.commit()
    tier_id = tier.id

    cart = uuid.uuid4()
    for sid in seat_ids:
        assert holds.acquire(db, sid, cart, 600)
    order = orders.create_order_from_holds(
        db, cart_id=cart, buyer_name="Refund Test", email="refund@x.com",
        phone="0900000000", extend_seconds=900,
    )
    code = order.order_code
    assert orders.mark_order_paid(db, code)
    db.close()

    yield code, seat_ids

    db = SessionLocal()
    oids = db.execute(
        select(OrderItem.order_id).where(OrderItem.seat_id.in_(seat_ids))
    ).scalars().all()
    db.execute(delete(Refund).where(Refund.seat_id.in_(seat_ids)))
    if oids:
        db.execute(delete(Ticket).where(Ticket.order_id.in_(oids)))
        db.execute(delete(OrderItem).where(OrderItem.order_id.in_(oids)))
        db.execute(delete(Order).where(Order.id.in_(oids)))
    db.execute(delete(Seat).where(Seat.id.in_(seat_ids)))
    db.execute(delete(PriceTier).where(PriceTier.id == tier_id))
    db.commit()
    db.close()


def _refund(code, seat_id, **kw):
    db = SessionLocal()
    try:
        return refunds.refund_seat(db, order_code=code, seat_id=seat_id, **kw)
    finally:
        db.close()


# ------------------------------------------------------------ the happy path

def test_refunding_one_seat_frees_it_voids_its_qr_and_leaves_the_rest(paid_order):
    code, seat_ids = paid_order
    target = seat_ids[0]

    r = _refund(code, target, operator="admin", note="CK 12345")
    assert r.amount_vnd == 200_000          # what the seat was actually charged

    db = SessionLocal()
    try:
        # The refunded seat is back on sale; the other two stay sold.
        assert db.get(Seat, target).status == "available"
        assert all(db.get(Seat, s).status == "booked" for s in seat_ids[1:])

        # Its ticket is voided but still present, so history survives.
        t = db.execute(
            select(Ticket).where(Ticket.seat_id == target)
        ).scalar_one()
        assert t.voided_at is not None

        # The order is still paid — the buyer is still coming with two seats.
        assert orders.get_order(db, code).status == "paid"
    finally:
        db.close()


def test_refunded_qr_is_rejected_at_the_door(paid_order, checkin_creds):
    code, seat_ids = paid_order
    db = SessionLocal()
    token = db.execute(
        select(Ticket.qr_token).where(Ticket.seat_id == seat_ids[0])
    ).scalar_one()
    db.close()

    _refund(code, seat_ids[0], operator="admin")

    c = TestClient(app)
    r = c.get(f"/checkin/{token}", auth=checkin_creds)
    assert r.status_code == 410                 # Gone, not 404 — it was real
    assert "VÉ ĐÃ HOÀN" in r.text
    assert "HỢP LỆ" not in r.text

    # And it stayed un-admitted: the void must not be recorded as a check-in.
    db = SessionLocal()
    try:
        t = db.execute(
            select(Ticket).where(Ticket.qr_token == token)
        ).scalar_one()
        assert t.checked_in_at is None
    finally:
        db.close()


def test_refunding_every_seat_marks_the_order_refunded(paid_order):
    code, seat_ids = paid_order
    for sid in seat_ids:
        _refund(code, sid, operator="admin")

    db = SessionLocal()
    try:
        assert orders.get_order(db, code).status == "refunded"
    finally:
        db.close()


def test_refunded_seat_always_goes_back_on_sale(paid_order):
    """There is deliberately no 'retire' option: a refunded seat is always
    resellable. Taking a seat out of the pool is a separate decision, made with
    scripts/block_seats.py, not a side effect of handing money back."""
    code, seat_ids = paid_order
    _refund(code, seat_ids[0], operator="admin")
    db = SessionLocal()
    try:
        assert db.get(Seat, seat_ids[0]).status == "available"
    finally:
        db.close()


# ----------------------------------------------------------------- the guards

def test_refunding_the_same_seat_twice_is_refused(paid_order):
    code, seat_ids = paid_order
    _refund(code, seat_ids[0], operator="admin")
    with pytest.raises(refunds.RefundError, match="đã được hoàn"):
        _refund(code, seat_ids[0], operator="admin")


def test_checked_in_seat_cannot_be_refunded(paid_order, checkin_creds):
    code, seat_ids = paid_order
    db = SessionLocal()
    token = db.execute(
        select(Ticket.qr_token).where(Ticket.seat_id == seat_ids[0])
    ).scalar_one()
    db.close()

    # They walked in...
    assert TestClient(app).get(f"/checkin/{token}", auth=checkin_creds).status_code == 200

    # ...so the one-click refund refuses.
    with pytest.raises(refunds.RefundError, match="soát vào cửa"):
        _refund(code, seat_ids[0], operator="admin")

    db = SessionLocal()
    try:
        assert db.get(Seat, seat_ids[0]).status == "booked"   # still sold
        assert not db.execute(
            select(Refund).where(Refund.seat_id == seat_ids[0])
        ).scalars().all()                                     # no ledger row
    finally:
        db.close()


def test_seat_from_another_order_is_refused(paid_order):
    code, _ = paid_order
    with pytest.raises(refunds.RefundError, match="không thuộc đơn"):
        _refund(code, 999_999_999, operator="admin")


def test_comp_order_is_not_refundable():
    """Invitations carry no money and their tickets may back a printed VIP PDF."""
    db = SessionLocal()
    tier = PriceTier(name="RFC", price_vnd=100_000)
    db.add(tier)
    db.flush()
    seat = Seat(section="RFC", row_label="Z", seat_number=880,
                label="RFC Z880", tier_id=tier.id, status="available")
    db.add(seat)
    db.flush()
    sid, tid = seat.id, tier.id
    order = orders.create_comp_order(db, seat_ids=[sid], guest_name="Comp", email="")
    code = order.order_code
    db.close()
    try:
        with pytest.raises(refunds.RefundError, match="vé mời"):
            _refund(code, sid, operator="admin")
    finally:
        db = SessionLocal()
        db.execute(delete(Ticket).where(Ticket.seat_id == sid))
        db.execute(delete(OrderItem).where(OrderItem.seat_id == sid))
        db.execute(delete(Order).where(Order.order_code == code))
        db.execute(delete(Seat).where(Seat.id == sid))
        db.execute(delete(PriceTier).where(PriceTier.id == tid))
        db.commit()
        db.close()


# -------------------------------------------------------------- money + reach

def test_partial_refund_comes_off_revenue_but_keeps_the_buyer_on_the_mailing_list(paid_order):
    code, seat_ids = paid_order
    db = SessionLocal()
    try:
        before = refunds.refunded_on_live_orders(db)
        emails_before = [e for e, _ in announcements.audience(db)]
    finally:
        db.close()
    assert "refund@x.com" in emails_before

    _refund(code, seat_ids[0], operator="admin")

    db = SessionLocal()
    try:
        # One seat's worth is now netted off the still-'paid' order's revenue.
        assert refunds.refunded_on_live_orders(db) == before + 200_000
        # ...but they keep two seats, so they still get announcements.
        assert "refund@x.com" in [e for e, _ in announcements.audience(db)]
    finally:
        db.close()


def test_full_refund_drops_out_of_revenue_and_the_mailing_list(paid_order):
    code, seat_ids = paid_order
    db = SessionLocal()
    try:
        before = refunds.refunded_on_live_orders(db)
    finally:
        db.close()

    for sid in seat_ids:
        _refund(code, sid, operator="admin")

    db = SessionLocal()
    try:
        # The order left the 'paid' bucket entirely, so its refunds must NOT also
        # be subtracted — that would double-count them out of revenue.
        assert refunds.refunded_on_live_orders(db) == before
        assert "refund@x.com" not in [e for e, _ in announcements.audience(db)]
    finally:
        db.close()


# ------------------------------------------------------------------ admin UI

def test_order_page_requires_auth(paid_order):
    code, _ = paid_order
    assert TestClient(app).get(f"/admin/orders/{code}").status_code == 401


def test_admin_can_refund_a_seat_through_the_form(paid_order, admin_creds):
    code, seat_ids = paid_order
    c = TestClient(app)

    page = c.get(f"/admin/orders/{code}", auth=admin_creds)
    assert page.status_code == 200
    assert "Hoàn tiền theo ghế" in page.text

    r = c.post(f"/admin/orders/{code}/refund", auth=admin_creds,
               follow_redirects=False,
               data={"seat_id": str(seat_ids[0]), "note": "CK 999",
                     "confirm_transferred": "1"})
    assert r.status_code == 303
    assert "error=" not in r.headers["location"]

    db = SessionLocal()
    try:
        row = db.execute(
            select(Refund).where(Refund.seat_id == seat_ids[0])
        ).scalar_one()
        assert row.amount_vnd == 200_000
        assert row.note == "CK 999"
        assert row.refunded_by == "admin"      # who pressed the button
    finally:
        db.close()


def test_form_reports_a_refused_refund_as_an_error(paid_order, admin_creds):
    code, seat_ids = paid_order
    c = TestClient(app)
    data = {"seat_id": str(seat_ids[0]), "note": "", "confirm_transferred": "1"}
    assert c.post(f"/admin/orders/{code}/refund", auth=admin_creds,
                  follow_redirects=False, data=data).status_code == 303
    # Second time round it must not silently succeed.
    again = c.post(f"/admin/orders/{code}/refund", auth=admin_creds,
                   follow_redirects=False, data=data)
    assert "error=" in again.headers["location"]


# ------------------------------------------- the manual-transfer attestation

def test_refund_without_the_confirmation_checkbox_is_refused(paid_order, admin_creds):
    """The checkbox is enforced server-side, not just by HTML `required`.

    Anyone can post the form without it; if that went through, a ticket would be
    voided for a buyer who was never actually paid back.
    """
    code, seat_ids = paid_order
    c = TestClient(app)
    r = c.post(f"/admin/orders/{code}/refund", auth=admin_creds,
               follow_redirects=False,
               data={"seat_id": str(seat_ids[0]), "note": "no tick"})
    assert r.status_code == 303
    assert "error=" in r.headers["location"]

    db = SessionLocal()
    try:
        # Nothing happened at all: seat still sold, ticket live, no ledger row.
        assert db.get(Seat, seat_ids[0]).status == "booked"
        t = db.execute(select(Ticket).where(Ticket.seat_id == seat_ids[0])).scalar_one()
        assert t.voided_at is None
        assert not db.execute(
            select(Refund).where(Refund.seat_id == seat_ids[0])
        ).scalars().all()
    finally:
        db.close()


def test_refund_form_carries_the_attestation_checkbox(paid_order, admin_creds):
    page = TestClient(app).get(f"/admin/orders/{paid_order[0]}", auth=admin_creds).text
    assert 'name="confirm_transferred"' in page
    assert "required" in page
    assert "Tôi đã chuyển khoản" in page


# ----------------------------------------------------------- the ledger page

def test_refunds_ledger_requires_auth():
    assert TestClient(app).get("/admin/refunds").status_code == 401


def test_refunds_ledger_lists_the_refund_with_its_details(paid_order, admin_creds):
    code, seat_ids = paid_order
    _refund(code, seat_ids[0], operator="admin", note="CK VCB 4242")

    page = TestClient(app).get("/admin/refunds", auth=admin_creds)
    assert page.status_code == 200
    assert str(code) in page.text            # linked back to its order
    assert "Refund Test" in page.text        # buyer
    assert "CK VCB 4242" in page.text        # the bank reference


def test_dashboard_links_to_the_ledger(admin_creds):
    page = TestClient(app).get("/admin", auth=admin_creds).text
    assert 'href="/admin/refunds"' in page
