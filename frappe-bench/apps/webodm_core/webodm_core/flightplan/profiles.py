"""Drone and camera profiles: the equipment data the planner reads, kept apart from the planner.

A profile is plain data. Built-in profiles live in ``catalog/*.json`` (adding
a manufacturer or model is a data change, no code); an organization's own
profiles are stored as documents and pass through the same ``normalize_*``
functions, so the planner, the validator and the exporters never know or
care where a profile came from.

No Frappe dependency.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache

CATALOG_DIR = os.path.join(os.path.dirname(__file__), "catalog")

AIRFRAMES = ("multirotor", "fixed-wing", "vtol")
SHUTTERS = ("mechanical", "global", "electronic")
RTK = ("none", "optional", "standard")
# How well a mission file format is supported for an aircraft; see drones.json "about".
FORMAT_LEVELS = ("official", "community", "boundary")
WPML_SOURCES = ("dji-docs", "community")

# Assumed when a camera's minimum photo interval is not published. Deliberately
# on the slow side: an optimistic value would let a plan fly too fast for the
# camera and leave gaps in the overlap.
DEFAULT_MIN_INTERVAL_S = 2.0

ANY_CAMERA = "*"


class ProfileError(ValueError):
    """A drone or camera profile is malformed. ``field`` names the offending key."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class Camera:
    id: str
    manufacturer: str
    name: str
    sensor_width_mm: float
    sensor_height_mm: float
    image_width_px: int
    image_height_px: int
    focal_length_mm: float
    min_interval_s: float | None = None
    shutter: str = "electronic"
    notes: str = ""
    wpml: dict | None = None
    source: str = "builtin"

    @property
    def label(self) -> str:
        return f"{self.manufacturer} {self.name}".strip()

    @property
    def megapixels(self) -> float:
        return self.image_width_px * self.image_height_px / 1e6

    @property
    def effective_min_interval_s(self) -> float:
        return self.min_interval_s if self.min_interval_s else DEFAULT_MIN_INTERVAL_S

    @property
    def interval_assumed(self) -> bool:
        return not self.min_interval_s

    def to_dict(self) -> dict:
        d = asdict(self)
        d["label"] = self.label
        return d


@dataclass(frozen=True)
class MissionFormat:
    id: str
    level: str
    app: str = ""
    note: str = ""
    trigger: str | None = None
    max_waypoints: int | None = None


@dataclass(frozen=True)
class Drone:
    id: str
    manufacturer: str
    model: str
    airframe: str
    max_flight_time_min: float
    max_speed_ms: float
    endurance_factor: float = 0.8
    max_mission_speed_ms: float | None = None
    min_speed_ms: float | None = None
    cruise_speed_ms: float | None = None
    max_ascent_ms: float | None = None
    max_descent_ms: float | None = None
    max_wind_ms: float | None = None
    service_ceiling_m: float | None = None
    max_height_above_takeoff_m: float | None = None
    takeoff_mass_g: float | None = None
    min_turn_radius_m: float | None = None
    rtk: str = "none"
    cameras: tuple[str, ...] = ()
    default_camera: str | None = None
    waypoints: bool = True
    per_waypoint_altitude: bool = True
    formats: tuple[MissionFormat, ...] = ()
    wpml: dict | None = None
    notes: str = ""
    source: str = "builtin"

    @property
    def label(self) -> str:
        return f"{self.manufacturer} {self.model}".strip()

    @property
    def mission_speed_limit_ms(self) -> float:
        return self.max_mission_speed_ms or self.max_speed_ms

    @property
    def can_hover(self) -> bool:
        return self.airframe == "multirotor"

    def format(self, format_id: str) -> MissionFormat | None:
        return next((f for f in self.formats if f.id == format_id), None)

    def accepts_camera(self, camera_id: str) -> bool:
        return ANY_CAMERA in self.cameras or camera_id in self.cameras

    def to_dict(self) -> dict:
        d = asdict(self)
        d["label"] = self.label
        d["cameras"] = list(self.cameras)
        d["mission"] = {
            "waypoints": d.pop("waypoints"),
            "per_waypoint_altitude": d.pop("per_waypoint_altitude"),
            "formats": [{k: v for k, v in f.items() if v not in (None, "")} for f in d.pop("formats")],
        }
        if d.get("wpml"):
            d["mission"]["wpml"] = d["wpml"]
        d.pop("wpml", None)
        return d


# -- normalisation -------------------------------------------------------------

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9:._-]{0,139}$")


def _text(data: dict, key: str, *, required: bool = True, max_len: int = 140, default: str = "") -> str:
    value = data.get(key)
    if value is None or str(value).strip() == "":
        if required:
            raise ProfileError(f"{key} is required", key)
        return default
    text = str(value).strip()
    if len(text) > max_len:
        raise ProfileError(f"{key} is longer than {max_len} characters", key)
    return text


def _number(data: dict, key: str, *, required: bool = True, low: float | None = None, high: float | None = None,
            exclusive_low: bool = False, default: float | None = None) -> float | None:
    value = data.get(key)
    if value is None or value == "":
        if required:
            raise ProfileError(f"{key} is required", key)
        return default
    if isinstance(value, bool):
        raise ProfileError(f"{key} must be a number", key)
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ProfileError(f"{key} must be a number", key)
    if not math.isfinite(number):
        raise ProfileError(f"{key} must be a finite number", key)
    if low is not None and (number <= low if exclusive_low else number < low):
        raise ProfileError(f"{key} must be {'greater than' if exclusive_low else 'at least'} {low:g}", key)
    if high is not None and number > high:
        raise ProfileError(f"{key} must be at most {high:g}", key)
    return number


def _choice(data: dict, key: str, choices: tuple[str, ...], default: str) -> str:
    value = data.get(key)
    if value is None or value == "":
        return default
    value = str(value).strip().lower()
    if value not in choices:
        raise ProfileError(f"{key} must be one of: {', '.join(choices)}", key)
    return value


def _wpml(data, keys: tuple[str, ...]) -> dict | None:
    if not data:
        return None
    if not isinstance(data, dict):
        raise ProfileError("wpml must be an object", "wpml")
    out = {}
    for key in keys:
        if data.get(key) is None:
            continue
        try:
            out[key] = int(data[key])
        except (TypeError, ValueError):
            raise ProfileError(f"wpml.{key} must be an integer", "wpml")
        if not (0 <= out[key] <= 99999):
            raise ProfileError(f"wpml.{key} is out of range", "wpml")
    if not out:
        return None
    source = str(data.get("source") or "community")
    out["source"] = source if source in WPML_SOURCES else "community"
    return out


def normalize_camera(data: dict, *, source: str = "custom") -> Camera:
    """Validate a camera profile dict and return a ``Camera``. Raises ``ProfileError``."""
    if not isinstance(data, dict):
        raise ProfileError("camera profile must be an object")
    cam_id = _text(data, "id")
    if not _ID_RE.match(cam_id):
        raise ProfileError("id may only contain lowercase letters, digits and : . _ -", "id")
    image_w = _number(data, "image_width_px", low=1, high=100000)
    image_h = _number(data, "image_height_px", low=1, high=100000)
    if image_w != int(image_w) or image_h != int(image_h):
        raise ProfileError("image size must be whole pixels", "image_width_px")
    interval = _number(data, "min_interval_s", required=False, low=0, high=120, exclusive_low=True)
    wpml = _wpml(data.get("wpml"), ("payload_enum", "payload_sub_enum"))
    if wpml and "payload_enum" not in wpml:
        wpml = None
    return Camera(
        id=cam_id,
        manufacturer=_text(data, "manufacturer", required=False, default=""),
        name=_text(data, "name"),
        sensor_width_mm=_number(data, "sensor_width_mm", low=0, high=200, exclusive_low=True),
        sensor_height_mm=_number(data, "sensor_height_mm", low=0, high=200, exclusive_low=True),
        image_width_px=int(image_w),
        image_height_px=int(image_h),
        focal_length_mm=_number(data, "focal_length_mm", low=0, high=2000, exclusive_low=True),
        min_interval_s=interval,
        shutter=_choice(data, "shutter", SHUTTERS, "electronic"),
        notes=_text(data, "notes", required=False, max_len=2000),
        wpml=wpml,
        source=source,
    )


def _formats(raw) -> tuple[MissionFormat, ...]:
    if raw in (None, ""):
        return ()
    if not isinstance(raw, (list, tuple)):
        raise ProfileError("mission.formats must be a list", "formats")
    out: list[MissionFormat] = []
    seen = set()
    for item in raw:
        if isinstance(item, str):
            item = {"id": item}
        if not isinstance(item, dict) or not item.get("id"):
            raise ProfileError("each mission format needs an id", "formats")
        fid = str(item["id"]).strip()
        if fid in seen:
            continue
        seen.add(fid)
        level = str(item.get("level") or "community").strip().lower()
        if level not in FORMAT_LEVELS:
            raise ProfileError(f"mission format level must be one of: {', '.join(FORMAT_LEVELS)}", "formats")
        max_wp = item.get("max_waypoints")
        if max_wp not in (None, ""):
            try:
                max_wp = int(max_wp)
            except (TypeError, ValueError):
                raise ProfileError("max_waypoints must be an integer", "formats")
            if max_wp < 2:
                raise ProfileError("max_waypoints must be at least 2", "formats")
        else:
            max_wp = None
        trigger = item.get("trigger")
        out.append(MissionFormat(
            id=fid, level=level, app=str(item.get("app") or "")[:140], note=str(item.get("note") or "")[:1000],
            trigger=str(trigger) if trigger else None, max_waypoints=max_wp,
        ))
    return tuple(out)


def normalize_drone(data: dict, *, source: str = "custom") -> Drone:
    """Validate a drone profile dict and return a ``Drone``. Raises ``ProfileError``."""
    if not isinstance(data, dict):
        raise ProfileError("drone profile must be an object")
    drone_id = _text(data, "id")
    if not _ID_RE.match(drone_id):
        raise ProfileError("id may only contain lowercase letters, digits and : . _ -", "id")
    mission = data.get("mission") or {}
    if not isinstance(mission, dict):
        raise ProfileError("mission must be an object", "mission")
    airframe = _choice(data, "airframe", AIRFRAMES, "multirotor")
    max_speed = _number(data, "max_speed_ms", low=0, high=120, exclusive_low=True)
    mission_speed = _number(data, "max_mission_speed_ms", required=False, low=0, high=120, exclusive_low=True)
    if mission_speed and mission_speed > max_speed:
        raise ProfileError("max_mission_speed_ms cannot exceed max_speed_ms", "max_mission_speed_ms")
    min_speed = _number(data, "min_speed_ms", required=False, low=0, high=120)
    if min_speed and min_speed > max_speed:
        raise ProfileError("min_speed_ms cannot exceed max_speed_ms", "min_speed_ms")
    cruise = _number(data, "cruise_speed_ms", required=False, low=0, high=120, exclusive_low=True)
    cameras = data.get("cameras") or []
    if isinstance(cameras, str):
        cameras = [c.strip() for c in cameras.split(",") if c.strip()]
    if not isinstance(cameras, (list, tuple)):
        raise ProfileError("cameras must be a list of camera ids", "cameras")
    cameras = tuple(str(c) for c in cameras)
    default_camera = data.get("default_camera") or None
    if default_camera and ANY_CAMERA not in cameras and default_camera not in cameras:
        raise ProfileError("default_camera must be one of the drone's cameras", "default_camera")
    waypoints = bool(mission.get("waypoints", True))
    formats = _formats(mission.get("formats"))
    wpml = _wpml(mission.get("wpml"), ("drone_enum", "drone_sub_enum"))
    if wpml and "drone_enum" not in wpml:
        wpml = None
    return Drone(
        id=drone_id,
        manufacturer=_text(data, "manufacturer", required=False, default=""),
        model=_text(data, "model"),
        airframe=airframe,
        max_flight_time_min=_number(data, "max_flight_time_min", low=0, high=1440, exclusive_low=True),
        endurance_factor=_number(data, "endurance_factor", required=False, low=0.1, high=1.0, default=0.8),
        max_speed_ms=max_speed,
        max_mission_speed_ms=mission_speed,
        min_speed_ms=min_speed or None,
        cruise_speed_ms=cruise,
        max_ascent_ms=_number(data, "max_ascent_ms", required=False, low=0, high=60, exclusive_low=True),
        max_descent_ms=_number(data, "max_descent_ms", required=False, low=0, high=60, exclusive_low=True),
        max_wind_ms=_number(data, "max_wind_ms", required=False, low=0, high=60, exclusive_low=True),
        service_ceiling_m=_number(data, "service_ceiling_m", required=False, low=0, high=20000, exclusive_low=True),
        max_height_above_takeoff_m=_number(data, "max_height_above_takeoff_m", required=False, low=0, high=20000,
                                           exclusive_low=True),
        takeoff_mass_g=_number(data, "takeoff_mass_g", required=False, low=0, high=1e6, exclusive_low=True),
        min_turn_radius_m=_number(data, "min_turn_radius_m", required=False, low=0, high=5000, exclusive_low=True),
        rtk=_choice(data, "rtk", RTK, "none"),
        cameras=cameras,
        default_camera=default_camera,
        waypoints=waypoints,
        per_waypoint_altitude=bool(mission.get("per_waypoint_altitude", waypoints)),
        formats=formats if waypoints else tuple(f for f in formats if f.level == "boundary"),
        wpml=wpml,
        notes=_text(data, "notes", required=False, max_len=2000),
        source=source,
    )


# -- flight app families -------------------------------------------------------

# What a custom drone's flight app can be given, as the handful of choices a
# user can actually answer. Each expands to the generic ``mission`` block, so
# nothing else in the planner knows these exist.
ECOSYSTEMS: tuple[dict, ...] = (
    {
        "id": "dji-enterprise", "label": "DJI Pilot 2 / FlightHub 2 (Enterprise)",
        "help": "Imports WPML route files (.kmz). Needs the aircraft's DJI WPML id.",
        "waypoints": True, "per_waypoint_altitude": True, "needs_wpml": True,
        "formats": [
            {"id": "dji-wpml", "level": "official", "app": "DJI Pilot 2 / FlightHub 2", "trigger": "distance"},
            {"id": "kml-boundary", "level": "boundary", "app": "DJI Pilot 2"},
        ],
    },
    {
        "id": "dji-fly", "label": "DJI Fly waypoint flight (unofficial file swap)",
        "help": "DJI Fly has no import; a WPML file replaces a saved mission on the controller. Needs the aircraft's DJI WPML id.",
        "waypoints": True, "per_waypoint_altitude": True, "needs_wpml": True,
        "formats": [
            {"id": "dji-wpml", "level": "community", "app": "DJI Fly (Waypoint Flight)", "trigger": "manual",
             "max_waypoints": 200},
        ],
    },
    {
        "id": "mavlink", "label": "PX4 / ArduPilot (QGroundControl, Mission Planner)",
        "help": "Loads QGroundControl plans and MAVLink waypoint lists.",
        "waypoints": True, "per_waypoint_altitude": True, "needs_wpml": False,
        "formats": [
            {"id": "qgc-plan", "level": "official", "app": "QGroundControl"},
            {"id": "mavlink-wpl", "level": "official", "app": "Mission Planner / QGroundControl"},
        ],
    },
    {
        "id": "litchi", "label": "Litchi (third-party app for DJI aircraft)",
        "help": "Imports waypoint CSV files through Litchi Mission Hub; 99 waypoints per mission.",
        "waypoints": True, "per_waypoint_altitude": True, "needs_wpml": False,
        "formats": [{"id": "litchi-csv", "level": "official", "app": "Litchi (Mission Hub import)"}],
    },
    {
        "id": "vendor", "label": "The manufacturer's app plans the flight (boundary import)",
        "help": "The app imports the survey boundary and lays out its own flight lines and altitudes.",
        "waypoints": True, "per_waypoint_altitude": False, "needs_wpml": False,
        "formats": [{"id": "kml-boundary", "level": "boundary", "app": "Manufacturer's flight app"}],
    },
    {
        "id": "manual", "label": "No autonomous flight (flown by hand)",
        "help": "No mission file can be exported; the plan is a reference.",
        "waypoints": False, "per_waypoint_altitude": False, "needs_wpml": False,
        "formats": [],
    },
)


def ecosystem(ecosystem_id: str | None) -> dict | None:
    return next((e for e in ECOSYSTEMS if e["id"] == ecosystem_id), None)


# -- built-in catalog ----------------------------------------------------------

def _load(name: str) -> dict:
    with open(os.path.join(CATALOG_DIR, name), encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def builtin_cameras() -> dict[str, Camera]:
    out: dict[str, Camera] = {}
    for raw in _load("cameras.json")["cameras"]:
        cam = normalize_camera(raw, source="builtin")
        if cam.id in out:
            raise ProfileError(f"duplicate camera id in catalog: {cam.id}", "id")
        out[cam.id] = cam
    return out


@lru_cache(maxsize=1)
def builtin_drones() -> dict[str, Drone]:
    cameras = builtin_cameras()
    out: dict[str, Drone] = {}
    for raw in _load("drones.json")["drones"]:
        drone = normalize_drone(raw, source="builtin")
        if drone.id in out:
            raise ProfileError(f"duplicate drone id in catalog: {drone.id}", "id")
        for cam_id in drone.cameras:
            if cam_id != ANY_CAMERA and cam_id not in cameras:
                raise ProfileError(f"{drone.id} references unknown camera {cam_id}", "cameras")
        out[drone.id] = drone
    return out


def catalog_as_of() -> str:
    return str(_load("drones.json").get("as_of") or "")


def cameras_for(drone: Drone, cameras: dict[str, Camera]) -> list[Camera]:
    """The cameras a drone can carry, its default first."""
    if ANY_CAMERA in drone.cameras:
        chosen = list(cameras.values())
    else:
        chosen = [cameras[c] for c in drone.cameras if c in cameras]
    chosen.sort(key=lambda c: (c.id != drone.default_camera, c.label.lower()))
    return chosen
