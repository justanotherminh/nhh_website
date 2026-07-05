"""Thin wrapper around the payOS SDK.

The rest of the app talks to *this* module, never to ``payos`` directly, so the
SDK details (and its deprecation churn) stay in one place and the checkout flow
is trivial to mock in tests. When credentials aren't configured, ``is_configured``
returns False and callers fall back to the local dev-pay simulator.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any

from app.config import settings


@dataclass
class PaymentLink:
    checkout_url: str
    payment_link_id: str
    qr_code: str
    order_code: int


def is_configured() -> bool:
    return bool(
        settings.payos_client_id
        and settings.payos_api_key
        and settings.payos_checksum_key
    )


def _client():
    from payos import PayOS

    return PayOS(
        client_id=settings.payos_client_id,
        api_key=settings.payos_api_key,
        checksum_key=settings.payos_checksum_key,
    )


def create_payment_link(
    *,
    order_code: int,
    amount: int,
    description: str,
    items: list[dict],
    return_url: str,
    cancel_url: str,
    buyer_name: str,
    buyer_email: str,
    buyer_phone: str,
    expired_at: int | None = None,
) -> PaymentLink:
    """Create a payOS payment link and return its checkout URL + link id."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from payos.type import ItemData, PaymentData

        payment = PaymentData(
            orderCode=order_code,
            amount=amount,
            description=description,
            cancelUrl=cancel_url,
            returnUrl=return_url,
            items=[
                ItemData(name=i["name"], quantity=i["quantity"], price=i["price"])
                for i in items
            ],
            buyerName=buyer_name,
            buyerEmail=buyer_email,
            buyerPhone=buyer_phone,
            expiredAt=expired_at,
        )
        result = _client().createPaymentLink(payment)

    return PaymentLink(
        checkout_url=result.checkoutUrl,
        payment_link_id=result.paymentLinkId,
        qr_code=result.qrCode,
        order_code=result.orderCode,
    )


def verify_webhook(body: Any):
    """Verify a webhook payload's signature and return its parsed data.

    Raises if the signature doesn't match the checksum key (i.e. the request
    isn't genuinely from payOS). The returned object exposes ``orderCode`` and
    ``code`` ('00' == success), among other transaction fields.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return _client().verifyPaymentWebhookData(body)


def cancel_payment_link(order_code: int, reason: str = "Đơn hàng đã hủy"):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return _client().cancelPaymentLink(order_code, reason)
