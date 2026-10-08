"""Photo-derived signals: vegetation mask and appearance change."""

import numpy as np

from cdplugin import visual


def _photo(shape=(160, 200), seed=0):
    rng = np.random.default_rng(seed)
    from scipy import ndimage
    texture = ndimage.gaussian_filter(rng.normal(0, 1, shape), 1.5) * 0.12
    rgb = np.stack([0.55 + texture, 0.45 + texture, 0.35 + texture]).astype("float32")
    return np.clip(rgb, 0, 1)


def test_green_vegetation_is_found_whatever_the_brightness():
    rgb = _photo()
    rgb[:, 20:60, 20:60] = np.array([0.2, 0.5, 0.15], dtype="float32")[:, None, None]
    rgb[:, 100:140, 20:60] = np.array([0.1, 0.25, 0.075], dtype="float32")[:, None, None]   # same green, in shadow
    valid = np.ones(rgb.shape[1:], dtype=bool)
    valid[30:40, 30:40] = False
    veg = visual.vegetation_mask(rgb, valid)
    assert veg[25, 25] and veg[120, 40] and not veg[35, 35]
    assert not veg[80, 150]                                   # bare soil
    assert veg.sum() == 2 * 40 * 40 - 100


def test_grey_dark_and_brown_surfaces_are_not_vegetation():
    rgb = np.zeros((3, 30, 30), dtype="float32")
    rgb[:, :10] = 0.6                                          # concrete
    rgb[:, 10:20] = 0.02                                       # deep shadow
    rgb[:, 20:] = np.array([0.5, 0.4, 0.25], dtype="float32")[:, None, None]   # dry soil
    assert not visual.vegetation_mask(rgb, np.ones((30, 30), dtype=bool)).any()


def test_identical_photos_under_a_different_exposure_show_no_change():
    before = _photo(seed=1)
    after = np.clip(before * 0.7, 0, 1)                        # darker day
    valid = np.ones(before.shape[1:], dtype=bool)
    flagged, info = visual.appearance_change(before, valid, after, valid, valid)
    assert flagged.sum() == 0
    assert info["calibration_cells"] > 0


def test_a_resurfaced_patch_is_flagged_and_little_else():
    before = _photo(seed=2)
    after = np.clip(before * 0.85, 0, 1)
    after[:, 60:100, 80:130] = np.array([0.25, 0.27, 0.33], dtype="float32")[:, None, None]   # asphalt
    valid = np.ones(before.shape[1:], dtype=bool)
    flagged, _ = visual.appearance_change(before, valid, after, valid, valid)
    patch = np.zeros_like(valid)
    patch[60:100, 80:130] = True
    assert flagged[patch].mean() > 0.9
    assert flagged[~patch].sum() < 0.02 * (~patch).sum()


def test_a_cast_shadow_is_not_flagged_as_a_whole():
    before = _photo(seed=3)
    after = before.copy()
    after[:, 40:120, 50:150] *= 0.45                           # a shadow falls across the site
    valid = np.ones(before.shape[1:], dtype=bool)
    flagged, _ = visual.appearance_change(before, valid, after, valid, valid)
    shadow = np.zeros_like(valid)
    shadow[45:115, 55:145] = True                              # its interior
    assert flagged[shadow].mean() < 0.02


def test_invalid_image_and_its_rim_are_never_flagged():
    before = _photo(seed=4)
    after = before.copy()
    valid_after = np.ones(before.shape[1:], dtype=bool)
    valid_after[:, :50] = False
    after[:, :, :50] = 0
    flagged, _ = visual.appearance_change(before, np.ones_like(valid_after), after, valid_after, valid_after)
    assert not flagged[:, :54].any()
