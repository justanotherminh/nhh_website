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


def _trace(cells: set[tuple[int, int]]) -> list[tuple[str, set[tuple[int, int]]]]:
    """Outline cells as (svg_path, cells) per connected block."""
    return [(_outline(blob), blob) for blob in _components(cells)]


def _outline(cells: set[tuple[int, int]]) -> str:
    """Trace one block's silhouette.

    Walks the cell borders that have no neighbour on the far side; interior
    borders cancel out, so what remains is the outline. A block with a hole
    yields several subpaths, which fill-rule: evenodd renders correctly.
    """
    edges: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for cx, cy in cells:
        x0, y0 = cx * CELL, cy * CELL
        x1, y1 = x0 + CELL, y0 + CELL
        # Clockwise, so each piece traces in a consistent direction.
        if (cx, cy - 1) not in cells: edges.setdefault((x0, y0), []).append((x1, y0))
        if (cx + 1, cy) not in cells: edges.setdefault((x1, y0), []).append((x1, y1))
        if (cx, cy + 1) not in cells: edges.setdefault((x1, y1), []).append((x0, y1))
        if (cx - 1, cy) not in cells: edges.setdefault((x0, y1), []).append((x0, y0))

    paths = []
    while edges:
        start = next(iter(edges))
        pts, cur = [start], start
        while True:
            nxts = edges.get(cur)
            if not nxts:
                break
            nxt = nxts.pop()
            if not nxts:
                del edges[cur]
            pts.append(nxt)
            cur = nxt
            if cur == start:
                break
        # Drop collinear midpoints so the path is a clean rectilinear outline.
        keep = [
            p for i, p in enumerate(pts)
            if not (0 < i < len(pts) - 1
                    and (p[0] - pts[i - 1][0]) * (pts[i + 1][1] - p[1])
                    == (p[1] - pts[i - 1][1]) * (pts[i + 1][0] - p[0]))
        ]
        if len(keep) >= 4:
            paths.append("M" + " L".join(f"{x},{y}" for x, y in keep) + " Z")
    return " ".join(paths)


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

    # Floor blocks: a shaded panel behind each floor's seats, named in place. The
    # floors interleave in plan view, so a bounding box per floor would just nest
    # them — the shape is traced from the seats instead.
    cells_by_floor: dict[str, set[tuple[int, int]]] = {}
    for s in seats:
        cells_by_floor.setdefault(s.section, set()).add((int(s.svg_x), int(s.svg_y)))

    floor_regions = []
    for floor, cells in sorted(cells_by_floor.items()):
        # Extend one row upward to open a clear band for the floor name — the panel
        # otherwise hugs the seats with nowhere free to put text. Only upward:
        # padding sideways too would close the narrow gaps between the side strips
        # and merge neighbouring floors into one grey mass.
        closed = _close(cells)
        panel = closed | {(x, y - 1) for (x, y) in closed}
        for d, cells_in in _trace(panel):
            top = min(c[1] for c in cells_in)
            top_xs = [c[0] for c in cells_in if c[1] == top]
            floor_regions.append({
                "floor": floor,
                "d": d,
                "cx": (min(top_xs) * CELL + max(top_xs) * CELL + CELL) / 2,
                "cy": top * CELL + CELL / 2,
            })

    # viewBox bounds across seats + stage + architecture + floor labels.
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
