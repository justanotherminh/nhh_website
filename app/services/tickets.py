"""E-ticket generation and delivery: QR codes + emailed tickets.

Each ``Ticket`` row (minted when an order is paid) has an unguessable ``qr_token``.
The QR encodes the tokenized ticket-page URL, so scanning it at the door opens the
seat's ticket page. Email goes out over SMTP (Mailpit locally, real SMTP in prod).
"""
from __future__ import annotations

import io
import logging
import smtplib
from email.message import EmailMessage

import qrcode
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import i18n
from app.config import settings
from app.models import Order, Seat, Ticket

log = logging.getLogger("tickets")


def ticket_url(qr_token: str) -> str:
    """Buyer-facing, view-only ticket page (used as the link in the email)."""
    return f"{settings.base_url}/ve/{qr_token}"


def checkin_url(qr_token: str) -> str:
    """Staff door-scan URL encoded in the QR: scanning it checks the ticket in."""
    return f"{settings.base_url}/checkin/{qr_token}"


def qr_png_bytes(qr_token: str) -> bytes:
    """A PNG QR code. It encodes the door check-in URL, so scanning it at the
    entrance verifies + redeems the ticket (staff-gated); buyers view their ticket
    via the /ve link in the email instead."""
    img = qrcode.make(checkin_url(qr_token))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _fmt_vnd(n: int) -> str:
    return f"{n:,.0f}".replace(",", ".") + " đ"


def _load_order_with_tickets(db: Session, order_code: int) -> Order | None:
    return db.execute(
        select(Order)
        .options(
            selectinload(Order.tickets)
            .selectinload(Ticket.seat)
            .selectinload(Seat.tier)
        )
        .where(Order.order_code == order_code)
    ).scalar_one_or_none()


CONTACT_EMAIL = "nanghoanghonconcert@gmail.com"
CONTACT_HOTLINE = "093 519 6666"
CONTACT_FACEBOOK = "https://www.facebook.com/nanghoanghonconcert"


def _seat_lines(order: Order, lang: str) -> list[str]:
    """One line per tier: quantity, tier name, then each seat's floor/row/number.

    Grouped by tier because a buyer picking three seats in one tier should read
    "3 Sông Trời", not the same tier name repeated three times.
    """
    is_comp = order.kind == "comp"
    groups: dict[str, list[Seat]] = {}
    for t in order.tickets:
        name = i18n.t("email.comp_group", lang) if is_comp else t.seat.tier.name
        groups.setdefault(name, []).append(t.seat)
    row_word = i18n.t("seatline.row", lang)
    seat_word = i18n.t("seatline.seat", lang)
    lines = []
    for name, seats in groups.items():
        where = ", ".join(
            f"{i18n.floor_name(s.section, lang)} {row_word} {s.row_label} "
            f"{seat_word} {s.seat_number}"
            for s in seats
        )
        lines.append(f"{len(seats)} {name} — {where}")
    return lines


def _email_html(order: Order, lang: str) -> str:
    is_comp = order.kind == "comp"
    headline = i18n.t("email.headline_comp" if is_comp else "email.headline_sale", lang)
    if is_comp:
        amount_row = (
            f"<tr><td style='padding:3px 0'>{i18n.t('email.ticket_type', lang)}:</td>"
            f"<td style='padding:3px 0 3px 10px'><strong>{i18n.t('email.comp_free', lang)}</strong></td></tr>"
        )
    else:
        amount_row = (
            f"<tr><td style='padding:3px 0'>{i18n.t('email.amount_label', lang)}:</td>"
            f"<td style='padding:3px 0 3px 10px'><strong>{_fmt_vnd(order.amount_vnd)}</strong></td></tr>"
        )
    seats_html = "<br>".join(_seat_lines(order, lang))
    view_label = i18n.t("email.view_ticket", lang)
    qr_alt = i18n.t("email.qr_alt", lang)
    # One QR per ticket, captioned so a multi-seat buyer knows which is which.
    qrs = "".join(
        f"<div style='display:inline-block;text-align:center;margin:0 14px 14px 0'>"
        f"<img src='cid:qr-{t.id}' width='150' height='150' alt='{qr_alt}'"
        f" style='display:block;border:1px solid #e6e8ec'>"
        f"<div style='font-size:12px;color:#6b7280;margin-top:5px'>{i18n.seat_label(t.seat, lang)}</div>"
        f"<div style='font-size:12px'><a href='{ticket_url(t.qr_token)}'"
        f" style='color:#1f6fc4'>{view_label}</a></div></div>"
        for t in order.tickets
    )
    thanks_html = f"<p>{i18n.t('email.thanks', lang)}</p>" if not is_comp else ""
    return f"""\
<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;color:#1c2230;
            max-width:620px;line-height:1.6;font-size:15px">
  <p>{i18n.t('email.greeting', lang)}</p>

  <p>{i18n.t('email.intro', lang)}</p>

  <p style="text-align:center;font-weight:700;font-size:17px;
            background:#f2f6fb;border-radius:8px;padding:14px 12px">{headline}</p>

  <p style="font-weight:700;margin-bottom:6px">{i18n.t('email.registrant', lang)}</p>
  <table style="border-collapse:collapse;font-size:15px">
    <tr><td style="padding:3px 0">{i18n.t('email.name', lang)}:</td>
        <td style="padding:3px 0 3px 10px"><strong>{order.buyer_name}</strong></td></tr>
    <tr><td style="padding:3px 0;vertical-align:top">{i18n.t('email.seat_class', lang)}:</td>
        <td style="padding:3px 0 3px 10px"><strong>{seats_html}</strong></td></tr>
    {amount_row}
    <tr><td style="padding:3px 0">{i18n.t('email.order_code', lang)}:</td>
        <td style="padding:3px 0 3px 10px"><strong>{order.order_code}</strong></td></tr>
  </table>

  <div style="margin:20px 0">{qrs}</div>

  <p>{i18n.t('email.update_note', lang)}</p>
  {thanks_html}
  <p>{i18n.t('email.closing', lang)}</p>

  <p style="font-weight:700;margin-bottom:4px">{i18n.t('email.signoff', lang)}</p>
  <p style="margin-top:0;color:#4b5563;font-size:14px">
    {i18n.t('email.contact', lang)}<br>
    Email: <a href="mailto:{CONTACT_EMAIL}" style="color:#1f6fc4">{CONTACT_EMAIL}</a><br>
    {i18n.t('email.hotline', lang)}: <a href="tel:+84935196666" style="color:#1f6fc4">{CONTACT_HOTLINE}</a><br>
    Facebook: <a href="{CONTACT_FACEBOOK}" style="color:#1f6fc4">Nắng Hoàng Hôn Concert</a>
  </p>
</div>"""


def _email_text(order: Order, lang: str) -> str:
    is_comp = order.kind == "comp"
    lines = [
        i18n.t("email.greeting", lang),
        "",
        i18n.t("email.intro", lang),
        "",
        i18n.t("email.headline_comp" if is_comp else "email.headline_sale", lang),
        "",
        i18n.t("email.registrant", lang),
        f"{i18n.t('email.name', lang)}: {order.buyer_name}",
        f"{i18n.t('email.seat_class', lang)}:",
    ]
    lines += [f"  {line}" for line in _seat_lines(order, lang)]
    lines.append(
        f"{i18n.t('email.ticket_type', lang)}: {i18n.t('email.comp_free', lang)}"
        if is_comp
        else f"{i18n.t('email.amount_label', lang)}: {_fmt_vnd(order.amount_vnd)}"
    )
    lines += [f"{i18n.t('email.order_code', lang)}: {order.order_code}", "",
              i18n.t("email.your_tickets", lang)]
    for t in order.tickets:
        lines.append(f"  - {i18n.seat_label(t.seat, lang)}: {ticket_url(t.qr_token)}")
    lines += ["", i18n.t("email.update_note", lang)]
    if not is_comp:
        lines += ["", i18n.t("email.thanks", lang)]
    lines += [
        "",
        i18n.t("email.closing", lang),
        "",
        i18n.t("email.signoff", lang),
        i18n.t("email.contact", lang),
        f"Email: {CONTACT_EMAIL}",
        f"{i18n.t('email.hotline', lang)}: {CONTACT_HOTLINE}",
        "Facebook: Nắng Hoàng Hôn Concert",
    ]
    return "\n".join(lines)


def _send(msg: EmailMessage) -> None:
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
        if settings.smtp_use_tls:
            server.starttls()
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg)


def send_ticket_email(db: Session, order_code: int) -> bool:
    """Email all e-tickets for an order, with each seat's QR embedded inline.

    Returns True if an email was sent. Callers should treat failure as non-fatal:
    the payment is already confirmed regardless of email delivery.
    """
    order = _load_order_with_tickets(db, order_code)
    if order is None or not order.tickets:
        return False

    lang = i18n.normalize(order.lang)
    msg = EmailMessage()
    msg["Subject"] = i18n.t("email.subject", lang)
    msg["From"] = settings.smtp_from
    msg["To"] = order.email
    msg.set_content(_email_text(order, lang))
    msg.add_alternative(_email_html(order, lang), subtype="html")

    # Attach each QR as a related image the HTML references via cid:.
    html_part = msg.get_payload()[-1]
    for t in order.tickets:
        html_part.add_related(
            qr_png_bytes(t.qr_token),
            maintype="image",
            subtype="png",
            cid=f"<qr-{t.id}>",
        )

    _send(msg)
    log.info("Sent %d e-ticket(s) for order %s to %s",
             len(order.tickets), order.order_code, order.email)
    return True
