"""Geodesy for flight planning: a local metric frame around the survey site.

Flight lines are laid out in metres (line spacing, photo spacing, corridor
width), so every plan is computed in a local projected frame and converted
back to WGS84 for display and export. The frame is a transverse Mercator
projection centred on the site (scale factor 1 at the origin): over the
tens of kilometres a drone mission can span, distances are true to a few
parts per million, which a tangent-plane approximation is not at high
latitudes.

No dependencies: the Frappe image carries no geospatial libraries, and the
handful of formulas here (Snyder, *Map Projections: A Working Manual*,
eqs. 8-9 ... 8-25) do not justify adding one.
"""

from __future__ import annotations

import math

# WGS84
A = 6378137.0
F = 1 / 298.257223563
E2 = F * (2 - F)
EP2 = E2 / (1 - E2)
_E4 = E2 * E2
_E6 = _E4 * E2
_M1 = 1 - E2 / 4 - 3 * _E4 / 64 - 5 * _E6 / 256
_M2 = 3 * E2 / 8 + 3 * _E4 / 32 + 45 * _E6 / 1024
_M3 = 15 * _E4 / 256 + 45 * _E6 / 1024
_M4 = 35 * _E6 / 3072
_E1 = (1 - math.sqrt(1 - E2)) / (1 + math.sqrt(1 - E2))


def _meridian_arc(phi: float) -> float:
    return A * (_M1 * phi - _M2 * math.sin(2 * phi) + _M3 * math.sin(4 * phi) - _M4 * math.sin(6 * phi))


def normalize_lng(lng: float) -> float:
    """Longitude wrapped to [-180, 180)."""
    return (lng + 180.0) % 360.0 - 180.0


class LocalFrame:
    """Transverse Mercator frame centred on ``(lat0, lng0)``; x east, y north, metres."""

    def __init__(self, lat0: float, lng0: float):
        if not (-89.0 <= lat0 <= 89.0):
            raise ValueError("flight planning is not supported within 1° of the poles")
        self.lat0 = float(lat0)
        self.lng0 = normalize_lng(float(lng0))
        self._m0 = _meridian_arc(math.radians(self.lat0))

    @classmethod
    def around(cls, points) -> "LocalFrame":
        """Frame centred on the bounding box of ``[(lng, lat), ...]`` (dateline-aware)."""
        pts = list(points)
        if not pts:
            raise ValueError("no points to centre a frame on")
        lats = [p[1] for p in pts]
        ref = pts[0][0]
        # Unwrap longitudes around the first point so a site straddling the
        # antimeridian is centred on itself, not on Greenwich.
        offsets = [normalize_lng(p[0] - ref) for p in pts]
        return cls((min(lats) + max(lats)) / 2, ref + (min(offsets) + max(offsets)) / 2)

    def forward(self, lng: float, lat: float) -> tuple[float, float]:
        phi = math.radians(lat)
        dlam = math.radians(normalize_lng(lng - self.lng0))
        s, c, t = math.sin(phi), math.cos(phi), math.tan(phi)
        n = A / math.sqrt(1 - E2 * s * s)
        t2 = t * t
        cc = EP2 * c * c
        a = dlam * c
        a2 = a * a
        x = n * (a + (1 - t2 + cc) * a2 * a / 6
                 + (5 - 18 * t2 + t2 * t2 + 72 * cc - 58 * EP2) * a2 * a2 * a / 120)
        y = (_meridian_arc(phi) - self._m0
             + n * t * (a2 / 2 + (5 - t2 + 9 * cc + 4 * cc * cc) * a2 * a2 / 24
                        + (61 - 58 * t2 + t2 * t2 + 600 * cc - 330 * EP2) * a2 * a2 * a2 / 720))
        return x, y

    def inverse(self, x: float, y: float) -> tuple[float, float]:
        mu = (self._m0 + y) / (A * _M1)
        e1 = _E1
        phi1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
                + (21 * e1 * e1 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
                + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
                + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
        s, c, t = math.sin(phi1), math.cos(phi1), math.tan(phi1)
        c1 = EP2 * c * c
        t1 = t * t
        w = 1 - E2 * s * s
        n1 = A / math.sqrt(w)
        r1 = A * (1 - E2) / (w ** 1.5)
        d = x / n1
        d2 = d * d
        phi = phi1 - (n1 * t / r1) * (
            d2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1 * c1 - 9 * EP2) * d2 * d2 / 24
            + (61 + 90 * t1 + 298 * c1 + 45 * t1 * t1 - 252 * EP2 - 3 * c1 * c1) * d2 * d2 * d2 / 720)
        lam = (d - (1 + 2 * t1 + c1) * d2 * d / 6
               + (5 - 2 * c1 + 28 * t1 - 3 * c1 * c1 + 8 * EP2 + 24 * t1 * t1) * d2 * d2 * d / 120) / c
        return normalize_lng(self.lng0 + math.degrees(lam)), math.degrees(phi)

    def forward_all(self, lnglats) -> list[tuple[float, float]]:
        return [self.forward(p[0], p[1]) for p in lnglats]

    def inverse_all(self, xys) -> list[tuple[float, float]]:
        return [self.inverse(p[0], p[1]) for p in xys]


def haversine_m(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    """Great-circle distance in metres (mean Earth radius); for coarse checks only."""
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlam = math.radians(normalize_lng(lng2 - lng1))
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def bearing_deg(dx: float, dy: float) -> float:
    """Compass bearing (0 = north, clockwise) of a local-frame vector."""
    return math.degrees(math.atan2(dx, dy)) % 360.0


def valid_lnglat(lng, lat) -> bool:
    try:
        lng, lat = float(lng), float(lat)
    except (TypeError, ValueError):
        return False
    return math.isfinite(lng) and math.isfinite(lat) and -180.0 <= lng <= 180.0 and -90.0 <= lat <= 90.0
