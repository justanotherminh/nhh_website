"""Public, server-rendered pages: front page and the ticket/seat page."""
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import PriceTier, Seat
from app.templates import templates

router = APIRouter()

# Hero "featured moments" carousel: any image dropped in this folder shows up,
# sorted by filename. Empty folder -> the template falls back to placeholder cards.
_MOMENTS_DIR = Path(__file__).resolve().parent.parent / "static" / "img" / "moments"
_MOMENT_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _moment_images() -> list[str]:
    if not _MOMENTS_DIR.is_dir():
        return []
    return [
        f"/static/img/moments/{p.name}"
        for p in sorted(_MOMENTS_DIR.iterdir())
        if p.suffix.lower() in _MOMENT_EXTS
    ]


@router.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {"app_name": settings.app_name, "moments": _moment_images()},
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

    # rows come priciest-first; rank 0 = cheapest (lightest) .. n-1 = priciest (darkest),
    # so the front-end palette can colour by rank without any stored hex.
    n = len(rows)
    tiers = [
        {
            "name": t.name,
            "rank": n - 1 - i,
            "price_vnd": t.price_vnd,
            "total": total,
            "available": available,
        }
        for i, (t, total, available) in enumerate(rows)
    ]
    return templates.TemplateResponse(
        request, "tickets.html", {"app_name": settings.app_name, "tiers": tiers}
    )
