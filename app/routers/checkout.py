"""Checkout: buyer form -> order + payOS link -> success / cancel.

When payOS credentials aren't configured (local dev), we redirect to an in-app
'dev-pay' simulator instead of a real payment page, so the full pending->paid->
booked flow is testable without live credentials.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import i18n
from app.config import settings
from app.db import get_db
from app.models import Seat
from app.services import cart as cartmod
from app.services import holds, orders, pricing
from app.services import payos_client
from app.templates import templates

router = APIRouter(tags=["checkout"])


def _use_dev_payments() -> bool:
    """Simulate payment in-app when explicitly in dev mode or when payOS has no
    credentials configured."""
    return settings.payments_dev_mode or not payos_client.is_configured()


def _held_seats(db: Session, cart_id) -> list[Seat]:
    seat_ids = holds.own_held_seat_ids(db, cart_id)
    if not seat_ids:
        return []
    return list(
        db.execute(
            select(Seat)
            .options(selectinload(Seat.tier))
            .where(Seat.id.in_(seat_ids))
            .order_by(Seat.section, Seat.row_label, Seat.seat_number)
        ).scalars().all()
    )


@router.get("/checkout", response_class=HTMLResponse)
def checkout_form(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    cart_id = cartmod.read_cart_id(request)
    seats = _held_seats(db, cart_id) if cart_id else []
    if not seats:
        # Nothing held (never selected, or the hold lapsed) -> back to the map.
        return RedirectResponse("/tickets", status_code=303)
    subtotal = sum(s.tier.price_vnd for s in seats)
    percent = pricing.active_discount_percent(db)
    total = sum(pricing.discounted_price(s.tier.price_vnd, percent) for s in seats)
    return templates.TemplateResponse(
        request,
        "checkout.html",
        {
            "app_name": settings.app_name,
            "seats": seats,
            "subtotal": subtotal,
            "discount_percent": percent,
            "discount_amount": subtotal - total,
            "total": total,
            "hold_ttl": settings.hold_ttl_seconds,
        },
    )


@router.post("/checkout")
def checkout_submit(
    request: Request,
    buyer_name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(...),
    db: Session = Depends(get_db),
):
    cart_id = cartmod.read_cart_id(request)
    if cart_id is None:
        return RedirectResponse("/tickets", status_code=303)

    try:
        order = orders.create_order_from_holds(
            db,
            cart_id=cart_id,
            buyer_name=buyer_name.strip(),
            email=email.strip(),
            phone=phone.strip(),
            extend_seconds=settings.payment_window_seconds,
            lang=getattr(request.state, "lang", i18n.DEFAULT_LANG),
        )
    except orders.NoSeatsHeld:
        return RedirectResponse("/tickets", status_code=303)

    return_url = f"{settings.base_url}/checkout/success?order={order.order_code}"
    cancel_url = f"{settings.base_url}/checkout/cancel?order={order.order_code}"

    if _use_dev_payments():
        # No real gateway (or dev mode): simulate payment in-app.
        return RedirectResponse(
            f"/checkout/dev-pay?order={order.order_code}", status_code=303
        )

    items = [
        {"name": it.seat.label[:25], "quantity": 1, "price": it.price_vnd}
        for it in order.items
    ]
    link = payos_client.create_payment_link(
        order_code=order.order_code,
        amount=order.amount_vnd,
        description=f"NHH {str(order.order_code)[-6:]}",
        items=items,
        return_url=return_url,
        cancel_url=cancel_url,
        buyer_name=order.buyer_name,
        buyer_email=order.email,
        buyer_phone=order.phone,
    )
    order.payos_payment_link_id = link.payment_link_id
    db.commit()
    return RedirectResponse(link.checkout_url, status_code=303)


@router.get("/checkout/success", response_class=HTMLResponse)
def checkout_success(request: Request, order: int, db: Session = Depends(get_db)) -> HTMLResponse:
    o = orders.get_order(db, order)
    if o is None:
        raise HTTPException(status_code=404, detail=i18n.t("err.order_not_found", getattr(request.state, "lang", i18n.DEFAULT_LANG)))
    return templates.TemplateResponse(
        request,
        "checkout_success.html",
        {"app_name": settings.app_name, "order": o},
    )


@router.get("/checkout/cancel", response_class=HTMLResponse)
def checkout_cancel(request: Request, order: int, db: Session = Depends(get_db)) -> HTMLResponse:
    orders.cancel_order(db, order, reason="Người mua hủy")
    o = orders.get_order(db, order)
    return templates.TemplateResponse(
        request,
        "checkout_cancel.html",
        {"app_name": settings.app_name, "order": o},
    )


@router.get("/api/order/{order_code}/status")
def order_status(order_code: int, db: Session = Depends(get_db)) -> dict:
    o = orders.get_order(db, order_code)
    if o is None:
        raise HTTPException(status_code=404, detail="not found")
    return {"order_code": order_code, "status": o.status}


# ----- local dev-only payment simulator (inactive once payOS is configured) -----

def _dev_only():
    if not _use_dev_payments():
        raise HTTPException(status_code=404, detail="not found")


@router.get("/checkout/dev-pay", response_class=HTMLResponse)
def dev_pay(request: Request, order: int, db: Session = Depends(get_db)) -> HTMLResponse:
    _dev_only()
    o = orders.get_order(db, order)
    if o is None:
        raise HTTPException(status_code=404, detail=i18n.t("err.order_not_found", getattr(request.state, "lang", i18n.DEFAULT_LANG)))
    return templates.TemplateResponse(
        request,
        "checkout_devpay.html",
        {"app_name": settings.app_name, "order": o},
    )


@router.post("/checkout/dev-pay/confirm")
def dev_pay_confirm(order: int = Form(...), db: Session = Depends(get_db)):
    _dev_only()
    orders.mark_order_paid(db, order)  # same code path the real webhook triggers
    return RedirectResponse(f"/checkout/success?order={order}", status_code=303)


@router.post("/checkout/dev-pay/cancel")
def dev_pay_cancel(order: int = Form(...), db: Session = Depends(get_db)):
    _dev_only()
    return RedirectResponse(f"/checkout/cancel?order={order}", status_code=303)
