"""Region numbers, uncertainty propagation and the GeoJSON layer."""

import math

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import shape

from cdplugin import detect, regions
from cdplugin.grid import Grid


def _grid(w=100, h=80, res=0.5):
    return Grid(crs=rasterio.crs.CRS.from_epsg(32633), transform=from_origin(500000, 4500000, res, res),
                width=w, height=h, meters_per_unit=1.0)


def _scene():
    grid = _grid()
    classes = np.zeros((grid.height, grid.width), dtype="uint8")
    dz = np.zeros(classes.shape, dtype="float32")
    classes[10:30, 10:50] = detect.FILL          # 800 cells = 200 m², +1.5 m -> 300 m³
    dz[10:30, 10:50] = 1.5
    classes[50:60, 20:40] = detect.CUT           # 200 cells = 50 m², -2 m -> 100 m³
    dz[50:60, 20:40] = -2.0
    classes[40:50, 70:90] = detect.VEGETATION    # 200 cells, +3 m
    dz[40:50, 70:90] = 3.0
    classes[60:70, 60:80] = detect.APPEARANCE    # 200 cells, no height change
    labels, regs = detect.label_regions(classes, {})
    return grid, labels, regs, dz


def test_region_numbers():
    grid, labels, regs, dz = _scene()
    regs = regions.measure_regions(labels, regs, dz, grid.cell_area_m2, 0.03, 0.02, 4.0)
    by = {r["class_name"]: r for r in regs}
    assert by["fill"]["area_m2"] == 200.0 and by["fill"]["volume_m3"] == pytest.approx(300.0)
    assert by["fill"]["mean_change_m"] == pytest.approx(1.5) and by["fill"]["max_change_m"] == pytest.approx(1.5)
    assert by["cut"]["volume_m3"] == pytest.approx(100.0) and by["cut"]["net_volume_m3"] == pytest.approx(-100.0)
    assert by["cut"]["max_change_m"] == pytest.approx(-2.0)
    assert "volume_m3" not in by["appearance"] and by["appearance"]["area_m2"] == 50.0


def test_volume_uncertainty_has_a_random_and_a_systematic_part():
    grid, labels, regs, dz = _scene()
    regs = regions.measure_regions(labels, regs, dz, grid.cell_area_m2, 0.03, 0.02, 4.0)
    fill = next(r for r in regs if r["class_name"] == "fill")
    random = 0.25 * 0.03 * math.sqrt(800 * 4)
    systematic = 200.0 * 0.02
    assert fill["volume_sigma_random_m3"] == pytest.approx(random)
    assert fill["edge_volume_m3"] == 0
    assert fill["volume_sigma_m3"] == pytest.approx(math.hypot(random, systematic))
    # The systematic part scales with area and dominates on any sizeable region.
    assert systematic > 5 * random


def test_change_just_outside_a_region_widens_its_error_bar():
    grid, labels, regs, dz = _scene()
    # A 4-cell (2 m) toe of 0.04 m around the fill, below any level of detection...
    toe = np.zeros(dz.shape, dtype=bool)
    toe[6:34, 6:54] = True
    toe[10:30, 10:50] = False
    dz[toe] = 0.04
    # ...and thin spoil of the opposite sign beside the cut, which is not the cut's own edge.
    dz[46:50, 20:40] = 0.03
    regs = regions.measure_regions(labels, regs, dz, grid.cell_area_m2, 0.03, 0.0, 1.0, ring_cells=10)
    by = {r["class_name"]: r for r in regs}
    assert by["fill"]["volume_m3"] == pytest.approx(300.0)                 # the volume itself is untouched
    assert by["fill"]["edge_volume_m3"] == pytest.approx(toe.sum() * 0.25 * 0.04, rel=0.02)
    assert by["fill"]["volume_sigma_m3"] > by["fill"]["edge_volume_m3"] > 5 * by["fill"]["volume_sigma_random_m3"]
    assert by["cut"]["edge_volume_m3"] == 0
    tot = regions.totals(regs, 8000, grid.cell_area_m2, 0.0)
    assert tot["fill_below_detection_m3"] == pytest.approx(by["fill"]["edge_volume_m3"])
    assert tot["fill_volume_sigma_m3"] >= tot["fill_below_detection_m3"] and tot["cut_below_detection_m3"] == 0
    assert tot["net_volume_sigma_m3"] >= tot["fill_below_detection_m3"]
    # No ring requested, no edge term.
    plain = regions.measure_regions(labels, [dict(r) for r in regs], dz, grid.cell_area_m2, 0.03, 0.0, 1.0)
    assert all(r.get("edge_volume_m3", 0) == 0 for r in plain)


def test_random_part_never_beats_full_correlation():
    # A region smaller than one independent sample: its cells are fully correlated.
    assert regions.random_volume_sigma(4, 1.0, 0.1, 64.0) == pytest.approx(0.1 * 4)
    assert regions.random_volume_sigma(400, 1.0, 0.1, 1.0) == pytest.approx(0.1 * 20)


def test_totals_and_changed_share():
    grid, labels, regs, dz = _scene()
    regs = regions.measure_regions(labels, regs, dz, grid.cell_area_m2, 0.03, 0.02, 4.0)
    tot = regions.totals(regs, valid_cells=8000, cell_area=grid.cell_area_m2, sigma_sys=0.02)
    assert tot["compared_area_m2"] == 2000.0
    assert tot["fill_volume_m3"] == pytest.approx(300.0) and tot["cut_volume_m3"] == pytest.approx(100.0)
    assert tot["net_volume_m3"] == pytest.approx(200.0)
    # Vegetation and appearance are reported but are not "changed ground".
    assert tot["changed_area_m2"] == 250.0 and tot["changed_share"] == pytest.approx(0.125)
    assert tot["vegetation_area_m2"] == 50.0 and tot["appearance_area_m2"] == 50.0
    # A leftover offset moves cut and fill the same way: the net carries it on the combined area.
    assert tot["net_volume_sigma_m3"] > tot["fill_volume_sigma_m3"] > tot["cut_volume_sigma_m3"]
    assert tot["net_volume_sigma_m3"] == pytest.approx(math.hypot(
        math.hypot(0.25 * 0.03 * math.sqrt(800 * 4), 0.25 * 0.03 * math.sqrt(200 * 4)), 250.0 * 0.02))


def test_nothing_changed():
    grid = _grid()
    labels = np.zeros((grid.height, grid.width), dtype="int32")
    assert regions.measure_regions(labels, [], None, 0.25, 0.03, 0.0, 1.0) == []
    tot = regions.totals([], 8000, 0.25, 0.0)
    assert tot["changed_share"] == 0 and tot["net_volume_m3"] == 0 and tot["fill_regions"] == 0
    collection, info = regions.feature_collection(labels, [], grid)
    assert collection == {"type": "FeatureCollection", "features": []} and info["feature_count"] == 0


def test_feature_collection_is_wgs84_with_the_numbers_attached():
    grid, labels, regs, dz = _scene()
    regs = regions.measure_regions(labels, regs, dz, grid.cell_area_m2, 0.03, 0.02, 4.0)
    collection, info = regions.feature_collection(labels, regs, grid)
    assert info == {"feature_count": 4, "regions": 4, "omitted_small_regions": 0}
    feats = collection["features"]
    # Earthwork first, largest volume first; then the informational classes.
    assert [f["properties"]["class"] for f in feats][:2] == ["fill", "cut"]
    fill = feats[0]["properties"]
    assert fill["area"] == 200.0 and fill["volume_m3"] == 300.0 and fill["volume_uncertainty_m3"] > 0
    assert fill["color"] == "#2563eb" and fill["label"].startswith("Fill")
    assert set(feats[-1]["properties"]) == {"id", "class", "label", "color", "area"}
    for f in feats:
        geom = shape(f["geometry"])
        assert geom.is_valid and 14.9 < geom.centroid.x < 15.1 and 40.6 < geom.centroid.y < 40.7
    # Area survives the trip to lon/lat (UTM zone 33 central meridian is 15°E).
    lon0, lat0, lon1, lat1 = shape(feats[0]["geometry"]).bounds
    width_m = (lon1 - lon0) * 111_320 * math.cos(math.radians(lat0))
    assert width_m == pytest.approx(20.0, rel=0.02)


def test_feature_cap_keeps_the_largest_and_says_so():
    grid, labels, regs, dz = _scene()
    regs = regions.measure_regions(labels, regs, dz, grid.cell_area_m2, 0.03, 0.02, 4.0)
    collection, info = regions.feature_collection(labels, regs, grid, max_features=2)
    assert info["feature_count"] == 2 and info["omitted_small_regions"] == 2
    assert [f["properties"]["class"] for f in collection["features"]] == ["fill", "cut"]


def test_regions_with_holes_and_diagonal_contacts_give_valid_geometry():
    grid = _grid(40, 40)
    classes = np.zeros((40, 40), dtype="uint8")
    classes[5:25, 5:25] = detect.FILL
    classes[10:15, 10:15] = detect.NONE          # a hole
    classes[25:30, 25:30] = detect.FILL          # touches the first square at one corner
    labels, regs = detect.label_regions(classes, {})
    assert len(regs) == 1
    regs = regions.measure_regions(labels, regs, np.ones((40, 40), dtype="float32"), grid.cell_area_m2, 0, 0, 1)
    collection, _ = regions.feature_collection(labels, regs, grid, simplify_cells=0)
    assert len(collection["features"]) == 1
    assert shape(collection["features"][0]["geometry"]).is_valid


def test_legend_covers_every_class():
    assert {e["name"] for e in regions.legend()} == set(detect.CLASS_NAMES.values())
