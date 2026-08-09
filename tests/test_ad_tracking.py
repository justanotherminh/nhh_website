"""Ad attribution, the Meta Pixel, and the Conversions API.

Three properties matter more than the rest and are tested hardest:

* tracking is completely inert until it's configured, so dev and tests never
  touch production ad data;
* the Pixel and CAPI agree on one event id, or every sale gets counted twice;
* a broken Meta call can never break the payOS webhook, which is how a paying
  customer's seats get booked.
"""
from __future__ import annotations

import types
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Order, OrderItem, PriceTier, Seat, Ticket
from app.services import attribution, holds, meta_capi, orders, payos_client
from app.services import tickets as tickets_svc


@pytest.fixture(autouse=True)
def _no_email(monkeypatch):
    monkeypatch.setattr(tickets_svc, "send_ticket_email", lambda db, order_code: True)


@pytest.fixture(autouse=True)
def _tracking_off(monkeypatch):
    """Every test starts with tracking disabled; opt in explicitly."""
    monkeypatch.setattr(settings, "meta_pixel_id", "")
    monkeypatch.setattr(settings, "meta_capi_token", "")
    monkeypatch.setattr(settings, "meta_test_event_code", "")


@pytest.fixture()
def ad_seats():
    """Two throwaway seats in their own tier."""
    db = SessionLocal()
    tier = PriceTier(name="ADS", price_vnd=700_000)
    db.add(tier)
    db.flush()
    ids = []
    for i in range(2):
        s = Seat(section="ADS", row_label="Z", seat_number=600 + i,
                 label=f"ADS Z{600 + i}", tier_id=tier.id, status="available")
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


# --------------------------------------------------------------- capture

def test_landing_with_utm_sets_the_cookie():
    c = TestClient(app)
    c.get("/?utm_source=facebook&utm_campaign=post-aug06&utm_content=video-a")
    got = attribution.decode(c.cookies.get(attribution.COOKIE))
    assert got["source"] == "facebook"
    assert got["utm_campaign"] == "post-aug06"
    assert got["utm_content"] == "video-a"


def test_fbclid_alone_counts_as_facebook():
    """Facebook appends fbclid even to links nobody tagged."""
    c = TestClient(app)
    c.get("/?fbclid=AbCdEf")
    got = attribution.decode(c.cookies.get(attribution.COOKIE))
    assert got["source"] == "facebook"
    assert got["fbc"].startswith("fb.1.")
    assert got["fbc"].endswith(".AbCdEf")


def test_untagged_visit_sets_nothing():
    c = TestClient(app)
    c.get("/")
    assert c.cookies.get(attribution.COOKIE) is None


def test_ordinary_navigation_does_not_clear_attribution():
    c = TestClient(app)
    c.get("/?utm_source=facebook&utm_campaign=first")
    before = c.cookies.get(attribution.COOKIE)
    c.get("/tickets")
    c.get("/")
    assert c.cookies.get(attribution.COOKIE) == before


def test_a_second_ad_click_wins():
    """Last touch: the click that actually brought them back gets the credit."""
    c = TestClient(app)
    c.get("/?utm_source=facebook&utm_campaign=first")
    c.get("/?utm_source=facebook&utm_campaign=second")
    got = attribution.decode(c.cookies.get(attribution.COOKIE))
    assert got["utm_campaign"] == "second"


def test_a_corrupt_cookie_reads_as_no_attribution():
    assert attribution.decode("!!!not-base64!!!") == {}
    assert attribution.decode("") == {}
    assert attribution.decode(None) == {}


def test_overlong_values_are_truncated_not_rejected():
    """Query parameters are attacker-controlled; they must not overflow the column."""
    got = attribution.from_query({"utm_source": "f" * 500, "utm_campaign": "c" * 500})
    assert len(got["source"]) == 60
    assert len(got["utm_campaign"]) == 120


# ------------------------------------------------------- stamped on the order

def test_order_records_where_the_buyer_came_from(ad_seats):
    c = TestClient(app)
    c.get("/?utm_source=facebook&utm_campaign=post-aug06&fbclid=XYZ")
    c.cookies.set("_fbp", "fb.1.1700000000000.987654321")

    for sid in ad_seats:
        r = c.post("/api/hold", json={"seat_id": sid})
        assert r.status_code == 200, r.text
    r = c.post("/checkout", data={"buyer_name": "Người mua", "email": "b@x.com",
                                  "phone": "0900000123"}, follow_redirects=False)
    assert r.status_code == 303

    db = SessionLocal()
    try:
        o = db.execute(
            select(Order).where(Order.email == "b@x.com").order_by(Order.id.desc())
        ).scalars().first()
        assert o is not None
        assert o.source == "facebook"
        assert o.utm_campaign == "post-aug06"
        assert o.fbc.endswith(".XYZ")
        assert o.fbp == "fb.1.1700000000000.987654321"
    finally:
        db.close()


def test_untagged_order_has_null_attribution(ad_seats):
    c = TestClient(app)
    for sid in ad_seats:
        assert c.post("/api/hold", json={"seat_id": sid}).status_code == 200
    c.post("/checkout", data={"buyer_name": "Khách", "email": "plain@x.com",
                              "phone": "0900000124"}, follow_redirects=False)
    db = SessionLocal()
    try:
        o = db.execute(
            select(Order).where(Order.email == "plain@x.com").order_by(Order.id.desc())
        ).scalars().first()
        assert o.source is None and o.fbc is None
    finally:
        db.close()


# ------------------------------------------------------------------- pixel

def test_no_pixel_markup_when_unconfigured():
    body = TestClient(app).get("/").text
    assert "fbevents.js" not in body
    assert "fbq(" not in body


def test_pixel_renders_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "meta_pixel_id", "1234567890")
    body = TestClient(app).get("/").text
    assert "fbevents.js" in body
    assert '"1234567890"' in body
    # Automatic Advanced Matching must stay off until the privacy call is made.
    assert "autoConfig', false" in body


def test_seatmap_fires_viewcontent(monkeypatch):
    monkeypatch.setattr(settings, "meta_pixel_id", "1234567890")
    assert "'ViewContent'" in TestClient(app).get("/tickets").text


# --------------------------------------------------------------- CAPI payload

def test_purchase_payload_shape():
    body = meta_capi.build_purchase(
        order_code=12345, value_vnd=700_000,
        fbc="fb.1.1700000000000.abc", fbp="fb.1.1700000000000.999",
        event_time=1700000000, num_items=2,
    )
    ev = body["data"][0]
    assert ev["event_name"] == "Purchase"
    assert ev["action_source"] == "website"
    assert ev["user_data"] == {"fbc": "fb.1.1700000000000.abc",
                               "fbp": "fb.1.1700000000000.999"}
    assert ev["custom_data"]["currency"] == "VND"
    assert "test_event_code" not in body


def test_vnd_value_is_sent_unscaled():
    """VND is zero-decimal. Scaling by 100 would inflate every ROAS report 100x."""
    body = meta_capi.build_purchase(order_code=1, value_vnd=700_000)
    assert body["data"][0]["custom_data"]["value"] == 700_000


def test_event_id_is_the_order_code_so_meta_can_dedupe():
    """The Pixel sends the same id on /checkout/success; a mismatch double-counts."""
    body = meta_capi.build_purchase(order_code=987654, value_vnd=1)
    assert body["data"][0]["event_id"] == "987654"


def test_test_event_code_is_included_when_set(monkeypatch):
    monkeypatch.setattr(settings, "meta_test_event_code", "TEST123")
    body = meta_capi.build_purchase(order_code=1, value_vnd=1)
    assert body["test_event_code"] == "TEST123"


def test_send_is_a_noop_when_unconfigured():
    assert meta_capi.send_purchase(order_code=1, value_vnd=1, fbc="fb.1.1.x") is False


def test_send_skips_when_there_is_nothing_to_match_on(monkeypatch):
    """No fbc and no fbp: Meta would reject it, and it isn't ad-driven anyway."""
    monkeypatch.setattr(settings, "meta_pixel_id", "123")
    monkeypatch.setattr(settings, "meta_capi_token", "tok")
    called = []
    monkeypatch.setattr("httpx.post", lambda *a, **k: called.append(1))
    assert meta_capi.send_purchase(order_code=1, value_vnd=1) is False
    assert called == []


def test_send_swallows_a_network_failure(monkeypatch):
    monkeypatch.setattr(settings, "meta_pixel_id", "123")
    monkeypatch.setattr(settings, "meta_capi_token", "tok")

    def _boom(*a, **k):
        raise RuntimeError("connection reset")

    monkeypatch.setattr("httpx.post", _boom)
    assert meta_capi.send_purchase(order_code=1, value_vnd=1, fbc="fb.1.1.x") is False


# ------------------------------------------- the one that really matters

def _fake_webhook_data(order_code):
    return types.SimpleNamespace(orderCode=order_code, code="00")


def _pending_order(seat_ids, email):
    cart = uuid.uuid4()
    db = SessionLocal()
    try:
        for sid in seat_ids:
            assert holds.acquire(db, sid, cart, 600)
        order = orders.create_order_from_holds(
            db, cart_id=cart, buyer_name="B", email=email, phone="0900000002",
            extend_seconds=900,
            attribution={"source": "facebook", "utm_campaign": "c",
                         "utm_content": None, "fbc": "fb.1.1700000000000.abc",
                         "fbp": None},
        )
        return order.order_code
    finally:
        db.close()


def test_a_failing_meta_call_cannot_break_the_webhook(ad_seats, monkeypatch):
    """The webhook is how seats get booked. Analytics must never endanger that."""
    order_code = _pending_order(ad_seats, "capi-fail@x.com")
    monkeypatch.setattr(settings, "meta_pixel_id", "123")
    monkeypatch.setattr(settings, "meta_capi_token", "tok")
    monkeypatch.setattr(
        payos_client, "verify_webhook", lambda body: _fake_webhook_data(order_code)
    )

    def _boom(*a, **k):
        raise RuntimeError("Meta is down")

    monkeypatch.setattr("httpx.post", _boom)

    c = TestClient(app)
    r = c.post("/payos/webhook", json={"code": "00", "data": {"orderCode": order_code}})

    assert r.status_code == 200
    assert r.json() == {"success": True}

    db = SessionLocal()
    try:
        o = orders.get_order(db, order_code)
        assert o.status == "paid"          # the sale completed regardless
        n = db.execute(select(Ticket).where(Ticket.order_id == o.id)).scalars().all()
        assert len(n) == len(ad_seats)     # and the tickets were minted
    finally:
        db.close()


def test_purchase_is_reported_once_not_on_every_redelivery(ad_seats, monkeypatch):
    """payOS re-delivers webhooks; only the transition to paid should report."""
    order_code = _pending_order(ad_seats, "capi-once@x.com")
    monkeypatch.setattr(settings, "meta_pixel_id", "123")
    monkeypatch.setattr(settings, "meta_capi_token", "tok")
    monkeypatch.setattr(
        payos_client, "verify_webhook", lambda body: _fake_webhook_data(order_code)
    )

    sent: list[dict] = []

    class _Resp:
        status_code = 200
        text = "{}"

    monkeypatch.setattr("httpx.post", lambda *a, **k: sent.append(k.get("json")) or _Resp())

    c = TestClient(app)
    body = {"code": "00", "data": {"orderCode": order_code}}
    c.post("/payos/webhook", json=body)
    c.post("/payos/webhook", json=body)      # payOS re-delivers

    assert len(sent) == 1
    assert sent[0]["data"][0]["event_id"] == str(order_code)
    assert sent[0]["data"][0]["custom_data"]["value"] > 0
