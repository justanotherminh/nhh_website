"""Bulk announcement emails to ticket holders.

Managers compose an announcement in the admin UI; it is queued with its audience
snapshotted into ``announcement_recipients`` rows, then drained by a background
job a few at a time.

Why the queue rather than a loop in the request:

* Several hundred recipients times a fresh SMTP connection each is far longer
  than a web request should live, and a timeout mid-loop would leave nobody
  able to say who had already been mailed.
* Providers cap how fast (and how many) messages they accept. Draining slowly
  keeps us under those caps; see ``settings.announcement_batch_size``.
* A restart mid-blast resumes exactly where it stopped, because "who has been
  sent to" is a database fact rather than a loop counter in memory.

Sending is deliberately one message per recipient rather than one message with
everyone BCC'd: a BCC blast exposes the whole list if a header leaks, can't be
personalised, and is far more likely to be classed as spam.
"""
from __future__ import annotations

import datetime as dt
import logging
import smtplib
from email.message import EmailMessage
from html import escape

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Announcement, AnnouncementRecipient, Order
from app.services.tickets import (
    CONTACT_EMAIL,
    CONTACT_FACEBOOK,
    CONTACT_HOTLINE,
    _send,
)

log = logging.getLogger("announcements")


def audience_query():
    """Ticket holders who bought a ticket: paid, non-invitation orders.

    One row per distinct address — a buyer with two orders is one person and
    should get one copy.
    """
    return (
        select(Order.email, func.min(Order.buyer_name).label("name"))
        .where(Order.status == "paid", Order.kind == "sale")
        .group_by(Order.email)
        .order_by(Order.email)
    )


def audience(db: Session) -> list[tuple[str, str]]:
    return [(r.email, r.name or "") for r in db.execute(audience_query()).all()]


def audience_count(db: Session) -> int:
    return len(audience(db))


# ---------------------------------------------------------------- rendering


def _paragraphs(body: str) -> list[str]:
    """Split the manager's plain text into paragraphs on blank lines.

    Single newlines inside a paragraph become <br>, which is what someone typing
    into a textarea expects (e.g. a list of dates on consecutive lines).
    """
    blocks = [b.strip() for b in body.replace("\r\n", "\n").split("\n\n")]
    return [b for b in blocks if b]


def render_html(subject: str, body: str) -> str:
    """Wrap the manager's text in the same shell as the ticket email.

    The body is escaped, never interpolated as markup: whatever a manager types
    goes out as text, so a stray "<" can't break every recipient's copy.
    """
    paras = "".join(
        f"<p>{escape(p).replace(chr(10), '<br>')}</p>" for p in _paragraphs(body)
    )
    return f"""\
<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;color:#1c2230;
            max-width:620px;line-height:1.6;font-size:15px">
  <p>Thân gửi Quý khán giả,</p>
  {paras}
  <p style="font-weight:700;margin-bottom:4px">BTC NẮNG HOÀNG HÔN 2026</p>
  <p style="margin-top:0;color:#4b5563;font-size:14px">
    Thông tin liên hệ:<br>
    Email: <a href="mailto:{CONTACT_EMAIL}" style="color:#1f6fc4">{CONTACT_EMAIL}</a><br>
    Hotline: <a href="tel:+84935196666" style="color:#1f6fc4">{CONTACT_HOTLINE}</a><br>
    Facebook: <a href="{CONTACT_FACEBOOK}" style="color:#1f6fc4">Nắng Hoàng Hôn Concert</a>
  </p>
</div>"""


def render_text(subject: str, body: str) -> str:
    lines = ["Thân gửi Quý khán giả,", ""]
    for p in _paragraphs(body):
        lines += [p, ""]
    lines += [
        "BTC NẮNG HOÀNG HÔN 2026",
        "Thông tin liên hệ:",
        f"Email: {CONTACT_EMAIL}",
        f"Hotline: {CONTACT_HOTLINE}",
        "Facebook: Nắng Hoàng Hôn Concert",
    ]
    return "\n".join(lines)


def _build(subject: str, body: str, to: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg.set_content(render_text(subject, body))
    msg.add_alternative(render_html(subject, body), subtype="html")
    return msg


def send_preview(subject: str, body: str, to: str) -> None:
    """Send one copy to a single address, touching no announcement records.

    Managers should always do this before queueing the real thing.
    """
    _send(_build(subject, body, to))


# ------------------------------------------------------------------ queueing


def queue(db: Session, subject: str, body: str) -> Announcement:
    """Create an announcement and snapshot its audience as pending recipients."""
    ann = Announcement(subject=subject.strip(), body=body, status="sending")
    db.add(ann)
    db.flush()
    for email, name in audience(db):
        db.add(AnnouncementRecipient(
            announcement_id=ann.id, email=email, name=name
        ))
    db.commit()
    log.info("Queued announcement %s to %d recipient(s)",
             ann.id, len(ann.recipients))
    return ann


def set_status(db: Session, announcement_id: int, status: str) -> None:
    db.execute(
        update(Announcement)
        .where(Announcement.id == announcement_id)
        .values(status=status)
    )
    db.commit()


def progress(db: Session, announcement_id: int) -> dict[str, int]:
    rows = db.execute(
        select(AnnouncementRecipient.status, func.count())
        .where(AnnouncementRecipient.announcement_id == announcement_id)
        .group_by(AnnouncementRecipient.status)
    ).all()
    counts = {status: n for status, n in rows}
    return {
        "pending": counts.get("pending", 0),
        "sent": counts.get("sent", 0),
        "failed": counts.get("failed", 0),
        "total": sum(counts.values()),
    }


# ------------------------------------------------------------------- sending


def send_batch(db: Session, limit: int | None = None) -> int:
    """Send up to ``limit`` queued messages. Returns how many were sent.

    Claims each recipient with a conditional UPDATE before sending, so two
    workers racing on the same row can't both mail the same person: only the
    one whose UPDATE matched a still-'pending' row proceeds.
    """
    limit = limit or settings.announcement_batch_size
    ann = db.execute(
        select(Announcement).where(Announcement.status == "sending")
        .order_by(Announcement.id).limit(1)
    ).scalar_one_or_none()
    if ann is None:
        return 0

    pending = db.execute(
        select(AnnouncementRecipient)
        .where(AnnouncementRecipient.announcement_id == ann.id,
               AnnouncementRecipient.status == "pending")
        .order_by(AnnouncementRecipient.id)
        .limit(limit)
    ).scalars().all()

    if not pending:
        ann.status = "sent"
        ann.finished_at = dt.datetime.now(dt.timezone.utc)
        db.commit()
        log.info("Announcement %s finished", ann.id)
        return 0

    sent = 0
    for r in pending:
        claimed = db.execute(
            update(AnnouncementRecipient)
            .where(AnnouncementRecipient.id == r.id,
                   AnnouncementRecipient.status == "pending")
            .values(status="sending")
        ).rowcount
        db.commit()
        if not claimed:
            continue
        try:
            _send(_build(ann.subject, ann.body, r.email))
        except (smtplib.SMTPException, OSError) as exc:
            # One bad address must not stop the blast; record and move on.
            r.status = "failed"
            r.error = str(exc)[:300]
            log.warning("Announcement %s to %s failed: %s", ann.id, r.email, exc)
        else:
            r.status = "sent"
            r.sent_at = dt.datetime.now(dt.timezone.utc)
            sent += 1
        db.commit()
    return sent
