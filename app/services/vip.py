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

from fpdf import FPDF
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.models import Order, Seat, Ticket, VipTicket
from app.services import orders as orders_svc
from app.services import tickets as tickets_svc

log = logging.getLogger("vip")

_FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_FONT = "Noto"

# Palette, matched to the printed-ticket look elsewhere in the app.
_INK = (28, 34, 48)
_MUTED = (120, 120, 120)
_ACCENT = (29, 53, 87)
_HAIRLINE = (208, 208, 208)


class AlreadyExported(Exception):
    """This seat already has a VIP ticket; re-exporting is refused."""


class NotExportable(Exception):
    """The seat can't be exported (missing name, not a VIP seat, etc.)."""


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
    # A landscape invitation card, 180×90 mm. One ticket per file.
    pdf = FPDF(orientation="L", unit="mm", format=(90, 180))
    pdf.set_auto_page_break(False)
    pdf.add_font(_FONT, "", str(_FONT_DIR / "NotoSans-Regular.ttf"))
    pdf.add_font(_FONT, "B", str(_FONT_DIR / "NotoSans-Bold.ttf"))
    return pdf


def render_ticket_pdf(recipient_name: str, seat: Seat, ticket: Ticket) -> bytes:
    """A single VIP invitation ticket as PDF bytes: recipient, seat, and QR.

    The QR encodes the same door check-in URL a paid ticket's QR does, so it scans
    and redeems identically at the entrance.
    """
    pdf = _new_pdf()
    pdf.add_page()

    # Card outline + the perforation line before the QR stub.
    stub_x = 130.0
    pdf.set_draw_color(*_HAIRLINE)
    pdf.rect(4, 4, 172, 82)
    pdf.line(stub_x, 4, stub_x, 86)

    left = 12.0

    def text_at(x, y, s, size, style="", color=_INK):
        pdf.set_xy(x, y)
        pdf.set_font(_FONT, style, size)
        pdf.set_text_color(*color)
        pdf.cell(0, size / 2.2, s)

    text_at(left, 11, "NẮNG HOÀNG HÔN · ĐÊM NHẠC GÂY QUỸ TỪ THIỆN", 8, "B", _ACCENT)
    text_at(left, 17, "Sông Trời", 30, "B", _INK)

    # "VÉ MỜI" pill.
    pdf.set_xy(left, 34)
    pdf.set_font(_FONT, "B", 9)
    pdf.set_text_color(*_ACCENT)
    pdf.set_draw_color(*_ACCENT)
    pdf.cell(26, 7, "VÉ MỜI", border=1, align="C")

    text_at(left, 46, "Vị trí", 8, "", _MUTED)
    text_at(left, 50, seat.label, 13, "B", _INK)

    text_at(left, 61, f"Kính mời: {recipient_name}", 12, "", _INK)

    text_at(left, 77, f"MÃ · {ticket.ticket_code}", 7, "", _MUTED)
    text_at(left, 81, "22.08.2026 · Học viện Âm nhạc Quốc gia Việt Nam", 7, "", _MUTED)

    # QR stub.
    png = tickets_svc.qr_png_bytes(ticket.qr_token)
    pdf.image(io.BytesIO(png), x=stub_x + 8, y=16, w=38, h=38)
    pdf.set_xy(stub_x, 57)
    pdf.set_font(_FONT, "", 7)
    pdf.set_text_color(*_MUTED)
    pdf.cell(46, 4, "Quét mã tại cửa vào", align="C")
    pdf.set_xy(stub_x, 62)
    pdf.set_font(_FONT, "B", 10)
    pdf.set_text_color(*_INK)
    pdf.cell(46, 5, f"{seat.row_label} · {seat.seat_number}", align="C")

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


def export_seat(db: Session, seat_id: int, recipient_name: str) -> VipTicket:
    """Generate and store a VIP ticket PDF for one seat, then record it.

    Mints the comp ticket (booking the seat) if it doesn't have one yet, renders
    the PDF into the depot, and creates the ``VipTicket`` row that marks the seat
    exported. Raises :class:`AlreadyExported` if the seat already has a VIP ticket.
    """
    recipient_name = (recipient_name or "").strip()
    if not recipient_name:
        raise NotExportable("Thiếu tên người nhận.")

    if db.execute(
        select(VipTicket.id).where(VipTicket.seat_id == seat_id)
    ).first():
        raise AlreadyExported(f"Ghế {seat_id} đã được xuất vé.")

    ticket = _existing_comp_ticket(db, seat_id)
    if ticket is None:
        # Books the seat and mints one comp Ticket; no email (hand-delivered).
        order = orders_svc.create_comp_order(
            db, seat_ids=[seat_id], guest_name=recipient_name, send_email=False
        )
        ticket = db.execute(
            select(Ticket)
            .options(selectinload(Ticket.seat).selectinload(Seat.tier))
            .where(Ticket.order_id == order.id)
        ).scalars().one()

    pdf_bytes = render_ticket_pdf(recipient_name, ticket.seat, ticket)
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
