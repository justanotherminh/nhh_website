"""Admin dashboard: HTTP Basic Auth, seat occupancy + orders + manual actions.

Single shared credential (ADMIN_USERNAME / ADMIN_PASSWORD), checked in constant
time. Everything under /admin requires it. This is internet-facing, so behind
Caddy's HTTPS the Basic Auth password is the gate — keep it strong.
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.db import get_db
from app.models import Order, PriceTier, Seat
from app.services import orders as orders_svc
from app.templates import templates

_basic = HTTPBasic()


def require_admin(creds: HTTPBasicCredentials = Depends(_basic)) -> str:
    user_ok = secrets.compare_digest(creds.username, settings.admin_username)
    pass_ok = secrets.compare_digest(creds.password, settings.admin_password)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sai thông tin đăng nhập",
            headers={"WWW-Authenticate": "Basic"},
        )
    return creds.username


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    # Seat occupancy.
    seat_counts = dict(
        db.execute(select(Seat.status, func.count()).group_by(Seat.status)).all()
    )
    booked = seat_counts.get("booked", 0)
    available_total = seat_counts.get("available", 0)
    held = db.execute(
        select(func.count()).select_from(Seat).where(
            Seat.status == "available",
            Seat.held_by_cart.is_not(None),
            Seat.hold_expires_at > func.now(),
        )
    ).scalar() or 0
    free_now = available_total - held

    # Orders grouped by status (count + summed amount).
    order_stats = {
        s: {"count": c, "sum": total}
        for s, c, total in db.execute(
            select(Order.status, func.count(), func.coalesce(func.sum(Order.amount_vnd), 0))
            .group_by(Order.status)
        ).all()
    }
    revenue_paid = order_stats.get("paid", {}).get("sum", 0)

    # Availability per tier.
    tier_rows = db.execute(
        select(
            PriceTier,
            func.count(Seat.id),
            func.count(Seat.id).filter(Seat.status == "available"),
        )
        .join(Seat, Seat.tier_id == PriceTier.id)
        .group_by(PriceTier.id)
        .order_by(PriceTier.price_vnd.desc())
    ).all()
    tiers = [
        {"name": t.name, "color": t.color_hex, "price": t.price_vnd, "total": total, "available": avail}
        for t, total, avail in tier_rows
    ]

    # Recent orders.
    recent = db.execute(
        select(Order).options(selectinload(Order.items)).order_by(Order.created_at.desc()).limit(50)
    ).scalars().all()

    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "app_name": settings.app_name,
            "booked": booked,
            "held": held,
            "free_now": free_now,
            "total_seats": sum(seat_counts.values()),
            "revenue_paid": revenue_paid,
            "order_stats": order_stats,
            "tiers": tiers,
            "orders": recent,
        },
    )


@router.post("/orders/{order_code}/cancel")
def cancel_order(order_code: int, db: Session = Depends(get_db)):
    """Manually cancel a pending order and release its held seats."""
    orders_svc.cancel_order(db, order_code, reason="Admin hủy")
    return RedirectResponse("/admin", status_code=303)


@router.post("/seats/{seat_id}/release")
def release_seat(seat_id: int, db: Session = Depends(get_db)):
    """Clear a lingering hold on a single seat (does not touch booked seats)."""
    db.execute(
        update(Seat)
        .where(Seat.id == seat_id, Seat.status == "available")
        .values(held_by_cart=None, hold_expires_at=None)
    )
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@router.post("/sweep")
def run_sweep(db: Session = Depends(get_db)):
    """Run the stale-order expiry sweep immediately (instead of waiting 60s)."""
    orders_svc.expire_stale_orders(db)
    return RedirectResponse("/admin", status_code=303)
