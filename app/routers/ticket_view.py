"""Tokenized e-ticket pages: the public view a buyer (or door scanner) reaches
via the QR code. The ``qr_token`` is unguessable, so no auth is needed."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import i18n
from app.config import settings
from app.db import get_db
from app.models import Seat, Ticket
from app.services import tickets as ticket_svc
from app.templates import templates

router = APIRouter(tags=["ticket"])


def _get_ticket(db: Session, qr_token: str) -> Ticket | None:
    return db.execute(
        select(Ticket)
        .options(
            selectinload(Ticket.seat).selectinload(Seat.tier),
            selectinload(Ticket.order),
        )
        .where(Ticket.qr_token == qr_token)
    ).scalar_one_or_none()


@router.get("/ve/{qr_token}", response_class=HTMLResponse)
def view_ticket(qr_token: str, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    ticket = _get_ticket(db, qr_token)
    if ticket is None:
        raise HTTPException(
            status_code=404,
            detail=i18n.t("err.ticket_not_found", getattr(request.state, "lang", i18n.DEFAULT_LANG)),
        )
    # Show the ticket in the language the buyer used, unless this visitor has
    # explicitly picked one via the toggle (a `lang` cookie is then present).
    if "lang" not in request.cookies:
        request.state.lang = i18n.normalize(ticket.order.lang)
    return templates.TemplateResponse(
        request,
        "ticket.html",
        {"app_name": settings.app_name, "ticket": ticket,
         "seat": ticket.seat, "order": ticket.order},
    )


@router.get("/ve/{qr_token}/qr.png")
def ticket_qr(qr_token: str, request: Request, db: Session = Depends(get_db)) -> Response:
    if _get_ticket(db, qr_token) is None:
        raise HTTPException(
            status_code=404,
            detail=i18n.t("err.ticket_not_found", getattr(request.state, "lang", i18n.DEFAULT_LANG)),
        )
    return Response(content=ticket_svc.qr_png_bytes(qr_token), media_type="image/png")
