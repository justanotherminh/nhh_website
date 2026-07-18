"""Pricing: the early-bird discount, in one place.

The promo is stored in the ``app_settings`` table so managers can set the window
and percent from the admin UI at runtime (no redeploy). Discounting is kept
separate from seat *availability* (blocked/VIP seats live in ``seats.status``) and
is applied only to genuine public sales — comp/invitation orders never pass through
here, so they can't be discounted.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AppSetting

_HANOI = ZoneInfo("Asia/Ho_Chi_Minh")

_K_ENABLED = "earlybird_enabled"
_K_PERCENT = "earlybird_percent"
_K_START = "earlybird_start"
_K_END = "earlybird_end"


def _get(db: Session, key: str) -> str | None:
    return db.execute(
        select(AppSetting.value).where(AppSetting.key == key)
    ).scalar_one_or_none()


def _set(db: Session, key: str, value: str) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value


def _parse_local(s: str | None) -> dt.datetime | None:
    """Parse a stored 'YYYY-MM-DDTHH:MM' (Hanoi wall-clock) into an aware datetime."""
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return d.replace(tzinfo=_HANOI) if d.tzinfo is None else d


def get_earlybird(db: Session) -> dict:
    """Current promo config: enabled flag, percent, and start/end (aware datetimes).

    ``start_raw`` / ``end_raw`` are the stored 'YYYY-MM-DDTHH:MM' strings, for
    pre-filling the admin form's datetime-local inputs.
    """
    start_raw = _get(db, _K_START) or ""
    end_raw = _get(db, _K_END) or ""
    return {
        "enabled": _get(db, _K_ENABLED) == "1",
        "percent": int(_get(db, _K_PERCENT) or 0),
        "start": _parse_local(start_raw),
        "end": _parse_local(end_raw),
        "start_raw": start_raw,
        "end_raw": end_raw,
    }


def set_earlybird(
    db: Session, *, enabled: bool, percent: int, start: str, end: str
) -> None:
    """Persist the promo config. ``start``/``end`` are datetime-local form strings."""
    _set(db, _K_ENABLED, "1" if enabled else "0")
    _set(db, _K_PERCENT, str(int(percent)))
    _set(db, _K_START, start or "")
    _set(db, _K_END, end or "")
    db.commit()


def active_discount_percent(db: Session, now: dt.datetime | None = None) -> int:
    """The discount percent in effect right now (0 unless enabled and within window)."""
    cfg = get_earlybird(db)
    if not cfg["enabled"] or cfg["percent"] <= 0 or not cfg["start"] or not cfg["end"]:
        return 0
    now = now or dt.datetime.now(dt.timezone.utc)
    return cfg["percent"] if cfg["start"] <= now < cfg["end"] else 0


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
