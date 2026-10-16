"""Litchi waypoint mission CSV, imported through Litchi Mission Hub.

Litchi (a third-party app) flies waypoint missions on DJI aircraft that
DJI's own app gives no mission planning for. Mission Hub imports a CSV with
one row per waypoint; the column set below is the one Mission Hub itself
exports.

Limits that shape the export:

- 99 waypoints per mission (the limit of the DJI flight controllers Litchi
  uploads to), so longer missions are split into several files;
- altitude is metres above the takeoff point (``altitudemode`` 0);
- photos are taken with Litchi's distance interval, set on the waypoint that
  starts each flight line and cleared on the one that ends it.
"""

from __future__ import annotations

from ..issues import info, warning
from ..issues import error as issue_error
from .base import ExportContext, Exporter, Route, _ground_distance, fmt

MAX_WAYPOINTS = 99
ALT_MIN, ALT_MAX = -200.0, 500.0
SPEED_MAX = 15.0
ACTION_SLOTS = 15
ACTION_NONE, ACTION_PHOTO = -1, 1
GIMBAL_INTERPOLATE = 2

HEADER = (
    ["latitude", "longitude", "altitude(m)", "heading(deg)", "curvesize(m)", "rotationdir", "gimbalmode",
     "gimbalpitchangle"]
    + [f"{kind}{n}" for n in range(1, ACTION_SLOTS + 1) for kind in ("actiontype", "actionparam")]
    + ["altitudemode", "speed(m/s)", "poi_latitude", "poi_longitude", "poi_altitude(m)", "poi_altitudemode",
       "photo_timeinterval", "photo_distinterval"]
)


class LitchiCsv(Exporter):
    id = "litchi-csv"
    label = "Litchi mission (.csv)"
    extension = "csv"
    mime = "text/csv"
    kind = "mission"
    description = "Waypoint mission for the Litchi app (older DJI aircraft), imported in Litchi Mission Hub."
    triggers = ("distance", "time", "waypoint", "manual")
    max_waypoints = MAX_WAYPOINTS
    instructions = (
        "Litchi Mission Hub (flylitchi.com/hub): Missions > Import, choose the .csv file, review the route and "
        "heights on the map, then save it to your account.",
        "In the Litchi app, open the saved mission in Waypoint mode and check the settings (finish action, "
        "heading mode, cruising speed) before starting: mission-level settings are not part of the CSV.",
        "Take off from the planned takeoff point: altitudes are relative to the launch position.",
    )

    def check(self, ctx: ExportContext, routes: list[Route]):
        issues = []
        alts = [p.alt for r in routes for p in r.points]
        if alts and (min(alts) < ALT_MIN or max(alts) > ALT_MAX):
            issues.append(issue_error(
                "LITCHI_ALTITUDE_RANGE", "export",
                f"Litchi accepts waypoint heights between {ALT_MIN:g} and {ALT_MAX:g} m relative to takeoff; this "
                f"route spans {min(alts):.0f} to {max(alts):.0f} m."))
        if ctx.speed > SPEED_MAX:
            issues.append(issue_error(
                "LITCHI_SPEED", "export",
                f"Litchi waypoint missions fly at up to {SPEED_MAX:g} m/s; the plan uses {ctx.speed:g} m/s."))
        if len(routes) > 1:
            issues.append(info(
                "LITCHI_SPLIT", "export",
                f"Litchi missions hold at most {MAX_WAYPOINTS} waypoints, so the route is split into "
                f"{len(routes)} files. Fly them in order; each starts where the previous one ended.", basis="note"))
        if ctx.takeoff.get("assumed"):
            issues.append(warning(
                "LITCHI_TAKEOFF_ASSUMED", "export",
                "Altitudes are relative to the launch position, and no takeoff point is set in the plan. Launch "
                "at the first waypoint's elevation or set the takeoff point and export again.", basis="note"))
        if ctx.trigger == "manual":
            issues.append(warning(
                "TRIGGER_MANUAL", "export",
                f"The file contains no photo trigger. Start interval shooting on the camera yourself: a photo every "
                f"{ctx.camera['photo_interval_s']:g} s at {ctx.speed:g} m/s.", basis="note"))
        issues.append(info(
            "LITCHI_SETTINGS", "export",
            "Finish action, heading mode and signal-loss behaviour are set in Litchi, not in the CSV. Set the "
            "heading mode to follow the waypoints and choose the finish action before flying.", basis="note"))
        return issues

    def render(self, ctx: ExportContext, route: Route) -> bytes:
        pitch = fmt(ctx.camera.get("gimbal_pitch_deg", -90), 1)
        rows = [",".join(HEADER)]
        pts = route.points
        for i, p in enumerate(pts):
            if p.stop:
                curve = 0.0
            else:
                before = _ground_distance(pts[i - 1], p) if i else 10.0
                after = _ground_distance(p, pts[i + 1]) if i + 1 < len(pts) else 10.0
                # Litchi needs the curve to fit within half of each neighbouring segment.
                curve = max(0.2, min(5.0, 0.4 * min(before, after)))
            actions = [ACTION_NONE, 0] * ACTION_SLOTS
            if p.take_photo:
                actions[0] = ACTION_PHOTO
            time_iv = dist_iv = -1
            if p.trigger_on and ctx.trigger == "distance":
                dist_iv = ctx.camera["photo_spacing_m"]
            elif p.trigger_on and ctx.trigger == "time":
                time_iv = ctx.camera["photo_interval_s"]
            row = [
                fmt(p.lat, 7), fmt(p.lng, 7), fmt(p.alt, 1), fmt(p.heading, 0), fmt(curve, 1), "0",
                str(GIMBAL_INTERPOLATE), pitch,
                *[str(a) for a in actions],
                "0", fmt(ctx.speed), "0", "0", "0", "0",
                fmt(time_iv, 1), fmt(dist_iv, 1),
            ]
            rows.append(",".join(row))
        return ("\n".join(rows) + "\n").encode()
