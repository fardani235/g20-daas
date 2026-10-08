"""Alignment estimators: vertical offset and noise, spatial consistency, horizontal shift."""

import numpy as np
import pytest
from scipy import ndimage

from cdplugin import align


def _rng(seed=0):
    return np.random.default_rng(seed)


# --- vertical offset ---------------------------------------------------------

def test_offset_and_noise_of_unchanged_ground():
    values = _rng().normal(0.42, 0.03, 50_000)
    fit = align.fit_stable_peak(values)
    assert fit.offset == pytest.approx(0.42, abs=0.003)
    assert fit.sigma == pytest.approx(0.03, rel=0.15)
    assert fit.stable_fraction > 0.98
    assert fit.ambiguity < 0.1


def test_offset_ignores_a_large_excavation():
    # 45 % of the site was dug out by 1-3 m: mean and median are dragged, the mode is not.
    rng = _rng(1)
    stable = rng.normal(-1.7, 0.04, 55_000)
    dug = -1.7 - rng.uniform(1.0, 3.0, 45_000)
    values = np.concatenate([stable, dug])
    assert abs(np.median(values) - -1.7) > 0.05
    fit = align.fit_stable_peak(values)
    assert fit.offset == pytest.approx(-1.7, abs=0.01)
    assert fit.sigma == pytest.approx(0.04, rel=0.25)
    assert fit.stable_fraction == pytest.approx(0.55, abs=0.05)


def test_offset_survives_a_minority_of_stable_ground():
    # Only 30 % unchanged; the rest is spread-out change. Still the tallest peak.
    rng = _rng(2)
    values = np.concatenate([rng.normal(5.0, 0.02, 30_000), 5.0 + rng.uniform(-4, 4, 70_000)])
    fit = align.fit_stable_peak(values)
    assert fit.offset == pytest.approx(5.0, abs=0.01)
    assert 0.25 < fit.stable_fraction < 0.40


def test_second_peak_is_reported_as_ambiguity():
    # A platform raised by exactly 0.5 m over 40 % of the site looks like a second "offset".
    rng = _rng(3)
    values = np.concatenate([rng.normal(0.0, 0.02, 60_000), rng.normal(0.5, 0.02, 40_000)])
    fit = align.fit_stable_peak(values)
    assert fit.offset == pytest.approx(0.0, abs=0.01)
    assert fit.ambiguity == pytest.approx(40 / 60, abs=0.08)
    assert fit.second_offset == pytest.approx(0.5, abs=0.05)


def test_one_sided_change_next_to_the_peak_does_not_widen_the_noise():
    # A thin layer of fill (0.05-0.4 m) adjoins the stable peak on one side only.
    rng = _rng(4)
    values = np.concatenate([rng.normal(0.0, 0.02, 60_000), rng.uniform(0.05, 0.4, 40_000)])
    fit = align.fit_stable_peak(values)
    assert fit.offset == pytest.approx(0.0, abs=0.01)
    assert fit.sigma < 0.035


def test_noise_free_and_tiny_samples():
    fit = align.fit_stable_peak(np.full(5000, 1.25))
    assert fit.offset == pytest.approx(1.25) and fit.sigma < 1e-3
    with pytest.raises(ValueError):
        align.fit_stable_peak(np.arange(50, dtype=float))


def test_half_sample_mode_finds_the_densest_value():
    rng = _rng(5)
    x = np.sort(np.concatenate([rng.normal(2.0, 0.01, 2000), rng.uniform(-10, 10, 4000)]))
    assert align.half_sample_mode(x) == pytest.approx(2.0, abs=0.01)
    assert align.half_sample_mode(np.array([1.0, 1.1, 5.0])) == pytest.approx(1.05)


# --- tiles, tilt, systematic uncertainty ---------------------------------------

def _residual(shape=(480, 600), sigma=0.03, seed=0):
    return _rng(seed).normal(0, sigma, shape).astype("float32")


def test_constant_offset_has_no_tilt_and_small_systematic_sigma():
    tiles = align.analyse_tiles(_residual(), 0.03)
    assert tiles.offsets.shape == (6, 6) and tiles.valid_tiles == 36
    assert tiles.sigma_sys_measured and tiles.sigma_sys < 0.005
    assert tiles.tilt_m < 0.01


def test_tilt_is_measured_and_its_plane_removes_it():
    r = _residual(seed=1)
    h, w = r.shape
    tilt = 0.30 * ((np.arange(w) + 0.5) / w - 0.5)[None, :] - 0.10 * ((np.arange(h) + 0.5) / h - 0.5)[:, None]
    r = (r + tilt).astype("float32")
    tiles = align.analyse_tiles(r, 0.03)
    assert tiles.plane[1] == pytest.approx(0.30, abs=0.02)
    assert tiles.plane[2] == pytest.approx(-0.10, abs=0.02)
    assert tiles.tilt_m == pytest.approx(0.40, abs=0.03)
    # Left in place, the tilt is carried as systematic uncertainty...
    assert tiles.sigma_sys > 0.05
    # ...and after removing the plane the tiles agree again.
    flat = align.analyse_tiles(r - align.plane_surface(tiles.plane, r.shape), 0.03)
    assert flat.sigma_sys < 0.01 and flat.tilt_m < 0.02


def test_tiles_dominated_by_change_are_set_aside():
    r = _residual(seed=2)
    r[0:80, 0:100] -= 2.5          # one whole tile excavated
    r[80:160, 100:200] += 1.2      # another one filled
    tiles = align.analyse_tiles(r, 0.03)
    assert tiles.valid_tiles == 36 and tiles.used.sum() == 34
    assert not tiles.used[0, 0] and not tiles.used[1, 1]
    assert tiles.sigma_sys < 0.005 and tiles.tilt_m < 0.02


def test_small_site_falls_back_to_the_noise_as_systematic_sigma():
    r = _residual(shape=(40, 40), seed=3)
    tiles = align.analyse_tiles(r, 0.03)
    assert not tiles.sigma_sys_measured and tiles.sigma_sys == pytest.approx(0.03)
    assert tiles.plane is None


def test_missing_cells_are_ignored():
    r = _residual(seed=4)
    r[:, :300] = np.nan
    tiles = align.analyse_tiles(r, 0.03)
    assert tiles.valid_tiles == 18 and tiles.sigma_sys_measured


# --- noise correlation -------------------------------------------------------

def test_independent_noise_counts_every_cell():
    out = align.block_noise(_residual(seed=5), 0.03)
    assert out["measured"] and out["cells_per_sample"] == pytest.approx(1.0, abs=0.3)


def test_correlated_noise_counts_fewer_independent_samples():
    smooth = ndimage.gaussian_filter(_rng(6).normal(0, 1, (480, 600)), 3.0)
    smooth = (smooth / smooth.std() * 0.03).astype("float32")
    out = align.block_noise(smooth, 0.03)
    assert out["cells_per_sample"] > 20


def test_block_noise_fallback_is_conservative():
    out = align.block_noise(_residual(shape=(20, 20)), 0.03)
    assert not out["measured"] and out["cells_per_sample"] == 32.0


# --- horizontal shift --------------------------------------------------------

def _terrain(shape=(300, 400), seed=0):
    rng = _rng(seed)
    z = ndimage.gaussian_filter(rng.normal(0, 1, shape), 12) * 40
    z[60:100, 80:140] += 5.0       # buildings give the search sharp edges
    z[180:230, 250:290] += 4.0
    z[120:135, 30:60] += 6.0
    return z.astype("float32")


def _shifted(z, dx, dy):
    """moving(row + dy, col + dx) == fixed(row, col)."""
    return ndimage.shift(z, (dy, dx), order=1, mode="nearest").astype("float32")


def test_shift_is_recovered_to_a_fraction_of_a_cell():
    fixed = _terrain()
    moving = _shifted(fixed, 3, -2) + 0.7 + _rng(1).normal(0, 0.02, fixed.shape).astype("float32")
    s = align.estimate_horizontal_shift(fixed, moving, res_m=0.25, max_shift_m=2.0)
    assert s.estimated and s.significant and not s.at_search_limit
    assert s.dx_cells == pytest.approx(3, abs=0.5) and s.dy_cells == pytest.approx(-2, abs=0.5)
    assert s.east_m == pytest.approx(0.75, abs=0.13) and s.north_m == pytest.approx(0.5, abs=0.13)
    assert s.gain > 1.5


def test_no_shift_is_reported_when_the_surveys_already_agree():
    fixed = _terrain(seed=2)
    moving = fixed + 0.3 + _rng(3).normal(0, 0.02, fixed.shape).astype("float32")
    s = align.estimate_horizontal_shift(fixed, moving, 0.25, 2.0)
    assert s.estimated and not s.significant
    assert s.magnitude_m < 0.13


def test_flat_ground_never_produces_a_shift():
    rng = _rng(4)
    fixed = (10 + rng.normal(0, 0.02, (300, 400))).astype("float32")
    moving = (10.4 + rng.normal(0, 0.02, (300, 400))).astype("float32")
    s = align.estimate_horizontal_shift(fixed, moving, 0.25, 2.0)
    assert not s.significant


def test_shift_survives_real_change_on_part_of_the_site():
    fixed = _terrain(seed=5)
    moving = _shifted(fixed, -2, 1)
    moving[200:280, 40:200] -= 3.0     # a big excavation in the second survey
    s = align.estimate_horizontal_shift(fixed, moving, 0.25, 2.0)
    assert s.significant
    assert s.dx_cells == pytest.approx(-2, abs=0.5) and s.dy_cells == pytest.approx(1, abs=0.5)


def test_shift_beyond_the_search_range_is_flagged():
    fixed = _terrain(seed=6)
    moving = _shifted(fixed, 7, 0)
    s = align.estimate_horizontal_shift(fixed, moving, 0.25, 1.0)   # searches ±4 cells
    assert s.at_search_limit
    assert "larger" in s.note or not s.significant


def test_long_search_ranges_use_block_means():
    fixed = _terrain(shape=(600, 800), seed=7)
    moving = _shifted(fixed, 12, 8)
    s = align.estimate_horizontal_shift(fixed, moving, res_m=0.25, max_shift_m=5.0)
    assert s.step_m == pytest.approx(0.75)        # 3-cell blocks
    assert s.significant
    assert s.dx_cells == pytest.approx(12, abs=1.5) and s.dy_cells == pytest.approx(8, abs=1.5)


def test_search_can_be_disabled_or_impossible():
    z = _terrain()
    assert not align.estimate_horizontal_shift(z, z, 0.25, 0).estimated
    small = z[:20, :20]
    assert not align.estimate_horizontal_shift(small, small, 0.25, 2.0).estimated
    empty = np.full_like(z, np.nan)
    assert not align.estimate_horizontal_shift(empty, z, 0.25, 2.0).estimated


def test_a_platform_over_almost_half_the_site_does_not_pass_for_a_tilt():
    # 15 of 36 tiles raised by 0.5 m: neither a tilt nor a reason to distrust the other 21.
    r = _residual(seed=8)
    r[:, :200] += 0.5
    r[:240, 200:300] += 0.5
    tiles = align.analyse_tiles(r, 0.03)
    assert tiles.model == "constant" and tiles.plane is None and tiles.tilt_m == 0
    assert tiles.used.sum() == 21 and tiles.set_aside_fraction == pytest.approx(15 / 36)
    assert tiles.sigma_sys < 0.005
    assert tiles.noise == pytest.approx(0.03, rel=0.2)


def test_tile_noise_is_not_smeared_by_a_tilt():
    r = _residual(seed=9)
    h, w = r.shape
    r = (r + 0.6 * ((np.arange(w) + 0.5) / w - 0.5)[None, :]).astype("float32")
    tiles = align.analyse_tiles(r, 0.2)                  # the global peak is wide...
    assert tiles.model == "plane" and tiles.noise < 0.05  # ...the noise inside a tile is not
    assert tiles.as_dict()["model"] == "plane" and tiles.as_dict()["tiles_used"] == 36


def test_two_halves_must_agree_on_the_shift():
    # Smooth relief only (no edges): whatever weak minimum appears, it is not trusted
    # unless both halves of the samples find it.
    rng = _rng(10)
    fixed = (ndimage.gaussian_filter(rng.normal(0, 1, (300, 400)), 40) * 3).astype("float32")
    moving = fixed + rng.normal(0, 0.05, fixed.shape).astype("float32")
    s = align.estimate_horizontal_shift(fixed, moving, 0.25, 2.0)
    assert not s.significant


def test_a_tilt_between_the_surveys_is_not_mistaken_for_a_shift():
    fixed = _terrain(seed=11)
    h, w = fixed.shape
    slope = (0.05 * np.arange(w))[None, :].astype("float32")     # sloping ground: tilt ~ shift without detrending
    tilt = (1.0 * ((np.arange(w) + 0.5) / w - 0.5))[None, :].astype("float32")
    s = align.estimate_horizontal_shift(fixed + slope, fixed + slope + tilt, 0.25, 2.0)
    assert not s.significant and s.magnitude_m < 0.2
