"""Simulation support: the elevation grid handed to the flight simulator."""

import copy
import json
import unittest

from webodm_core.flightplan import _fixtures as fx
from webodm_core.flightplan import plan_mission
from webodm_core.flightplan.simulation import DATUM_NOTE, ELEVATION_DECIMALS, terrain_payload
from webodm_core.flightplan.terrain import TerrainModel, TerrainSource


def bilinear(payload: dict, lng: float, lat: float):
    """The client's interpolation, written out independently of ``TerrainModel``."""
    if not (payload["south"] <= lat <= payload["north"] and payload["west"] <= lng <= payload["east"]):
        return None
    rows, cols, values = payload["rows"], payload["cols"], payload["values"]
    fy = (lat - payload["south"]) / (payload["north"] - payload["south"]) * (rows - 1)
    fx_ = (lng - payload["west"]) / (payload["east"] - payload["west"]) * (cols - 1)
    r0, c0 = min(int(fy), rows - 2), min(int(fx_), cols - 2)
    ty, tx = fy - r0, fx_ - c0
    corners = [(values[r0][c0], (1 - tx) * (1 - ty)), (values[r0][c0 + 1], tx * (1 - ty)),
               (values[r0 + 1][c0], (1 - tx) * ty), (values[r0 + 1][c0 + 1], tx * ty)]
    known = [(v, w) for v, w in corners if v is not None]
    if not known:
        return None
    weight = sum(w for _v, w in known)
    if weight <= 1e-9:
        return sum(v for v, _w in known) / len(known)
    return sum(v * w for v, w in known) / weight


class TestTerrainPayload(unittest.TestCase):
    def test_carries_the_grid_as_the_planner_holds_it(self):
        model = fx.terrain(fx.ridge(700, 60, 120), half_extent_m=400)
        payload = terrain_payload(model)
        self.assertEqual((payload["rows"], payload["cols"]), (model.rows, model.cols))
        self.assertEqual((payload["south"], payload["west"], payload["north"], payload["east"]),
                         (model.south, model.west, model.north, model.east))
        self.assertEqual(len(payload["values"]), model.rows)
        self.assertTrue(all(len(row) == model.cols for row in payload["values"]))
        self.assertEqual(payload["source"]["id"], "test-dem")
        self.assertEqual(payload["source"]["surface"], "terrain")
        self.assertAlmostEqual(payload["min_m"], 700.0, places=1)
        self.assertAlmostEqual(payload["max_m"], 760.0, delta=8.0)      # the crest falls between nodes
        self.assertEqual(payload["coverage"], 1.0)
        self.assertEqual(payload["datum_note"], DATUM_NOTE)
        json.dumps(payload)                                             # what the client receives

    def test_nodes_are_not_resampled(self):
        # Every node, to the rounding: no smoothing, no decimation.
        model = fx.terrain(lambda x, y: 500 + 0.137 * x - 0.071 * y, half_extent_m=300)
        payload = terrain_payload(model)
        for r in range(model.rows):
            for c in range(model.cols):
                self.assertAlmostEqual(payload["values"][r][c], model.values[r][c], delta=0.5 * 10 ** -ELEVATION_DECIMALS + 1e-9)

    def test_interpolating_from_the_payload_gives_the_planners_elevation(self):
        model = fx.terrain(fx.ridge(700, 60, 120), half_extent_m=400)
        payload = terrain_payload(model)
        frame = fx.frame()
        for x, y in [(0, 0), (-123.4, 55.5), (310, -290), (7.5, 399), (-400, -400), (60, 0)]:
            lng, lat = frame.inverse(x, y)
            self.assertAlmostEqual(bilinear(payload, lng, lat), model.elevation(lng, lat), delta=0.01)
        lng, lat = frame.inverse(900, 0)
        self.assertIsNone(bilinear(payload, lng, lat))
        self.assertIsNone(model.elevation(lng, lat))

    def test_missing_nodes_stay_missing(self):
        model = fx.terrain(fx.flat(300), half_extent_m=200, hole=lambda x, y: x > 100)
        payload = terrain_payload(model)
        flat = [v for row in payload["values"] for v in row]
        self.assertIn(None, flat)
        self.assertEqual(flat.count(None), sum(1 for row in model.values for v in row if v is None))
        self.assertLess(payload["coverage"], 1.0)
        self.assertEqual((payload["min_m"], payload["max_m"]), (300.0, 300.0))
        frame = fx.frame()
        lng, lat = frame.inverse(180, 0)
        self.assertIsNone(bilinear(payload, lng, lat))                  # unknown stays unknown
        json.dumps(payload)

    def test_a_grid_with_no_data_at_all(self):
        model = TerrainModel(south=0.0, west=0.0, north=1.0, east=1.0, rows=2, cols=2,
                             values=[[None, None], [None, None]], source=TerrainSource(id="t", label="T"))
        payload = terrain_payload(model)
        self.assertIsNone(payload["min_m"])
        self.assertIsNone(payload["max_m"])
        self.assertEqual(payload["coverage"], 0.0)

    def test_does_not_touch_the_model(self):
        model = fx.terrain(fx.slope(), half_extent_m=200)
        before = copy.deepcopy(model.to_dict())
        payload = terrain_payload(model)
        payload["values"][0][0] = -1.0
        payload["source"]["label"] = "changed"
        self.assertEqual(model.to_dict(), before)

    def test_height_above_ground_from_the_payload_matches_the_mission(self):
        # The point of sending the planner's own grid: the simulator's height
        # above ground at a waypoint is the mission's ``agl`` there.
        model = fx.terrain(fx.ridge(700, 60, 120), half_extent_m=600)
        definition = fx.area_plan(fx.rect(600, 300), takeoff_m=(-300, 0), altitude_mode="terrain", altitude_m=80)
        mission = plan_mission(definition, fx.drone(), fx.camera(), terrain=model)
        payload = terrain_payload(model)
        take = bilinear(payload, mission["takeoff"]["lng"], mission["takeoff"]["lat"])
        self.assertAlmostEqual(take, mission["takeoff"]["ground_m"], delta=0.06)
        for wp in mission["waypoints"]:
            ground = bilinear(payload, wp["lng"], wp["lat"])
            self.assertAlmostEqual(ground, wp["ground"], delta=0.08)
            self.assertAlmostEqual(take + wp["alt"] - ground, wp["agl"], delta=0.2)


if __name__ == "__main__":
    unittest.main()
