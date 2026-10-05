"""Potree octree reader + elevation profile.

Most tests run over a synthetic octree written by ``potree_fixture.py``
(multi-level LOD, proxy chunks, interleaved records) with a scene whose
ground slope, building height, tree canopy, wire height, noise and data
hole are known, so the profile's numbers can be asserted exactly. One test
runs the reader over an octree built by the real PotreeConverter when the
binary is available (CI does this inside the geospatial image).
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess

import numpy as np
import pytest
from fastapi import HTTPException

from app.routers.pointcloud import ProfileRequest, elevation_profile, profile_limits
from app.utils import objectstore, pointcloud, potree_octree, profile
from tests.potree_fixture import scene, write_octree

OX, OY = 500000.0, 4500000.0


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def octree_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("octree")
    xyz, cls, facts = scene()
    meta = write_octree(str(out), xyz, classification=cls, step_size=2, leaf_capacity=400)
    return {"dir": str(out), "xyz": xyz, "cls": cls, "facts": facts, "meta": meta}


def _open(d):
    return potree_octree.open_octree(os.path.join(d, "metadata.json"), os.path.join(d, "hierarchy.bin"),
                                     os.path.join(d, "octree.bin"))


# ------------------------------------------------------------- reader


def test_fixture_has_several_levels_and_proxy_chunks(octree_dir):
    meta = octree_dir["meta"]
    assert meta["hierarchy"]["depth"] >= 3
    size = os.path.getsize(os.path.join(octree_dir["dir"], "hierarchy.bin"))
    assert size > meta["hierarchy"]["firstChunkSize"], "scene should need more than the root chunk"


def test_reader_sees_every_point_once(octree_dir):
    with _open(octree_dir["dir"]) as tree:
        nodes = tree.select_nodes(lambda bmin, bmax: True)
        assert sum(n.num_points for n in nodes) == len(octree_dir["xyz"]) == tree.points_total
        assert max(n.level for n in nodes) == octree_dir["meta"]["hierarchy"]["depth"]
        rec = tree.read_nodes(nodes)
        x, y, z = tree.positions(rec)
    src = octree_dir["xyz"]
    # Same multiset of coordinates (quantised to the 1 mm scale).
    got = np.sort(np.round(x, 3) * 1e3 + np.round(y - OY, 3) + np.round(z, 3) * 1e-3)
    exp = np.sort(np.round(src[:, 0], 3) * 1e3 + np.round(src[:, 1] - OY, 3) + np.round(src[:, 2], 3) * 1e-3)
    assert np.allclose(got, exp, atol=2e-3)
    assert np.bincount(rec["classification"].ravel(), minlength=256).tolist() == octree_dir["meta"]["attributes"][2]["histogram"]


def test_select_nodes_prunes_by_region(octree_dir):
    with _open(octree_dir["dir"]) as tree:
        every = tree.select_nodes(lambda bmin, bmax: True)
        # A small box in the middle of the scene: far fewer nodes, every chosen node intersects it.
        box = (OX + 20, OY + 20, OX + 22, OY + 22)

        def hit(bmin, bmax):
            return not (bmax[0] < box[0] or bmin[0] > box[2] or bmax[1] < box[1] or bmin[1] > box[3])

        some = tree.select_nodes(hit)
        assert 0 < len(some) < len(every)
        assert all(hit(n.bmin, n.bmax) for n in some)
        # The root is always among them (it intersects everything).
        assert any(n.name == "r" for n in some)


def test_max_level_limits_descent(octree_dir):
    with _open(octree_dir["dir"]) as tree:
        shallow = tree.select_nodes(lambda bmin, bmax: True, max_level=1)
        assert {n.level for n in shallow} <= {0, 1}
        assert sum(n.num_points for n in shallow) < tree.points_total


def test_coalesce_ranges_merges_and_caps():
    merged = potree_octree.coalesce_ranges([(0, 10), (12, 10), (100, 10), (5000, 10)], gap=100, max_request=200)
    assert merged == [(0, 110), (5000, 10)]
    # Never exceeds max_request unless a single range does.
    big = potree_octree.coalesce_ranges([(0, 150), (150, 150)], gap=50, max_request=200)
    assert big == [(0, 150), (150, 150)]
    assert potree_octree.coalesce_ranges([(0, 500)], max_request=10) == [(0, 500)]


def test_record_dtype_rejects_inconsistent_sizes():
    with pytest.raises(potree_octree.OctreeError):
        potree_octree.record_dtype([{"name": "position", "type": "int32", "size": 8, "numElements": 3}])


def test_truncated_octree_is_an_error(octree_dir, tmp_path):
    d = tmp_path / "trunc"
    d.mkdir()
    for f in ("metadata.json", "hierarchy.bin"):
        (d / f).write_bytes(open(os.path.join(octree_dir["dir"], f), "rb").read())
    (d / "octree.bin").write_bytes(open(os.path.join(octree_dir["dir"], "octree.bin"), "rb").read()[:100])
    with _open(str(d)) as tree:
        nodes = tree.select_nodes(lambda bmin, bmax: True)
        with pytest.raises(potree_octree.OctreeError):
            tree.read_nodes(nodes)


# ------------------------------------------------------------ profile


def _profile(octree_dir, line, **kw):
    with _open(octree_dir["dir"]) as tree:
        return profile.compute_profile(tree, line, **kw)


def test_profile_ground_follows_the_slope_and_reports_climb(octree_dir):
    f = octree_dir["facts"]
    # West -> east across open ground at y = 10, avoiding every feature.
    out = _profile(octree_dir, [[OX + 2, OY + 10], [OX + 98, OY + 10]], width=2.0, step=1.0)
    assert out["classification"]["available"] and out["classification"]["ground_method"] == "class2_median"
    assert out["bins"] == 96 and out["step"] == 1.0 and out["stats"]["coverage"] == 1.0
    ground = np.array(out["ground"], dtype=float)
    expected = f["base_z"] + f["slope"] * (2 + np.array(out["distance"]))
    assert np.nanmax(np.abs(ground - expected)) < 0.05
    s = out["stats"]
    assert abs(s["ground_end"] - s["ground_start"] - f["slope"] * 95) < 0.1
    assert abs(s["mean_grade_pct"] - 5.0) < 0.3
    # The steepest 2 m window on a noisy 5 % slope is a little steeper than the mean.
    assert 4.0 < s["max_grade_pct"] < 9.0
    assert 4.3 < s["ascent"] < 5.5 and s["descent"] < 0.5
    # Open ground: nothing above it.
    assert s["height_max"] < 0.3
    assert out["classes"] == []
    assert out["sampling"]["full_resolution"] is True
    assert out["sampling"]["points_in_corridor"] == s["points"] > 300


def test_profile_building_is_a_layer_with_interpolated_ground(octree_dir):
    f = octree_dir["facts"]
    x0, x1, y0, y1, h = f["building"]
    out = _profile(octree_dir, [[OX + 20, OY + 50], [OX + 60, OY + 50]], width=2.0, step=1.0)
    codes = [c["code"] for c in out["classes"]]
    assert codes == [6]
    building = out["classes"][0]
    d = np.array(out["distance"])
    inside = (d > x0 - 20 + 0.5) & (d < x1 - 20 - 0.5)
    # Height above ground of the roof ~ 8 m; the ground under it is interpolated, not invented.
    heights = np.array(building["height"], dtype=float)
    assert np.nanmin(heights[inside]) > h - 0.3 and np.nanmax(heights[inside]) < h + 0.3
    ground = np.array(out["ground"], dtype=float)
    fill = np.array(out["ground_fill"], dtype=float)
    assert np.all(np.isnan(ground[inside])), "no measured ground under the roof"
    assert np.all(np.isfinite(fill[inside])), "ground bridged across the building"
    outside = (d < x0 - 20) | (d > x1 - 20)
    assert np.all(np.isfinite(ground[outside])), "measured ground on both sides"
    assert not np.any(np.isfinite(fill) & np.isfinite(ground)), "fill only where there is no measurement"
    assert abs(out["stats"]["height_max"] - h) < 0.3
    assert abs(building["max_height"] - h) < 0.3
    # Total height series agrees (top is the roof).
    assert abs(np.nanmax(np.array(out["height"], dtype=float)[inside]) - h) < 0.3


def test_profile_wire_and_trees_are_separate_layers_and_noise_is_excluded(octree_dir):
    f = octree_dir["facts"]
    # South -> north at x = 75: trees around y 17..23, wire at y = 70.
    out = _profile(octree_dir, [[OX + 75, OY + 0], [OX + 75, OY + 100]], width=2.0, step=1.0)
    by_code = {c["code"]: c for c in out["classes"]}
    assert set(by_code) == {5, 14}
    assert 7 not in by_code
    # Noise (30 m up) never reaches the top line.
    assert np.nanmax(np.array(out["top"], dtype=float)) < f["noise_z"] - 5
    trees = by_code[5]
    d = np.array(out["distance"])
    in_trees = (d > 17.5) & (d < 22.5)
    th = np.array(trees["height"], dtype=float)
    assert np.nanmax(th[in_trees]) > 9.0 and np.nanmax(th[in_trees]) <= 10.2
    tmin = np.array(trees["min"], dtype=float) - np.array(out["ground"], dtype=float)
    assert np.nanmin(tmin[in_trees]) > 3.5  # canopy bottom ~4 m
    wire = by_code[14]
    assert abs(wire["max_height"] - f["wire"][1]) < 0.2
    assert wire["bins"] <= 3  # a wire crossing the transect occupies a bin or two
    assert out["classification"]["noise_excluded"] == [7, 18]


def test_profile_reports_gaps_rather_than_values(octree_dir):
    f = octree_dir["facts"]
    hx0, hx1, hy0, hy1 = f["hole"]
    out = _profile(octree_dir, [[OX + 0, OY + 80], [OX + 30, OY + 80]], width=1.0, step=1.0)
    d = np.array(out["distance"])
    in_hole = (d > hx0 + 0.5) & (d < hx1 - 0.5)
    assert all(v == 0 for v in np.array(out["count"])[in_hole])
    assert all(v is None for v in np.array(out["ground"], dtype=object)[in_hole])
    assert all(v is None for v in np.array(out["top"], dtype=object)[in_hole])
    assert out["gaps"] and out["gaps"][0][0] <= hx0 + 1 and out["gaps"][0][1] >= hx1 - 1
    assert out["stats"]["coverage"] < 1.0
    # Ground is bridged across the small hole for the height series, flagged as fill.
    assert all(v is not None for v in np.array(out["ground_fill"], dtype=object)[in_hole])


@pytest.fixture(scope="module")
def unclassified_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("unclassified")
    xyz, cls, facts = scene()
    write_octree(str(out), xyz, classification=np.zeros(len(xyz), dtype=np.uint8), leaf_capacity=400, step_size=2)
    return {"dir": str(out), "facts": facts}


def test_profile_without_classification_uses_the_low_envelope(unclassified_dir):
    f = unclassified_dir["facts"]
    out = _profile(unclassified_dir, [[OX + 2, OY + 10], [OX + 98, OY + 10]], width=2.0, step=1.0)
    assert out["classification"]["available"] is False
    assert out["classification"]["ground_method"] == "low_percentile_filtered"
    assert out["classification"]["ground_window"] == profile.DEFAULT_GROUND_WINDOW
    assert out["classes"] == []
    ground = np.array(out["ground"], dtype=float)
    expected = f["base_z"] + f["slope"] * (2 + np.array(out["distance"]))
    assert np.nanmax(np.abs(ground - expected)) < 0.1
    # A plain 5 % slope is terrain: the filter removes nothing.
    assert np.isfinite(ground).all()
    assert abs(out["stats"]["mean_grade_pct"] - 5.0) < 0.3


def test_profile_without_classification_filters_a_building_out_of_the_ground(unclassified_dir):
    f = unclassified_dir["facts"]
    x0, x1, y0, y1, h = f["building"]
    out = _profile(unclassified_dir, [[OX + 20, OY + 50], [OX + 60, OY + 50]], width=2.0, step=1.0)
    d = np.array(out["distance"])
    ground = np.array(out["ground"], dtype=float)
    fill = np.array(out["ground_fill"], dtype=float)
    height = np.array(out["height"], dtype=float)
    roof = (d > x0 - 20 + 1.5) & (d < x1 - 20 - 1.5)
    # The roof is recognised as an object: no measured ground there, interpolated ground instead...
    assert np.isnan(ground[roof]).all()
    assert np.isfinite(fill[roof]).all()
    expected = f["base_z"] + f["slope"] * (20 + d)
    assert np.nanmax(np.abs(fill[roof] - expected[roof])) < 0.3
    # ...so the height above ground is the building's, as with a classified cloud.
    assert np.nanmin(height[roof]) > h - 0.4 and np.nanmax(height[roof]) < h + 0.4
    assert abs(out["stats"]["height_max"] - h) < 0.4
    # And the steepest grade is the slope, not the wall.
    assert out["stats"]["max_grade_pct"] < 12
    # Without the filter the low envelope is the roof itself.
    raw = _profile(unclassified_dir, [[OX + 20, OY + 50], [OX + 60, OY + 50]], width=2.0, step=1.0, ground_window=0)
    assert raw["classification"]["ground_method"] == "low_percentile"
    assert np.nanmax(np.array(raw["ground"], dtype=float)[roof] - expected[roof]) > h - 0.5
    assert raw["stats"]["max_grade_pct"] > 100


def test_profile_polyline_distance_is_cumulative_and_points_counted_once(octree_dir):
    # An L-shaped transect; the corner is at 50 m.
    out = _profile(octree_dir, [[OX + 2, OY + 10], [OX + 52, OY + 10], [OX + 52, OY + 60]], width=1.0, step=1.0)
    assert out["length"] == 100.0
    assert [v["distance"] for v in out["vertices"]] == [0.0, 50.0, 100.0]
    assert out["stats"]["segments"] == 2
    # Bin centres follow the polyline in the CRS.
    i = int(np.searchsorted(out["distance"], 75.0))
    assert abs(out["x"][i] - (OX + 52)) < 1e-6 and abs(out["y"][i] - (OY + 10 + 25.5)) < 1e-6
    # Every point of the corridor is counted once (corner included).
    with _open(octree_dir["dir"]) as tree:
        every = tree.read_nodes(tree.select_nodes(lambda a, b: True))
        x, y, _z = tree.positions(every)
    seg1 = (x >= OX + 2) & (x <= OX + 52) & (np.abs(y - (OY + 10)) <= 0.5)
    seg2 = (y >= OY + 10) & (y <= OY + 60) & (np.abs(x - (OX + 52)) <= 0.5)
    assert out["stats"]["points"] == int((seg1 | seg2).sum())


def test_profile_budget_drops_deepest_levels_and_widens_step(octree_dir):
    full = _profile(octree_dir, [[OX + 2, OY + 10], [OX + 98, OY + 10]], width=1.0, step=0.5)
    budget = full["sampling"]["points_available"] // 2
    coarse = _profile(octree_dir, [[OX + 2, OY + 10], [OX + 98, OY + 10]], width=1.0, step=0.5, point_budget=budget)
    assert full["sampling"]["full_resolution"] is True
    assert coarse["sampling"]["full_resolution"] is False
    assert coarse["sampling"]["max_level"] < coarse["sampling"]["deepest_level"]
    assert 0 < coarse["sampling"]["points_read"] <= budget
    assert coarse["sampling"]["point_budget"] == budget
    # The step is never finer than the sampled spacing.
    assert coarse["step"] >= coarse["sampling"]["spacing"]
    assert coarse["step"] >= full["step"]
    # Still the right slope, just coarser.
    g = np.array(coarse["ground"], dtype=float)
    expected = 100 + 0.05 * (2 + np.array(coarse["distance"]))
    assert np.nanmax(np.abs(g - expected)) < 0.6


def test_profile_step_auto_and_bins_cap(octree_dir):
    auto = _profile(octree_dir, [[OX + 2, OY + 10], [OX + 98, OY + 10]], width=1.0)
    assert auto["step"] == 0.2 or auto["step"] >= auto["sampling"]["spacing"]
    assert auto["bins"] <= profile.TARGET_BINS + 1
    capped = _profile(octree_dir, [[OX + 2, OY + 10], [OX + 98, OY + 10]], width=1.0, step=0.1, bins_cap=50)
    assert capped["bins"] <= 50
    assert capped["sampling"]["step_requested"] == 0.1


def test_profile_rejects_bad_input(octree_dir):
    with _open(octree_dir["dir"]) as tree:
        with pytest.raises(profile.ProfileError):
            profile.compute_profile(tree, [[OX, OY]], width=1.0)
        with pytest.raises(profile.ProfileError):
            profile.compute_profile(tree, [[OX, OY], [OX, OY]], width=1.0)
        with pytest.raises(profile.ProfileError):
            profile.compute_profile(tree, [[OX, OY], [OX + 1, OY]], width=0.0)
        with pytest.raises(profile.ProfileError):
            profile.compute_profile(tree, [[OX, OY], [OX + 1, OY]], width=1.0, step=0.0001)
        with pytest.raises(profile.ProfileError):
            profile.compute_profile(tree, [[OX + i, OY] for i in range(60)], width=1.0)
        with pytest.raises(profile.ProfileError):
            profile.compute_profile(tree, [[float("nan"), OY], [OX, OY]], width=1.0)


def test_profile_outside_the_cloud_is_all_gaps(octree_dir):
    out = _profile(octree_dir, [[OX + 1000, OY + 1000], [OX + 1050, OY + 1000]], width=1.0, step=1.0)
    assert out["stats"]["points"] == 0 and out["stats"]["coverage"] == 0.0
    assert all(v is None for v in out["ground"]) and all(v is None for v in out["top"])
    assert out["gaps"] == [[0.0, 50.0]]
    assert out["stats"]["ascent"] is None and out["stats"]["height_max"] is None


# ------------------------------------------------------------ helpers


def test_group_stats_percentiles_and_empty_groups():
    keys = np.array([0, 0, 0, 2, 2])
    z = np.array([3.0, 1.0, 2.0, 10.0, 20.0])
    s = profile.group_stats(keys, z, 3, percentiles=(5, 50))
    assert s["count"].tolist() == [3, 0, 2]
    assert s["min"][0] == 1.0 and s["max"][0] == 3.0 and s["p50"][0] == 2.0 and s["p05"][0] == 1.0
    assert np.isnan(s["min"][1]) and np.isnan(s["p50"][1])
    assert s["p50"][2] == 10.0 and s["mean"][2] == 15.0
    empty = profile.group_stats(np.array([], dtype=int), np.array([]), 2, percentiles=(50,))
    assert empty["count"].tolist() == [0, 0] and np.isnan(empty["p50"]).all()


def test_interpolate_gaps_respects_max_gap_and_edges():
    v = np.array([1.0, np.nan, np.nan, 4.0, np.nan, np.nan, np.nan, np.nan, 9.0, np.nan])
    f = profile.interpolate_gaps(v, step=1.0, max_gap=2.0)
    assert np.allclose(f[1:3], [2.0, 3.0])
    assert np.isnan(f[4:8]).all(), "a 4 m gap is beyond the 2 m limit"
    assert np.isnan(f[9]), "trailing gap is never extrapolated"
    assert np.isnan(f[0]) and np.isnan(f[3]), "measured bins are not duplicated into the fill"


def test_climb_stats_ignores_gaps_and_noise():
    g = np.array([10.0, 11.0, 12.0, 13.0, 14.0, np.nan, 20.0, 19.0, 18.0])
    s = profile.climb_stats(g, step=1.0)
    # The 6 m jump across the gap is not counted; the running median trims the ends.
    assert s["ascent"] == pytest.approx(3.0)
    assert s["descent"] == pytest.approx(1.0)
    # The steepest 2 m window never spans the gap (which would read as 300 %).
    assert 50 < s["max_grade_pct"] <= 100
    assert s["length_3d"] > 8.0
    # Noise of +-5 cm on flat ground adds no climbing.
    rng = np.random.default_rng(0)
    flat = 50 + rng.normal(0, 0.05, 400)
    asc, desc = profile.climb_totals(flat)
    assert asc < 0.5 and desc < 0.5
    # A real 10 m climb is counted in full (to within the hysteresis).
    asc, desc = profile.climb_totals(np.linspace(0, 10, 100))
    assert 9.7 < asc <= 10.0 and desc == 0.0


def test_filter_ground_removes_narrow_tall_objects_only():
    step = 1.0
    x = np.arange(200.0)
    terrain = 100 + 0.05 * x
    low = terrain.copy()
    low[50:60] += 8.0  # a 10 m building
    low[120:160] += 6.0  # a 40 m-wide plateau: wider than the window, kept
    low[90] = np.nan  # a gap
    flagged = profile.filter_ground(low, step, max_window=20.0)
    assert flagged[50:60].all()
    assert not flagged[:50].any() and not flagged[60:90].any()
    assert not flagged[120:160].any()
    assert not flagged[90]  # a gap is not an object
    # Disabled filter flags nothing.
    assert not profile.filter_ground(low, step, max_window=0).any()
    # A building with sloping edges (eaves / a canopy edge): the fringe bins are objects too.
    ramp = terrain.copy()
    ramp[48:52] += [1.0, 3.0, 5.0, 7.0]
    ramp[52:60] += 8.0
    ramp[60:63] += [6.0, 3.0, 1.0]
    flagged = profile.filter_ground(ramp, step, max_window=20.0)
    assert flagged[48:63].all()
    assert not flagged[:48].any() and not flagged[63:].any()


def test_nice_step():
    assert profile.nice_step(0.37) == 0.5
    assert profile.nice_step(1.0) == 1
    assert profile.nice_step(3.1) == 5
    assert profile.nice_step(12) == 20


# ------------------------------------------------------------- router


def _req(d, **kw):
    base = {"metadata": os.path.join(d, "metadata.json"), "hierarchy": os.path.join(d, "hierarchy.bin"),
            "octree": os.path.join(d, "octree.bin"), "line": [[OX + 2, OY + 10], [OX + 50, OY + 10]], "width": 1.0}
    base.update(kw)
    return ProfileRequest(**base)


def test_router_returns_profile_and_limits(octree_dir):
    out = _run(elevation_profile(_req(octree_dir["dir"], step=2.0)))
    assert out["bins"] == 24 and out["stats"]["points"] > 0
    limits = _run(profile_limits())
    assert limits["max_points"] >= 10_000 and limits["max_vertices"] == profile.MAX_VERTICES


def test_router_rejects_relative_and_missing_paths(octree_dir, tmp_path):
    with pytest.raises(HTTPException) as e:
        _run(elevation_profile(_req(octree_dir["dir"], octree="relative/octree.bin")))
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        _run(elevation_profile(_req(octree_dir["dir"], octree=str(tmp_path / "missing.bin"))))
    assert e.value.status_code == 404


def test_router_maps_bad_transect_to_422(octree_dir):
    with pytest.raises(HTTPException) as e:
        _run(elevation_profile(_req(octree_dir["dir"], line=[[OX, OY]])))
    assert e.value.status_code == 422
    with pytest.raises(HTTPException) as e:
        _run(elevation_profile(_req(octree_dir["dir"], width=500)))
    assert e.value.status_code == 422


def test_router_maps_broken_octree_to_422(octree_dir, tmp_path):
    bad = tmp_path / "metadata.json"
    bad.write_text("{not json")
    with pytest.raises(HTTPException) as e:
        _run(elevation_profile(_req(octree_dir["dir"], metadata=str(bad))))
    assert e.value.status_code == 422


# --------------------------------------------------- real PotreeConverter

needs_converter = pytest.mark.skipif(not pointcloud.converter_available(), reason="PotreeConverter not installed")


@needs_converter
def test_reader_matches_a_potreeconverter_octree(tmp_path):
    """The reader over a real converter output: every point once, right coordinates and classes."""
    from tests.test_pointcloud import write_las

    xyz, cls, _facts = scene(size=40.0, density=3.0)
    las = write_las(tmp_path / "scene.las", [tuple(p) for p in xyz], point_format=3, epsg=32632,
                    classification=[int(c) for c in cls], intensity=[0] * len(xyz), rgb=[(0, 0, 0)] * len(xyz))
    out_dir = str(tmp_path / "octree")
    pointcloud.convert(las, out_dir)
    with _open(out_dir) as tree:
        nodes = tree.select_nodes(lambda a, b: True)
        assert sum(n.num_points for n in nodes) == len(xyz)
        rec = tree.read_nodes(nodes)
        x, y, z = tree.positions(rec)
        assert np.allclose(sorted(np.round(z, 2)), sorted(np.round(xyz[:, 2], 2)), atol=0.011)
        assert np.allclose(sorted(np.round(x, 2)), sorted(np.round(xyz[:, 0], 2)), atol=0.011)
        got_hist = np.bincount(rec["classification"].ravel(), minlength=256)
        assert got_hist.tolist() == np.bincount(cls, minlength=256).tolist()
        prof = profile.compute_profile(tree, [[OX + 2, OY + 10], [OX + 38, OY + 10]], width=1.0, step=1.0)
    ground = np.array(prof["ground"], dtype=float)
    expected = 100 + 0.05 * (2 + np.array(prof["distance"]))
    assert np.nanmax(np.abs(ground - expected)) < 0.06


# ------------------------------------------------------------- object storage

moto_server = pytest.importorskip("moto.server")
boto3 = pytest.importorskip("boto3")

BUCKET = "webodm-profile-test"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def s3(tmp_path_factory):
    port = _free_port()
    server = moto_server.ThreadedMotoServer(ip_address="127.0.0.1", port=port)
    server.start()
    endpoint = f"http://127.0.0.1:{port}"
    env = {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing", "AWS_REGION": "us-east-1",
           "S3_ENDPOINT_URL": endpoint, "S3_BUCKETS": BUCKET}
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    client = boto3.client("s3", endpoint_url=endpoint, region_name="us-east-1",
                          aws_access_key_id="testing", aws_secret_access_key="testing")
    client.create_bucket(Bucket=BUCKET)
    try:
        yield client
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        server.stop()


def test_profile_reads_the_octree_from_object_storage_by_range(s3, octree_dir):
    for f in ("metadata.json", "hierarchy.bin", "octree.bin"):
        s3.upload_file(os.path.join(octree_dir["dir"], f), BUCKET, f"orgs/o/tasks/t/assets/potree/{f}")
    prefix = f"s3://{BUCKET}/orgs/o/tasks/t/assets/potree"
    # Mixed sources: metadata from the host cache, binaries from S3 (what an evicted cache looks like).
    req = ProfileRequest(metadata=os.path.join(octree_dir["dir"], "metadata.json"),
                         hierarchy=f"{prefix}/hierarchy.bin", octree=f"{prefix}/octree.bin",
                         line=[[OX + 2, OY + 10], [OX + 98, OY + 10]], width=1.0, step=1.0)
    out = _run(elevation_profile(req))
    local = _profile(octree_dir, [[OX + 2, OY + 10], [OX + 98, OY + 10]], width=1.0, step=1.0)
    assert out["ground"] == local["ground"] and out["top"] == local["top"]
    # Hierarchy waves + coalesced point ranges: a handful of GET requests, not one per node.
    assert out["sampling"]["requests"] <= 8 and out["sampling"]["nodes_read"] > 8


def test_profile_missing_object_is_502(s3, octree_dir):
    req = ProfileRequest(metadata=os.path.join(octree_dir["dir"], "metadata.json"),
                         hierarchy=f"s3://{BUCKET}/nope/hierarchy.bin", octree=f"s3://{BUCKET}/nope/octree.bin",
                         line=[[OX + 2, OY + 10], [OX + 98, OY + 10]], width=1.0)
    with pytest.raises(HTTPException) as e:
        _run(elevation_profile(req))
    assert e.value.status_code in (422, 502)


def test_profile_other_bucket_is_400(s3, octree_dir):
    req = ProfileRequest(metadata=os.path.join(octree_dir["dir"], "metadata.json"),
                         hierarchy="s3://someone-else/h.bin", octree="s3://someone-else/o.bin",
                         line=[[OX + 2, OY + 10], [OX + 98, OY + 10]], width=1.0)
    with pytest.raises(HTTPException) as e:
        _run(elevation_profile(req))
    assert e.value.status_code == 400
