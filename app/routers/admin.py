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

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.db import get_db
from app.models import Order, OrderItem, PriceTier, Seat, Ticket
from app.routers.seatmap import build_seatmap
from app.services import images as images_svc
from app.services import orders as orders_svc
from app.services import pricing
from app.services import tickets as tickets_svc
from app.templates import templates
from scripts.import_vip_seats import reserved_seat_ids

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


# ---------------------------------------------------------------- images
@router.get("/images", response_class=HTMLResponse)
def images_list(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "admin_images.html",
        {
            "app_name": settings.app_name,
            "images": images_svc.list_images(),
            "notice": request.query_params.get("notice"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/images")
async def images_upload(files: list[UploadFile] = File(default=[])):
    saved, errors = 0, []
    for f in files:
        if not f or not f.filename:
            continue
        try:
            images_svc.save_upload(f.filename, await f.read())
            saved += 1
        except images_svc.ImageError as exc:
            errors.append(f"{f.filename}: {exc}")
    if errors:
        from urllib.parse import quote
        return RedirectResponse(
            f"/admin/images?error={quote(' · '.join(errors[:3]))}", status_code=303
        )
    return RedirectResponse(f"/admin/images?notice=Đã+tải+lên+{saved}+ảnh.", status_code=303)


@router.post("/images/delete")
def images_delete(name: str = Form(...)):
    images_svc.delete_image(name)
    return RedirectResponse("/admin/images?notice=Đã+xoá+ảnh.", status_code=303)


@router.post("/images/reel")
def images_reel(name: str = Form(...), on: str = Form("")):
    """Show/hide an image on the homepage reel."""
    show = bool(on)
    images_svc.set_reel(name, show)
    msg = "Đã+thêm+vào+trang+chủ." if show else "Đã+bỏ+khỏi+trang+chủ."
    return RedirectResponse(f"/admin/images?notice={msg}", status_code=303)


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
# The invitation page is just the seat map: only the VIP-reserved seats are
# clickable, and clicking one exports its printable ticket. Which seats are VIP is
# defined in exactly one place — scripts/data/vip_reserved_seats.csv, applied on
# boot by scripts/import_vip_seats — so the admin never locks/unlocks seats here.
@router.get("/invitations", response_class=HTMLResponse)
def invitations(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "admin_invitations.html", {"app_name": settings.app_name}
    )


@router.get("/invitations/map")
def invitations_map(db: Session = Depends(get_db)) -> dict:
    """Seat-map JSON annotated for the admin: which seats are VIP, and which of
    those have already had their ticket exported."""
    data = build_seatmap(db)
    vip_ids = reserved_seat_ids(db)
    exported = set(
        db.execute(
            select(Ticket.seat_id).join(Order, Ticket.order_id == Order.id)
            .where(Order.kind == "comp")
        ).scalars().all()
    )
    for s in data["seats"]:
        s["vip"] = s["id"] in vip_ids
        s["exported"] = s["id"] in exported
    return data


@router.post("/invitations/print", response_class=HTMLResponse)
def print_tickets(
    request: Request,
    seat_ids: str = Form(""),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Print-ready sheet for the selected VIP seats. Mints any missing tickets, then
    renders each as a card (seat + write-on name line + QR embedded as a data URI)."""
    ids = [int(x) for x in seat_ids.split(",") if x.strip().isdigit()]
    vip_ids = reserved_seat_ids(db)
    ids = [i for i in ids if i in vip_ids]   # only ever print VIP seats
    orders_svc.ensure_reserved_tickets(db, ids)

    rows = db.execute(
        select(Ticket)
        .options(selectinload(Ticket.seat))
        .where(Ticket.seat_id.in_(ids))
        .order_by(Ticket.seat_id)
    ).scalars().all() if ids else []
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
