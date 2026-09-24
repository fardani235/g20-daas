"""``raster.read_metadata`` and the ``/raster/metadata`` + ``/export/cogify``
endpoints: normalized header metadata for normal, huge, CRS-less, nodata-less,
tiled/striped and broken rasters — never reading pixel data."""

import asyncio
import json
import os
import time

import numpy as np
import pytest
import rasterio
from fastapi import HTTPException
from rasterio.crs import CRS
from rasterio.errors import RasterioIOError
from rasterio.transform import from_origin

from app.routers.export import CogifyRequest, cogify
from app.routers.raster import raster_metadata
from app.utils import raster as raster_utils

UTM_ORIGIN = (500000.0, 5000000.0)


def _write(path, shape=(3, 64, 64), dtype="uint8", crs="EPSG:32633", transform="utm",
           nodata=None, tiled=True, blocksize=32, compress="deflate", overviews=(),
           sparse=False, colormap=None, res=0.05):
    count, height, width = shape
    if transform == "utm":
        transform = from_origin(UTM_ORIGIN[0], UTM_ORIGIN[1], res, res)
    opts = {"driver": "GTiff", "height": height, "width": width, "count": count, "dtype": dtype}
    if crs is not None:
        opts["crs"] = crs
    if transform is not None:
        opts["transform"] = transform
    if nodata is not None:
        opts["nodata"] = nodata
    if compress:
        opts["compress"] = compress
    if tiled:
        opts.update(tiled=True, blockxsize=blocksize, blockysize=blocksize)
    if sparse:
        opts["sparse_ok"] = True
    with rasterio.open(path, "w", **opts) as ds:
        if not sparse:
            ds.write(np.zeros(shape, dtype=dtype))
        if colormap:
            ds.write_colormap(1, colormap)
        if overviews:
            ds.build_overviews(list(overviews), rasterio.enums.Resampling.nearest)
    return str(path)


def test_normal_tiled_geotiff_reports_every_field(tmp_path):
    path = _write(tmp_path / "ortho.tif", nodata=0, overviews=(2, 4))
    meta = raster_utils.read_metadata(path)

    assert meta["driver"] == "GTiff"
    assert meta["file_size"] == os.path.getsize(path) > 0
    assert (meta["width"], meta["height"], meta["band_count"]) == (64, 64, 3)
    assert meta["dtype"] == "uint8"
    assert meta["epsg"] == 32633
    assert meta["crs_wkt"] is None            # WKT only when there is no EPSG code
    assert meta["crs_units"] == "metre"
    assert meta["is_georeferenced"] is True
    assert meta["geotransform"] == [500000.0, 0.05, 0.0, 5000000.0, 0.0, -0.05]
    assert meta["pixel_size"] == [0.05, 0.05]
    assert meta["bounds"] == [500000.0, 5000000.0 - 64 * 0.05, 500000.0 + 64 * 0.05, 5000000.0]
    minx, miny, maxx, maxy = meta["bounds_4326"]
    assert 14 < minx < maxx < 16 and 44 < miny < maxy < 46   # UTM 33N, 500 km E / 5000 km N
    assert meta["extent"]["type"] == "Polygon"
    assert meta["extent"]["coordinates"][0][0] == [minx, miny]
    assert meta["nodata"] == 0.0
    assert (meta["block_width"], meta["block_height"], meta["is_tiled"]) == (32, 32, True)
    assert meta["compression"] == "deflate"
    assert meta["overviews"] == [2, 4]
    assert meta["color_interp"] == ["red", "green", "blue"]
    assert isinstance(meta["is_cog"], bool)
    # Everything must survive strict JSON (the transport to Frappe).
    json.loads(json.dumps(meta, allow_nan=False))


def test_striped_uncompressed_raster_is_not_tiled(tmp_path):
    # Larger than one 512px tile so rio-cogeo actually requires tiling/overviews.
    path = _write(tmp_path / "strips.tif", shape=(3, 1024, 1024), tiled=False, compress=None)
    meta = raster_utils.read_metadata(path)
    assert meta["is_tiled"] is False
    assert meta["block_width"] == 1024          # strips span the full width
    assert meta["block_height"] < 1024
    assert meta["compression"] is None
    assert meta["overviews"] == []
    assert meta["is_cog"] is False


def test_missing_crs_keeps_native_bounds_but_no_4326(tmp_path):
    # Partially georeferenced: a geotransform but no CRS.
    path = _write(tmp_path / "nocrs.tif", crs=None)
    meta = raster_utils.read_metadata(path)
    assert meta["epsg"] is None and meta["crs_wkt"] is None and meta["crs_units"] is None
    assert meta["is_georeferenced"] is False
    assert meta["geotransform"] is not None
    assert meta["pixel_size"] == [0.05, 0.05]
    assert meta["bounds"] is not None
    assert meta["bounds_4326"] is None and meta["extent"] is None


def test_ungeoreferenced_raster_has_no_transform_fields(tmp_path):
    path = _write(tmp_path / "plain.tif", crs=None, transform=None, compress=None, tiled=False)
    meta = raster_utils.read_metadata(path)
    assert meta["is_georeferenced"] is False
    assert meta["geotransform"] is None
    assert meta["pixel_size"] is None
    assert meta["bounds"] is None
    assert meta["bounds_4326"] is None
    assert (meta["width"], meta["height"]) == (64, 64)   # header fields still present


def test_wkt_only_crs_is_reported_as_wkt(tmp_path):
    # A custom projection (no EPSG code): Transverse Mercator with a non-standard meridian.
    custom = CRS.from_proj4("+proj=tmerc +lat_0=0 +lon_0=15.5 +k=0.9996 +x_0=500000 +y_0=0 +datum=WGS84 +units=m +no_defs")
    path = _write(tmp_path / "custom.tif", crs=custom)
    meta = raster_utils.read_metadata(path)
    assert meta["epsg"] is None
    assert meta["crs_wkt"] and "Transverse_Mercator" in meta["crs_wkt"]
    assert meta["is_georeferenced"] is True
    assert meta["bounds_4326"] is not None      # still reprojectable


def test_missing_nodata_is_none_and_nan_nodata_is_json_safe(tmp_path):
    meta = raster_utils.read_metadata(_write(tmp_path / "nonodata.tif"))
    assert meta["nodata"] is None

    meta = raster_utils.read_metadata(
        _write(tmp_path / "dem.tif", shape=(1, 32, 32), dtype="float32", nodata=float("nan"))
    )
    assert meta["nodata"] == "nan"
    assert meta["color_interp"] == ["gray"]
    json.dumps(meta, allow_nan=False)

    meta = raster_utils.read_metadata(
        _write(tmp_path / "dem2.tif", shape=(1, 32, 32), dtype="float32", nodata=-9999)
    )
    assert meta["nodata"] == -9999.0
    assert meta["dtype"] == "float32"


def test_paletted_mask_reports_palette_interpretation(tmp_path):
    path = _write(tmp_path / "mask.tif", shape=(1, 16, 16), colormap={0: (0, 0, 0, 255), 1: (255, 0, 0, 255)})
    assert raster_utils.read_metadata(path)["color_interp"] == ["palette"]


def test_large_raster_reads_header_only(tmp_path):
    # 40k x 40k x 3 = 4.8 GB of pixels, written sparse so the file is tiny; any
    # implementation that touched pixel data would take seconds and gigabytes.
    path = _write(tmp_path / "huge.tif", shape=(3, 40000, 40000), sparse=True, blocksize=512)
    assert os.path.getsize(path) < 5 * 1024 * 1024

    t0 = time.monotonic()
    meta = raster_utils.read_metadata(path)
    elapsed = time.monotonic() - t0

    assert (meta["width"], meta["height"], meta["band_count"]) == (40000, 40000, 3)
    assert (meta["block_width"], meta["block_height"]) == (512, 512)
    assert meta["bounds"][2] - meta["bounds"][0] == pytest.approx(40000 * 0.05)
    assert elapsed < 2.0, f"metadata read took {elapsed:.2f}s"


def test_corrupt_file_raises_rasterio_error(tmp_path):
    bad = tmp_path / "bad.tif"
    bad.write_bytes(b"II*\x00" + b"\xff" * 64)   # TIFF magic, garbage IFD
    with pytest.raises((RasterioIOError, OSError)):
        raster_utils.read_metadata(str(bad))

    text = tmp_path / "notes.txt"
    text.write_text("not a raster")
    with pytest.raises((RasterioIOError, OSError)):
        raster_utils.read_metadata(str(text))


def test_truncated_geotiff_still_yields_header_metadata(tmp_path):
    # A download cut short keeps its IFD (written at the front by GDAL) but
    # loses pixel data; header metadata is still readable.
    path = _write(tmp_path / "full.tif", shape=(3, 256, 256), compress=None, tiled=False)
    with open(path, "rb") as fh:
        data = fh.read()
    cut = tmp_path / "cut.tif"
    cut.write_bytes(data[: len(data) // 2])
    meta = raster_utils.read_metadata(str(cut))
    assert (meta["width"], meta["height"]) == (256, 256)
    assert meta["file_size"] == len(data) // 2


# --- HTTP layer --------------------------------------------------------------

def test_metadata_endpoint_returns_normalized_dict(tmp_path):
    path = _write(tmp_path / "ortho.tif", nodata=0)
    res = asyncio.run(raster_metadata(path=path))
    assert res["epsg"] == 32633 and res["band_count"] == 3 and res["nodata"] == 0.0


def test_metadata_endpoint_rejects_bad_paths(tmp_path):
    with pytest.raises(HTTPException) as e:
        asyncio.run(raster_metadata(path="relative/ortho.tif"))
    assert e.value.status_code == 400

    with pytest.raises(HTTPException) as e:
        asyncio.run(raster_metadata(path=str(tmp_path / "missing.tif")))
    assert e.value.status_code == 404

    bad = tmp_path / "bad.tif"
    bad.write_bytes(b"II*\x00" + b"\xff" * 64)
    with pytest.raises(HTTPException) as e:
        asyncio.run(raster_metadata(path=str(bad)))
    assert e.value.status_code == 422
    assert "not a readable raster" in e.value.detail


def test_cogify_response_includes_metadata_of_the_cog(tmp_path):
    path = _write(tmp_path / "ortho.tif", shape=(3, 1024, 1024), tiled=False, compress=None, nodata=0)
    res = asyncio.run(cogify(CogifyRequest(path=path)))
    meta = res["metadata"]
    assert res["epsg"] == meta["epsg"] == 32633
    assert res["extent"] == meta["extent"]
    assert meta["is_cog"] is True
    assert meta["is_tiled"] is True
    assert meta["overviews"], "COG conversion must have built overviews"
    assert meta["compression"] == "deflate"
    assert meta["nodata"] == 0.0
    assert meta["file_size"] == os.path.getsize(path)
