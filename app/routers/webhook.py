"""payOS webhook: the source of truth for 'paid'.

payOS POSTs a signed payload when a payment completes. We verify the signature
against the checksum key (so a forged request can't book seats), then confirm the
order. Processing is idempotent because payOS may deliver the webhook more than once.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.services import meta_capi, orders, payos_client

log = logging.getLogger("payos.webhook")
router = APIRouter(tags=["webhook"])


def _report_purchase_to_meta(order_code: int) -> None:
    """Tell Meta the sale completed. Runs after the response; never raises.

    Deliberately off the response path. This webhook is how an order becomes paid
    and its seats become booked — if a slow or failing Meta call could delay or
    fail it, payOS would retry and, at worst, a paying customer's seats wouldn't be
    booked. No analytics event is worth that, so this owns its own session and
    swallows everything.
    """
    db = SessionLocal()
    try:
        order = orders.get_order(db, order_code)
        if order is None:
            return
        meta_capi.send_purchase(
            order_code=order.order_code,
            value_vnd=order.amount_vnd,
            fbc=order.fbc,
            fbp=order.fbp,
            num_items=len(order.items),
        )
    except Exception:                # noqa: BLE001 - see docstring
        log.exception("Meta purchase report failed for order %s", order_code)
    finally:
        db.close()


@router.post("/payos/webhook")
async def payos_webhook(
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    body = await request.json()

    try:
        data = payos_client.verify_webhook(body)
    except Exception:
        # Bad/absent signature -> not genuinely from payOS. Ack with success=False
        # (payOS treats a 200 as delivered; we simply don't act on it).
        log.warning("Rejected payOS webhook with invalid signature")
        return {"success": False}

    # payOS sends a probe with a dummy orderCode when registering the webhook;
    # mark_order_paid returns False for unknown orders, which is harmless.
    if str(getattr(data, "code", "")) == "00":
        order_code = int(data.orderCode)
        # mark_order_paid returns True for an order that was *already* paid, so it
        # can't distinguish a first delivery from a re-delivery — and payOS does
        # re-deliver. Check before, so the log line and the Meta report both
        # describe the transition rather than every duplicate.
        existing = orders.get_order(db, order_code)
        first_time = existing is not None and existing.status != "paid"
        if orders.mark_order_paid(db, order_code) and first_time:
            log.info("Order %s marked paid via webhook", order_code)
            if meta_capi.enabled():
                background.add_task(_report_purchase_to_meta, order_code)

    return {"success": True}
