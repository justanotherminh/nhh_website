"""Bulk announcement tests: audience selection, rendering, and safe delivery.

The delivery tests care most about the properties that make a one-way bulk send
survivable: nobody gets two copies, one bad address doesn't halt the run, and a
partially-finished blast resumes rather than restarts.
"""
from __future__ import annotations

import smtplib

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import Announcement, AnnouncementRecipient, Order
from app.services import announcements as ann
from app.services import orders as orders_svc


@pytest.fixture()
def buyers():
    """Four paid orders across three addresses, plus decoys that must be excluded."""
    db = SessionLocal()
    made = []
    rows = [
        ("sale", "paid", "a@test.invalid", "Người A"),
        ("sale", "paid", "b@test.invalid", "Người B"),
        ("sale", "paid", "c@test.invalid", "Người C"),
        ("sale", "paid", "a@test.invalid", "Người A"),   # 2nd order, same person
        ("sale", "pending", "nope@test.invalid", "Chưa trả"),   # not paid
        ("comp", "paid", "guest@test.invalid", "Khách mời"),    # invitation
    ]
    for kind, status, email, name in rows:
        o = Order(order_code=orders_svc._unique_order_code(db), kind=kind,
                  buyer_name=name, email=email, phone="", amount_vnd=0,
                  status=status)
        db.add(o)
        db.flush()
        made.append(o.id)
    db.commit()
    db.close()
    yield
    db = SessionLocal()
    db.execute(delete(AnnouncementRecipient).where(
        AnnouncementRecipient.email.like("%@test.invalid")))
    db.execute(delete(Order).where(Order.id.in_(made)))
    db.commit()
    db.close()


def _cleanup(announcement_id: int) -> None:
    db = SessionLocal()
    db.execute(delete(AnnouncementRecipient).where(
        AnnouncementRecipient.announcement_id == announcement_id))
    db.execute(delete(Announcement).where(Announcement.id == announcement_id))
    db.commit()
    db.close()


def test_audience_is_paid_buyers_deduped(buyers):
    db = SessionLocal()
    emails = [e for e, _ in ann.audience(db) if e.endswith("@test.invalid")]
    db.close()
    assert sorted(emails) == ["a@test.invalid", "b@test.invalid", "c@test.invalid"]
    # One person with two orders appears once; unpaid and comp are excluded.
    assert emails.count("a@test.invalid") == 1
    assert "guest@test.invalid" not in emails
    assert "nope@test.invalid" not in emails


def test_body_is_escaped_not_interpolated():
    html = ann.render_html("S", 'Xin chào <script>alert("x")</script>')
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_blank_line_starts_a_paragraph_single_newline_breaks():
    html = ann.render_html("S", "Một\nhai\n\nBa")
    assert "Một<br>hai" in html
    assert "<p>Ba</p>" in html


def test_send_delivers_once_each_and_marks_done(buyers, monkeypatch):
    seen = []
    monkeypatch.setattr(ann, "_send", lambda msg: seen.append(msg["To"]))
    db = SessionLocal()
    a = ann.queue(db, "Chủ đề", "Nội dung")
    assert ann.send_batch(db) == ann.progress(db, a.id)["total"]
    # Draining again sends nothing: the unique claim keeps it idempotent.
    assert ann.send_batch(db) == 0
    p = ann.progress(db, a.id)
    assert p["pending"] == 0 and p["failed"] == 0
    assert db.get(Announcement, a.id).status == "sent"
    assert len(seen) == len(set(seen)), "an address was mailed twice"
    db.close()
    _cleanup(a.id)


def test_one_bad_address_does_not_stop_the_rest(buyers, monkeypatch):
    def flaky(msg):
        if msg["To"] == "b@test.invalid":
            raise smtplib.SMTPRecipientsRefused({msg["To"]: (550, b"no such user")})

    monkeypatch.setattr(ann, "_send", flaky)
    db = SessionLocal()
    a = ann.queue(db, "Chủ đề", "Nội dung")
    ann.send_batch(db)
    p = ann.progress(db, a.id)
    assert p["failed"] == 1
    assert p["sent"] == p["total"] - 1
    bad = db.execute(
        select(AnnouncementRecipient).where(
            AnnouncementRecipient.announcement_id == a.id,
            AnnouncementRecipient.status == "failed")
    ).scalar_one()
    assert bad.email == "b@test.invalid" and bad.error
    db.close()
    _cleanup(a.id)


def test_batch_limit_resumes_where_it_stopped(buyers, monkeypatch):
    monkeypatch.setattr(ann, "_send", lambda msg: None)
    db = SessionLocal()
    a = ann.queue(db, "Chủ đề", "Nội dung")
    total = ann.progress(db, a.id)["total"]
    assert ann.send_batch(db, limit=1) == 1
    assert ann.progress(db, a.id)["pending"] == total - 1
    # Simulates the process dying and the scheduler picking it back up.
    while ann.send_batch(db, limit=1):
        pass
    assert ann.progress(db, a.id)["sent"] == total
    db.close()
    _cleanup(a.id)


def test_paused_announcement_is_not_drained(buyers, monkeypatch):
    monkeypatch.setattr(ann, "_send", lambda msg: None)
    db = SessionLocal()
    a = ann.queue(db, "Chủ đề", "Nội dung")
    ann.set_status(db, a.id, "paused")
    assert ann.send_batch(db) == 0
    assert ann.progress(db, a.id)["sent"] == 0
    db.close()
    _cleanup(a.id)
