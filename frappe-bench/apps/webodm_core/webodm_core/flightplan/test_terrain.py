"""Terrain: the elevation grid, the services behind it, terrain following and clearance."""

import math
import unittest

from webodm_core.flightplan import _fixtures as fx
from webodm_core.flightplan import plan_mission
from webodm_core.flightplan.elevation import (CallableProvider, ElevationError, OpenElevation, OpenMeteo,
                                              OpenTopoData, build_terrain, provider_from_config)
from webodm_core.flightplan.terrain import TerrainModel, TerrainSource, grid_spec, model_from_samples


def plan(definition, drone_id="dji-mavic-3e", terrain=None, camera_id=None):
    return plan_mission(definition, fx.drone(drone_id), fx.camera(camera_id, drone_id), terrain=terrain)


SRC = TerrainSource(id="t", label="T")


class TestTerrainModel(unittest.TestCase):
    def grid(self, values):
        return TerrainModel(south=0.0, west=0.0, north=1.0, east=1.0, rows=len(values), cols=len(values[0]),
                            values=values, source=SRC)

    def test_bilinear_interpolation(self):
        t = self.grid([[0.0, 10.0], [20.0, 30.0]])      # south row first
        self.assertEqual(t.elevation(0.0, 0.0), 0.0)
        self.assertEqual(t.elevation(1.0, 0.0), 10.0)
        self.assertEqual(t.elevation(0.0, 1.0), 20.0)
        self.assertEqual(t.elevation(1.0, 1.0), 30.0)
        self.assertAlmostEqual(t.elevation(0.5, 0.5), 15.0)
        self.assertAlmostEqual(t.elevation(0.25, 0.0), 2.5)

    def test_outside_the_grid_is_unknown(self):
        t = self.grid([[0.0, 10.0], [20.0, 30.0]])
        self.assertIsNone(t.elevation(1.5, 0.5))
        self.assertIsNone(t.elevation(0.5, -0.1))
        self.assertFalse(t.covers(2.0, 2.0))

    def test_voids_are_skipped_and_reported(self):
        t = self.grid([[None, 10.0], [20.0, 30.0]])
        self.assertAlmostEqual(t.coverage, 0.75)
        # Near the void the known corners are used; a cell with no data at all is unknown.
        self.assertAlmostEqual(t.elevation(0.5, 0.5), 20.0)
        self.assertIsNotNone(t.elevation(0.0, 0.0))
        empty = self.grid([[None, None], [None, None]])
        self.assertIsNone(empty.elevation(0.5, 0.5))
        self.assertIsNone(empty.range())
        self.assertEqual(t.range(), (10.0, 30.0))

    def test_serialisation_round_trip(self):
        t = fx.terrain(fx.slope(), half_extent_m=300)
        again = TerrainModel.from_dict(t.to_dict())
        self.assertEqual(again.values, t.values)
        self.assertEqual(again.source.label, "Test DEM")
        self.assertEqual(again.elevation(*fx.SITE), t.elevation(*fx.SITE))

    def test_shape_is_validated(self):
        with self.assertRaises(ValueError):
            TerrainModel(0, 0, 1, 1, 2, 2, [[1.0, 2.0]], SRC)
        with self.assertRaises(ValueError):
            TerrainModel(0, 0, 1, 1, 1, 2, [[1.0, 2.0]], SRC)

    def test_fixture_reproduces_its_surface(self):
        t = fx.terrain(fx.slope(700, 0.1, 0.05), half_extent_m=500, spacing_m=25)
        f = fx.frame()
        for x, y in [(0, 0), (120, -340), (-410, 77)]:
            self.assertAlmostEqual(t.elevation(*f.inverse(x, y)), 700 + 0.1 * x + 0.05 * y, delta=0.05)


class TestGridSpec(unittest.TestCase):
    BOX = (107.60, -6.83, 107.62, -6.81)       # about 2.2 km square

    def test_point_budget_is_respected(self):
        for budget in (16, 100, 400, 2000):
            spec = grid_spec(self.BOX, max_points=budget)
            self.assertLessEqual(spec.rows * spec.cols, budget)
            self.assertEqual(len(spec.points()), spec.rows * spec.cols)
            self.assertGreaterEqual(spec.rows, 2)

    def test_spacing_never_goes_below_the_data_resolution(self):
        small = (107.6100, -6.8200, 107.6110, -6.8195)
        spec = grid_spec(small, max_points=400, min_spacing_m=30, pad_m=0)
        self.assertGreaterEqual(spec.spacing_m, 25)
        fine = grid_spec(small, max_points=10000, min_spacing_m=5, pad_m=0)
        self.assertGreater(fine.rows * fine.cols, spec.rows * spec.cols)

    def test_larger_site_means_coarser_sampling(self):
        self.assertGreater(grid_spec(self.BOX, max_points=400).spacing_m, 100)

    def test_box_is_padded_and_snapped_so_edits_reuse_the_grid(self):
        a = grid_spec((107.61012, -6.82031, 107.61391, -6.81744))
        b = grid_spec((107.61015, -6.82029, 107.61388, -6.81746))    # a corner nudged by a few metres
        self.assertEqual((a.south, a.west, a.north, a.east), (b.south, b.west, b.north, b.east))
        self.assertLess(a.west, 107.61012)
        self.assertGreater(a.north, -6.81744)

    def test_points_run_south_west_to_north_east(self):
        spec = grid_spec(self.BOX, max_points=9)
        pts = spec.points()
        self.assertEqual(pts[0], (spec.south, spec.west))
        self.assertEqual(pts[-1], (spec.north, spec.east))

    def test_sample_count_must_match(self):
        spec = grid_spec(self.BOX, max_points=16)
        with self.assertRaises(ValueError):
            model_from_samples(spec, [1.0, 2.0], SRC)


class FakeHTTP:
    """Records calls and answers like the named service."""

    def __init__(self, kind, value=lambda lat, lng: 100.0 + lat, fail=None):
        self.kind, self.value, self.fail = kind, value, fail
        self.calls = []

    def __call__(self, method, url, *, params=None, json=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "params": params, "json": json, "headers": headers})
        if self.fail:
            raise ElevationError(self.fail)
        if self.kind == "opentopodata":
            pts = [tuple(map(float, p.split(","))) for p in params["locations"].split("|")]
            return {"status": "OK", "results": [{"elevation": self.value(lat, lng)} for lat, lng in pts]}
        if self.kind == "open-elevation":
            return {"results": [{"elevation": self.value(p["latitude"], p["longitude"])} for p in json["locations"]]}
        lats = [float(v) for v in params["latitude"].split(",")]
        lngs = [float(v) for v in params["longitude"].split(",")]
        return {"elevation": [self.value(a, b) for a, b in zip(lats, lngs)]}


class TestProviders(unittest.TestCase):
    POINTS = [(-6.82 + i * 0.0001, 107.61) for i in range(250)]

    def test_opentopodata_batches_and_paces_the_public_instance(self):
        http = FakeHTTP("opentopodata")
        naps = []
        p = OpenTopoData(fetch=http, sleep=naps.append)
        out = p.sample(self.POINTS)
        self.assertEqual(len(out), 250)
        self.assertAlmostEqual(out[0], 100.0 - 6.82)
        self.assertEqual(len(http.calls), 3)                       # 100 + 100 + 50
        self.assertEqual(len(naps), 2)                             # one pause between batches
        self.assertGreaterEqual(naps[0], 1.0)
        self.assertTrue(http.calls[0]["url"].endswith("/v1/mapzen"))
        self.assertEqual(http.calls[0]["params"]["locations"].count("|"), 99)
        self.assertIn("User-Agent", http.calls[0]["headers"])

    def test_self_hosted_opentopodata_is_not_paced(self):
        naps = []
        p = OpenTopoData(url="http://dem.internal/v1", dataset="eudem25m", fetch=FakeHTTP("opentopodata"),
                         sleep=naps.append)
        p.sample(self.POINTS)
        self.assertEqual(naps, [])
        self.assertIn("eudem25m", p.source().label)
        self.assertIn("dem.internal", p.source().label)

    def test_open_elevation_and_open_meteo(self):
        http = FakeHTTP("open-elevation")
        self.assertEqual(len(OpenElevation(fetch=http).sample(self.POINTS)), 250)
        self.assertEqual(http.calls[0]["method"], "POST")
        self.assertEqual(len(http.calls), 2)                       # 200 + 50
        http = FakeHTTP("open-meteo")
        out = OpenMeteo(fetch=http).sample(self.POINTS[:3])
        self.assertEqual(len(out), 3)
        self.assertEqual(OpenMeteo().source().surface, "surface")

    def test_voids_and_nonsense_become_unknown(self):
        values = iter([None, -32768, "n/a", 12.5, float("nan"), 9000.0, 9999.0])
        http = FakeHTTP("open-meteo", value=lambda lat, lng: next(values))
        out = OpenMeteo(fetch=http).sample(self.POINTS[:7])
        self.assertEqual(out[:4], [None, None, None, 12.5])
        self.assertIsNone(out[4])
        self.assertEqual(out[5], 9000.0)
        self.assertIsNone(out[6])

    def test_service_errors_are_reported_not_swallowed(self):
        with self.assertRaises(ElevationError):
            OpenTopoData(fetch=FakeHTTP("opentopodata", fail="down"), sleep=lambda s: None).sample(self.POINTS[:2])
        bad = lambda *a, **k: {"status": "INVALID_REQUEST", "error": "Too many locations"}  # noqa: E731
        with self.assertRaises(ElevationError) as cm:
            OpenTopoData(fetch=bad, sleep=lambda s: None).sample(self.POINTS[:2])
        self.assertIn("Too many locations", str(cm.exception))
        short = lambda *a, **k: {"elevation": [1.0]}  # noqa: E731
        with self.assertRaises(ElevationError):
            OpenMeteo(fetch=short).sample(self.POINTS[:2])

    def test_provider_from_config(self):
        self.assertIsInstance(provider_from_config({}), OpenTopoData)
        self.assertIsNone(provider_from_config({"provider": "none"}))
        p = provider_from_config({"provider": "open-elevation", "url": "http://x/api/v1", "vertical_accuracy_m": "3"})
        self.assertIsInstance(p, OpenElevation)
        self.assertEqual((p.url, p.vertical_accuracy_m), ("http://x/api/v1", 3.0))
        with self.assertRaises(ElevationError):
            provider_from_config({"provider": "google"})

    def test_build_terrain(self):
        spec = grid_spec((107.60, -6.83, 107.62, -6.81), max_points=100)
        model = build_terrain(OpenTopoData(fetch=FakeHTTP("opentopodata"), sleep=lambda s: None), spec)
        self.assertEqual(model.coverage, 1.0)
        self.assertEqual(model.source.id, "opentopodata")
        self.assertEqual(model.source.vertical_accuracy_m, 10.0)
        void = CallableProvider(func=lambda pts: [None] * len(pts))
        with self.assertRaises(ElevationError):
            build_terrain(void, spec)

    def test_callable_provider_describes_a_project_raster(self):
        p = CallableProvider(func=lambda pts: [5.0] * len(pts), provider_id="task:abc:dsm",
                             provider_label="Survey 1 DSM", provider_surface="surface", resolution_m=0.1)
        src = p.source()
        self.assertEqual((src.id, src.kind, src.surface), ("task:abc:dsm", "project-raster", "surface"))
        self.assertEqual(p.sample([(0.0, 0.0)] * 3), [5.0, 5.0, 5.0])


class TestConstantAltitudeOverTerrain(unittest.TestCase):
    def test_flat_ground_matches_the_flat_assumption(self):
        base = plan(fx.area_plan(takeoff_m=(0, -170)))
        m = plan(fx.area_plan(takeoff_m=(0, -170)), terrain=fx.terrain(fx.flat(700)))
        t = m["terrain"]
        self.assertEqual((t["agl_min_m"], t["agl_max_m"], t["clearance_min_m"]), (100.0, 100.0, 100.0))
        self.assertEqual(t["relief_m"], 0.0)
        self.assertAlmostEqual(t["side_overlap_min"], 70.0, delta=0.1)
        self.assertEqual(m["stats"], base["stats"])
        self.assertEqual(m["takeoff"]["ground_m"], 700.0)
        self.assertEqual(fx.assurance(m, "terrain")["status"], "estimated")
        self.assertEqual([w["agl"] for w in m["waypoints"]], [100.0] * len(m["waypoints"]))

    def test_rising_ground_eats_height_resolution_and_overlap(self):
        # Ground rises 10 m per 100 m eastwards; takeoff on the west edge.
        m = plan(fx.area_plan(fx.rect(600, 300), takeoff_m=(-300, 0), altitude_m=100),
                 terrain=fx.terrain(fx.slope(700, 0.10)))
        t = m["terrain"]
        self.assertAlmostEqual(t["agl_min_m"], 40.0, delta=1.0)     # 100 - 60 m of rise
        self.assertAlmostEqual(t["agl_max_m"], 100.0, delta=1.0)
        self.assertAlmostEqual(t["relief_m"], 60.0, delta=1.0)
        self.assertLess(t["gsd_min_cm"], 0.45 * t["gsd_max_cm"])
        self.assertAlmostEqual(t["side_overlap_min"], (1 - 0.3 * 100 / 40) * 100, delta=2.5)
        self.assertIn("TERRAIN_OVERLAP", fx.codes(m, "warning"))
        issue = next(i for i in m["issues"] if i["code"] == "TERRAIN_OVERLAP")
        self.assertEqual(issue["basis"], "estimated")
        # All waypoints are still at the same height above takeoff.
        self.assertEqual({w["alt"] for w in m["waypoints"]}, {100.0})
        self.assertAlmostEqual(min(w["agl"] for w in m["waypoints"]), 40.0, delta=1.0)

    def test_ground_higher_than_the_flight_is_an_error(self):
        m = plan(fx.area_plan(fx.rect(600, 300), takeoff_m=(-300, 0), altitude_m=50),
                 terrain=fx.terrain(fx.slope(700, 0.10)))
        issue = next(i for i in m["issues"] if i["code"] == "CLEARANCE_BELOW_MIN")
        self.assertEqual((issue["severity"], issue["basis"]), ("error", "estimated"))
        self.assertLess(issue["detail"]["clearance_m"], 0)
        self.assertEqual(len(issue["detail"]["at"]), 2)
        self.assertFalse(m["summary"]["exportable"])

    def test_clearance_inside_the_data_uncertainty_is_a_warning(self):
        # Minimum clearance 20 m, estimated 22 m, data +/- 5 m.
        m = plan(fx.area_plan(fx.rect(600, 300), takeoff_m=(-300, 0), altitude_m=82),
                 terrain=fx.terrain(fx.slope(700, 0.10)))
        self.assertAlmostEqual(m["terrain"]["clearance_min_m"], 22.0, delta=1.0)
        self.assertIn("CLEARANCE_MARGIN", fx.codes(m, "warning"))
        self.assertNotIn("CLEARANCE_BELOW_MIN", fx.codes(m))
        relaxed = plan(fx.area_plan(fx.rect(600, 300), takeoff_m=(-300, 0), altitude_m=82, min_clearance_m=10),
                       terrain=fx.terrain(fx.slope(700, 0.10)))
        self.assertNotIn("CLEARANCE_MARGIN", fx.codes(relaxed))

    def test_valley_below_takeoff_raises_the_height_above_ground(self):
        # Takeoff on the high east side: the route is far above the low west side.
        m = plan(fx.area_plan(fx.rect(600, 300), takeoff_m=(300, 0), altitude_m=100),
                 terrain=fx.terrain(fx.slope(700, 0.10)))
        self.assertAlmostEqual(m["terrain"]["agl_max_m"], 160.0, delta=1.0)
        issue = next(i for i in m["issues"] if i["code"] == "AGL_LIMIT")
        self.assertNotIn("assuming flat ground", issue["message"])
        self.assertIn("TERRAIN_RELIEF", fx.codes(m, "info"))

    def test_ridge_between_takeoff_and_area_is_caught_on_the_transit(self):
        # Survey east of a 60 m ridge, takeoff west of it, flying at 60 m.
        area = fx.polygon([(200, -100), (500, -100), (500, 100), (200, 100)])
        m = plan(fx.area_plan(area, takeoff_m=(-300, 0), altitude_m=60), terrain=fx.terrain(fx.ridge(700, 60, 120)))
        self.assertGreaterEqual(m["terrain"]["clearance_min_m"], 59.0)      # the survey itself is clear
        issue = next(i for i in m["issues"] if i["code"] == "TRANSIT_CLEARANCE")
        self.assertEqual(issue["severity"], "error")
        self.assertLess(issue["detail"]["clearance_m"], 5.0)
        # A higher return height does not help the outbound leg; taking off beyond the ridge does.
        ok = plan(fx.area_plan(area, takeoff_m=(350, -150), altitude_m=60), terrain=fx.terrain(fx.ridge(700, 60, 120)))
        self.assertNotIn("TRANSIT_CLEARANCE", fx.codes(ok))

    def test_service_ceiling_uses_the_terrain_elevation(self):
        m = plan(fx.area_plan(takeoff_m=(0, -170), altitude_m=120), "dji-mini-4-pro",
                 terrain=fx.terrain(fx.flat(3950)))
        self.assertIn("SERVICE_CEILING", fx.codes(m, "error"))
        high = plan(fx.area_plan(takeoff_m=(0, -170), altitude_m=100), "dji-mini-4-pro",
                    terrain=fx.terrain(fx.flat(3400)))
        self.assertIn("HIGH_ALTITUDE", fx.codes(high, "info"))
        self.assertNotIn("SERVICE_CEILING", fx.codes(high))

    def test_profile_is_bounded_and_ordered(self):
        m = plan(fx.area_plan(fx.rect(1500, 1200), takeoff_m=(-750, 0), altitude_m=120),
                 terrain=fx.terrain(fx.slope(700, 0.02)))
        profile = m["terrain"]["profile"]
        self.assertLessEqual(len(profile), 400)
        self.assertEqual([p[0] for p in profile], sorted(p[0] for p in profile))
        self.assertAlmostEqual(profile[-1][0], m["stats"]["survey_distance_m"], delta=2.0)
        self.assertTrue(all(len(p) == 3 for p in profile))


class TestTerrainFollowing(unittest.TestCase):
    def follow(self, surface, **kw):
        definition = fx.area_plan(fx.rect(600, 300), takeoff_m=kw.pop("takeoff_m", (-300, 0)),
                                  altitude_mode="terrain", altitude_m=kw.pop("altitude_m", 80), **kw)
        return plan(definition, terrain=fx.terrain(surface))

    def test_height_above_ground_stays_at_the_target(self):
        m = self.follow(fx.slope(700, 0.10))
        t = m["terrain"]
        self.assertTrue(t["following"])
        self.assertAlmostEqual(t["agl_min_m"], 80.0, delta=t["tolerance_m"] + 0.5)
        self.assertAlmostEqual(t["agl_max_m"], 80.0, delta=t["tolerance_m"] + 0.5)
        self.assertAlmostEqual(t["side_overlap_min"], 70.0, delta=4.0)
        # Waypoint heights climb with the ground: about +60 m across the area.
        alts = [w["alt"] for w in m["waypoints"]]
        self.assertAlmostEqual(max(alts) - min(alts), 60.0, delta=2.0)
        self.assertAlmostEqual(min(alts), 80.0, delta=2.0)
        self.assertNotIn("TERRAIN_OVERLAP", fx.codes(m))
        self.assertNotIn("CLEARANCE_BELOW_MIN", fx.codes(m))

    def test_a_uniform_slope_needs_no_extra_waypoints(self):
        m = self.follow(fx.slope(700, 0.10))
        self.assertEqual(m["stats"]["waypoint_count"], 2 * m["stats"]["leg_count"])

    def test_a_ridge_adds_waypoints_only_where_the_ground_bends(self):
        m = self.follow(fx.ridge(700, 60, 120), takeoff_m=(-300, 0))
        wps = m["waypoints"]
        vias = [w for w in wps if w["role"] == "via"]
        self.assertTrue(vias)
        self.assertLess(len(wps), 12 * m["stats"]["leg_count"])
        f = fx.frame()
        # Extra waypoints sit on the ridge (|x| < ~150 m), not on the flat approaches.
        self.assertTrue(all(abs(f.forward(w["lng"], w["lat"])[0]) < 160 for w in vias))
        # And the flown height above ground stays within the tolerance everywhere.
        t = m["terrain"]
        self.assertGreaterEqual(t["agl_min_m"], 80 - t["tolerance_m"] - 1.0)
        self.assertLessEqual(t["agl_max_m"], 80 + t["tolerance_m"] + 1.0)

    def test_tighter_tolerance_means_more_waypoints(self):
        loose = self.follow(fx.ridge(700, 60, 120), terrain_tolerance_m=10)
        tight = self.follow(fx.ridge(700, 60, 120), terrain_tolerance_m=1)
        self.assertGreater(tight["stats"]["waypoint_count"], loose["stats"]["waypoint_count"])
        self.assertEqual(tight["terrain"]["tolerance_m"], 1.0)

    def test_steep_ground_at_speed_exceeds_the_climb_rate(self):
        # 60 m up over 120 m along the line, at 10 m/s, is a 5 m/s climb... on a Mavic 3E (6 m/s) fine;
        steady = self.follow(fx.ridge(700, 60, 120), speed_ms=10)
        self.assertNotIn("CLIMB_RATE", fx.codes(steady))
        self.assertAlmostEqual(steady["stats"]["max_climb_ms"], 5.0, delta=0.6)
        # ... a PX4 multirotor limited to 3 m/s up and 1.5 m/s down cannot follow it.
        definition = fx.area_plan(fx.rect(600, 300), takeoff_m=(-300, 0), altitude_mode="terrain", altitude_m=80,
                                  speed_ms=10)
        slow = plan(definition, "generic-mavlink-multirotor", terrain=fx.terrain(fx.ridge(700, 60, 120)))
        issue = next(i for i in slow["issues"] if i["code"] == "CLIMB_RATE")
        self.assertEqual((issue["severity"], issue["basis"]), ("warning", "estimated"))
        self.assertLess(issue["detail"]["max_speed_ms"], 4.0)

    def test_route_below_takeoff_is_flagged(self):
        m = self.follow(fx.slope(700, 0.10), takeoff_m=(300, 0), altitude_m=40)
        self.assertLess(m["stats"]["min_altitude_m"], 0)
        self.assertIn("BELOW_TAKEOFF", fx.codes(m, "warning"))

    def test_corridor_follows_terrain_and_keeps_its_bends(self):
        definition = fx.corridor_plan(((-500, 0), (0, 0), (400, 300)), width=60, takeoff_m=(-500, -20),
                                      altitude_mode="terrain", altitude_m=70)
        m = plan(definition, terrain=fx.terrain(fx.ridge(700, 50, 150)))
        f = fx.frame()
        xs = [tuple(round(v) for v in f.forward(w["lng"], w["lat"])) for w in m["waypoints"]]
        self.assertIn((0, 0), xs)                                   # the bend is still a waypoint
        self.assertGreater(len(m["waypoints"]), 3)
        self.assertAlmostEqual(m["terrain"]["agl_min_m"], 70, delta=m["terrain"]["tolerance_m"] + 1)

    def test_without_data_terrain_following_is_refused_but_still_drawn(self):
        m = plan(fx.area_plan(altitude_mode="terrain"))
        issue = next(i for i in m["issues"] if i["code"] == "TERRAIN_DATA_MISSING")
        self.assertEqual((issue["severity"], issue["basis"]), ("error", "not_checked"))
        self.assertIsNone(m["terrain"])
        self.assertGreater(m["stats"]["waypoint_count"], 0)         # the route is still there to look at
        self.assertFalse(m["summary"]["exportable"])
        self.assertEqual(fx.assurance(m, "terrain")["status"], "not_checked")

    def test_takeoff_outside_the_data_counts_as_no_data(self):
        m = plan(fx.area_plan(takeoff_m=(5000, 0), altitude_mode="terrain"),
                 terrain=fx.terrain(fx.flat(), half_extent_m=600))
        self.assertIn("TERRAIN_DATA_MISSING", fx.codes(m, "error"))
        self.assertIn("does not cover the takeoff point", fx.assurance(m, "terrain")["summary"])

    def test_gaps_in_the_data_are_reported(self):
        holed = fx.terrain(fx.flat(), hole=lambda x, y: 50 < x < 250 and -80 < y < 80)
        m = plan(fx.area_plan(fx.rect(600, 300), takeoff_m=(-300, 0)), terrain=holed)
        self.assertIn("TERRAIN_PARTIAL", fx.codes(m, "warning"))
        self.assertGreater(m["terrain"]["missing_samples"], 0)

    def test_aircraft_whose_app_plans_altitude_still_gets_the_estimate(self):
        # WingtraPilot follows terrain itself: the plan estimates that flight
        # (so no false clearance alarm) and says the heights cannot be loaded.
        definition = fx.area_plan(fx.rect(600, 300), takeoff_m=(-300, 0), altitude_mode="terrain", altitude_m=120)
        m = plan(definition, "wingtra-wingtraone-gen2", terrain=fx.terrain(fx.slope(700, 0.10)))
        self.assertIn("TERRAIN_FOLLOW_BY_APP", fx.codes(m, "warning"))
        self.assertTrue(m["terrain"]["following"])
        self.assertAlmostEqual(m["terrain"]["agl_min_m"], 120.0, delta=m["terrain"]["tolerance_m"] + 1)
        self.assertNotIn("CLEARANCE_BELOW_MIN", fx.codes(m))
        constant = plan(fx.area_plan(fx.rect(600, 300), takeoff_m=(-300, 0), altitude_m=50),
                        "wingtra-wingtraone-gen2", terrain=fx.terrain(fx.slope(700, 0.10)))
        self.assertNotIn("TERRAIN_FOLLOW_BY_APP", fx.codes(constant))
        self.assertIn("CLEARANCE_BELOW_MIN", fx.codes(constant, "error"))

    def test_coarse_sampling_is_pointed_out(self):
        coarse = fx.terrain(fx.flat(), half_extent_m=3000, spacing_m=400,
                            source=TerrainSource(id="d", label="D", resolution_m=30, vertical_accuracy_m=10))
        m = plan(fx.area_plan(takeoff_m=(0, -170)), terrain=coarse)
        self.assertIn("TERRAIN_COARSE", fx.codes(m, "info"))
        self.assertIn("sampled every", fx.assurance(m, "terrain")["summary"])

    def test_surface_models_are_described_as_such(self):
        dsm = fx.terrain(fx.flat(), source=TerrainSource(id="task:t1:dsm", label="Survey DSM", kind="project-raster",
                                                         resolution_m=0.1, surface="surface"))
        m = plan(fx.area_plan(takeoff_m=(0, -170)), terrain=dsm)
        summary = fx.assurance(m, "terrain")["summary"]
        self.assertIn("surface model", summary)
        self.assertIn("accuracy not stated", summary)


class TestObstacles(unittest.TestCase):
    def test_declared_obstacle_is_compared_with_the_lowest_point(self):
        base = fx.area_plan(takeoff_m=(0, -170), altitude_m=60)
        ok = plan({**base, "params": {**base["params"], "obstacle_height_m": 30}})
        self.assertNotIn("OBSTACLE_MARGIN", fx.codes(ok))
        thin = plan({**base, "params": {**base["params"], "obstacle_height_m": 50}})
        self.assertIn("OBSTACLE_MARGIN", fx.codes(thin, "warning"))
        hit = plan({**base, "params": {**base["params"], "obstacle_height_m": 58}})
        self.assertIn("OBSTACLE_CLEARANCE", fx.codes(hit, "error"))
        self.assertFalse(hit["summary"]["exportable"])

    def test_obstacles_are_never_reported_as_checked(self):
        for height in (0, 40):
            definition = fx.area_plan(takeoff_m=(0, -170), obstacle_height_m=height)
            m = plan(definition, terrain=fx.terrain(fx.flat()))
            self.assertEqual(fx.assurance(m, "obstacles")["status"], "not_checked")
        self.assertIn("40 m", fx.assurance(m, "obstacles")["summary"])

    def test_obstacle_check_uses_the_terrain_estimate_when_there_is_one(self):
        definition = fx.area_plan(fx.rect(600, 300), takeoff_m=(-300, 0), altitude_m=100, obstacle_height_m=30)
        m = plan(definition, terrain=fx.terrain(fx.slope(700, 0.10)))      # lowest point 40 m above ground
        self.assertIn("OBSTACLE_MARGIN", fx.codes(m, "warning"))


if __name__ == "__main__":
    unittest.main()
