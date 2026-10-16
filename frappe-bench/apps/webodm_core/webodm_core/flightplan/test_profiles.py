"""Equipment profiles: the built-in catalog is sound, and custom profiles are validated the same way."""

import unittest

from webodm_core.flightplan import photogrammetry as pg
from webodm_core.flightplan.exporters import EXPORTERS
from webodm_core.flightplan.profiles import (ANY_CAMERA, DEFAULT_MIN_INTERVAL_S, ProfileError, builtin_cameras,
                                             builtin_drones, cameras_for, normalize_camera, normalize_drone)

CAMERA = {
    "id": "custom:cam1", "manufacturer": "Sony", "name": "RX0 II", "sensor_width_mm": 13.2,
    "sensor_height_mm": 8.8, "image_width_px": 4800, "image_height_px": 3200, "focal_length_mm": 7.9,
}
DRONE = {
    "id": "custom:d1", "manufacturer": "Acme", "model": "Hexa", "airframe": "multirotor",
    "max_flight_time_min": 30, "max_speed_ms": 18, "cameras": ["custom:cam1"], "default_camera": "custom:cam1",
    "mission": {"waypoints": True, "formats": [{"id": "qgc-plan", "level": "official", "app": "QGroundControl"}]},
}


class TestCatalog(unittest.TestCase):
    def test_catalog_loads_and_is_non_trivial(self):
        drones, cameras = builtin_drones(), builtin_cameras()
        self.assertGreaterEqual(len(drones), 20)
        self.assertGreaterEqual(len(cameras), 25)
        makers = {d.manufacturer for d in drones.values()}
        self.assertTrue({"DJI", "Autel Robotics", "Parrot", "Wingtra"} <= makers)
        self.assertEqual({d.airframe for d in drones.values()}, {"multirotor", "fixed-wing", "vtol"})

    def test_every_drone_camera_reference_resolves(self):
        cameras = builtin_cameras()
        for d in builtin_drones().values():
            self.assertTrue(d.cameras, d.id)
            for cam in d.cameras:
                self.assertTrue(cam == ANY_CAMERA or cam in cameras, f"{d.id} -> {cam}")
            self.assertIn(d.default_camera, cameras, d.id)
            self.assertTrue(d.accepts_camera(d.default_camera), d.id)
            self.assertEqual(cameras_for(d, cameras)[0].id, d.default_camera)

    def test_every_listed_format_exists_and_matches_its_kind(self):
        for d in builtin_drones().values():
            for f in d.formats:
                self.assertIn(f.id, EXPORTERS, f"{d.id} lists unknown format {f.id}")
                kind = EXPORTERS[f.id].kind
                # A boundary-level entry points at a boundary file, a mission file is never "boundary".
                self.assertEqual(f.level == "boundary", kind == "boundary", f"{d.id}/{f.id}")
                if f.trigger:
                    self.assertIn(f.trigger, EXPORTERS[f.id].triggers, f"{d.id}/{f.id}")

    def test_wpml_aircraft_have_ids_with_a_stated_source(self):
        for d in builtin_drones().values():
            if d.format("dji-wpml"):
                self.assertIsNotNone(d.wpml, d.id)
                self.assertIn(d.wpml["source"], ("dji-docs", "community"))
                self.assertIn("drone_enum", d.wpml)
        docs = {(d.wpml["drone_enum"], d.wpml.get("drone_sub_enum", 0))
                for d in builtin_drones().values() if d.wpml and d.wpml["source"] == "dji-docs"}
        # The values DJI publishes in its WPML reference.
        self.assertTrue({(89, 0), (60, 0), (67, 1), (77, 0), (77, 1), (77, 2), (91, 0)} <= docs)

    def test_capabilities_differ_between_models(self):
        d = builtin_drones()
        self.assertFalse(d["dji-mini-3"].waypoints)
        self.assertEqual(d["dji-mini-3"].formats, ())
        self.assertFalse(d["dji-phantom-4-rtk"].per_waypoint_altitude)
        self.assertEqual([f.id for f in d["dji-phantom-4-rtk"].formats], ["kml-boundary"])
        self.assertEqual(d["dji-air-2s"].format("litchi-csv").level, "official")
        self.assertEqual(d["dji-mini-4-pro"].format("dji-wpml").level, "community")
        self.assertEqual(d["dji-mini-4-pro"].format("dji-wpml").max_waypoints, 200)
        self.assertEqual(d["dji-mavic-3e"].format("dji-wpml").level, "official")
        self.assertFalse(d["ageagle-ebee-x"].can_hover)
        self.assertTrue(d["generic-mavlink-multirotor"].accepts_camera("micasense-rededge-mx"))
        self.assertFalse(d["dji-mavic-3e"].accepts_camera("dji-zenmuse-p1-35mm"))

    def test_specs_are_physically_plausible(self):
        for d in builtin_drones().values():
            self.assertTrue(5 <= d.max_flight_time_min <= 120, d.id)
            self.assertTrue(3 <= d.max_speed_ms <= 40, d.id)
            self.assertLessEqual(d.mission_speed_limit_ms, d.max_speed_ms, d.id)
            if d.cruise_speed_ms:
                self.assertLessEqual(d.cruise_speed_ms, d.mission_speed_limit_ms, d.id)
            if not d.can_hover:
                self.assertTrue(d.min_turn_radius_m, d.id)
        for c in builtin_cameras().values():
            # Square pixels to within 2 %.
            pitch_w = c.sensor_width_mm / c.image_width_px
            pitch_h = c.sensor_height_mm / c.image_height_px
            self.assertAlmostEqual(pitch_w / pitch_h, 1.0, delta=0.02, msg=c.id)
            self.assertTrue(0.5 <= c.megapixels <= 110, c.id)

    def test_ground_resolution_matches_vendor_figures(self):
        c = builtin_cameras()
        # Published: Phantom 4 Pro / RTK H/36.5, Mavic 3E H/36.5, Zenmuse P1 35 mm H/80 (cm/px, H in m).
        self.assertAlmostEqual(pg.gsd_m(c["dji-phantom-4-pro"], 100) * 100, 100 / 36.5, delta=0.02)
        self.assertAlmostEqual(pg.gsd_m(c["dji-mavic-3e-wide"], 100) * 100, 100 / 36.5, delta=0.03)
        self.assertAlmostEqual(pg.gsd_m(c["dji-zenmuse-p1-35mm"], 100) * 100, 100 / 80.0, delta=0.01)
        # MicaSense RedEdge-MX: 8 cm per pixel at 120 m.
        self.assertAlmostEqual(pg.gsd_m(c["micasense-rededge-mx"], 120) * 100, 8.0, delta=0.4)

    def test_unpublished_interval_falls_back_to_a_cautious_default(self):
        cam = builtin_cameras()["dji-m30t-wide"]
        self.assertIsNone(cam.min_interval_s)
        self.assertTrue(cam.interval_assumed)
        self.assertEqual(cam.effective_min_interval_s, DEFAULT_MIN_INTERVAL_S)
        self.assertFalse(builtin_cameras()["dji-mavic-3e-wide"].interval_assumed)

    def test_to_dict_round_trips_through_normalize(self):
        for d in builtin_drones().values():
            again = normalize_drone(d.to_dict(), source="builtin")
            self.assertEqual(again, d)
        for c in builtin_cameras().values():
            self.assertEqual(normalize_camera(c.to_dict(), source="builtin"), c)


class TestCustomProfiles(unittest.TestCase):
    def test_valid_custom_profiles(self):
        cam = normalize_camera(CAMERA)
        self.assertEqual(cam.source, "custom")
        self.assertEqual(cam.label, "Sony RX0 II")
        self.assertEqual(cam.shutter, "electronic")
        drone = normalize_drone(DRONE)
        self.assertEqual(drone.endurance_factor, 0.8)
        self.assertEqual(drone.mission_speed_limit_ms, 18)
        self.assertEqual(drone.format("qgc-plan").app, "QGroundControl")
        self.assertTrue(drone.per_waypoint_altitude)

    def test_camera_field_errors_name_the_field(self):
        cases = [
            ({"sensor_width_mm": 0}, "sensor_width_mm"),
            ({"focal_length_mm": -1}, "focal_length_mm"),
            ({"image_width_px": 4000.5}, "image_width_px"),
            ({"image_height_px": "many"}, "image_height_px"),
            ({"min_interval_s": 0}, "min_interval_s"),
            ({"shutter": "leaf"}, "shutter"),
            ({"name": ""}, "name"),
            ({"id": "Bad Id"}, "id"),
            ({"sensor_width_mm": float("nan")}, "sensor_width_mm"),
            ({"sensor_width_mm": True}, "sensor_width_mm"),
        ]
        for patch, field in cases:
            with self.assertRaises(ProfileError) as cm:
                normalize_camera({**CAMERA, **patch})
            self.assertEqual(cm.exception.field, field, patch)

    def test_drone_field_errors_name_the_field(self):
        cases = [
            ({"max_flight_time_min": 0}, "max_flight_time_min"),
            ({"max_speed_ms": None}, "max_speed_ms"),
            ({"max_mission_speed_ms": 25}, "max_mission_speed_ms"),
            ({"min_speed_ms": 30}, "min_speed_ms"),
            ({"airframe": "blimp"}, "airframe"),
            ({"endurance_factor": 1.5}, "endurance_factor"),
            ({"default_camera": "other"}, "default_camera"),
            ({"rtk": "maybe"}, "rtk"),
            ({"mission": {"formats": [{"id": "qgc-plan", "level": "blessed"}]}}, "formats"),
            ({"mission": {"formats": [{"level": "official"}]}}, "formats"),
            ({"mission": {"formats": [{"id": "litchi-csv", "max_waypoints": 1}]}}, "formats"),
        ]
        for patch, field in cases:
            with self.assertRaises(ProfileError) as cm:
                normalize_drone({**DRONE, **patch})
            self.assertEqual(cm.exception.field, field, patch)

    def test_no_waypoint_flight_drops_mission_formats(self):
        d = normalize_drone({**DRONE, "mission": {"waypoints": False, "formats": [
            {"id": "qgc-plan", "level": "official"}, {"id": "kml-boundary", "level": "boundary"}]}})
        self.assertFalse(d.waypoints)
        self.assertEqual([f.id for f in d.formats], ["kml-boundary"])
        self.assertFalse(d.per_waypoint_altitude)

    def test_wpml_ids_on_a_custom_drone(self):
        d = normalize_drone({**DRONE, "mission": {"wpml": {"drone_enum": "77", "drone_sub_enum": 0}, "formats": []}})
        self.assertEqual(d.wpml, {"drone_enum": 77, "drone_sub_enum": 0, "source": "community"})
        with self.assertRaises(ProfileError):
            normalize_drone({**DRONE, "mission": {"wpml": {"drone_enum": "seventy"}}})

    def test_cameras_given_as_a_comma_separated_string(self):
        d = normalize_drone({**DRONE, "cameras": "custom:cam1, dji-phantom-4-pro", "default_camera": None})
        self.assertEqual(d.cameras, ("custom:cam1", "dji-phantom-4-pro"))


class TestPhotogrammetry(unittest.TestCase):
    def setUp(self):
        self.cam = builtin_cameras()["dji-phantom-4-pro"]   # 13.2 x 8.8 mm, 5472 x 3648, 8.8 mm

    def test_footprint_and_spacing(self):
        fp = pg.footprint(self.cam, 100.0)
        self.assertAlmostEqual(fp.across_m, 150.0)       # 13.2 / 8.8 * 100
        self.assertAlmostEqual(fp.along_m, 100.0)        # 8.8 / 8.8 * 100
        self.assertAlmostEqual(pg.line_spacing(fp, 70), 45.0)
        self.assertAlmostEqual(pg.photo_spacing(fp, 80), 20.0)
        self.assertAlmostEqual(fp.gsd_cm, 2.741, places=3)

    def test_portrait_swaps_the_footprint(self):
        fp = pg.footprint(self.cam, 100.0, "portrait")
        self.assertAlmostEqual(fp.across_m, 100.0)
        self.assertAlmostEqual(fp.along_m, 150.0)

    def test_height_for_gsd_inverts_gsd(self):
        for h in (30.0, 100.0, 350.0):
            self.assertAlmostEqual(pg.height_for_gsd(self.cam, pg.gsd_m(self.cam, h)), h)

    def test_overlap_shrinks_as_the_ground_rises(self):
        fp = pg.footprint(self.cam, 100.0)
        spacing = pg.line_spacing(fp, 70)
        self.assertAlmostEqual(pg.overlap_at(spacing, fp.across_m, 100.0, 100.0), 70.0)
        self.assertAlmostEqual(pg.overlap_at(spacing, fp.across_m, 100.0, 50.0), 40.0)
        self.assertAlmostEqual(pg.overlap_at(spacing, fp.across_m, 100.0, 200.0), 85.0)
        self.assertLess(pg.overlap_at(spacing, fp.across_m, 100.0, 25.0), 0)   # a gap
        self.assertEqual(pg.overlap_at(spacing, fp.across_m, 100.0, 0.0), -100.0)

    def test_motion_blur(self):
        gsd = pg.gsd_m(self.cam, 100.0)
        self.assertAlmostEqual(pg.motion_blur_px(10.0, 1 / 1000, gsd), 0.01 / gsd)
        self.assertEqual(pg.motion_blur_px(10.0, 1 / 1000, 0.0), 0.0)


if __name__ == "__main__":
    unittest.main()
