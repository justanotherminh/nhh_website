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


EMAIL_SUBJECT = "[NẮNG HOÀNG HÔN 2026] XÁC NHẬN ĐĂNG KÝ VÉ THÀNH CÔNG"

CONTACT_EMAIL = "nanghoanghonconcert@gmail.com"
CONTACT_HOTLINE = "093 519 6666"
CONTACT_FACEBOOK = "https://www.facebook.com/nanghoanghonconcert"

# The closing thank-you is about the donation, so it only fits paid orders.
_THANKS = (
    "Thay mặt cho các bệnh nhân chạy thận nhân tạo được nhận sự hỗ trợ này, chúng "
    "tôi xin gửi lời tri ân sâu sắc đến bạn. Hành động tử tế của bạn không chỉ mang "
    "đến sự hỗ trợ thiết thực cho những mảnh đời đang gặp khó khăn mà còn lan tỏa "
    "thông điệp ý nghĩa về lòng nhân ái và sự sẻ chia. Mỗi đóng góp, dù là nhỏ bé "
    "nhất, đều góp phần tạo nên sự khác biệt to lớn, mang đến hy vọng và niềm tin "
    "cho những người đang phải chiến đấu với căn bệnh hiểm nghèo."
)

_UPDATE_NOTE = (
    "BTC sẽ gửi email cập nhật thông tin chi tiết về buổi hòa nhạc trong thời gian "
    "ngắn tới. Quý khán giả vui lòng chú ý hòm mail."
)


def _seat_lines(order: Order) -> list[str]:
    """One line per tier: quantity, tier name, then each seat's floor/row/number.

    Grouped by tier because a buyer picking three seats in one tier should read
    "3 Sông Trời", not the same tier name repeated three times.
    """
    is_comp = order.kind == "comp"
    groups: dict[str, list[Seat]] = {}
    for t in order.tickets:
        name = "Vé mời" if is_comp else t.seat.tier.name
        groups.setdefault(name, []).append(t.seat)
    lines = []
    for name, seats in groups.items():
        where = ", ".join(
            f"{s.section} Hàng {s.row_label} Ghế {s.seat_number}" for s in seats
        )
        lines.append(f"{len(seats)} {name} — {where}")
    return lines


def _email_html(order: Order) -> str:
    is_comp = order.kind == "comp"
    headline = (
        "BẠN ĐÃ ĐĂNG KÝ VÉ MỜI THÀNH CÔNG!"
        if is_comp
        else "BẠN ĐÃ ĐĂNG KÝ ĐẶT VÉ VÀ QUYÊN GÓP THÀNH CÔNG!"
    )
    amount_row = (
        "<tr><td style='padding:3px 0'>Loại vé:</td>"
        "<td style='padding:3px 0 3px 10px'><strong>Vé mời (miễn phí)</strong></td></tr>"
        if is_comp
        else "<tr><td style='padding:3px 0'>Giá vé tương đương giá trị gây quỹ:</td>"
        f"<td style='padding:3px 0 3px 10px'><strong>{_fmt_vnd(order.amount_vnd)}</strong></td></tr>"
    )
    seats_html = "<br>".join(_seat_lines(order))
    # One QR per ticket, captioned so a multi-seat buyer knows which is which.
    qrs = "".join(
        f"<div style='display:inline-block;text-align:center;margin:0 14px 14px 0'>"
        f"<img src='cid:qr-{t.id}' width='150' height='150' alt='Mã QR'"
        f" style='display:block;border:1px solid #e6e8ec'>"
        f"<div style='font-size:12px;color:#6b7280;margin-top:5px'>{t.seat.label}</div>"
        f"<div style='font-size:12px'><a href='{ticket_url(t.qr_token)}'"
        f" style='color:#1f6fc4'>Xem vé</a></div></div>"
        for t in order.tickets
    )
    thanks_html = f"<p>{_THANKS}</p>" if not is_comp else ""
    return f"""\
<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;color:#1c2230;
            max-width:620px;line-height:1.6;font-size:15px">
  <p>Thân gửi Quý khán giả,</p>

  <p>Lời đầu tiên, BTC Nắng Hoàng Hôn 2026 xin gửi lời cảm ơn sâu sắc tới quý vị vì
     đã quan tâm và dành thời gian tham dự Chương trình hoà nhạc từ thiện Nắng Hoàng
     Hôn 2026 – Sông Trời. Chúng tôi xin trân trọng thông báo:</p>

  <p style="text-align:center;font-weight:700;font-size:17px;
            background:#f2f6fb;border-radius:8px;padding:14px 12px">{headline}</p>

  <p style="font-weight:700;margin-bottom:6px">Thông tin người đăng ký</p>
  <table style="border-collapse:collapse;font-size:15px">
    <tr><td style="padding:3px 0">Họ và tên:</td>
        <td style="padding:3px 0 3px 10px"><strong>{order.buyer_name}</strong></td></tr>
    <tr><td style="padding:3px 0;vertical-align:top">Hạng ghế:</td>
        <td style="padding:3px 0 3px 10px"><strong>{seats_html}</strong></td></tr>
    {amount_row}
    <tr><td style="padding:3px 0">Mã đơn hàng:</td>
        <td style="padding:3px 0 3px 10px"><strong>{order.order_code}</strong></td></tr>
  </table>

  <div style="margin:20px 0">{qrs}</div>

  <p>{_UPDATE_NOTE}</p>
  {thanks_html}
  <p>Xin chân thành cảm ơn!</p>

  <p style="font-weight:700;margin-bottom:4px">BTC NẮNG HOÀNG HÔN 2026</p>
  <p style="margin-top:0;color:#4b5563;font-size:14px">
    Thông tin liên hệ:<br>
    Email: <a href="mailto:{CONTACT_EMAIL}" style="color:#1f6fc4">{CONTACT_EMAIL}</a><br>
    Hotline: <a href="tel:+84935196666" style="color:#1f6fc4">{CONTACT_HOTLINE}</a><br>
    Facebook: <a href="{CONTACT_FACEBOOK}" style="color:#1f6fc4">Nắng Hoàng Hôn Concert</a>
  </p>
</div>"""


def _email_text(order: Order) -> str:
    is_comp = order.kind == "comp"
    lines = [
        "Thân gửi Quý khán giả,",
        "",
        "Lời đầu tiên, BTC Nắng Hoàng Hôn 2026 xin gửi lời cảm ơn sâu sắc tới quý vị "
        "vì đã quan tâm và dành thời gian tham dự Chương trình hoà nhạc từ thiện Nắng "
        "Hoàng Hôn 2026 – Sông Trời. Chúng tôi xin trân trọng thông báo:",
        "",
        "BẠN ĐÃ ĐĂNG KÝ VÉ MỜI THÀNH CÔNG!"
        if is_comp
        else "BẠN ĐÃ ĐĂNG KÝ ĐẶT VÉ VÀ QUYÊN GÓP THÀNH CÔNG!",
        "",
        "Thông tin người đăng ký",
        f"Họ và tên: {order.buyer_name}",
        "Hạng ghế:",
    ]
    lines += [f"  {line}" for line in _seat_lines(order)]
    lines.append(
        "Loại vé: Vé mời (miễn phí)"
        if is_comp
        else f"Giá vé tương đương giá trị gây quỹ: {_fmt_vnd(order.amount_vnd)}"
    )
    lines += [f"Mã đơn hàng: {order.order_code}", "", "Vé của bạn:"]
    for t in order.tickets:
        lines.append(f"  - {t.seat.label}: {ticket_url(t.qr_token)}")
    lines += ["", _UPDATE_NOTE]
    if not is_comp:
        lines += ["", _THANKS]
    lines += [
        "",
        "Xin chân thành cảm ơn!",
        "",
        "BTC NẮNG HOÀNG HÔN 2026",
        "Thông tin liên hệ:",
        f"Email: {CONTACT_EMAIL}",
        f"Hotline: {CONTACT_HOTLINE}",
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

    msg = EmailMessage()
    msg["Subject"] = EMAIL_SUBJECT
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
