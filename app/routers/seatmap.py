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

# The three floors overlap in plan view: Tầng 3 wraps the sides, Tầng 2 sits at
# the back, Tầng 1 is the stalls. Each is drawn as a shaded block behind its
# seats and named in place — see the floor blocks built in build_seatmap.


def _close(cells: set[tuple[int, int]], r: int = 2) -> set[tuple[int, int]]:
    """Morphological closing (dilate then erode) with a (2r+1)-square element.

    Bridges the aisles inside a floor — including the centre aisle that carries
    the row letters — so each floor reads as one block per seating area rather
    than fragmenting. Erosion restores the outer extent, so the block still hugs
    the seats.
    """
    span = range(-r, r + 1)
    nb = lambda x, y: [(x + dx, y + dy) for dx in span for dy in span]
    grown = {c for (x, y) in cells for c in nb(x, y)}
    return {(x, y) for (x, y) in grown if all(c in grown for c in nb(x, y))}


def _components(cells: set[tuple[int, int]]) -> list[set[tuple[int, int]]]:
    """Split cells into connected blocks (4-connectivity)."""
    todo, out = set(cells), []
    while todo:
        stack = [todo.pop()]
        blob = set(stack)
        while stack:
            x, y = stack.pop()
            for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if n in todo:
                    todo.discard(n)
                    blob.add(n)
                    stack.append(n)
        out.append(blob)
    return out


LABEL_CELLS = 3    # cells a "Tầng N" label needs at its rendered size


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
    return build_seatmap(db)


def build_seatmap(db: Session) -> dict:
    """Assemble the hall layout JSON (geometry, tiers, seats, architecture).

    Shared by the public seat-map endpoint and the admin invitation map.
    """
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

    # Floor blocks: a shaded rectangle behind each floor's seats, named in place.
    # Boxed per seating block rather than per floor — the floors interleave in
    # plan view, so one box per floor would just nest them.
    cells_by_floor: dict[str, set[tuple[int, int]]] = {}
    for s in seats:
        cells_by_floor.setdefault(s.section, set()).add((int(s.svg_x), int(s.svg_y)))

    hall_cx = (min(int(s.svg_x) for s in seats) + max(int(s.svg_x) for s in seats)) / 2

    boxes = []
    for floor, cells in sorted(cells_by_floor.items()):
        for blob in _components(_close(cells)):
            cxs = [x for x, _ in blob]
            cys = [y for _, y in blob]
            # One row of headroom on top to sit the floor name in.
            boxes.append({"floor": floor, "c0": min(cxs), "r0": min(cys) - 1,
                          "c1": max(cxs), "r1": max(cys)})

    # The stalls box takes in the stage and the walls/doors flanking it, which
    # otherwise hang outside the box. It's the block sitting closest above the
    # stage and overlapping it horizontally.
    sc0, sr0, sc1, sr1 = range_boundaries(STAGE[0])
    above = [b for b in boxes if b["c0"] <= sc1 and b["c1"] >= sc0 and b["r1"] < sr0]
    if above:
        stalls = max(above, key=lambda b: b["r1"])
        # Architecture counts as the stalls' own if it sits within a few cells of
        # the block's sides and between its top row and the foot of the stage.
        lo, hi = stalls["c0"] - 3, stalls["c1"] + 3
        stalls["c1"], stalls["r1"] = max(stalls["c1"], sc1), max(stalls["r1"], sr1)
        stalls["c0"] = min(stalls["c0"], sc0)
        for ref in DOORS + WALLS + COLUMNS:
            a0, b0, a1, b1 = range_boundaries(ref)
            if lo <= a0 and a1 <= hi and b0 >= stalls["r0"] and b1 <= sr1:
                stalls["c0"] = min(stalls["c0"], a0)
                stalls["c1"] = max(stalls["c1"], a1)
                stalls["r1"] = max(stalls["r1"], b1)

    # The rear blocks sit right under a row of doors and a wall, which would land
    # in the header band on top of the floor name. Lift the band clear of them.
    arch_ranges = [range_boundaries(ref) for ref in DOORS + WALLS + COLUMNS]
    for b in boxes:
        for _ in range(4):
            clash = any(
                a0 <= b["c1"] and a1 >= b["c0"] and b0 <= b["r0"] <= b1
                for a0, b0, a1, b1 in arch_ranges
            )
            if not clash:
                break
            b["r0"] -= 1

    # Narrow side strips are thinner than their own label — widen them away from
    # the middle of the hall, into the empty margin, so the name fits inside.
    for b in boxes:
        need = LABEL_CELLS - (b["c1"] - b["c0"] + 1)
        if need > 0:
            if (b["c0"] + b["c1"]) / 2 < hall_cx:
                b["c0"] -= need
            else:
                b["c1"] += need

    floor_regions = [
        {
            "floor": b["floor"],
            "x": b["c0"] * CELL,
            "y": b["r0"] * CELL,
            "w": (b["c1"] - b["c0"] + 1) * CELL,
            "h": (b["r1"] - b["r0"] + 1) * CELL,
            "cx": (b["c0"] + b["c1"] + 1) * CELL / 2,
            "cy": b["r0"] * CELL + CELL / 2,
        }
        for b in boxes
    ]

    # viewBox bounds across seats + stage + architecture + floor boxes.
    all_x = xs + [stage["x"], stage["x"] + stage["w"]]
    all_y = ys + [stage["y"], stage["y"] + stage["h"]]
    for a in architecture + floor_regions:
        all_x += [a["x"], a["x"] + a["w"]]
        all_y += [a["y"], a["y"] + a["h"]]
    min_x, max_x = min(all_x) - PAD, max(all_x) + PAD + SEAT
    min_y, max_y = min(all_y) - PAD, max(all_y) + PAD + SEAT

    return {
        "viewBox": f"{min_x} {min_y} {max_x - min_x} {max_y - min_y}",
        "seat": SEAT,
        "maxPerOrder": settings.max_seats_per_order,
        # tiers come priciest-first; rank 0 = cheapest .. n-1 = priciest. The
        # front-end colours seats by rank (see .seat-g.tier-r* in styles.css).
        "tiers": [
            {"id": t.id, "name": t.name, "price": t.price_vnd, "rank": len(tiers) - 1 - i}
            for i, t in enumerate(tiers)
        ],
        "seats": seat_dicts,
        "rowMarkers": list(row_markers.values()),
        "architecture": architecture,
        "floorRegions": floor_regions,
        "stage": stage,
    }
