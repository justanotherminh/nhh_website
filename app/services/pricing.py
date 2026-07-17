"""Pricing: the early-bird discount, in one place.

Discounting is deliberately kept separate from seat *availability* (blocked / VIP
seats live in ``seats.status``). This module only answers "given a list price, what
does a buyer pay right now?" — and it is applied solely to genuine public sales.
Free invitations (comp orders) never come through here, so they can't be discounted.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from app.config import settings

_HANOI = ZoneInfo("Asia/Ho_Chi_Minh")


def active_discount_percent(now: dt.datetime | None = None) -> int:
    """The discount percent in effect right now (0 when disabled or expired)."""
    pct = settings.earlybird_percent or 0
    if pct <= 0:
        return 0
    until = settings.earlybird_until
    if until is None:
        return 0  # a percent with no deadline is treated as off, to avoid "forever" sales
    if until.tzinfo is None:               # a naive deadline is Hanoi local time
        until = until.replace(tzinfo=_HANOI)
    now = now or dt.datetime.now(dt.timezone.utc)
    return pct if now < until else 0


def discounted_price(list_price: int, percent: int) -> int:
    """Apply ``percent`` off a list price, rounded to the nearest 1,000 VND.

    Rounding to 1,000 keeps amounts tidy (Vietnamese transfers are in thousands);
    callers sum the per-seat results so the order total always reconciles exactly
    with its line items (important for the payOS charge).
    """
    if percent <= 0:
        return list_price
    net = list_price * (100 - percent) / 100
    return int(round(net / 1000.0)) * 1000
