"""Admin dashboard: HTTP Basic Auth, seat occupancy + orders + manual actions.

Single shared credential (ADMIN_USERNAME / ADMIN_PASSWORD), checked in constant
time. Everything under /admin requires it. This is internet-facing, so behind
Caddy's HTTPS the Basic Auth password is the gate — keep it strong.
"""
from __future__ import annotations

import base64
import datetime as dt
import secrets
from zoneinfo import ZoneInfo

_HANOI = ZoneInfo("Asia/Ho_Chi_Minh")

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.db import get_db
from app.models import Order, OrderItem, PriceTier, Seat, Ticket
from app.services import orders as orders_svc
from app.services import pricing
from app.services import tickets as tickets_svc
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
    blocked_pool = seat_counts.get("blocked", 0)
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

    # Invitations issued = seats booked via a comp order.
    comps_issued = db.execute(
        select(func.count())
        .select_from(OrderItem)
        .join(Order, OrderItem.order_id == Order.id)
        .where(Order.kind == "comp")
    ).scalar() or 0

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
        {"name": t.name, "rank": len(tier_rows) - 1 - i, "price": t.price_vnd,
         "total": total, "available": avail}
        for i, (t, total, avail) in enumerate(tier_rows)
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
            "blocked_pool": blocked_pool,
            "comps_issued": comps_issued,
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


# ---------------------------------------------------------------- early-bird
def _earlybird_status(cfg: dict) -> str:
    if not cfg["enabled"] or cfg["percent"] <= 0 or not cfg["start"] or not cfg["end"]:
        return "off"
    now = dt.datetime.now(dt.timezone.utc)
    if cfg["start"] >= cfg["end"]:
        return "invalid"
    if now < cfg["start"]:
        return "scheduled"
    if now >= cfg["end"]:
        return "ended"
    return "active"


@router.get("/early-bird", response_class=HTMLResponse)
def early_bird_form(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    cfg = pricing.get_earlybird(db)
    fmt = lambda d: d.astimezone(_HANOI).strftime("%H:%M %d/%m/%Y") if d else "—"
    return templates.TemplateResponse(
        request,
        "admin_early_bird.html",
        {
            "app_name": settings.app_name,
            "cfg": cfg,
            "status": _earlybird_status(cfg),
            "window": f"{fmt(cfg['start'])} → {fmt(cfg['end'])}",
            "notice": request.query_params.get("notice"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/early-bird")
def early_bird_save(
    enabled: str = Form(default=""),
    percent: int = Form(default=10),
    start: str = Form(default=""),
    end: str = Form(default=""),
    db: Session = Depends(get_db),
):
    on = bool(enabled)
    start, end = start.strip(), end.strip()
    if on and (not start or not end):
        return RedirectResponse(
            "/admin/early-bird?error=Cần+nhập+thời+gian+bắt+đầu+và+kết+thúc.",
            status_code=303,
        )
    if on and start >= end:
        return RedirectResponse(
            "/admin/early-bird?error=Thời+gian+kết+thúc+phải+sau+thời+gian+bắt+đầu.",
            status_code=303,
        )
    percent = max(0, min(100, percent))
    pricing.set_earlybird(db, enabled=on, percent=percent, start=start, end=end)
    msg = f"Đã+lưu+ưu+đãi+{percent}%25." if on else "Đã+tắt+ưu+đãi+mở+bán+sớm."
    return RedirectResponse(f"/admin/early-bird?notice={msg}", status_code=303)


# ---------------------------------------------------------------- invitations
@router.get("/invitations", response_class=HTMLResponse)
def invitations(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Invitation pool + issuance: reserved (blocked) seats and comp orders."""
    pool = db.execute(
        select(Seat)
        .options(selectinload(Seat.tier))
        .where(Seat.status == "blocked")
        .order_by(Seat.section, Seat.row_label, Seat.seat_number)
    ).scalars().all()
    comps = db.execute(
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.kind == "comp")
        .order_by(Order.created_at.desc())
        .limit(50)
    ).scalars().all()
    # price -> rank (0 = cheapest) so the pool swatches match the seat-map palette.
    prices = db.execute(select(PriceTier.price_vnd).order_by(PriceTier.price_vnd)).scalars().all()
    rank_map = {p: i for i, p in enumerate(prices)}
    return templates.TemplateResponse(
        request,
        "admin_invitations.html",
        {
            "app_name": settings.app_name,
            "pool": pool,
            "comps": comps,
            "rank_map": rank_map,
            "notice": request.query_params.get("notice"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/invitations")
def issue_invitation(
    guest_name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(""),
    seat_ids: list[int] = Form(default=[]),
    db: Session = Depends(get_db),
):
    """Issue a free e-ticket to a named guest for the selected pool seats."""
    if not seat_ids:
        return RedirectResponse("/admin/invitations?error=Chưa+chọn+ghế+nào.", status_code=303)
    try:
        order = orders_svc.create_comp_order(
            db,
            seat_ids=seat_ids,
            guest_name=guest_name.strip(),
            email=email.strip(),
            phone=phone.strip(),
        )
    except orders_svc.SeatsNotBookable:
        return RedirectResponse(
            "/admin/invitations?error=Một+số+ghế+không+còn+trống.", status_code=303
        )
    return RedirectResponse(
        f"/admin/invitations?notice=Đã+phát+{len(order.items)}+vé+mời+cho+{email.strip()}.",
        status_code=303,
    )


@router.post("/invitations/block")
def block_pool(identifiers: str = Form(""), db: Session = Depends(get_db)):
    """Reserve seats into the invitation pool (available -> blocked).

    ``identifiers`` is a free-text list (newline/comma separated) of seat ids or
    exact seat labels. Only currently-available seats are moved; booked seats are
    left alone and unknown tokens are reported back.
    """
    tokens = [t.strip() for t in identifiers.replace(",", "\n").splitlines() if t.strip()]
    ids, missed = _resolve_seat_ids(db, tokens)
    blocked = 0
    if ids:
        res = db.execute(
            update(Seat)
            .where(Seat.id.in_(ids), Seat.status == "available")
            .values(status="blocked", held_by_cart=None, hold_expires_at=None)
        )
        db.commit()
        blocked = res.rowcount
    notice = f"Đã+giữ+{blocked}+ghế+cho+vé+mời."
    if missed:
        notice += f"+Không+tìm+thấy:+{len(missed)}."
    return RedirectResponse(f"/admin/invitations?notice={notice}", status_code=303)


@router.post("/invitations/unblock/{seat_id}")
def unblock_pool_seat(seat_id: int, db: Session = Depends(get_db)):
    """Return a reserved seat to public sale (blocked -> available)."""
    db.execute(
        update(Seat)
        .where(Seat.id == seat_id, Seat.status == "blocked")
        .values(status="available")
    )
    db.commit()
    return RedirectResponse("/admin/invitations", status_code=303)


@router.post("/invitations/generate")
def generate_printed_tickets(db: Session = Depends(get_db)):
    """Pre-mint QR tickets for all reserved (blocked) seats, for printout (no email)."""
    n = orders_svc.generate_reserved_tickets(db)
    return RedirectResponse(
        f"/admin/invitations?notice=Đã+tạo+{n}+vé+mời+in+sẵn.", status_code=303
    )


@router.get("/invitations/print", response_class=HTMLResponse)
def print_tickets(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Print-ready sheet of every invitation ticket: seat + a write-on name line + QR.

    QR images are embedded as data URIs so the page is self-contained and prints
    reliably (no 145 separate image requests)."""
    rows = db.execute(
        select(Ticket)
        .join(Order, Ticket.order_id == Order.id)
        .options(selectinload(Ticket.seat))
        .where(Order.kind == "comp")
        .order_by(Ticket.seat_id)
    ).scalars().all()
    cards = [
        {
            "seat": t.seat.label,
            "seat_short": f"{t.seat.row_label} · {t.seat.seat_number}",
            "code": t.ticket_code,
            "qr": "data:image/png;base64,"
            + base64.b64encode(tickets_svc.qr_png_bytes(t.qr_token)).decode(),
        }
        for t in rows
    ]
    return templates.TemplateResponse(
        request,
        "admin_print_tickets.html",
        {"app_name": settings.app_name, "cards": cards},
    )


def _resolve_seat_ids(db: Session, tokens: list[str]) -> tuple[set[int], list[str]]:
    """Map free-text tokens (seat id or exact label) to seat ids; report misses."""
    ids: set[int] = set()
    missed: list[str] = []
    for tok in tokens:
        seat = db.get(Seat, int(tok)) if tok.isdigit() else None
        if seat is None:
            seat = db.execute(
                select(Seat).where(Seat.label == tok)
            ).scalar_one_or_none()
        if seat is None:
            missed.append(tok)
        else:
            ids.add(seat.id)
    return ids, missed
