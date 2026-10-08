"""The common grid: overlap, resolution, CRS, and reading onto it."""

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from cdplugin.errors import InputError
from cdplugin.grid import Raster, build_grid, fit_resolution, read_rgb, read_surface, working_crs
from tests.conftest import ORIGIN, ground, reproject_to, survey


def _open(s, key="dsm"):
    return Raster(s[key], f"test {key}", min_bands=3 if key == "orthophoto" else 1)


def test_grid_is_the_overlap_at_the_coarser_resolution(tmp_path):
    a = _open(survey(tmp_path, "a", res=0.20, extent=(4.0, 3.0, 196.0, 158.0), with_ortho=False))
    b = _open(survey(tmp_path, "b", res=0.25, with_ortho=False))
    grid = build_grid(a, b, a.crs)
    assert grid.resolution_m == pytest.approx(0.25) and grid.native_resolution_m == pytest.approx(0.25)
    left, bottom, right, top = grid.bounds
    assert left == pytest.approx(ORIGIN[0] + 4.0) and bottom <= ORIGIN[1] + 3.0
    assert right >= ORIGIN[0] + 196.0 and top == pytest.approx(ORIGIN[1] + 158.0)
    assert (grid.width, grid.height) == (768, 620)
    assert not grid.coarsened and grid.cell_area_m2 == pytest.approx(0.0625)
    # The order of the two surveys does not change the grid.
    assert build_grid(b, a, a.crs).bounds == grid.bounds


def test_requested_resolution_is_never_finer_than_the_data(tmp_path):
    a = _open(survey(tmp_path, "a", res=0.25, with_ortho=False))
    b = _open(survey(tmp_path, "b", res=0.5, with_ortho=False))
    warnings = []
    assert build_grid(a, b, a.crs, 0.1, warn=warnings.append).resolution_m == pytest.approx(0.5)
    assert "finer" in warnings[0]
    assert build_grid(a, b, a.crs, 2.0).resolution_m == pytest.approx(2.0)


def test_grid_over_the_budget_is_coarsened_not_refused(tmp_path):
    a = _open(survey(tmp_path, "a", res=0.25, with_ortho=False))
    warnings = []
    grid = build_grid(a, a, a.crs, max_pixels=50_000, warn=warnings.append)
    assert grid.coarsened and grid.pixels <= 50_000 and grid.resolution_m > 0.25
    assert "megapixel" in warnings[0] and "volumes are unaffected" in warnings[0]
    assert fit_resolution(0, 0, 200, 160, 1.0, 0.25, 50_000) == pytest.approx(grid.resolution_m)


def test_surveys_that_do_not_overlap_are_refused(tmp_path):
    a = _open(survey(tmp_path, "a", extent=(0.0, 0.0, 60.0, 60.0), with_ortho=False))
    b = _open(survey(tmp_path, "b", extent=(100.0, 0.0, 160.0, 60.0), with_ortho=False))
    with pytest.raises(InputError, match="do not overlap"):
        build_grid(a, b, a.crs)
    c = _open(survey(tmp_path, "c", extent=(58.0, 0.0, 120.0, 60.0), with_ortho=False))
    with pytest.raises(InputError, match="too little"):
        build_grid(a, c, a.crs)


def test_average_resampling_conserves_the_mean_height(tmp_path):
    fine = _open(survey(tmp_path, "fine", res=0.1, noise=0.05, border=0, with_ortho=False))
    coarse = _open(survey(tmp_path, "coarse", res=1.0, noise=0, border=0, with_ortho=False))
    grid = build_grid(fine, coarse, fine.crs)
    assert grid.resolution_m == pytest.approx(1.0)
    with rasterio.open(fine.path) as src:
        truth = src.read(1).astype("float64").mean()
    z = read_surface(fine, grid)
    assert np.isfinite(z).all()
    assert float(z.astype("float64").mean()) == pytest.approx(truth, abs=1e-3)


def test_nodata_becomes_nan_declared_or_not(tmp_path):
    s = survey(tmp_path, "a", res=0.5, border=5.0, with_ortho=False)
    a = _open(s)
    grid = build_grid(a, a, a.crs)
    z = read_surface(a, grid)
    assert np.isnan(z[:10, :]).all() and np.isfinite(z[20:-20, 20:-20]).all()
    assert np.nanmin(z) > 90
    # The same file with the nodata tag stripped: the -9999 sentinel is still recognised.
    with rasterio.open(s["dsm"]) as src:
        data, profile = src.read(1), src.profile.copy()
    profile.pop("nodata")
    bare = str(tmp_path / "bare.tif")
    with rasterio.open(bare, "w", **profile) as dst:
        dst.write(data, 1)
    b = Raster(bare, "bare surface")
    z2 = read_surface(b, grid)
    assert np.array_equal(np.isnan(z), np.isnan(z2)) and np.nanmin(z2) > 90


def test_a_survey_in_another_crs_lands_on_the_same_ground(tmp_path):
    s = survey(tmp_path, "a", res=0.5, noise=0, with_ortho=False)
    other = reproject_to(s["dsm"], str(tmp_path / "a_3857.tif"), "EPSG:3857")
    a, b = _open(s), Raster(other, "web-mercator surface")
    assert working_crs([a, b]) == a.crs
    grid = build_grid(a, b, a.crs)
    za, zb = read_surface(a, grid), read_surface(b, grid)
    both = np.isfinite(za) & np.isfinite(zb)
    assert both.mean() > 0.9
    # Away from the 5 m building walls the two agree to a few centimetres.
    diff = np.abs(za - zb)[both]
    assert np.median(diff) < 0.01 and np.percentile(diff, 90) < 0.05


def test_geographic_inputs_get_a_metric_working_crs(tmp_path):
    s = survey(tmp_path, "a", res=1.0, noise=0, with_ortho=False)
    geo = Raster(reproject_to(s["dsm"], str(tmp_path / "a_4326.tif"), "EPSG:4326"), "geographic surface")
    crs = working_crs([geo, geo])
    assert crs.to_epsg() == 32633
    grid = build_grid(geo, geo, crs)
    assert grid.meters_per_unit == 1.0 and 0.5 < grid.resolution_m < 2.0
    assert geo.resolution_m() == pytest.approx(1.0, rel=0.3)


def test_shifted_read_moves_the_content_by_whole_cells(tmp_path):
    a = _open(survey(tmp_path, "a", res=0.5, noise=0, border=0, with_ortho=False))
    grid = build_grid(a, a, a.crs)
    z = read_surface(a, grid)
    shifted = read_surface(a, grid, shift=(4, -6))
    # shifted(row, col) shows what the raster holds at (row - 6, col + 4).
    assert np.allclose(shifted[20:100, 20:100], z[14:94, 24:104], atol=1e-4)


def test_rgb_is_scaled_and_masked_by_alpha(tmp_path):
    s = survey(tmp_path, "a", res=0.5, border=4.0)
    a, o = _open(s), _open(s, "orthophoto")
    grid = build_grid(a, a, a.crs)
    rgb, valid = read_rgb(o, grid)
    assert rgb.shape == (3, grid.height, grid.width) and rgb.dtype == np.float32
    assert 0.0 <= rgb.min() and rgb.max() <= 1.0 and rgb[:, 50, 50].sum() > 0.5
    assert not valid[:8].any() and valid[20:-20, 20:-20].all()


def test_unusable_rasters_are_reported_by_name(tmp_path):
    with pytest.raises(InputError, match="the thing is not a readable raster"):
        Raster(str(tmp_path / "missing.tif"), "the thing")
    plain = str(tmp_path / "plain.tif")
    with rasterio.open(plain, "w", driver="GTiff", width=8, height=8, count=1, dtype="float32") as dst:
        dst.write(np.zeros((1, 8, 8), dtype="float32"))
    with pytest.raises(InputError, match="no coordinate reference system"):
        Raster(plain, "the thing")
    one_band = str(tmp_path / "one.tif")
    with rasterio.open(one_band, "w", driver="GTiff", width=8, height=8, count=1, dtype="uint8",
                       crs="EPSG:32633", transform=from_origin(0, 8, 1, 1)) as dst:
        dst.write(np.zeros((1, 8, 8), dtype="uint8"))
    with pytest.raises(InputError, match="at least 3 bands"):
        Raster(one_band, "orthophoto", min_bands=3)


def test_fixture_matches_its_analytic_ground(tmp_path):
    s = survey(tmp_path, "a", res=0.5, noise=0, border=0, with_ortho=False)
    with rasterio.open(s["dsm"]) as src:
        z = src.read(1)
        x, y = src.xy(100, 100)
    assert z[100, 100] == pytest.approx(float(ground(np.array(x - ORIGIN[0]), np.array(y - ORIGIN[1]))), abs=1e-3)
