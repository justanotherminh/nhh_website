"""Seed a believable placeholder hall so the UI works before the real Excel
importer (scripts/import_seatmap.py) is built.

Tiers/prices mirror the real legend (the 'Vé' tab):
    Loại 1 = 700,000 VND   Loại 2 = 500,000 VND   Loại 3 = 300,000 VND

Idempotent: wipes seats/tiers and re-creates them. Run with:
    .venv/bin/python -m scripts.seed_placeholder
"""
from __future__ import annotations

from sqlalchemy import delete

from app.db import SessionLocal
from app.models import OrderItem, PriceTier, Seat, Ticket

# (name, color_hex, price_vnd, row_range)  — rows nearer the stage are pricier.
TIERS = [
    ("Loại 1", "#c0392b", 700_000, "ABCDE"),
    ("Loại 2", "#2980b9", 500_000, "FGHIJ"),
    ("Loại 3", "#27ae60", 300_000, "KLMNO"),
]

SEATS_PER_ROW = 20
SEAT_DX = 34       # horizontal spacing on the SVG
SEAT_DY = 40       # vertical spacing on the SVG
X_ORIGIN = 60
Y_ORIGIN = 120
SECTION = "Tầng 1"


def seed() -> None:
    db = SessionLocal()
    try:
        # Clean slate (children first to respect FKs).
        db.execute(delete(Ticket))
        db.execute(delete(OrderItem))
        db.execute(delete(Seat))
        db.execute(delete(PriceTier))
        db.flush()

        row_to_tier: dict[str, PriceTier] = {}
        for name, color, price, rows in TIERS:
            tier = PriceTier(name=name, color_hex=color, price_vnd=price)
            db.add(tier)
            db.flush()  # assign id
            for r in rows:
                row_to_tier[r] = tier

        n = 0
        for ri, row in enumerate(row_to_tier):
            tier = row_to_tier[row]
            for s in range(1, SEATS_PER_ROW + 1):
                label = f"{SECTION} – Hàng {row} – Ghế {s}"
                db.add(
                    Seat(
                        section=SECTION,
                        row_label=row,
                        seat_number=s,
                        label=label,
                        tier_id=tier.id,
                        svg_x=X_ORIGIN + (s - 1) * SEAT_DX,
                        svg_y=Y_ORIGIN + ri * SEAT_DY,
                        status="available",
                    )
                )
                n += 1

        db.commit()
        print(f"Seeded {len(TIERS)} tiers and {n} seats.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
