"""VIP invitation tickets: generate a per-recipient PDF, store it in the depot,
and track its delivery state.

The depot is a directory outside the ``/static`` mount (``settings.vip_depot_dir``,
a persistent volume in prod), because a ticket PDF embeds a live check-in QR and so
must never be publicly downloadable — it's streamed only through the authenticated
admin route.

A VIP seat's three map states derive entirely from the ``VipTicket`` row:
    no row          -> "unexported"
    row, sent_at NULL -> "exported"
    row, sent_at set  -> "sent"
"""
from __future__ import annotations

import datetime as dt
import io
import logging
import re
import secrets
from pathlib import Path

import qrcode
from fpdf import FPDF
from PIL import Image, ImageDraw
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.models import Order, Seat, Ticket, VipTicket
from app.services import orders as orders_svc
from app.services import tickets as tickets_svc

log = logging.getLogger("vip")

_ASSET_DIR = Path(__file__).resolve().parent.parent / "assets"
_FONT_DIR = _ASSET_DIR / "fonts"
# The invitation's own typeface is Poppins (a geometric sans); Montserrat is the
# closest match we can ship (also SIL OFL) and blends on the all-caps seat line.
_FONT = "Montserrat"
# Optimized raster of the designed A5 invitation (the "SỐ GHẾ:" template). The
# 558 MB source PDF is a design artifact and stays out of the repo; this derivative
# is what every generated ticket is drawn on top of.
_BG = _ASSET_DIR / "vip_ticket_bg.jpg"

# A5 portrait, matching the template exactly.
_PAGE_W, _PAGE_H = 148.0, 210.0

# The template's "SỐ GHẾ:" ink, sampled from the design.
_NAVY = (40, 56, 102)
_MUTED = (90, 108, 140)

# Position of the "SỐ GHẾ:" line, measured from the design (px -> mm).
_SEAT_BASELINE_Y = 194.5   # text baseline
_SEAT_X = 32.0             # just past the colon (~27.7mm) + a small gap
_SEAT_PT = 15.0            # ≈ the label's cap height (3.64mm)


class AlreadyExported(Exception):
    """This seat already has a VIP ticket; re-exporting is refused."""


def _depot() -> Path:
    d = Path(settings.vip_depot_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def depot_file(filename: str) -> Path | None:
    """Resolve a stored filename to a real file in the depot, or None.

    Rejects any path separators so a crafted filename can't escape the depot.
    """
    if not filename or "/" in filename or "\\" in filename:
        return None
    depot = _depot().resolve()
    p = (depot / filename).resolve()
    if p.parent == depot and p.is_file():
        return p
    return None


# --------------------------------------------------------------------- PDF

def _new_pdf() -> FPDF:
    pdf = FPDF(orientation="P", unit="mm", format=(_PAGE_W, _PAGE_H))
    pdf.set_auto_page_break(False)
    pdf.set_margins(0, 0, 0)
    pdf.add_font(_FONT, "", str(_FONT_DIR / "Montserrat-Regular.ttf"))
    pdf.add_font(_FONT, "B", str(_FONT_DIR / "Montserrat-Bold.ttf"))
    return pdf


def _qr_png(data: str, color: tuple[int, int, int], box: int = 12, border: int = 2) -> bytes:
    """A QR as an RGBA PNG: ``color`` modules on a fully transparent background, so
    it sits on the ticket artwork with no white box behind it."""
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                       box_size=box, border=border)
    qr.add_data(data)
    qr.make(fit=True)
    matrix = qr.get_matrix()          # includes the quiet-zone border
    side = len(matrix) * box
    img = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    fill = (*color, 255)
    for r, row in enumerate(matrix):
        for c, on in enumerate(row):
            if on:
                draw.rectangle([c * box, r * box, (c + 1) * box - 1, (r + 1) * box - 1], fill=fill)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_ticket_pdf(seat: Seat, ticket: Ticket) -> bytes:
    """A single VIP invitation ticket as PDF bytes: the designed A5 invitation with
    this seat filled in after "SỐ GHẾ:" and its check-in QR placed bottom-right.

    Deliberately carries no recipient name, so the ticket is bearer/transferable —
    whoever holds it can be admitted. The QR encodes the same door check-in URL a
    paid ticket's QR does, so it scans and redeems identically at the entrance.
    """
    pdf = _new_pdf()
    pdf.add_page()

    # The designed invitation, full-bleed.
    pdf.image(str(_BG), x=0, y=0, w=_PAGE_W, h=_PAGE_H)

    # Seat, sitting on the template's "SỐ GHẾ:" baseline. Upper-cased to match the
    # design's all-caps line; pdf.text places by the baseline, for exact alignment.
    seat_text = f"{seat.section} · {seat.row_label}{seat.seat_number}".upper()
    pdf.set_font(_FONT, "", _SEAT_PT)
    pdf.set_text_color(*_NAVY)
    pdf.text(_SEAT_X, _SEAT_BASELINE_Y, seat_text)

    # Transparent, navy check-in QR, low in the bottom-right corner so its centre
    # lines up with the seat row (~192mm) while keeping a small bottom margin.
    qr = _qr_png(tickets_svc.checkin_url(ticket.qr_token), _NAVY)
    qr_w = 30.0
    qr_x = _PAGE_W - qr_w - 10      # right margin ~10mm, tucked toward the corner
    qr_y = _SEAT_BASELINE_Y - qr_w / 2 - 2.5   # centre ≈ 192mm
    pdf.image(io.BytesIO(qr), x=qr_x, y=qr_y, w=qr_w, h=qr_w)

    return bytes(pdf.output())


# ---------------------------------------------------------------- operations

def _existing_comp_ticket(db: Session, seat_id: int) -> Ticket | None:
    """A comp ticket already minted for this seat, if any (with seat + tier)."""
    return db.execute(
        select(Ticket)
        .options(selectinload(Ticket.seat).selectinload(Seat.tier))
        .join(Order, Ticket.order_id == Order.id)
        .where(Ticket.seat_id == seat_id, Order.kind == "comp")
    ).scalars().first()


def export_seat(db: Session, seat_id: int, recipient_name: str = "") -> VipTicket:
    """Generate and store a VIP ticket PDF for one seat, then record it.

    Mints the comp ticket (booking the seat) if it doesn't have one yet, renders
    the PDF into the depot, and creates the ``VipTicket`` row that marks the seat
    exported. Raises :class:`AlreadyExported` if the seat already has a VIP ticket.

    ``recipient_name`` is optional and for the organisers' own tracking only — it is
    never printed on the ticket, which is intentionally bearer/transferable.
    """
    recipient_name = (recipient_name or "").strip()

    if db.execute(
        select(VipTicket.id).where(VipTicket.seat_id == seat_id)
    ).first():
        raise AlreadyExported(f"Ghế {seat_id} đã được xuất vé.")

    ticket = _existing_comp_ticket(db, seat_id)
    if ticket is None:
        # Books the seat and mints one comp Ticket; no email (hand-delivered).
        order = orders_svc.create_comp_order(
            db, seat_ids=[seat_id], guest_name=recipient_name or "Vé mời",
            send_email=False,
        )
        ticket = db.execute(
            select(Ticket)
            .options(selectinload(Ticket.seat).selectinload(Seat.tier))
            .where(Ticket.order_id == order.id)
        ).scalars().one()

    pdf_bytes = render_ticket_pdf(ticket.seat, ticket)
    filename = f"seat{seat_id}-{secrets.token_hex(4)}.pdf"
    (_depot() / filename).write_bytes(pdf_bytes)

    vip = VipTicket(
        seat_id=seat_id,
        ticket_id=ticket.id,
        recipient_name=recipient_name,
        pdf_filename=filename,
    )
    db.add(vip)
    db.commit()
    db.refresh(vip)
    log.info("Exported VIP ticket for seat %s to %r (%s)", seat_id, recipient_name, filename)
    return vip


def states_by_seat(db: Session) -> dict[int, str]:
    """Map of seat_id -> "exported" | "sent" for every seat with a VIP ticket."""
    rows = db.execute(select(VipTicket.seat_id, VipTicket.sent_at)).all()
    return {sid: ("sent" if sent_at else "exported") for sid, sent_at in rows}


def list_tickets(db: Session) -> list[VipTicket]:
    """All generated VIP tickets, newest first, with their seat loaded."""
    return db.execute(
        select(VipTicket)
        .options(selectinload(VipTicket.seat))
        .order_by(VipTicket.created_at.desc())
    ).scalars().all()


def mark_sent(db: Session, vip_id: int, sent: bool = True) -> bool:
    """Flag (or un-flag) a VIP ticket as printed and handed to the recipient."""
    vip = db.get(VipTicket, vip_id)
    if vip is None:
        return False
    vip.sent_at = dt.datetime.now(dt.timezone.utc) if sent else None
    db.commit()
    return True


def download_name(vip: VipTicket) -> str:
    """A safe, human ASCII filename for the browser's Save dialog."""
    seat = vip.seat
    stem = f"ve-moi-{seat.section}-{seat.row_label}-{seat.seat_number}"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-") or "ve-moi"
    return f"{stem}.pdf"
