"""Print the built-in equipment catalog as Markdown tables.

    PYTHONPATH=frappe-bench/apps/webodm_core python -m webodm_core.flightplan.catalog_table

The tables in ``docs/flight-planning/equipment.md`` are this output; run it
again after editing ``catalog/*.json`` and paste the result there.
"""

from __future__ import annotations

from . import photogrammetry as pg
from .exporters import EXPORTERS
from .profiles import ANY_CAMERA, builtin_cameras, builtin_drones

LEVEL = {"official": "supported", "community": "unofficial", "boundary": "boundary import"}
SHORT = {"dji-wpml": "DJI WPML", "qgc-plan": "QGC plan", "mavlink-wpl": "MAVLink waypoints",
         "litchi-csv": "Litchi CSV", "kml-boundary": "boundary KML"}


def _n(value, unit="") -> str:
    if value is None:
        return "–"
    text = f"{value:g}"
    return f"{text}{unit}"


def drone_table() -> str:
    cameras = builtin_cameras()
    lines = [
        "| Aircraft | Type | Rated time | Usable¹ | Mission speed | Wind limit | Cameras | Mission files |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for d in sorted(builtin_drones().values(), key=lambda d: (d.manufacturer == "Generic", d.label)):
        usable = d.max_flight_time_min * d.endurance_factor * 0.75
        cams = "any" if ANY_CAMERA in d.cameras else ", ".join(cameras[c].name for c in d.cameras)
        if not d.waypoints:
            files = "none (no autonomous flight)"
        else:
            files = "; ".join(f"{SHORT.get(f.id, f.id)} — {LEVEL[f.level]}" + (f" ({f.app})" if f.app else "")
                              for f in d.formats) or "none known"
        lines.append(
            f"| {d.label} | {d.airframe} | {_n(d.max_flight_time_min, ' min')} | {usable:.0f} min | "
            f"{_n(d.mission_speed_limit_ms, ' m/s')} | {_n(d.max_wind_ms, ' m/s')} | {cams} | {files} |")
    return "\n".join(lines)


def camera_table() -> str:
    lines = [
        "| Camera | Sensor (mm) | Image (px) | Focal length | GSD at 100 m | Footprint at 100 m | Min. interval | Shutter |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for c in sorted(builtin_cameras().values(), key=lambda c: c.label):
        fp = pg.footprint(c, 100.0)
        lines.append(
            f"| {c.label} | {c.sensor_width_mm:g} × {c.sensor_height_mm:g} | {c.image_width_px} × {c.image_height_px} | "
            f"{c.focal_length_mm:g} mm | {fp.gsd_cm:.2f} cm/px | {fp.across_m:.0f} × {fp.along_m:.0f} m | "
            f"{_n(c.min_interval_s, ' s') if c.min_interval_s else 'not published'} | {c.shutter} |")
    return "\n".join(lines)


def format_table() -> str:
    lines = ["| Format | File | Kind | Photo trigger | Waypoint limit |", "|---|---|---|---|---|"]
    for e in EXPORTERS.values():
        lines.append(f"| {e.label} | `.{e.extension}` | {e.kind} | {', '.join(e.triggers) or '–'} | "
                     f"{e.max_waypoints or '–'} |")
    return "\n".join(lines)


if __name__ == "__main__":
    print(drone_table())
    print()
    print(camera_table())
    print()
    print(format_table())
