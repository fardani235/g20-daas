"""Advisory airspace check against an operator-supplied zone file.

The platform ships no airspace data: authoritative, current airspace
information belongs to the national aviation authority and changes daily
(NOTAMs, temporary restrictions). What this module offers is narrower and
says so: if the operator installs a GeoJSON file of zones they care about
(published UAS geographical zones, aerodrome buffers, client no-fly areas),
a plan that touches one of them is flagged.

A plan that touches no zone is **not** cleared to fly; it only means the
file contained nothing there. The result is always reported with the
``advisory`` basis, and without a file the status is ``not_checked``.

GeoJSON: a FeatureCollection of Polygon / MultiPolygon features. Recognised
properties (all optional): ``name``, ``type`` (free text, e.g. "prohibited",
"CTR", "aerodrome"), ``lower_m`` / ``upper_m`` (height limits above ground,
informational), ``message``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import geometry as g

MAX_ZONES = 20000


class ZoneFileError(ValueError):
    """The zone file is not usable GeoJSON."""


@dataclass
class Zone:
    name: str
    kind: str
    polygons: list[list[list[tuple[float, float]]]]   # polygons -> rings -> (lng, lat)
    bbox: tuple[float, float, float, float]
    lower_m: float | None = None
    upper_m: float | None = None
    message: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "type": self.kind, "lower_m": self.lower_m, "upper_m": self.upper_m,
                "message": self.message}


@dataclass
class ZoneSet:
    zones: list[Zone] = field(default_factory=list)
    label: str = "Zone file"
    as_of: str = ""

    def describe(self) -> str:
        when = f", dated {self.as_of}" if self.as_of else ""
        return f"{self.label} ({len(self.zones)} zone{'' if len(self.zones) == 1 else 's'}{when})"


def _num(value) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _rings(coords) -> list[list[tuple[float, float]]]:
    rings = []
    for ring in coords or []:
        pts = []
        for p in ring or []:
            try:
                pts.append((float(p[0]), float(p[1])))
            except (TypeError, ValueError, IndexError):
                raise ZoneFileError("zone geometry contains an invalid coordinate")
        if len(pts) > 1 and pts[0] == pts[-1]:
            pts = pts[:-1]
        if len(pts) >= 3:
            rings.append(pts)
    return rings


def load_zones(data: dict, *, label: str = "Zone file", as_of: str = "") -> ZoneSet:
    """Parse a GeoJSON FeatureCollection into a ``ZoneSet``. Raises ``ZoneFileError``."""
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        raise ZoneFileError("the zone file must be a GeoJSON FeatureCollection")
    features = data.get("features") or []
    if len(features) > MAX_ZONES:
        raise ZoneFileError(f"the zone file has more than {MAX_ZONES} features")
    zones: list[Zone] = []
    for i, feat in enumerate(features):
        geom = (feat or {}).get("geometry") or {}
        props = (feat or {}).get("properties") or {}
        if geom.get("type") == "Polygon":
            polygons = [_rings(geom.get("coordinates"))]
        elif geom.get("type") == "MultiPolygon":
            polygons = [_rings(poly) for poly in geom.get("coordinates") or []]
        else:
            continue
        polygons = [p for p in polygons if p]
        if not polygons:
            continue
        pts = [pt for poly in polygons for pt in poly[0]]
        zones.append(Zone(
            name=str(props.get("name") or props.get("NAME") or f"Zone {i + 1}")[:140],
            kind=str(props.get("type") or props.get("category") or "restricted")[:60],
            polygons=polygons, bbox=g.bbox(pts),
            lower_m=_num(props.get("lower_m")), upper_m=_num(props.get("upper_m")),
            message=str(props.get("message") or "")[:500],
        ))
    return ZoneSet(zones=zones, label=label, as_of=str(data.get("as_of") or as_of or ""))


def _bbox_overlap(a, b) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def _touches(zone: Zone, points: list[tuple[float, float]], closed_rings: list[list[tuple[float, float]]],
             open_lines: list[list[tuple[float, float]]]) -> bool:
    for poly in zone.polygons:
        # Any plan point inside the zone.
        if any(g.point_in_rings(p, poly) for p in points):
            return True
        outer = poly[0]
        # The zone lying entirely inside the survey area.
        if any(g.point_in_rings(outer[0], [ring]) for ring in closed_rings):
            return True
        # An edge of the plan crossing the zone outline.
        edges = [(outer[i], outer[(i + 1) % len(outer)]) for i in range(len(outer))]
        segments = []
        for ring in closed_rings:
            segments += [(ring[i], ring[(i + 1) % len(ring)]) for i in range(len(ring))]
        for line in open_lines:
            segments += [(line[i], line[i + 1]) for i in range(len(line) - 1)]
        for a, b in segments:
            for c, d in edges:
                if g.segments_cross(a, b, c, d):
                    return True
    return False


def zones_touched(zoneset: ZoneSet, *, boundary_rings: list[list[tuple[float, float]]],
                  route: list[tuple[float, float]], takeoff: tuple[float, float] | None) -> list[Zone]:
    """Zones the survey boundary, the route or the takeoff point touch (coordinates as ``(lng, lat)``)."""
    points = list(route) + [p for r in boundary_rings for p in r]
    if takeoff:
        points.append(takeoff)
        route = [takeoff, *route, takeoff]
    if not points:
        return []
    box = g.bbox(points)
    hits = []
    for zone in zoneset.zones:
        if not _bbox_overlap(box, zone.bbox):
            continue
        if _touches(zone, points, boundary_rings, [route] if len(route) > 1 else []):
            hits.append(zone)
    return hits
