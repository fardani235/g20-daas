import asyncio

import numpy as np
import pytest
import rasterio
from fastapi import HTTPException
from rasterio.transform import from_origin
from rasterio.warp import transform_geom

from app.routers.volume import volume, VolumeRequest


def _dsm(tmp_path):
    elev = np.full((50, 50), 10.0)
    elev[20:30, 20:30] = 13.0
    tr = from_origin(500000, 4500000, 1.0, 1.0)
    path = str(tmp_path / "d.tif")
    with rasterio.open(
        path, "w", driver="GTiff", height=50, width=50, count=1,
        dtype="float32", crs="EPSG:32615", transform=tr, nodata=-9999,
    ) as ds:
        ds.write(elev.astype("float32"), 1)
    return path


def test_volume_endpoint_returns_expected_keys(tmp_path):
    path = _dsm(tmp_path)
    geom_utm = {"type": "Polygon", "coordinates": [[
        [500010, 4499990], [500040, 4499990],
        [500040, 4499960], [500010, 4499960], [500010, 4499990],
    ]]}
    poly = transform_geom("EPSG:32615", "EPSG:4326", geom_utm)
    res = asyncio.run(volume(VolumeRequest(path=path, polygon=poly)))
    assert set(res) == {"volume", "fill", "cut", "area", "base_plane"}


def test_volume_endpoint_missing_file_is_404():
    req = VolumeRequest(
        path="/nonexistent/x.tif",
        polygon={"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]},
    )
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(volume(req))
    assert excinfo.value.status_code == 404


def _square_poly():
    geom_utm = {"type": "Polygon", "coordinates": [[
        [500010, 4499990], [500040, 4499990],
        [500040, 4499960], [500010, 4499960], [500010, 4499990],
    ]]}
    return transform_geom("EPSG:32615", "EPSG:4326", geom_utm)


def test_volume_endpoint_defaults_to_triangulate(tmp_path):
    res = asyncio.run(volume(VolumeRequest(path=_dsm(tmp_path), polygon=_square_poly())))
    assert res["base_plane"] == "triangulate"


def test_volume_endpoint_honours_requested_method(tmp_path):
    req = VolumeRequest(path=_dsm(tmp_path), polygon=_square_poly(), method="plane")
    res = asyncio.run(volume(req))
    assert res["base_plane"] == "plane"


def test_volume_endpoint_rejects_unknown_method_with_422(tmp_path):
    req = VolumeRequest(path=_dsm(tmp_path), polygon=_square_poly(), method="bogus")
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(volume(req))
    assert excinfo.value.status_code == 422
    assert "base method" in str(excinfo.value.detail)


def test_volume_endpoint_treats_blank_method_as_default(tmp_path):
    """WebODM's serializer allows a blank method; treat it as the default."""
    req = VolumeRequest(path=_dsm(tmp_path), polygon=_square_poly(), method="")
    res = asyncio.run(volume(req))
    assert res["base_plane"] == "triangulate"
