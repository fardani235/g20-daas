"""Flight planning endpoints.

The planner itself is the Frappe-free ``webodm_core.flightplan`` package;
this module is its only bridge to the platform: documents and permissions,
the organization's own equipment profiles, elevation data (a global
service, or a task's DSM / DTM through the geospatial service), caching,
site configuration and file downloads.

Every endpoint gates access explicitly (``check_permission``): ``frappe.get_doc``
enforces nothing on its own, and the org-scoping hooks in ``permissions.py``
only apply automatically to list queries. Endpoints that change something or
cause an outbound lookup accept POST only, so they are always covered by the
session's CSRF token.

Site configuration (all optional; see ``docs/flight-planning/configuration.md``):

``flightplan_geocoder``            ``nominatim`` (default) | ``none``
``flightplan_geocoder_url``        Nominatim-compatible base URL
``flightplan_elevation_provider``  ``opentopodata`` (default) | ``open-elevation`` | ``open-meteo`` | ``none``
``flightplan_elevation_url``       base URL of that provider (a self-hosted instance)
``flightplan_elevation_dataset``   dataset name (Open Topo Data)
``flightplan_elevation_resolution_m`` / ``flightplan_elevation_accuracy_m``  what to tell users about it
``flightplan_elevation_max_points``  grid nodes requested per site (default 400)
``flightplan_contact``             real contact address sent in the User-Agent of outbound requests
                                   (the public services refuse placeholder addresses)
``flightplan_airspace_file``       GeoJSON zone file (absolute, or relative to the site directory)
``flightplan_airspace_label``      what to call that file in the UI
"""

from __future__ import annotations

import hashlib
import json
import os
import time

import frappe
from frappe.utils import cint, flt, now_datetime

from webodm_core import tenancy
from webodm_core.flightplan import NOTICE, PLANNER_VERSION, PlanError, append_issue, plan_mission
from webodm_core.flightplan import airspace as airspace_mod
from webodm_core.flightplan import elevation as elevation_mod
from webodm_core.flightplan import exporters, geocode as geocode_mod
from webodm_core.flightplan import issues as issues_mod
from webodm_core.flightplan import profiles
from webodm_core.flightplan.httpjson import fetch_json
from webodm_core.flightplan.mission import LIMITS
from webodm_core.flightplan.params import PLAN_TYPES, rule_sets, specs_as_dicts
from webodm_core.flightplan.terrain import TerrainModel, grid_spec

PLAN = "WebODM Flight Plan"
DRONE = "WebODM Drone Profile"
CAMERA = "WebODM Camera Profile"
CUSTOM = "custom:"

ELEVATION_SOURCES = ("auto", "none")
TASK_DATASETS = {"dtm": ("Terrain model (DTM)", "terrain", "mean"), "dsm": ("Surface model (DSM)", "surface", "max")}

DEFAULT_MAX_POINTS = 400
TASK_MAX_POINTS = 10000
TASK_MIN_SPACING_M = 5.0
TERRAIN_CACHE_SECONDS = 30 * 24 * 3600
TASK_TERRAIN_CACHE_SECONDS = 24 * 3600
GEOCODE_CACHE_SECONDS = 24 * 3600
NOMINATIM_PUBLIC = "https://nominatim.openstreetmap.org"
OSM_ATTRIBUTION = "Search © OpenStreetMap contributors"


# -- small helpers --------------------------------------------------------------

def _obj(value, default=None):
    """A request argument that may arrive as an object or as its JSON text."""
    if value is None or value == "":
        return default
    if isinstance(value, (bytes, bytearray)):
        value = value.decode()
    if isinstance(value, str):
        try:
            return frappe.parse_json(value)
        except Exception:
            frappe.throw("Malformed JSON in request")
    return value


def _conf(key: str, default=None):
    value = frappe.conf.get(key)
    return default if value in (None, "") else value


def _user_agent() -> str:
    contact = _conf("flightplan_contact")
    site = getattr(frappe.local, "site", "") or "webodm"
    return f"WebODM-FlightPlanner/{PLANNER_VERSION} ({site}{'; ' + str(contact) if contact else ''})"


def _zero_none(value):
    """Frappe stores an unset Float / Int as 0; for these fields 0 means "unknown"."""
    v = flt(value)
    return v if v else None


def _over_budget(kind: str, limit: int, seconds: int = 60) -> bool:
    """Whether the session user has made more than ``limit`` outbound ``kind`` lookups in the window.

    The planner proxies lookups to services that are rate limited (and, when
    public, shared with everyone else); a script hammering the endpoint must
    not spend that budget. Answers served from the cache do not count.
    """
    cache = frappe.cache()
    key = f"flightplan:budget:{kind}:{frappe.session.user}"
    count = cint(cache.get_value(key)) + 1
    cache.set_value(key, count, expires_in_sec=seconds)
    return count > limit


GEOCODE_LOOKUPS_PER_MINUTE = 30
TERRAIN_FETCHES_PER_MINUTE = 20


# -- equipment: documents -> profiles -------------------------------------------

def camera_from_doc(doc) -> profiles.Camera:
    """The ``Camera`` a ``WebODM Camera Profile`` describes. Throws a validation error if it is not valid."""
    data = {
        "id": f"{CUSTOM}{doc.name or 'new'}",
        "manufacturer": doc.manufacturer or "",
        "name": doc.profile_name,
        "sensor_width_mm": doc.sensor_width_mm, "sensor_height_mm": doc.sensor_height_mm,
        "image_width_px": doc.image_width_px, "image_height_px": doc.image_height_px,
        "focal_length_mm": doc.focal_length_mm,
        "min_interval_s": _zero_none(doc.min_interval_s),
        "shutter": doc.shutter or "electronic",
        "notes": doc.notes or "",
        "wpml": {"payload_enum": cint(doc.wpml_payload_enum), "source": "community"} if cint(doc.wpml_payload_enum) else None,
    }
    try:
        return profiles.normalize_camera(data, source="custom")
    except profiles.ProfileError as e:
        frappe.throw(f"Camera profile: {e}", frappe.ValidationError)


def _formats_of(doc) -> list:
    raw = _obj(doc.mission_formats, [])
    if not isinstance(raw, list):
        frappe.throw("Drone profile: mission formats must be a list", frappe.ValidationError)
    return raw


def drone_from_doc(doc, cameras: dict | None = None) -> profiles.Drone:
    """The ``Drone`` a ``WebODM Drone Profile`` describes. Throws a validation error if it is not valid."""
    camera_ids = [c.strip() for c in (doc.cameras or "").replace("\n", ",").split(",") if c.strip()]
    data = {
        "id": f"{CUSTOM}{doc.name or 'new'}",
        "manufacturer": doc.manufacturer or "",
        "model": doc.model,
        "airframe": doc.airframe or "multirotor",
        "rtk": doc.rtk or "none",
        "max_flight_time_min": doc.max_flight_time_min,
        "endurance_factor": _zero_none(doc.endurance_factor),
        "max_speed_ms": doc.max_speed_ms,
        "max_mission_speed_ms": _zero_none(doc.max_mission_speed_ms),
        "min_speed_ms": _zero_none(doc.min_speed_ms),
        "cruise_speed_ms": _zero_none(doc.cruise_speed_ms),
        "max_ascent_ms": _zero_none(doc.max_ascent_ms),
        "max_descent_ms": _zero_none(doc.max_descent_ms),
        "max_wind_ms": _zero_none(doc.max_wind_ms),
        "service_ceiling_m": _zero_none(doc.service_ceiling_m),
        "max_height_above_takeoff_m": _zero_none(doc.max_height_above_takeoff_m),
        "takeoff_mass_g": _zero_none(doc.takeoff_mass_g),
        "min_turn_radius_m": _zero_none(doc.min_turn_radius_m),
        "cameras": camera_ids,
        "default_camera": doc.default_camera or (camera_ids[0] if camera_ids and camera_ids[0] != "*" else None),
        "notes": doc.notes or "",
        "mission": {
            "waypoints": bool(cint(doc.waypoints)),
            "per_waypoint_altitude": bool(cint(doc.per_waypoint_altitude)),
            "formats": _formats_of(doc),
            "wpml": ({"drone_enum": cint(doc.wpml_drone_enum), "drone_sub_enum": cint(doc.wpml_drone_sub_enum),
                      "source": "community"} if cint(doc.wpml_drone_enum) else None),
        },
    }
    try:
        drone = profiles.normalize_drone(data, source="custom")
    except profiles.ProfileError as e:
        frappe.throw(f"Drone profile: {e}", frappe.ValidationError)
    if not drone.cameras:
        frappe.throw("Drone profile: choose at least one camera this aircraft carries", frappe.ValidationError)
    if drone.airframe != "multirotor" and not drone.min_turn_radius_m:
        frappe.throw("Drone profile: a fixed-wing or VTOL aircraft needs its turn radius", frappe.ValidationError)
    known = cameras if cameras is not None else _all_cameras(doc.organization or tenancy.get_current_org())
    for cam_id in drone.cameras:
        if cam_id != profiles.ANY_CAMERA and cam_id not in known:
            frappe.throw(f"Drone profile: unknown camera {cam_id}", frappe.ValidationError)
    if drone.default_camera and drone.default_camera not in known:
        frappe.throw(f"Drone profile: unknown default camera {drone.default_camera}", frappe.ValidationError)
    for fmt in drone.formats:
        exporter = exporters.EXPORTERS.get(fmt.id)
        if exporter is None:
            frappe.throw(f"Drone profile: unknown mission format {fmt.id}", frappe.ValidationError)
        if (fmt.level == "boundary") != (exporter.kind == "boundary"):
            frappe.throw(f"Drone profile: {fmt.id} cannot have the support level '{fmt.level}'", frappe.ValidationError)
        if fmt.trigger and fmt.trigger not in exporter.triggers:
            frappe.throw(f"Drone profile: {fmt.id} cannot trigger photos by '{fmt.trigger}'", frappe.ValidationError)
    if drone.format("dji-wpml") and not drone.wpml:
        frappe.throw("Drone profile: a DJI WPML aircraft id is needed to export WPML routes", frappe.ValidationError)
    return drone


def _custom_cameras(org: str | None) -> dict[str, profiles.Camera]:
    if not org:
        return {}
    out = {}
    for name in frappe.get_all(CAMERA, filters={"organization": org}, pluck="name", order_by="creation asc"):
        try:
            cam = camera_from_doc(frappe.get_doc(CAMERA, name))
        except frappe.ValidationError:
            continue  # a row made invalid by hand must not take the catalog down
        out[cam.id] = cam
    return out


def _all_cameras(org: str | None) -> dict[str, profiles.Camera]:
    return {**profiles.builtin_cameras(), **_custom_cameras(org)}


def _custom_drones(org: str | None, cameras: dict) -> dict[str, profiles.Drone]:
    if not org:
        return {}
    out = {}
    for name in frappe.get_all(DRONE, filters={"organization": org}, pluck="name", order_by="creation asc"):
        try:
            drone = drone_from_doc(frappe.get_doc(DRONE, name), cameras)
        except frappe.ValidationError:
            continue
        out[drone.id] = drone
    return out


def _equipment(org: str | None) -> tuple[dict[str, profiles.Drone], dict[str, profiles.Camera]]:
    cameras = _all_cameras(org)
    return {**profiles.builtin_drones(), **_custom_drones(org, cameras)}, cameras


def _resolve_equipment(drone_id: str, camera_id: str | None, org: str | None, snapshot: dict | None = None):
    """The ``(Drone, Camera)`` for a plan.

    ``snapshot`` is the equipment stored with a saved plan; it is the
    fallback when a profile has since been deleted, so an old plan can still
    be opened and exported with what it was planned for.
    """
    drones, cameras = _equipment(org)
    drone = drones.get(drone_id)
    try:
        # A snapshot is re-validated and labelled as such: it is never passed
        # off as a catalog entry or as a current profile.
        if drone is None and snapshot and (snapshot.get("drone") or {}).get("id") == drone_id:
            drone = profiles.normalize_drone(snapshot["drone"], source="snapshot")
        if drone is None:
            raise PlanError("Choose the aircraft for this plan", "drone")
        camera_id = camera_id or drone.default_camera
        camera = cameras.get(camera_id)
        if camera is None and snapshot and (snapshot.get("camera") or {}).get("id") == camera_id:
            camera = profiles.normalize_camera(snapshot["camera"], source="snapshot")
    except profiles.ProfileError as e:
        raise PlanError(f"The equipment saved with this plan is no longer usable ({e}); choose it again", "drone")
    if camera is None:
        raise PlanError("Choose the camera for this plan", "camera")
    if not drone.accepts_camera(camera.id):
        raise PlanError(f"The {drone.label} does not carry the {camera.label}", "camera")
    return drone, camera


# -- elevation -------------------------------------------------------------------

def _global_provider():
    conf = {
        "provider": _conf("flightplan_elevation_provider"),
        "url": _conf("flightplan_elevation_url"),
        "dataset": _conf("flightplan_elevation_dataset"),
        "resolution_m": _conf("flightplan_elevation_resolution_m"),
        "vertical_accuracy_m": _conf("flightplan_elevation_accuracy_m"),
        "user_agent": _user_agent(),
    }
    return elevation_mod.provider_from_config(conf)


def _task_for_source(source: str, project: str | None):
    """``(task, dataset)`` of a ``task:<name>:<dsm|dtm>`` source, permission-checked."""
    parts = source.split(":")
    if len(parts) != 3 or parts[2] not in TASK_DATASETS:
        raise elevation_mod.ElevationError("unknown elevation source")
    if not frappe.db.exists("WebODM Task", parts[1]):
        raise elevation_mod.ElevationError("the survey chosen as elevation source no longer exists")
    task = frappe.get_doc("WebODM Task", parts[1])
    # A task of another organization (or project) must read exactly like a missing one.
    if not task.has_permission("read") or (project and task.project != project):
        raise elevation_mod.ElevationError("the survey chosen as elevation source no longer exists")
    if not task.get(parts[2]):
        raise elevation_mod.ElevationError(f"that survey has no {parts[2].upper()}")
    return task, parts[2]


def _task_provider(task, dataset: str, spacing: dict):
    from webodm_core.plugins import geospatial
    from webodm_core.storage import assets, cache

    label, surface, statistic = TASK_DATASETS[dataset]
    try:
        _kind, path = assets.raster_source(task, dataset)
    except (cache.CacheMiss, frappe.DoesNotExistError, frappe.PermissionError) as e:
        raise elevation_mod.ElevationError(f"that survey's {dataset.upper()} is not available") from e

    provider = elevation_mod.CallableProvider(
        provider_id=f"task:{task.name}:{dataset}",
        provider_label=f"{task.title or task.name} · {label}",
        provider_surface=surface,
        provider_note=(f"From this project's own survey, processed {frappe.utils.format_date(task.modified)}. "
                       "It shows the site as it was then; heights are relative within the survey, which is what "
                       "terrain following needs."),
    )

    def sample(points):
        try:
            data = geospatial.raster_sample(path, [[lng, lat] for lat, lng in points],
                                            spacing_m=spacing.get("m") or 30.0, statistic=statistic)
        except geospatial.GeospatialError as e:
            raise elevation_mod.ElevationError(str(e)) from e
        provider.resolution_m = data.get("resolution_m")
        return data["values"]

    provider.func = sample
    return provider


def _bounds(definition: dict) -> tuple[float, float, float, float] | None:
    geometry = definition.get("geometry") or {}
    coords = geometry.get("coordinates") or []
    if geometry.get("type") == "Polygon":
        pts = [p for ring in coords for p in ring]
    else:
        pts = list(coords)
    take = definition.get("takeoff")
    if isinstance(take, (list, tuple)) and len(take) >= 2:
        pts = [*pts, take]
    try:
        lngs = [float(p[0]) for p in pts]
        lats = [float(p[1]) for p in pts]
    except (TypeError, ValueError, IndexError):
        return None
    if not lngs or max(lngs) - min(lngs) > 5 or max(lats) - min(lats) > 5:
        return None  # nonsense, or across the antimeridian: no terrain rather than a bad grid
    return min(lngs), min(lats), max(lngs), max(lats)


def _terrain_for(definition: dict, project: str | None, fetch: bool):
    """``(TerrainModel | None, Issue | None, pending)`` for a plan definition.

    ``pending`` is true when the data is not cached and ``fetch`` is false:
    the caller asked for a fast answer and should ask again with ``fetch``.
    A failure to get elevation data is never fatal: the plan is computed
    without terrain and carries a finding that says so.
    """
    source = str(definition.get("elevation_source") or "auto")
    if source == "none":
        return None, None, False
    bounds = _bounds(definition)
    if bounds is None:
        return None, None, False
    try:
        width = flt(definition.get("corridor_width_m")) if definition.get("type") == "corridor" else 0.0
        pad = 80.0 + min(width, 5000.0) / 2.0
        spacing: dict = {}
        if source.startswith("task:"):
            task, dataset = _task_for_source(source, project)
            provider = _task_provider(task, dataset, spacing)
            spec = grid_spec(bounds, max_points=TASK_MAX_POINTS, min_spacing_m=TASK_MIN_SPACING_M, pad_m=pad)
            identity = [provider.provider_id, task.get(dataset)]
            ttl = TASK_TERRAIN_CACHE_SECONDS
        else:
            provider = _global_provider()
            if provider is None:
                return None, None, False
            max_points = cint(_conf("flightplan_elevation_max_points")) or DEFAULT_MAX_POINTS
            spec = grid_spec(bounds, max_points=max_points, min_spacing_m=provider.resolution_m or 30.0, pad_m=pad)
            identity = [provider.id, provider.url, provider.dataset]
            ttl = TERRAIN_CACHE_SECONDS
        spacing["m"] = spec.spacing_m
        key = "flightplan:terrain:" + hashlib.sha1(json.dumps(
            [identity, spec.south, spec.west, spec.north, spec.east, spec.rows, spec.cols]).encode()).hexdigest()
        cached = frappe.cache().get_value(key)
        if cached:
            return TerrainModel.from_dict(cached), None, False
        if not fetch:
            return None, None, True
        if _over_budget("terrain", TERRAIN_FETCHES_PER_MINUTE):
            raise elevation_mod.ElevationError("too many elevation requests in a minute; wait a moment")
        model = elevation_mod.build_terrain(provider, spec)
        frappe.cache().set_value(key, model.to_dict(), expires_in_sec=ttl)
        return model, None, False
    except elevation_mod.ElevationError as e:
        return None, issues_mod.warning(
            "TERRAIN_FETCH_FAILED", "terrain",
            f"Elevation data could not be loaded ({e}). The plan is computed as if the ground were flat; "
            "try again, or choose another elevation source.", basis="not_checked"), False


def _zones():
    """The operator's airspace zone file, parsed once per file version; ``None`` if not configured."""
    path = _conf("flightplan_airspace_file")
    if not path:
        return None
    if not os.path.isabs(path):
        path = frappe.get_site_path(path)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        frappe.log_error(f"flightplan_airspace_file not readable: {path}", "WebODM Flight Planning")
        return None
    cached = _ZONE_CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with open(path, encoding="utf-8") as f:
            zones = airspace_mod.load_zones(
                json.load(f), label=str(_conf("flightplan_airspace_label") or "Operator zone file"),
                as_of=time.strftime("%Y-%m-%d", time.localtime(mtime)))
    except (OSError, ValueError) as e:
        frappe.log_error(f"flightplan_airspace_file invalid: {e}", "WebODM Flight Planning")
        zones = None
    _ZONE_CACHE[path] = (mtime, zones)
    return zones


_ZONE_CACHE: dict = {}


# -- computing a plan ------------------------------------------------------------

def _definition(raw) -> dict:
    d = _obj(raw, {})
    if not isinstance(d, dict):
        frappe.throw("Plan definition must be an object")
    plan_type = d.get("type") or "area"
    if plan_type not in PLAN_TYPES:
        frappe.throw(f"Unknown plan type: {plan_type}")
    source = str(d.get("elevation_source") or "auto")
    if source not in ELEVATION_SOURCES and not source.startswith("task:"):
        frappe.throw(f"Unknown elevation source: {source}")
    return {
        "type": plan_type,
        "geometry": _obj(d.get("geometry")),
        "corridor_width_m": d.get("corridor_width_m"),
        "takeoff": _obj(d.get("takeoff")) or None,
        "params": _obj(d.get("params"), {}) or {},
        "drone": d.get("drone"),
        "camera": d.get("camera"),
        "elevation_source": source,
    }


def compute(definition: dict, project: str | None, *, fetch: bool = True, snapshot: dict | None = None,
            org: str | None = None) -> dict:
    """Mission for a normalised ``definition``. Raises ``PlanError`` when it cannot be laid out."""
    org = org or tenancy.get_current_org()
    drone, camera = _resolve_equipment(definition.get("drone"), definition.get("camera"), org, snapshot)
    terrain, problem, pending = _terrain_for(definition, project, fetch)
    mission = plan_mission(definition, drone, camera, terrain=terrain, zones=_zones())
    if problem:
        append_issue(mission, problem)
    mission["terrain_pending"] = pending
    mission["elevation_source"] = definition.get("elevation_source") or "auto"
    return mission


def _doc_definition(doc) -> dict:
    return {
        "type": doc.plan_type or "area",
        "geometry": _obj(doc.geometry),
        "corridor_width_m": flt(doc.corridor_width_m) or None,
        "takeoff": _obj(doc.takeoff) or None,
        "params": _obj(doc.parameters, {}) or {},
        "drone": doc.drone,
        "camera": doc.camera,
        "elevation_source": doc.elevation_source or "auto",
    }


def _store(doc, definition: dict, mission: dict) -> None:
    doc.plan_type = definition["type"]
    doc.geometry = frappe.as_json(definition["geometry"])
    doc.corridor_width_m = flt(definition.get("corridor_width_m")) if definition["type"] == "corridor" else 0
    doc.takeoff = frappe.as_json(definition["takeoff"]) if definition.get("takeoff") else None
    # Stored as given by the user (not the defaults-filled copy), so a default
    # that changes later applies to a plan that never set the value.
    doc.parameters = frappe.as_json({k: v for k, v in (definition.get("params") or {}).items()
                                     if v is not None and v != ""})
    doc.drone = mission["equipment"]["drone"]["id"]
    doc.camera = mission["equipment"]["camera"]["id"]
    doc.elevation_source = definition.get("elevation_source") or "auto"
    stats, summary = mission["stats"], mission["summary"]
    doc.drone_label = mission["equipment"]["drone"]["label"]
    doc.camera_label = mission["equipment"]["camera"]["label"]
    doc.gsd_cm = mission["camera"]["gsd_cm"]
    doc.area_m2 = stats["area_m2"]
    doc.distance_m = stats["total_distance_m"]
    doc.duration_s = stats["total_duration_s"]
    doc.photo_count = stats["photo_count"]
    doc.batteries = stats["batteries"]
    doc.waypoint_count = stats["waypoint_count"]
    doc.issue_errors = summary["error"]
    doc.issue_warnings = summary["warning"]
    doc.planner_version = mission["version"]
    doc.computed_at = now_datetime()
    doc.mission = json.dumps(mission, separators=(",", ":"))


def compute_into(doc) -> None:
    """Recompute ``doc``'s mission from its definition fields (the controller's ``validate``)."""
    definition = _doc_definition(doc)
    snapshot = (_obj(doc.mission, {}) or {}).get("equipment")
    try:
        mission = compute(definition, doc.project, snapshot=snapshot, org=doc.organization)
    except PlanError as e:
        frappe.throw(str(e), frappe.ValidationError)
    _store(doc, definition, mission)


def _mission_of(doc) -> dict:
    mission = _obj(doc.mission, {})
    return mission if isinstance(mission, dict) else {}


def _plan_row(doc) -> dict:
    return {
        "name": doc.name, "title": doc.title, "description": doc.get("description") or "", "project": doc.project,
        "plan_type": doc.plan_type, "drone": doc.drone, "camera": doc.camera,
        "drone_label": doc.drone_label, "camera_label": doc.camera_label,
        "gsd_cm": flt(doc.gsd_cm), "area_m2": flt(doc.area_m2), "distance_m": flt(doc.distance_m),
        "duration_s": flt(doc.duration_s), "photo_count": cint(doc.photo_count), "batteries": cint(doc.batteries),
        "waypoint_count": cint(doc.waypoint_count), "issue_errors": cint(doc.issue_errors),
        "issue_warnings": cint(doc.issue_warnings), "computed_at": str(doc.computed_at or ""),
        "creation": str(doc.creation or ""), "modified": str(doc.modified or ""),
        "created_by": doc.get("created_by") or doc.get("owner"),
    }


def _plan_payload(doc) -> dict:
    out = _plan_row(doc)
    out["project_title"] = frappe.db.get_value("WebODM Project", doc.project, "title") or doc.project
    out["definition"] = _doc_definition(doc)
    out["mission"] = _mission_of(doc)
    return out


def _get_plan_checked(name: str, ptype: str = "read"):
    if not name:
        frappe.throw("plan is required")
    doc = frappe.get_doc(PLAN, name)
    doc.check_permission(ptype)
    return doc


def _project_checked(project: str):
    if not project:
        frappe.throw("project is required")
    doc = frappe.get_doc("WebODM Project", project)
    doc.check_permission("read")
    return doc


# -- endpoints: catalog, search, sources ------------------------------------------

@frappe.whitelist(allow_guest=False)
def catalog():
    """Everything the planner form needs: equipment, parameters, formats, presets and what is configured."""
    org = tenancy.get_current_org()
    drones, cameras = _equipment(org)
    provider = None
    try:
        provider = _global_provider()
    except elevation_mod.ElevationError:
        provider = None
    geocoder = str(_conf("flightplan_geocoder", "nominatim")).lower()
    return {
        "drones": [_drone_entry(d, cameras) for d in drones.values()],
        "cameras": [_camera_entry(c) for c in cameras.values()],
        "formats": exporters.formats_catalog(),
        "ecosystems": [dict(e) for e in profiles.ECOSYSTEMS],
        "params": specs_as_dicts(),
        "rule_sets": list(rule_sets().values()),
        "elevation": {
            "enabled": provider is not None,
            "label": provider.describe() if provider else "",
            "resolution_m": provider.resolution_m if provider else None,
            "vertical_accuracy_m": provider.vertical_accuracy_m if provider else None,
            "note": provider.note if provider else "",
        },
        "geocoder": {"enabled": geocoder not in ("none", "off", "disabled"),
                     "attribution": OSM_ATTRIBUTION if geocoder == "nominatim" else ""},
        "airspace": {"configured": _zones() is not None,
                     "label": (_zones().describe() if _zones() else "")},
        "limits": {"max_vertices": LIMITS.max_vertices, "max_extent_m": LIMITS.max_extent_m},
        "catalog_as_of": profiles.catalog_as_of(),
        "can_edit_profiles": bool(org),
        "notice": NOTICE,
    }


def _drone_entry(drone: profiles.Drone, cameras: dict) -> dict:
    d = drone.to_dict()
    d["editable"] = drone.source == "custom"
    d["camera_choices"] = [c.id for c in profiles.cameras_for(drone, cameras)]
    if drone.source == "custom":
        d["docname"] = drone.id[len(CUSTOM):]
        d["ecosystem"] = frappe.db.get_value(DRONE, d["docname"], "ecosystem")
    return d


def _camera_entry(camera: profiles.Camera) -> dict:
    c = camera.to_dict()
    c["editable"] = camera.source == "custom"
    if camera.source == "custom":
        c["docname"] = camera.id[len(CUSTOM):]
    return c


@frappe.whitelist(allow_guest=False)
def elevation_sources(project=None):
    """Elevation sources a plan in ``project`` can use: the global model, the project's own surveys, none."""
    out = []
    try:
        provider = _global_provider()
    except elevation_mod.ElevationError:
        provider = None
    if provider:
        out.append({"id": "auto", "label": provider.describe(), "kind": "global-dem",
                    "resolution_m": provider.resolution_m, "vertical_accuracy_m": provider.vertical_accuracy_m,
                    "surface": provider.surface, "note": provider.note})
    if project:
        _project_checked(project)
        tasks = frappe.get_list("WebODM Task", filters={"project": project, "status": "Completed"},
                                fields=["name", "title", "dsm", "dtm", "modified"], order_by="modified desc",
                                limit_page_length=50)
        for t in tasks:
            for dataset, (label, surface, _stat) in TASK_DATASETS.items():
                if not t.get(dataset):
                    continue
                out.append({
                    "id": f"task:{t.name}:{dataset}", "kind": "project-raster", "surface": surface,
                    "label": f"{t.title or t.name} · {label}", "task": t.name,
                    "note": (f"This project's own survey, processed {frappe.utils.format_date(t.modified)}. "
                             + ("Includes vegetation and buildings as they were then."
                                if surface == "surface" else "Ground only.")
                             + " Covers only the area that survey covered."),
                })
    out.append({"id": "none", "label": "None (assume flat ground)", "kind": "none",
                "note": "Terrain is not considered: height above ground, clearance and terrain following are unavailable."})
    return out


@frappe.whitelist(allow_guest=False, methods=["POST"])
def geocode(query=None):
    """Find a place: coordinates are parsed locally, anything else goes to the configured geocoder."""
    tenancy.require_org()
    query = (query or "").strip()
    if not query:
        return {"results": [], "attribution": ""}
    if len(query) > geocode_mod.MAX_QUERY:
        frappe.throw("The search text is too long")
    position = geocode_mod.parse_coordinates(query)
    if position:
        return {"results": [position], "attribution": ""}
    provider = str(_conf("flightplan_geocoder", "nominatim")).lower()
    if provider in ("none", "off", "disabled"):
        return {"results": [], "attribution": "",
                "message": "Address search is switched off on this platform. Enter coordinates instead."}
    url = str(_conf("flightplan_geocoder_url", NOMINATIM_PUBLIC))
    cache = frappe.cache()
    key = "flightplan:geocode:" + hashlib.sha1(f"{url}|{query.lower()}".encode()).hexdigest()
    cached = cache.get_value(key)
    if cached is not None:
        return {"results": cached, "attribution": OSM_ATTRIBUTION}
    if _over_budget("geocode", GEOCODE_LOOKUPS_PER_MINUTE):
        return {"results": [], "attribution": OSM_ATTRIBUTION,
                "message": "Too many searches in a minute. Wait a moment, or enter coordinates (latitude, longitude)."}
    if url.rstrip("/") == NOMINATIM_PUBLIC:
        # The public service asks for at most one request per second.
        last = flt(cache.get_value("flightplan:geocode:last") or 0)
        wait = 1.0 - (time.time() - last)
        if 0 < wait <= 1.0:
            time.sleep(wait)
        cache.set_value("flightplan:geocode:last", time.time(), expires_in_sec=60)
    try:
        results = geocode_mod.search(query, url=url, fetch=fetch_json, user_agent=_user_agent(),
                                     language=frappe.local.lang if getattr(frappe.local, "lang", None) else None)
    except geocode_mod.GeocodeError as e:
        return {"results": [], "attribution": OSM_ATTRIBUTION,
                "message": f"{e}. You can still enter coordinates (latitude, longitude)."}
    cache.set_value(key, results, expires_in_sec=GEOCODE_CACHE_SECONDS)
    return {"results": results, "attribution": OSM_ATTRIBUTION}


# -- endpoints: plans --------------------------------------------------------------

@frappe.whitelist(allow_guest=False, methods=["POST"])
def preview(definition=None, project=None, terrain="fetch", plan=None):
    """Compute a mission without saving it.

    Returns ``{"ok": true, "mission": ...}`` or ``{"ok": false, "error":
    {"message", "field"}}``: an unusable definition is an expected state
    while someone is drawing, not a failure of the request. ``terrain``:
    ``fetch`` (default) loads elevation data if needed; ``cached`` answers
    immediately and sets ``mission.terrain_pending`` when it had to skip it.
    ``plan`` names a saved plan whose equipment snapshot may stand in for a
    deleted profile.
    """
    tenancy.require_org()
    d = _definition(definition)
    if project:
        _project_checked(project)
    snapshot = None
    if plan:
        snapshot = _mission_of(_get_plan_checked(plan)).get("equipment")
    try:
        mission = compute(d, project, fetch=str(terrain) != "cached", snapshot=snapshot)
    except PlanError as e:
        return {"ok": False, "error": {"message": str(e), "field": e.field}}
    return {"ok": True, "mission": mission}


@frappe.whitelist(allow_guest=False)
def list_plans(project=None):
    """The organization's flight plans (one project's when ``project`` is given), newest first."""
    tenancy.require_org()
    filters = {}
    if project:
        _project_checked(project)
        filters["project"] = project
    rows = frappe.get_list(
        PLAN, filters=filters, order_by="modified desc", limit_page_length=0,
        fields=["name", "title", "description", "project", "plan_type", "drone", "camera", "drone_label",
                "camera_label", "gsd_cm", "area_m2", "distance_m", "duration_s", "photo_count", "batteries",
                "waypoint_count", "issue_errors", "issue_warnings", "computed_at", "creation", "modified",
                "created_by", "owner"])
    titles = {}
    if rows:
        for p in frappe.get_all("WebODM Project", filters={"name": ["in", list({r.project for r in rows})]},
                                fields=["name", "title"]):
            titles[p.name] = p.title
    out = []
    for r in rows:
        row = _plan_row(frappe._dict(r))
        row["project_title"] = titles.get(r.project) or r.project
        out.append(row)
    return out


@frappe.whitelist(allow_guest=False)
def get_plan(name=None):
    """A saved plan: its definition, its computed mission and its summary."""
    return _plan_payload(_get_plan_checked(name))


@frappe.whitelist(allow_guest=False, methods=["POST"])
def save_plan(project=None, title=None, definition=None, name=None, description=None):
    """Create a plan in ``project`` or update ``name``. The mission is always recomputed here.

    A plan with findings (even errors) can be saved: it is a draft. Only a
    definition that cannot be laid out at all is refused.
    """
    tenancy.require_org()
    d = _definition(definition)
    title = (title or "").strip()
    if not title:
        frappe.throw("Give the flight plan a title")
    if name:
        doc = _get_plan_checked(name, "write")
        project = project or doc.project
    else:
        doc = frappe.new_doc(PLAN)
    _project_checked(project)
    snapshot = _mission_of(doc).get("equipment") if name else None
    try:
        mission = compute(d, project, snapshot=snapshot)
    except PlanError as e:
        frappe.throw(str(e), frappe.ValidationError)
    doc.title = title
    doc.project = project
    if description is not None:
        doc.description = description or None
    _store(doc, d, mission)
    doc.flags.mission_computed = True
    if name:
        doc.save()
    else:
        doc.insert()
    return _plan_payload(doc)


@frappe.whitelist(allow_guest=False, methods=["POST"])
def delete_plan(name=None):
    doc = _get_plan_checked(name, "delete")
    frappe.delete_doc(PLAN, doc.name)
    return {"deleted": doc.name}


def _copy_title(title: str, project: str) -> str:
    base = f"{title} (copy)"
    candidate, n = base, 2
    while frappe.db.exists(PLAN, {"project": project, "title": candidate}):
        candidate = f"{title} (copy {n})"
        n += 1
    return candidate


@frappe.whitelist(allow_guest=False, methods=["POST"])
def duplicate_plan(name=None, project=None, title=None):
    """Copy a plan, into the same project or another one of the organization.

    The copy is recomputed from the definition with today's profiles and
    elevation data, so it is a fresh plan rather than a stale snapshot.
    """
    tenancy.require_org()
    source = _get_plan_checked(name)
    project = project or source.project
    _project_checked(project)
    d = _doc_definition(source)
    if d["elevation_source"].startswith("task:") and project != source.project:
        d["elevation_source"] = "auto"  # another project cannot read this project's survey
    try:
        mission = compute(d, project, snapshot=_mission_of(source).get("equipment"))
    except PlanError as e:
        frappe.throw(str(e), frappe.ValidationError)
    doc = frappe.new_doc(PLAN)
    doc.title = (title or "").strip() or _copy_title(source.title, project)
    doc.project = project
    doc.description = source.description
    _store(doc, d, mission)
    doc.flags.mission_computed = True
    doc.insert()
    return _plan_payload(doc)


# -- endpoints: export -------------------------------------------------------------

def _export_args(doc):
    d = _doc_definition(doc)
    return {"title": doc.title, "geometry": d["geometry"], "corridor_width_m": d.get("corridor_width_m")}


@frappe.whitelist(allow_guest=False)
def export_options(name=None, triggers=None):
    """The formats ``name`` can be exported in: support level for its aircraft, file count, findings, verdict."""
    doc = _get_plan_checked(name)
    mission = _mission_of(doc)
    if not mission.get("waypoints"):
        frappe.throw("This plan has no computed mission yet; open it in the planner and save it.")
    return {
        "plan": _plan_row(doc),
        "summary": mission.get("summary"),
        "issues": mission.get("issues"),
        "assurance": mission.get("assurance"),
        "formats": exporters.export_options(mission, triggers=_obj(triggers, {}) or {}, **_export_args(doc)),
        "notice": NOTICE,
    }


@frappe.whitelist(allow_guest=False, methods=["POST"])
def export_plan(name=None, format=None, trigger=None, acknowledged=0, firmware=None):
    """Download ``name`` as ``format``.

    Refused (with the reason) when the format does not apply to the plan's
    aircraft, when the plan has findings that block a mission file, or when
    it has warnings and ``acknowledged`` is not set. An exported mission file
    is noted on the plan with who exported it and what they acknowledged.
    """
    from werkzeug.wrappers import Response

    doc = _get_plan_checked(name)
    mission = _mission_of(doc)
    options = {"firmware": firmware} if firmware else None
    try:
        out = exporters.export(mission, format, trigger=trigger or None, acknowledged=bool(cint(acknowledged)),
                               options=options, **_export_args(doc))
    except exporters.ExportError as e:
        frappe.throw(str(e), frappe.ValidationError)

    report = out["report"]
    if report["kind"] == "mission":
        acknowledged_codes = sorted({i["code"] for i in (mission.get("issues") or []) + report["issues"]
                                     if i["severity"] == "warning"})
        doc.add_comment("Info", (
            f"Exported {report['label']} ({out['files']} file{'s' if out['files'] != 1 else ''}"
            f"{', photo trigger: ' + report['trigger'] if report.get('trigger') else ''})."
            + (f" Warnings acknowledged: {', '.join(acknowledged_codes)}." if acknowledged_codes else "")))
        frappe.db.commit()

    response = Response(out["content"], mimetype=out["mime"])
    response.headers["Content-Disposition"] = f'attachment; filename="{out["filename"]}"'
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Flight-Plan-Files"] = str(out["files"])
    return response


# -- endpoints: the organization's own equipment ------------------------------------

CAMERA_FIELDS = ("manufacturer", "sensor_width_mm", "sensor_height_mm", "image_width_px", "image_height_px",
                 "focal_length_mm", "min_interval_s", "shutter", "notes")
DRONE_FIELDS = ("manufacturer", "model", "airframe", "rtk", "max_flight_time_min", "endurance_factor",
                "max_speed_ms", "max_mission_speed_ms", "min_speed_ms", "cruise_speed_ms", "max_ascent_ms",
                "max_descent_ms", "max_wind_ms", "service_ceiling_m", "max_height_above_takeoff_m",
                "takeoff_mass_g", "min_turn_radius_m", "notes")


def _profile_doc(doctype: str, docname: str | None):
    if docname:
        doc = frappe.get_doc(doctype, docname)
        doc.check_permission("write")
        return doc
    return frappe.new_doc(doctype)


@frappe.whitelist(allow_guest=False, methods=["POST"])
def save_camera(profile=None):
    """Create or update one of the organization's camera profiles. Returns its catalog entry."""
    tenancy.require_org()
    p = _obj(profile, {})
    if not isinstance(p, dict):
        frappe.throw("profile must be an object")
    doc = _profile_doc(CAMERA, p.get("docname"))
    doc.profile_name = (p.get("name") or "").strip()
    for field in CAMERA_FIELDS:
        doc.set(field, p.get(field))
    doc.wpml_payload_enum = cint((p.get("wpml") or {}).get("payload_enum"))
    doc.save() if p.get("docname") else doc.insert()
    return _camera_entry(camera_from_doc(doc))


@frappe.whitelist(allow_guest=False, methods=["POST"])
def delete_camera(docname=None):
    """Delete a camera profile unless one of the organization's drone profiles still carries it."""
    doc = frappe.get_doc(CAMERA, docname)
    doc.check_permission("delete")
    cam_id = f"{CUSTOM}{doc.name}"
    users = [d.model for d in frappe.get_all(DRONE, filters={"organization": doc.organization},
                                             fields=["model", "cameras", "default_camera"])
             if cam_id in [c.strip() for c in (d.cameras or "").split(",")] or d.default_camera == cam_id]
    if users:
        frappe.throw(f"This camera is still carried by: {', '.join(users)}. Remove it from those drone profiles first.")
    frappe.delete_doc(CAMERA, doc.name)
    return {"deleted": doc.name}


@frappe.whitelist(allow_guest=False, methods=["POST"])
def save_drone(profile=None):
    """Create or update one of the organization's drone profiles. Returns its catalog entry.

    ``profile.ecosystem`` (one of ``profiles.ECOSYSTEMS``) fills the mission
    capability fields; an explicit ``profile.mission`` overrides it.
    """
    org = tenancy.require_org()
    p = _obj(profile, {})
    if not isinstance(p, dict):
        frappe.throw("profile must be an object")
    doc = _profile_doc(DRONE, p.get("docname"))
    for field in DRONE_FIELDS:
        doc.set(field, p.get(field))
    cams = p.get("cameras") or []
    if isinstance(cams, str):
        cams = [c.strip() for c in cams.split(",") if c.strip()]
    doc.cameras = ", ".join(cams)
    doc.default_camera = p.get("default_camera") or (cams[0] if cams and cams[0] != "*" else None)

    mission = p.get("mission") if isinstance(p.get("mission"), dict) else {}
    eco = profiles.ecosystem(p.get("ecosystem"))
    if p.get("ecosystem") and not eco:
        frappe.throw(f"Unknown flight app family: {p.get('ecosystem')}")
    base = eco or {}
    doc.ecosystem = p.get("ecosystem") or None
    doc.waypoints = 1 if mission.get("waypoints", base.get("waypoints", True)) else 0
    doc.per_waypoint_altitude = 1 if mission.get("per_waypoint_altitude", base.get("per_waypoint_altitude", True)) else 0
    doc.mission_formats = frappe.as_json(mission.get("formats", base.get("formats", [])))
    wpml = mission.get("wpml") or {}
    doc.wpml_drone_enum = cint(wpml.get("drone_enum"))
    doc.wpml_drone_sub_enum = cint(wpml.get("drone_sub_enum"))
    doc.save() if p.get("docname") else doc.insert()
    cameras = _all_cameras(org)
    return _drone_entry(drone_from_doc(doc, cameras), cameras)


@frappe.whitelist(allow_guest=False, methods=["POST"])
def delete_drone(docname=None):
    """Delete a drone profile. Saved plans keep the snapshot they were planned with."""
    doc = frappe.get_doc(DRONE, docname)
    doc.check_permission("delete")
    frappe.delete_doc(DRONE, doc.name)
    return {"deleted": doc.name}
