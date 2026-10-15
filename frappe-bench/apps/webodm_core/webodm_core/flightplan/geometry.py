"""Planar geometry in the local metric frame (see ``geo.LocalFrame``).

Points are ``(x, y)`` tuples in metres. Everything a survey grid or corridor
needs is here and nothing more: ring area and validity, the sweep direction
that needs the fewest flight lines, scan-line clipping (even-odd, so holes
and concave boundaries work), polyline offsetting and profile simplification.
"""

from __future__ import annotations

import math

Point = tuple[float, float]

EPS = 1e-9


def dist(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def polyline_length(points: list[Point]) -> float:
    return sum(dist(points[i], points[i + 1]) for i in range(len(points) - 1))


def open_ring(ring: list[Point]) -> list[Point]:
    """Ring without the repeated closing vertex and without consecutive duplicates."""
    out: list[Point] = []
    for p in ring:
        if not out or dist(out[-1], p) > 1e-6:
            out.append((float(p[0]), float(p[1])))
    if len(out) > 1 and dist(out[0], out[-1]) <= 1e-6:
        out.pop()
    return out


def signed_area(ring: list[Point]) -> float:
    """Shoelace area; positive for counter-clockwise rings."""
    s = 0.0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def polygon_area(rings: list[list[Point]]) -> float:
    """Area of a polygon given as exterior ring + holes."""
    if not rings:
        return 0.0
    return abs(signed_area(rings[0])) - sum(abs(signed_area(r)) for r in rings[1:])


def centroid(ring: list[Point]) -> Point:
    a = signed_area(ring)
    if abs(a) < EPS:
        n = len(ring) or 1
        return sum(p[0] for p in ring) / n, sum(p[1] for p in ring) / n
    cx = cy = 0.0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        f = x1 * y2 - x2 * y1
        cx += (x1 + x2) * f
        cy += (y1 + y2) * f
    return cx / (6 * a), cy / (6 * a)


def bbox(points: list[Point]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def rotate(points: list[Point], angle_rad: float, origin: Point = (0.0, 0.0)) -> list[Point]:
    c, s = math.cos(angle_rad), math.sin(angle_rad)
    ox, oy = origin
    return [(ox + (x - ox) * c - (y - oy) * s, oy + (x - ox) * s + (y - oy) * c) for x, y in points]


def _orient(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def segments_cross(p1: Point, p2: Point, p3: Point, p4: Point) -> bool:
    """True when the open segments p1p2 and p3p4 properly intersect (or overlap)."""
    d1 = _orient(p3, p4, p1)
    d2 = _orient(p3, p4, p2)
    d3 = _orient(p1, p2, p3)
    d4 = _orient(p1, p2, p4)
    if ((d1 > EPS and d2 < -EPS) or (d1 < -EPS and d2 > EPS)) and \
            ((d3 > EPS and d4 < -EPS) or (d3 < -EPS and d4 > EPS)):
        return True

    def on(a, b, c):  # c collinear with ab and strictly inside it
        return (min(a[0], b[0]) - EPS <= c[0] <= max(a[0], b[0]) + EPS
                and min(a[1], b[1]) - EPS <= c[1] <= max(a[1], b[1]) + EPS
                and dist(a, c) > 1e-6 and dist(b, c) > 1e-6)

    if abs(d1) <= EPS and on(p3, p4, p1):
        return True
    if abs(d2) <= EPS and on(p3, p4, p2):
        return True
    if abs(d3) <= EPS and on(p1, p2, p3):
        return True
    if abs(d4) <= EPS and on(p1, p2, p4):
        return True
    return False


def ring_self_intersects(ring: list[Point]) -> bool:
    n = len(ring)
    for i in range(n):
        a1, a2 = ring[i], ring[(i + 1) % n]
        for j in range(i + 1, n):
            if j == i or (j + 1) % n == i or (i + 1) % n == j:
                continue  # adjacent edges share a vertex
            if segments_cross(a1, a2, ring[j], ring[(j + 1) % n]):
                return True
    return False


def polyline_self_intersects(points: list[Point]) -> bool:
    n = len(points) - 1
    for i in range(n):
        for j in range(i + 2, n):
            if segments_cross(points[i], points[i + 1], points[j], points[j + 1]):
                return True
    return False


def point_in_rings(p: Point, rings: list[list[Point]]) -> bool:
    """Even-odd containment test against exterior + holes."""
    x, y = p
    inside = False
    for ring in rings:
        n = len(ring)
        for i in range(n):
            x1, y1 = ring[i]
            x2, y2 = ring[(i + 1) % n]
            if (y1 > y) != (y2 > y):
                if x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                    inside = not inside
    return inside


def convex_hull(points: list[Point]) -> list[Point]:
    """Andrew's monotone chain; counter-clockwise, no repeated closing point."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts
    lower: list[Point] = []
    for p in pts:
        while len(lower) >= 2 and _orient(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[Point] = []
    for p in reversed(pts):
        while len(upper) >= 2 and _orient(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def min_width_direction(points: list[Point]) -> tuple[float, float]:
    """``(angle_rad, width)`` of the direction along which the shape is narrowest across.

    Flight lines flown parallel to the returned direction need the fewest
    passes: the minimum width of a convex shape is always attained
    perpendicular to one of its hull edges, so only those are tested.
    The angle is a mathematical angle (from +x, counter-clockwise) in [0, π).
    """
    hull = convex_hull(points)
    if len(hull) < 3:
        if len(hull) == 2:
            return math.atan2(hull[1][1] - hull[0][1], hull[1][0] - hull[0][0]) % math.pi, 0.0
        return 0.0, 0.0
    best = (0.0, float("inf"))
    n = len(hull)
    for i in range(n):
        a, b = hull[i], hull[(i + 1) % n]
        length = dist(a, b)
        if length < 1e-9:
            continue
        ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
        width = max(abs((p[0] - a[0]) * -uy + (p[1] - a[1]) * ux) for p in hull)
        if width < best[1] - 1e-9:
            best = (math.atan2(uy, ux) % math.pi, width)
    return best


def scanline_segments(rings: list[list[Point]], y: float) -> list[tuple[float, float]]:
    """Intervals ``[(x_in, x_out), ...]`` where the horizontal line at ``y`` is inside the polygon.

    Even-odd rule over every ring, so holes cut gaps and concave outlines give
    several intervals. Edges are treated as half-open in y, which makes a scan
    line through a vertex count that vertex exactly once.
    """
    xs: list[float] = []
    for ring in rings:
        n = len(ring)
        for i in range(n):
            x1, y1 = ring[i]
            x2, y2 = ring[(i + 1) % n]
            if (y1 <= y < y2) or (y2 <= y < y1):
                xs.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
    xs.sort()
    return [(xs[i], xs[i + 1]) for i in range(0, len(xs) - 1, 2) if xs[i + 1] - xs[i] > 1e-6]


def offset_polyline(points: list[Point], offset: float, miter_limit: float = 2.0) -> list[Point]:
    """Polyline displaced ``offset`` metres to the left of its direction of travel.

    Joins are mitred; a join sharper than ``miter_limit`` is bevelled (two
    points) so a hairpin does not throw a spike out to infinity. On the inside
    of a bend tighter than the offset the result would fold back on itself;
    those backward runs are removed so the offset line always advances with
    the centre line.
    """
    pts = _dedupe(points)
    if len(pts) < 2:
        return list(pts)
    if abs(offset) < 1e-9:
        return list(pts)

    def normal(a: Point, b: Point) -> Point:
        d = dist(a, b)
        return (-(b[1] - a[1]) / d, (b[0] - a[0]) / d)

    normals = [normal(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    out: list[Point] = [(pts[0][0] + normals[0][0] * offset, pts[0][1] + normals[0][1] * offset)]
    src: list[int] = [0]  # index of the source segment each output point starts
    for i in range(1, len(pts) - 1):
        n1, n2 = normals[i - 1], normals[i]
        mx, my = n1[0] + n2[0], n1[1] + n2[1]
        ml = math.hypot(mx, my)
        cos_half = ml / 2.0  # cos of half the turning angle
        if ml < 1e-9 or 1.0 / max(cos_half, 1e-9) > miter_limit:
            out.append((pts[i][0] + n1[0] * offset, pts[i][1] + n1[1] * offset))
            src.append(i)
            out.append((pts[i][0] + n2[0] * offset, pts[i][1] + n2[1] * offset))
            src.append(i)
        else:
            k = offset / (cos_half * ml)
            out.append((pts[i][0] + mx * k, pts[i][1] + my * k))
            src.append(i)
    out.append((pts[-1][0] + normals[-1][0] * offset, pts[-1][1] + normals[-1][1] * offset))
    src.append(len(pts) - 2)
    return _drop_backtracking(out, src, pts)


def _dedupe(points: list[Point]) -> list[Point]:
    out: list[Point] = []
    for p in points:
        if not out or dist(out[-1], p) > 1e-6:
            out.append((float(p[0]), float(p[1])))
    return out


def _drop_backtracking(out: list[Point], src: list[int], centre: list[Point]) -> list[Point]:
    """Remove offset vertices whose leg runs against the centre line's direction."""
    changed = True
    guard = 0
    while changed and len(out) > 2 and guard < len(out) + 5:
        changed = False
        guard += 1
        for i in range(len(out) - 1):
            seg = min(src[i], len(centre) - 2)
            cx, cy = centre[seg + 1][0] - centre[seg][0], centre[seg + 1][1] - centre[seg][1]
            ox, oy = out[i + 1][0] - out[i][0], out[i + 1][1] - out[i][1]
            if ox * cx + oy * cy < -1e-9:
                # Drop the interior endpoint of the reversed leg, never an end of the line.
                drop = i + 1 if i + 1 < len(out) - 1 else i
                if drop == 0:
                    continue
                del out[drop]
                del src[drop]
                changed = True
                break
    return _dedupe(out)


def resample_polyline(points: list[Point], step: float) -> list[tuple[Point, float]]:
    """``[(point, distance_along), ...]`` every ``step`` metres, always including both ends and every vertex."""
    out: list[tuple[Point, float]] = []
    travelled = 0.0
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        seg = dist(a, b)
        if seg < 1e-9:
            continue
        n = max(1, int(math.ceil(seg / step - 1e-9)))
        for k in range(n):
            t = k / n
            out.append(((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t), travelled + seg * t))
        travelled += seg
    if points:
        out.append((points[-1], travelled))
    return out


def simplify_profile(samples: list[tuple[float, float]], tolerance: float) -> list[int]:
    """Indices of the samples a Douglas-Peucker pass keeps, for ``(distance, height)`` pairs.

    The deviation measured is *vertical* (height at the same distance), not
    perpendicular: what matters for terrain following is how far the straight
    leg between two kept waypoints strays from the wanted height.
    """
    n = len(samples)
    if n <= 2:
        return list(range(n))
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        lo, hi = stack.pop()
        if hi - lo < 2:
            continue
        d0, h0 = samples[lo]
        d1, h1 = samples[hi]
        span = d1 - d0
        worst, worst_i = 0.0, -1
        for i in range(lo + 1, hi):
            d, h = samples[i]
            interp = h0 + (h1 - h0) * ((d - d0) / span) if span > 1e-9 else h0
            dev = abs(h - interp)
            if dev > worst:
                worst, worst_i = dev, i
        if worst > tolerance and worst_i >= 0:
            keep[worst_i] = True
            stack.append((lo, worst_i))
            stack.append((worst_i, hi))
    return [i for i, k in enumerate(keep) if k]


def interpolate_along(a: Point, b: Point, t: float) -> Point:
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
