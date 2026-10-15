"""Mission export: file formats, per-aircraft compatibility and the export gate."""

import csv
import io
import json
import unittest
import xml.etree.ElementTree as ET
import zipfile

from webodm_core.flightplan import _fixtures as fx
from webodm_core.flightplan import plan_mission
from webodm_core.flightplan.exporters import EXPORTERS, ExportError, export, export_options, formats_catalog
from webodm_core.flightplan.exporters.base import build_routes
from webodm_core.flightplan.issues import NOTICE
from webodm_core.flightplan.profiles import normalize_drone

KML = "{http://www.opengis.net/kml/2.2}"
WPML = "{http://www.dji.com/wpmz/1.0.2}"


def mission(definition=None, drone_id="dji-mavic-3e", camera_id=None, terrain=None):
    definition = definition or fx.area_plan(takeoff_m=(0, -170))
    return plan_mission(definition, fx.drone(drone_id), fx.camera(camera_id, drone_id), terrain=terrain), definition


def do_export(m, definition, format_id, **kw):
    kw.setdefault("title", "North field")
    kw.setdefault("created_ms", 1760572800000)
    return export(m, format_id, geometry=definition["geometry"],
                  corridor_width_m=definition.get("corridor_width_m"), **kw)


def options(m, definition, **kw):
    return {o["id"]: o for o in export_options(m, geometry=definition["geometry"],
                                               corridor_width_m=definition.get("corridor_width_m"), **kw)}


def wpml_docs(content):
    z = zipfile.ZipFile(io.BytesIO(content))
    names = sorted(z.namelist())
    return names, ET.fromstring(z.read("wpmz/template.kml")), ET.fromstring(z.read("wpmz/waylines.wpml"))


def text(node, path):
    found = node.find(path)
    return None if found is None else (found.text or "").strip()


class TestRegistry(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(list(EXPORTERS), ["dji-wpml", "qgc-plan", "mavlink-wpl", "litchi-csv", "kml-boundary",
                                           "kml", "geojson"])
        kinds = {e.id: e.kind for e in EXPORTERS.values()}
        self.assertEqual(kinds["dji-wpml"], "mission")
        self.assertEqual(kinds["kml-boundary"], "boundary")
        self.assertEqual(kinds["geojson"], "reference")
        for entry in formats_catalog():
            self.assertTrue(entry["label"] and entry["extension"] and entry["description"])
            self.assertTrue(entry["instructions"])
            self.assertEqual(set(entry["trigger_labels"]), set(entry["triggers"]))


class TestCompatibility(unittest.TestCase):
    """Not every aircraft takes every file; the options say which and why."""

    def available(self, drone_id):
        m, d = mission(drone_id=drone_id)
        return {k: v for k, v in options(m, d).items() if v["available"]}, options(m, d)

    def test_enterprise_dji_gets_wpml(self):
        ok, _all = self.available("dji-mavic-3e")
        self.assertEqual(set(ok), {"dji-wpml", "kml-boundary", "kml", "geojson"})
        self.assertEqual(ok["dji-wpml"]["support"]["level"], "official")
        self.assertEqual(ok["dji-wpml"]["trigger"], "distance")
        self.assertEqual(ok["kml-boundary"]["support"]["level"], "boundary")
        self.assertEqual(ok["kml"]["support"]["level"], "reference")

    def test_consumer_dji_fly_is_marked_unofficial(self):
        ok, _all = self.available("dji-mini-4-pro")
        self.assertEqual(ok["dji-wpml"]["support"]["level"], "community")
        self.assertEqual(ok["dji-wpml"]["trigger"], "manual")
        self.assertEqual(ok["dji-wpml"]["waypoint_cap"], 200)
        self.assertIn("no import button", ok["dji-wpml"]["support"]["note"])

    def test_older_dji_gets_litchi_only(self):
        ok, every = self.available("dji-air-2s")
        self.assertEqual(set(ok), {"litchi-csv", "kml-boundary", "kml", "geojson"})
        self.assertFalse(every["dji-wpml"]["available"])
        self.assertIn("does not read this format", every["dji-wpml"]["reason"])
        self.assertEqual(ok["litchi-csv"]["waypoint_cap"], 99)

    def test_mavlink_vehicles_get_plan_and_waypoints(self):
        ok, _all = self.available("generic-mavlink-multirotor")
        self.assertEqual(set(ok), {"qgc-plan", "mavlink-wpl", "kml-boundary", "kml", "geojson"})

    def test_vendor_planned_aircraft_get_the_boundary_only(self):
        for drone_id, app in [("wingtra-wingtraone-gen2", "WingtraPilot"), ("ageagle-ebee-x", "eMotion"),
                              ("dji-phantom-4-rtk", "DJI GS RTK")]:
            ok, _all = self.available(drone_id)
            self.assertEqual(set(ok), {"kml-boundary", "kml", "geojson"}, drone_id)
            self.assertEqual(ok["kml-boundary"]["support"], {"level": "boundary", "app": app, "note": ""})

    def test_no_waypoint_flight_means_no_mission_file(self):
        ok, every = self.available("dji-mini-3")
        self.assertEqual(set(ok), {"kml-boundary", "kml", "geojson"})
        self.assertIn("no autonomous waypoint flight", every["dji-wpml"]["reason"])
        m, d = mission(drone_id="dji-mini-3")
        with self.assertRaises(ExportError) as cm:
            do_export(m, d, "dji-wpml", acknowledged=True)
        self.assertEqual(cm.exception.code, "EXPORT_NOT_COMPATIBLE")

    def test_incompatible_format_is_refused_even_if_asked_for_directly(self):
        m, d = mission(drone_id="dji-mavic-3e")
        for fmt_id in ("qgc-plan", "litchi-csv", "mavlink-wpl"):
            with self.assertRaises(ExportError) as cm:
                do_export(m, d, fmt_id, acknowledged=True)
            self.assertEqual(cm.exception.code, "EXPORT_NOT_COMPATIBLE")
        with self.assertRaises(ExportError) as cm:
            do_export(m, d, "dji-fly-magic")
        self.assertEqual(cm.exception.code, "EXPORT_UNKNOWN_FORMAT")


class TestGate(unittest.TestCase):
    def test_clean_plan_exports_without_acknowledgement(self):
        m, d = mission()
        opt = options(m, d)["dji-wpml"]
        self.assertFalse(opt["blocked"])
        self.assertFalse(opt["needs_acknowledgement"])
        out = do_export(m, d, "dji-wpml")
        self.assertEqual(out["filename"], "north-field-dji-wpml.kmz")
        self.assertEqual(out["files"], 1)
        self.assertEqual(out["report"]["notice"], NOTICE)

    def test_error_findings_block_mission_files(self):
        m, d = mission(fx.area_plan(takeoff_m=(0, -170), speed_ms=18))
        opt = options(m, d)["dji-wpml"]
        self.assertTrue(opt["blocked"])
        self.assertEqual([i["code"] for i in opt["blocking"]], ["SPEED_ABOVE_MAX"])
        with self.assertRaises(ExportError) as cm:
            do_export(m, d, "dji-wpml", acknowledged=True)      # acknowledging does not unblock an error
        self.assertEqual(cm.exception.code, "EXPORT_BLOCKED")
        self.assertEqual(cm.exception.issues[0]["code"], "SPEED_ABOVE_MAX")
        self.assertIn("15 m/s", str(cm.exception))

    def test_boundary_and_reference_files_stay_available_for_a_blocked_plan(self):
        m, d = mission(fx.area_plan(takeoff_m=(0, -170), speed_ms=18))
        opts = options(m, d)
        for fmt_id in ("kml-boundary", "kml", "geojson"):
            self.assertFalse(opts[fmt_id]["blocked"])
            self.assertTrue(do_export(m, d, fmt_id)["content"])
        findings = json.loads(do_export(m, d, "geojson")["content"])["plan"]["findings"]
        self.assertIn("SPEED_ABOVE_MAX", [f["code"] for f in findings])

    def test_warnings_need_an_explicit_acknowledgement(self):
        m, d = mission(fx.area_plan(takeoff_m=(0, -170), altitude_m=150))        # above the 120 m preset
        opt = options(m, d)["dji-wpml"]
        self.assertFalse(opt["blocked"])
        self.assertTrue(opt["needs_acknowledgement"])
        self.assertIn("AGL_LIMIT", [i["code"] for i in opt["acknowledge"]])
        with self.assertRaises(ExportError) as cm:
            do_export(m, d, "dji-wpml")
        self.assertEqual(cm.exception.code, "EXPORT_NEEDS_ACKNOWLEDGEMENT")
        out = do_export(m, d, "dji-wpml", acknowledged=True)
        self.assertTrue(out["report"]["acknowledged"])

    def test_terrain_errors_block_too(self):
        definition = fx.area_plan(fx.rect(600, 300), takeoff_m=(-300, 0), altitude_m=50)
        m, d = mission(definition, terrain=fx.terrain(fx.slope(700, 0.10)))
        with self.assertRaises(ExportError) as cm:
            do_export(m, d, "dji-wpml", acknowledged=True)
        self.assertEqual(cm.exception.issues[0]["code"], "CLEARANCE_BELOW_MIN")

    def test_terrain_heights_are_not_exported_to_an_aircraft_that_cannot_take_them(self):
        raw = fx.drone("generic-mavlink-multirotor").to_dict() | {"id": "custom:fixed-height"}
        raw["mission"] = {**raw["mission"], "per_waypoint_altitude": False}
        custom = normalize_drone(raw, source="custom")
        definition = fx.area_plan(fx.rect(300, 200), takeoff_m=(0, -110), speed_ms=5, altitude_mode="terrain",
                                  altitude_m=80)
        m = plan_mission(definition, custom, fx.camera("dji-phantom-4-pro"), terrain=fx.terrain(fx.slope(700, 0.05)))
        self.assertTrue(m["terrain"]["following"])
        opt = options(m, definition)["qgc-plan"]
        self.assertTrue(opt["blocked"])
        self.assertIn("TERRAIN_HEIGHTS_NOT_LOADABLE", [i["code"] for i in opt["blocking"]])
        with self.assertRaises(ExportError) as cm:
            do_export(m, definition, "qgc-plan", acknowledged=True)
        self.assertEqual(cm.exception.code, "EXPORT_BLOCKED")
        # The same aircraft at constant altitude exports.
        flat = fx.area_plan(fx.rect(300, 200), takeoff_m=(0, -110), speed_ms=5, altitude_m=80)
        m2 = plan_mission(flat, custom, fx.camera("dji-phantom-4-pro"), terrain=fx.terrain(fx.slope(700, 0.05)))
        self.assertFalse(options(m2, flat)["qgc-plan"]["blocked"])

    def test_missing_takeoff_point_is_a_warning_on_every_mission_format(self):
        for drone_id, fmt_id, code in [("dji-mavic-3e", "dji-wpml", "WPML_TAKEOFF_ASSUMED"),
                                       ("generic-mavlink-multirotor", "qgc-plan", "MAV_TAKEOFF_ASSUMED"),
                                       ("dji-air-2s", "litchi-csv", "LITCHI_TAKEOFF_ASSUMED")]:
            m, d = mission(fx.area_plan(), drone_id)
            opt = options(m, d)[fmt_id]
            self.assertTrue(opt["needs_acknowledgement"], fmt_id)
            self.assertIn(code, [i["code"] for i in opt["acknowledge"]])

    def test_unknown_trigger_and_missing_mission(self):
        m, d = mission(drone_id="generic-mavlink-multirotor")
        with self.assertRaises(ExportError) as cm:
            do_export(m, d, "qgc-plan", trigger="waypoint")
        self.assertEqual(cm.exception.code, "EXPORT_BAD_TRIGGER")
        with self.assertRaises(ExportError) as cm:
            export({}, "kml", geometry=d["geometry"])
        self.assertEqual(cm.exception.code, "EXPORT_NO_MISSION")
        with self.assertRaises(ExportError) as cm:
            export(m, "kml")
        self.assertEqual(cm.exception.code, "EXPORT_NO_GEOMETRY")

    def test_export_is_deterministic(self):
        m, d = mission()
        self.assertEqual(do_export(m, d, "dji-wpml")["content"], do_export(m, d, "dji-wpml")["content"])


class TestDjiWpml(unittest.TestCase):
    def test_archive_layout_and_mission_config(self):
        m, d = mission()
        names, template, waylines = wpml_docs(do_export(m, d, "dji-wpml")["content"])
        self.assertEqual(names, ["wpmz/template.kml", "wpmz/waylines.wpml"])
        for doc in (template, waylines):
            cfg = doc.find(f"{KML}Document/{WPML}missionConfig")
            self.assertEqual(text(cfg, f"{WPML}flyToWaylineMode"), "safely")
            self.assertEqual(text(cfg, f"{WPML}finishAction"), "goHome")
            self.assertEqual(text(cfg, f"{WPML}droneInfo/{WPML}droneEnumValue"), "77")
            self.assertEqual(text(cfg, f"{WPML}droneInfo/{WPML}droneSubEnumValue"), "0")
            self.assertEqual(text(cfg, f"{WPML}payloadInfo/{WPML}payloadEnumValue"), "66")
            self.assertEqual(text(cfg, f"{WPML}payloadInfo/{WPML}payloadPositionIndex"), "0")
            self.assertTrue(0 < float(text(cfg, f"{WPML}globalTransitionalSpeed")) <= 15)
            self.assertTrue(1.2 <= float(text(cfg, f"{WPML}takeOffSecurityHeight")) <= 1500)
        self.assertEqual(text(template, f"{KML}Document/{WPML}createTime"), "1760572800000")
        folder = template.find(f"{KML}Document/{KML}Folder")
        self.assertEqual(text(folder, f"{WPML}templateType"), "waypoint")
        self.assertEqual(text(folder, f"{WPML}waylineCoordinateSysParam/{WPML}heightMode"), "relativeToStartPoint")
        wl = waylines.find(f"{KML}Document/{KML}Folder")
        self.assertEqual(text(wl, f"{WPML}executeHeightMode"), "relativeToStartPoint")
        self.assertEqual(text(wl, f"{WPML}autoFlightSpeed"), "10")
        # The reference takeoff point is written latitude first, as the WPML reference specifies.
        ref = text(template, f"{KML}Document/{WPML}missionConfig/{WPML}takeOffRefPoint").split(",")
        self.assertAlmostEqual(float(ref[0]), m["takeoff"]["lat"], places=6)
        self.assertAlmostEqual(float(ref[1]), m["takeoff"]["lng"], places=6)

    def test_waypoints_match_the_mission(self):
        m, d = mission()
        _n, template, waylines = wpml_docs(do_export(m, d, "dji-wpml")["content"])
        for doc, height_tag in ((template, "height"), (waylines, "executeHeight")):
            marks = doc.findall(f"{KML}Document/{KML}Folder/{KML}Placemark")
            self.assertEqual(len(marks), len(m["waypoints"]))
            self.assertEqual([int(text(p, f"{WPML}index")) for p in marks], list(range(len(marks))))
            for p, wp in zip(marks, m["waypoints"]):
                lng, lat = map(float, text(p, f"{KML}Point/{KML}coordinates").split(","))
                self.assertAlmostEqual(lng, wp["lng"], places=6)       # longitude first in KML
                self.assertAlmostEqual(lat, wp["lat"], places=6)
                self.assertEqual(float(text(p, f"{WPML}{height_tag}")), wp["alt"])

    def test_distance_trigger_spans_each_flight_line(self):
        m, d = mission()
        _n, _t, waylines = wpml_docs(do_export(m, d, "dji-wpml")["content"])
        groups = waylines.findall(f".//{WPML}actionGroup")
        ids = [int(text(g, f"{WPML}actionGroupId")) for g in groups]
        self.assertEqual(ids, list(range(len(ids))))               # unique and increasing
        photo = [g for g in groups if text(g, f"{WPML}actionTrigger/{WPML}actionTriggerType") == "multipleDistance"]
        self.assertEqual(len(photo), len(m["legs"]))
        for g, leg in zip(photo, m["legs"]):
            self.assertEqual(int(text(g, f"{WPML}actionGroupStartIndex")), leg["from"])
            self.assertEqual(int(text(g, f"{WPML}actionGroupEndIndex")), leg["to"])
            self.assertEqual(float(text(g, f"{WPML}actionTrigger/{WPML}actionTriggerParam")),
                             m["camera"]["photo_spacing_m"])
            self.assertEqual(text(g, f"{WPML}action/{WPML}actionActuatorFunc"), "takePhoto")
        gimbal = [g for g in groups if text(g, f"{WPML}action/{WPML}actionActuatorFunc") == "gimbalRotate"]
        self.assertEqual(len(gimbal), 1)
        self.assertEqual(text(gimbal[0], f".//{WPML}gimbalPitchRotateAngle"), "-90")
        self.assertEqual(text(gimbal[0], f"{WPML}actionGroupStartIndex"), "0")

    def test_other_trigger_modes(self):
        m, d = mission()
        timed = wpml_docs(do_export(m, d, "dji-wpml", trigger="time")["content"])[2]
        t = [g for g in timed.findall(f".//{WPML}actionGroup")
             if text(g, f"{WPML}actionTrigger/{WPML}actionTriggerType") == "multipleTiming"]
        self.assertEqual(len(t), len(m["legs"]))
        self.assertEqual(float(text(t[0], f"{WPML}actionTrigger/{WPML}actionTriggerParam")),
                         m["camera"]["photo_interval_s"])
        with self.assertRaises(ExportError):
            do_export(m, d, "dji-wpml", trigger="manual")           # a warning: needs acknowledging
        manual = wpml_docs(do_export(m, d, "dji-wpml", trigger="manual", acknowledged=True)["content"])[2]
        funcs = [text(a, f"{WPML}actionActuatorFunc") for a in manual.findall(f".//{WPML}action")]
        self.assertEqual(funcs, ["gimbalRotate"])

    def test_photo_at_every_waypoint(self):
        m, d = mission()
        out = do_export(m, d, "dji-wpml", trigger="waypoint")
        _n, _t, waylines = wpml_docs(out["content"])
        marks = waylines.findall(f"{KML}Document/{KML}Folder/{KML}Placemark")
        shots = [g for g in waylines.findall(f".//{WPML}actionGroup")
                 if text(g, f"{WPML}action/{WPML}actionActuatorFunc") == "takePhoto"]
        self.assertEqual(len(shots), m["stats"]["photo_count"])
        for g in shots:
            self.assertEqual(text(g, f"{WPML}actionTrigger/{WPML}actionTriggerType"), "reachPoint")
            self.assertEqual(text(g, f"{WPML}actionGroupStartIndex"), text(g, f"{WPML}actionGroupEndIndex"))
        # One waypoint per photo plus, on each line, its end point where no photo falls.
        self.assertGreaterEqual(len(marks), m["stats"]["photo_count"])
        self.assertLessEqual(len(marks), m["stats"]["photo_count"] + len(m["legs"]))
        self.assertIn("TRIGGER_STOP_AND_SHOOT", [i["code"] for i in out["report"]["issues"]])

    def test_terrain_following_heights_and_fly_through_turns(self):
        definition = fx.area_plan(fx.rect(600, 300), takeoff_m=(-300, 0), altitude_mode="terrain", altitude_m=80,
                                  vlos_m=1000)
        m, d = mission(definition, terrain=fx.terrain(fx.ridge(700, 60, 120)))
        _n, template, waylines = wpml_docs(do_export(m, d, "dji-wpml")["content"])
        marks = waylines.findall(f"{KML}Document/{KML}Folder/{KML}Placemark")
        heights = [float(text(p, f"{WPML}executeHeight")) for p in marks]
        self.assertEqual(heights, [w["alt"] for w in m["waypoints"]])
        self.assertGreater(max(heights) - min(heights), 50)
        for p, wp in zip(marks, m["waypoints"]):
            mode = text(p, f"{WPML}waypointTurnParam/{WPML}waypointTurnMode")
            if wp["role"] == "via":
                self.assertEqual(mode, "toPointAndPassWithContinuityCurvature")
                self.assertEqual(text(p, f"{WPML}useStraightLine"), "1")
                self.assertGreater(float(text(p, f"{WPML}waypointTurnParam/{WPML}waypointTurnDampingDist")), 0)
            else:
                self.assertEqual(mode, "toPointAndStopWithDiscontinuityCurvature")
        via = [p for p in template.findall(f".//{KML}Placemark") if text(p, f"{WPML}useGlobalTurnParam") == "0"]
        self.assertEqual(len(via), sum(1 for w in m["waypoints"] if w["role"] == "via"))

    def test_consumer_file_has_no_payload_block_and_is_capped(self):
        definition = fx.area_plan(fx.rect(300, 200), takeoff_m=(0, -110), altitude_m=50, speed_ms=5)
        m, d = mission(definition, "dji-mini-4-pro")
        self.assertEqual(len(m["flights"]), 1)
        self.assertGreater(m["stats"]["photo_count"], 200)
        out = do_export(m, d, "dji-wpml", acknowledged=True)         # manual trigger -> warning
        self.assertIn("TRIGGER_MANUAL", [i["code"] for i in out["report"]["issues"]])
        _n, template, _w = wpml_docs(out["content"])
        cfg = template.find(f"{KML}Document/{WPML}missionConfig")
        self.assertEqual(text(cfg, f"{WPML}droneInfo/{WPML}droneEnumValue"), "68")
        self.assertIsNone(cfg.find(f"{WPML}payloadInfo"))
        self.assertIn("WPML_AIRCRAFT_ID_UNOFFICIAL", [i["code"] for i in out["report"]["issues"]])
        # A photo at every waypoint exceeds DJI Fly's ~200 waypoints: split into parts.
        split = do_export(m, d, "dji-wpml", trigger="waypoint", acknowledged=True)
        self.assertEqual(split["mime"], "application/zip")
        z = zipfile.ZipFile(io.BytesIO(split["content"]))
        kmz = [n for n in z.namelist() if n.endswith(".kmz")]
        self.assertGreater(len(kmz), 1)
        self.assertIn("README.txt", z.namelist())
        for name in kmz:
            _n2, _t2, w = wpml_docs(z.read(name))
            self.assertLessEqual(len(w.findall(f".//{KML}Placemark")), 200)
        self.assertEqual(sum(r["waypoints"] for r in split["report"]["routes"]),
                         sum(len(r.points) for r in build_routes(m, "waypoint", 200)))

    def test_custom_drone_without_wpml_id_is_refused(self):
        raw = fx.drone().to_dict() | {"id": "custom:x"}
        raw["mission"] = {**raw["mission"], "wpml": None}
        custom = normalize_drone(raw, source="custom")
        definition = fx.area_plan(takeoff_m=(0, -170))
        m = plan_mission(definition, custom, fx.camera())
        with self.assertRaises(ExportError) as cm:
            do_export(m, definition, "dji-wpml")
        self.assertEqual(cm.exception.issues[0]["code"], "WPML_NO_AIRCRAFT_ID")

    def test_finish_action_mapping(self):
        for action, expected in (("hover", "noAction"), ("land", "autoLand"), ("rth", "goHome")):
            m, d = mission(fx.area_plan(takeoff_m=(0, -170), finish_action=action))
            _n, template, _w = wpml_docs(do_export(m, d, "dji-wpml")["content"])
            self.assertEqual(text(template, f"{KML}Document/{WPML}missionConfig/{WPML}finishAction"), expected)

    def test_title_is_escaped(self):
        m, d = mission()
        out = do_export(m, d, "kml", title='A & B <"survey">')
        self.assertEqual(out["filename"], "a-b-survey-kml.kml")
        ET.fromstring(out["content"])                                # still well-formed


class TestMavlink(unittest.TestCase):
    def setUp(self):
        # 5 m/s keeps the photo interval above the 2 s assumed for this camera,
        # and the area is small enough for one of this vehicle's batteries.
        self.m, self.d = mission(fx.area_plan(fx.rect(300, 200), takeoff_m=(0, -110), speed_ms=5),
                                 "generic-mavlink-multirotor")
        self.assertEqual(len(self.m["flights"]), 1)

    def test_qgc_plan_structure(self):
        plan = json.loads(do_export(self.m, self.d, "qgc-plan")["content"])
        self.assertEqual((plan["fileType"], plan["version"], plan["groundStation"]), ("Plan", 1, "QGroundControl"))
        mis = plan["mission"]
        self.assertEqual((mis["firmwareType"], mis["vehicleType"], mis["version"]), (12, 2, 2))
        self.assertEqual(mis["plannedHomePosition"][:2], [self.m["takeoff"]["lat"], self.m["takeoff"]["lng"]])
        items = mis["items"]
        self.assertEqual([i["doJumpId"] for i in items], list(range(1, len(items) + 1)))
        self.assertEqual([i["command"] for i in items[:3]], [22, 178, 205])
        self.assertEqual(items[-1]["command"], 20)
        self.assertTrue(all(len(i["params"]) == 7 and i["type"] == "SimpleItem" for i in items))
        nav = [i for i in items if i["command"] == 16]
        self.assertEqual(len(nav), len(self.m["waypoints"]))
        for item, wp in zip(nav, self.m["waypoints"]):
            self.assertEqual(item["frame"], 3)
            self.assertEqual(item["params"][4:], [wp["lat"], wp["lng"], wp["alt"]])
            self.assertEqual((item["Altitude"], item["AltitudeMode"]), (wp["alt"], 1))
        # Commands without a position carry no altitude keys.
        self.assertTrue(all("Altitude" not in i for i in items if i["frame"] == 2))

    def test_camera_trigger_brackets_every_line(self):
        items = json.loads(do_export(self.m, self.d, "qgc-plan")["content"])["mission"]["items"]
        seq = [(i["command"], i["params"][0]) for i in items if i["command"] in (16, 206)]
        spacing = self.m["camera"]["photo_spacing_m"]
        for k in range(len(self.m["legs"])):
            chunk = seq[4 * k:4 * k + 4]
            self.assertEqual([c[0] for c in chunk], [16, 206, 16, 206])
            self.assertEqual(chunk[1][1], spacing)                    # on after the line start
            self.assertEqual(chunk[3][1], 0)                          # off after the line end

    def test_time_trigger_and_manual(self):
        timed = json.loads(do_export(self.m, self.d, "qgc-plan", trigger="time")["content"])["mission"]["items"]
        starts = [i for i in timed if i["command"] == 2000]
        self.assertEqual(len(starts), len(self.m["legs"]))
        self.assertEqual(starts[0]["params"][1], self.m["camera"]["photo_interval_s"])
        self.assertEqual(len([i for i in timed if i["command"] == 2001]), len(self.m["legs"]))
        manual = json.loads(do_export(self.m, self.d, "qgc-plan", trigger="manual")["content"])["mission"]["items"]
        self.assertFalse([i for i in manual if i["command"] in (206, 2000, 2001)])

    def test_firmware_and_vehicle_type(self):
        ardu = json.loads(do_export(self.m, self.d, "qgc-plan", options={"firmware": "ardupilot"})["content"])
        self.assertEqual(ardu["mission"]["firmwareType"], 3)
        m, d = mission(fx.area_plan(takeoff_m=(0, -170), speed_ms=16), "generic-mavlink-fixed-wing")
        plane = json.loads(do_export(m, d, "qgc-plan", acknowledged=True)["content"])
        self.assertEqual(plane["mission"]["vehicleType"], 1)

    def test_finish_actions(self):
        for action, last in (("land", 21), ("rth", 20), ("hover", 206)):
            m, d = mission(fx.area_plan(fx.rect(300, 200), takeoff_m=(0, -110), speed_ms=5, finish_action=action),
                           "generic-mavlink-multirotor")
            items = json.loads(do_export(m, d, "qgc-plan")["content"])["mission"]["items"]
            self.assertEqual(items[-1]["command"], last)

    def test_waypoint_list_format(self):
        lines = do_export(self.m, self.d, "mavlink-wpl")["content"].decode().splitlines()
        self.assertEqual(lines[0], "QGC WPL 110")
        rows = [ln.split("\t") for ln in lines[1:]]
        self.assertTrue(all(len(r) == 12 for r in rows))
        self.assertEqual([int(r[0]) for r in rows], list(range(len(rows))))
        home = rows[0]
        self.assertEqual((home[1], home[2], home[3]), ("1", "0", "16"))       # current, absolute frame
        self.assertAlmostEqual(float(home[8]), self.m["takeoff"]["lat"], places=6)
        self.assertEqual(rows[1][3], "22")
        self.assertEqual(rows[-1][3], "20")
        nav = [r for r in rows[1:] if r[3] == "16"]
        self.assertEqual(len(nav), len(self.m["waypoints"]))
        self.assertEqual([float(r[10]) for r in nav], [w["alt"] for w in self.m["waypoints"]])
        self.assertTrue(all(r[11] == "1" for r in rows))

    def test_terrain_heights_are_exported_per_waypoint(self):
        definition = fx.area_plan(fx.rect(400, 120), takeoff_m=(-200, 0), altitude_mode="terrain", altitude_m=80,
                                  speed_ms=5)
        m, d = mission(definition, "generic-mavlink-multirotor", terrain=fx.terrain(fx.ridge(700, 60, 120)))
        self.assertEqual(len(m["flights"]), 1)
        # Descending the ridge at 5 m/s is beyond this vehicle: a warning to acknowledge.
        plan = json.loads(do_export(m, d, "qgc-plan", acknowledged=True)["content"])
        alts = [i["Altitude"] for i in plan["mission"]["items"] if i["command"] == 16]
        self.assertEqual(alts, [w["alt"] for w in m["waypoints"]])
        self.assertEqual(plan["mission"]["plannedHomePosition"][2], 700.0)


class TestLitchi(unittest.TestCase):
    def rows(self, content):
        return list(csv.reader(io.StringIO(content.decode())))

    def test_columns_and_values(self):
        m, d = mission(drone_id="dji-air-2s")
        rows = self.rows(do_export(m, d, "litchi-csv")["content"])
        header = rows[0]
        self.assertEqual(len(header), 46)
        self.assertEqual(header[:8], ["latitude", "longitude", "altitude(m)", "heading(deg)", "curvesize(m)",
                                      "rotationdir", "gimbalmode", "gimbalpitchangle"])
        self.assertEqual(header[8:12], ["actiontype1", "actionparam1", "actiontype2", "actionparam2"])
        self.assertEqual(header[36:38], ["actiontype15", "actionparam15"])
        self.assertEqual(header[38:], ["altitudemode", "speed(m/s)", "poi_latitude", "poi_longitude",
                                       "poi_altitude(m)", "poi_altitudemode", "photo_timeinterval",
                                       "photo_distinterval"])
        body = rows[1:]
        self.assertEqual(len(body), len(m["waypoints"]))
        self.assertTrue(all(len(r) == 46 for r in body))
        for r, wp in zip(body, m["waypoints"]):
            self.assertAlmostEqual(float(r[0]), wp["lat"], places=6)
            self.assertAlmostEqual(float(r[1]), wp["lng"], places=6)
            self.assertEqual(float(r[2]), wp["alt"])
            self.assertIn(r[5], ("0", "1"))
            self.assertEqual((r[6], r[7], r[38]), ("2", "-90", "0"))
            self.assertEqual(float(r[39]), m["stats"]["speed_ms"])
            self.assertEqual(r[8:38], ["-1", "0"] * 15)
            # Distance interval on at a line start, off at its end.
            self.assertEqual(float(r[45]), m["camera"]["photo_spacing_m"] if wp["photo"] else -1)
            self.assertEqual(r[44], "-1")

    def test_missions_over_99_waypoints_are_split(self):
        definition = fx.area_plan(fx.rect(1600, 1500), takeoff_m=(0, -760), altitude_m=60, speed_ms=5,
                                  direction=0, transit_speed_ms=13, vlos_m=5000)
        m, d = mission(definition, "dji-phantom-4-pro-v2")
        self.assertGreater(m["stats"]["waypoint_count"], 99)
        out = do_export(m, d, "litchi-csv")
        z = zipfile.ZipFile(io.BytesIO(out["content"]))
        files = sorted(n for n in z.namelist() if n.endswith(".csv"))
        self.assertEqual(len(files), out["files"])
        total = 0
        for name in files:
            body = self.rows(z.read(name))[1:]
            self.assertLessEqual(len(body), 99)
            self.assertGreaterEqual(len(body), 2)
            total += len(body)
        self.assertEqual(total, m["stats"]["waypoint_count"])
        readme = z.read("README.txt").decode()
        self.assertIn(NOTICE, readme)
        self.assertIn("Fly in this order".lower(), readme.lower())
        self.assertIn("LITCHI_SPLIT", [i["code"] for i in out["report"]["issues"]])

    def test_photo_action_per_waypoint(self):
        m, d = mission(drone_id="dji-air-2s")
        out = do_export(m, d, "litchi-csv", trigger="waypoint")
        z = zipfile.ZipFile(io.BytesIO(out["content"])) if out["mime"] == "application/zip" else None
        contents = [z.read(n) for n in z.namelist() if n.endswith(".csv")] if z else [out["content"]]
        shots = sum(1 for c in contents for r in self.rows(c)[1:] if r[8] == "1")
        self.assertEqual(shots, m["stats"]["photo_count"])

    def test_speed_and_height_limits(self):
        m, d = mission(fx.area_plan(takeoff_m=(0, -170), altitude_m=120), "dji-air-2s")
        m["stats"]["speed_ms"] = 16.0                       # beyond what Litchi flies
        with self.assertRaises(ExportError) as cm:
            do_export(m, d, "litchi-csv")
        self.assertEqual(cm.exception.issues[0]["code"], "LITCHI_SPEED")


class TestKmlAndGeoJson(unittest.TestCase):
    def test_boundary_kml_holds_exactly_one_geometry(self):
        m, d = mission()
        out = do_export(m, d, "kml-boundary")
        root = ET.fromstring(out["content"])
        marks = root.findall(f".//{KML}Placemark")
        self.assertEqual(len(marks), 1)
        coords = text(marks[0], f"{KML}Polygon/{KML}outerBoundaryIs/{KML}LinearRing/{KML}coordinates").split()
        self.assertEqual(len(coords), 5)
        self.assertEqual(coords[0], coords[-1])
        lng, lat, _z = map(float, coords[0].split(","))
        self.assertAlmostEqual(lng, d["geometry"]["coordinates"][0][0][0], places=6)
        self.assertAlmostEqual(lat, d["geometry"]["coordinates"][0][0][1], places=6)
        codes = [i["code"] for i in out["report"]["issues"]]
        self.assertEqual(codes, ["BOUNDARY_PARAMETERS"])
        self.assertIn("height 100 m", out["report"]["issues"][0]["message"])

    def test_corridor_boundary_is_the_centre_line(self):
        definition = fx.corridor_plan(takeoff_m=(-600, -30))
        m, d = mission(definition, "wingtra-wingtraone-gen2")
        out = do_export(m, d, "kml-boundary")
        root = ET.fromstring(out["content"])
        self.assertEqual(len(text(root, f".//{KML}LineString/{KML}coordinates").split()), 3)
        self.assertIn("corridor width 120 m", out["report"]["issues"][0]["message"])

    def test_holes_survive_in_the_boundary(self):
        with_hole = fx.polygon([(-200, -150), (200, -150), (200, 150), (-200, 150)],
                               holes=[[(-50, -50), (50, -50), (50, 50), (-50, 50)]])
        m, d = mission(fx.area_plan(with_hole, takeoff_m=(0, -170)))
        root = ET.fromstring(do_export(m, d, "kml-boundary")["content"])
        self.assertEqual(len(root.findall(f".//{KML}innerBoundaryIs")), 1)

    def test_overview_kml(self):
        m, d = mission()
        root = ET.fromstring(do_export(m, d, "kml")["content"])
        names = [text(p, f"{KML}name") for p in root.findall(f".//{KML}Placemark")]
        self.assertEqual(names[:3], ["Survey boundary", "Takeoff point", "Flight path"])
        self.assertEqual(len(names), 3 + len(m["waypoints"]))
        self.assertIn("not verified flight-safety information", text(root, f"{KML}Document/{KML}description"))
        path = text(root, f".//{KML}LineString/{KML}coordinates").split()
        self.assertEqual(len(path), len(m["waypoints"]))

    def test_geojson(self):
        m, d = mission(fx.area_plan(takeoff_m=(0, -170)), terrain=fx.terrain(fx.slope(700, 0.02)))
        doc = json.loads(do_export(m, d, "geojson")["content"])
        self.assertEqual(doc["type"], "FeatureCollection")
        self.assertEqual(doc["notice"], NOTICE)
        roles = [f["properties"]["role"] for f in doc["features"]]
        self.assertEqual(roles[:3], ["boundary", "takeoff", "flight_path"])
        self.assertEqual(roles.count("waypoint"), len(m["waypoints"]))
        path = doc["features"][2]["geometry"]["coordinates"]
        self.assertEqual(path[0], [m["waypoints"][0]["lng"], m["waypoints"][0]["lat"], m["waypoints"][0]["alt"]])
        self.assertEqual(doc["plan"]["estimates"]["photo_count"], m["stats"]["photo_count"])
        self.assertNotIn("profile", doc["plan"]["terrain"])
        self.assertEqual({a["topic"] for a in doc["plan"]["assurance"]}, {a["topic"] for a in m["assurance"]})
        wp = doc["features"][3]["properties"]
        self.assertIsNotNone(wp["estimated_height_above_ground_m"])


class TestFindingVocabulary(unittest.TestCase):
    """Every finding, from the planner or an exporter, uses the fixed vocabulary the UI words."""

    def test_severity_basis_and_category_are_always_known(self):
        from webodm_core.flightplan.issues import BASES, CATEGORIES, SEVERITIES

        cases = [
            ("dji-mavic-3e", fx.area_plan()),
            ("dji-mavic-3e", fx.area_plan(takeoff_m=(0, -170), speed_ms=18, altitude_m=150, gimbal_pitch_deg=-60)),
            ("dji-mini-4-pro", fx.area_plan(fx.rect(900, 700), altitude_m=50, speed_ms=5)),
            ("dji-air-2s", fx.corridor_plan(width=40)),
            ("generic-mavlink-fixed-wing", fx.area_plan(speed_ms=16, finish_action="hover")),
            ("wingtra-wingtraone-gen2", fx.area_plan(altitude_mode="terrain")),
            ("dji-mini-3", fx.area_plan()),
            ("dji-mavic-3e", fx.area_plan(altitude_mode="terrain")),      # no data when terrain is None
        ]
        seen = set()
        for drone_id, definition in cases:
            for terrain in (None, fx.terrain(fx.ridge(700, 60, 120))):
                m, d = mission(definition, drone_id, terrain=terrain)
                found = list(m["issues"])
                for entry in export_options(m, geometry=d["geometry"], corridor_width_m=d.get("corridor_width_m")):
                    found += entry.get("issues", [])
                for trigger in ("waypoint", "manual", "time"):
                    for entry in export_options(m, geometry=d["geometry"], corridor_width_m=d.get("corridor_width_m"),
                                                triggers={"dji-wpml": trigger, "litchi-csv": trigger}):
                        found += entry.get("issues", [])
                for i in found:
                    self.assertIn(i["severity"], SEVERITIES, i)
                    self.assertIn(i["basis"], BASES, i)
                    self.assertIn(i["category"], CATEGORIES, i)
                    self.assertTrue(i["message"].strip(), i)
                    seen.add(i["basis"])
                for a in m["assurance"]:
                    self.assertIn(a["status"], BASES[:4], a)       # a topic is never a mere "note"
        self.assertEqual(seen, set(BASES))

    def test_guidance_about_a_file_is_a_note_not_a_check(self):
        m, d = mission()
        notes = {i["code"]: i["basis"] for i in options(m, d)["dji-wpml"]["issues"]}
        self.assertEqual(notes["WPML_RELATIVE_HEIGHTS"], "note")
        m2, d2 = mission(fx.area_plan())
        self.assertEqual(next(i for i in m2["issues"] if i["code"] == "TAKEOFF_ASSUMED")["basis"], "note")


class TestRoutes(unittest.TestCase):
    def test_one_route_per_battery_flight(self):
        definition = fx.area_plan(fx.rect(2000, 1500), takeoff_m=(0, -760), altitude_m=80, speed_ms=8, vlos_m=5000)
        m, d = mission(definition, "dji-mavic-3-classic")
        self.assertGreater(len(m["flights"]), 1)
        routes = build_routes(m, "distance", None)
        self.assertEqual(len(routes), len(m["flights"]))
        for r, f in zip(routes, m["flights"]):
            self.assertEqual(len(r.points), f["wp_to"] - f["wp_from"] + 1)
            self.assertTrue(r.points[0].first_of_leg)
            self.assertTrue(r.points[-1].last_of_leg)
        out = do_export(m, d, "dji-wpml", acknowledged=True)
        z = zipfile.ZipFile(io.BytesIO(out["content"]))
        names = sorted(n for n in z.namelist() if n.endswith(".kmz"))
        self.assertEqual(len(names), len(m["flights"]))
        self.assertIn(f"flight-1-of-{len(m['flights'])}", names[0])

    def test_caps_split_at_line_ends_and_cover_everything_once(self):
        m, _d = mission(fx.area_plan(fx.rect(1200, 900), takeoff_m=(0, -460), altitude_m=60, vlos_m=5000))
        full = build_routes(m, "distance", None)
        capped = build_routes(m, "distance", 10)
        self.assertGreater(len(capped), len(full))
        self.assertEqual(sum(len(r.points) for r in capped), sum(len(r.points) for r in full))
        for r in capped:
            self.assertLessEqual(len(r.points), 10)
            self.assertTrue(r.points[0].first_of_leg and r.points[-1].last_of_leg)
            self.assertEqual(r.parts, sum(1 for other in capped if other.flight == r.flight))
        # Parts are numbered within their battery flight, in flight order.
        self.assertEqual([(r.flight, r.part) for r in capped], sorted((r.flight, r.part) for r in capped))

    def test_a_line_longer_than_the_cap_is_cut_and_resumed_from_the_cut(self):
        m, _d = mission(fx.corridor_plan(((0, 0), (1500, 0)), width=40, takeoff_m=(0, -20), altitude_m=60, vlos_m=5000))
        routes = build_routes(m, "waypoint", 20)
        self.assertGreater(len(routes), 1)
        for a, b in zip(routes, routes[1:]):
            self.assertLessEqual(len(a.points), 20)
            self.assertEqual((a.points[-1].lng, a.points[-1].lat), (b.points[0].lng, b.points[0].lat))

    def test_per_photo_route_keeps_corridor_bends(self):
        definition = fx.corridor_plan(((-400, 0), (0, 0), (300, 300)), width=40, takeoff_m=(-400, -20), altitude_m=60)
        m, _d = mission(definition)
        route = build_routes(m, "waypoint", None)[0]
        f = fx.frame()
        near_bend = min(abs(f.forward(p.lng, p.lat)[0]) + abs(f.forward(p.lng, p.lat)[1]) for p in route.points)
        self.assertLess(near_bend, 0.5)
        self.assertEqual(sum(1 for p in route.points if p.take_photo), m["stats"]["photo_count"])


if __name__ == "__main__":
    unittest.main()
