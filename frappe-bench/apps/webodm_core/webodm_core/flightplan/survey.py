"""Flight-line layout: where the aircraft flies to photograph an area or a corridor.

Works entirely in the local metric frame. The result is a list of ``Leg``s
in flight order; a leg is one continuous photographed run (a flight line, a
piece of one where the boundary is concave, or a corridor pass). The
straight hops between consecutive legs are the turns.

Altitude, timing and export know nothing about how the legs were laid out,
so a new mission type is a new function here that returns legs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import geometry as g
from .geometry import Point

START_CHOICES = ("auto", "0", "1", "2", "3")


@dataclass
class Leg:
    points: list[Point]    # polyline flown with the camera triggering
    line: int              # flight line / corridor pass this leg belongs to

    @property
    def length(self) -> float:
        return g.polyline_length(self.points)


@dataclass
class Layout:
    legs: list[Leg]
    line_count: int
    direction_deg: float | None        # compass bearing of the flight lines (None for a corridor)
    spacing_m: float
    target: list[list[Point]] = field(default_factory=list)   # rings of the area the mission covers
    passes: int | None = None          # corridor: parallel passes

    @property
    def photo_length(self) -> float:
        return sum(leg.length for leg in self.legs)


class LayoutError(ValueError):
    """The geometry cannot be turned into flight lines (the message says why)."""


def bearing_to_angle(bearing_deg: float) -> float:
    """Mathematical angle (rad, from +x counter-clockwise) of a compass bearing."""
    return math.radians(90.0 - bearing_deg)


def angle_to_bearing(angle_rad: float) -> float:
    """Compass bearing in [0, 180) of an undirected line with the given mathematical angle."""
    return (90.0 - math.degrees(angle_rad)) % 180.0


def optimal_direction_deg(exterior: list[Point]) -> float:
    """Bearing of the flight lines that covers the area in the fewest passes."""
    angle, _width = g.min_width_direction(exterior)
    return angle_to_bearing(angle)


def _extend(a: Point, b: Point, amount: float) -> Point:
    """``b`` pushed ``amount`` metres further along a→b."""
    d = g.dist(a, b)
    if d < 1e-9 or amount <= 0:
        return b
    return b[0] + (b[0] - a[0]) / d * amount, b[1] + (b[1] - a[1]) / d * amount


def _merge(segments: list[tuple[float, float]], gap: float) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for a, b in segments:
        if out and a - out[-1][1] <= gap:
            out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


def _grid_rows(rings: list[list[Point]], angle: float, spacing: float, merge_gap: float,
               overshoot: float) -> list[list[tuple[Point, Point]]]:
    """Rows of ``(start, end)`` segments in the *rotated* frame, each row sorted by x."""
    rotated = [g.rotate(r, -angle) for r in rings]
    _xmin, ymin, _xmax, ymax = g.bbox(rotated[0])
    height = ymax - ymin
    if height <= 0:
        raise LayoutError("the survey area has no extent across the flight direction")
    count = max(1, int(math.ceil(height / spacing - 1e-9)))
    first = ymin + (height - (count - 1) * spacing) / 2.0
    rows: list[list[tuple[Point, Point]]] = []
    for k in range(count):
        y = first + k * spacing
        segments = _merge(g.scanline_segments(rotated, y), merge_gap)
        row = [((a - overshoot, y), (b + overshoot, y)) for a, b in segments]
        if row:
            rows.append(row)
    return rows


def _order_rows(rows: list[list[tuple[Point, Point]]], corner: int) -> list[list[tuple[Point, Point]]]:
    """Boustrophedon order starting from one of the four corners.

    ``corner`` bit 0 = start on the last row instead of the first, bit 1 =
    fly the first row towards -x instead of +x. Each returned row lists its
    segments in the order and direction flown.
    """
    ordered = list(reversed(rows)) if corner & 1 else list(rows)
    out = []
    forward = not (corner & 2)
    for row in ordered:
        if forward:
            out.append(list(row))
        else:
            out.append([(b, a) for a, b in reversed(row)])
        forward = not forward
    return out


def _pick_corner(candidates: list[list[list[tuple[Point, Point]]]], near: Point | None, start: str) -> int:
    if start in ("0", "1", "2", "3"):
        return int(start)
    if near is None:
        return 0
    best, best_d = 0, float("inf")
    for i, rows in enumerate(candidates):
        d = g.dist(near, rows[0][0][0])
        if d < best_d - 1e-6:
            best, best_d = i, d
    return best


def area_layout(rings: list[list[Point]], spacing: float, *, direction_deg: float | None = None,
                overshoot: float = 0.0, merge_gap: float = 0.0, start: str = "auto",
                near: Point | None = None, crosshatch: bool = False) -> Layout:
    """Parallel flight lines across a polygon (exterior ring + optional holes).

    ``direction_deg`` is the compass bearing of the lines; ``None`` picks the
    one needing the fewest lines. ``merge_gap`` bridges short gaps where a
    line leaves and re-enters a concave boundary, so the aircraft does not
    stop for a gap it would cross in a couple of photos. ``near`` (usually
    the takeoff point) decides which corner the mission starts from when
    ``start`` is ``auto``.
    """
    if spacing <= 0:
        raise LayoutError("line spacing must be positive")
    if not rings or len(rings[0]) < 3:
        raise LayoutError("the survey area needs at least three corners")
    bearing = optimal_direction_deg(rings[0]) if direction_deg is None else float(direction_deg) % 180.0

    legs: list[Leg] = []
    line_no = 0
    passes = [bearing, (bearing + 90.0) % 180.0] if crosshatch else [bearing]
    for i, b in enumerate(passes):
        angle = bearing_to_angle(b)
        rows = _grid_rows(rings, angle, spacing, merge_gap, overshoot)
        if not rows:
            raise LayoutError("no flight line crosses the survey area")
        candidates = [_order_rows(rows, c) for c in range(4)]
        # Candidates are in the rotated frame, so the anchor goes there too. The
        # second grid of a crosshatch starts wherever the first one ended.
        if i == 0:
            anchor = g.rotate([near], -angle)[0] if near is not None else None
        else:
            anchor = g.rotate([legs[-1].points[-1]], -angle)[0]
        corner = _pick_corner(candidates, anchor, start if i == 0 else "auto")
        for row in candidates[corner]:
            for a, b_pt in row:
                pts = g.rotate([a, b_pt], angle)
                legs.append(Leg(points=pts, line=line_no))
            line_no += 1
    return Layout(legs=legs, line_count=line_no, direction_deg=bearing, spacing_m=spacing,
                  target=[list(r) for r in rings])


def corridor_passes(width: float, across: float, spacing: float, min_passes: int = 1) -> int:
    """Parallel passes needed for a corridor ``width`` wide with a swath ``across`` wide."""
    needed = 1 if width <= across else int(math.ceil((width - across) / spacing - 1e-9)) + 1
    return max(1, int(min_passes), needed)


def corridor_layout(centerline: list[Point], width: float, spacing: float, across: float, *,
                    min_passes: int = 1, overshoot: float = 0.0, start: str = "auto",
                    near: Point | None = None) -> Layout:
    """Passes parallel to a centre line, covering ``width`` metres across it.

    The passes are offset curves of the centre line, spaced ``spacing`` apart
    and centred on it, flown alternately out and back.
    """
    if spacing <= 0:
        raise LayoutError("line spacing must be positive")
    if width <= 0:
        raise LayoutError("corridor width must be positive")
    line = g._dedupe(centerline)
    if len(line) < 2:
        raise LayoutError("the corridor needs at least two points")
    count = corridor_passes(width, across, spacing, min_passes)
    offsets = [(k - (count - 1) / 2.0) * spacing for k in range(count)]
    curves = []
    for off in offsets:
        curve = g.offset_polyline(line, off)
        if len(curve) < 2:
            raise LayoutError("the corridor is too tightly curved for its width")
        if overshoot > 0:
            curve = [_extend(curve[1], curve[0], overshoot), *curve[1:-1], _extend(curve[-2], curve[-1], overshoot)]
        curves.append(curve)

    def order(corner: int) -> list[list[Point]]:
        seq = list(reversed(curves)) if corner & 1 else list(curves)
        forward = not (corner & 2)
        out = []
        for c in seq:
            out.append(list(c) if forward else list(reversed(c)))
            forward = not forward
        return out

    candidates = [order(c) for c in range(4)]
    if start in ("0", "1", "2", "3"):
        corner = int(start)
    elif near is None:
        corner = 0
    else:
        corner = min(range(4), key=lambda c: (round(g.dist(near, candidates[c][0][0]), 6), c))
    legs = [Leg(points=pts, line=i) for i, pts in enumerate(candidates[corner])]

    half = width / 2.0
    left = g.offset_polyline(line, half)
    right = g.offset_polyline(line, -half)
    target = [left + list(reversed(right))] if len(left) >= 2 and len(right) >= 2 else []
    return Layout(legs=legs, line_count=count, direction_deg=None, spacing_m=spacing, target=target, passes=count)
