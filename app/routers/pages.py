"""Public, server-rendered pages: front page and the ticket/seat page."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import PriceTier, Seat
from app.templates import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "index.html", {"app_name": settings.app_name}
    )


@router.get("/tickets", response_class=HTMLResponse)
def tickets(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    # Per-tier availability summary (real SVG seat map comes in a later step).
    total_subq = (
        select(Seat.tier_id, func.count().label("total"))
        .group_by(Seat.tier_id)
        .subquery()
    )
    avail_subq = (
        select(Seat.tier_id, func.count().label("available"))
        .where(Seat.status == "available")
        .group_by(Seat.tier_id)
        .subquery()
    )
    rows = db.execute(
        select(
            PriceTier,
            func.coalesce(total_subq.c.total, 0),
            func.coalesce(avail_subq.c.available, 0),
        )
        .outerjoin(total_subq, total_subq.c.tier_id == PriceTier.id)
        .outerjoin(avail_subq, avail_subq.c.tier_id == PriceTier.id)
        .order_by(PriceTier.price_vnd.desc())
    ).all()

    tiers = [
        {
            "name": t.name,
            "color_hex": t.color_hex,
            "price_vnd": t.price_vnd,
            "total": total,
            "available": available,
        }
        for (t, total, available) in rows
    ]
    return templates.TemplateResponse(
        request, "tickets.html", {"app_name": settings.app_name, "tiers": tiers}
    )
