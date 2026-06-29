"""Seat-map data endpoint: serves the hall layout as JSON for the SVG renderer.

Coordinates are emitted in pixels (spreadsheet column/row * CELL), so the frontend
can draw the map without knowing anything about the source Excel.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from openpyxl.utils import range_boundaries
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import PriceTier, Seat

router = APIRouter(prefix="/api", tags=["seatmap"])

CELL = 24          # pixels per spreadsheet cell
SEAT = 20          # seat square size
PAD = CELL * 2     # viewBox padding

# Architecture, taken from the labeled merged ranges on the 'Sơ đồ' tab.
STAGE = ("AA36:AM41", "SÂN KHẤU")
DOORS = [
    "W5", "AN5", "K16:K17", "BC16:BC17", "K30:K31", "BC30:BC31",
    "B37:B39", "F37:F39", "K37:K39", "BC37:BC39", "BH37:BH39", "BL37:BL39",
]
WALLS = ["AA5", "M14:M18", "BA14:BA18"]
COLUMNS = ["U15:V15", "AR15:AS15", "F33:F34", "BH33:BH34"]


def _rect(ref: str) -> dict:
    """Convert an A1[:B2] reference into a pixel rectangle."""
    min_c, min_r, max_c, max_r = range_boundaries(ref)
    return {
        "x": min_c * CELL,
        "y": min_r * CELL,
        "w": (max_c - min_c + 1) * CELL,
        "h": (max_r - min_r + 1) * CELL,
    }


@router.get("/seatmap")
def seatmap(db: Session = Depends(get_db)) -> dict:
    tiers = db.execute(select(PriceTier).order_by(PriceTier.price_vnd.desc())).scalars().all()
    seats = db.execute(select(Seat)).scalars().all()

    seat_dicts = []
    xs, ys = [], []
    # Collect one row-label marker per (section, row) at the center aisle.
    row_markers: dict[tuple[str, str], dict] = {}
    AF_X = 32 * CELL  # center label column

    for s in seats:
        px = s.svg_x * CELL
        py = s.svg_y * CELL
        xs.append(px)
        ys.append(py)
        seat_dicts.append(
            {
                "id": s.id,
                "section": s.section,
                "row": s.row_label,
                "num": s.seat_number,
                "label": s.label,
                "tier_id": s.tier_id,
                "x": px,
                "y": py,
                "status": s.status,
            }
        )
        # Center-aisle row markers: only for genuine horizontal rows in the center
        # blocks. The Tầng 3 side columns use row_label "L"/"R" (vertical strips) and
        # must NOT get a center marker, or they overlap the Tầng 1 letter rows.
        is_center_row = len(s.row_label) == 1 and (
            s.section == "Tầng 1"
            or (s.section == "Tầng 3" and s.row_label not in ("L", "R"))
        )
        if is_center_row:
            row_markers.setdefault(
                (s.section, s.row_label), {"label": s.row_label, "x": AF_X, "y": py}
            )

    architecture = []
    for ref in DOORS:
        architecture.append({"type": "door", "label": "Cửa", **_rect(ref)})
    for ref in WALLS:
        architecture.append({"type": "wall", "label": "Tường", **_rect(ref)})
    for ref in COLUMNS:
        architecture.append({"type": "column", "label": "Cột", **_rect(ref)})

    stage = {**_rect(STAGE[0]), "label": STAGE[1]}

    # viewBox bounds across seats + stage + architecture.
    all_x = xs + [stage["x"], stage["x"] + stage["w"]]
    all_y = ys + [stage["y"], stage["y"] + stage["h"]]
    for a in architecture:
        all_x += [a["x"], a["x"] + a["w"]]
        all_y += [a["y"], a["y"] + a["h"]]
    min_x, max_x = min(all_x) - PAD, max(all_x) + PAD + SEAT
    min_y, max_y = min(all_y) - PAD, max(all_y) + PAD + SEAT

    return {
        "viewBox": f"{min_x} {min_y} {max_x - min_x} {max_y - min_y}",
        "seat": SEAT,
        "maxPerOrder": settings.max_seats_per_order,
        "tiers": [
            {"id": t.id, "name": t.name, "color": t.color_hex, "price": t.price_vnd}
            for t in tiers
        ],
        "seats": seat_dicts,
        "rowMarkers": list(row_markers.values()),
        "architecture": architecture,
        "stage": stage,
    }
