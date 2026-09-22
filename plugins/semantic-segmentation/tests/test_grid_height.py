import numpy as np
import pytest
import rasterio
from rasterio.enums import Resampling
from rasterio.windows import Window

from segplugin import grid as grid_mod
from segplugin.errors import InputError
from segplugin.height import HeightProvider, estimate_ground
from tests.conftest import reproject_to, write_dsm, write_dtm, write_orthophoto


def test_grid_uses_finest_input_and_intersection(scene):
    inputs = grid_mod.open_inputs(scene)
    g = grid_mod.build_grid(inputs, ["orthophoto", "dsm", "dtm"], None)
    assert g.crs.to_epsg() == 32633
    assert g.resolution_m == pytest.approx(0.2)         # orthophoto is finest
    assert (g.width, g.height) == (200, 200)
    warnings = []
    g2 = grid_mod.build_grid(inputs, ["orthophoto"], 0.5, warn=warnings.append)
    assert g2.resolution_m == pytest.approx(0.5) and (g2.width, g2.height) == (80, 80)
    g3 = grid_mod.build_grid(inputs, ["dsm"], 0.1, warn=warnings.append)
    assert g3.resolution_m == pytest.approx(0.4)        # never finer than native
    assert any("finer" in w for w in warnings)
    for r in inputs.values():
        r.close()


def test_pixel_budget_coarsens_with_a_warning(scene):
    inputs = grid_mod.open_inputs(scene)
    warnings = []
    g = grid_mod.build_grid(inputs, ["orthophoto"], None, max_pixels=10_000, warn=warnings.append)
    assert g.pixels <= 10_100 and any("coarsened" in w for w in warnings)
    for r in inputs.values():
        r.close()


def test_inputs_in_different_crs_are_aligned(tmp_path, scene):
    dsm_4326 = reproject_to(scene["dsm"], tmp_path / "dsm4326.tif")
    inputs = grid_mod.open_inputs({"orthophoto": scene["orthophoto"], "dsm": dsm_4326})
    g = grid_mod.build_grid(inputs, ["orthophoto"], 0.5)
    assert g.crs.to_epsg() == 32633
    reader = grid_mod.AlignedReader(inputs["dsm"], g, Resampling.bilinear)
    z = reader.read_elevation(Window(0, 0, g.width, g.height))
    assert z.shape == (g.height, g.width)
    # Building at x 10-22 m, y 10-20 m -> 0.5 m cells (20..44, 20..40) are ~108 m.
    assert z[30, 30] == pytest.approx(108.0, abs=0.6)
    assert z[70, 10] == pytest.approx(100.0, abs=0.3)
    reader.close()
    for r in inputs.values():
        r.close()


def test_non_overlapping_inputs_rejected(tmp_path, scene):
    far = write_dtm(tmp_path / "far.tif")
    with rasterio.open(far, "r+") as ds:
        ds.transform = rasterio.Affine(0.5, 0, 600000.0, 0, -0.5, 4600000.0)
    inputs = grid_mod.open_inputs({"orthophoto": scene["orthophoto"], "dtm": far})
    with pytest.raises(InputError, match="do not overlap"):
        grid_mod.build_grid(inputs, ["orthophoto", "dtm"], None)
    for r in inputs.values():
        r.close()


def test_invalid_inputs_rejected(tmp_path, scene):
    (tmp_path / "junk.tif").write_bytes(b"not a tiff")
    with pytest.raises(InputError, match="not a readable raster"):
        grid_mod.open_inputs({"dsm": str(tmp_path / "junk.tif")})
    with pytest.raises(InputError, match="at least 3 bands"):
        grid_mod.open_inputs({"orthophoto": scene["dsm"]})
    with pytest.raises(InputError, match="single-band"):
        grid_mod.open_inputs({"dsm": scene["orthophoto"]})
    with pytest.raises(InputError, match="No input datasets"):
        grid_mod.open_inputs({})
    nocrs = tmp_path / "nocrs.tif"
    with rasterio.open(nocrs, "w", driver="GTiff", width=4, height=4, count=1, dtype="float32") as ds:
        ds.write(np.zeros((4, 4), "float32"), 1)
    with pytest.raises(InputError, match="coordinate reference"):
        grid_mod.open_inputs({"dtm": str(nocrs)})


def test_rgb_reader_scales_and_masks(scene):
    inputs = grid_mod.open_inputs(scene)
    g = grid_mod.build_grid(inputs, ["orthophoto"], None)
    reader = grid_mod.AlignedReader(inputs["orthophoto"], g, Resampling.bilinear)
    rgb, valid = reader.read_rgb(Window(0, 0, 40, 40))
    assert rgb.shape == (3, 40, 40) and rgb.max() <= 1.0
    assert not valid[:, :10].any() and valid[:, 20:].all()     # alpha strip is nodata
    assert rgb[1, 20, 20] == pytest.approx(150 / 255, abs=1e-3)
    reader.close()
    for r in inputs.values():
        r.close()


def test_height_provider_exact_and_estimated(scene):
    inputs = grid_mod.open_inputs(scene)
    g = grid_mod.build_grid(inputs, ["dsm"], 0.4)
    exact = HeightProvider(inputs, g)
    assert exact.mode == "dsm-dtm"
    h = exact.ndsm(Window(0, 0, g.width, g.height))
    assert h[37, 40] == pytest.approx(8.0, abs=0.3)      # building
    assert h[90, 10] == pytest.approx(0.0, abs=0.3)      # ground
    exact.close()

    warnings = []
    est = HeightProvider({"dsm": inputs["dsm"]}, g, ground_window_m=16, warn=warnings.append)
    assert est.mode == "dsm-estimated-ground" and warnings
    h2 = est.ndsm(Window(0, 0, g.width, g.height))
    assert h2[37, 40] == pytest.approx(8.0, abs=0.5)
    assert abs(h2[90, 10]) < 0.3
    est.close()

    none = HeightProvider({"dtm": inputs["dtm"]}, g)
    assert not none.available
    for r in inputs.values():
        r.close()


def test_estimate_ground_removes_objects_keeps_terrain():
    n = 100
    x = np.arange(n, dtype="float32")[None, :].repeat(n, 0)
    terrain = 0.05 * x                       # gentle slope
    dsm = terrain.copy()
    dsm[40:50, 40:50] += 9.0                 # 10-cell building
    dsm[10, 10] = np.nan
    ground = estimate_ground(dsm, 25)
    assert ground[45, 45] == pytest.approx(terrain[45, 45], abs=0.05 * 25)
    assert np.isnan(ground[10, 10])
    assert (np.nan_to_num(ground, nan=0) <= np.nan_to_num(dsm, nan=0) + 1e-6).all()
