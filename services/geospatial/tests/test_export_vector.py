import asyncio
import json
import subprocess

import pytest
from fastapi import HTTPException

from app.routers.export import VectorToGeoJSONRequest, vector_to_geojson


def _geojson(path):
    data = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"level": 10},
            "geometry": {"type": "Point", "coordinates": [500000, 4500000]},
        }],
    }
    path.write_text(json.dumps(data))
    return str(path)


def test_vector_to_geojson_from_gpkg(tmp_path):
    src = _geojson(tmp_path / "src.geojson")
    gpkg = str(tmp_path / "src.gpkg")
    subprocess.run(["ogr2ogr", "-f", "GPKG", gpkg, src], check=True)
    out = tmp_path / "out.geojson"

    res = asyncio.run(vector_to_geojson(
        VectorToGeoJSONRequest(path=gpkg, output_path=str(out))
    ))

    assert out.is_file()
    data = json.loads(out.read_text())
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) == 1
    assert res["format"] == "GeoJSON"
    assert res["crs"] == "EPSG:4326"


def test_vector_to_geojson_missing_is_404(tmp_path):
    req = VectorToGeoJSONRequest(path="/nonexistent/x.gpkg", output_path=str(tmp_path / "o.geojson"))
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(vector_to_geojson(req))
    assert excinfo.value.status_code == 404
