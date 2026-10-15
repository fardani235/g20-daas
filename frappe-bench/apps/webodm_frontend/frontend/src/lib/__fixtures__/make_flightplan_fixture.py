"""Regenerate flightplan.json, the planner fixture the frontend tests read.

The fixture is produced by the real planner so the tests exercise the shapes
the server actually returns. Run from the repository root:

    PYTHONPATH=frappe-bench/apps/webodm_core python3 \
        frappe-bench/apps/webodm_frontend/frontend/src/lib/__fixtures__/make_flightplan_fixture.py
"""

import json
import os

from webodm_core.flightplan import _fixtures as fx
from webodm_core.flightplan import plan_mission, profiles
from webodm_core.flightplan.exporters import export_options, formats_catalog
from webodm_core.flightplan.issues import NOTICE
from webodm_core.flightplan.params import rule_sets, specs_as_dicts

KEEP_DRONES = ("dji-mavic-3e", "dji-mini-4-pro", "dji-air-2s", "dji-mini-3", "dji-matrice-350-rtk",
               "generic-mavlink-multirotor", "wingtra-wingtraone-gen2")


def main():
    cameras = profiles.builtin_cameras()
    drones = [profiles.builtin_drones()[d] for d in KEEP_DRONES]
    used = {c for d in drones for c in d.cameras if c != "*"} | {"sony-a6000-20mm", "micasense-rededge-mx"}
    cams = {k: v for k, v in cameras.items() if k in used}

    def drone_entry(d):
        out = d.to_dict()
        out["editable"] = False
        out["camera_choices"] = [c.id for c in profiles.cameras_for(d, cams)]
        return out

    catalog = {
        "drones": [drone_entry(d) for d in drones],
        "cameras": [{**c.to_dict(), "editable": False} for c in cams.values()],
        "formats": formats_catalog(),
        "ecosystems": [dict(e) for e in profiles.ECOSYSTEMS],
        "params": specs_as_dicts(),
        "rule_sets": list(rule_sets().values()),
        "elevation": {"enabled": True, "label": "Open Topo Data · mapzen", "resolution_m": 30.0,
                      "vertical_accuracy_m": 10.0, "note": ""},
        "geocoder": {"enabled": True, "attribution": "Search © OpenStreetMap contributors"},
        "airspace": {"configured": False, "label": ""},
        "limits": {"max_vertices": 500, "max_extent_m": 60000.0},
        "catalog_as_of": profiles.catalog_as_of(),
        "can_edit_profiles": True,
        "notice": NOTICE,
    }

    m3e = profiles.builtin_drones()["dji-mavic-3e"]
    definition = fx.area_plan(fx.rect(600, 300), takeoff_m=(-300, 0), altitude_m=100)
    definition.update({"drone": m3e.id, "camera": m3e.default_camera, "elevation_source": "auto"})
    terrain = fx.terrain(fx.slope(700, 0.10))
    mission = plan_mission(definition, m3e, cameras[m3e.default_camera], terrain=terrain)
    mission["terrain_pending"] = False
    mission["elevation_source"] = "auto"

    blocked_def = fx.area_plan(takeoff_m=(0, -170), speed_ms=18)
    blocked = plan_mission(blocked_def, m3e, cameras[m3e.default_camera])

    geometry = definition["geometry"]
    out = {
        "catalog": catalog,
        "definition": definition,
        "mission": mission,
        "export": {
            "summary": mission["summary"], "issues": mission["issues"], "assurance": mission["assurance"],
            "formats": export_options(mission, geometry=geometry, title="North field"), "notice": NOTICE,
        },
        "export_blocked": {
            "summary": blocked["summary"], "issues": blocked["issues"], "assurance": blocked["assurance"],
            "formats": export_options(blocked, geometry=blocked_def["geometry"], title="North field"),
            "notice": NOTICE,
        },
    }
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flightplan.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=1, sort_keys=True)
        f.write("\n")
    print(path, os.path.getsize(path), "bytes")


if __name__ == "__main__":
    main()
