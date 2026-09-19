import json
import os
import subprocess
import sys

import numpy as np
import rasterio
from rasterio.transform import from_origin

from app.analysis.ops import hillshade
from app.analysis.ops.contours import ContoursParams, run_contours
from app.analysis.ops.hillshade import HillshadeParams, run_hillshade
from app.utils import raster

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _hilly_dem(path, size=60, nodata=-9999.0):
    """A non-trivial DEM with a nodata hole that straddles a small block seam."""
    yy, xx = np.mgrid[0:size, 0:size]
    arr = (
        1000.0
        + 50.0 * np.sin(xx / 5.0)
        + 30.0 * np.cos(yy / 4.0)
        + 2.0 * xx
    ).astype("float32")
    arr[15, 16] = nodata
    tr = from_origin(500000, 4500000, 5.0, 5.0)
    with rasterio.open(
        path, "w", driver="GTiff", height=size, width=size, count=1,
        dtype="float32", crs="EPSG:32615", transform=tr, nodata=nodata,
    ) as ds:
        ds.write(arr, 1)
    return str(path)


def _reference_hillshade(src):
    """Whole-raster hillshade, independent of the production blocking logic."""
    with rasterio.open(src) as ds:
        band = ds.read(1).astype("float64")
        cellsize = abs(ds.transform.a) or 1.0
        nodata = ds.nodata
    if nodata is not None:
        band = np.where(band == nodata, np.nan, band)
    x, y = np.gradient(band, cellsize)
    slope = np.pi / 2.0 - np.arctan(np.sqrt(x * x + y * y))
    aspect = np.arctan2(-x, y)
    az = np.radians(360.0 - 315.0 + 90.0)
    alt = np.radians(45.0)
    shaded = (
        np.sin(alt) * np.sin(slope)
        + np.cos(alt) * np.cos(slope) * np.cos((az - np.pi / 2.0) - aspect)
    )
    shaded = np.nan_to_num(shaded, nan=0.0)
    return ((shaded + 1.0) * 127.5).clip(0, 255).astype("uint8")


def _dem(path):
    arr = np.full((60, 60), 100.0, dtype="float32")
    arr[10:30, 10:40] = 130.0
    arr[30:50, 20:50] = 160.0
    tr = from_origin(500000, 4500000, 5.0, 5.0)
    with rasterio.open(
        path, "w", driver="GTiff", height=60, width=60, count=1,
        dtype="float32", crs="EPSG:32615", transform=tr, nodata=-9999,
    ) as ds:
        ds.write(arr, 1)
    return str(path)


def test_contours_produces_features_and_extent(tmp_path):
    src = _dem(tmp_path / "d.tif")
    out = tmp_path / "contours.geojson"

    result = run_contours({"raster": src}, ContoursParams(interval_m=10.0), str(out))

    assert out.is_file()
    data = json.loads(out.read_text())
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) > 0
    assert result["metadata"]["feature_count"] == len(data["features"])
    assert result["metadata"]["crs"] == "EPSG:4326"

    # Coordinates must be reprojected to lon/lat, not left in UTM metres.
    lon, lat = data["features"][0]["geometry"]["coordinates"][0][:2]
    assert -180 <= lon <= 180
    assert -90 <= lat <= 90


def test_contours_simplify_writes_geojson(tmp_path):
    src = _dem(tmp_path / "d.tif")
    out = tmp_path / "contours.geojson"
    result = run_contours(
        {"raster": src},
        ContoursParams(interval_m=10.0, simplify_m=2.0),
        str(out),
    )
    assert out.is_file()
    assert result["metadata"]["simplify_m"] == 2.0


def test_hillshade_outputs_cog_with_georef(tmp_path):
    src = _dem(tmp_path / "d.tif")
    out = tmp_path / "hillshade.tif"

    result = run_hillshade({"raster": src}, HillshadeParams(), str(out))

    assert out.is_file()
    assert raster.is_cog(str(out))
    with rasterio.open(out) as ds:
        assert ds.count == 1
        assert ds.dtypes[0] == "uint8"
        assert ds.crs is not None

    georef = raster.read_georef(str(out))
    assert georef["extent"]["type"] == "Polygon"
    assert georef["epsg"] == 32615
    assert result["output_path"] == str(out)


def test_hillshade_block_size_does_not_change_output(tmp_path, monkeypatch):
    src = _hilly_dem(tmp_path / "hilly.tif")
    expected = _reference_hillshade(src)

    # A block far smaller than the raster forces many blocks and seams; the
    # output must be identical to a whole-raster computation.
    monkeypatch.setattr(hillshade, "BLOCK_SIZE", 8, raising=True)
    out = tmp_path / "blocked.tif"
    run_hillshade({"raster": src}, HillshadeParams(), str(out))

    with rasterio.open(out) as ds:
        got = ds.read(1)
    assert got.shape == expected.shape
    np.testing.assert_array_equal(got, expected)


def test_hillshade_memory_is_bounded(tmp_path):
    """A DEM much larger than one block must not materialize in full in RAM."""
    size = 3000
    src = tmp_path / "big.tif"
    arr = np.full((size, size), 100.0, dtype="float32")
    with rasterio.open(
        src, "w", driver="GTiff", height=size, width=size, count=1,
        dtype="float32", crs="EPSG:32615",
        transform=from_origin(500000, 4500000, 5.0, 5.0),
    ) as ds:
        ds.write(arr, 1)

    out = tmp_path / "big_hillshade.tif"
    script = (
        "import resource;"
        "from app.analysis.ops.hillshade import HillshadeParams, run_hillshade;"
        f"run_hillshade({{'raster': {str(src)!r}}}, HillshadeParams(), {str(out)!r});"
        "print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    peak_mib = int(proc.stdout.strip()) / 1024.0
    assert peak_mib < 400, f"peak RSS {peak_mib:.0f} MiB for a {size}x{size} DEM"
