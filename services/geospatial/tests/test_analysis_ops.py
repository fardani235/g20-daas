import json

import numpy as np
import rasterio
from rasterio.transform import from_origin

from app.analysis.ops.contours import ContoursParams, run_contours
from app.analysis.ops.hillshade import HillshadeParams, run_hillshade
from app.utils import raster


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
