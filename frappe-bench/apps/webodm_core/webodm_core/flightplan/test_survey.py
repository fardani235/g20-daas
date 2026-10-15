"""Flight-line layout for areas and corridors."""

import math
import unittest

from webodm_core.flightplan import geometry as g
from webodm_core.flightplan.survey import (LayoutError, area_layout, corridor_layout, corridor_passes,
                                           optimal_direction_deg)

RECT = [(0, 0), (400, 0), (400, 300), (0, 300)]


def _dist_to_legs(p, legs):
    best = float("inf")
    for leg in legs:
        for a, b in zip(leg.points, leg.points[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)))
            best = min(best, math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy)))
    return best


class TestAreaLayout(unittest.TestCase):
    def test_rectangle_gets_evenly_spaced_lines_along_its_long_side(self):
        layout = area_layout([RECT], 45.0)
        self.assertEqual(layout.direction_deg, 90.0)          # east-west lines
        self.assertEqual(layout.line_count, 7)                # ceil(300 / 45)
        ys = [leg.points[0][1] for leg in layout.legs]
        gaps = [abs(b - a) for a, b in zip(ys, ys[1:])]
        self.assertTrue(all(abs(gap - 45.0) < 1e-6 for gap in gaps))
        # Centred: the same margin to both edges.
        self.assertAlmostEqual(min(ys), 300 - max(ys), places=6)
        for leg in layout.legs:
            self.assertAlmostEqual(leg.length, 400.0, places=6)

    def test_lines_alternate_direction(self):
        layout = area_layout([RECT], 45.0)
        signs = [leg.points[1][0] - leg.points[0][0] > 0 for leg in layout.legs]
        self.assertEqual(signs, [True, False, True, False, True, False, True])
        # Each line starts at the end of the boundary where the previous one finished.
        for a, b in zip(layout.legs, layout.legs[1:]):
            self.assertAlmostEqual(a.points[-1][0], b.points[0][0], places=6)

    def test_whole_area_is_within_half_a_swath_of_a_line(self):
        # With 70 % side overlap the swath is spacing / 0.3 wide; even the
        # edges must fall inside the swath of the outermost line.
        spacing = 45.0
        swath = spacing / 0.3
        for ring in (RECT, g.rotate(RECT, math.radians(27)),
                     [(0, 0), (500, 60), (620, 400), (300, 520), (-40, 260)]):
            layout = area_layout([ring], spacing)
            xmin, ymin, xmax, ymax = g.bbox(ring)
            for i in range(21):
                for j in range(21):
                    p = (xmin + (xmax - xmin) * i / 20, ymin + (ymax - ymin) * j / 20)
                    if g.point_in_rings(p, [ring]):
                        self.assertLessEqual(_dist_to_legs(p, layout.legs), swath / 2 + 1e-6)

    def test_explicit_direction_is_a_compass_bearing(self):
        north_south = area_layout([RECT], 45.0, direction_deg=0.0)
        self.assertEqual(north_south.line_count, 9)           # ceil(400 / 45)
        for leg in north_south.legs:
            self.assertAlmostEqual(leg.points[0][0], leg.points[1][0], places=6)
            self.assertAlmostEqual(leg.length, 300.0, places=6)
        diagonal = area_layout([RECT], 45.0, direction_deg=45.0)
        dx = diagonal.legs[0].points[1][0] - diagonal.legs[0].points[0][0]
        dy = diagonal.legs[0].points[1][1] - diagonal.legs[0].points[0][1]
        self.assertAlmostEqual(abs(dx), abs(dy), places=6)
        self.assertGreater(dx * dy, 0)                        # north-east / south-west
        # 225° is the same line direction as 45°.
        self.assertEqual(area_layout([RECT], 45.0, direction_deg=225.0).direction_deg, 45.0)

    def test_auto_direction_needs_no_more_lines_than_any_other(self):
        ring = g.rotate([(0, 0), (900, 0), (900, 200), (0, 200)], math.radians(63))
        best = area_layout([ring], 40.0).line_count
        self.assertAlmostEqual(optimal_direction_deg(ring), 27.0, places=6)
        for bearing in range(0, 180, 15):
            self.assertLessEqual(best, area_layout([ring], 40.0, direction_deg=bearing).line_count)

    def test_concave_boundary_splits_lines_and_stays_inside(self):
        # A U open to the north: lines across the prongs come in two pieces.
        u = [(0, 0), (600, 0), (600, 400), (400, 400), (400, 100), (200, 100), (200, 400), (0, 400)]
        layout = area_layout([u], 50.0, direction_deg=90.0)
        self.assertGreater(len(layout.legs), layout.line_count)
        for leg in layout.legs:
            mid = ((leg.points[0][0] + leg.points[1][0]) / 2, (leg.points[0][1] + leg.points[1][1]) / 2)
            self.assertTrue(g.point_in_rings(mid, [u]))
        split = [leg for leg in layout.legs if leg.points[0][1] > 100]
        self.assertTrue(all(abs(leg.length - 200.0) < 1e-6 for leg in split))
        # Pieces of one line are flown in the direction of travel, without doubling back.
        by_line = {}
        for leg in layout.legs:
            by_line.setdefault(leg.line, []).append(leg)
        for legs in by_line.values():
            if len(legs) == 2:
                d1 = legs[0].points[1][0] - legs[0].points[0][0]
                d2 = legs[1].points[0][0] - legs[0].points[1][0]
                d3 = legs[1].points[1][0] - legs[1].points[0][0]
                self.assertTrue(d1 * d2 > 0 and d2 * d3 > 0)

    def test_short_gaps_are_bridged(self):
        u = [(0, 0), (600, 0), (600, 400), (400, 400), (400, 100), (200, 100), (200, 400), (0, 400)]
        bridged = area_layout([u], 50.0, direction_deg=90.0, merge_gap=250.0)
        self.assertEqual(len(bridged.legs), bridged.line_count)

    def test_hole_is_not_flown_over(self):
        hole = [(150, 100), (250, 100), (250, 200), (150, 200)]
        layout = area_layout([RECT, hole], 45.0, direction_deg=90.0)
        self.assertGreater(len(layout.legs), layout.line_count)
        for leg in layout.legs:
            mid = ((leg.points[0][0] + leg.points[1][0]) / 2, leg.points[0][1])
            self.assertFalse(g.point_in_rings(mid, [hole]))

    def test_overshoot_extends_both_ends(self):
        layout = area_layout([RECT], 45.0, overshoot=30.0)
        for leg in layout.legs:
            self.assertAlmostEqual(leg.length, 460.0, places=6)
            self.assertAlmostEqual(min(leg.points[0][0], leg.points[1][0]), -30.0, places=6)

    def test_start_corner_follows_the_takeoff_point(self):
        corners = {
            (-50, -50): (0, True), (450, -50): (400, True), (-50, 350): (0, False), (450, 350): (400, False),
        }
        for near, (x, low) in corners.items():
            first = area_layout([RECT], 45.0, near=near).legs[0].points[0]
            self.assertAlmostEqual(first[0], x, places=6)
            self.assertEqual(first[1] < 150, low)
        # An explicit corner wins over the takeoff point.
        fixed = area_layout([RECT], 45.0, near=(450, 350), start="0").legs[0].points[0]
        self.assertAlmostEqual(fixed[0], 0.0, places=6)
        self.assertLess(fixed[1], 150)

    def test_crosshatch_adds_a_perpendicular_grid(self):
        single = area_layout([RECT], 45.0)
        double = area_layout([RECT], 45.0, crosshatch=True)
        self.assertEqual(double.line_count, 7 + 9)
        self.assertAlmostEqual(double.photo_length, single.photo_length + 9 * 300.0, places=4)
        # The second grid starts at the corner nearest to where the first ended.
        end = double.legs[6].points[-1]
        start = double.legs[7].points[0]
        self.assertLess(g.dist(end, start), 60.0)

    def test_tiny_area_still_gets_one_line(self):
        layout = area_layout([[(0, 0), (20, 0), (20, 10), (0, 10)]], 45.0)
        self.assertEqual(layout.line_count, 1)
        self.assertAlmostEqual(layout.legs[0].points[0][1], 5.0)

    def test_bad_input(self):
        with self.assertRaises(LayoutError):
            area_layout([RECT], 0.0)
        with self.assertRaises(LayoutError):
            area_layout([[(0, 0), (1, 1)]], 10.0)


class TestCorridorLayout(unittest.TestCase):
    LINE = [(0, 0), (500, 0), (900, 300)]

    def test_pass_count(self):
        # Swath 150 m, lines 45 m apart.
        self.assertEqual(corridor_passes(100, 150, 45), 1)
        self.assertEqual(corridor_passes(150, 150, 45), 1)
        self.assertEqual(corridor_passes(151, 150, 45), 2)
        self.assertEqual(corridor_passes(240, 150, 45), 3)
        self.assertEqual(corridor_passes(100, 150, 45, min_passes=2), 2)

    def test_passes_are_parallel_and_centred(self):
        layout = corridor_layout([(0, 0), (1000, 0)], 240, 45, 150)
        self.assertEqual(layout.passes, 3)
        ys = sorted(leg.points[0][1] for leg in layout.legs)
        self.assertEqual([round(y, 6) for y in ys], [-45.0, 0.0, 45.0])
        for leg in layout.legs:
            self.assertAlmostEqual(leg.length, 1000.0)
        # Covered width = span of the passes + one swath >= requested width.
        self.assertGreaterEqual((ys[-1] - ys[0]) + 150, 240)

    def test_passes_alternate_direction(self):
        layout = corridor_layout([(0, 0), (1000, 0)], 240, 45, 150)
        heads = [leg.points[-1][0] > leg.points[0][0] for leg in layout.legs]
        self.assertEqual(heads, [True, False, True])
        for a, b in zip(layout.legs, layout.legs[1:]):
            self.assertAlmostEqual(a.points[-1][0], b.points[0][0])

    def test_passes_follow_the_bends(self):
        layout = corridor_layout(self.LINE, 240, 45, 150)
        for leg in layout.legs:
            self.assertEqual(len(leg.points), 3)
        centre = next(leg for leg in layout.legs if g.dist(leg.points[0], (0, 0)) < 1e-6
                      or g.dist(leg.points[-1], (0, 0)) < 1e-6)
        self.assertAlmostEqual(centre.length, 1000.0, places=6)
        outer = [leg for leg in layout.legs if leg is not centre]
        # The outside of the bend is longer than the centre line, the inside shorter.
        self.assertEqual(sorted(leg.length > 1000 for leg in outer), [False, True])

    def test_single_pass_is_the_centre_line(self):
        layout = corridor_layout(self.LINE, 80, 45, 150)
        self.assertEqual(layout.passes, 1)
        self.assertEqual(layout.legs[0].points, [(0.0, 0.0), (500.0, 0.0), (900.0, 300.0)])

    def test_start_end_follows_the_takeoff_point(self):
        near_end = corridor_layout(self.LINE, 240, 45, 150, near=(950, 320))
        self.assertLess(g.dist(near_end.legs[0].points[0], (900, 300)), 80)
        near_start = corridor_layout(self.LINE, 240, 45, 150, near=(-50, 0))
        self.assertLess(g.dist(near_start.legs[0].points[0], (0, 0)), 80)

    def test_overshoot_lengthens_each_pass(self):
        plain = corridor_layout([(0, 0), (1000, 0)], 100, 45, 150)
        longer = corridor_layout([(0, 0), (1000, 0)], 100, 45, 150, overshoot=60)
        self.assertAlmostEqual(longer.legs[0].length, plain.legs[0].length + 120.0)

    def test_target_polygon_is_the_corridor_width(self):
        layout = corridor_layout([(0, 0), (1000, 0)], 240, 45, 150)
        self.assertAlmostEqual(abs(g.signed_area(layout.target[0])), 240 * 1000.0)

    def test_bad_input(self):
        with self.assertRaises(LayoutError):
            corridor_layout([(0, 0)], 100, 45, 150)
        with self.assertRaises(LayoutError):
            corridor_layout([(0, 0), (10, 0)], 0, 45, 150)


if __name__ == "__main__":
    unittest.main()
