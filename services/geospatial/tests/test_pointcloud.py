"""LAS header parsing and the ``/pointcloud/to-potree`` endpoint.

The header/validation tests need nothing but Python. The conversion tests
run PotreeConverter (``POTREE_CONVERTER_BIN`` or on ``PATH``) and are skipped
when it is not installed; CI runs them inside the geospatial image, which
builds the converter.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import struct

import pytest
from fastapi import HTTPException

from app.routers.pointcloud import ToPotreeRequest, convert_to_potree, converter_info
from app.utils import objectstore, pointcloud

WKT_32632 = ('PROJCS["WGS 84 / UTM zone 32N",GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],'
             'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],'
             'PARAMETER["central_meridian",9],UNIT["metre",1],AUTHORITY["EPSG","32632"]]')

needs_converter = pytest.mark.skipif(not pointcloud.converter_available(), reason="PotreeConverter not installed")


# ------------------------------------------------------------ LAS writer

def _vlr(user_id: str, record_id: int, payload: bytes, description: str = "") -> bytes:
    head = struct.pack("<H16sHH32s", 0, user_id.encode().ljust(16, b"\0"), record_id, len(payload),
                       description.encode().ljust(32, b"\0"))
    return head + payload


def _geokeys(epsg: int) -> bytes:
    # KeyDirectoryVersion, KeyRevision, MinorRevision, NumberOfKeys, then one key.
    return struct.pack("<4H", 1, 1, 0, 1) + struct.pack("<4H", 3072, 0, 1, epsg)


def write_las(path, points, *, point_format=3, wkt: str | None = None, epsg: int | None = None,
              classification=None, intensity=None, rgb=None, scale=0.01):
    """Minimal LAS 1.2 writer: formats 0-3, optional CRS VLRs. ``points`` = [(x,y,z), ...]."""
    record_lengths = {0: 20, 1: 28, 2: 26, 3: 34}
    rec_len = record_lengths[point_format]
    vlrs = b""
    n_vlr = 0
    if wkt is not None:
        vlrs += _vlr("LASF_Projection", 2112, wkt.encode() + b"\0", "OGC WKT")
        n_vlr += 1
    if epsg is not None:
        vlrs += _vlr("LASF_Projection", 34735, _geokeys(epsg), "GeoTIFF keys")
        n_vlr += 1
    header_size = 227
    offset_to_points = header_size + len(vlrs)
    xs, ys, zs = zip(*points)
    ox, oy, oz = min(xs), min(ys), min(zs)
    header = bytearray(header_size)
    struct.pack_into("<4s", header, 0, b"LASF")
    header[24], header[25] = 1, 2
    struct.pack_into("<32s", header, 58, b"webodm-tests")
    struct.pack_into("<HIL", header, 94, header_size, offset_to_points, n_vlr)
    header[104] = point_format
    struct.pack_into("<H", header, 105, rec_len)
    struct.pack_into("<I", header, 107, len(points))
    struct.pack_into("<5I", header, 111, len(points), 0, 0, 0, 0)
    struct.pack_into("<3d", header, 131, scale, scale, scale)
    struct.pack_into("<3d", header, 155, ox, oy, oz)
    struct.pack_into("<6d", header, 179, max(xs), ox, max(ys), oy, max(zs), oz)
    body = bytearray()
    for i, (x, y, z) in enumerate(points):
        cls = classification[i] if classification else 0
        inten = intensity[i] if intensity else 0
        rec = struct.pack("<3iHBBbBH", round((x - ox) / scale), round((y - oy) / scale), round((z - oz) / scale),
                          inten, 0b00001001, cls, 0, 0, 0)
        if point_format in (1, 3):
            rec += struct.pack("<d", float(i))
        if point_format in (2, 3):
            r, g, b = rgb[i] if rgb else (0, 0, 0)
            rec += struct.pack("<3H", r, g, b)
        assert len(rec) == rec_len
        body += rec
    with open(path, "wb") as fh:
        fh.write(bytes(header) + vlrs + bytes(body))
    return str(path)


def _grid(n=40, size=20.0, base=(500000.0, 4500000.0, 100.0)):
    pts, cls, inten, rgb = [], [], [], []
    for i in range(n):
        for j in range(n):
            x = base[0] + i * size / n
            y = base[1] + j * size / n
            z = base[2] + ((i * 7 + j * 3) % 11) * 0.5
            pts.append((x, y, z))
            cls.append(2 if (i + j) % 3 else 6)
            inten.append((i * j) % 400)
            rgb.append(((i * 6) % 256, (j * 6) % 256, 128))
    return pts, cls, inten, rgb


# ------------------------------------------------------------- header

def test_read_header_wkt_and_flags(tmp_path):
    pts, cls, inten, rgb = _grid(n=4)
    path = write_las(tmp_path / "a.las", pts, point_format=3, wkt=WKT_32632, classification=cls, intensity=inten, rgb=rgb)
    h = pointcloud.read_header(path)
    assert h["version"] == "1.2"
    assert h["point_format"] == 3
    assert h["compressed"] is False
    assert h["points"] == 16
    assert h["has_rgb"] and h["has_gps_time"]
    assert h["wkt"] == WKT_32632
    assert h["bounds"][0] == pytest.approx(500000.0)
    assert h["bounds"][5] == pytest.approx(max(p[2] for p in pts))
    assert pointcloud.projection_for(h) == WKT_32632


def test_read_header_geokeys_epsg(tmp_path):
    pts, *_ = _grid(n=3)
    path = write_las(tmp_path / "b.las", pts, point_format=0, epsg=32632)
    h = pointcloud.read_header(path)
    assert h["epsg"] == 32632 and h["wkt"] == ""
    assert h["has_rgb"] is False and h["has_gps_time"] is False
    assert pointcloud.projection_for(h) == "EPSG:32632"


def test_read_header_rejects_garbage(tmp_path):
    bad = tmp_path / "x.laz"
    bad.write_bytes(b"not a las file at all" * 20)
    with pytest.raises(pointcloud.PointCloudError):
        pointcloud.read_header(str(bad))


def test_summarize_reports_present_classes():
    meta = {"version": "2.0", "points": 3, "spacing": 0.5, "boundingBox": {"min": [0, 0, 0], "max": [1, 1, 1]},
            "attributes": [{"name": "classification", "type": "uint8", "numElements": 1, "min": [2], "max": [6],
                            "histogram": [0, 0, 5, 0, 0, 0, 1] + [0] * 249}]}
    s = pointcloud.summarize(meta)
    assert s["attributes"][0]["present_values"] == [2, 6]
    assert s["projection"] == ""


# ---------------------------------------------------------- validation

def _run(req):
    return asyncio.run(convert_to_potree(req))


def test_relative_paths_are_400(tmp_path):
    with pytest.raises(HTTPException) as e:
        _run(ToPotreeRequest(path="relative.laz", output_path=str(tmp_path / "o")))
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        _run(ToPotreeRequest(path=str(tmp_path / "a.laz"), output_path="out"))
    assert e.value.status_code == 400


def test_mixed_local_and_object_is_400(tmp_path, monkeypatch):
    monkeypatch.setenv("S3_BUCKETS", "b")
    with pytest.raises(HTTPException) as e:
        _run(ToPotreeRequest(path=str(tmp_path / "a.laz"), output_path="s3://b/orgs/x/tasks/t/assets/potree"))
    assert e.value.status_code == 400


def test_bucket_not_allowed_is_400(tmp_path, monkeypatch):
    monkeypatch.setenv("S3_BUCKETS", "b")
    with pytest.raises(HTTPException) as e:
        _run(ToPotreeRequest(path="s3://other/k.laz", output_path="s3://other/potree"))
    assert e.value.status_code == 400


def test_missing_converter_is_503(tmp_path, monkeypatch):
    monkeypatch.setenv("POTREE_CONVERTER_BIN", str(tmp_path / "nope"))
    with pytest.raises(HTTPException) as e:
        _run(ToPotreeRequest(path=str(tmp_path / "a.laz"), output_path=str(tmp_path / "o")))
    assert e.value.status_code == 503
    info = asyncio.run(converter_info())
    assert info["available"] is False


@needs_converter
def test_missing_file_is_404(tmp_path):
    with pytest.raises(HTTPException) as e:
        _run(ToPotreeRequest(path=str(tmp_path / "missing.laz"), output_path=str(tmp_path / "o")))
    assert e.value.status_code == 404


# ----------------------------------------------------------- conversion

@needs_converter
def test_convert_local_octree(tmp_path):
    pts, cls, inten, rgb = _grid()
    src = write_las(tmp_path / "cloud.las", pts, point_format=3, wkt=WKT_32632, classification=cls,
                    intensity=inten, rgb=rgb)
    out = tmp_path / "potree"

    res = _run(ToPotreeRequest(path=src, output_path=str(out), name="Task 1"))

    assert sorted(os.listdir(out)) == sorted(pointcloud.OUTPUT_FILES)  # no log.txt, no temp dirs
    assert res["points"] == len(pts)
    assert res["encoding"] == "UNCOMPRESSED"
    assert res["projection"] == WKT_32632
    assert set(res["files"]) == set(pointcloud.OUTPUT_FILES)
    assert res["files"]["octree.bin"]["size"] > 0
    names = {a["name"]: a for a in res["attributes"]}
    assert {"position", "rgb", "intensity", "classification"} <= set(names)
    assert names["intensity"]["max"][0] > names["intensity"]["min"][0]
    assert names["classification"]["present_values"] == [2, 6]
    meta = json.loads((out / "metadata.json").read_text())
    assert meta["projection"] == WKT_32632  # WKT with quotes survived (we write it, not the converter)
    assert meta["name"] == "Task 1"
    assert meta["source"]["points"] == len(pts)
    bb = meta["boundingBox"]
    assert bb["min"][0] == pytest.approx(500000.0, abs=0.05)
    assert bb["min"][2] == pytest.approx(100.0, abs=0.05)
    # No scratch left behind next to the output.
    assert [d for d in os.listdir(tmp_path) if d.startswith(".potree-")] == []


@needs_converter
def test_convert_replaces_previous_octree_atomically(tmp_path):
    pts, cls, inten, rgb = _grid(n=10)
    src = write_las(tmp_path / "cloud.las", pts, point_format=2, epsg=32632, rgb=rgb)
    out = tmp_path / "potree"
    out.mkdir()
    (out / "octree.bin").write_bytes(b"stale")
    (out / "junk.txt").write_text("old")

    res = _run(ToPotreeRequest(path=src, output_path=str(out)))

    assert res["projection"] == "EPSG:32632"
    assert not (out / "junk.txt").exists()
    assert (out / "octree.bin").stat().st_size == res["files"]["octree.bin"]["size"] > 5


@needs_converter
def test_convert_empty_cloud_is_422(tmp_path):
    src = write_las(tmp_path / "one.las", [(1.0, 2.0, 3.0)], point_format=0)
    # Zero the point count so the header says "empty".
    with open(src, "r+b") as fh:
        fh.seek(107)
        fh.write(struct.pack("<I", 0))
    with pytest.raises(HTTPException) as e:
        _run(ToPotreeRequest(path=src, output_path=str(tmp_path / "o")))
    assert e.value.status_code == 422


# --------------------------------------------------------------- S3 flow

moto_server = pytest.importorskip("moto.server")
BUCKET = "webodm-test"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def s3(tmp_path_factory):
    import boto3

    port = _free_port()
    server = moto_server.ThreadedMotoServer(ip_address="127.0.0.1", port=port)
    server.start()
    endpoint = f"http://127.0.0.1:{port}"
    env = {
        "AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing", "AWS_REGION": "us-east-1",
        "S3_ENDPOINT_URL": endpoint, "S3_BUCKETS": BUCKET,
        "COG_SCRATCH_DIR": str(tmp_path_factory.mktemp("scratch")),
    }
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    client = boto3.client("s3", endpoint_url=endpoint, region_name="us-east-1",
                          aws_access_key_id="testing", aws_secret_access_key="testing")
    client.create_bucket(Bucket=BUCKET)
    try:
        yield client
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        server.stop()


def test_object_source_missing_is_404(s3):
    with pytest.raises(HTTPException) as e:
        _run(ToPotreeRequest(path=f"s3://{BUCKET}/orgs/acme/tasks/t1/assets/georeferenced_model.laz",
                             output_path=f"s3://{BUCKET}/orgs/acme/tasks/t1/assets/potree"))
    assert e.value.status_code in (404, 503)  # 503 only when the converter is absent


@needs_converter
def test_convert_object_to_object(s3, tmp_path):
    pts, cls, inten, rgb = _grid(n=12)
    src = write_las(tmp_path / "cloud.las", pts, point_format=3, wkt=WKT_32632, classification=cls, rgb=rgb)
    src_key = "orgs/acme/tasks/t1/assets/georeferenced_model.las"
    s3.upload_file(src, BUCKET, src_key)
    prefix = f"s3://{BUCKET}/orgs/acme/tasks/t1/assets/potree"

    res = _run(ToPotreeRequest(path=f"s3://{BUCKET}/{src_key}", output_path=prefix))

    assert res["points"] == len(pts)
    for fname in pointcloud.OUTPUT_FILES:
        assert res["files"][fname]["path"] == f"{prefix}/{fname}"
        head = s3.head_object(Bucket=BUCKET, Key=f"orgs/acme/tasks/t1/assets/potree/{fname}")
        assert head["ContentLength"] == res["files"][fname]["size"]
        assert head["ContentType"] == pointcloud.CONTENT_TYPES[fname]
    # Scratch is clean afterwards.
    assert os.listdir(os.environ["COG_SCRATCH_DIR"]) == []
    meta = json.loads(s3.get_object(Bucket=BUCKET, Key="orgs/acme/tasks/t1/assets/potree/metadata.json")["Body"].read())
    assert meta["projection"] == WKT_32632
