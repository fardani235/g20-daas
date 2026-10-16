"""Regenerate flightsim.json, the fixture the flight simulator tests read.

Everything in it comes from the real planner, so the simulator is tested
against the missions the server returns and against the planner's own
numbers: the local frame, the terrain interpolation and the exposure
positions the exporters write. Run from the repository root:

    PYTHONPATH=frappe-bench/apps/webodm_core python3 \
        frappe-bench/apps/webodm_frontend/frontend/src/lib/__fixtures__/make_flightsim_fixture.py
"""

import json
import os
import random

from webodm_core.flightplan import _fixtures as fx
from webodm_core.flightplan import plan_mission, profiles
from webodm_core.flightplan.exporters.base import _leg_points
from webodm_core.flightplan.geo import LocalFrame
from webodm_core.flightplan.simulation import terrain_payload


def plan(definition, drone_id="dji-mavic-3e", terrain=None):
    drone = profiles.builtin_drones()[drone_id]
    camera = profiles.builtin_cameras()[drone.default_camera]
    mission = plan_mission(definition, drone, camera, terrain=terrain)
    mission["terrain_pending"] = False
    mission["elevation_source"] = "auto" if terrain is not None else "none"
    return mission


def exposures(mission):
    """Where the 'photo at every waypoint' export puts each exposure: ``[leg, lng, lat, alt]``."""
    out = []
    for leg in mission["legs"]:
        for p in _leg_points(mission, leg, "waypoint"):
            if p.take_photo:
                out.append([leg["index"], p.lng, p.lat, p.alt])
    return out


def main():
    rng = random.Random(20261016)
    missions, terrains, terrain_samples = {}, {}, {}

    # One battery, flat ground assumed (no elevation data).
    missions["area_flat"] = plan(fx.area_plan(takeoff_m=(0, -170)))

    # Terrain following over a 60 m ridge.
    ridge = fx.terrain(fx.ridge(700, 60, 120), half_extent_m=600)
    missions["area_terrain"] = plan(
        fx.area_plan(fx.rect(600, 300), takeoff_m=(-300, 0), altitude_mode="terrain", altitude_m=80, vlos_m=1000),
        terrain=ridge)
    terrains["area_terrain"] = ridge

    # Constant altitude over a 10 % slope, with a patch of the grid missing.
    slope = fx.terrain(fx.slope(700, 0.10), half_extent_m=450, hole=lambda x, y: x > 260 and y > 220)
    missions["area_slope"] = plan(fx.area_plan(takeoff_m=(-220, -170)), terrain=slope)
    terrains["area_slope"] = slope

    # Three batteries.
    missions["multi_battery"] = plan(fx.area_plan(fx.rect(1500, 1000), takeoff_m=(-760, 0)))

    # A corridor with a bend, two passes.
    missions["corridor"] = plan(fx.corridor_plan(takeoff_m=(-620, -30), corridor_min_passes=2))

    # Land where the survey ends instead of returning.
    missions["land_in_place"] = plan(fx.area_plan(takeoff_m=(0, -170), finish_action="land"))

    # Airframes that cannot hover.
    missions["fixed_wing"] = plan(fx.area_plan(fx.rect(900, 600), takeoff_m=(-520, -340), altitude_m=120),
                                  drone_id="ageagle-ebee-x")
    missions["vtol"] = plan(fx.area_plan(fx.rect(900, 600), takeoff_m=(-520, -340), altitude_m=120),
                            drone_id="wingtra-wingtraone-gen2")

    for name, model in terrains.items():
        pts = []
        for _ in range(60):
            lng = model.west + (model.east - model.west) * rng.uniform(-0.05, 1.05)
            lat = model.south + (model.north - model.south) * rng.uniform(-0.05, 1.05)
            pts.append([lng, lat, model.elevation(lng, lat)])
        # Exactly on the corners and edges too.
        for lng, lat in ((model.west, model.south), (model.east, model.north), (model.east, model.south),
                         ((model.west + model.east) / 2, model.north)):
            pts.append([lng, lat, model.elevation(lng, lat)])
        terrain_samples[name] = pts

    # The planner's local frame at several latitudes: [lat0, lng0, lng, lat, x, y].
    frame_refs = []
    for lat0, lng0 in ((fx.SITE[1], fx.SITE[0]), (0.0, 0.0), (64.1, -21.9), (-45.0, 170.5), (35.0, 179.99)):
        frame = LocalFrame(lat0, lng0)
        for _ in range(8):
            x, y = rng.uniform(-30000, 30000), rng.uniform(-30000, 30000)
            lng, lat = frame.inverse(x, y)
            fx_, fy_ = frame.forward(lng, lat)
            frame_refs.append([lat0, lng0, lng, lat, fx_, fy_])

    out = {
        "missions": missions,
        "terrain": {name: terrain_payload(model) for name, model in terrains.items()},
        "refs": {
            "frame": frame_refs,
            "terrain_samples": terrain_samples,
            "exposures": {name: exposures(m) for name, m in missions.items()},
        },
    }
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flightsim.json")
    with open(path, "w") as f:
        json.dump(out, f, sort_keys=True, separators=(",", ":"))
        f.write("\n")
    print(path, os.path.getsize(path), "bytes")
    for name, m in missions.items():
        s = m["stats"]
        print(f"  {name:14s} wps={s['waypoint_count']:4d} legs={s['leg_count']:3d} photos={s['photo_count']:5d} "
              f"flights={s['flights']} t={s['total_duration_s']:.0f}s d={s['total_distance_m']:.0f}m "
              f"errors={m['summary']['error']}")


if __name__ == "__main__":
    main()
