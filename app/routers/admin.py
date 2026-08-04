"""Admin dashboard: HTTP Basic Auth, seat occupancy + orders + manual actions.

Single shared credential (ADMIN_USERNAME / ADMIN_PASSWORD), checked in constant
time. Everything under /admin requires it. This is internet-facing, so behind
Caddy's HTTPS the Basic Auth password is the gate — keep it strong.
"""
from __future__ import annotations

import datetime as dt
import logging
import secrets
from zoneinfo import ZoneInfo

_HANOI = ZoneInfo("Asia/Ho_Chi_Minh")

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.db import get_db
from app.models import Announcement, Order, OrderItem, PriceTier, Seat, Ticket, VipTicket
from app.routers.seatmap import build_seatmap
from app.services import announcements as announce_svc
from app.services import images as images_svc
from app.services import orders as orders_svc
from app.services import pricing
from app.services import refunds as refunds_svc
from app.services import vip as vip_svc
from app.templates import templates
from scripts.import_vip_seats import reserved_seat_ids

log = logging.getLogger("admin")

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

ORDERS_PER_PAGE = 25


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    page: int = 1,
    cancelled: int = 0,
    db: Session = Depends(get_db),
) -> HTMLResponse:
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

    # Orders grouped by status (count + summed amount). Throwaway orders from
    # scripts/send_test_ticket are excluded so they can't inflate revenue.
    order_stats = {
        s: {"count": c, "sum": total}
        for s, c, total in db.execute(
            select(Order.status, func.count(), func.coalesce(func.sum(Order.amount_vnd), 0))
            .where(Order.kind != "test")
            .group_by(Order.status)
        ).all()
    }
    # Revenue nets off refunds that are still sitting inside the paid total (see
    # refunds.refunded_on_live_orders — fully refunded orders have already left it).
    gross_paid = order_stats.get("paid", {}).get("sum", 0)
    refunded_live = refunds_svc.refunded_on_live_orders(db)
    revenue_paid = gross_paid - refunded_live
    refunded_total = refunds_svc.refunded_total(db)

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

    # Orders listing — paginated so the whole table is never loaded at once.
    # Cancelled orders are hidden unless explicitly requested; the same filter is
    # applied to the COUNT and the page query so paging stays consistent.
    show_cancelled = bool(cancelled)
    list_conds = [] if show_cancelled else [Order.status != "cancelled"]

    total_orders = db.execute(
        select(func.count()).select_from(Order).where(*list_conds)
    ).scalar_one()
    total_pages = max(1, (total_orders + ORDERS_PER_PAGE - 1) // ORDERS_PER_PAGE)
    page = min(max(1, page), total_pages)          # clamp to a real page
    offset = (page - 1) * ORDERS_PER_PAGE

    orders = db.execute(
        select(Order)
        .options(selectinload(Order.items))
        .where(*list_conds)
        .order_by(Order.created_at.desc())
        .limit(ORDERS_PER_PAGE)
        .offset(offset)
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
            "refunded_total": refunded_total,
            "blocked_pool": blocked_pool,
            "comps_issued": comps_issued,
            "order_stats": order_stats,
            "tiers": tiers,
            "orders": orders,
            "page": page,
            "total_pages": total_pages,
            "total_orders": total_orders,
            "show_cancelled": show_cancelled,
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


# ---------------------------------------------------------------- refunds
# payOS cannot refund, so the money is transferred back by hand and this screen
# only reconciles the database afterwards. It is per-seat: an order can hold up
# to eight seats and a buyer may hand back only some of them.
@router.get("/orders/{order_code}", response_class=HTMLResponse)
def order_detail(
    order_code: int, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    order = refunds_svc.refundable_order(db, order_code)
    if order is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng.")

    done = refunds_svc.refunded_seat_ids(db, order.id)
    ticket_by_seat = {t.seat_id: t for t in order.tickets}
    seats = []
    for it in sorted(order.items, key=lambda i: i.seat.label):
        t = ticket_by_seat.get(it.seat_id)
        seats.append({
            "seat_id": it.seat_id,
            "label": it.seat.label,
            "price": it.price_vnd,
            "status": it.seat.status,
            "refunded": it.seat_id in done,
            "checked_in": t.checked_in_at is not None if t else False,
            "checked_in_at": (
                t.checked_in_at.astimezone(_HANOI).strftime("%H:%M %d/%m/%Y")
                if t and t.checked_in_at else ""
            ),
        })
    return templates.TemplateResponse(
        request,
        "admin_order.html",
        {
            "app_name": settings.app_name,
            "order": order,
            "seats": seats,
            # Formatted here, in Hanoi time, like every other admin timestamp —
            # created_at is stored UTC and would otherwise print as UTC.
            "refunds": [
                {
                    "when": r.created_at.astimezone(_HANOI).strftime("%H:%M %d/%m/%Y"),
                    "seat": r.seat.label,
                    "amount": r.amount_vnd,
                    "by": r.refunded_by,
                    "note": r.note,
                }
                for r in refunds_svc.recent_for_order(db, order.id)
            ],
            "refunded_sum": sum(s["price"] for s in seats if s["refunded"]),
            "live_sum": sum(s["price"] for s in seats if not s["refunded"]),
            "notice": request.query_params.get("notice"),
            "error": request.query_params.get("error"),
        },
    )


@router.get("/refunds", response_class=HTMLResponse)
def refunds_ledger(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Every refund, in one table — what gets reconciled against the bank.

    The per-order page shows only that order's history; this is the whole book.
    """
    rows = refunds_svc.recent(db)
    total_rows = refunds_svc.count(db)
    return templates.TemplateResponse(
        request,
        "admin_refunds.html",
        {
            "app_name": settings.app_name,
            "refunds": [
                {
                    "when": r.created_at.astimezone(_HANOI).strftime("%H:%M %d/%m/%Y"),
                    "order_code": r.order.order_code,
                    "buyer": r.order.buyer_name,
                    "seat": r.seat.label,
                    "amount": r.amount_vnd,
                    "by": r.refunded_by,
                    "note": r.note,
                }
                for r in rows
            ],
            "total_amount": refunds_svc.refunded_total(db),
            "total_rows": total_rows,
            # True when the cap hid some rows, so the page can say so out loud.
            "truncated": total_rows > len(rows),
        },
    )


@router.post("/orders/{order_code}/refund")
def refund_seat(
    order_code: int,
    seat_id: int = Form(...),
    note: str = Form(""),
    confirm_transferred: str = Form(""),
    operator: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Refund one seat. The money is NOT moved here — see services/refunds.py."""
    from urllib.parse import quote

    # The manager must tick "I have already transferred the money". Enforced here
    # and not only in the form: an HTML `required` attribute is trivially skipped,
    # and this is the one control standing between a voided ticket and a buyer who
    # was never actually paid back.
    if not confirm_transferred:
        return RedirectResponse(
            f"/admin/orders/{order_code}?error="
            + quote("Cần tích xác nhận đã chuyển khoản trả khách trước khi hoàn ghế."),
            status_code=303,
        )

    try:
        refund = refunds_svc.refund_seat(
            db,
            order_code=order_code,
            seat_id=seat_id,
            operator=operator,
            note=note,
        )
    except refunds_svc.RefundError as exc:
        return RedirectResponse(
            f"/admin/orders/{order_code}?error={quote(str(exc))}", status_code=303
        )
    amount = f"{refund.amount_vnd:,}".replace(",", ".")
    return RedirectResponse(
        f"/admin/orders/{order_code}?notice="
        + quote(f"Đã hoàn {amount} đ — ghế được mở bán lại."),
        status_code=303,
    )


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
    """Seat-map JSON annotated for the admin: which seats are VIP, and each VIP
    seat's export state — "none" (unexported), "exported", or "sent"."""
    data = build_seatmap(db)
    vip_ids = reserved_seat_ids(db)
    states = vip_svc.states_by_seat(db)
    for s in data["seats"]:
        is_vip = s["id"] in vip_ids
        state = states.get(s["id"], "none") if is_vip else "none"
        s["vip"] = is_vip
        s["vip_state"] = state
        s["exported"] = state != "none"   # kept for any older consumers
    return data


class _ExportItem(BaseModel):
    seat_id: int
    name: str = ""


class _ExportBody(BaseModel):
    tickets: list[_ExportItem]


@router.post("/invitations/export")
def invitations_export(body: _ExportBody, db: Session = Depends(get_db)) -> dict:
    """Generate + store a PDF ticket for each (VIP seat, recipient name).

    Only VIP-reserved seats are ever touched; a seat that's already been exported
    is skipped rather than duplicated. Returns per-request counts so the map can
    report what happened.
    """
    vip_ids = reserved_seat_ids(db)
    created, skipped, errors = 0, 0, []
    for item in body.tickets:
        if item.seat_id not in vip_ids:
            errors.append(f"{item.seat_id}: không phải ghế vé mời")
            continue
        try:
            vip_svc.export_seat(db, item.seat_id, item.name)
            created += 1
        except vip_svc.AlreadyExported:
            skipped += 1
        except Exception:                       # noqa: BLE001 - one bad seat won't stop the rest
            log.exception("VIP export failed for seat %s", item.seat_id)
            errors.append(f"{item.seat_id}: lỗi tạo vé")
    return {"ok": True, "created": created, "skipped": skipped, "errors": errors}


@router.get("/invitations/tickets", response_class=HTMLResponse)
def invitations_tickets(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """The depot: every generated VIP ticket, with download + mark-sent controls."""
    rows = vip_svc.list_tickets(db)
    tickets = [
        {
            "id": v.id,
            "seat": v.seat.label,
            "recipient": v.recipient_name,
            "created": v.created_at.astimezone(_HANOI).strftime("%d/%m/%Y %H:%M"),
            "sent": v.sent_at is not None,
            "sent_at": v.sent_at.astimezone(_HANOI).strftime("%d/%m/%Y %H:%M") if v.sent_at else "",
        }
        for v in rows
    ]
    sent_count = sum(1 for t in tickets if t["sent"])
    return templates.TemplateResponse(
        request,
        "admin_vip_tickets.html",
        {
            "app_name": settings.app_name,
            "tickets": tickets,
            "sent_count": sent_count,
            "notice": request.query_params.get("notice"),
        },
    )


@router.get("/invitations/tickets/{vip_id}/pdf")
def invitations_ticket_pdf(vip_id: int, db: Session = Depends(get_db)) -> FileResponse:
    """Stream a stored VIP ticket PDF. Admin-only (the whole router is gated), so
    the depot stays off the public web even though the QR inside is a live pass."""
    vip = db.get(VipTicket, vip_id)
    if vip is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy vé.")
    path = vip_svc.depot_file(vip.pdf_filename)
    if path is None:
        raise HTTPException(status_code=404, detail="Tệp PDF không tồn tại.")
    return FileResponse(path, media_type="application/pdf", filename=vip_svc.download_name(vip))


@router.post("/invitations/tickets/{vip_id}/sent")
def invitations_ticket_sent(
    vip_id: int, undo: str = Form(""), db: Session = Depends(get_db)
) -> RedirectResponse:
    """Mark a VIP ticket printed-and-sent (or clear it again with ?undo)."""
    vip_svc.mark_sent(db, vip_id, sent=not bool(undo))
    return RedirectResponse("/admin/invitations/tickets", status_code=303)


# -------------------------------------------------------------- announcements
# Bulk email to ticket holders. The compose form only queues the send; the actual
# delivery is drained by the background job in app/main.py, a batch at a time.
@router.get("/announcements", response_class=HTMLResponse)
def announcements_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    past = db.execute(
        select(Announcement).order_by(Announcement.id.desc()).limit(20)
    ).scalars().all()
    history = [
        {
            "id": a.id,
            "subject": a.subject,
            "status": a.status,
            "created": a.created_at.astimezone(_HANOI).strftime("%d/%m/%Y %H:%M"),
            **announce_svc.progress(db, a.id),
        }
        for a in past
    ]
    return templates.TemplateResponse(
        request,
        "admin_announcements.html",
        {
            "app_name": settings.app_name,
            "recipient_count": announce_svc.audience_count(db),
            "history": history,
            "notice": request.query_params.get("notice"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/announcements/preview")
def announcements_preview(
    subject: str = Form(""),
    body: str = Form(""),
    to: str = Form(""),
) -> RedirectResponse:
    """Send one copy to a single address. No announcement record is created."""
    if not subject.strip() or not body.strip():
        return RedirectResponse(
            "/admin/announcements?error=Cần+cả+tiêu+đề+và+nội+dung.", status_code=303
        )
    if "@" not in to:
        return RedirectResponse(
            "/admin/announcements?error=Địa+chỉ+gửi+thử+không+hợp+lệ.", status_code=303
        )
    try:
        announce_svc.send_preview(subject, body, to.strip())
    except Exception as exc:                      # noqa: BLE001 - shown to the admin
        return RedirectResponse(
            f"/admin/announcements?error=Gửi+thử+thất+bại:+{str(exc)[:120]}",
            status_code=303,
        )
    return RedirectResponse(
        f"/admin/announcements?notice=Đã+gửi+thử+tới+{to.strip()}.", status_code=303
    )


@router.post("/announcements/send")
def announcements_send(
    subject: str = Form(""),
    body: str = Form(""),
    confirm: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Queue an announcement to every paid buyer.

    Requires the typed recipient count as confirmation: it forces whoever clicks
    to have looked at how many people this reaches.
    """
    if not subject.strip() or not body.strip():
        return RedirectResponse(
            "/admin/announcements?error=Cần+cả+tiêu+đề+và+nội+dung.", status_code=303
        )
    total = announce_svc.audience_count(db)
    if not total:
        return RedirectResponse(
            "/admin/announcements?error=Chưa+có+người+nhận+nào.", status_code=303
        )
    if confirm.strip() != str(total):
        return RedirectResponse(
            f"/admin/announcements?error=Nhập+đúng+số+{total}+để+xác+nhận+gửi.",
            status_code=303,
        )
    if db.execute(
        select(func.count()).select_from(Announcement)
        .where(Announcement.status == "sending")
    ).scalar_one():
        return RedirectResponse(
            "/admin/announcements?error=Đang+có+một+thông+báo+được+gửi.+Vui+lòng+đợi.",
            status_code=303,
        )
    ann = announce_svc.queue(db, subject, body)
    return RedirectResponse(
        f"/admin/announcements?notice=Đã+xếp+hàng+gửi+tới+{total}+người+nhận"
        f"+(thông+báo+%23{ann.id}).",
        status_code=303,
    )


@router.post("/announcements/{announcement_id}/pause")
def announcements_pause(
    announcement_id: int, db: Session = Depends(get_db)
) -> RedirectResponse:
    """Stop an in-flight blast. Already-sent copies cannot be recalled, but the
    remaining recipients stay pending and can be resumed."""
    announce_svc.set_status(db, announcement_id, "paused")
    return RedirectResponse(
        "/admin/announcements?notice=Đã+tạm+dừng.", status_code=303
    )


@router.post("/announcements/{announcement_id}/resume")
def announcements_resume(
    announcement_id: int, db: Session = Depends(get_db)
) -> RedirectResponse:
    announce_svc.set_status(db, announcement_id, "sending")
    return RedirectResponse(
        "/admin/announcements?notice=Đã+tiếp+tục+gửi.", status_code=303
    )
