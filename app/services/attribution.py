"""Where a visitor came from: capture it on arrival, stamp it on the order.

Facebook appends ``fbclid`` to every outbound link click, and we tag the links we
post with ``utm_*``. Both arrive as query parameters on whatever page the visitor
lands on, and are gone the moment they navigate. So the landing request writes them
to a first-party cookie, and order creation copies that cookie onto the order.

This is deliberately our own record rather than Meta's. Meta's dashboard counts
view-through conversions and so credits itself generously; these columns are what
"should we keep paying for this?" should be answered with. The same capture also
produces the ``fbc`` value the Conversions API needs (see ``meta_capi``).

Nothing here is sent anywhere by itself: it's a first-party cookie and a few
columns in our own database.
"""
from __future__ import annotations

import base64
import time
from urllib.parse import parse_qsl, urlencode

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Order

COOKIE = "nhh_src"
# Long enough to cover a considered purchase (people click an ad, think, come back
# days later) without pretending to attribute a sale months after the fact.
MAX_AGE = 30 * 24 * 3600

# Column widths in models.Order; truncate rather than let a crafted URL error the
# insert. These are attacker-controllable query parameters.
_LIMITS = {"source": 60, "utm_campaign": 120, "utm_content": 120,
           "fbc": 255, "fbp": 120}


def _fbc(fbclid: str, now_ms: int | None = None) -> str:
    """Meta's click-id format: ``fb.<subdomain-index>.<click-time-ms>.<fbclid>``.

    The timestamp must be when the *click* happened, which is why this is built at
    capture time and stored — rebuilding it at order time would claim the click
    occurred at checkout and degrade match quality. Subdomain index 1 = the click
    landed on the registrable domain (we serve one host).
    """
    ms = now_ms if now_ms is not None else int(time.time() * 1000)
    return f"fb.1.{ms}.{fbclid}"


def from_query(params) -> dict[str, str]:
    """Attribution carried by a landing URL, or {} if it carries none.

    ``fbclid`` alone is enough — Facebook adds it to untagged links too, so a post
    nobody remembered to tag is still identifiable as Facebook traffic, just not as
    a specific campaign.
    """
    utm_source = (params.get("utm_source") or "").strip()
    fbclid = (params.get("fbclid") or "").strip()
    if not utm_source and not fbclid:
        return {}

    data = {
        "source": utm_source or "facebook",   # fbclid without utm_source == Facebook
        "utm_campaign": (params.get("utm_campaign") or "").strip(),
        "utm_content": (params.get("utm_content") or "").strip(),
        "fbc": _fbc(fbclid) if fbclid else "",
    }
    return {k: v[: _LIMITS[k]] for k, v in data.items() if v}


def encode(data: dict[str, str]) -> str:
    """Pack the fields into one cookie-safe value.

    base64url, not raw ``a=1&b=2``: Python's cookie writer doesn't consider ``&``
    or ``=`` legal in an unquoted value, so it wraps the whole thing in double
    quotes — which then arrive as part of the value and corrupt the first key and
    the last value. base64url's alphabet needs no quoting, and stripping the ``=``
    padding keeps it that way.
    """
    packed = urlencode(data).encode()
    return base64.urlsafe_b64encode(packed).decode().rstrip("=")


def decode(raw: str | None) -> dict[str, str]:
    """Unpack :func:`encode`, tolerating anything that isn't ours.

    The cookie is attacker-supplied like any other request header, so a malformed
    value must read as "no attribution" rather than raise.
    """
    if not raw:
        return {}
    try:
        padded = raw + "=" * (-len(raw) % 4)
        packed = base64.urlsafe_b64decode(padded.encode()).decode()
    except Exception:                # noqa: BLE001 - see docstring
        return {}
    return {
        k: v[: _LIMITS[k]]
        for k, v in parse_qsl(packed, keep_blank_values=False)
        if k in _LIMITS and v
    }


def stamp_response(request, response) -> None:
    """Persist any attribution on this request's URL to the first-party cookie.

    Last touch wins: a visitor who clicks a second ad should be credited to the
    second one. Ordinary navigation carries no parameters and so never overwrites
    an earlier click — only a fresh tagged arrival does.

    ``samesite=lax`` matters: the buyer returns from payOS via a top-level
    navigation, and ``strict`` would withhold the cookie on exactly that hop.
    """
    data = from_query(request.query_params)
    if not data:
        return
    response.set_cookie(
        COOKIE, encode(data), max_age=MAX_AGE,
        httponly=True, samesite="lax", path="/",
    )


def for_order(request) -> dict[str, str | None]:
    """Attribution to record on an order being created now.

    The Pixel's own ``_fbc``/``_fbp`` cookies win where present: the Pixel sets
    ``_fbc`` from the same click we did but is authoritative for Meta, and ``_fbp``
    only exists if the Pixel ran at all. Our cookie is the fallback for visitors
    whose Pixel was blocked — which is the case this whole module exists for.
    """
    stored = decode(request.cookies.get(COOKIE))
    fbc = request.cookies.get("_fbc") or stored.get("fbc")
    fbp = request.cookies.get("_fbp")
    out = {
        "source": stored.get("source"),
        "utm_campaign": stored.get("utm_campaign"),
        "utm_content": stored.get("utm_content"),
        "fbc": fbc,
        "fbp": fbp,
    }
    return {k: (v[: _LIMITS[k]] if v else None) for k, v in out.items()}


# ------------------------------------------------------------------ reporting

def revenue_by_source(db: Session) -> list[dict]:
    """Paid sales grouped by where the buyer came from, richest first.

    Gross of refunds deliberately: this answers "what did this campaign bring in",
    and a later refund isn't the campaign's doing. The dashboard's own revenue card
    remains the net figure.
    """
    rows = db.execute(
        select(
            Order.source,
            Order.utm_campaign,
            func.count(Order.id),
            func.coalesce(func.sum(Order.amount_vnd), 0),
        )
        .where(Order.kind == "sale", Order.status.in_(("paid", "refunded")))
        .group_by(Order.source, Order.utm_campaign)
        .order_by(func.coalesce(func.sum(Order.amount_vnd), 0).desc())
    ).all()
    return [
        {
            "source": source or "Trực tiếp / không rõ",
            "campaign": campaign or "—",
            "orders": count,
            "revenue": int(revenue or 0),
            "tagged": source is not None,
        }
        for source, campaign, count, revenue in rows
    ]
