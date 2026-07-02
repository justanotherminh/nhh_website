"""Cart identity: a signed ``cart_id`` UUID stored in a cookie.

Holds and orders are tied to this id. The cookie value is signed (itsdangerous)
so a client can't forge another cart's id, but it isn't secret — it only names
which browser session a hold belongs to.
"""
from __future__ import annotations

import uuid

from fastapi import Request, Response
from itsdangerous import BadSignature, URLSafeSerializer

from app.config import settings

COOKIE_NAME = "cart"
# Live comfortably longer than a hold so the id survives the whole checkout.
COOKIE_MAX_AGE = 60 * 60 * 6  # 6 hours

_serializer = URLSafeSerializer(settings.secret_key, salt="cart")


def read_cart_id(request: Request) -> uuid.UUID | None:
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        return None
    try:
        return uuid.UUID(_serializer.loads(raw))
    except (BadSignature, ValueError, TypeError):
        return None


def issue_cart_id(response: Response, cart_id: uuid.UUID) -> None:
    response.set_cookie(
        COOKIE_NAME,
        _serializer.dumps(str(cart_id)),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
    )


def get_or_create(request: Request, response: Response) -> uuid.UUID:
    """Return the request's cart id, minting + setting a fresh one if absent."""
    cart_id = read_cart_id(request)
    if cart_id is None:
        cart_id = uuid.uuid4()
        issue_cart_id(response, cart_id)
    return cart_id
