import numpy as np
import pytest
import rasterio
from rasterio import features
from rasterio.transform import from_origin
from shapely import affinity
from shapely.geometry import box

from bfplugin.grid import Grid
from bfplugin.vectorize import Footprint, build_footprints, feature_collection, outlines, regularize

RES = 0.2


def _grid(w=200, h=150):
    return Grid(crs=rasterio.crs.CRS.from_epsg(32633), transform=from_origin(500000, 4500000, RES, RES),
                width=w, height=h, meters_per_unit=1.0)


def _labels(grid, polys):
    shapes = [(affinity.affine_transform(p, [1, 0, 0, -1, 500000, 4500000]), i) for i, p in enumerate(polys, 1)]
    return features.rasterize(shapes, out_shape=(grid.height, grid.width), transform=grid.transform, dtype="int32")


def test_outlines_take_the_exterior_ring_only():
    grid = _grid()
    ring = box(2, 2, 14, 12).difference(box(6, 6, 10, 8))        # a courtyard
    labels = _labels(grid, [ring])
    polys = outlines(labels, grid)
    assert set(polys) == {1} and len(polys[1].interiors) == 0
    assert polys[1].area == pytest.approx(12 * 10, rel=0.02)


def test_regularize_only_near_rectangles_and_only_without_new_overlap():
    grid = _grid()
    rect = affinity.rotate(box(2, 2, 12, 8), 25)                 # rotated → staircase outline
    lshape = box(20, 2, 36, 18).difference(box(28, 10, 36, 18))
    labels = _labels(grid, [rect, lshape])
    prob = np.full(labels.shape, 250, "uint8")
    fps, stats = build_footprints(labels, prob, grid, simplify_m=0.3, min_area_m2=1,
                                  regularize_area_threshold=0.85, regularize_overlap_tol_m2=1.0)
    by_id = {f.instance_id: f for f in fps}
    assert by_id[1].regularized and not by_id[2].regularized and stats["regularized"] == 1
    assert by_id[1].area_m2(grid) == pytest.approx(60, rel=0.1)
    assert by_id[2].area_m2(grid) == pytest.approx(256 - 64, rel=0.05)
    assert len(by_id[1].polygon.exterior.coords) == 5
    assert by_id[1].score == pytest.approx(250 / 255, abs=1e-3)


def test_regularize_respects_neighbour_overlap_tolerance():
    grid = _grid()
    # a rectangle with a notch cut by its neighbour: its MRR would overlap the neighbour by 2 x 2 m
    neighbour = box(10, 0, 12, 2)
    notched = box(0, 0, 12, 10).difference(neighbour)
    fps = [Footprint(1, notched, 0.9), Footprint(2, neighbour, 0.9)]
    assert regularize(fps, grid, area_threshold=0.85, overlap_tol_m2=1.0) == 1          # only the neighbour (already a rectangle)
    assert not fps[0].regularized and fps[1].regularized
    fps = [Footprint(1, notched, 0.9), Footprint(2, neighbour, 0.9)]
    assert regularize(fps, grid, area_threshold=0.85, overlap_tol_m2=5.0) == 2
    assert fps[0].regularized and fps[0].polygon.area == pytest.approx(120, rel=0.01)


def test_feature_collection_is_4326_with_class_score_area():
    grid = _grid()
    fc = feature_collection([Footprint(7, affinity.affine_transform(box(0, 0, 10, 10), [1, 0, 0, -1, 500000, 4500000]),
                                       0.87654, regularized=True)], grid, "building")
    f = fc["features"][0]
    assert fc["type"] == "FeatureCollection" and f["geometry"]["type"] == "Polygon"
    lon, lat = f["geometry"]["coordinates"][0][0]
    assert 14 < lon < 16 and 40 < lat < 41
    assert f["properties"] == {"class": "building", "score": 0.877, "area": 100.0, "regularized": True, "instance_id": 7}
