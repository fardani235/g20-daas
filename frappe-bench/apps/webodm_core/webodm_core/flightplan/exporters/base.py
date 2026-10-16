"""Exporter contract and the route model exporters share.

An exporter turns a mission into one file format. It declares what the
format can carry (``Exporter`` attributes) and renders one ``Route`` at a
time; splitting a mission into routes (one per battery, and again where a
format or app caps the waypoint count) is done once, here, for every format.

Formats differ in three ways that matter, and each is explicit:

- ``kind``: ``mission`` files are flown as-is, ``boundary`` files hand only
  the survey outline to a vendor app that plans its own lines, ``reference``
  files are for review and GIS.
- photo triggering (``triggers``): by distance, by time, a photo action at
  every exposure position, or none in the file (the pilot starts interval
  shooting on the camera).
- waypoint capacity (``max_waypoints``), which the aircraft's app may lower.

Which formats apply to an aircraft, and how well supported they are, is
data on the drone profile (``mission.formats``), not code here.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from ..geo import LocalFrame
from ..issues import Issue

TRIGGERS = ("distance", "time", "waypoint", "manual")
TRIGGER_LABELS = {
    "distance": "Distance interval (photo every N metres, set in the file)",
    "time": "Time interval (photo every N seconds, set in the file)",
    "waypoint": "Photo at every waypoint (one waypoint per photo; the aircraft stops for each)",
    "manual": "None in the file (start interval shooting on the camera yourself)",
}

# Braking + accelerating + exposure when the aircraft stops for a photo.
STOP_AND_SHOOT_S = 2.0


class ExportError(ValueError):
    """The plan cannot be exported in the requested way; the message is for the user."""

    def __init__(self, message: str, code: str = "EXPORT_REFUSED", issues: list | None = None):
        super().__init__(message)
        self.code = code
        self.issues = issues or []


@dataclass
class RoutePoint:
    lng: float
    lat: float
    alt: float            # metres above the takeoff point
    heading: float
    leg: int
    stop: bool            # the aircraft stops here (start / end of a flight line)
    trigger_on: bool      # interval triggering runs from this point to the next
    take_photo: bool      # a single photo is taken at this point
    first_of_leg: bool = False
    last_of_leg: bool = False


@dataclass
class Route:
    """One file's worth of flying: a battery flight, or part of one."""

    points: list[RoutePoint]
    flight: int                    # battery flight this route belongs to (0-based)
    part: int = 0                  # part within the flight when a waypoint cap split it
    parts: int = 1
    name: str = ""

    @property
    def length_m(self) -> float:
        total = 0.0
        for a, b in zip(self.points, self.points[1:]):
            total += _ground_distance(a, b)
        return total


def _ground_distance(a: RoutePoint, b: RoutePoint) -> float:
    lat = math.radians((a.lat + b.lat) / 2.0)
    dx = math.radians(b.lng - a.lng) * math.cos(lat) * 6371008.8
    dy = math.radians(b.lat - a.lat) * 6371008.8
    return math.hypot(dx, dy)


@dataclass
class ExportContext:
    """What a renderer needs besides the route."""

    mission: dict
    title: str
    trigger: str
    options: dict = field(default_factory=dict)
    created_ms: int = 0
    author: str = "WebODM flight planner"

    @property
    def params(self) -> dict:
        return self.mission["params"]

    @property
    def camera(self) -> dict:
        return self.mission["camera"]

    @property
    def drone(self) -> dict:
        return self.mission["equipment"]["drone"]

    @property
    def camera_profile(self) -> dict:
        return self.mission["equipment"]["camera"]

    @property
    def takeoff(self) -> dict:
        return self.mission["takeoff"]

    @property
    def speed(self) -> float:
        return float(self.mission["stats"]["speed_ms"])

    @property
    def transit_speed(self) -> float:
        return float(self.params.get("transit_speed_ms") or self.speed)

    @property
    def rth_altitude(self) -> float:
        return float(self.mission["stats"]["rth_altitude_m"])


class Exporter:
    """Base class; one subclass per file format."""

    id = ""
    label = ""
    extension = ""
    mime = "application/octet-stream"
    kind = "mission"                 # "mission" | "boundary" | "reference"
    description = ""
    triggers: tuple[str, ...] = ()   # trigger modes the format can express; first is the default
    max_waypoints: int | None = None
    min_waypoints = 2
    # Steps shown to the user for getting the file onto the aircraft.
    instructions: tuple[str, ...] = ()

    def check(self, ctx: ExportContext, routes: list[Route]) -> list[Issue]:
        """Format-specific findings (category ``export``). Errors refuse the export."""
        return []

    def render(self, ctx: ExportContext, route: Route) -> bytes:  # pragma: no cover - abstract
        raise NotImplementedError

    def render_plan(self, ctx: ExportContext) -> bytes:  # pragma: no cover - abstract
        """Whole-plan rendering for ``boundary`` / ``reference`` formats."""
        raise NotImplementedError

    def describe(self) -> dict:
        return {
            "id": self.id, "label": self.label, "extension": self.extension, "kind": self.kind,
            "description": self.description, "triggers": list(self.triggers),
            "max_waypoints": self.max_waypoints, "instructions": list(self.instructions),
        }


# -- route construction ---------------------------------------------------------

def _leg_points(mission: dict, leg: dict, trigger: str) -> list[RoutePoint]:
    wps = mission["waypoints"][leg["from"]:leg["to"] + 1]
    if trigger != "waypoint":
        out = []
        for wp in wps:
            edge = wp["role"] in ("start", "end")
            out.append(RoutePoint(
                lng=wp["lng"], lat=wp["lat"], alt=wp["alt"], heading=wp["heading"], leg=leg["index"],
                stop=edge, trigger_on=bool(wp["photo"]) and trigger in ("distance", "time"), take_photo=False,
            ))
        out[0].first_of_leg = True
        out[-1].last_of_leg = True
        return out

    # One point per exposure: walk the leg at the photo spacing, interpolating
    # position and height between the leg's waypoints.
    frame = LocalFrame(mission["frame"]["lat0"], mission["frame"]["lng0"])
    xy = [frame.forward(wp["lng"], wp["lat"]) for wp in wps]
    cum = [0.0]
    for a, b in zip(xy, xy[1:]):
        cum.append(cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    total = cum[-1]
    spacing = float(mission["camera"]["photo_spacing_m"])
    count = int(math.floor(total / spacing + 1e-9)) + 1
    stations: list[tuple[float, bool]] = [(k * spacing, True) for k in range(count)]
    # Keep the leg's shape: its end point, and every bend of a corridor pass,
    # are flown even where no exposure falls on them.
    extra = [total]
    for k in range(1, len(wps) - 1):
        turn = abs((wps[k]["heading"] - wps[k - 1]["heading"] + 180.0) % 360.0 - 180.0)
        if turn > 1.0:
            extra.append(cum[k])
    for s in extra:
        if all(abs(s - other) > 0.5 for other, _photo in stations):
            stations.append((s, False))
    stations.sort()
    out = []
    seg = 0
    for s, is_photo in stations:
        while seg < len(cum) - 2 and s > cum[seg + 1] + 1e-9:
            seg += 1
        span = cum[seg + 1] - cum[seg]
        t = 0.0 if span < 1e-9 else min(1.0, max(0.0, (s - cum[seg]) / span))
        x = xy[seg][0] + (xy[seg + 1][0] - xy[seg][0]) * t
        y = xy[seg][1] + (xy[seg + 1][1] - xy[seg][1]) * t
        alt = wps[seg]["alt"] + (wps[seg + 1]["alt"] - wps[seg]["alt"]) * t
        lng, lat = frame.inverse(x, y)
        out.append(RoutePoint(
            lng=round(lng, 7), lat=round(lat, 7), alt=round(alt, 1), heading=wps[seg]["heading"],
            leg=leg["index"], stop=is_photo, trigger_on=False, take_photo=is_photo,
        ))
    out[0].stop = out[-1].stop = True
    out[0].first_of_leg = True
    out[-1].last_of_leg = True
    return out


def _chunks(points: list[RoutePoint], cap: int | None) -> list[list[RoutePoint]]:
    """Split a flight's points into runs of at most ``cap``, at flight-line ends where possible."""
    if not cap or len(points) <= cap:
        return [points]
    out: list[list[RoutePoint]] = []
    current: list[RoutePoint] = []
    by_leg: list[list[RoutePoint]] = []
    for p in points:
        if not by_leg or by_leg[-1][0].leg != p.leg:
            by_leg.append([])
        by_leg[-1].append(p)
    for leg_pts in by_leg:
        if len(leg_pts) > cap:
            # A single line longer than the cap: cut inside it, repeating the
            # cut point so the next file resumes exactly where this one ended.
            if current:
                out.append(current)
                current = []
            i = 0
            while i < len(leg_pts):
                piece = leg_pts[i:i + cap]
                out.append(piece)
                if i + cap >= len(leg_pts):
                    break
                i += cap - 1
            continue
        if len(current) + len(leg_pts) > cap:
            out.append(current)
            current = []
        current = current + leg_pts
    if current:
        out.append(current)
    return out


def build_routes(mission: dict, trigger: str, max_waypoints: int | None) -> list[Route]:
    """Routes in flight order: one per battery flight, split further by ``max_waypoints``."""
    legs = mission["legs"]
    routes: list[Route] = []
    for flight in mission["flights"]:
        points: list[RoutePoint] = []
        for leg in legs[flight["leg_from"]:flight["leg_to"] + 1]:
            points.extend(_leg_points(mission, leg, trigger))
        pieces = _chunks(points, max_waypoints)
        for k, piece in enumerate(pieces):
            routes.append(Route(points=piece, flight=flight["index"], part=k, parts=len(pieces)))
    return routes


def slug(text: str, fallback: str = "flight-plan") -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", str(text or "")).strip("-").lower()
    return s[:60] or fallback


def route_suffix(route: Route, routes: list[Route]) -> str:
    flights = len({r.flight for r in routes})
    parts = []
    if flights > 1:
        parts.append(f"flight-{route.flight + 1}-of-{flights}")
    if route.parts > 1:
        parts.append(f"part-{route.part + 1}-of-{route.parts}")
    return ("-" + "-".join(parts)) if parts else ""


def xml_escape(text) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def fmt(value: float, digits: int = 2) -> str:
    """Number without a trailing ``.0`` tail (stable output, small files)."""
    s = f"{float(value):.{digits}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"
