"""SQLAlchemy ORM models for the ticketing system.

Seat-hold state lives directly on the ``seats`` row and is claimed with a single
atomic conditional UPDATE (see ``services/holds.py``); there are no explicit locks.
"""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class AppSetting(Base):
    """Runtime-editable key/value config (e.g. the early-bird promo), so managers
    can change it from the admin UI without a redeploy."""
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(String(200), nullable=False, default="")


class PriceTier(Base):
    __tablename__ = "price_tiers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    price_vnd: Mapped[int] = mapped_column(Integer, nullable=False)
    # Colour is presentation, not domain data — it lives in the front-end palette
    # (styles.css, keyed by price rank), not here. See seatmap.js / .seat-g.tier-r*.

    seats: Mapped[list[Seat]] = relationship(back_populates="tier")


class Seat(Base):
    __tablename__ = "seats"
    __table_args__ = (
        UniqueConstraint("section", "row_label", "seat_number", name="uq_seat_position"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    section: Mapped[str] = mapped_column(String(50), nullable=False)
    row_label: Mapped[str] = mapped_column(String(10), nullable=False)
    seat_number: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    tier_id: Mapped[int] = mapped_column(ForeignKey("price_tiers.id"), nullable=False)

    # Position on the rendered SVG hall map.
    svg_x: Mapped[float] = mapped_column(nullable=False, default=0)
    svg_y: Mapped[float] = mapped_column(nullable=False, default=0)

    # 'available' (sellable), 'blocked' (held back from public sale), 'booked' (paid).
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="available")

    # Membership of the VIP / invitation pool, and the single source of truth for it.
    #
    # Deliberately separate from ``status``: a VIP seat is 'blocked' until its
    # invitation is exported and 'booked' afterwards, and it stays VIP throughout.
    # Conversely a seat can be 'blocked' for unrelated reasons (see
    # scripts/block_seats.py) without ever being VIP.
    #
    # This used to be defined by scripts/data/vip_reserved_seats.csv, re-applied on
    # every boot. That CSV is now only a first-boot seed: managers edit the pool from
    # /admin/vip-seats, and re-applying a file on each deploy would silently revert
    # their changes. See app/services/vip.py.
    is_vip: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    # Hold state: a seat is *held* when status='available' AND hold_expires_at > now().
    held_by_cart: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    hold_expires_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    tier: Mapped[PriceTier] = relationship(back_populates="seats")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Numeric code required by payOS (unique per payment link).
    order_code: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    # 'sale' = paid via payOS; 'comp' = free invitation ticket (no payment).
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default="sale")
    cart_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)

    buyer_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str] = mapped_column(String(30), nullable=False)
    # Language the buyer used at checkout; the confirmation e-ticket is sent in it.
    lang: Mapped[str] = mapped_column(String(5), nullable=False, default="vi", server_default="vi")

    amount_vnd: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Early-bird discount actually applied to this order (percent; 0 = none). The
    # per-seat OrderItem.price_vnd already reflect it and sum to amount_vnd; this is
    # kept for honest receipts/reporting (list price vs. what was charged).
    discount_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # 'pending' | 'paid' | 'cancelled' | 'expired'
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    payos_payment_link_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    tickets: Mapped[list[Ticket]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    seat_id: Mapped[int] = mapped_column(ForeignKey("seats.id"), nullable=False)
    price_vnd: Mapped[int] = mapped_column(Integer, nullable=False)

    order: Mapped[Order] = relationship(back_populates="items")
    seat: Mapped[Seat] = relationship()


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    seat_id: Mapped[int] = mapped_column(ForeignKey("seats.id"), nullable=False)
    ticket_code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    qr_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # Set the first time the ticket is scanned at the door; guards against re-entry.
    checked_in_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Set when the seat is refunded. The row is kept rather than deleted so the
    # check-in history survives and the door can say "refunded" instead of the
    # same "invalid" it shows a forged QR. A voided ticket never admits anyone.
    voided_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    order: Mapped[Order] = relationship(back_populates="tickets")
    seat: Mapped[Seat] = relationship()


class Refund(Base):
    """One refunded seat: the ledger the fund managers reconcile against the bank.

    payOS has no refund API, so the money moves by hand — a human transfers it back
    from the organisers' own banking. This row records only what happened *in the
    database* so the two can be reconciled afterwards: which seat of which order was
    given back, for how much, by whom, and where the seat went.

    Refunds are per-seat, not per-order, because an order can hold up to eight seats
    and a buyer may hand back only some of them. ``amount_vnd`` is copied from the
    seat's ``OrderItem.price_vnd``, which is what that seat was actually charged
    (early-bird discount included) — never the tier's list price.

    The (order_id, seat_id) uniqueness is what makes refunding idempotent: a
    double-submitted form or a second manager clicking the same button hits the
    constraint instead of paying someone back twice.
    """
    __tablename__ = "refunds"
    __table_args__ = (
        UniqueConstraint("order_id", "seat_id", name="uq_refund_order_seat"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    seat_id: Mapped[int] = mapped_column(ForeignKey("seats.id"), nullable=False)
    # The ticket voided by this refund. Nullable only so the ledger survives if a
    # ticket row is ever removed by other means; normally always set.
    ticket_id: Mapped[int | None] = mapped_column(
        ForeignKey("tickets.id"), nullable=True
    )
    amount_vnd: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Admin username from HTTP Basic auth — who pressed the button.
    refunded_by: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    # Free text: bank transfer reference, reason, whatever the managers need later.
    note: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    order: Mapped[Order] = relationship()
    seat: Mapped[Seat] = relationship()
    ticket: Mapped[Ticket | None] = relationship()


class VipTicket(Base):
    """A generated VIP invitation ticket: its stored PDF and delivery state.

    One row per VIP seat that has been exported. The comp ``Ticket`` it points at
    holds the QR; this row adds the recipient's name, the depot PDF filename, and
    whether the ticket has been printed and handed over. A VIP seat's three map
    states derive entirely from this row: no row = unexported, row with
    ``sent_at`` null = exported, ``sent_at`` set = sent.
    """
    __tablename__ = "vip_tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    # One active VIP ticket per seat: exporting the same seat twice is blocked.
    seat_id: Mapped[int] = mapped_column(
        ForeignKey("seats.id"), unique=True, nullable=False
    )
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    recipient_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Filename within settings.vip_depot_dir (never a path); see services/vip.py.
    pdf_filename: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Set when a manager marks the ticket printed and handed to the recipient.
    sent_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    seat: Mapped[Seat] = relationship()
    ticket: Mapped[Ticket] = relationship()


class Announcement(Base):
    """A bulk email composed in the admin UI and sent to ticket holders.

    Recipients are snapshotted into ``AnnouncementRecipient`` rows the moment the
    announcement is queued, so the audience can't shift underneath a send that's
    already in flight, and so a crash mid-blast can resume without re-mailing
    anyone who already received it.
    """
    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(primary_key=True)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # draft -> sending -> sent (or paused, if a manager stops it part-way)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    recipients: Mapped[list[AnnouncementRecipient]] = relationship(
        back_populates="announcement",
        cascade="all, delete-orphan",
    )


class AnnouncementRecipient(Base):
    """One addressee of one announcement, and whether their copy went out.

    The (announcement_id, email) unique constraint is what makes double-sending
    impossible even if a send is triggered twice concurrently.
    """
    __tablename__ = "announcement_recipients"
    __table_args__ = (
        UniqueConstraint("announcement_id", "email", name="uq_announcement_email"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    announcement_id: Mapped[int] = mapped_column(
        ForeignKey("announcements.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    # pending -> sent | failed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    sent_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    announcement: Mapped[Announcement] = relationship(back_populates="recipients")
