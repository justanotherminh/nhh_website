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

from app.config import settings
from app.models import Order, Seat, Ticket

log = logging.getLogger("tickets")


def ticket_url(qr_token: str) -> str:
    return f"{settings.base_url}/ve/{qr_token}"


def qr_png_bytes(qr_token: str) -> bytes:
    """A PNG QR code encoding the ticket-page URL for this token."""
    img = qrcode.make(ticket_url(qr_token))
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


def _email_html(order: Order) -> str:
    rows = "".join(
        f"<tr><td style='padding:6px 10px;border:1px solid #eee'>{t.seat.label}</td>"
        f"<td style='padding:6px 10px;border:1px solid #eee'>{_fmt_vnd(t.seat.tier.price_vnd)}</td>"
        f"<td style='padding:6px 10px;border:1px solid #eee'>"
        f"<img src='cid:qr-{t.id}' width='120' height='120' alt='QR'></td></tr>"
        for t in order.tickets
    )
    return f"""\
<div style="font-family:system-ui,sans-serif;color:#1c2230;max-width:600px">
  <h2>Vé điện tử — {settings.app_name}</h2>
  <p>Xin chào {order.buyer_name}, cảm ơn bạn đã ủng hộ đêm nhạc gây quỹ từ thiện.</p>
  <p><strong>Mã đơn hàng:</strong> {order.order_code}<br>
     <strong>Tổng cộng:</strong> {_fmt_vnd(order.amount_vnd)}</p>
  <p>Vui lòng xuất trình mã QR tương ứng tại cửa vào:</p>
  <table style="border-collapse:collapse">
    <thead><tr>
      <th style="padding:6px 10px;border:1px solid #eee;text-align:left">Ghế</th>
      <th style="padding:6px 10px;border:1px solid #eee;text-align:left">Giá</th>
      <th style="padding:6px 10px;border:1px solid #eee;text-align:left">Mã QR</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>"""


def _email_text(order: Order) -> str:
    lines = [
        f"Vé điện tử — {settings.app_name}",
        f"Xin chào {order.buyer_name},",
        f"Mã đơn hàng: {order.order_code}",
        f"Tổng cộng: {_fmt_vnd(order.amount_vnd)}",
        "",
        "Ghế của bạn:",
    ]
    for t in order.tickets:
        lines.append(f"  - {t.seat.label}: {ticket_url(t.qr_token)}")
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

    msg = EmailMessage()
    msg["Subject"] = f"Vé điện tử — {settings.app_name} (Đơn {order.order_code})"
    msg["From"] = settings.smtp_from
    msg["To"] = order.email
    msg.set_content(_email_text(order))
    msg.add_alternative(_email_html(order), subtype="html")

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
