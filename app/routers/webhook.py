"""payOS webhook: the source of truth for 'paid'.

payOS POSTs a signed payload when a payment completes. We verify the signature
against the checksum key (so a forged request can't book seats), then confirm the
order. Processing is idempotent because payOS may deliver the webhook more than once.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import orders, payos_client

log = logging.getLogger("payos.webhook")
router = APIRouter(tags=["webhook"])


@router.post("/payos/webhook")
async def payos_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
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
        if orders.mark_order_paid(db, order_code):
            log.info("Order %s marked paid via webhook", order_code)

    return {"success": True}
