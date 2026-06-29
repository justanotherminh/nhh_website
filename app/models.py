"""SQLAlchemy ORM models for the ticketing system.

Seat-hold state lives directly on the ``seats`` row and is claimed with a single
atomic conditional UPDATE (see ``services/holds.py``); there are no explicit locks.
"""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class PriceTier(Base):
    __tablename__ = "price_tiers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    color_hex: Mapped[str] = mapped_column(String(7), nullable=False)
    price_vnd: Mapped[int] = mapped_column(Integer, nullable=False)

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

    amount_vnd: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
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

    order: Mapped[Order] = relationship(back_populates="tickets")
    seat: Mapped[Seat] = relationship()
