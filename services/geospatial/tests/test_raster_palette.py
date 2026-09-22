"""Paletted single-band rasters (classification masks) render with their own
colour table instead of the terrain stretch."""

import io

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.utils import raster as raster_utils

PIL = pytest.importorskip("PIL.Image")


def _write(path, data, colormap=None, nodata=None):
    with rasterio.open(
        path, "w", driver="GTiff", height=data.shape[0], width=data.shape[1], count=1,
        dtype=data.dtype, crs="EPSG:3857", nodata=nodata,
        # 3857 so the web-mercator tile maps 1:1 onto the raster without warping.
        transform=from_origin(-20037508.34, 20037508.34, 20037508.34 * 2 / data.shape[1],
                              20037508.34 * 2 / data.shape[0]),
    ) as ds:
        ds.write(data, 1)
        if colormap:
            ds.write_colormap(1, colormap)
    return str(path)


def _pixels(png: bytes):
    img = PIL.open(io.BytesIO(png)).convert("RGBA")
    return np.asarray(img)


def test_paletted_mask_uses_its_colour_table(tmp_path):
    mask = np.zeros((64, 64), dtype="uint8")
    mask[:, 32:] = 2
    mask[:8, :8] = 255  # nodata
    path = _write(tmp_path / "mask.tif", mask,
                  colormap={0: (10, 20, 30, 255), 2: (200, 100, 0, 255)}, nodata=255)

    px = _pixels(raster_utils.render_tile(path, 0, 0, 0, kind="dem"))
    assert tuple(px[128, 64][:3]) == (10, 20, 30)      # class 0, left half
    assert tuple(px[128, 192][:3]) == (200, 100, 0)    # class 2, right half
    assert px[4, 4][3] == 0                            # nodata is transparent
    # Only values present in the raster/palette are used; nothing is stretched.
    assert not {tuple(p) for p in px[:, :, :3].reshape(-1, 3)} - {(10, 20, 30), (200, 100, 0), (0, 0, 0)}


def test_unpaletted_single_band_still_uses_terrain_ramp(tmp_path):
    dem = np.linspace(0, 100, 64 * 64, dtype="float32").reshape(64, 64)
    path = _write(tmp_path / "dem.tif", dem)
    px = _pixels(raster_utils.render_tile(path, 0, 0, 0, kind="dem"))
    # A stretched ramp has many distinct colours; a palette would have few.
    assert len({tuple(p) for p in px[:, :, :3].reshape(-1, 3)}) > 20
