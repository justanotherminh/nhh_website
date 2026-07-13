"""Door check-in: staff scan a ticket QR to verify and redeem it.

The QR encodes ``/checkin/{qr_token}``. Scanning (a GET) atomically marks the
ticket used and shows a big VALID/USED/INVALID result. Gated by a dedicated door
credential (CHECKIN_USERNAME/PASSWORD) so entrance volunteers can redeem tickets
without any access to the admin dashboard or buyer data.
"""
from __future__ import annotations

import secrets
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.db import get_db
from app.models import Seat, Ticket
from app.templates import templates

_basic = HTTPBasic()


def require_checkin(creds: HTTPBasicCredentials = Depends(_basic)) -> str:
    user_ok = secrets.compare_digest(creds.username, settings.checkin_username)
    pass_ok = secrets.compare_digest(creds.password, settings.checkin_password)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sai thông tin đăng nhập",
            headers={"WWW-Authenticate": "Basic"},
        )
    return creds.username


router = APIRouter(prefix="/checkin", tags=["checkin"], dependencies=[Depends(require_checkin)])


def _load(db: Session, qr_token: str) -> Ticket | None:
    return db.execute(
        select(Ticket)
        .options(selectinload(Ticket.seat).selectinload(Seat.tier), selectinload(Ticket.order))
        .where(Ticket.qr_token == qr_token)
    ).scalar_one_or_none()


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def checkin_home(request: Request) -> HTMLResponse:
    """Landing page volunteers open BEFORE doors, to authenticate once and confirm
    they're ready — so the first real scan isn't a password prompt with a queue."""
    return templates.TemplateResponse(
        request,
        "checkin.html",
        {"app_name": settings.app_name, "result": "ready",
         "ticket": None, "seat": None, "order": None, "checked_at": None},
    )


@router.get("/{qr_token}", response_class=HTMLResponse)
def check_in(qr_token: str, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    # Atomically claim the check-in: only the FIRST scan flips NULL -> now(), so two
    # volunteers scanning at once can't both admit the same ticket.
    first = db.execute(
        update(Ticket)
        .where(Ticket.qr_token == qr_token, Ticket.checked_in_at.is_(None))
        .values(checked_in_at=func.now())
        .returning(Ticket.id)
    ).first()
    db.commit()

    ticket = _load(db, qr_token)
    if ticket is None:
        result = "invalid"          # unknown / fake token
    elif first is not None:
        result = "valid"            # this scan just admitted them
    else:
        result = "used"             # already checked in earlier

    checked_at = None
    if ticket and ticket.checked_in_at:
        checked_at = ticket.checked_in_at.astimezone(
            ZoneInfo("Asia/Ho_Chi_Minh")
        ).strftime("%H:%M — %d/%m/%Y")

    return templates.TemplateResponse(
        request,
        "checkin.html",
        {
            "app_name": settings.app_name,
            "result": result,
            "ticket": ticket,
            "seat": ticket.seat if ticket else None,
            "order": ticket.order if ticket else None,
            "checked_at": checked_at,
        },
        status_code=200 if result == "valid" else (404 if result == "invalid" else 409),
    )
