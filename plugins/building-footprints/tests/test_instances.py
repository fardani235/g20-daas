"""Watershed separation on hand-made masks with exact distance transforms."""

import numpy as np
import pytest
from scipy import ndimage

from bfplugin.instances import separate_instances


def _scene(shapes, shape=(120, 160), outside_scale=10.0):
    """Mask + per-building normalised distance (positive inside, negative outside)."""
    mask = np.zeros(shape, bool)
    dist = np.zeros(shape, "float32")
    for (y0, y1, x0, x1) in shapes:
        m = np.zeros(shape, bool)
        m[y0:y1, x0:x1] = True
        d = ndimage.distance_transform_edt(m)
        dist[m] = d[m] / d.max()
        mask |= m
    out = ndimage.distance_transform_edt(~mask)
    dist[~mask] = -np.minimum(out[~mask] / outside_scale, 1.0)
    return mask, dist


def _run(mask, dist, **kw):
    args = dict(seed_min_distance=5, large_blob_area_px=2000, h_maxima_depth=0.1, min_area_px=20)
    args.update(kw)
    return separate_instances(mask, dist, **args)


@pytest.mark.parametrize("large", [0, 10 ** 9], ids=["h-maxima path", "local-maxima path"])
def test_touching_rectangles_become_two_buildings(large):
    mask, dist = _scene([(20, 70, 20, 80), (20, 60, 80, 120)])        # share the wall at x = 80
    labels, stats = _run(mask, dist, large_blob_area_px=large)
    assert stats.components == 1 and stats.instances == 2 and stats.split == 1
    assert len(np.unique(labels[20:70, 20:80])) == 1 and len(np.unique(labels[20:60, 80:120])) == 1
    assert labels[45, 50] != labels[40, 100] and labels[45, 50] > 0 and labels[40, 100] > 0
    assert (labels[mask] > 0).all() and (labels[~mask] == 0).all()


@pytest.mark.parametrize("large", [0, 10 ** 9], ids=["h-maxima path", "local-maxima path"])
def test_one_elongated_rectangle_stays_one_building(large):
    """Its distance crest is a flat ridge: many equal maxima must not split it."""
    mask, dist = _scene([(30, 60, 10, 150)])
    labels, stats = _run(mask, dist, large_blob_area_px=large)
    assert stats.instances == 1 and stats.split == 0
    assert (labels[mask] == 1).all()


def test_tiny_blobs_are_dropped_before_seeding():
    mask, dist = _scene([(20, 70, 20, 80), (100, 103, 100, 103)])
    labels, stats = _run(mask, dist, min_area_px=20)
    assert stats.components == 2 and stats.dropped_small == 1 and stats.instances == 1
    assert (labels[100:103, 100:103] == 0).all()


def test_separate_blobs_keep_separate_ids_and_ids_are_compact():
    mask, dist = _scene([(10, 30, 10, 40), (50, 80, 10, 40), (10, 40, 100, 150)])
    labels, stats = _run(mask, dist)
    assert stats.instances == 3 and sorted(np.unique(labels)) == [0, 1, 2, 3]


def test_empty_mask():
    mask = np.zeros((50, 50), bool)
    labels, stats = _run(mask, np.full((50, 50), -1, "float32"))
    assert stats.instances == 0 and labels.shape == mask.shape and not labels.any()


def test_h_maxima_depth_controls_merging():
    """Two maxima whose dip is shallower than h merge into one building."""
    mask = np.zeros((60, 140), bool)
    mask[10:50, 10:130] = True
    yy, xx = np.mgrid[0:60, 0:140]
    # two bumps of height 1.0 and 0.9 with a saddle at 0.85 between them
    dist = np.maximum(np.exp(-((xx - 45) ** 2 + (yy - 30) ** 2) / 400.0),
                      0.9 * np.exp(-((xx - 95) ** 2 + (yy - 30) ** 2) / 400.0)).astype("float32")
    dist[~mask] = -1
    saddle = dist[30, 45:95].min()
    assert 0.05 < saddle < 0.9
    deep, _ = _run(mask, dist, large_blob_area_px=0, h_maxima_depth=0.05)
    shallow, _ = _run(mask, dist, large_blob_area_px=0, h_maxima_depth=0.95)
    assert deep.max() == 2 and shallow.max() == 1
