import numpy as np
import pytest

from recon3d import sources
from recon3d.errors import InputError, ParameterError
from recon3d.params import PRESETS, Params


def test_presets_and_overrides():
    p = Params.from_request({})
    assert p.quality == "balanced" and p.effective()["max_triangles"] == PRESETS["balanced"]["max_triangles"]
    p = Params.from_request({"quality": "high-detail", "texture_size": 1024, "quantize": "false", "texture_tiles": 4})
    eff = p.effective()
    assert eff["texture_size"] == 1024 and eff["max_texture_tiles"] == 4 and eff["texture_tiles_fixed"]
    assert p.quantize is False
    assert Params.from_request({"max_error_m": "0.25"}).max_error_m == 0.25


@pytest.mark.parametrize("bad", [
    {"quality": "ultra"}, {"workflow": "magic"}, {"texture_tiles": 3}, {"texture_size": 100},
    {"resolution_m": -1}, {"texture_quality": 101}, {"point_cloud_stat": "median"}, {"max_triangles": "lots"},
])
def test_invalid_params(bad):
    with pytest.raises(ParameterError):
        Params.from_request(bad)


def test_detect_describes_every_kind(dsm, dtm, orthophoto, las, odm_glb, tmp_path):
    found = sources.detect({"dsm": dsm, "dtm": dtm, "orthophoto": orthophoto, "point_cloud": las,
                            "model": odm_glb, "bogus": str(tmp_path / "missing.tif")})
    assert found.available() == ["dsm", "dtm", "orthophoto", "point_cloud", "model"]
    assert found.dsm.res == pytest.approx(0.4) and found.orthophoto.has_alpha and found.orthophoto.count == 4
    assert found.point_cloud.has_rgb and found.point_cloud.crs.to_epsg() == 32633
    assert found.point_cloud.count == 160 * 160
    assert found.model.format == "glb"
    assert any("bogus" in n for n in found.notes)


def test_bad_inputs_raise_clear_errors(tmp_path):
    (tmp_path / "junk.glb").write_bytes(b"nope")
    with pytest.raises(InputError, match="not a GLB"):
        sources.describe_model("model", str(tmp_path / "junk.glb"))
    (tmp_path / "junk.tif").write_bytes(b"nope")
    with pytest.raises(InputError, match="cannot open raster"):
        sources.open_raster("dsm", str(tmp_path / "junk.tif"))
    (tmp_path / "junk.las").write_bytes(b"nope")
    pc = sources.describe_point_cloud("point_cloud", str(tmp_path / "junk.las"))
    assert pc.error


def test_working_crs_and_utm_fallback():
    from rasterio.crs import CRS
    assert sources.utm_crs_for(15.0, 40.6).to_epsg() == 32633
    assert sources.utm_crs_for(-70.0, -33.0).to_epsg() == 32719
    assert sources.working_crs([CRS.from_epsg(4326), CRS.from_epsg(32633)]).to_epsg() == 32633
    assert sources.working_crs([(CRS.from_epsg(4326), (15.0, 40.0, 15.1, 40.1))]).to_epsg() == 32633
    assert sources.intersection((0, 0, 10, 10), (5, 5, 20, 20)) == (5, 5, 10, 10)
    assert sources.intersection((0, 0, 10, 10), (11, 11, 20, 20)) is None


def test_point_cloud_rasterize_stats(las):
    from rasterio.crs import CRS

    from recon3d import pointcloud
    from recon3d.grid import Grid
    src = pointcloud.describe("point_cloud", las)
    grid = Grid(CRS.from_epsg(32633), 500000.0, 4500000.0, 1.0, 41, 41)
    z_max, rgb = pointcloud.rasterize(src, grid, stat="max")
    z_mean, _ = pointcloud.rasterize(src, grid, stat="mean", want_rgb=False)
    assert rgb.shape == (3, 41, 41)
    assert np.nanmax(z_max) == pytest.approx(108.0) and np.nanmin(z_max) == pytest.approx(100.0)
    assert np.nanmean(z_mean) <= np.nanmean(z_max) + 1e-6
    # roof colour (red) arrives where the building is
    assert rgb[0, 15, 16] > 150 and rgb[1, 15, 16] < 80
    assert pointcloud.suggested_cell(src, 40.0, 2049) == pytest.approx(0.25 * np.sqrt(2), rel=0.05)
