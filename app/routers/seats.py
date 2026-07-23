"""Seat hold / release / status endpoints (JSON, consumed by seatmap.js)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import i18n
from app.config import settings
from app.db import get_db
from app.services import cart as cartmod
from app.services import holds

router = APIRouter(prefix="/api", tags=["seats"])


class SeatRef(BaseModel):
    seat_id: int


@router.post("/hold")
def hold(
    body: SeatRef,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    cart_id = cartmod.get_or_create(request, response)

    # Enforce the per-order cap server-side. Re-holding a seat this cart already
    # has is a refresh, not a new seat, so it's always allowed.
    lang = getattr(request.state, "lang", i18n.DEFAULT_LANG)
    already = holds.own_held_seat_ids(db, cart_id)
    if body.seat_id not in already and len(already) >= settings.max_seats_per_order:
        raise HTTPException(
            status_code=409,
            detail=i18n.t("err.too_many_seats", lang, n=settings.max_seats_per_order),
        )

    if not holds.acquire(db, body.seat_id, cart_id, settings.hold_ttl_seconds):
        raise HTTPException(status_code=409, detail=i18n.t("err.seat_taken", lang))

    return {
        "ok": True,
        "count": holds.count_for_cart(db, cart_id),
        "ttl": settings.hold_ttl_seconds,
    }


@router.post("/release")
def release(
    body: SeatRef,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    cart_id = cartmod.read_cart_id(request)
    freed = bool(cart_id) and holds.release(db, body.seat_id, cart_id)
    count = holds.count_for_cart(db, cart_id) if cart_id else 0
    return {"ok": True, "freed": freed, "count": count}


@router.get("/seats/status")
def status(request: Request, db: Session = Depends(get_db)) -> dict:
    """Which seats are unavailable to this cart, and which it already holds.

    Polled by the client so seats grabbed by other buyers grey out live, and so a
    reload can restore this cart's own current selection.
    """
    cart_id = cartmod.read_cart_id(request)
    return {
        "taken": holds.taken_seat_ids(db, cart_id),
        "yours": holds.own_held_seat_ids(db, cart_id),
    }
