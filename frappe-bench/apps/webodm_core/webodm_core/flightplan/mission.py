"""Mission builder: plan definition + equipment (+ terrain) -> a complete mission.

``plan_mission`` is the single entry point. It is pure: the same inputs give
the same mission, there is no I/O and no Frappe. The caller resolves the
drone and camera profiles and, if wanted, fetches a ``TerrainModel``; this
module lays out the flight lines, assigns altitudes, estimates the flight
and runs the checks.

The returned mission is a JSON-serialisable dict. It is what the map draws,
what is stored with a saved plan and what the exporters read, so everything
an exporter needs (waypoints, legs, spacing, takeoff point, equipment
snapshot) is in it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import checks
from . import geometry as g
from . import photogrammetry as pg
from .geo import LocalFrame, bearing_deg, valid_lnglat
from .issues import NOTICE, counts, worst
from .params import PLAN_TYPES, PlanError, normalize_params, rule_sets
from .profiles import Camera, Drone
from .survey import Layout, LayoutError, area_layout, corridor_layout
from .terrain import TerrainModel

PLANNER_VERSION = 1

# Flight-time model constants. They are deliberately simple and on the
# cautious side; every number they produce is shown as an estimate.
ACCEL_MS2 = 2.5              # multirotor acceleration / braking along the line
TURN_SECONDS = 2.0           # yaw to the next heading at a line end
GROUND_OVERHEAD_S = 20.0     # spin-up, takeoff hover, touchdown
DEFAULT_ASCENT_MS = 3.0
DEFAULT_DESCENT_MS = 2.0


@dataclass(frozen=True)
class Limits:
    """Bounds that keep a request cheap to compute and a mission loadable."""

    max_vertices: int = 500
    max_extent_m: float = 60000.0
    max_lines: int = 2000
    max_waypoints: int = 20000
    max_photos: int = 100000
    max_analysis_samples: int = 6000
    max_profile_points: int = 400


LIMITS = Limits()


# -- geometry parsing ----------------------------------------------------------

def _coords(raw, what: str, minimum: int) -> list[tuple[float, float]]:
    if not isinstance(raw, (list, tuple)):
        raise PlanError(f"{what} must be a list of [longitude, latitude] pairs", "geometry")
    out = []
    for p in raw:
        if not isinstance(p, (list, tuple)) or len(p) < 2 or not valid_lnglat(p[0], p[1]):
            raise PlanError(f"{what} contains an invalid coordinate", "geometry")
        pt = (float(p[0]), float(p[1]))
        if not out or abs(out[-1][0] - pt[0]) > 1e-9 or abs(out[-1][1] - pt[1]) > 1e-9:
            out.append(pt)
    if len(out) < minimum:
        raise PlanError(f"{what} needs at least {minimum} distinct points", "geometry")
    return out


def parse_geometry(plan_type: str, geometry) -> list[list[tuple[float, float]]] | list[tuple[float, float]]:
    """GeoJSON geometry -> polygon rings (area) or a line (corridor), as ``(lng, lat)`` tuples."""
    if plan_type not in PLAN_TYPES:
        raise PlanError(f"Unknown plan type: {plan_type!r}", "type")
    if not isinstance(geometry, dict):
        raise PlanError("Draw the survey area on the map first", "geometry")
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if plan_type == "area":
        if gtype != "Polygon" or not isinstance(coords, (list, tuple)) or not coords:
            raise PlanError("An area plan needs a polygon", "geometry")
        rings = []
        for i, ring in enumerate(coords):
            pts = _coords(ring, "The survey boundary" if i == 0 else "A hole in the boundary", 3)
            if len(pts) > 1 and pts[0] == pts[-1]:
                pts = pts[:-1]
            if len(pts) < 3:
                raise PlanError("The survey boundary needs at least 3 corners", "geometry")
            rings.append(pts)
        return rings
    if gtype != "LineString":
        raise PlanError("A corridor plan needs a line", "geometry")
    return _coords(coords, "The corridor centre line", 2)


def _takeoff(raw) -> tuple[float, float] | None:
    if raw in (None, "", []):
        return None
    if isinstance(raw, dict):
        raw = [raw.get("lng"), raw.get("lat")]
    if not isinstance(raw, (list, tuple)) or len(raw) < 2 or not valid_lnglat(raw[0], raw[1]):
        raise PlanError("The takeoff point is not a valid coordinate", "takeoff")
    return float(raw[0]), float(raw[1])


# -- the builder ---------------------------------------------------------------

def plan_mission(definition: dict, drone: Drone, camera: Camera, terrain: TerrainModel | None = None,
                 zones=None, limits: Limits = LIMITS) -> dict:
    """Build the mission for ``definition``. Raises ``PlanError`` when it cannot be laid out at all.

    ``definition``: ``{"type": "area"|"corridor", "geometry": GeoJSON,
    "corridor_width_m": float, "takeoff": [lng, lat] | None, "params": {...}}``.

    Problems that leave a mission that can still be drawn (too fast for the
    camera, too high for the rule preset, not enough clearance ...) do not
    raise: they are returned as ``issues`` so the user sees the route *and*
    what is wrong with it.
    """
    if not isinstance(definition, dict):
        raise PlanError("plan definition must be an object")
    plan_type = definition.get("type") or "area"
    shape = parse_geometry(plan_type, definition.get("geometry"))
    params = normalize_params(definition.get("params"), drone)
    takeoff_ll = _takeoff(definition.get("takeoff"))

    all_ll = [p for ring in shape for p in ring] if plan_type == "area" else list(shape)
    if len(all_ll) > limits.max_vertices:
        raise PlanError(f"The boundary has more than {limits.max_vertices} points; simplify it", "geometry")
    frame = LocalFrame.around(all_ll)

    if plan_type == "area":
        rings = [frame.forward_all(r) for r in shape]
        outline = rings[0]
        if g.ring_self_intersects(outline):
            raise PlanError("The survey boundary crosses itself; move a corner so the outline is simple", "geometry")
        if g.polygon_area(rings) < 1.0:
            raise PlanError("The survey area is too small to plan", "geometry")
        extent_pts = outline
    else:
        centre = frame.forward_all(shape)
        if g.polyline_length(centre) < 1.0:
            raise PlanError("The corridor is too short to plan", "geometry")
        extent_pts = centre
    xmin, ymin, xmax, ymax = g.bbox(extent_pts)
    if max(xmax - xmin, ymax - ymin) > limits.max_extent_m:
        raise PlanError(f"The survey is more than {limits.max_extent_m / 1000:.0f} km across; split it into "
                        "several plans", "geometry")

    # -- camera geometry -------------------------------------------------------
    if params["altitude_from"] == "gsd":
        altitude = pg.height_for_gsd(camera, params["gsd_cm"] / 100.0)
        if not (5.0 <= altitude <= 2000.0):
            raise PlanError(
                f"A ground resolution of {params['gsd_cm']:g} cm/px needs a flight height of {altitude:.0f} m "
                f"with the {camera.label}, which is outside 5-2000 m", "gsd_cm")
    else:
        altitude = params["altitude_m"]
    fp = pg.footprint(camera, altitude, params["camera_orientation"])
    spacing = pg.line_spacing(fp, params["side_overlap"])
    photo_sp = pg.photo_spacing(fp, params["front_overlap"])
    if spacing < 0.5 or photo_sp < 0.2:
        raise PlanError("Overlap this high at this height puts the flight lines or photos less than half a "
                        "metre apart; lower the overlap or fly higher", "side_overlap")

    overshoot = params["overshoot_m"]
    if overshoot is None:
        overshoot = 0.0 if drone.can_hover else float(drone.min_turn_radius_m or 0.0)

    near = frame.forward(*takeoff_ll) if takeoff_ll else None
    try:
        if plan_type == "area":
            direction = None if params["direction"] == "auto" else float(params["direction"])
            # Bridge gaps shorter than a couple of exposures rather than stopping for them.
            layout = area_layout(rings, spacing, direction_deg=direction, overshoot=overshoot,
                                 merge_gap=max(2.0 * photo_sp, 10.0), start=params["start"], near=near,
                                 crosshatch=params["crosshatch"])
            area_m2 = g.polygon_area(rings)
        else:
            width = definition.get("corridor_width_m")
            try:
                width = float(width)
            except (TypeError, ValueError):
                raise PlanError("Enter the corridor width", "corridor_width_m")
            if not (1.0 <= width <= 5000.0):
                raise PlanError("Corridor width must be between 1 and 5000 m", "corridor_width_m")
            if g.polyline_self_intersects(centre):
                raise PlanError("The corridor centre line crosses itself; split it into separate plans", "geometry")
            layout = corridor_layout(centre, width, spacing, fp.across_m,
                                     min_passes=params["corridor_min_passes"], overshoot=overshoot,
                                     start=params["start"], near=near)
            area_m2 = g.polyline_length(centre) * width
    except LayoutError as e:
        raise PlanError(str(e), "geometry") from e

    if layout.line_count > limits.max_lines:
        raise PlanError(f"This plan needs {layout.line_count} flight lines (limit {limits.max_lines}). Fly higher, "
                        "lower the side overlap or split the area", "altitude_m")

    # -- takeoff point and terrain reference -----------------------------------
    first_xy = layout.legs[0].points[0]
    takeoff_assumed = near is None
    takeoff_xy = near if near is not None else first_xy
    takeoff_lnglat = takeoff_ll if takeoff_ll else frame.inverse(*first_xy)

    def ground_at(xy) -> float | None:
        if terrain is None:
            return None
        lng, lat = frame.inverse(xy[0], xy[1])
        return terrain.elevation(lng, lat)

    takeoff_ground = ground_at(takeoff_xy)
    terrain_ok = terrain is not None and takeoff_ground is not None
    # Terrain following is planned whenever it is asked for and data exists.
    # An aircraft whose app plans altitudes itself still gets the estimate
    # (that is what its app will fly); it only cannot be given the heights.
    follow = params["altitude_mode"] == "terrain" and terrain_ok

    tolerance = params["terrain_tolerance_m"]
    if tolerance is None:
        tolerance = min(10.0, max(2.0, altitude * 0.05))

    photo_length = layout.photo_length
    step = 25.0
    if terrain is not None:
        step = min(100.0, max(10.0, (terrain.spacing_m or 30.0) / 2.0))
    step = max(step, photo_length / max(1, limits.max_analysis_samples))

    # -- waypoints -------------------------------------------------------------
    waypoints: list[dict] = []
    legs_out: list[dict] = []
    photos_total = 0
    missing_ground = 0
    for li, leg in enumerate(layout.legs):
        pts = leg.points
        first_index = len(waypoints)
        route: list[tuple[tuple[float, float], float, float | None]] = []   # (xy, alt_rel, ground)
        if follow:
            for si in range(len(pts) - 1):
                samples = g.resample_polyline([pts[si], pts[si + 1]], step)
                profile = []
                grounds = []
                last_ground = takeoff_ground
                for xy, d in samples:
                    gr = ground_at(xy)
                    if gr is None:
                        missing_ground += 1
                        gr_used = last_ground
                    else:
                        gr_used = last_ground = gr
                    grounds.append(gr)
                    profile.append((d, gr_used + altitude - takeoff_ground))
                keep = g.simplify_profile(profile, tolerance)
                for k in keep:
                    if k == len(samples) - 1 and si < len(pts) - 2:
                        continue  # the next segment starts with this vertex
                    route.append((samples[k][0], profile[k][1], grounds[k]))
        else:
            for xy in pts:
                route.append((xy, altitude, ground_at(xy) if terrain_ok else None))

        length = leg.length
        n_photos = int(math.floor(length / photo_sp + 1e-9)) + 1
        photos_total += n_photos
        for k, (xy, alt, gr) in enumerate(route):
            role = "start" if k == 0 else ("end" if k == len(route) - 1 else "via")
            waypoints.append({
                "x": xy[0], "y": xy[1], "alt": alt, "ground": gr,
                "leg": li, "line": leg.line, "role": role, "photo": role != "end",
            })
        if len(waypoints) > limits.max_waypoints:
            raise PlanError(f"This plan needs more than {limits.max_waypoints} waypoints. Loosen the terrain "
                            "following tolerance, fly higher or split the area", "terrain_tolerance_m")
        heading = bearing_deg(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1])
        legs_out.append({
            "index": li, "line": leg.line, "from": first_index, "to": len(waypoints) - 1,
            "length_m": round(length, 1), "photos": n_photos, "heading": round(heading, 1),
        })
    if photos_total > limits.max_photos:
        raise PlanError(f"This plan would take about {photos_total:,} photos (limit {limits.max_photos:,}). "
                        "Fly higher, lower the overlap or split the area", "altitude_m")

    for i, wp in enumerate(waypoints):
        nxt = waypoints[i + 1] if i + 1 < len(waypoints) else None
        prev = waypoints[i - 1] if i else None
        if nxt is not None and (nxt["leg"] == wp["leg"] or prev is None or prev["leg"] != wp["leg"]):
            ref = (nxt["x"] - wp["x"], nxt["y"] - wp["y"])
        elif prev is not None:
            ref = (wp["x"] - prev["x"], wp["y"] - prev["y"])
        else:
            ref = (0.0, 1.0)
        wp["heading"] = round(bearing_deg(*ref), 1) if (abs(ref[0]) + abs(ref[1])) > 1e-9 else 0.0
        wp["index"] = i
        wp["lng"], wp["lat"] = frame.inverse(wp["x"], wp["y"])
        if terrain_ok and wp["ground"] is not None:
            wp["agl"] = takeoff_ground + wp["alt"] - wp["ground"]
        else:
            wp["agl"] = None

    # -- flight estimate -------------------------------------------------------
    speed = params["speed_ms"]
    transit_speed = params["transit_speed_ms"] or speed
    ascent = drone.max_ascent_ms or DEFAULT_ASCENT_MS
    descent = drone.max_descent_ms or DEFAULT_DESCENT_MS
    max_alt = max(wp["alt"] for wp in waypoints)
    rth_alt = params["rth_altitude_m"] if params["rth_altitude_m"] is not None else max(max_alt, altitude)
    returns_home = params["finish_action"] != "land"

    if drone.can_hover:
        leg_penalty = 2.0 * (speed / ACCEL_MS2 + TURN_SECONDS)
        hop_extra_m = 0.0
    else:
        leg_penalty = 0.0
        hop_extra_m = math.pi * float(drone.min_turn_radius_m or 0.0)

    leg_time: list[float] = []
    hop_time: list[float] = []
    hop_dist: list[float] = []
    for li, leg in enumerate(layout.legs):
        leg_time.append(leg.length / speed + leg_penalty)
        if li == 0:
            hop_dist.append(0.0)
            hop_time.append(0.0)
        else:
            d = g.dist(layout.legs[li - 1].points[-1], leg.points[0]) + hop_extra_m
            hop_dist.append(d)
            hop_time.append(d / speed)

    def out_and_back(a: int, b: int) -> tuple[float, float, float]:
        """(time, distance out, distance back) of the transit for a flight covering legs a..b."""
        first_wp = waypoints[legs_out[a]["from"]]
        last_wp = waypoints[legs_out[b]["to"]]
        d_out = g.dist(takeoff_xy, (first_wp["x"], first_wp["y"]))
        d_back = g.dist((last_wp["x"], last_wp["y"]), takeoff_xy) if returns_home else 0.0
        t = GROUND_OVERHEAD_S + max(first_wp["alt"], 0.0) / ascent + d_out / transit_speed
        if returns_home:
            t += d_back / transit_speed + max(rth_alt, last_wp["alt"], 0.0) / descent
        else:
            t += max(last_wp["alt"], 0.0) / descent
        return t, d_out, d_back

    usable_s = drone.max_flight_time_min * 60.0 * drone.endurance_factor * (1.0 - params["reserve_percent"] / 100.0)
    rated_s = drone.max_flight_time_min * 60.0 * drone.endurance_factor

    flights: list[dict] = []
    a = 0
    n_legs = len(layout.legs)
    while a < n_legs:
        b = a
        work = leg_time[a]
        while b + 1 < n_legs:
            more = work + hop_time[b + 1] + leg_time[b + 1]
            if more + out_and_back(a, b + 1)[0] > usable_s:
                break
            b += 1
            work = more
        t_transit, d_out, d_back = out_and_back(a, b)
        total = work + t_transit
        dist_m = sum(layout.legs[i].length for i in range(a, b + 1)) + sum(hop_dist[i] for i in range(a + 1, b + 1))
        flights.append({
            "index": len(flights), "leg_from": a, "leg_to": b,
            "wp_from": legs_out[a]["from"], "wp_to": legs_out[b]["to"],
            "duration_s": round(total, 1), "survey_s": round(work, 1),
            "distance_m": round(dist_m + d_out + d_back, 1),
            "photos": sum(legs_out[i]["photos"] for i in range(a, b + 1)),
            "battery_percent": round(total / rated_s * 100.0, 1) if rated_s > 0 else None,
            "exceeds_endurance": total > usable_s + 1e-6,
        })
        a = b + 1

    survey_time = sum(leg_time) + sum(hop_time)
    survey_dist = photo_length + sum(hop_dist)
    total_time = sum(f["duration_s"] for f in flights)
    total_dist = sum(f["distance_m"] for f in flights)
    photo_interval = photo_sp / speed

    # -- terrain analysis ------------------------------------------------------
    terrain_out = None
    transit_clearance = None
    if terrain_ok:
        terrain_out, transit_clearance = _analyse_terrain(
            waypoints, frame, terrain, takeoff_ground, takeoff_xy, rth_alt, returns_home, step, limits,
            altitude, fp, spacing, photo_sp, camera)
        terrain_out["missing_samples"] = missing_ground + terrain_out.pop("_missing")
        terrain_out["following"] = follow
        terrain_out["tolerance_m"] = round(tolerance, 1) if follow else None

    climb = _climb_rates(waypoints, speed)
    furthest = max(g.dist(takeoff_xy, (wp["x"], wp["y"])) for wp in waypoints)

    # -- assemble --------------------------------------------------------------
    target_rings = [[list(map(_r7, frame.inverse(*p))) for p in ring] for ring in layout.target]
    for ring in target_rings:
        if ring and ring[0] != ring[-1]:
            ring.append(ring[0])
    first_leg = layout.legs[0].points
    foot = _footprint_polygon(first_leg[0], first_leg[1], fp, frame)

    rule = dict(rule_sets().get(params["regulation"]) or {})
    facts = checks.Facts(
        plan_type=plan_type, params=params, drone=drone, camera=camera, altitude_m=altitude, footprint=fp,
        line_spacing_m=spacing, photo_spacing_m=photo_sp, photo_interval_s=photo_interval,
        waypoint_count=len(waypoints), line_count=layout.line_count, passes=layout.passes,
        photos=photos_total, max_alt_rel=max_alt, min_alt_rel=min(wp["alt"] for wp in waypoints),
        rth_alt=rth_alt, takeoff_assumed=takeoff_assumed, takeoff_ground=takeoff_ground,
        terrain=terrain, terrain_ok=terrain_ok, following=follow, terrain_stats=terrain_out,
        transit_clearance=transit_clearance, climb=climb, furthest_m=furthest, flights=flights,
        usable_s=usable_s, rule=rule, overshoot_m=overshoot, zones=zones,
        boundary_lnglat=shape, takeoff_lnglat=takeoff_lnglat, waypoints=waypoints,
    )
    issues, assurance = checks.evaluate(facts)

    mission = {
        "version": PLANNER_VERSION,
        "type": plan_type,
        "frame": {"lat0": frame.lat0, "lng0": frame.lng0},
        "takeoff": {
            "lng": _r7(takeoff_lnglat[0]), "lat": _r7(takeoff_lnglat[1]),
            "assumed": takeoff_assumed,
            "ground_m": _r1(takeoff_ground),
        },
        "camera": {
            "altitude_m": _r1(altitude),
            "gsd_cm": round(fp.gsd_cm, 2),
            "footprint_across_m": _r1(fp.across_m),
            "footprint_along_m": _r1(fp.along_m),
            "line_spacing_m": round(spacing, 2),
            "photo_spacing_m": round(photo_sp, 2),
            "photo_interval_s": round(photo_interval, 2),
            "orientation": params["camera_orientation"],
            "gimbal_pitch_deg": params["gimbal_pitch_deg"],
        },
        "layout": {
            "direction_deg": None if layout.direction_deg is None else round(layout.direction_deg, 1),
            "direction_auto": plan_type == "area" and params["direction"] == "auto",
            "line_count": layout.line_count,
            "passes": layout.passes,
            "overshoot_m": _r1(overshoot),
            "crosshatch": bool(params["crosshatch"]) and plan_type == "area",
        },
        "legs": legs_out,
        "waypoints": [
            {
                "index": wp["index"], "lng": _r7(wp["lng"]), "lat": _r7(wp["lat"]), "alt": _r1(wp["alt"]),
                "agl": _r1(wp["agl"]), "ground": _r1(wp["ground"]), "leg": wp["leg"], "line": wp["line"],
                "role": wp["role"], "photo": wp["photo"], "heading": wp["heading"],
            }
            for wp in waypoints
        ],
        "coverage": {"type": "Polygon", "coordinates": target_rings} if target_rings else None,
        "footprint": foot,
        "flights": flights,
        "stats": {
            "area_m2": round(area_m2, 1),
            "line_count": layout.line_count,
            "leg_count": len(legs_out),
            "waypoint_count": len(waypoints),
            "photo_count": photos_total,
            "survey_distance_m": round(survey_dist, 1),
            "total_distance_m": round(total_dist, 1),
            "survey_duration_s": round(survey_time, 1),
            "total_duration_s": round(total_time, 1),
            "speed_ms": speed,
            "flights": len(flights),
            "batteries": len(flights),
            "usable_flight_time_s": round(usable_s, 1),
            "furthest_from_takeoff_m": round(furthest, 1),
            "max_altitude_m": _r1(max_alt),
            "min_altitude_m": _r1(min(wp["alt"] for wp in waypoints)),
            "rth_altitude_m": _r1(rth_alt),
            "max_climb_ms": round(climb["up"], 2),
            "max_sink_ms": round(climb["down"], 2),
        },
        "terrain": terrain_out,
        "params": params,
        "equipment": {"drone": drone.to_dict(), "camera": camera.to_dict()},
        "issues": [i.to_dict() for i in issues],
        "assurance": [a_.to_dict() for a_ in assurance],
        "notice": NOTICE,
    }
    mission["summary"] = {**counts(issues), "worst": worst(issues), "exportable": worst(issues) != "error"}
    return mission


def append_issue(mission: dict, issue) -> dict:
    """Add a finding raised outside the planner (e.g. the elevation service failed) and refresh the summary."""
    mission["issues"].append(issue.to_dict() if hasattr(issue, "to_dict") else dict(issue))
    order = {"error": 0, "warning": 1, "info": 2}
    mission["issues"].sort(key=lambda i: order.get(i.get("severity"), 3))
    top = worst(mission["issues"])
    mission["summary"] = {**counts(mission["issues"]), "worst": top, "exportable": top != "error"}
    return mission


def _r1(v):
    return None if v is None else round(float(v), 1)


def _r7(v):
    return round(float(v), 7)


def _footprint_polygon(a, b, fp: pg.Footprint, frame: LocalFrame) -> dict:
    """Ground footprint of the first photo, as a GeoJSON polygon."""
    d = g.dist(a, b) or 1.0
    ux, uy = (b[0] - a[0]) / d, (b[1] - a[1]) / d
    nx, ny = -uy, ux
    ha, hc = fp.along_m / 2.0, fp.across_m / 2.0
    corners = [
        (a[0] - ux * ha - nx * hc, a[1] - uy * ha - ny * hc),
        (a[0] + ux * ha - nx * hc, a[1] + uy * ha - ny * hc),
        (a[0] + ux * ha + nx * hc, a[1] + uy * ha + ny * hc),
        (a[0] - ux * ha + nx * hc, a[1] - uy * ha + ny * hc),
    ]
    ring = [list(map(_r7, frame.inverse(*c))) for c in corners]
    ring.append(ring[0])
    return {"type": "Polygon", "coordinates": [ring]}


def _climb_rates(waypoints: list[dict], speed: float) -> dict:
    """Steepest vertical speed the route asks for at the planned ground speed."""
    up = down = 0.0
    for i in range(len(waypoints) - 1):
        a, b = waypoints[i], waypoints[i + 1]
        horizontal = math.hypot(b["x"] - a["x"], b["y"] - a["y"])
        if horizontal < 1.0:
            continue
        rate = (b["alt"] - a["alt"]) * speed / horizontal
        up = max(up, rate)
        down = max(down, -rate)
    return {"up": up, "down": down}


def _analyse_terrain(waypoints, frame, terrain, takeoff_ground, takeoff_xy, rth_alt, returns_home, step, limits,
                     altitude, fp, spacing, photo_sp, camera):
    """Height above the terrain model along the whole route, and what that does to the imagery."""
    total_len = sum(math.hypot(waypoints[i + 1]["x"] - waypoints[i]["x"], waypoints[i + 1]["y"] - waypoints[i]["y"])
                    for i in range(len(waypoints) - 1))
    step = max(step, total_len / max(1, limits.max_analysis_samples))

    def ground(xy):
        lng, lat = frame.inverse(xy[0], xy[1])
        return terrain.elevation(lng, lat)

    profile = []          # (distance, ground, flight amsl)
    missing = 0
    agl_min = agl_max = None
    clear_min = None
    clear_at = None
    ground_min = ground_max = None
    travelled = 0.0
    for i in range(len(waypoints) - 1):
        a, b = waypoints[i], waypoints[i + 1]
        seg = math.hypot(b["x"] - a["x"], b["y"] - a["y"])
        n = max(1, int(math.ceil(seg / step)))
        last = i == len(waypoints) - 2
        for k in range(n + (1 if last else 0)):
            t = k / n
            xy = (a["x"] + (b["x"] - a["x"]) * t, a["y"] + (b["y"] - a["y"]) * t)
            alt = a["alt"] + (b["alt"] - a["alt"]) * t
            gr = ground(xy)
            if gr is None:
                missing += 1
                continue
            agl = takeoff_ground + alt - gr
            profile.append((travelled + seg * t, gr, takeoff_ground + alt))
            ground_min = gr if ground_min is None else min(ground_min, gr)
            ground_max = gr if ground_max is None else max(ground_max, gr)
            if clear_min is None or agl < clear_min:
                clear_min, clear_at = agl, xy
            if a["photo"]:
                agl_min = agl if agl_min is None else min(agl_min, agl)
                agl_max = agl if agl_max is None else max(agl_max, agl)
        travelled += seg

    # Transit: level at the first waypoint's height on the way out, at the
    # return height on the way back.
    transit_min = None

    def sweep(p, q, alt):
        nonlocal transit_min, missing
        seg = g.dist(p, q)
        n = max(1, int(math.ceil(seg / step)))
        for k in range(n + 1):
            t = k / n
            gr = ground((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t))
            if gr is None:
                missing += 1
                continue
            agl = takeoff_ground + alt - gr
            transit_min = agl if transit_min is None else min(transit_min, agl)

    first, last_wp = waypoints[0], waypoints[-1]
    sweep(takeoff_xy, (first["x"], first["y"]), first["alt"])
    if returns_home:
        sweep((last_wp["x"], last_wp["y"]), takeoff_xy, max(rth_alt, last_wp["alt"]))

    if len(profile) > limits.max_profile_points:
        stride = int(math.ceil(len(profile) / limits.max_profile_points))
        low = min(range(len(profile)), key=lambda j: profile[j][2] - profile[j][1])
        picked = sorted(set(range(0, len(profile), stride)) | {low, len(profile) - 1})
        profile = [profile[j] for j in picked]

    src = terrain.source
    out = {
        "source": src.to_dict(),
        "grid_spacing_m": _r1(terrain.spacing_m),
        "takeoff_ground_m": _r1(takeoff_ground),
        "ground_min_m": _r1(ground_min),
        "ground_max_m": _r1(ground_max),
        "relief_m": _r1(None if ground_min is None else ground_max - ground_min),
        "agl_min_m": _r1(agl_min),
        "agl_max_m": _r1(agl_max),
        "clearance_min_m": _r1(clear_min),
        "clearance_min_at": None if clear_at is None else list(map(_r7, frame.inverse(*clear_at))),
        "profile": [[round(d, 1), round(gr, 1), round(fl, 1)] for d, gr, fl in profile],
        "_missing": missing,
    }
    if agl_min is not None and agl_min > 0:
        out["gsd_min_cm"] = round(pg.gsd_m(camera, agl_min) * 100.0, 2)
        out["gsd_max_cm"] = round(pg.gsd_m(camera, agl_max) * 100.0, 2)
        out["side_overlap_min"] = round(pg.overlap_at(spacing, fp.across_m, altitude, agl_min), 1)
        out["front_overlap_min"] = round(pg.overlap_at(photo_sp, fp.along_m, altitude, agl_min), 1)
    else:
        out["gsd_min_cm"] = out["gsd_max_cm"] = None
        out["side_overlap_min"] = out["front_overlap_min"] = None
    return out, transit_min
