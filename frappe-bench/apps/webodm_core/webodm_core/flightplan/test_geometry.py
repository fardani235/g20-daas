"""Local frame and planar geometry: the numbers every flight line is built on."""

import math
import unittest

from webodm_core.flightplan import geometry as g
from webodm_core.flightplan.geo import LocalFrame, bearing_deg, haversine_m, normalize_lng


class TestLocalFrame(unittest.TestCase):
    def test_round_trip_is_exact_to_a_millimetre(self):
        for lat0, lng0 in [(0.0, 0.0), (-6.82, 107.61), (52.37, 4.9), (69.65, 18.96), (-45.03, 168.66)]:
            f = LocalFrame(lat0, lng0)
            for dx, dy in [(0, 0), (1500, -900), (-25000, 18000), (40000, 40000)]:
                lng, lat = f.inverse(dx, dy)
                x, y = f.forward(lng, lat)
                self.assertAlmostEqual(x, dx, delta=1e-3)
                self.assertAlmostEqual(y, dy, delta=1e-3)

    def test_distances_match_the_ellipsoid(self):
        # One degree of latitude centred on 45°N is 111 131.7 m on WGS84.
        f = LocalFrame(45.0, 10.0)
        _, south = f.forward(10.0, 44.5)
        _, north = f.forward(10.0, 45.5)
        self.assertAlmostEqual(north - south, 111131.745, delta=0.05)
        # One degree of longitude is 111 319.49 m at the equator and 55 800.0 m
        # at 60°N; measured over a tenth of a degree, inside the span a
        # mission covers (the projection's scale grows away from its centre).
        eq = LocalFrame(0.0, 0.0)
        west, _ = eq.forward(-0.05, 0.0)
        east, _ = eq.forward(0.05, 0.0)
        self.assertAlmostEqual(east - west, 11131.949, delta=0.01)
        hi = LocalFrame(60.0, 0.0)
        west, _ = hi.forward(-0.05, 60.0)
        east, _ = hi.forward(0.05, 60.0)
        self.assertAlmostEqual(east - west, 5580.0, delta=0.05)

    def test_scale_error_stays_small_across_a_large_site(self):
        # 30 km east of the centre (the largest plan allowed is 60 km across)
        # the projection stretches a line by about 11 parts per million.
        # Checked against an independent great-circle distance, which is
        # itself only good to a few tenths of a percent.
        f = LocalFrame(-6.82, 107.61)
        a = f.inverse(30000.0, 0.0)
        b = f.inverse(31000.0, 0.0)
        self.assertAlmostEqual(haversine_m(a[0], a[1], b[0], b[1]), 1000.0, delta=4.0)

    def test_frame_centres_on_a_site_across_the_antimeridian(self):
        f = LocalFrame.around([(179.99, -16.5), (-179.99, -16.4)])
        self.assertAlmostEqual(abs(f.lng0), 180.0, delta=1e-6)
        x1, _ = f.forward(179.99, -16.45)
        x2, _ = f.forward(-179.99, -16.45)
        self.assertAlmostEqual(x2 - x1, 2 * abs(x1), delta=1e-6)
        self.assertLess(abs(x2 - x1), 2500)  # ~2.1 km, not the long way round

    def test_poles_are_refused(self):
        with self.assertRaises(ValueError):
            LocalFrame(89.5, 0.0)

    def test_helpers(self):
        self.assertEqual(normalize_lng(190.0), -170.0)
        self.assertAlmostEqual(bearing_deg(1, 0), 90.0)
        self.assertAlmostEqual(bearing_deg(0, -1), 180.0)
        self.assertAlmostEqual(haversine_m(0, 0, 0, 1), 111195.0, delta=5)


class TestRings(unittest.TestCase):
    SQUARE = [(0, 0), (10, 0), (10, 10), (0, 10)]
    # An L: 10 x 10 with the top-right 5 x 5 removed.
    ELL = [(0, 0), (10, 0), (10, 5), (5, 5), (5, 10), (0, 10)]

    def test_area_orientation_and_centroid(self):
        self.assertEqual(g.signed_area(self.SQUARE), 100.0)
        self.assertEqual(g.signed_area(list(reversed(self.SQUARE))), -100.0)
        self.assertEqual(g.polygon_area([self.SQUARE, [(2, 2), (4, 2), (4, 4), (2, 4)]]), 96.0)
        self.assertEqual(g.polygon_area([self.ELL]), 75.0)
        self.assertEqual(g.centroid(self.SQUARE), (5.0, 5.0))

    def test_open_ring_drops_the_closing_point_and_duplicates(self):
        ring = g.open_ring([(0, 0), (0, 0), (10, 0), (10, 10), (0, 0)])
        self.assertEqual(ring, [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)])

    def test_self_intersection(self):
        bow_tie = [(0, 0), (10, 10), (10, 0), (0, 10)]
        self.assertTrue(g.ring_self_intersects(bow_tie))
        self.assertFalse(g.ring_self_intersects(self.SQUARE))
        self.assertFalse(g.ring_self_intersects(self.ELL))
        self.assertTrue(g.polyline_self_intersects([(0, 0), (10, 0), (10, 10), (5, -5)]))
        self.assertFalse(g.polyline_self_intersects([(0, 0), (10, 0), (10, 10), (0, 10)]))

    def test_point_in_rings_respects_holes(self):
        hole = [(2, 2), (4, 2), (4, 4), (2, 4)]
        self.assertTrue(g.point_in_rings((1, 1), [self.SQUARE, hole]))
        self.assertFalse(g.point_in_rings((3, 3), [self.SQUARE, hole]))
        self.assertFalse(g.point_in_rings((11, 5), [self.SQUARE, hole]))
        self.assertFalse(g.point_in_rings((7, 7), [self.ELL]))

    def test_scanline_gives_one_interval_per_crossing_of_the_inside(self):
        self.assertEqual(g.scanline_segments([self.SQUARE], 5.0), [(0.0, 10.0)])
        self.assertEqual(g.scanline_segments([self.ELL], 7.0), [(0.0, 5.0)])
        # A U shape: two prongs with a gap between them.
        u = [(0, 0), (30, 0), (30, 20), (20, 20), (20, 5), (10, 5), (10, 20), (0, 20)]
        self.assertEqual(g.scanline_segments([u], 10.0), [(0.0, 10.0), (20.0, 30.0)])
        self.assertEqual(g.scanline_segments([u], 2.0), [(0.0, 30.0)])
        # A hole cuts the line in two.
        hole = [(4, 4), (6, 4), (6, 6), (4, 6)]
        self.assertEqual(g.scanline_segments([self.SQUARE, hole], 5.0), [(0.0, 4.0), (6.0, 10.0)])
        # Outside, and exactly on the top edge (half-open), nothing.
        self.assertEqual(g.scanline_segments([self.SQUARE], 11.0), [])
        self.assertEqual(g.scanline_segments([self.SQUARE], 10.0), [])

    def test_scanline_through_a_vertex_counts_it_once(self):
        diamond = [(0, 5), (5, 0), (10, 5), (5, 10)]
        self.assertEqual(g.scanline_segments([diamond], 5.0), [(0.0, 10.0)])


class TestSweepDirection(unittest.TestCase):
    def test_long_rectangle_is_swept_along_its_length(self):
        rect = [(0, 0), (500, 0), (500, 100), (0, 100)]
        angle, width = g.min_width_direction(rect)
        self.assertAlmostEqual(width, 100.0)
        self.assertAlmostEqual(math.sin(angle), 0.0, places=9)   # along x

    def test_rotated_rectangle(self):
        a = math.radians(30)
        rect = g.rotate([(0, 0), (500, 0), (500, 100), (0, 100)], a)
        angle, width = g.min_width_direction(rect)
        self.assertAlmostEqual(width, 100.0, places=6)
        self.assertAlmostEqual(angle, a, places=9)

    def test_concave_shape_uses_its_hull(self):
        ell = [(0, 0), (300, 0), (300, 50), (50, 50), (50, 200), (0, 200)]
        _angle, width = g.min_width_direction(ell)
        self.assertLess(width, 200.0 + 1e-6)

    def test_convex_hull(self):
        pts = [(0, 0), (10, 0), (10, 10), (0, 10), (5, 5), (5, 0)]
        self.assertEqual(sorted(g.convex_hull(pts)), [(0, 0), (0, 10), (10, 0), (10, 10)])


class TestOffsetPolyline(unittest.TestCase):
    def test_straight_line_shifts_left(self):
        self.assertEqual(g.offset_polyline([(0, 0), (100, 0)], 10), [(0.0, 10.0), (100.0, 10.0)])
        self.assertEqual(g.offset_polyline([(0, 0), (100, 0)], -10), [(0.0, -10.0), (100.0, -10.0)])

    def test_right_angle_is_mitred(self):
        out = g.offset_polyline([(0, 0), (100, 0), (100, 100)], 10)
        self.assertEqual(len(out), 3)
        self.assertAlmostEqual(out[1][0], 90.0)
        self.assertAlmostEqual(out[1][1], 10.0)
        outer = g.offset_polyline([(0, 0), (100, 0), (100, 100)], -10)
        self.assertAlmostEqual(outer[1][0], 110.0)
        self.assertAlmostEqual(outer[1][1], -10.0)

    def test_every_offset_point_keeps_its_distance(self):
        centre = [(0, 0), (200, 0), (350, 120), (600, 100)]
        for off in (25.0, -25.0):
            for p in g.offset_polyline(centre, off):
                d = min(_point_segment(p, centre[i], centre[i + 1]) for i in range(len(centre) - 1))
                self.assertGreaterEqual(d, abs(off) - 1e-6)

    def test_hairpin_is_bevelled_not_spiked(self):
        out = g.offset_polyline([(0, 0), (100, 0), (0, 5)], -10)
        self.assertLess(max(p[0] for p in out), 100 + 10 * 2.0 + 1e-6)

    def test_inside_of_a_tight_bend_never_runs_backwards(self):
        centre = [(0, 0), (50, 0), (60, 10), (60, 60)]
        out = g.offset_polyline(centre, 40)   # offset larger than the bend
        for a, b in zip(out, out[1:]):
            # Each leg advances along x or y like the centre line does.
            self.assertGreaterEqual((b[0] - a[0]) + (b[1] - a[1]), -1e-6)

    def test_zero_offset_and_degenerate_input(self):
        self.assertEqual(g.offset_polyline([(0, 0), (5, 5)], 0), [(0.0, 0.0), (5.0, 5.0)])
        self.assertEqual(g.offset_polyline([(1, 1)], 3), [(1.0, 1.0)])


def _point_segment(p, a, b):
    ax, ay, bx, by = a[0], a[1], b[0], b[1]
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


class TestProfiles(unittest.TestCase):
    def test_resample_includes_every_vertex_and_both_ends(self):
        out = g.resample_polyline([(0, 0), (100, 0), (100, 35)], 30)
        pts = [p for p, _d in out]
        self.assertIn((0.0, 0.0), [(round(x, 6), round(y, 6)) for x, y in pts])
        self.assertIn((100.0, 0.0), [(round(x, 6), round(y, 6)) for x, y in pts])
        self.assertEqual(pts[-1], (100, 35))
        self.assertAlmostEqual(out[-1][1], 135.0)
        gaps = [b[1] - a[1] for a, b in zip(out, out[1:])]
        self.assertLessEqual(max(gaps), 30.0 + 1e-9)

    def test_simplify_keeps_ends_and_respects_the_tolerance(self):
        samples = [(d, 100 + 20 * math.sin(d / 80.0)) for d in range(0, 1001, 10)]
        keep = g.simplify_profile(samples, 2.0)
        self.assertEqual(keep[0], 0)
        self.assertEqual(keep[-1], len(samples) - 1)
        self.assertLess(len(keep), len(samples) / 2)
        # Linear interpolation between kept points stays within the tolerance everywhere.
        for i, (d, h) in enumerate(samples):
            lo = max(k for k in keep if k <= i)
            hi = min(k for k in keep if k >= i)
            if lo == hi:
                continue
            d0, h0 = samples[lo]
            d1, h1 = samples[hi]
            self.assertLessEqual(abs(h - (h0 + (h1 - h0) * (d - d0) / (d1 - d0))), 2.0 + 1e-9)

    def test_flat_profile_collapses_to_two_points(self):
        self.assertEqual(g.simplify_profile([(d, 50.0) for d in range(0, 500, 25)], 1.0), [0, 19])


if __name__ == "__main__":
    unittest.main()
