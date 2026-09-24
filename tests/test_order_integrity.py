"""One seat, one live ticket: the order lifecycle under late, repeated and
concurrent payments, and under cancels and expiries that race other buyers.

Each test here was first written as a reproduction against the previous code,
where it failed by producing a second valid ticket for a seat or by touching
another buyer's order.
"""
from __future__ import annotations

import datetime as dt
import threading
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, update

from app.db import SessionLocal
from app.main import app
from app.models import Order, OrderItem, PriceTier, Seat, Ticket
from app.routers import checkout as checkout_router
from app.services import holds, orders


@pytest.fixture()
def seats(monkeypatch):
    # Never touch the network to void a payOS link.
    monkeypatch.setattr(orders, "payos_client_configured", lambda: False)
    db = SessionLocal()
    tier = PriceTier(name="TEST", price_vnd=100_000)
    db.add(tier)
    db.flush()
    ids = []
    for i in range(2):
        s = Seat(section="TEST", row_label="Y", seat_number=800 + i,
                 label=f"TEST Y{800 + i}", tier_id=tier.id, status="available")
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


def _pending(seat_ids, cart=None) -> tuple[int, uuid.UUID]:
    cart = cart or uuid.uuid4()
    db = SessionLocal()
    try:
        for sid in seat_ids:
            assert holds.acquire(db, sid, cart, 600)
        o = orders.create_order_from_holds(
            db, cart_id=cart, buyer_name="Integrity", email="i@x.com",
            phone="0900000000", extend_seconds=900,
        )
        return o.order_code, cart
    finally:
        db.close()


def _pay(code) -> bool:
    db = SessionLocal()
    try:
        return orders.mark_order_paid(db, code)
    finally:
        db.close()


def _status(code) -> str:
    db = SessionLocal()
    try:
        return orders.get_order(db, code).status
    finally:
        db.close()


def _live_tickets(seat_id) -> int:
    db = SessionLocal()
    try:
        return db.execute(
            select(func.count()).select_from(Ticket)
            .where(Ticket.seat_id == seat_id, Ticket.voided_at.is_(None))
        ).scalar_one()
    finally:
        db.close()


def _expire(code) -> None:
    """Age an order past its payment window and let the sweeper cancel it."""
    db = SessionLocal()
    try:
        db.execute(
            update(Order).where(Order.order_code == code)
            .values(created_at=func.now() - dt.timedelta(hours=1))
        )
        db.commit()
        assert orders.expire_stale_orders(db, older_than_seconds=60) >= 1
    finally:
        db.close()


def _lapse_holds(seat_ids) -> None:
    db = SessionLocal()
    try:
        db.execute(
            update(Seat).where(Seat.id.in_(seat_ids))
            .values(hold_expires_at=func.now() - dt.timedelta(seconds=1))
        )
        db.commit()
    finally:
        db.close()


# ------------------------------------------------------------ late payments
def test_late_payment_is_honoured_while_the_seat_is_still_free(seats):
    code, _ = _pending(seats[:1])
    _expire(code)
    assert _status(code) == "cancelled"

    assert _pay(code) is True               # nobody else wanted it: honour the payment
    assert _status(code) == "paid"
    assert _live_tickets(seats[0]) == 1


def test_late_payment_after_the_seat_was_resold_needs_refund(seats):
    s = seats[0]
    late, _ = _pending([s])
    _expire(late)
    buyer_b, _ = _pending([s])              # the freed seat is sold on
    assert _pay(buyer_b) is True

    assert _pay(late) is False              # the late payment lands
    assert _status(late) == "needs_refund"
    assert _live_tickets(s) == 1            # still only B's ticket
    assert _status(buyer_b) == "paid"

    # A redelivery of the late payment changes nothing.
    assert _pay(late) is False
    assert _status(late) == "needs_refund"
    assert _live_tickets(s) == 1


def test_a_partly_resold_order_claims_none_of_its_seats(seats):
    late, _ = _pending(seats)               # both seats
    _expire(late)
    other, _ = _pending(seats[1:])          # only the second is resold
    assert _pay(other) is True

    assert _pay(late) is False
    db = SessionLocal()
    try:
        # All-or-nothing: the still-free first seat was not booked on its own.
        assert db.get(Seat, seats[0]).status == "available"
    finally:
        db.close()
    assert _live_tickets(seats[0]) == 0


# ------------------------------------------------------ concurrent webhooks
def test_concurrent_deliveries_mint_one_set_of_tickets(seats, monkeypatch):
    code, _ = _pending(seats)

    # Hold each delivery inside its read-status -> commit window for a moment, so
    # the deliveries genuinely overlap there instead of racing past each other.
    # Without this the window is a few milliseconds and the test passes whether
    # or not the order is locked.
    real_ticket = orders.Ticket

    def slow_ticket(*args, **kwargs):
        time.sleep(0.2)
        return real_ticket(*args, **kwargs)

    monkeypatch.setattr(orders, "Ticket", slow_ticket)

    n = 4
    barrier = threading.Barrier(n)
    results: list[bool] = []

    def deliver():
        db = SessionLocal()
        try:
            barrier.wait(timeout=10)
            results.append(orders.mark_order_paid(db, code))
        finally:
            db.close()

    threads = [threading.Thread(target=deliver) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert results == [True] * n
    assert _status(code) == "paid"
    assert [_live_tickets(s) for s in seats] == [1, 1]


# ---------------------------------------------------------------- cancelling
@pytest.fixture()
def dev_pay(monkeypatch):
    monkeypatch.setattr(checkout_router, "_use_dev_payments", lambda: True)


def _checkout_via_browser(client: TestClient, seat_id: int) -> int:
    assert client.post("/api/hold", json={"seat_id": seat_id}).status_code == 200
    r = client.post("/checkout", data={"buyer_name": "B", "email": "b@x.com",
                                       "phone": "0900000000"}, follow_redirects=False)
    return int(r.headers["location"].rsplit("order=", 1)[1])


def test_cancel_link_does_nothing_for_anyone_but_the_buyer(seats, dev_pay):
    buyer = TestClient(app)
    code = _checkout_via_browser(buyer, seats[0])

    stranger = TestClient(app)               # no cart cookie
    assert stranger.get(f"/checkout/cancel?order={code}").status_code == 200
    assert _status(code) == "pending"
    db = SessionLocal()
    try:
        assert db.get(Seat, seats[0]).held_by_cart is not None
    finally:
        db.close()

    buyer.get(f"/checkout/cancel?order={code}")   # payOS sends the buyer here
    assert _status(code) == "cancelled"
    db = SessionLocal()
    try:
        assert db.get(Seat, seats[0]).held_by_cart is None
    finally:
        db.close()


def test_cancel_only_moves_pending_orders(seats):
    late, _ = _pending(seats[:1])
    _expire(late)
    other, _ = _pending(seats[:1])
    _pay(other)
    _pay(late)
    assert _status(late) == "needs_refund"

    db = SessionLocal()
    try:
        # The flag a manager must act on can't be erased by the cancel path...
        assert orders.cancel_order(db, late) is False
        # ...and nor can a paid order be cancelled.
        assert orders.cancel_order(db, other) is False
    finally:
        db.close()
    assert _status(late) == "needs_refund"
    assert _status(other) == "paid"


def test_expiry_leaves_another_carts_hold_alone(seats):
    s = seats[0]
    code, _ = _pending([s])
    _lapse_holds([s])                        # A's hold lapses before the sweep...
    cart_b = uuid.uuid4()
    db = SessionLocal()
    try:
        assert holds.acquire(db, s, cart_b, 600)   # ...and B picks the seat up
    finally:
        db.close()

    _expire(code)

    db = SessionLocal()
    try:
        assert holds.own_held_seat_ids(db, cart_b) == [s]
    finally:
        db.close()


# ------------------------------------------------------- resubmitted checkout
def test_resubmitting_checkout_supersedes_the_earlier_order(seats):
    s = seats[0]
    first, cart = _pending([s])
    second, _ = _pending([s], cart=cart)    # Back from payOS, submit again

    assert _status(first) == "cancelled"
    assert _status(second) == "pending"

    # Even if both links get paid, the seat gets one ticket; the other payment
    # is flagged for a refund.
    assert _pay(second) is True
    assert _pay(first) is False
    assert _status(first) == "needs_refund"
    assert _live_tickets(s) == 1


# ------------------------------------------------------------ what people see
def test_needs_refund_is_shown_to_buyer_and_managers(seats, monkeypatch):
    from app.config import settings

    late, _ = _pending(seats[:1])
    _expire(late)
    other, _ = _pending(seats[:1])
    _pay(other)
    _pay(late)

    page = TestClient(app).get(f"/checkout/success?order={late}").text
    assert "Đã nhận thanh toán — nhưng ghế không còn" in page

    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password", "s3cret-test")
    dash = TestClient(app).get("/admin", auth=("admin", "s3cret-test")).text
    assert "cần hoàn tiền thủ công" in dash
    assert f"/admin/orders/{late}" in dash
