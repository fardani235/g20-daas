"""GeoJSON: the plan as data, for GIS and for other software.

A FeatureCollection with the survey boundary, the takeoff point, the flight
path (3D: metres above the takeoff point) and one point per waypoint. The
estimates, the findings and the notice ride along as foreign members so the
file can never be separated from what it is: a planning estimate.
"""

from __future__ import annotations

import json

from ..issues import NOTICE
from .base import ExportContext, Exporter


class GeoJson(Exporter):
    id = "geojson"
    label = "Plan data (.geojson)"
    extension = "geojson"
    mime = "application/geo+json"
    kind = "reference"
    description = "Boundary, flight path and waypoints with their attributes, for GIS software and custom tooling."
    instructions = (
        "Open in QGIS or any GIS; waypoint heights are metres above the takeoff point.",
        "This file is data about the plan. It is not a mission file.",
    )

    def render_plan(self, ctx: ExportContext) -> bytes:
        m = ctx.mission
        take = m["takeoff"]
        wps = m["waypoints"]
        features = [
            {"type": "Feature", "geometry": ctx.options["geometry"],
             "properties": {"role": "boundary", "plan_type": m["type"],
                            "corridor_width_m": ctx.options.get("corridor_width_m")}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [take["lng"], take["lat"]]},
             "properties": {"role": "takeoff", "assumed": bool(take.get("assumed")), "ground_m": take.get("ground_m")}},
            {"type": "Feature",
             "geometry": {"type": "LineString", "coordinates": [[wp["lng"], wp["lat"], wp["alt"]] for wp in wps]},
             "properties": {"role": "flight_path", "height_reference": "takeoff point"}},
        ]
        for wp in wps:
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [wp["lng"], wp["lat"], wp["alt"]]},
                "properties": {
                    "role": "waypoint", "index": wp["index"], "line": wp["line"], "kind": wp["role"],
                    "camera_on": wp["photo"], "height_above_takeoff_m": wp["alt"],
                    "estimated_height_above_ground_m": wp.get("agl"), "heading_deg": wp["heading"],
                },
            })
        doc = {
            "type": "FeatureCollection",
            "name": ctx.title,
            "notice": NOTICE,
            "plan": {
                "drone": m["equipment"]["drone"]["label"],
                "camera": m["equipment"]["camera"]["label"],
                "camera_geometry": m["camera"],
                "estimates": m["stats"],
                "terrain": {k: v for k, v in (m.get("terrain") or {}).items() if k != "profile"} or None,
                "findings": m["issues"],
                "assurance": m["assurance"],
            },
            "features": features,
        }
        return (json.dumps(doc, indent=1) + "\n").encode()
