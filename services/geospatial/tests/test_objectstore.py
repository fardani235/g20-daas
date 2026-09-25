"""Object storage: rasters read straight from S3 (range reads through
``/vsis3/``), COGs written S3 -> S3, and the path gates in the routers.

Runs against moto in *server* mode so GDAL's curl-based reader talks to a
real HTTP endpoint, exactly like MinIO or AWS would be talked to in
production. Skipped if the server cannot be started.
"""

import asyncio
import os
import socket

import boto3
import numpy as np
import pytest
import rasterio
from fastapi import HTTPException
from rasterio.transform import from_origin

from app.routers.export import CogifyRequest, cogify
from app.routers.raster import raster_metadata
from app.routers.tiles import _require_raster
from app.utils import objectstore, raster

moto_server = pytest.importorskip("moto.server")

BUCKET = "webodm-test"
OTHER_BUCKET = "someone-else"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def s3(tmp_path_factory):
    port = _free_port()
    server = moto_server.ThreadedMotoServer(ip_address="127.0.0.1", port=port)
    server.start()
    endpoint = f"http://127.0.0.1:{port}"
    env = {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_REGION": "us-east-1",
        "S3_ENDPOINT_URL": endpoint,
        "S3_BUCKETS": BUCKET,
        "COG_SCRATCH_DIR": str(tmp_path_factory.mktemp("scratch")),
    }
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    client = boto3.client("s3", endpoint_url=endpoint, region_name="us-east-1",
                          aws_access_key_id="testing", aws_secret_access_key="testing")
    client.create_bucket(Bucket=BUCKET)
    client.create_bucket(Bucket=OTHER_BUCKET)
    try:
        yield client
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        server.stop()


def _write_raster(path, *, tiled=False, size=1200):
    data = np.random.default_rng(0).integers(0, 255, size=(3, size, size), dtype="uint8")
    profile = dict(
        driver="GTiff", width=size, height=size, count=3, dtype="uint8",
        crs="EPSG:32632", transform=from_origin(500000, 5000000, 0.05, 0.05),
        compress="deflate",
    )
    if tiled:
        profile.update(tiled=True, blockxsize=256, blockysize=256)
    with rasterio.open(path, "w", **profile) as ds:
        ds.write(data)
    return data


@pytest.fixture
def raw_uri(s3, tmp_path):
    local = tmp_path / "raw.tif"
    _write_raster(local)
    s3.upload_file(str(local), BUCKET, "orgs/acme/tasks/t1/raw/orthophoto.tif")
    return f"s3://{BUCKET}/orgs/acme/tasks/t1/raw/orthophoto.tif"


# -- URI handling -----------------------------------------------------------


def test_uri_parsing_and_allowlist(s3):
    assert objectstore.to_gdal_path(f"s3://{BUCKET}/a/b.tif") == f"/vsis3/{BUCKET}/a/b.tif"
    with pytest.raises(objectstore.ObjectStoreError):
        objectstore.parse_uri(f"s3://{OTHER_BUCKET}/a/b.tif")
    with pytest.raises(objectstore.ObjectStoreError):
        objectstore.parse_uri(f"s3://{BUCKET}/")
    with pytest.raises(objectstore.ObjectStoreError):
        objectstore.parse_uri(f"s3://{BUCKET}/a/../b.tif")
    assert objectstore.resolve_read_path("/data/x.tif") == "/data/x.tif"


def test_gdal_env_targets_custom_endpoint(s3):
    env = objectstore.gdal_env()
    assert env["AWS_S3_ENDPOINT"].startswith("127.0.0.1:")
    assert env["AWS_HTTPS"] == "NO"
    assert env["AWS_VIRTUAL_HOSTING"] == "FALSE"
    assert env["GDAL_DISABLE_READDIR_ON_OPEN"] == "EMPTY_DIR"
    assert env["AWS_ACCESS_KEY_ID"] == "testing"
    # credentials never travel as plain Env options (rasterio forbids it)
    assert "AWS_ACCESS_KEY_ID" not in objectstore.gdal_options()


def test_not_configured_rejects_uris(monkeypatch):
    monkeypatch.setenv("S3_BUCKETS", "")
    with pytest.raises(objectstore.ObjectStoreNotConfigured):
        objectstore.parse_uri("s3://x/y.tif")


# -- reading straight from S3 --------------------------------------------------


def test_metadata_read_from_object(s3, raw_uri):
    meta = raster.read_metadata(raw_uri)
    assert meta["path"] == raw_uri
    assert (meta["width"], meta["height"], meta["band_count"]) == (1200, 1200, 3)
    assert meta["crs"]["epsg"] == 32632
    assert meta["georeference"] == "full"
    assert meta["file_size"] == objectstore.object_size(raw_uri) > 0
    assert meta["is_cog"] is False


def test_metadata_missing_object(s3):
    with pytest.raises(raster.RasterMetadataError, match="not found"):
        raster.read_metadata(f"s3://{BUCKET}/orgs/acme/tasks/t1/raw/nope.tif")


def test_tiles_render_from_object(s3, raw_uri):
    info = raster.tile_info(raw_uri)
    assert info["band_count"] == 3
    assert 0 < info["maxzoom"] <= 30
    lon = (info["bounds"][0] + info["bounds"][2]) / 2
    lat = (info["bounds"][1] + info["bounds"][3]) / 2
    import morecantile

    tile = morecantile.tms.get("WebMercatorQuad").tile(lon, lat, info["maxzoom"])
    png = raster.render_tile(raw_uri, tile.z, tile.x, tile.y)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


# -- S3 -> S3 COG conversion -------------------------------------------------


def test_cogify_object_to_object(s3, raw_uri):
    out_uri = f"s3://{BUCKET}/orgs/acme/tasks/t1/assets/orthophoto.tif"
    resp = asyncio.run(cogify(CogifyRequest(path=raw_uri, output_path=out_uri)))
    assert resp["path"] == out_uri
    assert resp["epsg"] == 32632
    assert resp["extent"]["type"] == "Polygon"
    # metadata is read from the *output object*
    assert resp["metadata"]["path"] == out_uri
    assert resp["metadata"]["is_cog"] is True
    assert resp["metadata"]["is_tiled"] is True
    assert resp["metadata"]["overviews"]
    assert objectstore.exists(out_uri)
    assert raster.is_cog(out_uri)
    # nothing left in scratch
    assert os.listdir(os.environ["COG_SCRATCH_DIR"]) == []


def test_cogify_object_source_requires_output(s3, raw_uri):
    with pytest.raises(HTTPException) as e:
        asyncio.run(cogify(CogifyRequest(path=raw_uri)))
    assert e.value.status_code == 400


def test_cogify_missing_object_is_404(s3):
    with pytest.raises(HTTPException) as e:
        asyncio.run(cogify(CogifyRequest(path=f"s3://{BUCKET}/missing.tif",
                                         output_path=f"s3://{BUCKET}/out.tif")))
    assert e.value.status_code == 404


def test_cogify_local_source_object_destination(s3, tmp_path):
    local = tmp_path / "local.tif"
    _write_raster(local)
    out_uri = f"s3://{BUCKET}/orgs/acme/tasks/t2/assets/dsm.tif"
    resp = asyncio.run(cogify(CogifyRequest(path=str(local), output_path=out_uri)))
    assert resp["path"] == out_uri
    assert raster.is_cog(out_uri)
    # the local source is untouched
    assert not raster.is_cog(str(local))


def test_cogify_local_in_place_unchanged(s3, tmp_path):
    local = tmp_path / "inplace.tif"
    _write_raster(local)
    resp = asyncio.run(cogify(CogifyRequest(path=str(local))))
    assert resp["path"] == str(local)
    assert raster.is_cog(str(local))


# -- router gates ----------------------------------------------------------------


def test_require_raster_accepts_allowed_uri_rejects_others(s3):
    _require_raster(f"s3://{BUCKET}/orgs/acme/x.tif")  # no exception, no round trip
    with pytest.raises(HTTPException) as e:
        _require_raster(f"s3://{OTHER_BUCKET}/x.tif")
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        _require_raster("relative/path.tif")
    assert e.value.status_code == 400


def test_metadata_router_over_object(s3, raw_uri):
    meta = asyncio.run(raster_metadata(path=raw_uri))
    assert meta["width"] == 1200
    with pytest.raises(HTTPException) as e:
        asyncio.run(raster_metadata(path=f"s3://{BUCKET}/nope.tif"))
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:
        asyncio.run(raster_metadata(path=f"s3://{OTHER_BUCKET}/nope.tif"))
    assert e.value.status_code == 400
