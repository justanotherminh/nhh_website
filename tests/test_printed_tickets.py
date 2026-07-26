"""VIP invitation export: generate a per-recipient PDF, store it in the depot,
and track the exported -> sent lifecycle.

A seat only counts as "exported" once its PDF has been generated and stored; the
map's three states (unexported / exported / sent) all derive from the VipTicket row.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Order, OrderItem, PriceTier, Seat, Ticket, VipTicket
from app.services import orders, tickets, vip


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
    db.execute(delete(VipTicket).where(VipTicket.seat_id.in_(ids)))
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


@pytest.fixture()
def as_vip(monkeypatch, blocked_seats, tmp_path):
    """Treat the throwaway blocked seats as the VIP set, and point the depot at a
    temp dir so generated PDFs don't touch the real one."""
    monkeypatch.setattr("app.routers.admin.reserved_seat_ids", lambda db: set(blocked_seats))
    monkeypatch.setattr(settings, "vip_depot_dir", str(tmp_path))
    return blocked_seats


# ------------------------------------------------------------ ensure_reserved
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
    finally:
        db.close()
    assert sent == []  # no email for exported tickets


def test_ensure_is_idempotent(blocked_seats, monkeypatch):
    monkeypatch.setattr(tickets, "send_ticket_email", lambda db, code: True)
    db = SessionLocal()
    try:
        assert orders.ensure_reserved_tickets(db, blocked_seats) == 3
        assert orders.ensure_reserved_tickets(db, blocked_seats) == 0
    finally:
        db.close()


# --------------------------------------------------------------- map + auth
def test_invitations_map_requires_auth():
    assert TestClient(app).get("/admin/invitations/map").status_code == 401


def test_invitations_map_returns_seat_states(admin_creds):
    c = TestClient(app)
    r = c.get("/admin/invitations/map", auth=admin_creds)
    assert r.status_code == 200
    s0 = r.json()["seats"][0]
    assert "vip" in s0 and "vip_state" in s0
    assert s0["vip_state"] in ("none", "exported", "sent")


def test_export_requires_auth():
    r = TestClient(app).post("/admin/invitations/export", json={"tickets": []})
    assert r.status_code == 401


# ------------------------------------------------------------------ exporting
def test_export_generates_pdf_and_marks_exported(as_vip, admin_creds, tmp_path):
    seat_id = as_vip[0]
    c = TestClient(app)
    r = c.post("/admin/invitations/export", auth=admin_creds,
               json={"tickets": [{"seat_id": seat_id, "name": "Nguyễn Văn A"}]})
    assert r.status_code == 200
    body = r.json()
    assert body["created"] == 1 and not body["errors"]

    db = SessionLocal()
    try:
        vt = db.execute(select(VipTicket).where(VipTicket.seat_id == seat_id)).scalar_one()
        assert vt.recipient_name == "Nguyễn Văn A"
        # A real PDF landed in the depot.
        pdf = tmp_path / vt.pdf_filename
        assert pdf.is_file() and pdf.read_bytes().startswith(b"%PDF")
    finally:
        db.close()

    # The map now reports the seat as exported (not selectable), and re-exporting
    # the same seat is skipped rather than duplicated.
    seats = {s["id"]: s for s in c.get("/admin/invitations/map", auth=admin_creds).json()["seats"]}
    assert seats[seat_id]["vip_state"] == "exported"
    again = c.post("/admin/invitations/export", auth=admin_creds,
                   json={"tickets": [{"seat_id": seat_id, "name": "Ai đó"}]})
    assert again.json()["created"] == 0 and again.json()["skipped"] == 1


def test_export_rejects_non_vip_seat(blocked_seats, admin_creds, monkeypatch, tmp_path):
    # No VIP monkeypatch: these seats are not in the VIP set, so export must refuse.
    monkeypatch.setattr("app.routers.admin.reserved_seat_ids", lambda db: set())
    monkeypatch.setattr(settings, "vip_depot_dir", str(tmp_path))
    c = TestClient(app)
    r = c.post("/admin/invitations/export", auth=admin_creds,
               json={"tickets": [{"seat_id": blocked_seats[0], "name": "X"}]})
    assert r.json()["created"] == 0 and r.json()["errors"]
    db = SessionLocal()
    try:
        assert db.execute(
            select(func.count()).select_from(VipTicket).where(VipTicket.seat_id == blocked_seats[0])
        ).scalar() == 0
    finally:
        db.close()


def test_export_allows_blank_name(as_vip, admin_creds):
    # Names are optional now (the ticket is anonymous/transferable): a blank name
    # still produces a ticket, just with no recipient recorded.
    seat_id = as_vip[0]
    c = TestClient(app)
    r = c.post("/admin/invitations/export", auth=admin_creds,
               json={"tickets": [{"seat_id": seat_id, "name": "   "}]})
    assert r.json()["created"] == 1 and not r.json()["errors"]
    db = SessionLocal()
    try:
        vt = db.execute(select(VipTicket).where(VipTicket.seat_id == seat_id)).scalar_one()
        assert vt.recipient_name == ""
    finally:
        db.close()


# ------------------------------------------------------- download + mark sent
def test_download_pdf_and_mark_sent(as_vip, admin_creds):
    seat_id = as_vip[0]
    c = TestClient(app)
    c.post("/admin/invitations/export", auth=admin_creds,
           json={"tickets": [{"seat_id": seat_id, "name": "Người Nhận"}]})
    db = SessionLocal()
    vt_id = db.execute(select(VipTicket.id).where(VipTicket.seat_id == seat_id)).scalar_one()
    db.close()

    pdf = c.get(f"/admin/invitations/tickets/{vt_id}/pdf", auth=admin_creds)
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")

    sent = c.post(f"/admin/invitations/tickets/{vt_id}/sent", auth=admin_creds,
                  follow_redirects=False)
    assert sent.status_code == 303
    seats = {s["id"]: s for s in c.get("/admin/invitations/map", auth=admin_creds).json()["seats"]}
    assert seats[seat_id]["vip_state"] == "sent"

    # And it can be un-marked.
    c.post(f"/admin/invitations/tickets/{vt_id}/sent", auth=admin_creds,
           data={"undo": "1"}, follow_redirects=False)
    seats = {s["id"]: s for s in c.get("/admin/invitations/map", auth=admin_creds).json()["seats"]}
    assert seats[seat_id]["vip_state"] == "exported"
