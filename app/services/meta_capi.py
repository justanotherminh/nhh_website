"""Meta Conversions API: report a completed purchase server-side.

The browser Pixel reports purchases too, but misses a lot of them here. Our buyer
is redirected to payOS, pays on their phone, and very often closes the tab instead
of following the redirect back — so ``/checkout/success`` never renders and its
Pixel event never fires, even though the money definitely arrived. Ad blockers and
Safari's cookie policy remove more. The payOS webhook, by contrast, is our
authoritative "money arrived" moment, which makes it a far better place to report
a purchase from.

Both report the same sale, so both send the **same event_id** (the order code) and
Meta de-duplicates. Getting that wrong double-counts every conversion.

Two things this module will not do:

* **Never raise.** It is called from the payOS webhook, which is critical path: if
  a Meta timeout propagated, payOS would see a failed delivery and retry, and in
  the worst case an order wouldn't be marked paid. Trading working ticket sales
  for analytics is not a trade worth making, so every failure is logged and
  swallowed.
* **Never send personal data.** Only ``fbc``/``fbp`` — Meta's own click and browser
  ids, which identify a browser to Meta and nobody else. Hashed email/phone
  ("advanced matching") would improve match rates and is deliberately not here: it
  is a privacy decision for the organisers, not an implementation detail. See
  ADS.md Part 6.

Note that we cannot send the buyer's IP or user agent either, even though Meta
accepts them: this runs from a webhook whose request comes from payOS, so those
would describe payOS's server rather than the buyer, and would actively poison
match quality.
"""
from __future__ import annotations

import logging
import time

import httpx

from app.config import settings

log = logging.getLogger("meta.capi")

# Short: this runs off the response path, but a hung connection still ties up a
# worker thread, and a late conversion report is worth nothing anyway.
TIMEOUT_SECONDS = 5.0


def enabled() -> bool:
    """Both a pixel id and a CAPI token are needed to report anything."""
    return bool(settings.meta_pixel_id and settings.meta_capi_token)


def _endpoint() -> str:
    return (
        f"https://graph.facebook.com/{settings.meta_graph_version}"
        f"/{settings.meta_pixel_id}/events"
    )


def build_purchase(
    *,
    order_code: int,
    value_vnd: int,
    fbc: str | None = None,
    fbp: str | None = None,
    event_time: int | None = None,
    num_items: int = 0,
) -> dict:
    """The request body for one Purchase event. Pure, so it can be asserted on."""
    user_data = {}
    if fbc:
        user_data["fbc"] = fbc
    if fbp:
        user_data["fbp"] = fbp

    event = {
        "event_name": "Purchase",
        "event_time": event_time if event_time is not None else int(time.time()),
        # Must equal the Pixel's eventID on /checkout/success, or one sale counts
        # twice. The order code is unique and known to both sides.
        "event_id": str(order_code),
        "action_source": "website",
        "event_source_url": f"{settings.base_url}/checkout/success?order={order_code}",
        "user_data": user_data,
        "custom_data": {
            # VND is zero-decimal — 700000 means 700.000 đ, not 7.000 đ. Scaling
            # this by 100 would make every ROAS report wrong by two orders of
            # magnitude, in the flattering direction.
            "currency": "VND",
            "value": int(value_vnd),
        },
    }
    if num_items:
        event["custom_data"]["num_items"] = num_items

    body: dict = {"data": [event]}
    if settings.meta_test_event_code:
        body["test_event_code"] = settings.meta_test_event_code
    return body


def send_purchase(
    *,
    order_code: int,
    value_vnd: int,
    fbc: str | None = None,
    fbp: str | None = None,
    num_items: int = 0,
) -> bool:
    """Report a purchase to Meta. Returns whether it was accepted; never raises.

    Skips silently when tracking is unconfigured, and when the order carries
    neither ``fbc`` nor ``fbp`` — with no identifier Meta has nothing to match the
    event to and would reject it. That case is an ordinary organic purchase, which
    isn't attributable to an ad anyway.
    """
    if not enabled():
        return False
    if not fbc and not fbp:
        log.debug("Order %s has no fbc/fbp; skipping CAPI purchase", order_code)
        return False

    body = build_purchase(
        order_code=order_code, value_vnd=value_vnd,
        fbc=fbc, fbp=fbp, num_items=num_items,
    )
    try:
        resp = httpx.post(
            _endpoint(),
            params={"access_token": settings.meta_capi_token},
            json=body,
            timeout=TIMEOUT_SECONDS,
        )
        if resp.status_code >= 400:
            # Body, not just status: Meta explains *why* it rejected an event, and
            # without that a match-quality problem is invisible.
            log.warning(
                "CAPI purchase for order %s rejected (%s): %s",
                order_code, resp.status_code, resp.text[:500],
            )
            return False
        log.info("Reported purchase for order %s to Meta", order_code)
        return True
    except Exception:                # noqa: BLE001 - see module docstring
        log.exception("CAPI purchase for order %s failed", order_code)
        return False
