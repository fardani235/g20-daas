"""Flight parameters: one table of names, types, ranges and defaults.

The planner, the API and the form all read this table, so a parameter cannot
mean one range to the user interface and another to the planner. Ranges here
are *sanity* bounds (what is a meaningful number at all); what a particular
aircraft can do is checked later, against its profile, in ``checks``.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from functools import lru_cache

from .photogrammetry import ORIENTATIONS
from .profiles import CATALOG_DIR, Drone
from .survey import START_CHOICES

ALTITUDE_MODES = ("constant", "terrain")
ALTITUDE_SOURCES = ("altitude", "gsd")
FINISH_ACTIONS = ("rth", "hover", "land")
PLAN_TYPES = ("area", "corridor")


class PlanError(ValueError):
    """The plan definition is unusable. ``field`` names the offending input when there is one."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class Spec:
    name: str
    kind: str                     # "number" | "int" | "choice" | "bool" | "direction"
    default: object = None
    low: float | None = None
    high: float | None = None
    choices: tuple = ()
    unit: str = ""
    label: str = ""
    help: str = ""

    def to_dict(self) -> dict:
        d = {"name": self.name, "kind": self.kind, "default": self.default, "unit": self.unit,
             "label": self.label, "help": self.help}
        if self.low is not None:
            d["min"] = self.low
        if self.high is not None:
            d["max"] = self.high
        if self.choices:
            d["choices"] = list(self.choices)
        return d


@lru_cache(maxsize=1)
def rule_sets() -> dict[str, dict]:
    with open(os.path.join(CATALOG_DIR, "regulations.json"), encoding="utf-8") as f:
        return {r["id"]: r for r in json.load(f)["rule_sets"]}


DEFAULT_RULE_SET = "generic-120m"

SPECS: tuple[Spec, ...] = (
    Spec("altitude_from", "choice", "altitude", choices=ALTITUDE_SOURCES, label="Plan by",
         help="Enter the flight height and read the resolution, or enter the resolution you need and read the height."),
    Spec("altitude_m", "number", 100.0, 5, 2000, unit="m", label="Flight height",
         help="Height above the takeoff point (constant altitude) or above the ground (terrain following)."),
    Spec("gsd_cm", "number", None, 0.1, 200, unit="cm/px", label="Ground resolution (GSD)",
         help="Size of one image pixel on the ground."),
    Spec("speed_ms", "number", None, 0.5, 60, unit="m/s", label="Flight speed",
         help="Speed along the flight lines. Defaults to the aircraft's suggested mapping speed."),
    Spec("front_overlap", "number", 80.0, 10, 95, unit="%", label="Front overlap",
         help="Overlap between consecutive photos along a flight line."),
    Spec("side_overlap", "number", 70.0, 10, 95, unit="%", label="Side overlap",
         help="Overlap between neighbouring flight lines."),
    Spec("direction", "direction", "auto", 0, 360, unit="°", label="Flight direction",
         help="Compass bearing of the flight lines. Auto picks the direction that needs the fewest lines."),
    Spec("overshoot_m", "number", None, 0, 1000, unit="m", label="Line overshoot",
         help="How far each line continues past the boundary before turning. Automatic for the airframe when empty."),
    Spec("crosshatch", "bool", False, label="Double grid",
         help="Fly a second grid at right angles to the first (better 3D reconstruction, about twice the flight)."),
    Spec("corridor_min_passes", "int", 1, 1, 9, label="Minimum passes",
         help="Corridor only: fly at least this many parallel passes even if one swath covers the width."),
    Spec("start", "choice", "auto", choices=START_CHOICES, label="Start corner",
         help="Auto starts at the corner nearest the takeoff point."),
    Spec("altitude_mode", "choice", "constant", choices=ALTITUDE_MODES, label="Altitude mode",
         help="Constant height above the takeoff point, or follow the terrain to keep the height above ground."),
    Spec("terrain_tolerance_m", "number", None, 0.5, 50, unit="m", label="Terrain following tolerance",
         help="How far the flown height may differ from the ideal one between waypoints. Smaller follows more closely with more waypoints."),
    Spec("camera_orientation", "choice", "landscape", choices=ORIENTATIONS, label="Camera orientation",
         help="Landscape puts the long side of the image across the flight line."),
    Spec("gimbal_pitch_deg", "number", -90.0, -90, 0, unit="°", label="Gimbal pitch",
         help="-90 looks straight down. Coverage estimates assume straight down."),
    Spec("shutter_speed", "number", 1000.0, 30, 16000, unit="1/s", label="Shutter speed",
         help="Exposure time as 1/x s; used to estimate motion blur at the chosen speed."),
    Spec("finish_action", "choice", "rth", choices=FINISH_ACTIONS, label="When finished",
         help="What the aircraft does after the last waypoint."),
    Spec("rth_altitude_m", "number", None, 5, 2000, unit="m", label="Return height",
         help="Height above the takeoff point for the return flight. Defaults to the highest point of the mission."),
    Spec("transit_speed_ms", "number", None, 0.5, 60, unit="m/s", label="Transit speed",
         help="Speed to and from the survey area. Defaults to the mapping speed."),
    Spec("reserve_percent", "number", 25.0, 0, 60, unit="%", label="Battery reserve",
         help="Share of each battery kept in hand for the return and landing."),
    Spec("min_clearance_m", "number", 20.0, 0, 500, unit="m", label="Minimum terrain clearance",
         help="The plan is flagged where the estimated height above the terrain model falls below this."),
    Spec("obstacle_height_m", "number", 0.0, 0, 1000, unit="m", label="Tallest known obstacle",
         help="Height above ground of the tallest obstacle you know of in or near the area (trees, masts, cranes)."),
    Spec("regulation", "choice", DEFAULT_RULE_SET, choices=tuple(rule_sets()), label="Rule preset",
         help="Numeric limits the plan is compared with. Not legal advice."),
    Spec("max_agl_m", "number", None, 1, 5000, unit="m", label="Height limit above ground",
         help="Overrides the preset's limit, for example under a specific authorisation."),
    Spec("vlos_m", "number", None, 10, 100000, unit="m", label="Line-of-sight distance",
         help="Flag the plan when it goes further than this from the takeoff point."),
)

SPEC_BY_NAME = {s.name: s for s in SPECS}


def _num(spec: Spec, value) -> float:
    if isinstance(value, bool):
        raise PlanError(f"{spec.label or spec.name} must be a number", spec.name)
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise PlanError(f"{spec.label or spec.name} must be a number", spec.name)
    if not math.isfinite(v):
        raise PlanError(f"{spec.label or spec.name} must be a finite number", spec.name)
    if spec.low is not None and v < spec.low:
        raise PlanError(f"{spec.label or spec.name} must be at least {spec.low:g} {spec.unit}".strip(), spec.name)
    if spec.high is not None and v > spec.high:
        raise PlanError(f"{spec.label or spec.name} must be at most {spec.high:g} {spec.unit}".strip(), spec.name)
    return v


def normalize_params(raw: dict | None, drone: Drone | None = None) -> dict:
    """Fill defaults, coerce types and enforce the sanity ranges. Raises ``PlanError``.

    Unknown keys are ignored so an older saved plan (or a newer client)
    never breaks the planner. ``None`` / ``""`` mean "use the default".
    """
    raw = raw or {}
    if not isinstance(raw, dict):
        raise PlanError("params must be an object", "params")
    out: dict = {}
    for spec in SPECS:
        value = raw.get(spec.name)
        if value is None or value == "":
            out[spec.name] = spec.default
            continue
        if spec.kind == "number":
            out[spec.name] = _num(spec, value)
        elif spec.kind == "int":
            v = _num(spec, value)
            if v != int(v):
                raise PlanError(f"{spec.label or spec.name} must be a whole number", spec.name)
            out[spec.name] = int(v)
        elif spec.kind == "bool":
            out[spec.name] = value in (True, 1, "1", "true", "True", "yes", "on")
        elif spec.kind == "choice":
            v = str(value).strip()
            if v not in spec.choices:
                raise PlanError(f"{spec.label or spec.name} must be one of: {', '.join(map(str, spec.choices))}",
                                spec.name)
            out[spec.name] = v
        elif spec.kind == "direction":
            if isinstance(value, str) and value.strip().lower() == "auto":
                out[spec.name] = "auto"
            else:
                out[spec.name] = _num(spec, value) % 180.0
    if out["speed_ms"] is None:
        out["speed_ms"] = float(drone.cruise_speed_ms) if drone and drone.cruise_speed_ms else 8.0
    if out["altitude_from"] == "gsd" and out["gsd_cm"] is None:
        raise PlanError("Enter the ground resolution to plan by", "gsd_cm")
    return out


def specs_as_dicts() -> list[dict]:
    return [s.to_dict() for s in SPECS]
