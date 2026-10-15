"""Plan checks: the selected aircraft's limits, the imagery, the terrain, the rules.

``evaluate`` turns the numbers the mission builder computed into findings
(``issues``) and into the short table that says, per topic, whether it was
checked, estimated or not looked at (``assurance``). See ``issues.py`` for
what severity and basis mean; this module only decides which apply.

Checks never guess in the comfortable direction. Where the platform has no
data (obstacles, weather, usually airspace) the finding is "not checked",
never silence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import airspace as airspace_mod
from . import photogrammetry as pg
from .issues import Assurance, Issue, error, info, warning
from .profiles import Camera, Drone, catalog_as_of
from .terrain import TerrainModel

# Photogrammetry rules of thumb (ODM asks for 65 % minimum, 72 %+ typical).
MIN_FRONT_OVERLAP = 65.0
MIN_SIDE_OVERLAP = 60.0
# Over the highest ground, below this the block may not reconstruct there.
MIN_EFFECTIVE_OVERLAP = 50.0
MAX_BLUR_PX = 1.0
# Typical rolling-shutter readout of a small-sensor camera; only used to
# decide whether to mention rolling-shutter correction.
ROLLING_READOUT_S = 0.03
ROLLING_SKEW_PX = 2.0
# Separation from the tallest obstacle the user declared.
OBSTACLE_ERROR_M = 5.0
OBSTACLE_WARNING_M = 15.0


@dataclass
class Facts:
    """Everything the checks need, gathered by ``mission.plan_mission``."""

    plan_type: str
    params: dict
    drone: Drone
    camera: Camera
    altitude_m: float
    footprint: pg.Footprint
    line_spacing_m: float
    photo_spacing_m: float
    photo_interval_s: float
    waypoint_count: int
    line_count: int
    passes: int | None
    photos: int
    max_alt_rel: float
    min_alt_rel: float
    rth_alt: float
    takeoff_assumed: bool
    takeoff_ground: float | None
    terrain: TerrainModel | None
    terrain_ok: bool
    following: bool
    terrain_stats: dict | None
    transit_clearance: float | None
    climb: dict
    furthest_m: float
    flights: list
    usable_s: float
    rule: dict
    overshoot_m: float
    zones: object = None
    boundary_lnglat: object = None
    takeoff_lnglat: tuple | None = None
    waypoints: list = field(default_factory=list)


def _fmt(v: float, digits: int = 0) -> str:
    return f"{v:.{digits}f}"


def evaluate(f: Facts) -> tuple[list[Issue], list[Assurance]]:
    issues: list[Issue] = []
    assurance: list[Assurance] = []
    _setup(f, issues)
    _capability(f, issues, assurance)
    _imagery(f, issues)
    _terrain(f, issues, assurance)
    _obstacles(f, issues, assurance)
    _battery(f, issues, assurance)
    _rules(f, issues, assurance)
    _airspace(f, issues, assurance)
    assurance.append(Assurance(
        "weather", "not_checked", "Wind and weather",
        "Not checked. Wind, gusts, temperature, precipitation and visibility are not considered; "
        + (f"the {f.drone.label} is rated for winds up to {f.drone.max_wind_ms:g} m/s. "
           if f.drone.max_wind_ms else "this aircraft has no published wind limit here. ")
        + "Headwind shortens endurance beyond the estimate."))
    order = {"error": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda i: order.get(i.severity, 3))
    return issues, assurance


# -- individual groups ---------------------------------------------------------

def _setup(f: Facts, issues: list[Issue]) -> None:
    if f.takeoff_assumed:
        issues.append(info(
            "TAKEOFF_ASSUMED", "geometry",
            "No takeoff point is set, so the plan assumes you launch at the first waypoint. Place the takeoff "
            "point where you will actually launch: altitudes are relative to it, and the transit and battery "
            "figures depend on it.", basis="note"))
    if f.params["gimbal_pitch_deg"] > -89.5:
        issues.append(info(
            "NADIR_ASSUMED", "photogrammetry",
            f"Gimbal pitch is {f.params['gimbal_pitch_deg']:g}°. Coverage, overlap and resolution are computed "
            "for a camera looking straight down; an oblique camera covers more ground ahead and less behind.", basis="note"))
    if f.plan_type == "corridor" and f.passes == 1:
        issues.append(info(
            "CORRIDOR_SINGLE_PASS", "photogrammetry",
            "One pass covers the corridor width. A single strip of photos reconstructs weakly (no side overlap); "
            "set the minimum passes to 2 or more if you need a reliable surface model.", basis="note"))


def _capability(f: Facts, issues: list[Issue], assurance: list[Assurance]) -> None:
    d, cam, p = f.drone, f.camera, f.params
    before = len(issues)

    if not d.waypoints:
        issues.append(warning(
            "NO_WAYPOINT_SUPPORT", "capability",
            f"The {d.label} has no autonomous waypoint flight. The plan can only serve as a reference for a "
            "manually flown mission; no flyable mission file can be exported for it."))
    elif d.formats and all(fm.level == "boundary" for fm in d.formats):
        apps = ", ".join(sorted({fm.app for fm in d.formats if fm.app})) or "the manufacturer's app"
        issues.append(info(
            "BOUNDARY_ONLY", "capability",
            f"{apps} plans its own flight lines from an imported boundary. The lines shown here are an "
            "estimate of that mission, not what the aircraft will fly."))

    speed = p["speed_ms"]
    limit = d.mission_speed_limit_ms
    if speed > limit + 1e-9:
        issues.append(error(
            "SPEED_ABOVE_MAX", "capability",
            f"Flight speed {speed:g} m/s is above the {d.label}'s limit of {limit:g} m/s for automated flight.",
            limit_ms=limit))
    if d.min_speed_ms and speed < d.min_speed_ms - 1e-9:
        issues.append(error(
            "SPEED_BELOW_MIN", "capability",
            f"Flight speed {speed:g} m/s is below the {d.label}'s minimum airspeed of {d.min_speed_ms:g} m/s.",
            limit_ms=d.min_speed_ms))
    transit = p["transit_speed_ms"]
    if transit and transit > d.max_speed_ms + 1e-9:
        issues.append(error(
            "TRANSIT_SPEED_ABOVE_MAX", "capability",
            f"Transit speed {transit:g} m/s is above the {d.label}'s top speed of {d.max_speed_ms:g} m/s.",
            limit_ms=d.max_speed_ms))

    min_interval = cam.effective_min_interval_s
    if f.photo_interval_s < min_interval - 1e-9:
        max_speed = f.photo_spacing_m / min_interval
        text = (f"At {speed:g} m/s the camera must take a photo every {f.photo_interval_s:.2f} s, but the "
                f"{cam.label} needs at least {min_interval:g} s between photos")
        fix = (f". Fly at {max_speed:.1f} m/s or slower, fly higher, or lower the front overlap; otherwise "
               "photos will be skipped and the overlap will have gaps.")
        if cam.interval_assumed:
            issues.append(warning(
                "PHOTO_INTERVAL_UNCERTAIN", "capability",
                text + " (assumed: its minimum interval is not published)" + fix, max_speed_ms=round(max_speed, 2)))
        else:
            issues.append(error("PHOTO_INTERVAL_TOO_SHORT", "capability", text + fix,
                                max_speed_ms=round(max_speed, 2)))

    if d.max_height_above_takeoff_m and f.max_alt_rel > d.max_height_above_takeoff_m + 1e-9:
        issues.append(error(
            "HEIGHT_ABOVE_TAKEOFF_LIMIT", "capability",
            f"The route climbs to {f.max_alt_rel:.0f} m above the takeoff point; the {d.label} is limited to "
            f"{d.max_height_above_takeoff_m:g} m.", limit_m=d.max_height_above_takeoff_m))
    if f.min_alt_rel < -0.5:
        issues.append(warning(
            "BELOW_TAKEOFF", "capability",
            f"Part of the route is {abs(f.min_alt_rel):.0f} m below the takeoff point. Not every flight app "
            "accepts negative heights, and the link to the aircraft may be lost below the launch site; consider "
            "launching from lower ground.", basis="estimated"))
    if d.service_ceiling_m and f.takeoff_ground is not None:
        top = f.takeoff_ground + f.max_alt_rel
        if top > d.service_ceiling_m:
            issues.append(error(
                "SERVICE_CEILING", "capability",
                f"The route reaches about {top:.0f} m above sea level; the {d.label}'s service ceiling is "
                f"{d.service_ceiling_m:g} m.", basis="estimated", limit_m=d.service_ceiling_m))
        elif top > d.service_ceiling_m * 0.8:
            issues.append(info(
                "HIGH_ALTITUDE", "capability",
                f"The route reaches about {top:.0f} m above sea level. Thin air reduces thrust and endurance "
                f"well before the {d.service_ceiling_m:g} m service ceiling.", basis="estimated"))

    if f.following and not d.per_waypoint_altitude:
        issues.append(warning(
            "TERRAIN_FOLLOW_BY_APP", "capability",
            f"The {d.label}'s flight app plans altitudes itself, so the heights here cannot be loaded into it. "
            "They estimate a terrain-following flight: switch terrain following on in the flight app, and check "
            "that it has elevation data for the site."))

    if f.following:
        up, down = f.climb["up"], f.climb["down"]
        max_up, max_down = d.max_ascent_ms, d.max_descent_ms
        worst = None
        if max_up and up > max_up + 1e-6:
            worst = ("climb", up, max_up)
        if max_down and down > max_down + 1e-6 and (worst is None or down / max_down > worst[1] / worst[2]):
            worst = ("descend", down, max_down)
        if worst:
            verb, need, cap = worst
            slower = p["speed_ms"] * cap / need
            issues.append(warning(
                "CLIMB_RATE", "capability",
                f"Following the terrain at {p['speed_ms']:g} m/s needs the aircraft to {verb} at {need:.1f} m/s; "
                f"the {d.label} manages {cap:g} m/s. Fly at {slower:.1f} m/s or slower, or loosen the terrain "
                "following tolerance.", basis="estimated", max_speed_ms=round(slower, 2)))

    if not d.can_hover:
        radius = d.min_turn_radius_m
        if radius and d.formats and any(fm.level != "boundary" for fm in d.formats):
            if f.overshoot_m < radius - 1e-6:
                issues.append(warning(
                    "FIXED_WING_OVERSHOOT", "capability",
                    f"A fixed-wing needs room to turn: the line overshoot ({f.overshoot_m:g} m) is shorter than "
                    f"the turn radius ({radius:g} m), so the aircraft will still be turning when it enters the area."))
            if f.line_spacing_m < 2 * radius and f.line_count > 1:
                issues.append(info(
                    "FIXED_WING_LINE_SPACING", "capability",
                    f"Flight lines are {f.line_spacing_m:.0f} m apart, tighter than the {2 * radius:g} m turn "
                    "diameter. The autopilot will fly wider turns than drawn; expect a longer flight than estimated."))

    if d.source == "builtin":
        source = "published nominal specifications in the built-in catalog" + (
            f" ({catalog_as_of()})" if catalog_as_of() else "")
    elif d.source == "snapshot":
        source = "the figures saved with this plan (the profile itself no longer exists)"
    else:
        source = "the custom profile your organization entered"
    failed = any(i.severity == "error" for i in issues[before:])
    assurance.append(Assurance(
        "capability", "checked", "Aircraft and camera limits",
        f"{'Limit exceeded' if failed else 'Within limits'}: speed, photo interval and height were compared with "
        f"the {d.label} and {cam.label} profiles, from {source}. The profile is not the aircraft: firmware, "
        "payload and conditions change what it can do."))


def _imagery(f: Facts, issues: list[Issue]) -> None:
    p, cam = f.params, f.camera
    if p["front_overlap"] < MIN_FRONT_OVERLAP or p["side_overlap"] < MIN_SIDE_OVERLAP:
        issues.append(warning(
            "LOW_OVERLAP", "photogrammetry",
            f"Overlap {p['front_overlap']:g}% front / {p['side_overlap']:g}% side is below what photogrammetry "
            f"usually needs ({MIN_FRONT_OVERLAP:g}% / {MIN_SIDE_OVERLAP:g}% at the very least; 75-80% / 65-70% "
            "is typical). Expect holes in the reconstruction."))
    shutter = 1.0 / p["shutter_speed"]
    blur = pg.motion_blur_px(p["speed_ms"], shutter, f.footprint.gsd_m)
    if blur > MAX_BLUR_PX:
        max_speed = MAX_BLUR_PX * f.footprint.gsd_m / shutter
        issues.append(warning(
            "MOTION_BLUR", "photogrammetry",
            f"At {p['speed_ms']:g} m/s and 1/{p['shutter_speed']:g} s the ground moves {blur:.1f} px during each "
            f"exposure. For sharp images keep it under {MAX_BLUR_PX:g} px: fly at {max_speed:.1f} m/s or slower, "
            "or use a faster shutter.", blur_px=round(blur, 2), max_speed_ms=round(max_speed, 2)))
    if cam.shutter == "electronic":
        skew = pg.motion_blur_px(p["speed_ms"], ROLLING_READOUT_S, f.footprint.gsd_m)
        if skew > ROLLING_SKEW_PX:
            issues.append(info(
                "ROLLING_SHUTTER", "photogrammetry",
                f"The {cam.label} has an electronic (rolling) shutter, so photos taken on the move are skewed. "
                "Turn on rolling-shutter correction when processing (ODM option rolling-shutter) or fly slower.",
                basis="estimated"))
    if cam.interval_assumed:
        issues.append(info(
            "INTERVAL_ASSUMED", "capability",
            f"The {cam.label}'s minimum photo interval is not published; {cam.effective_min_interval_s:g} s is "
            "assumed. Check it on the camera before relying on the speed."))


def _terrain(f: Facts, issues: list[Issue], assurance: list[Assurance]) -> None:
    p = f.params
    wants_follow = p["altitude_mode"] == "terrain"
    if not f.terrain_ok:
        reason = ("No elevation data is available for this plan" if f.terrain is None
                  else "The elevation data does not cover the takeoff point")
        if wants_follow:
            issues.append(error(
                "TERRAIN_DATA_MISSING", "terrain",
                f"{reason}, so terrain following cannot be planned. Choose another elevation source, move the "
                "takeoff point inside its coverage, or switch to constant altitude.", basis="not_checked"))
        assurance.append(Assurance(
            "terrain", "not_checked", "Terrain clearance",
            f"Not checked. {reason}; the plan treats the ground as flat and level with the takeoff point. "
            "Height above ground, resolution and overlap will differ wherever the ground rises or falls."))
        return

    t = f.terrain_stats
    src = f.terrain.source
    accuracy = src.vertical_accuracy_m
    acc_text = f"±{accuracy:g} m nominal" if accuracy else "accuracy not stated"
    res_text = f"{src.resolution_m:g} m data" if src.resolution_m else "data of unknown resolution"
    sampled = f", sampled every {t['grid_spacing_m']:.0f} m" if t.get("grid_spacing_m") else ""

    if t.get("missing_samples"):
        issues.append(warning(
            "TERRAIN_PARTIAL", "terrain",
            "The elevation data has gaps along the route; clearance and resolution are unknown there.",
            basis="estimated"))
    if src.resolution_m and t.get("grid_spacing_m") and t["grid_spacing_m"] > max(2.5 * src.resolution_m, 100.0):
        issues.append(info(
            "TERRAIN_COARSE", "terrain",
            f"The site is large for the elevation service: terrain was sampled every {t['grid_spacing_m']:.0f} m, "
            "so narrow ridges and gullies between samples are smoothed away.", basis="estimated"))

    min_clear = p["min_clearance_m"]
    clear = t.get("clearance_min_m")
    if clear is not None:
        if clear < min_clear:
            issues.append(error(
                "CLEARANCE_BELOW_MIN", "terrain",
                f"The route comes within an estimated {clear:.0f} m of the terrain, below your minimum clearance "
                f"of {min_clear:g} m. Fly higher, use terrain following, or move the takeoff point.",
                basis="estimated", clearance_m=clear, at=t.get("clearance_min_at")))
        elif accuracy and clear - accuracy < min_clear:
            issues.append(warning(
                "CLEARANCE_MARGIN", "terrain",
                f"Estimated terrain clearance is {clear:.0f} m. Allowing for the elevation data's {acc_text}, it "
                f"could be below your minimum of {min_clear:g} m.", basis="estimated", clearance_m=clear,
                at=t.get("clearance_min_at")))
    if f.transit_clearance is not None and f.transit_clearance < min_clear:
        issues.append(error(
            "TRANSIT_CLEARANCE", "terrain",
            f"Between the takeoff point and the survey area the aircraft would be within an estimated "
            f"{f.transit_clearance:.0f} m of the terrain (minimum {min_clear:g} m). Raise the return height, "
            "or take off closer to the area or from higher ground.", basis="estimated",
            clearance_m=round(f.transit_clearance, 1)))

    if not f.following and t.get("side_overlap_min") is not None:
        side, front = t["side_overlap_min"], t["front_overlap_min"]
        if side < MIN_EFFECTIVE_OVERLAP or front < MIN_EFFECTIVE_OVERLAP:
            issues.append(warning(
                "TERRAIN_OVERLAP", "terrain",
                f"The ground rises to within {t['agl_min_m']:.0f} m of the flight height. Over the highest ground "
                f"the overlap drops to about {max(front, 0):.0f}% front / {max(side, 0):.0f}% side and the "
                f"resolution ranges {t['gsd_min_cm']:g}-{t['gsd_max_cm']:g} cm/px. Use terrain following or fly "
                "higher.", basis="estimated"))
        elif t.get("relief_m") and t["relief_m"] > 0.25 * f.altitude_m:
            issues.append(info(
                "TERRAIN_RELIEF", "terrain",
                f"The terrain varies by {t['relief_m']:.0f} m under the route, so the resolution ranges "
                f"{t['gsd_min_cm']:g}-{t['gsd_max_cm']:g} cm/px at constant altitude. Terrain following would "
                "keep it even.", basis="estimated"))

    surface = (" It is a surface model: it includes vegetation and buildings as they were when it was captured."
               if src.surface == "surface" else
               " It models the ground only: trees, buildings, masts and wires are not in it.")
    assurance.append(Assurance(
        "terrain", "estimated", "Terrain clearance",
        f"Estimated from {src.label} ({res_text}, {acc_text}{sampled})."
        + (f" Lowest estimated height above it: {clear:.0f} m." if clear is not None else "") + surface
        + " Treat the clearance as indicative and confirm it on site."))


def _obstacles(f: Facts, issues: list[Issue], assurance: list[Assurance]) -> None:
    height = f.params["obstacle_height_m"] or 0.0
    t = f.terrain_stats
    lowest = t.get("clearance_min_m") if t else None
    basis = "estimated"
    if lowest is None:
        lowest = min(f.min_alt_rel, f.altitude_m)
    if height > 0:
        margin = lowest - height
        if margin < OBSTACLE_ERROR_M:
            issues.append(error(
                "OBSTACLE_CLEARANCE", "obstacles",
                f"The lowest part of the route is about {lowest:.0f} m above ground, only {margin:.0f} m above the "
                f"{height:g} m obstacle you entered. Fly higher.", basis=basis, margin_m=round(margin, 1)))
        elif margin < OBSTACLE_WARNING_M:
            issues.append(warning(
                "OBSTACLE_MARGIN", "obstacles",
                f"The lowest part of the route is about {lowest:.0f} m above ground, {margin:.0f} m above the "
                f"{height:g} m obstacle you entered. That is a thin margin for height and position errors.",
                basis=basis, margin_m=round(margin, 1)))
        assurance.append(Assurance(
            "obstacles", "not_checked", "Obstacles",
            f"Not checked against any obstacle data. The route was only compared with the tallest obstacle you "
            f"entered ({height:g} m). Anything taller, or not known to you, is not accounted for."))
    else:
        assurance.append(Assurance(
            "obstacles", "not_checked", "Obstacles",
            "Not checked. The platform has no data on trees, buildings, masts, cranes or power lines. Enter the "
            "height of the tallest obstacle you know of to compare the route with it, and inspect the site."))


def _battery(f: Facts, issues: list[Issue], assurance: list[Assurance]) -> None:
    d, p = f.drone, f.params
    usable_min = f.usable_s / 60.0
    over = [fl for fl in f.flights if fl.get("exceeds_endurance")]
    if over:
        worst = max(over, key=lambda fl: fl["duration_s"])
        issues.append(error(
            "LEG_EXCEEDS_ENDURANCE", "battery",
            f"One flight line with its transit takes about {worst['duration_s'] / 60:.0f} min, more than the "
            f"{usable_min:.0f} min usable on one battery. Fly faster, shorten the lines (change the flight "
            "direction or split the area) or take off closer.", basis="estimated"))
    elif len(f.flights) > 1:
        issues.append(info(
            "MULTIPLE_FLIGHTS", "battery",
            f"The mission needs about {len(f.flights)} flights ({len(f.flights)} batteries), each kept within "
            f"{usable_min:.0f} min. It is split at flight-line ends.", basis="estimated"))
    assurance.append(Assurance(
        "battery", "estimated", "Flight time and batteries",
        f"Estimated: {d.max_flight_time_min:g} min rated endurance x {d.endurance_factor:g} (payload and real "
        f"conditions) with {p['reserve_percent']:g}% held in reserve gives {usable_min:.0f} min usable per "
        "battery. Wind, temperature, battery age and hovering are not modelled."))


def _rules(f: Facts, issues: list[Issue], assurance: list[Assurance]) -> None:
    p, rule, d = f.params, f.rule, f.drone
    label = rule.get("label") or "the selected preset"
    limit = p["max_agl_m"] if p["max_agl_m"] is not None else rule.get("max_agl_m")
    t = f.terrain_stats
    highest = t.get("agl_max_m") if t and t.get("agl_max_m") is not None else f.max_alt_rel
    flat = t is None
    if limit and highest > limit + 0.05:
        issues.append(warning(
            "AGL_LIMIT", "regulation",
            f"The route reaches about {highest:.0f} m above ground{' (assuming flat ground)' if flat else ''}, "
            f"over the {limit:g} m limit of {label}. That needs an authorisation or a lower flight.",
            basis="advisory", limit_m=limit))
    vlos = p["vlos_m"] if p["vlos_m"] is not None else rule.get("vlos_advisory_m")
    if vlos and f.furthest_m > vlos:
        issues.append(warning(
            "VLOS_DISTANCE", "regulation",
            f"The route goes {f.furthest_m:.0f} m from the takeoff point, beyond the {vlos:g} m used here as a "
            "visual-line-of-sight distance. Plan observers, a closer takeoff point or the required authorisation.",
            basis="advisory", limit_m=vlos))
    if rule.get("max_speed_ms") and p["speed_ms"] > rule["max_speed_ms"]:
        issues.append(warning(
            "RULE_SPEED", "regulation",
            f"Flight speed {p['speed_ms']:g} m/s is above the {rule['max_speed_ms']:g} m/s limit of {label}.",
            basis="advisory"))
    if rule.get("max_takeoff_mass_g") and d.takeoff_mass_g and d.takeoff_mass_g > rule["max_takeoff_mass_g"]:
        issues.append(warning(
            "RULE_MASS", "regulation",
            f"The {d.label} ({d.takeoff_mass_g / 1000:g} kg) is over the {rule['max_takeoff_mass_g'] / 1000:g} kg "
            f"limit of {label}.", basis="advisory"))
    parts = []
    if limit:
        parts.append(f"{limit:g} m above ground")
    if vlos:
        parts.append(f"{vlos:g} m from the takeoff point")
    compared = " and ".join(parts) if parts else "no numeric limits"
    assurance.append(Assurance(
        "regulation", "advisory", "Aviation rules",
        f"Advisory: compared with {compared} ({label}). This is not legal advice and not an authorisation: "
        "registration, pilot competency, flights over people, night operations, permits and local rules are "
        "not evaluated."))


def _airspace(f: Facts, issues: list[Issue], assurance: list[Assurance]) -> None:
    zones = f.zones
    if not isinstance(zones, airspace_mod.ZoneSet) or not zones.zones:
        assurance.append(Assurance(
            "airspace", "not_checked", "Airspace",
            "Not checked. The platform holds no airspace data: controlled airspace, aerodromes, restricted and "
            "prohibited areas, temporary restrictions and NOTAMs must be checked with your aviation authority's "
            "official sources before every flight."))
        return
    shape = f.boundary_lnglat or []
    rings = [list(shape[0])] if f.plan_type == "area" and shape else []
    route = [(wp["lng"], wp["lat"]) for wp in f.waypoints]
    hits = airspace_mod.zones_touched(zones, boundary_rings=rings, route=route, takeoff=f.takeoff_lnglat)
    for z in hits[:10]:
        limits = ""
        if z.lower_m is not None or z.upper_m is not None:
            limits = f" ({'from ' + format(z.lower_m, 'g') + ' m ' if z.lower_m is not None else ''}" \
                     f"{'up to ' + format(z.upper_m, 'g') + ' m' if z.upper_m is not None else ''})".replace(" )", ")")
        issues.append(warning(
            "AIRSPACE_ZONE", "airspace",
            f"The plan touches the zone \"{z.name}\" [{z.kind}]{limits}. {z.message}".strip(),
            basis="advisory", zone=z.to_dict()))
    if len(hits) > 10:
        issues.append(warning("AIRSPACE_ZONE_MORE", "airspace",
                              f"The plan touches {len(hits) - 10} more zones.", basis="advisory"))
    assurance.append(Assurance(
        "airspace", "advisory", "Airspace",
        f"Advisory: compared with {zones.describe()} supplied by your platform operator; "
        f"{'it touches ' + str(len(hits)) + ' zone' + ('' if len(hits) == 1 else 's') if hits else 'no zone in that file is touched'}. "
        "The file may be incomplete or out of date and holds no temporary restrictions or NOTAMs. "
        "This is not a clearance: check your aviation authority's official sources."))
