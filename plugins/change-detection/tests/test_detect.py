"""Noise filters: shift tolerance, level of detection, minimum area."""

import numpy as np
import pytest
from scipy import ndimage

from cdplugin import detect


def _site(shape=(200, 240), seed=0):
    rng = np.random.default_rng(seed)
    z = ndimage.gaussian_filter(rng.normal(0, 1, shape), 10) * 20 + 50
    z[40:80, 50:100] += 6.0        # a building: 6 m walls
    return z.astype("float32")


def test_a_pure_shift_within_the_tolerance_is_not_change():
    before = _site()
    after = np.roll(before, (1, -1), axis=(0, 1))
    plain = after - before
    assert np.abs(plain).max() > 5.0                     # the building edges light up...
    tolerant = detect.tolerant_difference(before, after, radius=1)
    assert np.abs(tolerant[2:-2, 2:-2]).max() == 0.0     # ...and are silent with the tolerance


def test_shift_larger_than_the_tolerance_shows_again():
    before = _site()
    after = np.roll(before, 3, axis=1)
    assert np.abs(detect.tolerant_difference(before, after, 1)[2:-2, 5:-5]).max() > 5.0
    assert np.abs(detect.tolerant_difference(before, after, 3)[4:-4, 5:-5]).max() == 0.0


def test_real_change_keeps_its_sign_and_most_of_its_size():
    before = _site(seed=1)
    after = before.copy()
    after[120:160, 30:90] += 1.5       # fill
    after[120:160, 150:210] -= 2.0     # cut
    t = detect.tolerant_difference(before, after, 1)
    assert t[140, 60] == pytest.approx(1.5, abs=0.1)
    assert t[140, 180] == pytest.approx(-2.0, abs=0.1)
    assert np.abs(t[10:30, 10:30]).max() == 0.0


def test_thin_features_that_appear_or_disappear_are_both_kept():
    flat = np.full((60, 60), 10.0, dtype="float32")
    wall = flat.copy()
    wall[:, 30] += 2.0                 # one cell wide
    appeared = detect.tolerant_difference(flat, wall, 1)
    removed = detect.tolerant_difference(wall, flat, 1)
    assert appeared[20, 30] == pytest.approx(2.0)
    assert removed[20, 30] == pytest.approx(-2.0)     # needs the second point of view


def test_noise_is_damped_not_amplified():
    rng = np.random.default_rng(2)
    before = (rng.normal(0, 0.03, (300, 300))).astype("float32")
    after = (rng.normal(0, 0.03, (300, 300))).astype("float32")
    plain = np.abs(after - before)
    tolerant = np.abs(detect.tolerant_difference(before, after, 1))
    assert (tolerant > 0.08).mean() < (plain > 0.08).mean() / 5


def test_missing_cells_yield_zero_and_radius_zero_is_the_plain_difference():
    before = _site(seed=3)
    after = before + 1.0
    after[50:60, 50:60] = np.nan
    before[100:110, 100:110] = np.nan
    t = detect.tolerant_difference(before, after, 1)
    assert np.isfinite(t).all() and t[55, 55] == 0 and t[105, 105] == 0
    plain = detect.tolerant_difference(before, after, 0)
    assert plain[10, 10] == pytest.approx(1.0) and plain[55, 55] == 0


def test_level_of_detection_combines_noise_systematic_term_and_floor():
    lod = detect.level_of_detection(1.96, 0.04, 0.03, 0.0)
    assert lod["sigma_total_m"] == pytest.approx(0.05)
    assert lod["level_of_detection_m"] == pytest.approx(0.098) and lod["limited_by"] == "noise"
    quiet = detect.level_of_detection(1.96, 0.005, 0.0, 0.0)
    assert quiet["level_of_detection_m"] == detect.MIN_AUTO_LOD_M and quiet["limited_by"] == "floor"
    raised = detect.level_of_detection(1.96, 0.02, 0.0, 0.30)
    assert raised["level_of_detection_m"] == 0.30 and raised["floor_source"] == "min_height_change_m"
    # A user minimum cannot push the threshold below what the noise allows.
    noisy = detect.level_of_detection(1.96, 0.10, 0.0, 0.02)
    assert noisy["level_of_detection_m"] == pytest.approx(0.196)


def test_classify_and_vegetation():
    t = np.zeros((10, 10), dtype="float32")
    t[1:4, 1:4] = 0.5
    t[6:9, 6:9] = -0.5
    t[1:4, 6:9] = 0.04
    valid = np.ones_like(t, dtype=bool)
    valid[6, 6] = False
    veg = np.zeros_like(valid)
    veg[1:4, 1:3] = True
    classes = detect.classify(t, t, valid, 0.1, veg)
    assert (classes[1:4, 3] == detect.FILL).all() and (classes[1:4, 1:3] == detect.VEGETATION).all()
    assert classes[7, 7] == detect.CUT and classes[6, 6] == detect.NONE
    assert (classes[1:4, 6:9] == detect.NONE).all()


def test_the_rim_the_tolerance_took_is_given_back():
    before = np.full((80, 80), 10.0, dtype="float32")
    after = before.copy()
    after[20:60, 20:60] -= 2.0                 # a 40 x 40 cell pit with vertical walls
    valid = np.ones(before.shape, dtype=bool)
    tolerant = detect.tolerant_difference(before, after, 1)
    dz = after - before
    eroded = detect.classify(tolerant, dz, valid, 0.1) == detect.CUT
    restored = detect.classify(tolerant, dz, valid, 0.1, reach=2) == detect.CUT
    assert eroded.sum() == 40 * 40             # (here the second point of view already keeps the rim)
    assert restored.sum() == 40 * 40
    # On rough ground the tolerant difference loses the rim; the plain one restores exactly it.
    shaved = tolerant.copy()
    shaved[20:22, 20:60] = 0
    assert (detect.classify(shaved, dz, valid, 0.1) == detect.CUT).sum() == 38 * 40
    assert (detect.classify(shaved, dz, valid, 0.1, reach=2) == detect.CUT).sum() == 40 * 40


def test_restoring_the_rim_does_not_swallow_the_neighbourhood():
    dz = np.zeros((40, 80), dtype="float32")
    tolerant = np.zeros_like(dz)
    dz[10:30, 10:30] = tolerant[10:30, 10:30] = 1.0     # an established fill
    dz[10:30, 30:70] = 0.5                              # a displaced wall right next to it: plain only
    valid = np.ones(dz.shape, dtype=bool)
    fill = detect.classify(tolerant, dz, valid, 0.1, reach=2) == detect.FILL
    assert fill[:, :32].sum() == 20 * 22 and not fill[:, 32:].any()
    # Never across missing data or into the opposite sign.
    dz[10:30, 30:32] = -0.5
    assert (detect.classify(tolerant, dz, valid, 0.1, reach=2) == detect.FILL).sum() == 400
    dz[10:30, 30:32] = 0.5
    valid[:, 30:32] = False
    assert (detect.classify(tolerant, dz, valid, 0.1, reach=2) == detect.FILL).sum() == 400


def test_speckle_cannot_seed_a_region():
    rng = np.random.default_rng(5)
    dz = rng.normal(0, 0.1, (200, 200)).astype("float32")       # 16 % of the cells exceed the level
    tolerant = np.zeros_like(dz)
    tolerant[100, 100] = 0.5                                    # one confirmed cell
    valid = np.ones(dz.shape, dtype=bool)
    assert not detect.classify(tolerant, dz, valid, 0.1, min_cells=16, reach=2).any()


def test_small_regions_are_dropped_and_ids_are_contiguous():
    classes = np.zeros((50, 50), dtype="uint8")
    classes[5:15, 5:15] = detect.FILL          # 100 cells
    classes[30, 30] = detect.FILL              # speck
    classes[20:22, 40:45] = detect.CUT         # 10 cells
    classes[40:44, 5:9] = detect.APPEARANCE    # 16 cells
    labels, regions = detect.label_regions(classes, {detect.FILL: 8, detect.CUT: 8, detect.APPEARANCE: 20})
    assert [(r["class"], r["cells"]) for r in regions] == [(detect.FILL, 100), (detect.CUT, 10)]
    assert [r["id"] for r in regions] == [1, 2]
    assert labels[30, 30] == 0 and labels[41, 6] == 0 and labels[10, 10] == 1 and labels[21, 42] == 2


def test_diagonal_neighbours_form_one_region_and_trenches_survive():
    classes = np.zeros((40, 40), dtype="uint8")
    for i in range(30):
        classes[5 + i, 5 + i] = detect.CUT     # a one-cell-wide diagonal trench
    labels, regions = detect.label_regions(classes, {detect.CUT: 16})
    assert len(regions) == 1 and regions[0]["cells"] == 30


def test_edge_buffer_shrinks_the_valid_area():
    valid = np.ones((30, 30), dtype=bool)
    valid[10:12, 10:12] = False
    out = detect.erode_valid(valid, 2)
    assert not out[:2].any() and not out[:, -2:].any() and not out[8:14, 8:14].any()
    assert out[5, 5] and out[20, 20]
    assert detect.erode_valid(valid, 0) is valid
