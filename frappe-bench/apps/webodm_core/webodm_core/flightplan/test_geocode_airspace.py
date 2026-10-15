"""Site search (coordinates and addresses) and the advisory airspace zone check."""

import unittest

from webodm_core.flightplan import _fixtures as fx
from webodm_core.flightplan import plan_mission
from webodm_core.flightplan.airspace import ZoneFileError, ZoneSet, load_zones, zones_touched
from webodm_core.flightplan.geocode import GeocodeError, parse_coordinates, search


class TestParseCoordinates(unittest.TestCase):
    def check(self, text, lat, lng, places=4):
        out = parse_coordinates(text)
        self.assertIsNotNone(out, text)
        self.assertAlmostEqual(out["lat"], lat, places=places, msg=text)
        self.assertAlmostEqual(out["lng"], lng, places=places, msg=text)
        self.assertEqual(out["source"], "coordinates")
        return out

    def test_decimal_degrees(self):
        for text in ("-6.9147, 107.6098", "-6.9147 107.6098", "  -6.9147,107.6098 ", "-6.9147; 107.6098",
                     "-6.9147 / 107.6098"):
            self.check(text, -6.9147, 107.6098)
        self.check("40.7128, -74.0060", 40.7128, -74.0060)
        self.check("0, 0", 0.0, 0.0)

    def test_hemisphere_letters(self):
        self.check("6.9147 S, 107.6098 E", -6.9147, 107.6098)
        self.check("6.9147S 107.6098E", -6.9147, 107.6098)
        self.check("S6.9147 E107.6098", -6.9147, 107.6098)
        self.check("N 40.7128, W 74.0060", 40.7128, -74.0060)
        self.check("40.7128° N, 74.0060° W", 40.7128, -74.0060)

    def test_degrees_minutes_seconds(self):
        self.check("6°54'52.9\"S 107°36'35.3\"E", -6.91469, 107.60981)
        self.check("40 42 46 N 74 0 21 W", 40.71278, -74.00583)
        self.check("40°42.767'N, 74°0.36'W", 40.71278, -74.006)
        self.check("-6 54 52.9, 107 36 35.3", -6.91469, 107.60981)

    def test_longitude_first_is_recognised_only_when_unambiguous(self):
        out = self.check("107.6098 E, 6.9147 S", -6.9147, 107.6098)
        self.assertIn("longitude, latitude", out["note"])
        out = self.check("107.6098, -6.9147", -6.9147, 107.6098)
        self.assertIn("too large for a latitude", out["note"])
        # Both orders are plausible here: latitude first, as written, no note.
        self.assertEqual(self.check("45.5, 10.2", 45.5, 10.2)["note"], "")

    def test_map_links(self):
        self.check("https://www.google.com/maps/@-6.9147,107.6098,15z", -6.9147, 107.6098)
        self.check("https://maps.example/?q=40.7128,-74.0060", 40.7128, -74.0060)

    def test_label_is_readable(self):
        self.assertEqual(parse_coordinates("-6.9147, 107.6098")["label"], "6.914700° S, 107.609800° E")

    def test_addresses_and_nonsense_are_not_coordinates(self):
        for text in ("Jalan Diponegoro 22, Bandung", "Gedung Sate", "", "   ", "12", "91, 95", "200, 10", "10, 181",
                     "6 S, 107 N", "-6 N, 107 E", "1 2 3 4 5", "40 75 10 N 74 0 0 W", "x" * 300, None,
                     "Route 66, 12"):
            self.assertIsNone(parse_coordinates(text), text)


class TestSearch(unittest.TestCase):
    ANSWER = [
        {"lat": "-6.9024981", "lon": "107.6187750", "display_name": "Gedung Sate, Bandung, Indonesia",
         "boundingbox": ["-6.9029855", "-6.9023070", "107.6182706", "107.6192795"], "type": "government"},
        {"lat": "bad", "lon": "107.6"},
        {"lat": "-6.9", "lon": "107.6", "display_name": "Somewhere", "boundingbox": ["a", "b", "c", "d"]},
    ]

    def test_results_are_normalised(self):
        calls = []

        def fetch(method, url, *, params=None, headers=None, timeout=None):
            calls.append((method, url, params, headers))
            return self.ANSWER

        out = search("Gedung Sate", url="https://geo.example/", fetch=fetch, user_agent="daas-test", language="id")
        self.assertEqual(len(out), 2)                       # the row without coordinates is dropped
        first = out[0]
        self.assertEqual((first["lat"], first["lng"]), (-6.9024981, 107.618775))
        # Nominatim gives south, north, west, east; the client wants west, south, east, north.
        self.assertEqual(first["bbox"], [107.6182706, -6.9029855, 107.6192795, -6.902307])
        self.assertEqual((first["source"], first["kind"]), ("geocoder", "government"))
        self.assertIsNone(out[1]["bbox"])
        method, url, params, headers = calls[0]
        self.assertEqual((method, url), ("GET", "https://geo.example/search"))
        self.assertEqual((params["q"], params["format"], params["limit"]), ("Gedung Sate", "jsonv2", 5))
        self.assertEqual((headers["User-Agent"], headers["Accept-Language"]), ("daas-test", "id"))

    def test_empty_query_makes_no_request(self):
        self.assertEqual(search("  ", url="x", fetch=lambda *a, **k: 1 / 0, user_agent="t"), [])

    def test_failures_are_reported(self):
        def down(*a, **k):
            raise RuntimeError("connection refused")

        with self.assertRaises(GeocodeError) as cm:
            search("Bandung", url="https://geo.example", fetch=down, user_agent="t")
        self.assertIn("unavailable", str(cm.exception))
        with self.assertRaises(GeocodeError):
            search("Bandung", url="https://geo.example", fetch=lambda *a, **k: {"error": "nope"}, user_agent="t")
        with self.assertRaises(GeocodeError):
            search("x" * 300, url="https://geo.example", fetch=lambda *a, **k: [], user_agent="t")


def square_zone(cx_m, cy_m, half_m, **props):
    ring = fx.lnglat([(cx_m - half_m, cy_m - half_m), (cx_m + half_m, cy_m - half_m),
                      (cx_m + half_m, cy_m + half_m), (cx_m - half_m, cy_m + half_m)])
    return {"type": "Feature", "properties": props,
            "geometry": {"type": "Polygon", "coordinates": [ring + [ring[0]]]}}


def zone_file(*features, **extra):
    return {"type": "FeatureCollection", "features": list(features), **extra}


class TestZones(unittest.TestCase):
    def test_loading(self):
        zs = load_zones(zone_file(
            square_zone(0, 0, 100, name="Airport CTR", type="CTR", lower_m=0, upper_m="120", message="Call the tower."),
            {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [0, 0]}},
            {"type": "Feature", "properties": {"NAME": "Two parts"}, "geometry": {"type": "MultiPolygon", "coordinates": [
                square_zone(500, 0, 50)["geometry"]["coordinates"], square_zone(900, 0, 50)["geometry"]["coordinates"]]}},
            as_of="2026-09-01"), label="National UAS zones")
        self.assertEqual(len(zs.zones), 2)                       # the point feature is ignored
        z = zs.zones[0]
        self.assertEqual((z.name, z.kind, z.lower_m, z.upper_m, z.message),
                         ("Airport CTR", "CTR", 0.0, 120.0, "Call the tower."))
        self.assertEqual(zs.zones[1].name, "Two parts")
        self.assertEqual(len(zs.zones[1].polygons), 2)
        self.assertEqual(zs.describe(), "National UAS zones (2 zones, dated 2026-09-01)")

    def test_bad_files(self):
        for bad in ({}, {"type": "Feature"}, [], "zones"):
            with self.assertRaises(ZoneFileError):
                load_zones(bad)
        broken = zone_file({"type": "Feature", "properties": {},
                            "geometry": {"type": "Polygon", "coordinates": [[["x", 1], [2, 3], [4, 5]]]}})
        with self.assertRaises(ZoneFileError):
            load_zones(broken)

    def touched(self, zone_feature, **kw):
        zs = load_zones(zone_file(zone_feature))
        boundary = fx.lnglat([(-200, -150), (200, -150), (200, 150), (-200, 150)])
        route = fx.lnglat([(-200, -100), (200, -100), (200, 100), (-200, 100)])
        args = {"boundary_rings": [[tuple(p) for p in boundary]], "route": [tuple(p) for p in route],
                "takeoff": tuple(fx.lnglat([(0, -400)])[0])}
        args.update(kw)
        return [z.name for z in zones_touched(zs, **args)]

    def test_overlap_cases(self):
        self.assertEqual(self.touched(square_zone(150, 0, 100, name="edge")), ["edge"])          # partial overlap
        self.assertEqual(self.touched(square_zone(0, 0, 20, name="inside")), ["inside"])         # zone inside the area
        self.assertEqual(self.touched(square_zone(0, 0, 5000, name="around")), ["around"])       # area inside the zone
        self.assertEqual(self.touched(square_zone(0, -400, 30, name="launch")), ["launch"])      # takeoff point only
        self.assertEqual(self.touched(square_zone(0, -270, 40, name="transit")), ["transit"])    # crossed on the way
        self.assertEqual(self.touched(square_zone(2000, 2000, 100, name="far")), [])
        self.assertEqual(self.touched(square_zone(320, 0, 100, name="near miss")), [])

    def test_empty_inputs(self):
        self.assertEqual(zones_touched(ZoneSet(), boundary_rings=[], route=[], takeoff=None), [])


class TestAirspaceInPlans(unittest.TestCase):
    def plan(self, zones=None, **kw):
        return plan_mission(fx.area_plan(takeoff_m=(0, -170), **kw), fx.drone(), fx.camera(), zones=zones)

    def test_without_a_zone_file_airspace_is_not_checked(self):
        m = self.plan()
        a = fx.assurance(m, "airspace")
        self.assertEqual(a["status"], "not_checked")
        self.assertIn("NOTAM", a["summary"])
        self.assertEqual([c for c in fx.codes(m) if c.startswith("AIRSPACE")], [])
        self.assertEqual(fx.assurance(self.plan(zones=ZoneSet()), "airspace")["status"], "not_checked")

    def test_touching_a_zone_is_an_advisory_warning(self):
        zs = load_zones(zone_file(square_zone(150, 0, 100, name="Heliport", type="aerodrome", upper_m=60,
                                              message="Coordinate with the operator.")), label="Operator zones")
        m = self.plan(zones=zs)
        issue = next(i for i in m["issues"] if i["code"] == "AIRSPACE_ZONE")
        self.assertEqual((issue["severity"], issue["basis"], issue["category"]), ("warning", "advisory", "airspace"))
        self.assertIn('"Heliport" [aerodrome] (up to 60 m)', issue["message"])
        self.assertIn("Coordinate with the operator.", issue["message"])
        self.assertEqual(issue["detail"]["zone"]["name"], "Heliport")
        a = fx.assurance(m, "airspace")
        self.assertEqual(a["status"], "advisory")
        self.assertIn("it touches 1 zone", a["summary"])
        self.assertTrue(m["summary"]["exportable"])           # the pilot decides; it must be acknowledged

    def test_no_hit_is_explicitly_not_a_clearance(self):
        zs = load_zones(zone_file(square_zone(5000, 5000, 100, name="Elsewhere")), label="Operator zones")
        m = self.plan(zones=zs)
        self.assertNotIn("AIRSPACE_ZONE", fx.codes(m))
        a = fx.assurance(m, "airspace")
        self.assertEqual(a["status"], "advisory")
        self.assertIn("no zone in that file is touched", a["summary"])
        self.assertIn("not a clearance", a["summary"])

    def test_many_zones_are_summarised(self):
        zs = load_zones(zone_file(*[square_zone(0, 0, 10 + i, name=f"Z{i}") for i in range(14)]))
        m = self.plan(zones=zs)
        self.assertEqual(fx.codes(m).count("AIRSPACE_ZONE"), 10)
        more = next(i for i in m["issues"] if i["code"] == "AIRSPACE_ZONE_MORE")
        self.assertIn("4 more zones", more["message"])

    def test_corridor_route_is_checked(self):
        zs = load_zones(zone_file(square_zone(100, 0, 30, name="On the road")))
        m = plan_mission(fx.corridor_plan(((-400, 0), (400, 0)), width=60, takeoff_m=(-400, -20)),
                         fx.drone(), fx.camera(), zones=zs)
        self.assertIn("AIRSPACE_ZONE", fx.codes(m, "warning"))


if __name__ == "__main__":
    unittest.main()
