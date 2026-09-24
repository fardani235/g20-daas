"""Header-only raster metadata (``raster.read_metadata`` and ``GET /raster/metadata``).

Covers a normal ODM-style GeoTIFF (tiled, deflate, overviews, alpha), a very
large sparse raster (must stay fast and must not read pixels), missing CRS,
missing / NaN nodata, tiled vs strip layouts, paletted masks, corrupt and
non-raster files, and the cogify endpoint carrying the metadata block.
"""

import asyncio
import json
import os
import time
import tracemalloc

import numpy as np
import pytest
import rasterio
from fastapi import HTTPException
from rasterio.enums import ColorInterp
from rasterio.transform import from_origin

from app.routers.export import CogifyRequest, cogify
from app.routers.raster import raster_metadata
from app.utils import raster


UTM_5CM = from_origin(500000, 4500000, 0.05, 0.05)


def _write(path, data, *, crs="EPSG:32632", transform=UTM_5CM,
           nodata=None, tiled=True, blocksize=256, compress="deflate", overviews=None,
           colorinterp=None, colormap=None, tags=None):
    if data.ndim == 2:
        data = data[np.newaxis, ...]
    count, height, width = data.shape
    profile = dict(
        driver="GTiff", height=height, width=width, count=count, dtype=data.dtype,
        crs=crs, transform=transform, nodata=nodata, compress=compress,
    )
    if tiled:
        profile.update(tiled=True, blockxsize=blocksize, blockysize=blocksize)
    with rasterio.open(path, "w", **profile) as ds:
        ds.write(data)
        if colorinterp:
            ds.colorinterp = colorinterp
        if colormap:
            ds.write_colormap(1, colormap)
        if tags:
            ds.update_tags(**tags)
        if overviews:
            ds.build_overviews(overviews)
    return str(path)


def _rgba(h=300, w=200):
    rng = np.random.default_rng(0)
    return rng.integers(0, 255, size=(4, h, w), dtype="uint8")


# --------------------------------------------------------------------------
# Normal ODM-style orthophoto
# --------------------------------------------------------------------------

def test_orthophoto_geotiff_full_metadata(tmp_path):
    path = _write(
        tmp_path / "ortho.tif", _rgba(), overviews=[2, 4],
        colorinterp=[ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.alpha],
        tags={"TIFFTAG_SOFTWARE": "ODM 3.5.6"},
    )
    m = raster.read_metadata(path)

    assert m["driver"] == "GTiff"
    assert (m["width"], m["height"], m["band_count"]) == (200, 300, 4)
    assert m["dtype"] == "uint8" and m["dtypes"] == ["uint8"] * 4
    assert m["crs"]["epsg"] == 32632
    assert m["crs"]["wkt"].startswith("PROJCRS") or m["crs"]["wkt"].startswith("PROJCS")
    assert m["crs"]["units"] == "metre" and m["crs"]["is_projected"]
    assert m["georeference"] == "full"
    assert m["pixel_size"] == [0.05, 0.05]
    assert m["geotransform"] == [500000.0, 0.05, 0.0, 4500000.0, 0.0, -0.05]
    assert m["bounds"] == [500000.0, 4500000.0 - 300 * 0.05, 500000.0 + 200 * 0.05, 4500000.0]
    w, s, e, n = m["bounds_4326"]
    assert -180 < w < e < 180 and -90 < s < n < 90
    assert m["extent"]["type"] == "Polygon" and len(m["extent"]["coordinates"][0]) == 5
    assert m["nodata"] is None
    assert m["is_tiled"] is True and m["block_size"] == [256, 256]
    assert m["compression"] == "deflate"
    assert m["interleave"] == "pixel"
    assert m["overviews"] == [2, 4] and m["overview_count"] == 2
    assert m["color_interpretation"] == ["red", "green", "blue", "alpha"]
    assert m["has_colormap"] is False
    assert m["file_size"] == os.path.getsize(path)
    assert m["software"] == "ODM 3.5.6"
    assert isinstance(m["is_cog"], bool)
    assert [b["index"] for b in m["bands"]] == [1, 2, 3, 4]
    assert m["bands"][3]["color_interpretation"] == "alpha"
    # The whole thing must be JSON (no NaN sneaking through).
    json.dumps(m, allow_nan=False)


def test_cog_output_is_recognised(tmp_path):
    src = _write(tmp_path / "src.tif", _rgba(600, 700))
    out = raster.to_cog(src, str(tmp_path / "out.tif"))
    m = raster.read_metadata(out)
    assert m["is_cog"] is True
    assert m["is_tiled"] is True
    assert m["overview_count"] >= 1


# --------------------------------------------------------------------------
# Large rasters: header-only, never reads pixels
# --------------------------------------------------------------------------

def test_large_raster_is_fast_and_does_not_read_pixels(tmp_path):
    # 30000 x 30000 x 4 uint8 = 3.6 GB uncompressed; sparse so the file is tiny.
    path = str(tmp_path / "huge.tif")
    with rasterio.open(
        path, "w", driver="GTiff", width=30000, height=30000, count=4, dtype="uint8",
        crs="EPSG:32632", transform=from_origin(500000, 4500000, 0.02, 0.02),
        tiled=True, blockxsize=512, blockysize=512, compress="deflate", sparse_ok=True,
    ):
        pass
    assert os.path.getsize(path) < 5 * 1024 * 1024

    tracemalloc.start()
    t0 = time.perf_counter()
    m = raster.read_metadata(path)
    elapsed = time.perf_counter() - t0
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert (m["width"], m["height"], m["band_count"]) == (30000, 30000, 4)
    assert m["block_size"] == [512, 512]
    assert m["pixel_size"] == [0.02, 0.02]
    assert elapsed < 5.0, f"metadata took {elapsed:.2f}s"
    # Reading even one band would allocate 900 MB; header reads stay tiny.
    assert peak < 50 * 1024 * 1024, f"peak traced memory {peak / 1e6:.1f} MB"


# --------------------------------------------------------------------------
# Georeferencing edge cases
# --------------------------------------------------------------------------

def test_missing_crs_reports_no_crs_and_native_bounds_only(tmp_path):
    path = _write(tmp_path / "nocrs.tif", np.zeros((50, 40), "float32"), crs=None,
                  transform=from_origin(10, 20, 0.5, 0.5))
    m = raster.read_metadata(path)
    assert m["georeference"] == "no_crs"
    assert m["crs"] == {"epsg": None, "wkt": None, "units": None,
                        "is_geographic": False, "is_projected": False}
    assert m["bounds"] == [10.0, 20.0 - 50 * 0.5, 10.0 + 40 * 0.5, 20.0]
    assert m["pixel_size"] == [0.5, 0.5]
    assert m["bounds_4326"] is None and m["extent"] is None


def test_no_georeferencing_at_all(tmp_path):
    path = str(tmp_path / "plain.tif")
    with rasterio.open(path, "w", driver="GTiff", height=8, width=8, count=3, dtype="uint8") as ds:
        ds.write(np.zeros((3, 8, 8), "uint8"))
    m = raster.read_metadata(path)
    assert m["georeference"] == "none"
    assert m["bounds"] is None and m["geotransform"] is None and m["pixel_size"] is None
    assert m["bounds_4326"] is None
    assert m["color_interpretation"] == ["red", "green", "blue"]


def test_crs_without_transform_is_partial(tmp_path):
    path = str(tmp_path / "pixelspace.tif")
    with rasterio.open(path, "w", driver="GTiff", height=8, width=8, count=1, dtype="uint8",
                       crs="EPSG:4326") as ds:
        ds.write(np.zeros((1, 8, 8), "uint8"))
    m = raster.read_metadata(path)
    assert m["georeference"] == "no_transform"
    assert m["crs"]["epsg"] == 4326 and m["crs"]["units"] == "degree"
    assert m["bounds_4326"] is None


def test_geographic_crs_units_are_degrees(tmp_path):
    path = _write(tmp_path / "geo.tif", np.zeros((10, 10), "uint8"), crs="EPSG:4326",
                  transform=from_origin(6.0, 46.0, 0.001, 0.001))
    m = raster.read_metadata(path)
    assert m["crs"]["is_geographic"] and m["crs"]["units"] == "degree"
    assert m["bounds_4326"] == pytest.approx([6.0, 45.99, 6.01, 46.0])


# --------------------------------------------------------------------------
# NoData
# --------------------------------------------------------------------------

def test_dem_nodata_value_is_reported(tmp_path):
    path = _write(tmp_path / "dsm.tif", np.zeros((20, 20), "float32"), nodata=-9999)
    m = raster.read_metadata(path)
    assert m["nodata"] == -9999.0
    assert m["bands"][0]["nodata"] == -9999.0
    assert m["dtype"] == "float32"
    assert m["color_interpretation"] == ["gray"]


def test_nan_nodata_is_json_safe(tmp_path):
    path = _write(tmp_path / "nan.tif", np.zeros((20, 20), "float32"), nodata=float("nan"))
    m = raster.read_metadata(path)
    assert m["nodata"] == "nan"
    json.dumps(m, allow_nan=False)


def test_missing_nodata_is_null(tmp_path):
    path = _write(tmp_path / "nonodata.tif", _rgba(30, 30))
    assert raster.read_metadata(path)["nodata"] is None


# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------

def test_strip_layout_is_not_tiled(tmp_path):
    path = _write(tmp_path / "strips.tif", _rgba(600, 700), tiled=False, compress=None)
    m = raster.read_metadata(path)
    assert m["is_tiled"] is False
    assert m["block_size"][0] == 700  # a strip spans the full width
    assert m["compression"] is None
    assert m["overviews"] == [] and m["overview_count"] == 0


def test_tiled_layout_reports_block_size(tmp_path):
    path = _write(tmp_path / "tiled.tif", _rgba(600, 700), tiled=True, blocksize=128, compress="lzw")
    m = raster.read_metadata(path)
    assert m["is_tiled"] is True
    assert m["block_size"] == [128, 128]
    assert m["compression"] == "lzw"


def test_paletted_mask_has_colormap(tmp_path):
    path = _write(tmp_path / "mask.tif", np.zeros((16, 16), "uint8"),
                  colormap={0: (0, 0, 0, 255), 1: (255, 0, 0, 255)})
    m = raster.read_metadata(path)
    assert m["color_interpretation"] == ["palette"]
    assert m["has_colormap"] is True


# --------------------------------------------------------------------------
# Failures
# --------------------------------------------------------------------------

def test_missing_file_raises(tmp_path):
    with pytest.raises(raster.RasterMetadataError):
        raster.read_metadata(str(tmp_path / "nope.tif"))


def test_corrupt_tiff_raises(tmp_path):
    path = tmp_path / "bad.tif"
    path.write_bytes(b"II*\x00" + b"\xff" * 64)
    with pytest.raises(raster.RasterMetadataError):
        raster.read_metadata(str(path))


def test_non_raster_file_raises(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("this is not a raster")
    with pytest.raises(raster.RasterMetadataError):
        raster.read_metadata(str(path))


def test_truncated_geotiff_still_yields_header_metadata(tmp_path):
    # A GeoTIFF cut off mid-pixel-data: the header is intact so metadata
    # extraction succeeds; only pixel reads would fail. Header-first layout
    # is what GDAL writes by default for classic (non-COG) TIFFs.
    full = _write(tmp_path / "full.tif", _rgba(400, 400), tiled=False, compress=None)
    data = open(full, "rb").read()
    cut = tmp_path / "cut.tif"
    cut.write_bytes(data[: len(data) // 2])
    m = raster.read_metadata(str(cut))
    assert (m["width"], m["height"]) == (400, 400)
    assert m["file_size"] == len(data) // 2


# --------------------------------------------------------------------------
# Routers
# --------------------------------------------------------------------------

def test_metadata_endpoint_returns_metadata(tmp_path):
    path = _write(tmp_path / "ortho.tif", _rgba(), nodata=None)
    out = asyncio.run(raster_metadata(path=path))
    assert out["width"] == 200 and out["crs"]["epsg"] == 32632


def test_metadata_endpoint_rejects_relative_path():
    with pytest.raises(HTTPException) as e:
        asyncio.run(raster_metadata(path="relative/ortho.tif"))
    assert e.value.status_code == 400


def test_metadata_endpoint_missing_is_404(tmp_path):
    with pytest.raises(HTTPException) as e:
        asyncio.run(raster_metadata(path=str(tmp_path / "missing.tif")))
    assert e.value.status_code == 404


def test_metadata_endpoint_corrupt_is_422(tmp_path):
    bad = tmp_path / "bad.tif"
    bad.write_bytes(b"not a tiff at all")
    with pytest.raises(HTTPException) as e:
        asyncio.run(raster_metadata(path=str(bad)))
    assert e.value.status_code == 422


def test_metadata_endpoint_missing_crs_is_not_an_error(tmp_path):
    path = _write(tmp_path / "nocrs.tif", np.zeros((10, 10), "uint8"), crs=None)
    out = asyncio.run(raster_metadata(path=path))
    assert out["georeference"] == "no_crs"


def test_cogify_response_carries_metadata(tmp_path):
    src = _write(tmp_path / "src.tif", _rgba(600, 700), tiled=False, compress=None)
    out = asyncio.run(cogify(CogifyRequest(path=src)))
    assert out["is_cog"] is True
    meta = out["metadata"]
    assert meta is not None
    assert meta["is_cog"] is True and meta["is_tiled"] is True
    assert meta["path"] == out["path"]
    assert meta["crs"]["epsg"] == out["epsg"] == 32632
    assert meta["bounds_4326"] == out["bounds_4326"]
