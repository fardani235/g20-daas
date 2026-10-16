"""Mission export: the format registry and the one gate every export passes.

``export_options`` tells a client, for one mission, which formats apply to
its aircraft, how well supported each is, what it would produce and whether
it is blocked. ``export`` produces the file.

The gate (``_gate``) is the same for both, so what the dialog shows is what
the download does:

- a *mission* file is refused while the plan has any ``error`` finding
  (the aircraft or camera cannot fly it as specified) or the format itself
  objects; with ``warning`` findings it needs ``acknowledged=True``;
- a mission format the aircraft's profile does not list is not offered;
- *boundary* and *reference* files are always available: they carry no
  flight instructions, only geometry and the plan's own findings.

Adding a format: write an ``Exporter`` subclass in a new module and add it
to ``_EXPORTERS``. Declaring that an aircraft can use it is a line in the
drone profile's ``mission.formats``.
"""

from __future__ import annotations

import io
import time
import zipfile

from ..issues import NOTICE, Issue
from .base import (TRIGGER_LABELS, TRIGGERS, ExportContext, ExportError, Exporter, Route, build_routes,
                   route_suffix, slug)
from .dji_wpml import DjiWpml
from .geojson import GeoJson
from .kml import KmlBoundary, KmlPlan
from .litchi_csv import LitchiCsv
from .mavlink import MavlinkWpl, QgcPlan

_EXPORTERS: tuple[Exporter, ...] = (DjiWpml(), QgcPlan(), MavlinkWpl(), LitchiCsv(), KmlBoundary(), KmlPlan(), GeoJson())
EXPORTERS: dict[str, Exporter] = {e.id: e for e in _EXPORTERS}

LEVEL_LABELS = {
    "official": "Supported by the flight app",
    "community": "Unofficial procedure",
    "boundary": "Boundary import",
    "reference": "Reference only",
}


def formats_catalog() -> list[dict]:
    """Every format the platform can write (for the equipment forms)."""
    return [{**e.describe(), "trigger_labels": {t: TRIGGER_LABELS[t] for t in e.triggers}} for e in _EXPORTERS]


def _drone_format(mission: dict, format_id: str) -> dict | None:
    formats = ((mission["equipment"]["drone"].get("mission") or {}).get("formats")) or []
    return next((f for f in formats if f.get("id") == format_id), None)


def _support(exporter: Exporter, mission: dict) -> dict | None:
    """How the mission's aircraft supports ``exporter``; ``None`` = not usable for it."""
    entry = _drone_format(mission, exporter.id)
    drone = mission["equipment"]["drone"]
    if exporter.kind == "mission":
        if not (drone.get("mission") or {}).get("waypoints") or not entry or entry.get("level") == "boundary":
            return None
        return {"level": entry.get("level") or "community", "app": entry.get("app") or "", "note": entry.get("note") or ""}
    if exporter.kind == "boundary" and entry:
        return {"level": "boundary", "app": entry.get("app") or "", "note": entry.get("note") or ""}
    return {"level": "reference", "app": "", "note": ""}


def _trigger(exporter: Exporter, mission: dict, requested: str | None) -> str | None:
    if not exporter.triggers:
        return None
    if requested:
        if requested not in exporter.triggers:
            raise ExportError(f"{exporter.label} cannot trigger photos by '{requested}'", "EXPORT_BAD_TRIGGER")
        return requested
    entry = _drone_format(mission, exporter.id) or {}
    preferred = entry.get("trigger")
    return preferred if preferred in exporter.triggers else exporter.triggers[0]


def _cap(exporter: Exporter, mission: dict) -> int | None:
    entry = _drone_format(mission, exporter.id) or {}
    caps = [c for c in (exporter.max_waypoints, entry.get("max_waypoints")) if c]
    return min(caps) if caps else None


def _context(mission: dict, title: str, trigger: str | None, geometry: dict | None, corridor_width_m,
             options: dict | None, created_ms: int | None) -> ExportContext:
    opts = dict(options or {})
    opts["geometry"] = geometry
    opts["corridor_width_m"] = corridor_width_m
    return ExportContext(mission=mission, title=title or "Flight plan", trigger=trigger or "manual", options=opts,
                         created_ms=created_ms if created_ms is not None else int(time.time() * 1000))


def _gate(exporter: Exporter, mission: dict, ctx: ExportContext, routes: list[Route]) -> dict:
    """Findings and verdict for exporting ``mission`` with ``exporter``."""
    export_issues = [i.to_dict() for i in exporter.check(ctx, routes)]
    plan_issues = mission.get("issues") or []
    blocking = []
    acknowledge = []
    if exporter.kind == "mission":
        blocking = [i for i in plan_issues if i["severity"] == "error"]
        acknowledge = [i for i in plan_issues if i["severity"] == "warning"]
        drone = mission["equipment"]["drone"]
        if (mission.get("terrain") or {}).get("following") and not (drone.get("mission") or {}).get("per_waypoint_altitude"):
            # The plan's heights vary along the route, and this aircraft's
            # missions cannot carry a height per waypoint.
            export_issues.append(Issue(
                "TERRAIN_HEIGHTS_NOT_LOADABLE", "error", "export",
                f"This is a terrain-following plan, and the {drone.get('label')}'s missions cannot carry a height "
                "for each waypoint. Use constant altitude, or let the flight app follow the terrain.").to_dict())
    blocking += [i for i in export_issues if i["severity"] == "error"]
    acknowledge += [i for i in export_issues if i["severity"] == "warning"]
    return {
        "issues": export_issues,
        "blocked": bool(blocking),
        "blocking": blocking,
        "needs_acknowledgement": bool(acknowledge) and not blocking,
        "acknowledge": acknowledge,
    }


def export_options(mission: dict, *, geometry: dict | None = None, corridor_width_m=None, title: str = "",
                   triggers: dict | None = None) -> list[dict]:
    """One entry per format: support level, what it would write, and whether it is blocked.

    ``triggers`` optionally maps a format id to the photo-trigger mode the
    user picked, so the preview (file count, findings) matches the choice.
    """
    out = []
    for exporter in _EXPORTERS:
        entry = exporter.describe()
        support = _support(exporter, mission)
        entry["support"] = support
        entry["available"] = support is not None
        entry["level_label"] = LEVEL_LABELS.get((support or {}).get("level"), "")
        if support is None:
            drone = mission["equipment"]["drone"]
            entry["reason"] = (
                f"The {drone.get('label')} has no autonomous waypoint flight."
                if not (drone.get("mission") or {}).get("waypoints")
                else f"The {drone.get('label')}'s flight app does not read this format.")
            out.append(entry)
            continue
        trigger = _trigger(exporter, mission, (triggers or {}).get(exporter.id))
        ctx = _context(mission, title, trigger, geometry, corridor_width_m, None, 0)
        routes = build_routes(mission, trigger or "manual", _cap(exporter, mission)) if exporter.kind == "mission" else []
        entry.update(_gate(exporter, mission, ctx, routes))
        entry["trigger"] = trigger
        entry["trigger_choices"] = [{"id": t, "label": TRIGGER_LABELS[t]} for t in exporter.triggers]
        entry["waypoint_cap"] = _cap(exporter, mission) if exporter.kind == "mission" else None
        entry["files"] = len(routes) if exporter.kind == "mission" else 1
        entry["waypoints"] = sum(len(r.points) for r in routes) if routes else None
        out.append(entry)
    return out


def export(mission: dict, format_id: str, *, title: str = "", geometry: dict | None = None,
           corridor_width_m=None, trigger: str | None = None, acknowledged: bool = False,
           options: dict | None = None, created_ms: int | None = None) -> dict:
    """Render ``mission`` as ``format_id``. Returns ``{"filename", "content", "mime", "files", "report"}``.

    Raises ``ExportError`` when the format is unknown, not usable for the
    aircraft, blocked by an error finding, or needs an acknowledgement that
    was not given. ``ExportError.issues`` lists the findings behind a refusal.
    """
    exporter = EXPORTERS.get(format_id)
    if exporter is None:
        raise ExportError(f"Unknown export format: {format_id}", "EXPORT_UNKNOWN_FORMAT")
    if not isinstance(mission, dict) or not mission.get("waypoints"):
        raise ExportError("The plan has no computed mission to export; open it and save it again.", "EXPORT_NO_MISSION")
    support = _support(exporter, mission)
    if support is None:
        drone = mission["equipment"]["drone"]
        raise ExportError(
            f"{exporter.label} is not available for the {drone.get('label')}: "
            + ("it has no autonomous waypoint flight." if not (drone.get("mission") or {}).get("waypoints")
               else "its flight app does not read this format."), "EXPORT_NOT_COMPATIBLE")
    if exporter.kind != "mission" and not geometry:
        raise ExportError("The plan has no boundary to export.", "EXPORT_NO_GEOMETRY")

    chosen = _trigger(exporter, mission, trigger)
    ctx = _context(mission, title, chosen, geometry, corridor_width_m, options, created_ms)
    routes = build_routes(mission, chosen or "manual", _cap(exporter, mission)) if exporter.kind == "mission" else []
    gate = _gate(exporter, mission, ctx, routes)
    if gate["blocked"]:
        first = gate["blocking"][0]["message"]
        more = len(gate["blocking"]) - 1
        raise ExportError(
            f"This plan cannot be exported as a mission file yet: {first}"
            + (f" (and {more} more problem{'s' if more > 1 else ''})" if more else ""),
            "EXPORT_BLOCKED", gate["blocking"])
    if gate["needs_acknowledgement"] and not acknowledged:
        raise ExportError(
            "This plan has warnings that must be acknowledged before a mission file is exported.",
            "EXPORT_NEEDS_ACKNOWLEDGEMENT", gate["acknowledge"])

    base = f"{slug(title)}-{exporter.id}"
    if exporter.kind != "mission":
        files = [(f"{base}.{exporter.extension}", exporter.render_plan(ctx))]
    else:
        if any(len(r.points) < exporter.min_waypoints for r in routes):
            raise ExportError("A route part has too few waypoints to form a mission.", "EXPORT_TOO_SHORT")
        files = [(f"{base}{route_suffix(r, routes)}.{exporter.extension}", exporter.render(ctx, r)) for r in routes]

    report = {
        "format": exporter.id, "label": exporter.label, "kind": exporter.kind, "support": support,
        "trigger": chosen, "files": [name for name, _ in files],
        "routes": [{"flight": r.flight + 1, "part": r.part + 1, "parts": r.parts, "waypoints": len(r.points),
                    "length_m": round(r.length_m, 1)} for r in routes],
        "issues": gate["issues"], "acknowledged": bool(acknowledged), "notice": NOTICE,
    }
    if len(files) == 1:
        name, content = files[0]
        return {"filename": name, "content": content, "mime": exporter.mime, "files": 1, "report": report}

    readme = _readme(mission, exporter, ctx, routes, files, gate, support)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in files:
            z.writestr(name, content)
        z.writestr("README.txt", readme)
    return {"filename": f"{base}.zip", "content": buf.getvalue(), "mime": "application/zip", "files": len(files),
            "report": report}


def _readme(mission, exporter, ctx, routes, files, gate, support) -> str:
    stats, cam = mission["stats"], mission["camera"]
    lines = [
        ctx.title,
        "=" * len(ctx.title),
        "",
        NOTICE,
        "",
        f"Aircraft: {mission['equipment']['drone']['label']}",
        f"Camera:   {mission['equipment']['camera']['label']}",
        f"Format:   {exporter.label}" + (f" - {LEVEL_LABELS.get(support['level'], '')}" if support else "")
        + (f" ({support['app']})" if support and support.get("app") else ""),
        f"Flight height {cam['altitude_m']:g} m, GSD {cam['gsd_cm']:g} cm/px, speed {stats['speed_ms']:g} m/s, "
        f"photo every {cam['photo_spacing_m']:g} m / {cam['photo_interval_s']:g} s",
        f"Estimated: {stats['photo_count']} photos, {stats['total_distance_m'] / 1000:.1f} km, "
        f"{stats['total_duration_s'] / 60:.0f} min, {stats['batteries']} batteries",
        "",
        "Files (fly in this order; each starts where the previous one ended):",
    ]
    for (name, _content), r in zip(files, routes):
        lines.append(f"  {name}  - flight {r.flight + 1}"
                     + (f", part {r.part + 1} of {r.parts}" if r.parts > 1 else "")
                     + f", {len(r.points)} waypoints, {r.length_m / 1000:.1f} km")
    findings = [i for i in (mission.get("issues") or []) + gate["issues"] if i["severity"] in ("warning", "error")]
    if findings:
        lines += ["", "Warnings on this plan:"]
        lines += [f"  - {i['message']}" for i in findings]
    lines += ["", "Loading the files:"]
    lines += [f"  - {step}" for step in exporter.instructions]
    return "\n".join(lines) + "\n"


__all__ = ["EXPORTERS", "ExportError", "Exporter", "TRIGGERS", "export", "export_options", "formats_catalog"]
