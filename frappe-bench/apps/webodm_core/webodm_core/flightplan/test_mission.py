"""plan_mission: estimates, capability checks and what is and is not claimed."""

import json
import math
import unittest

from webodm_core.flightplan import PlanError, plan_mission
from webodm_core.flightplan import _fixtures as fx
from webodm_core.flightplan.issues import NOTICE
from webodm_core.flightplan.mission import Limits
from webodm_core.flightplan.params import SPECS, normalize_params
from webodm_core.flightplan.profiles import builtin_cameras, builtin_drones, normalize_drone


def plan(definition=None, drone_id="dji-mavic-3e", camera_id=None, **kw):
    d = fx.drone(drone_id)
    return plan_mission(definition or fx.area_plan(), d, fx.camera(camera_id, drone_id), **kw)


class TestEstimates(unittest.TestCase):
    def test_mission_is_json_serialisable_and_self_describing(self):
        m = plan()
        again = json.loads(json.dumps(m))
        self.assertEqual(again["version"], 1)
        self.assertEqual(again["notice"], NOTICE)
        self.assertEqual(again["equipment"]["drone"]["id"], "dji-mavic-3e")
        self.assertEqual(again["equipment"]["camera"]["id"], "dji-mavic-3e-wide")
        for key in ("legs", "waypoints", "flights", "stats", "camera", "layout", "issues", "assurance",
                    "coverage", "footprint", "takeoff", "frame", "params", "summary"):
            self.assertIn(key, again)

    def test_camera_geometry(self):
        m = plan(fx.area_plan(altitude_m=100, front_overlap=80, side_overlap=70))
        cam = m["camera"]
        self.assertAlmostEqual(cam["gsd_cm"], 2.73, delta=0.01)
        self.assertAlmostEqual(cam["footprint_across_m"], 144.3, delta=0.1)
        self.assertAlmostEqual(cam["footprint_along_m"], 108.2, delta=0.1)
        self.assertAlmostEqual(cam["line_spacing_m"], 43.28, delta=0.02)
        self.assertAlmostEqual(cam["photo_spacing_m"], 21.64, delta=0.02)
        self.assertAlmostEqual(cam["photo_interval_s"], cam["photo_spacing_m"] / 10.0, delta=0.01)

    def test_counts_and_distance_for_a_rectangle(self):
        m = plan(fx.area_plan(fx.rect(400, 300), altitude_m=100))
        s = m["stats"]
        self.assertAlmostEqual(s["area_m2"], 120000, delta=5)
        self.assertEqual(s["line_count"], 7)                       # ceil(300 / 43.28)
        self.assertEqual(s["waypoint_count"], 14)
        self.assertEqual(m["layout"]["direction_deg"], 90.0)
        # 7 lines of 400 m plus 6 turns of one line spacing.
        self.assertAlmostEqual(s["survey_distance_m"], 7 * 400 + 6 * 43.28, delta=1.0)
        # Each 400 m line holds floor(400 / 21.64) + 1 = 19 photos.
        self.assertEqual(s["photo_count"], 7 * 19)
        self.assertEqual([leg["photos"] for leg in m["legs"]], [19] * 7)

    def test_photo_count_tracks_area_over_photo_footprint(self):
        m = plan(fx.area_plan(fx.rect(1200, 900), altitude_m=120))
        cam, s = m["camera"], m["stats"]
        expected = s["area_m2"] / (cam["line_spacing_m"] * cam["photo_spacing_m"])
        self.assertAlmostEqual(s["photo_count"] / expected, 1.0, delta=0.08)

    def test_duration_model(self):
        m = plan(fx.area_plan(fx.rect(400, 300), altitude_m=100, speed_ms=10))
        s = m["stats"]
        # Lines and turns at 10 m/s, plus (10 / 2.5 + 2) s for each of the 14 stops.
        self.assertAlmostEqual(s["survey_duration_s"], s["survey_distance_m"] / 10 + 14 * 6.0, delta=0.5)
        f = m["flights"][0]
        # Takeoff assumed at the first waypoint: climb 100 m at 6 m/s, return from the last
        # waypoint, descend at 6 m/s, 20 s on the ground.
        back = math.hypot(400, 6 * 43.28)
        self.assertAlmostEqual(f["duration_s"], s["survey_duration_s"] + 20 + 100 / 6 + back / 10 + 100 / 6, delta=1.0)
        self.assertEqual(s["total_duration_s"], f["duration_s"])
        self.assertEqual(s["batteries"], 1)

    def test_halving_the_speed_roughly_doubles_the_line_time(self):
        fast = plan(fx.area_plan(speed_ms=10))["stats"]["survey_duration_s"]
        slow = plan(fx.area_plan(speed_ms=5))["stats"]["survey_duration_s"]
        self.assertTrue(1.6 < slow / fast < 2.0)

    def test_plan_by_gsd_derives_the_height(self):
        m = plan(fx.area_plan(altitude_from="gsd", gsd_cm=2.0, altitude_m=300))
        self.assertAlmostEqual(m["camera"]["gsd_cm"], 2.0, delta=0.005)
        self.assertAlmostEqual(m["camera"]["altitude_m"], 73.2, delta=0.1)
        self.assertEqual(m["waypoints"][0]["alt"], m["camera"]["altitude_m"])
        with self.assertRaises(PlanError) as cm:
            plan(fx.area_plan(altitude_from="gsd", gsd_cm=0.1))
        self.assertEqual(cm.exception.field, "gsd_cm")
        with self.assertRaises(PlanError):
            plan(fx.area_plan(altitude_from="gsd"))

    def test_higher_overlap_means_more_lines_and_photos(self):
        low = plan(fx.area_plan(front_overlap=70, side_overlap=60))["stats"]
        high = plan(fx.area_plan(front_overlap=85, side_overlap=80))["stats"]
        self.assertGreater(high["line_count"], low["line_count"])
        self.assertGreater(high["photo_count"], 2 * low["photo_count"])

    def test_crosshatch_roughly_doubles_the_mission(self):
        single = plan(fx.area_plan(fx.rect(400, 400)))["stats"]
        double = plan(fx.area_plan(fx.rect(400, 400), crosshatch=True))["stats"]
        self.assertEqual(double["line_count"], 2 * single["line_count"])
        self.assertTrue(1.8 < double["photo_count"] / single["photo_count"] < 2.2)

    def test_portrait_orientation_needs_more_lines_fewer_photos_per_line(self):
        land = plan(fx.area_plan())
        port = plan(fx.area_plan(camera_orientation="portrait"))
        self.assertGreater(port["stats"]["line_count"], land["stats"]["line_count"])
        self.assertGreater(port["camera"]["photo_spacing_m"], land["camera"]["photo_spacing_m"])

    def test_coverage_and_footprint_geometry(self):
        m = plan()
        ring = m["coverage"]["coordinates"][0]
        self.assertEqual(ring[0], ring[-1])
        self.assertEqual(len(ring), 5)
        self.assertEqual(len(m["footprint"]["coordinates"][0]), 5)

    def test_waypoints_are_ordered_and_tagged(self):
        m = plan()
        wps = m["waypoints"]
        self.assertEqual([w["index"] for w in wps], list(range(len(wps))))
        self.assertEqual([w["role"] for w in wps[:4]], ["start", "end", "start", "end"])
        self.assertEqual([w["photo"] for w in wps[:4]], [True, False, True, False])
        self.assertEqual(wps[0]["heading"], 90.0)
        self.assertEqual(wps[1]["heading"], 90.0)       # a line end keeps the line's heading
        self.assertEqual(wps[2]["heading"], 270.0)
        for leg in m["legs"]:
            self.assertEqual(wps[leg["from"]]["role"], "start")
            self.assertEqual(wps[leg["to"]]["role"], "end")

    def test_same_input_same_output(self):
        self.assertEqual(json.dumps(plan(), sort_keys=True), json.dumps(plan(), sort_keys=True))


class TestEquipmentChangesThePlan(unittest.TestCase):
    """The same area with different aircraft and cameras gives different missions."""

    AREA = fx.rect(600, 400)

    def test_cameras_change_resolution_spacing_and_counts(self):
        rows = {}
        for drone_id, cam_id in [
            ("dji-mavic-3e", None), ("dji-matrice-350-rtk", "dji-zenmuse-p1-35mm"),
            ("dji-matrice-350-rtk", "dji-zenmuse-p1-50mm"), ("dji-mini-4-pro", "dji-mini-4-pro-12mp"),
            ("generic-mavlink-multirotor", "micasense-rededge-mx"), ("dji-phantom-4-pro-v2", None),
        ]:
            m = plan(fx.area_plan(self.AREA, altitude_m=100, speed_ms=5), drone_id, cam_id)
            rows[(drone_id, cam_id)] = (m["camera"]["gsd_cm"], m["stats"]["line_count"], m["stats"]["photo_count"])
        gsds = [r[0] for r in rows.values()]
        self.assertEqual(len(set(gsds)), len(gsds))
        # A longer lens resolves finer and needs more lines for the same area.
        p35 = rows[("dji-matrice-350-rtk", "dji-zenmuse-p1-35mm")]
        p50 = rows[("dji-matrice-350-rtk", "dji-zenmuse-p1-50mm")]
        self.assertLess(p50[0], p35[0])
        self.assertGreater(p50[1], p35[1])
        self.assertAlmostEqual(p35[0], 1.25, delta=0.01)
        # The 1.2 MP multispectral camera is far coarser than any RGB mapping camera.
        self.assertGreater(rows[("generic-mavlink-multirotor", "micasense-rededge-mx")][0], 6.0)

    def test_endurance_changes_the_battery_count(self):
        big = fx.rect(1500, 1200)
        definition = fx.area_plan(big, altitude_m=80, speed_ms=8)
        m350 = plan(definition, "dji-matrice-350-rtk")
        mini = plan(definition, "dji-mini-4-pro")
        self.assertGreater(mini["stats"]["batteries"], m350["stats"]["batteries"])
        self.assertGreater(m350["stats"]["usable_flight_time_s"], mini["stats"]["usable_flight_time_s"])

    def test_default_speed_comes_from_the_aircraft(self):
        self.assertEqual(plan(drone_id="dji-mavic-3e")["stats"]["speed_ms"], 10)
        self.assertEqual(plan(drone_id="dji-mini-4-pro")["stats"]["speed_ms"], 6)
        self.assertEqual(plan(drone_id="wingtra-wingtraone-gen2")["stats"]["speed_ms"], 16)

    def test_snapshot_survives_catalog_independent_use(self):
        m = plan(drone_id="dji-air-2s")
        d = m["equipment"]["drone"]
        self.assertEqual(d["mission"]["formats"][0]["id"], "litchi-csv")
        self.assertEqual(normalize_drone(d, source="builtin"), builtin_drones()["dji-air-2s"])


class TestFlights(unittest.TestCase):
    def test_long_mission_is_split_at_line_ends_within_usable_time(self):
        m = plan(fx.area_plan(fx.rect(2000, 1500), altitude_m=80, speed_ms=8), "dji-mini-4-pro")
        flights = m["flights"]
        self.assertGreater(len(flights), 1)
        usable = m["stats"]["usable_flight_time_s"]
        self.assertAlmostEqual(usable, 34 * 60 * 0.75 * 0.75, delta=0.5)
        for f in flights:
            self.assertLessEqual(f["duration_s"], usable + 1e-6)
            self.assertFalse(f["exceeds_endurance"])
        # Contiguous, complete, each starting on a line start.
        self.assertEqual(flights[0]["leg_from"], 0)
        self.assertEqual(flights[-1]["leg_to"], len(m["legs"]) - 1)
        for a, b in zip(flights, flights[1:]):
            self.assertEqual(b["leg_from"], a["leg_to"] + 1)
            self.assertEqual(m["waypoints"][b["wp_from"]]["role"], "start")
        self.assertEqual(sum(f["photos"] for f in flights), m["stats"]["photo_count"])
        self.assertIn("MULTIPLE_FLIGHTS", fx.codes(m, "info"))
        self.assertEqual(m["stats"]["batteries"], len(flights))

    def test_a_far_takeoff_point_costs_flights(self):
        area = fx.rect(1500, 1200)
        near = plan(fx.area_plan(area, takeoff_m=(-760, -610), altitude_m=80, speed_ms=8), "dji-mini-4-pro")
        far = plan(fx.area_plan(area, takeoff_m=(-3500, -610), altitude_m=80, speed_ms=8), "dji-mini-4-pro")
        self.assertGreater(far["stats"]["total_distance_m"], near["stats"]["total_distance_m"] + 5000)
        self.assertGreaterEqual(far["stats"]["batteries"], near["stats"]["batteries"])
        self.assertGreater(far["stats"]["total_duration_s"], near["stats"]["total_duration_s"])

    def test_a_line_that_cannot_fit_one_battery_is_an_error(self):
        # A 10 km corridor at 3 m/s takes ~55 min: more than a Mini 4 Pro has.
        m = plan(fx.corridor_plan(((0, 0), (10000, 0)), width=60, speed_ms=3), "dji-mini-4-pro")
        self.assertIn("LEG_EXCEEDS_ENDURANCE", fx.codes(m, "error"))
        self.assertFalse(m["summary"]["exportable"])
        self.assertTrue(m["flights"][0]["exceeds_endurance"])

    def test_reserve_reduces_usable_time(self):
        a = plan(fx.area_plan(reserve_percent=10))["stats"]["usable_flight_time_s"]
        b = plan(fx.area_plan(reserve_percent=40))["stats"]["usable_flight_time_s"]
        self.assertAlmostEqual(a / b, 0.9 / 0.6, places=3)


class TestCorridor(unittest.TestCase):
    def test_corridor_mission(self):
        m = plan(fx.corridor_plan(width=120, altitude_m=80))
        self.assertEqual(m["type"], "corridor")
        # Swath at 80 m is 115 m: two passes are needed for 120 m.
        self.assertEqual(m["layout"]["passes"], 2)
        self.assertIsNone(m["layout"]["direction_deg"])
        centre = 600 + math.hypot(500, 300)
        self.assertAlmostEqual(m["stats"]["area_m2"], centre * 120, delta=1)
        self.assertAlmostEqual(sum(leg["length_m"] for leg in m["legs"]), 2 * centre, delta=20)
        self.assertEqual([w["role"] for w in m["waypoints"][:3]], ["start", "via", "end"])
        self.assertEqual(m["coverage"]["type"], "Polygon")

    def test_width_drives_the_pass_count(self):
        passes = [plan(fx.corridor_plan(width=w, altitude_m=80))["layout"]["passes"] for w in (60, 120, 200, 400)]
        self.assertEqual(passes, sorted(passes))
        self.assertEqual(passes[0], 1)
        self.assertGreater(passes[-1], passes[0])

    def test_single_pass_is_pointed_out_and_min_passes_fixes_it(self):
        m = plan(fx.corridor_plan(width=60, altitude_m=80))
        self.assertIn("CORRIDOR_SINGLE_PASS", fx.codes(m, "info"))
        m2 = plan(fx.corridor_plan(width=60, altitude_m=80, corridor_min_passes=3))
        self.assertEqual(m2["layout"]["passes"], 3)
        self.assertNotIn("CORRIDOR_SINGLE_PASS", fx.codes(m2))

    def test_corridor_width_is_required_and_bounded(self):
        for width in (None, "wide", 0, 6000):
            d = fx.corridor_plan()
            d["corridor_width_m"] = width
            with self.assertRaises(PlanError) as cm:
                plan(d)
            self.assertEqual(cm.exception.field, "corridor_width_m")

    def test_self_crossing_corridor_is_refused(self):
        with self.assertRaises(PlanError):
            plan(fx.corridor_plan(((0, 0), (500, 0), (500, 400), (200, -300))))


class TestCapabilityChecks(unittest.TestCase):
    def test_clean_plan_has_no_errors_or_warnings(self):
        m = plan(fx.area_plan(takeoff_m=(0, -170)))
        self.assertEqual(fx.codes(m, "error"), [])
        self.assertEqual(fx.codes(m, "warning"), [])
        self.assertTrue(m["summary"]["exportable"])
        self.assertEqual(m["summary"], {"error": 0, "warning": 0, "info": 0, "worst": None, "exportable": True})

    def test_speed_above_the_aircraft_limit(self):
        m = plan(fx.area_plan(speed_ms=18))
        self.assertIn("SPEED_ABOVE_MAX", fx.codes(m, "error"))
        self.assertFalse(m["summary"]["exportable"])
        ok = plan(fx.area_plan(speed_ms=15, shutter_speed=2000))
        self.assertNotIn("SPEED_ABOVE_MAX", fx.codes(ok))

    def test_camera_cannot_keep_up(self):
        # Mini 4 Pro in 48 MP mode needs 5 s between photos.
        m = plan(fx.area_plan(altitude_m=60, speed_ms=8), "dji-mini-4-pro", "dji-mini-4-pro-48mp")
        issue = next(i for i in m["issues"] if i["code"] == "PHOTO_INTERVAL_TOO_SHORT")
        self.assertEqual(issue["severity"], "error")
        max_speed = issue["detail"]["max_speed_ms"]
        self.assertAlmostEqual(max_speed, m["camera"]["photo_spacing_m"] / 5.0, delta=0.01)
        # Flying at the suggested speed clears it.
        ok = plan(fx.area_plan(altitude_m=60, speed_ms=math.floor(max_speed * 10) / 10), "dji-mini-4-pro",
                  "dji-mini-4-pro-48mp")
        self.assertNotIn("PHOTO_INTERVAL_TOO_SHORT", fx.codes(ok))
        # The same flight in 12 MP mode (2 s) is fine.
        ok12 = plan(fx.area_plan(altitude_m=60, speed_ms=5), "dji-mini-4-pro", "dji-mini-4-pro-12mp")
        self.assertNotIn("PHOTO_INTERVAL_TOO_SHORT", fx.codes(ok12))

    def test_unpublished_interval_is_a_warning_not_an_error(self):
        m = plan(fx.area_plan(altitude_m=40, speed_ms=12, front_overlap=85), "dji-matrice-30t")
        self.assertIn("PHOTO_INTERVAL_UNCERTAIN", fx.codes(m, "warning"))
        self.assertNotIn("PHOTO_INTERVAL_TOO_SHORT", fx.codes(m))
        self.assertIn("INTERVAL_ASSUMED", fx.codes(m, "info"))

    def test_height_limit_above_takeoff(self):
        m = plan(fx.area_plan(altitude_m=600), "dji-mini-4-pro")
        self.assertIn("HEIGHT_ABOVE_TAKEOFF_LIMIT", fx.codes(m, "error"))

    def test_aircraft_without_waypoint_flight(self):
        m = plan(drone_id="dji-mini-3")
        self.assertIn("NO_WAYPOINT_SUPPORT", fx.codes(m, "warning"))

    def test_boundary_only_aircraft_are_labelled(self):
        for drone_id in ("wingtra-wingtraone-gen2", "ageagle-ebee-x", "dji-phantom-4-rtk"):
            m = plan(fx.area_plan(altitude_m=100), drone_id)
            self.assertIn("BOUNDARY_ONLY", fx.codes(m, "info"), drone_id)

    def test_fixed_wing_needs_airspeed_and_room_to_turn(self):
        slow = plan(fx.area_plan(speed_ms=8), "generic-mavlink-fixed-wing")
        self.assertIn("SPEED_BELOW_MIN", fx.codes(slow, "error"))
        m = plan(fx.area_plan(speed_ms=16), "generic-mavlink-fixed-wing")
        self.assertEqual(m["layout"]["overshoot_m"], 60.0)          # automatic: the turn radius
        self.assertNotIn("FIXED_WING_OVERSHOOT", fx.codes(m))
        self.assertIn("FIXED_WING_LINE_SPACING", fx.codes(m, "info"))
        short = plan(fx.area_plan(speed_ms=16, overshoot_m=10), "generic-mavlink-fixed-wing")
        self.assertIn("FIXED_WING_OVERSHOOT", fx.codes(short, "warning"))
        # A multirotor turns on the spot: no overshoot by default.
        self.assertEqual(plan()["layout"]["overshoot_m"], 0.0)

    def test_motion_blur_and_rolling_shutter(self):
        m = plan(fx.area_plan(altitude_m=40, speed_ms=12, shutter_speed=250), "dji-mavic-3-classic")
        blur = next(i for i in m["issues"] if i["code"] == "MOTION_BLUR")
        self.assertEqual(blur["severity"], "warning")
        self.assertGreater(blur["detail"]["blur_px"], 1.0)
        self.assertIn("ROLLING_SHUTTER", fx.codes(m, "info"))
        sharp = plan(fx.area_plan(altitude_m=100, speed_ms=10, shutter_speed=1000))
        self.assertNotIn("MOTION_BLUR", fx.codes(sharp))
        self.assertNotIn("ROLLING_SHUTTER", fx.codes(sharp))        # mechanical shutter

    def test_low_overlap(self):
        self.assertIn("LOW_OVERLAP", fx.codes(plan(fx.area_plan(front_overlap=60, side_overlap=40)), "warning"))
        self.assertNotIn("LOW_OVERLAP", fx.codes(plan(fx.area_plan(front_overlap=75, side_overlap=65))))

    def test_oblique_gimbal_is_noted(self):
        self.assertIn("NADIR_ASSUMED", fx.codes(plan(fx.area_plan(gimbal_pitch_deg=-60)), "info"))

    def test_takeoff_assumed_is_noted_until_set(self):
        m = plan()
        self.assertTrue(m["takeoff"]["assumed"])
        self.assertIn("TAKEOFF_ASSUMED", fx.codes(m, "info"))
        self.assertAlmostEqual(m["takeoff"]["lng"], m["waypoints"][0]["lng"], places=6)
        m2 = plan(fx.area_plan(takeoff_m=(-250, -200)))
        self.assertFalse(m2["takeoff"]["assumed"])
        self.assertNotIn("TAKEOFF_ASSUMED", fx.codes(m2))


class TestRulePresets(unittest.TestCase):
    def test_height_limit_is_advisory(self):
        m = plan(fx.area_plan(altitude_m=150))
        issue = next(i for i in m["issues"] if i["code"] == "AGL_LIMIT")
        self.assertEqual((issue["severity"], issue["basis"]), ("warning", "advisory"))
        self.assertIn("assuming flat ground", issue["message"])
        self.assertTrue(m["summary"]["exportable"])         # a warning, not a block
        self.assertNotIn("AGL_LIMIT", fx.codes(plan(fx.area_plan(altitude_m=120))))

    def test_presets_differ(self):
        self.assertIn("AGL_LIMIT", fx.codes(plan(fx.area_plan(altitude_m=121, regulation="easa-open"))))
        self.assertNotIn("AGL_LIMIT", fx.codes(plan(fx.area_plan(altitude_m=121, regulation="faa-part-107"))))
        self.assertNotIn("AGL_LIMIT", fx.codes(plan(fx.area_plan(altitude_m=300, regulation="custom"))))
        self.assertIn("AGL_LIMIT", fx.codes(plan(fx.area_plan(altitude_m=300, regulation="custom", max_agl_m=250))))
        self.assertNotIn("AGL_LIMIT", fx.codes(plan(fx.area_plan(altitude_m=150, max_agl_m=200))))

    def test_line_of_sight_distance(self):
        far = plan(fx.area_plan(fx.rect(1600, 300)))
        issue = next(i for i in far["issues"] if i["code"] == "VLOS_DISTANCE")
        self.assertEqual(issue["basis"], "advisory")
        self.assertNotIn("VLOS_DISTANCE", fx.codes(plan(fx.area_plan(fx.rect(1600, 300), vlos_m=3000))))
        self.assertNotIn("VLOS_DISTANCE", fx.codes(plan(fx.area_plan(fx.rect(300, 300), takeoff_m=(0, 0)))))

    def test_mass_limit(self):
        heavy = normalize_drone({**fx.drone("dji-matrice-350-rtk").to_dict(), "takeoff_mass_g": 26000})
        m = plan_mission(fx.area_plan(regulation="easa-open"), heavy, fx.camera(None, "dji-matrice-350-rtk"))
        self.assertIn("RULE_MASS", fx.codes(m, "warning"))


class TestAssurance(unittest.TestCase):
    def test_every_topic_states_how_far_to_trust_it(self):
        m = plan()
        table = {a["topic"]: a["status"] for a in m["assurance"]}
        self.assertEqual(table, {
            "capability": "checked", "terrain": "not_checked", "obstacles": "not_checked",
            "battery": "estimated", "regulation": "advisory", "airspace": "not_checked", "weather": "not_checked",
        })
        self.assertIn("flat", fx.assurance(m, "terrain")["summary"])
        self.assertIn("not legal advice", fx.assurance(m, "regulation")["summary"])
        self.assertIn("12 m/s", fx.assurance(m, "weather")["summary"])
        self.assertIn("built-in catalog", fx.assurance(m, "capability")["summary"])

    def test_nothing_is_ever_reported_as_verified(self):
        m = plan(fx.area_plan(takeoff_m=(-220, -170)), terrain=fx.terrain(fx.flat()))
        for a in m["assurance"]:
            self.assertIn(a["status"], ("checked", "estimated", "advisory", "not_checked"))
        text = json.dumps(m["assurance"]).lower() + json.dumps(m["issues"]).lower()
        for word in ("verified", "safe to fly", "cleared", "approved"):
            self.assertNotIn(word, text)
        self.assertIn("not verified flight-safety information", m["notice"])

    def test_custom_profile_is_named_as_the_source(self):
        custom = normalize_drone(fx.drone().to_dict() | {"id": "custom:x"}, source="custom")
        m = plan_mission(fx.area_plan(), custom, fx.camera())
        self.assertIn("custom profile", fx.assurance(m, "capability")["summary"])

    def test_capability_line_reports_an_exceeded_limit(self):
        m = plan(fx.area_plan(speed_ms=18))
        self.assertTrue(fx.assurance(m, "capability")["summary"].startswith("Limit exceeded"))


class TestInputValidation(unittest.TestCase):
    def test_geometry_errors(self):
        bad = [
            ({"type": "area", "geometry": None}, "geometry"),
            ({"type": "area", "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}}, "geometry"),
            ({"type": "area", "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 1]]]}}, "geometry"),
            ({"type": "area", "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [200, 5]]]}}, "geometry"),
            ({"type": "corridor", "geometry": fx.rect(), "corridor_width_m": 50}, "geometry"),
            ({"type": "orbit", "geometry": fx.rect()}, "type"),
            ({"type": "area", "geometry": fx.rect(), "takeoff": [500, 0]}, "takeoff"),
        ]
        for definition, field in bad:
            with self.assertRaises(PlanError) as cm:
                plan(definition)
            self.assertEqual(cm.exception.field, field, definition)

    def test_self_crossing_boundary_is_refused_with_a_reason(self):
        bow = fx.polygon([(0, 0), (300, 300), (300, 0), (0, 300)])
        with self.assertRaises(PlanError) as cm:
            plan(fx.area_plan(bow))
        self.assertIn("crosses itself", str(cm.exception))

    def test_degenerate_and_oversized_areas(self):
        with self.assertRaises(PlanError):
            plan(fx.area_plan(fx.rect(0.5, 0.5)))
        with self.assertRaises(PlanError) as cm:
            plan(fx.area_plan(fx.rect(70000, 500)))
        self.assertIn("km across", str(cm.exception))

    def test_work_is_bounded(self):
        with self.assertRaises(PlanError) as cm:
            plan(fx.area_plan(fx.rect(3000, 3000), altitude_m=10, side_overlap=90))
        self.assertIn("flight lines", str(cm.exception))
        with self.assertRaises(PlanError):
            plan(fx.area_plan(fx.rect(900, 900), altitude_m=30), limits=Limits(max_photos=100))
        with self.assertRaises(PlanError):
            plan(fx.area_plan(altitude_m=5, front_overlap=95, side_overlap=95))

    def test_parameter_errors_name_the_field(self):
        for patch, field in [({"altitude_m": 2}, "altitude_m"), ({"front_overlap": 99}, "front_overlap"),
                             ({"speed_ms": "fast"}, "speed_ms"), ({"altitude_mode": "hover"}, "altitude_mode"),
                             ({"regulation": "moon"}, "regulation"), ({"corridor_min_passes": 1.5}, "corridor_min_passes"),
                             ({"reserve_percent": 90}, "reserve_percent"), ({"direction": "sideways"}, "direction")]:
            with self.assertRaises(PlanError) as cm:
                plan(fx.area_plan(**patch))
            self.assertEqual(cm.exception.field, field, patch)

    def test_defaults_and_unknown_keys(self):
        p = normalize_params({"altitude_m": "", "made_up": 1, "direction": 270, "crosshatch": "1"})
        self.assertEqual(p["altitude_m"], 100.0)
        self.assertEqual(p["direction"], 90.0)
        self.assertIs(p["crosshatch"], True)
        self.assertNotIn("made_up", p)
        self.assertEqual(set(p), {s.name for s in SPECS})

    def test_holes_are_supported(self):
        with_hole = fx.polygon([(-200, -150), (200, -150), (200, 150), (-200, 150)],
                               holes=[[(-50, -50), (50, -50), (50, 50), (-50, 50)]])
        m = plan(fx.area_plan(with_hole))
        self.assertAlmostEqual(m["stats"]["area_m2"], 120000 - 10000, delta=5)
        self.assertGreater(m["stats"]["leg_count"], m["stats"]["line_count"])

    def test_all_cameras_and_drones_plan_without_crashing(self):
        cameras = builtin_cameras()
        for d in builtin_drones().values():
            for cam_id in ([c for c in d.cameras if c != "*"] or [d.default_camera]):
                m = plan_mission(fx.area_plan(altitude_m=90), d, cameras[cam_id])
                self.assertGreater(m["stats"]["photo_count"], 0, f"{d.id}/{cam_id}")
                c = plan_mission(fx.corridor_plan(altitude_m=90), d, cameras[cam_id])
                self.assertGreater(c["stats"]["photo_count"], 0, f"{d.id}/{cam_id}")


if __name__ == "__main__":
    unittest.main()
