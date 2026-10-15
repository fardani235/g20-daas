"""Flight planning on the platform: plans in projects, the organization's own equipment, export, isolation.

The planner's arithmetic is covered by ``webodm_core/flightplan/test_*.py``
(no Frappe). These tests pin what only exists once it is wired into the
platform:

* plans are saved in a project, org-scoped, and their mission is always
  computed on the server, never taken from the request;
* custom drone and camera profiles go through the same validation as the
  built-in catalog and are visible only to their organization;
* elevation data comes from the configured service or a survey of the same
  project, is cached, and its absence is reported, not hidden;
* an export is refused for an aircraft that cannot use the format, for a
  plan with blocking findings, and without acknowledgement of warnings.

No test here touches the network: the global elevation provider and the
geocoder are replaced in ``setUp``.
"""

from __future__ import annotations

import io
import json
import os
import zipfile
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from webodm_core.api import flightplan as api
from webodm_core.flightplan import _fixtures as fx
from webodm_core.flightplan import elevation as elevation_mod
from webodm_core.testing import make_task

PLAN = "WebODM Flight Plan"
DRONE = "WebODM Drone Profile"
CAMERA = "WebODM Camera Profile"


def _user(email):
    if not frappe.db.exists("User", email):
        frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0],
                        "send_welcome_email": 0}).insert(ignore_permissions=True)
    u = frappe.get_doc("User", email)
    u.roles = []
    u.append("roles", {"role": "WebODM User"})
    u.save(ignore_permissions=True)
    return email


def _org(name):
    existing = frappe.db.get_value("WebODM Organization", {"organization_name": name}, "name")
    if existing:
        return existing
    return frappe.get_doc({"doctype": "WebODM Organization", "organization_name": name}
                          ).insert(ignore_permissions=True).name


def _join(user, org, role="Owner"):
    if not frappe.db.exists("WebODM Org Membership", {"user": user, "organization": org}):
        frappe.get_doc({"doctype": "WebODM Org Membership", "user": user, "organization": org,
                        "role": role}).insert(ignore_permissions=True)


def _as(user):
    frappe.set_user(user)
    frappe.local.webodm_org_cache = {}


def _project(title):
    existing = frappe.db.exists("WebODM Project", title)
    return existing or frappe.get_doc({"doctype": "WebODM Project", "title": title}).insert().name


def definition(**overrides):
    d = fx.area_plan(takeoff_m=(0, -170))
    d.update({"drone": "dji-mavic-3e", "camera": "dji-mavic-3e-wide", "elevation_source": "auto"})
    params = overrides.pop("params", None)
    d.update(overrides)
    if params:
        d["params"] = {**d["params"], **params}
    return d


CAMERA_PROFILE = {
    "name": "RX0 II", "manufacturer": "Sony", "sensor_width_mm": 13.2, "sensor_height_mm": 8.8,
    "image_width_px": 4800, "image_height_px": 3200, "focal_length_mm": 7.9, "min_interval_s": 1.0,
    "shutter": "electronic",
}


def drone_profile(**overrides):
    p = {
        "model": "Survey Hexa", "manufacturer": "Acme", "airframe": "multirotor", "max_flight_time_min": 32,
        "max_speed_ms": 16, "cruise_speed_ms": 6, "max_ascent_ms": 4, "max_descent_ms": 3,
        "cameras": ["dji-phantom-4-pro"], "ecosystem": "mavlink",
    }
    p.update(overrides)
    return p


class _Base(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = _user("fp_owner@example.com")
        cls.member = _user("fp_member@example.com")
        cls.outsider = _user("fp_outsider@example.com")
        cls.org = _org("Flight Plan Org")
        cls.other_org = _org("Flight Plan Other Org")
        _join(cls.owner, cls.org)
        _join(cls.member, cls.org, role="Member")
        _join(cls.outsider, cls.other_org)
        _as(cls.owner)
        cls.project = _project("Flight Plan Project")
        cls.project_b = _project("Flight Plan Project B")
        _as(cls.outsider)
        cls.other_project = _project("Flight Plan Other Project")
        _as("Administrator")

    def setUp(self):
        _as(self.owner)
        frappe.cache().delete_keys("flightplan:")
        api._ZONE_CACHE.clear()
        # Never the network: no global elevation service unless a test installs one.
        self._provider = patch.object(api, "_global_provider", return_value=None)
        self._provider.start()
        patch("frappe.log_error").start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        _as("Administrator")
        for org in (self.org, self.other_org):
            for doctype in (PLAN, DRONE, CAMERA):
                for name in frappe.get_all(doctype, filters={"organization": org}, pluck="name"):
                    frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
            for t in frappe.get_all("WebODM Task", filters={"organization": org}, pluck="name"):
                frappe.delete_doc("WebODM Task", t, force=True, ignore_permissions=True)
            for ds in frappe.get_all("WebODM Dataset", filters={"organization": org}, pluck="name"):
                frappe.delete_doc("WebODM Dataset", ds, force=True, ignore_permissions=True)
        # A mission export commits its audit note, so the clean-up has to be
        # committed too or the class-level rollback would bring the rows back.
        frappe.db.commit()

    def use_provider(self, surface=None, fail=None):
        """Install a fake global elevation service; returns the list of batches it was asked for."""
        calls = []
        frame = fx.frame()

        def sample(points):
            calls.append(len(points))
            if fail:
                raise elevation_mod.ElevationError(fail)
            return [(surface or fx.flat(700))(*frame.forward(lng, lat)) for lat, lng in points]

        provider = elevation_mod.CallableProvider(func=sample, provider_id="fake-dem", provider_label="Fake DEM",
                                                  kind="global-dem", resolution_m=30.0, vertical_accuracy_m=5.0)
        self._provider.stop()
        self._provider = patch.object(api, "_global_provider", return_value=provider)
        self._provider.start()
        return calls

    def save(self, title="North field", project=None, description=None, **overrides):
        return api.save_plan(project=project or self.project, title=title, description=description,
                             definition=definition(**overrides))


class TestCatalog(_Base):
    def test_catalog_shape(self):
        c = api.catalog()
        ids = {d["id"] for d in c["drones"]}
        self.assertIn("dji-mavic-3e", ids)
        self.assertGreaterEqual(len(c["drones"]), 20)
        m3e = next(d for d in c["drones"] if d["id"] == "dji-mavic-3e")
        self.assertEqual((m3e["source"], m3e["editable"]), ("builtin", False))
        self.assertEqual(m3e["camera_choices"], ["dji-mavic-3e-wide"])
        self.assertEqual(m3e["mission"]["formats"][0]["id"], "dji-wpml")
        generic = next(d for d in c["drones"] if d["id"] == "generic-mavlink-multirotor")
        self.assertEqual(len(generic["camera_choices"]), len(c["cameras"]))      # carries any camera
        self.assertEqual(generic["camera_choices"][0], "sony-a6000-20mm")        # its default first
        self.assertEqual([f["id"] for f in c["formats"]],
                         ["dji-wpml", "qgc-plan", "mavlink-wpl", "litchi-csv", "kml-boundary", "kml", "geojson"])
        self.assertEqual({e["id"] for e in c["ecosystems"]},
                         {"dji-enterprise", "dji-fly", "mavlink", "litchi", "vendor", "manual"})
        names = {p["name"] for p in c["params"]}
        self.assertTrue({"altitude_m", "speed_ms", "front_overlap", "side_overlap", "direction", "altitude_mode",
                         "gsd_cm", "regulation"} <= names)
        self.assertEqual({r["id"] for r in c["rule_sets"]}, {"generic-120m", "easa-open", "faa-part-107", "custom"})
        self.assertFalse(c["elevation"]["enabled"])
        self.assertFalse(c["airspace"]["configured"])
        self.assertTrue(c["can_edit_profiles"])
        self.assertIn("not verified flight-safety information", c["notice"])

    def test_catalog_reports_the_configured_elevation_service(self):
        self.use_provider()
        e = api.catalog()["elevation"]
        self.assertEqual((e["enabled"], e["resolution_m"], e["vertical_accuracy_m"]), (True, 30.0, 5.0))

    def test_user_without_an_organization_is_refused(self):
        loner = _user("fp_loner@example.com")
        _as(loner)
        self.assertFalse(api.catalog()["can_edit_profiles"])
        with self.assertRaises(frappe.PermissionError):
            api.preview(definition=definition())
        with self.assertRaises(frappe.PermissionError):
            api.list_plans()
        with self.assertRaises(frappe.PermissionError):
            api.save_camera(profile=CAMERA_PROFILE)


class TestCustomEquipment(_Base):
    def test_custom_camera_round_trip(self):
        cam = api.save_camera(profile=CAMERA_PROFILE)
        self.assertTrue(cam["id"].startswith("custom:"))
        self.assertEqual((cam["label"], cam["source"], cam["editable"]), ("Sony RX0 II", "custom", True))
        self.assertEqual(frappe.db.get_value(CAMERA, cam["docname"], "organization"), self.org)
        self.assertIn(cam["id"], {c["id"] for c in api.catalog()["cameras"]})
        # A colleague in the organization sees and can change it.
        _as(self.member)
        updated = api.save_camera(profile={**CAMERA_PROFILE, "docname": cam["docname"], "focal_length_mm": 8.5})
        self.assertEqual(updated["focal_length_mm"], 8.5)
        self.assertEqual(updated["id"], cam["id"])

    def test_invalid_camera_is_refused_by_every_writer(self):
        for patch_, text in [({"sensor_width_mm": 0}, "sensor_width_mm"), ({"focal_length_mm": -3}, "focal_length_mm"),
                             ({"image_width_px": 0}, "image_width_px"), ({"shutter": "leaf"}, "shutter")]:
            with self.assertRaises(frappe.ValidationError) as cm:
                api.save_camera(profile={**CAMERA_PROFILE, **patch_})
            self.assertIn(text, str(cm.exception))
        # The same rule through a plain document insert (Desk, REST resource).
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc({"doctype": CAMERA, "profile_name": "Bad", "sensor_width_mm": 13.2, "sensor_height_mm": 8.8,
                            "image_width_px": 4800, "image_height_px": 3200, "focal_length_mm": 0}).insert()
        self.assertEqual(frappe.db.count(CAMERA, {"organization": self.org}), 0)

    def test_custom_drone_with_custom_camera_plans_and_exports(self):
        cam = api.save_camera(profile=CAMERA_PROFILE)
        drone = api.save_drone(profile=drone_profile(cameras=[cam["id"], "dji-phantom-4-pro"]))
        self.assertEqual((drone["label"], drone["source"], drone["ecosystem"]), ("Acme Survey Hexa", "custom", "mavlink"))
        self.assertEqual(drone["default_camera"], cam["id"])
        self.assertEqual([f["id"] for f in drone["mission"]["formats"]], ["qgc-plan", "mavlink-wpl"])
        self.assertEqual(drone["camera_choices"][0], cam["id"])

        out = api.preview(definition=definition(drone=drone["id"], camera=cam["id"], params={"altitude_m": 80}),
                          project=self.project)
        self.assertTrue(out["ok"])
        m = out["mission"]
        # GSD from the custom camera: 13.2 mm x 80 m / (7.9 mm x 4800 px).
        self.assertAlmostEqual(m["camera"]["gsd_cm"], 13.2 * 80 / (7.9 * 4800) * 100, places=2)
        self.assertEqual(m["stats"]["speed_ms"], 6)                    # the profile's suggested speed
        self.assertAlmostEqual(m["stats"]["usable_flight_time_s"], 32 * 60 * 0.8 * 0.75, delta=0.5)
        self.assertIn("custom profile", next(a for a in m["assurance"] if a["topic"] == "capability")["summary"])

        plan = api.save_plan(project=self.project, title="Hexa survey",
                             definition=definition(drone=drone["id"], camera=cam["id"], params={"altitude_m": 80}))
        formats = {f["id"]: f for f in api.export_options(name=plan["name"])["formats"]}
        self.assertTrue(formats["qgc-plan"]["available"])
        self.assertFalse(formats["dji-wpml"]["available"])
        self.assertFalse(formats["litchi-csv"]["available"])

    def test_capability_limits_of_a_custom_drone_are_enforced(self):
        drone = api.save_drone(profile=drone_profile(max_mission_speed_ms=8))
        out = api.preview(definition=definition(drone=drone["id"], camera=None, params={"speed_ms": 12}))
        codes = [i["code"] for i in out["mission"]["issues"] if i["severity"] == "error"]
        self.assertIn("SPEED_ABOVE_MAX", codes)
        self.assertEqual(out["mission"]["equipment"]["camera"]["id"], "dji-phantom-4-pro")    # the default camera

    def test_drone_profile_validation(self):
        cases = [
            (drone_profile(cameras=[]), "at least one camera"),
            (drone_profile(cameras=["no-such-camera"]), "unknown camera"),
            (drone_profile(max_flight_time_min=0), "max_flight_time_min"),
            (drone_profile(max_mission_speed_ms=40), "max_mission_speed_ms"),
            (drone_profile(airframe="fixed-wing"), "turn radius"),
            (drone_profile(ecosystem="dji-enterprise"), "WPML aircraft id"),
            (drone_profile(ecosystem="warp-drive"), "Unknown flight app family"),
            (drone_profile(ecosystem=None, mission={"formats": [{"id": "gpx", "level": "official"}]}), "unknown mission format"),
            (drone_profile(ecosystem=None, mission={"formats": [{"id": "qgc-plan", "level": "boundary"}]}), "support level"),
            (drone_profile(ecosystem=None, mission={"formats": [{"id": "qgc-plan", "level": "official",
                                                                "trigger": "waypoint"}]}), "cannot trigger"),
        ]
        for profile, text in cases:
            with self.assertRaises(frappe.ValidationError) as cm:
                api.save_drone(profile=profile)
            self.assertIn(text, str(cm.exception), profile)
        self.assertEqual(frappe.db.count(DRONE, {"organization": self.org}), 0)

    def test_ecosystems_map_to_capabilities(self):
        wpml = api.save_drone(profile=drone_profile(model="Custom M3", ecosystem="dji-enterprise",
                                                    mission={"wpml": {"drone_enum": 77, "drone_sub_enum": 0}}))
        self.assertEqual(wpml["mission"]["wpml"], {"drone_enum": 77, "drone_sub_enum": 0, "source": "community"})
        self.assertEqual([f["id"] for f in wpml["mission"]["formats"]], ["dji-wpml", "kml-boundary"])
        vendor = api.save_drone(profile=drone_profile(model="Vendor wing", ecosystem="vendor"))
        self.assertFalse(vendor["mission"]["per_waypoint_altitude"])
        self.assertEqual([f["level"] for f in vendor["mission"]["formats"]], ["boundary"])
        manual = api.save_drone(profile=drone_profile(model="Hand flown", ecosystem="manual"))
        self.assertFalse(manual["mission"]["waypoints"])
        self.assertEqual(manual["mission"]["formats"], [])
        out = api.preview(definition=definition(drone=manual["id"], camera=None))
        self.assertIn("NO_WAYPOINT_SUPPORT", [i["code"] for i in out["mission"]["issues"]])

    def test_camera_in_use_cannot_be_deleted(self):
        cam = api.save_camera(profile=CAMERA_PROFILE)
        drone = api.save_drone(profile=drone_profile(cameras=[cam["id"]]))
        with self.assertRaises(frappe.ValidationError) as cm:
            api.delete_camera(docname=cam["docname"])
        self.assertIn("Survey Hexa", str(cm.exception))
        api.delete_drone(docname=drone["docname"])
        api.delete_camera(docname=cam["docname"])
        self.assertFalse(frappe.db.exists(CAMERA, cam["docname"]))

    def test_profiles_are_invisible_and_untouchable_across_organizations(self):
        cam = api.save_camera(profile=CAMERA_PROFILE)
        drone = api.save_drone(profile=drone_profile(cameras=[cam["id"]]))
        _as(self.outsider)
        c = api.catalog()
        self.assertNotIn(cam["id"], {x["id"] for x in c["cameras"]})
        self.assertNotIn(drone["id"], {x["id"] for x in c["drones"]})
        self.assertEqual(frappe.get_list(DRONE, pluck="name"), [])
        self.assertEqual(frappe.get_list(CAMERA, pluck="name"), [])
        for call in (lambda: api.save_camera(profile={**CAMERA_PROFILE, "docname": cam["docname"]}),
                     lambda: api.delete_camera(docname=cam["docname"]),
                     lambda: api.save_drone(profile=drone_profile(docname=drone["docname"])),
                     lambda: api.delete_drone(docname=drone["docname"])):
            with self.assertRaises(frappe.PermissionError):
                call()
        # Another organization's profile id reads exactly like one that does not exist.
        out = api.preview(definition=definition(drone=drone["id"], camera=cam["id"]))
        self.assertEqual(out, {"ok": False, "error": {"message": "Choose the aircraft for this plan", "field": "drone"}})
        # ...and cannot be listed as a camera of the outsider's own drone.
        with self.assertRaises(frappe.ValidationError):
            api.save_drone(profile=drone_profile(cameras=[cam["id"]]))


class TestPreview(_Base):
    def test_preview_returns_a_mission(self):
        out = api.preview(definition=definition(), project=self.project)
        self.assertTrue(out["ok"])
        m = out["mission"]
        self.assertEqual(m["equipment"]["drone"]["id"], "dji-mavic-3e")
        self.assertEqual(m["stats"]["line_count"], 7)
        self.assertFalse(m["terrain_pending"])
        self.assertEqual(m["elevation_source"], "auto")
        json.dumps(out)                                                # what the client receives

    def test_unusable_definitions_are_answers_not_failures(self):
        for patch_, field in [({"geometry": None}, "geometry"), ({"drone": "no-such-drone"}, "drone"),
                              ({"camera": "dji-zenmuse-p1-35mm"}, "camera"),
                              ({"params": {"altitude_m": 1}}, "altitude_m"),
                              ({"type": "corridor"}, "geometry")]:
            out = api.preview(definition=definition(**patch_))
            self.assertFalse(out["ok"], patch_)
            self.assertEqual(out["error"]["field"], field, patch_)
            self.assertTrue(out["error"]["message"])

    def test_malformed_requests_are_rejected(self):
        for bad in ({"type": "orbit"}, {"elevation_source": "moon"}, "[1, 2]", "{not json"):
            with self.assertRaises(frappe.ValidationError):
                api.preview(definition=bad if isinstance(bad, str) else definition(**bad))

    def test_definition_may_arrive_as_json_text(self):
        out = api.preview(definition=json.dumps(definition()))
        self.assertTrue(out["ok"])

    def test_project_of_another_organization_is_refused(self):
        with self.assertRaises(frappe.PermissionError):
            api.preview(definition=definition(), project=self.other_project)


class TestTerrain(_Base):
    def test_cached_only_answers_at_once_then_fetch_fills_and_caches(self):
        calls = self.use_provider(fx.slope(700, 0.05))
        fast = api.preview(definition=definition(), terrain="cached")["mission"]
        self.assertTrue(fast["terrain_pending"])
        self.assertIsNone(fast["terrain"])
        self.assertEqual(calls, [])

        full = api.preview(definition=definition(), terrain="fetch")["mission"]
        self.assertFalse(full["terrain_pending"])
        self.assertEqual(full["terrain"]["source"]["id"], "fake-dem")
        self.assertAlmostEqual(full["terrain"]["relief_m"], 20.0, delta=2.0)     # 400 m x 5 %
        self.assertEqual(len(calls), 1)
        self.assertLessEqual(calls[0], 400)

        again = api.preview(definition=definition(), terrain="cached")["mission"]
        self.assertFalse(again["terrain_pending"])
        self.assertIsNotNone(again["terrain"])
        self.assertEqual(len(calls), 1)                                # served from the cache

    def test_small_edits_and_parameter_changes_reuse_the_cached_grid(self):
        calls = self.use_provider()
        api.preview(definition=definition())
        nudged = definition()
        nudged["geometry"]["coordinates"][0][1][0] += 0.00002          # a corner moved ~2 m
        api.preview(definition=nudged)
        api.preview(definition=definition(params={"altitude_m": 60, "side_overlap": 80, "direction": 30}))
        self.assertEqual(len(calls), 1)

    def test_point_budget_is_configurable(self):
        calls = self.use_provider()
        with patch.dict(frappe.conf, {"flightplan_elevation_max_points": 36}):
            api.preview(definition=definition())
        self.assertLessEqual(calls[0], 36)

    def test_service_failure_is_reported_and_the_plan_still_computes(self):
        self.use_provider(fail="elevation service unreachable: ConnectTimeout")
        m = api.preview(definition=definition())["mission"]
        issue = next(i for i in m["issues"] if i["code"] == "TERRAIN_FETCH_FAILED")
        self.assertEqual((issue["severity"], issue["basis"]), ("warning", "not_checked"))
        self.assertIn("ConnectTimeout", issue["message"])
        self.assertIsNone(m["terrain"])
        self.assertEqual(m["summary"]["warning"], 1)
        self.assertEqual(next(a for a in m["assurance"] if a["topic"] == "terrain")["status"], "not_checked")
        # A failure is not cached: the next attempt asks again.
        calls = self.use_provider()
        api.preview(definition=definition())
        self.assertEqual(len(calls), 1)

    def test_source_none_never_asks_the_service(self):
        calls = self.use_provider()
        m = api.preview(definition=definition(elevation_source="none"))["mission"]
        self.assertEqual(calls, [])
        self.assertIsNone(m["terrain"])
        self.assertFalse(m["terrain_pending"])

    def test_terrain_following_from_the_service(self):
        self.use_provider(fx.ridge(700, 60, 120))
        d = definition(geometry=fx.rect(600, 300), takeoff=fx.lnglat([(-300, 0)])[0],
                       params={"altitude_mode": "terrain", "altitude_m": 80, "vlos_m": 1000})
        m = api.preview(definition=d)["mission"]
        self.assertTrue(m["terrain"]["following"])
        alts = [w["alt"] for w in m["waypoints"]]
        self.assertGreater(max(alts) - min(alts), 40)
        self.assertEqual([i["code"] for i in m["issues"] if i["severity"] == "error"], [])


class TestProjectSurveyAsElevationSource(_Base):
    def _survey(self, project=None, **fields):
        task = make_task(project or self.project, title="Survey 1", status="Completed",
                         dsm="/private/files/survey1_dsm.tif", **fields)
        return task

    def test_sources_list_the_projects_completed_surveys(self):
        task = self._survey(dtm="/private/files/survey1_dtm.tif")
        make_task(self.project, title="Still running", status="Running", dsm="/private/files/x.tif")
        make_task(self.project_b, title="Other project", status="Completed", dsm="/private/files/y.tif")
        ids = [s["id"] for s in api.elevation_sources(project=self.project)]
        self.assertEqual(ids, [f"task:{task.name}:dtm", f"task:{task.name}:dsm", "none"])
        self.use_provider()
        ids = [s["id"] for s in api.elevation_sources(project=self.project)]
        self.assertEqual(ids[0], "auto")
        self.assertEqual(ids[-1], "none")
        surface = next(s for s in api.elevation_sources(project=self.project) if s["id"].endswith(":dsm"))
        self.assertEqual((surface["kind"], surface["surface"]), ("project-raster", "surface"))
        self.assertIn("Survey 1", surface["label"])
        with self.assertRaises(frappe.PermissionError):
            api.elevation_sources(project=self.other_project)

    def test_plan_uses_the_surveys_surface_model(self):
        task = self._survey()
        seen = {}
        frame = fx.frame()

        def raster_sample(path, points, *, spacing_m, statistic, timeout=120):
            seen.update(path=path, n=len(points), spacing_m=spacing_m, statistic=statistic)
            return {"values": [700 + 0.05 * frame.forward(lng, lat)[0] for lng, lat in points],
                    "resolution_m": 0.05, "cell_m": spacing_m * 1.5, "statistic": statistic, "covered": len(points)}

        with patch("webodm_core.storage.assets.raster_source", return_value=("local", "/sites/x/survey1_dsm.tif")), \
                patch("webodm_core.plugins.geospatial.raster_sample", side_effect=raster_sample):
            m = api.preview(definition=definition(elevation_source=f"task:{task.name}:dsm"),
                            project=self.project)["mission"]
        self.assertEqual(seen["path"], "/sites/x/survey1_dsm.tif")
        self.assertEqual(seen["statistic"], "max")                     # a surface model is cleared against its tops
        self.assertGreaterEqual(seen["spacing_m"], 5.0)
        self.assertLessEqual(seen["n"], 10000)
        src = m["terrain"]["source"]
        self.assertEqual((src["id"], src["kind"], src["surface"], src["resolution_m"]),
                         (f"task:{task.name}:dsm", "project-raster", "surface", 0.05))
        self.assertIsNone(src["vertical_accuracy_m"])
        self.assertAlmostEqual(m["terrain"]["relief_m"], 20.0, delta=1.5)
        summary = next(a for a in m["assurance"] if a["topic"] == "terrain")["summary"]
        self.assertIn("surface model", summary)
        self.assertIn("accuracy not stated", summary)

    def test_survey_of_another_project_or_organization_reads_as_missing(self):
        elsewhere = make_task(self.project_b, title="Elsewhere", status="Completed", dsm="/private/files/y.tif")
        _as(self.outsider)
        foreign = make_task(self.other_project, title="Foreign", status="Completed", dsm="/private/files/z.tif")
        _as(self.owner)
        for source in (f"task:{elsewhere.name}:dsm", f"task:{foreign.name}:dsm", "task:nope:dsm", "task:nope:xyz"):
            with patch("webodm_core.plugins.geospatial.raster_sample") as sample:
                m = api.preview(definition=definition(elevation_source=source), project=self.project)["mission"]
            sample.assert_not_called()
            issue = next(i for i in m["issues"] if i["code"] == "TERRAIN_FETCH_FAILED")
            self.assertIn("no longer exists" if "xyz" not in source else "unknown elevation source", issue["message"])

    def test_geospatial_failure_is_a_finding(self):
        from webodm_core.plugins import geospatial

        task = self._survey()
        with patch("webodm_core.storage.assets.raster_source", return_value=("local", "/x.tif")), \
                patch("webodm_core.plugins.geospatial.raster_sample",
                      side_effect=geospatial.GeospatialUnavailable("geospatial service unreachable")):
            m = api.preview(definition=definition(elevation_source=f"task:{task.name}:dsm"),
                            project=self.project)["mission"]
        self.assertIn("TERRAIN_FETCH_FAILED", [i["code"] for i in m["issues"]])


class TestPlans(_Base):
    def test_save_and_get(self):
        saved = self.save(description="First block")
        doc = frappe.get_doc(PLAN, saved["name"])
        self.assertEqual((doc.organization, doc.project, doc.created_by), (self.org, self.project, self.owner))
        self.assertEqual((doc.plan_type, doc.drone, doc.camera), ("area", "dji-mavic-3e", "dji-mavic-3e-wide"))
        self.assertEqual((doc.drone_label, doc.photo_count, doc.batteries), ("DJI Mavic 3 Enterprise (M3E)", 133, 1))
        self.assertAlmostEqual(doc.gsd_cm, 2.73, places=2)
        self.assertAlmostEqual(doc.area_m2, 120000, delta=5)
        self.assertEqual((doc.issue_errors, doc.issue_warnings, doc.planner_version), (0, 0, 1))
        self.assertTrue(doc.computed_at)

        got = api.get_plan(name=saved["name"])
        self.assertEqual((got["title"], got["description"], got["project_title"]),
                         ("North field", "First block", "Flight Plan Project"))
        self.assertEqual(got["definition"]["geometry"], definition()["geometry"])
        self.assertEqual(got["definition"]["takeoff"], definition()["takeoff"])
        self.assertEqual(got["definition"]["params"], {})              # only what the user set is stored
        self.assertEqual(got["mission"]["stats"]["photo_count"], 133)
        self.assertEqual(got["mission"]["equipment"]["drone"]["id"], "dji-mavic-3e")

    def test_update_recomputes(self):
        saved = self.save()
        updated = api.save_plan(name=saved["name"], title="North field v2",
                                definition=definition(params={"altitude_m": 60}))
        self.assertEqual(updated["name"], saved["name"])
        self.assertEqual(updated["title"], "North field v2")
        self.assertEqual(updated["definition"]["params"], {"altitude_m": 60})
        self.assertGreater(updated["photo_count"], saved["photo_count"])
        self.assertEqual(frappe.db.count(PLAN, {"project": self.project}), 1)

    def test_corridor_plan(self):
        d = fx.corridor_plan(takeoff_m=(-600, -30), altitude_m=80)
        d.update({"drone": "dji-mavic-3e", "camera": "dji-mavic-3e-wide"})
        saved = api.save_plan(project=self.project, title="Access road", definition=d)
        self.assertEqual(saved["plan_type"], "corridor")
        self.assertEqual(saved["definition"]["corridor_width_m"], 120.0)
        self.assertEqual(saved["mission"]["layout"]["passes"], 2)

    def test_a_plan_with_findings_can_be_saved_as_a_draft(self):
        saved = self.save(params={"speed_ms": 18})
        self.assertEqual(saved["issue_errors"], 1)
        self.assertFalse(saved["mission"]["summary"]["exportable"])

    def test_a_definition_that_cannot_be_laid_out_is_refused(self):
        for bad in ({"geometry": None}, {"drone": "nope"}, {"params": {"front_overlap": 99}}):
            with self.assertRaises(frappe.ValidationError):
                self.save(**bad)
        with self.assertRaises(frappe.ValidationError):
            api.save_plan(project=self.project, title="  ", definition=definition())
        self.assertEqual(frappe.db.count(PLAN, {"organization": self.org}), 0)

    def test_the_mission_is_computed_on_the_server_whoever_saves(self):
        saved = self.save()
        doc = frappe.get_doc(PLAN, saved["name"])
        # A client writing through the REST resource cannot plant a mission
        # (here: one with its findings removed and a made-up photo count).
        forged = json.loads(doc.mission)
        forged["issues"] = []
        forged["stats"]["photo_count"] = 1
        doc.mission = json.dumps(forged)
        doc.photo_count = 1
        doc.parameters = json.dumps({"speed_ms": 18})                  # above the aircraft's limit
        doc.save()
        doc.reload()
        mission = json.loads(doc.mission)
        self.assertIn("SPEED_ABOVE_MAX", [i["code"] for i in mission["issues"]])
        self.assertEqual(doc.photo_count, 133)
        self.assertEqual(doc.issue_errors, 1)
        with self.assertRaises(frappe.ValidationError):
            api.export_plan(name=doc.name, format="dji-wpml", acknowledged=1)

    def test_plain_document_insert_is_validated_and_computed(self):
        d = definition()
        doc = frappe.get_doc({"doctype": PLAN, "title": "Via REST", "project": self.project, "plan_type": "area",
                              "drone": "dji-air-2s", "camera": "dji-air-2s", "geometry": json.dumps(d["geometry"])}
                             ).insert()
        self.assertEqual((doc.organization, doc.drone_label), (self.org, "DJI Air 2S"))
        self.assertGreater(doc.photo_count, 0)
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc({"doctype": PLAN, "title": "Broken", "project": self.project, "plan_type": "area",
                            "drone": "dji-air-2s", "camera": "dji-air-2s",
                            "geometry": json.dumps({"type": "Polygon", "coordinates": [[[0, 0], [1, 1]]]})}).insert()

    def test_project_must_be_in_the_same_organization(self):
        with self.assertRaises(frappe.PermissionError):
            self.save(project=self.other_project)
        d = definition()
        with self.assertRaises(frappe.PermissionError):
            frappe.get_doc({"doctype": PLAN, "title": "Smuggled", "project": self.other_project, "plan_type": "area",
                            "drone": "dji-air-2s", "camera": "dji-air-2s", "geometry": json.dumps(d["geometry"])}
                           ).insert()

    def test_list_plans(self):
        a = self.save("Block A")
        b = self.save("Block B", project=self.project_b)
        rows = api.list_plans()
        self.assertEqual({r["name"] for r in rows}, {a["name"], b["name"]})
        self.assertEqual({r["project_title"] for r in rows}, {"Flight Plan Project", "Flight Plan Project B"})
        self.assertTrue(all("mission" not in r for r in rows))
        only_a = api.list_plans(project=self.project)
        self.assertEqual([r["name"] for r in only_a], [a["name"]])
        self.assertEqual(only_a[0]["drone_label"], "DJI Mavic 3 Enterprise (M3E)")

    def test_colleagues_share_plans(self):
        saved = self.save()
        _as(self.member)
        self.assertEqual([r["name"] for r in api.list_plans()], [saved["name"]])
        api.save_plan(name=saved["name"], title="Renamed by a colleague", definition=definition())
        api.delete_plan(name=saved["name"])
        self.assertFalse(frappe.db.exists(PLAN, saved["name"]))

    def test_other_organizations_cannot_see_or_touch_a_plan(self):
        saved = self.save()
        _as(self.outsider)
        self.assertEqual(api.list_plans(), [])
        self.assertEqual(frappe.get_list(PLAN, pluck="name"), [])
        for call in (lambda: api.get_plan(name=saved["name"]),
                     lambda: api.save_plan(name=saved["name"], title="Hijacked", definition=definition()),
                     lambda: api.delete_plan(name=saved["name"]),
                     lambda: api.duplicate_plan(name=saved["name"], project=self.other_project),
                     lambda: api.export_options(name=saved["name"]),
                     lambda: api.export_plan(name=saved["name"], format="kml"),
                     lambda: api.preview(definition=definition(), plan=saved["name"]),
                     lambda: api.list_plans(project=self.project)):
            with self.assertRaises(frappe.PermissionError):
                call()
        self.assertEqual(frappe.db.get_value(PLAN, saved["name"], "title"), "North field")

    def test_duplicate_within_and_across_projects(self):
        saved = self.save(params={"altitude_m": 80})
        copy = api.duplicate_plan(name=saved["name"])
        self.assertNotEqual(copy["name"], saved["name"])
        self.assertEqual((copy["title"], copy["project"]), ("North field (copy)", self.project))
        self.assertEqual(copy["definition"], saved["definition"])
        self.assertEqual(copy["photo_count"], saved["photo_count"])
        self.assertEqual(api.duplicate_plan(name=saved["name"])["title"], "North field (copy 2)")
        moved = api.duplicate_plan(name=saved["name"], project=self.project_b, title="Reused at site B")
        self.assertEqual((moved["title"], moved["project"]), ("Reused at site B", self.project_b))

    def test_duplicate_into_another_project_drops_a_survey_elevation_source(self):
        task = make_task(self.project, title="Survey", status="Completed", dsm="/private/files/s.tif")
        with patch("webodm_core.storage.assets.raster_source", return_value=("local", "/s.tif")), \
                patch("webodm_core.plugins.geospatial.raster_sample",
                      side_effect=lambda path, points, **kw: {"values": [700.0] * len(points), "resolution_m": 0.1}):
            saved = self.save(elevation_source=f"task:{task.name}:dsm")
            self.assertEqual(saved["mission"]["terrain"]["source"]["id"], f"task:{task.name}:dsm")
            moved = api.duplicate_plan(name=saved["name"], project=self.project_b)
        self.assertEqual(moved["definition"]["elevation_source"], "auto")
        self.assertIsNone(moved["mission"]["terrain"])

    def test_deleted_custom_profile_falls_back_to_the_plans_snapshot(self):
        drone = api.save_drone(profile=drone_profile())
        saved = self.save(drone=drone["id"], camera="dji-phantom-4-pro")
        api.delete_drone(docname=drone["docname"])
        # The plan still opens, previews and exports with what it was planned for.
        self.assertEqual(api.get_plan(name=saved["name"])["mission"]["equipment"]["drone"]["label"], "Acme Survey Hexa")
        d = definition(drone=drone["id"], camera="dji-phantom-4-pro")
        self.assertFalse(api.preview(definition=d)["ok"])                      # unknown without the plan
        out = api.preview(definition=d, plan=saved["name"])
        self.assertTrue(out["ok"])
        self.assertEqual(out["mission"]["equipment"]["drone"]["label"], "Acme Survey Hexa")
        # ...and the plan says where those figures come from.
        self.assertEqual(out["mission"]["equipment"]["drone"]["source"], "snapshot")
        self.assertIn("figures saved with this plan",
                      next(a for a in out["mission"]["assurance"] if a["topic"] == "capability")["summary"])
        again = api.save_plan(name=saved["name"], title="Still works", definition=d)
        self.assertEqual(again["drone_label"], "Acme Survey Hexa")

    def test_deleting_a_project_deletes_its_plans(self):
        project = _project("Flight Plan Doomed Project")
        saved = self.save(project=project)
        frappe.delete_doc("WebODM Project", project)
        self.assertFalse(frappe.db.exists(PLAN, saved["name"]))


class TestEndpointMethods(_Base):
    def test_state_changing_and_outbound_endpoints_are_post_only(self):
        # A GET carries no CSRF token; nothing that writes, deletes, exports or
        # calls an outside service may be reachable that way.
        post_only = ("geocode", "preview", "save_plan", "delete_plan", "duplicate_plan", "export_plan",
                     "save_camera", "delete_camera", "save_drone", "delete_drone")
        for name in post_only:
            fn = getattr(api, name)
            self.assertIn(fn, frappe.whitelisted, name)
            self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func[fn], ["POST"], name)
        for name in ("catalog", "elevation_sources", "list_plans", "get_plan", "export_options"):
            self.assertIn(getattr(api, name), frappe.whitelisted, name)
        self.assertNotIn(api.catalog, frappe.guest_methods)


class TestExport(_Base):
    def test_options_reflect_the_aircraft(self):
        saved = self.save()
        out = api.export_options(name=saved["name"])
        formats = {f["id"]: f for f in out["formats"]}
        self.assertEqual({k for k, f in formats.items() if f["available"]}, {"dji-wpml", "kml-boundary", "kml", "geojson"})
        self.assertEqual(formats["dji-wpml"]["support"]["level"], "official")
        self.assertEqual((formats["dji-wpml"]["files"], formats["dji-wpml"]["waypoints"]), (1, 14))
        self.assertFalse(formats["dji-wpml"]["blocked"])
        self.assertTrue(formats["dji-wpml"]["instructions"])
        self.assertIn("does not read this format", formats["qgc-plan"]["reason"])
        self.assertEqual(out["plan"]["name"], saved["name"])
        self.assertIn("not verified flight-safety information", out["notice"])
        self.assertEqual({a["topic"] for a in out["assurance"]},
                         {"capability", "terrain", "obstacles", "battery", "regulation", "airspace", "weather"})
        choice = api.export_options(name=saved["name"], triggers={"dji-wpml": "waypoint"})
        chosen = next(f for f in choice["formats"] if f["id"] == "dji-wpml")
        self.assertEqual((chosen["trigger"], chosen["waypoints"]), ("waypoint", 140))

    def test_download(self):
        saved = self.save()
        response = api.export_plan(name=saved["name"], format="dji-wpml")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Disposition"], 'attachment; filename="north-field-dji-wpml.kmz"')
        self.assertEqual(response.headers["X-Flight-Plan-Files"], "1")
        z = zipfile.ZipFile(io.BytesIO(response.get_data()))
        self.assertEqual(sorted(z.namelist()), ["wpmz/template.kml", "wpmz/waylines.wpml"])
        self.assertIn(b"<wpml:droneEnumValue>77</wpml:droneEnumValue>", z.read("wpmz/waylines.wpml"))
        kml = api.export_plan(name=saved["name"], format="kml-boundary")
        self.assertEqual(kml.mimetype, "application/vnd.google-earth.kml+xml")
        self.assertIn(b"<Polygon>", kml.get_data())

    def test_blocked_plan_gives_no_mission_file(self):
        saved = self.save(params={"speed_ms": 18})
        with self.assertRaises(frappe.ValidationError) as cm:
            api.export_plan(name=saved["name"], format="dji-wpml", acknowledged=1)
        self.assertIn("cannot be exported as a mission file", str(cm.exception))
        self.assertIn("15 m/s", str(cm.exception))
        # The boundary is still available to plan in the manufacturer's app.
        self.assertEqual(api.export_plan(name=saved["name"], format="kml-boundary").status_code, 200)

    def test_warnings_need_acknowledgement_and_it_is_recorded(self):
        saved = self.save(params={"altitude_m": 150})
        with self.assertRaises(frappe.ValidationError) as cm:
            api.export_plan(name=saved["name"], format="dji-wpml")
        self.assertIn("acknowledged", str(cm.exception))
        self.assertEqual(frappe.db.count("Comment", {"reference_doctype": PLAN, "reference_name": saved["name"]}), 0)
        api.export_plan(name=saved["name"], format="dji-wpml", acknowledged=1)
        comment = frappe.get_all("Comment", filters={"reference_doctype": PLAN, "reference_name": saved["name"]},
                                 fields=["content", "owner", "comment_email"])[0]
        self.assertIn("Exported DJI WPML route", comment.content)
        self.assertIn("AGL_LIMIT", comment.content)
        self.assertIn(self.owner, (comment.owner, comment.comment_email))

    def test_reference_exports_leave_no_audit_note(self):
        saved = self.save()
        api.export_plan(name=saved["name"], format="geojson")
        self.assertEqual(frappe.db.count("Comment", {"reference_doctype": PLAN, "reference_name": saved["name"]}), 0)

    def test_format_the_aircraft_cannot_use_is_refused(self):
        saved = self.save()
        for fmt in ("litchi-csv", "qgc-plan", "nonsense"):
            with self.assertRaises(frappe.ValidationError):
                api.export_plan(name=saved["name"], format=fmt, acknowledged=1)

    def test_firmware_option_reaches_the_exporter(self):
        saved = self.save(drone="generic-mavlink-multirotor", camera="dji-phantom-4-pro",
                          geometry=fx.rect(300, 200), takeoff=fx.lnglat([(0, -110)])[0], params={"speed_ms": 5})
        plan = json.loads(api.export_plan(name=saved["name"], format="qgc-plan", firmware="ardupilot").get_data())
        self.assertEqual(plan["mission"]["firmwareType"], 3)


class TestGeocodeAndAirspace(_Base):
    def test_coordinates_need_no_service(self):
        with patch.object(api.geocode_mod, "search", side_effect=AssertionError("must not be called")):
            out = api.geocode(query="-6.9147, 107.6098")
        self.assertEqual(len(out["results"]), 1)
        self.assertEqual((out["results"][0]["lat"], out["results"][0]["lng"]), (-6.9147, 107.6098))
        self.assertEqual(api.geocode(query="  ")["results"], [])

    def test_address_goes_to_the_geocoder_once_then_the_cache(self):
        hit = [{"lat": -6.9025, "lng": 107.6188, "label": "Gedung Sate, Bandung", "bbox": None,
                "source": "geocoder", "kind": "government", "note": ""}]
        with patch.dict(frappe.conf, {"flightplan_geocoder_url": "https://geo.internal"}), \
                patch.object(api.geocode_mod, "search", return_value=hit) as search:
            first = api.geocode(query="Gedung Sate")
            second = api.geocode(query="gedung sate")
        self.assertEqual(first["results"], hit)
        self.assertEqual(second["results"], hit)
        self.assertIn("OpenStreetMap", first["attribution"])
        self.assertEqual(search.call_count, 1)
        self.assertEqual(search.call_args.kwargs["url"], "https://geo.internal")
        self.assertIn("WebODM-FlightPlanner", search.call_args.kwargs["user_agent"])

    def test_geocoder_off_or_down_still_leaves_coordinates(self):
        with patch.dict(frappe.conf, {"flightplan_geocoder": "none"}), \
                patch.object(api.geocode_mod, "search", side_effect=AssertionError("must not be called")):
            out = api.geocode(query="Bandung")
        self.assertEqual(out["results"], [])
        self.assertIn("switched off", out["message"])
        with patch.dict(frappe.conf, {"flightplan_geocoder_url": "https://geo.internal"}), \
                patch.object(api.geocode_mod, "search", side_effect=api.geocode_mod.GeocodeError("Address search is unavailable")):
            down = api.geocode(query="Jakarta")
        self.assertEqual(down["results"], [])
        self.assertIn("coordinates", down["message"])

    def test_outbound_lookups_are_budgeted_per_user(self):
        with patch.dict(frappe.conf, {"flightplan_geocoder_url": "https://geo.internal"}), \
                patch.object(api, "GEOCODE_LOOKUPS_PER_MINUTE", 3), \
                patch.object(api.geocode_mod, "search", return_value=[]) as search:
            for i in range(5):
                out = api.geocode(query=f"place {i}")
            self.assertEqual(search.call_count, 3)
            self.assertIn("Too many searches", out["message"])
            api.geocode(query="place 0")                                 # cached answers are free
            self.assertEqual(api.geocode(query="-6.9, 107.6")["results"][0]["lat"], -6.9)   # and so are coordinates
            # The budget is per user.
            _as(self.member)
            api.geocode(query="somewhere else")
            self.assertEqual(search.call_count, 4)
        _as(self.owner)
        calls = self.use_provider()
        with patch.object(api, "TERRAIN_FETCHES_PER_MINUTE", 2):
            issues = []
            for i in range(4):
                d = definition(geometry=fx.rect(400, 300, origin=(107.61 + 0.02 * i, -6.82)),
                               takeoff=[107.61 + 0.02 * i, -6.8216])
                issues.append([x["code"] for x in api.preview(definition=d)["mission"]["issues"]])
        self.assertEqual(len(calls), 2)
        self.assertNotIn("TERRAIN_FETCH_FAILED", issues[1])
        self.assertIn("TERRAIN_FETCH_FAILED", issues[3])

    def test_operator_zone_file_makes_airspace_advisory(self):
        ring = fx.lnglat([(100, -50), (300, -50), (300, 50), (100, 50)])
        zones = {"type": "FeatureCollection", "features": [{
            "type": "Feature", "properties": {"name": "Hospital helipad", "type": "aerodrome"},
            "geometry": {"type": "Polygon", "coordinates": [ring + [ring[0]]]}}]}
        path = frappe.get_site_path("private", "fp_test_zones.geojson")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(zones, f)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        with patch.dict(frappe.conf, {"flightplan_airspace_file": "private/fp_test_zones.geojson",
                                      "flightplan_airspace_label": "National UAS zones"}):
            c = api.catalog()
            m = api.preview(definition=definition())["mission"]
        self.assertTrue(c["airspace"]["configured"])
        self.assertIn("National UAS zones (1 zone", c["airspace"]["label"])
        issue = next(i for i in m["issues"] if i["code"] == "AIRSPACE_ZONE")
        self.assertEqual((issue["severity"], issue["basis"]), ("warning", "advisory"))
        self.assertIn("Hospital helipad", issue["message"])
        self.assertEqual(next(a for a in m["assurance"] if a["topic"] == "airspace")["status"], "advisory")

    def test_unreadable_zone_file_means_not_checked(self):
        with patch.dict(frappe.conf, {"flightplan_airspace_file": "private/does_not_exist.geojson"}):
            m = api.preview(definition=definition())["mission"]
        self.assertEqual(next(a for a in m["assurance"] if a["topic"] == "airspace")["status"], "not_checked")
