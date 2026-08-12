"""Public, server-rendered pages: front page and the ticket/seat page."""
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import artists, i18n
from app.config import settings
from app.db import get_db
from app.models import PriceTier
from app.services import images as images_svc
from app.templates import static_url, templates

router = APIRouter()


# One year: the choice is a preference, not a session, so it should persist.
_LANG_COOKIE_MAX_AGE = 60 * 60 * 24 * 365


def _safe_next(raw: str | None) -> str:
    """A same-site redirect target: a single leading slash, no scheme or host.

    Rejects protocol-relative (``//evil.com``) and absolute URLs so the toggle
    can't be turned into an open redirect.
    """
    if raw and raw.startswith("/") and not raw.startswith("//"):
        return raw
    return "/"


@router.get("/lang/{code}")
def set_language(code: str, request: Request, next: str = "/") -> RedirectResponse:
    """Switch the display language, then return to where the visitor was.

    Unknown codes fall back to the default, so a hand-typed URL can't wedge the
    cookie into an invalid state.
    """
    lang = i18n.normalize(code)
    resp = RedirectResponse(_safe_next(next), status_code=303)
    resp.set_cookie(
        "lang", lang, max_age=_LANG_COOKIE_MAX_AGE, httponly=False, samesite="lax"
    )
    return resp

# Hero reel photos: whatever managers marked "show on homepage" in the admin image
# library (stored in the uploads volume). Until they add any, fall back to the
# bundled placeholder tiles so the hero is never empty.
_MOMENTS_DIR = Path(__file__).resolve().parent.parent / "static" / "img" / "moments"
_MOMENT_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _moment_images() -> list[str]:
    uploaded = images_svc.reel_relpaths()
    if uploaded:
        return [static_url(rel) for rel in uploaded]   # cache-busted
    if not _MOMENTS_DIR.is_dir():
        return []
    return [
        static_url(f"img/moments/{p.name}")
        for p in sorted(_MOMENTS_DIR.iterdir())
        if p.suffix.lower() in _MOMENT_EXTS
    ]


@router.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    lang = getattr(request.state, "lang", i18n.DEFAULT_LANG)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "app_name": settings.app_name,
            "moments": _moment_images(),
            "artist_groups": artists.for_lang(lang),
        },
    )


@router.get("/tickets", response_class=HTMLResponse)
def tickets(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    # Just the price legend. Per-tier availability counts used to be shown here and
    # were deliberately dropped: the seat map already tells a buyer exactly which
    # seats are free, seat by seat, without also publishing how much of the hall is
    # still empty.
    rows = db.execute(
        select(PriceTier).order_by(PriceTier.price_vnd.desc())
    ).scalars().all()

    # rows come priciest-first; rank 0 = cheapest (lightest) .. n-1 = priciest (darkest),
    # so the front-end palette can colour by rank without any stored hex.
    n = len(rows)
    tiers = [
        {"name": t.name, "rank": n - 1 - i, "price_vnd": t.price_vnd}
        for i, t in enumerate(rows)
    ]
    return templates.TemplateResponse(
        request, "tickets.html", {"app_name": settings.app_name, "tiers": tiers}
    )
