"""Point cloud endpoints.

``POST /pointcloud/to-potree`` converts a task's LAS/LAZ into a Potree 2.0
octree (``metadata.json`` + ``hierarchy.bin`` + ``octree.bin``) with
PotreeConverter. Like ``/export/cogify`` it takes either absolute paths on the
shared volume or ``s3://`` URIs: an object source is downloaded to container
scratch, converted there, and the three files are uploaded under the
``output_path`` prefix, so an S3 -> S3 conversion touches no host disk.

The call is synchronous (the caller — a Frappe background job — holds the
request open, as it does for cogify) and bounded per worker by an in-process
semaphore (``POTREE_MAX_CONCURRENT``); job state lives in Frappe, never here.

``POST /pointcloud/export`` (re-export as LAS/LAZ/PLY/CSV) stays a stub: the
viewer does not need it and exporting the cloud is out of scope for now.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.utils import objectstore, pointcloud

log = logging.getLogger("webodm.geospatial.pointcloud")

router = APIRouter()

_slots = asyncio.Semaphore(pointcloud.max_concurrent())


class ToPotreeRequest(BaseModel):
    # LAS/LAZ source: absolute path on shared storage or an s3:// URI.
    path: str
    # Destination directory (absolute path) or s3:// prefix the three files go under.
    output_path: str
    # CRS to record in metadata.json (WKT or "EPSG:n"); default: from the LAS header.
    projection: str | None = None
    # Display name recorded in metadata.json.
    name: str | None = None


def _validate(label: str, value: str) -> bool:
    """True when ``value`` is an object URI (validated), False for an absolute path."""
    if objectstore.is_object_uri(value):
        try:
            objectstore.parse_uri(value)
        except objectstore.ObjectStoreError as e:
            raise HTTPException(status_code=400, detail=f"{label}: {e}")
        return True
    if not os.path.isabs(value):
        raise HTTPException(status_code=400, detail=f"{label} must be absolute or an s3:// URI")
    return False


def _convert_local(src: str, out_dir: str, projection: str | None, name: str | None) -> dict:
    return pointcloud.convert(src, out_dir, projection=projection, name=name)


def _convert_object(src_uri: str, out_prefix: str, projection: str | None, name: str | None) -> dict:
    scratch = tempfile.mkdtemp(prefix="potree-", dir=objectstore.scratch_dir())
    try:
        ext = os.path.splitext(src_uri)[1].lower() or ".laz"
        local_src = os.path.join(scratch, f"input{ext}")
        objectstore.download_file(src_uri, local_src)
        out_dir = os.path.join(scratch, "octree")
        summary = pointcloud.convert(local_src, out_dir, projection=projection, name=name)
        os.unlink(local_src)
        prefix = out_prefix.rstrip("/") + "/"
        files = {}
        for fname in pointcloud.OUTPUT_FILES:
            local = os.path.join(out_dir, fname)
            uri = prefix + fname
            objectstore.upload_file(local, uri, pointcloud.CONTENT_TYPES[fname])
            files[fname] = {"path": uri, "size": os.path.getsize(local)}
        summary["files"] = files
        return summary
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


@router.get("/converter")
async def converter_info():
    """Whether PotreeConverter is installed on this instance (for diagnostics)."""
    path = pointcloud.converter_path()
    return {
        "available": path is not None,
        "bin": pointcloud.converter_bin(),
        "path": path,
        "max_concurrent": pointcloud.max_concurrent(),
        "timeout_seconds": pointcloud.timeout_seconds(),
    }


@router.post("/to-potree")
async def convert_to_potree(req: ToPotreeRequest):
    """Convert a LAS/LAZ point cloud to a Potree 2.0 octree.

    Returns ``files`` (the three outputs with their path/URI and size),
    ``points``, ``bounding_box``, ``spacing``, ``projection`` and the
    per-attribute ``attributes`` summary (name, type, min/max and, for
    classification, the values present) so the caller can persist it.
    """
    src_is_object = _validate("path", req.path)
    dst_is_object = _validate("output_path", req.output_path)
    if src_is_object != dst_is_object:
        raise HTTPException(status_code=400, detail="path and output_path must both be local or both be s3:// URIs")
    if not pointcloud.converter_available():
        raise HTTPException(status_code=503, detail="PotreeConverter is not installed on the geospatial service")

    if src_is_object:
        try:
            if not await run_in_threadpool(objectstore.exists, req.path):
                raise HTTPException(status_code=404, detail="point cloud not found in object storage")
        except objectstore.ObjectStoreError as e:
            raise HTTPException(status_code=502, detail=f"object storage: {e}")
    elif not os.path.isfile(req.path):
        raise HTTPException(status_code=404, detail=f"point cloud not found: {req.path}")

    async with _slots:
        try:
            if src_is_object:
                summary = await run_in_threadpool(_convert_object, req.path, req.output_path, req.projection, req.name)
            else:
                summary = await run_in_threadpool(_convert_local, req.path, req.output_path, req.projection, req.name)
        except objectstore.ObjectStoreError as e:
            raise HTTPException(status_code=502, detail=f"object storage: {e}")
        except pointcloud.ConverterUnavailable as e:
            raise HTTPException(status_code=503, detail=str(e))
        except pointcloud.PointCloudError as e:
            log.warning("potree conversion failed for %s: %s", req.path, e)
            raise HTTPException(status_code=422, detail=f"conversion failed: {e}")
        except Exception as e:  # pragma: no cover - defensive
            log.exception("potree conversion crashed for %s", req.path)
            raise HTTPException(status_code=500, detail=f"conversion failed: {e.__class__.__name__}")

    summary["output_path"] = req.output_path
    return summary


@router.post("/export")
async def export_pointcloud():
    """Export point cloud as LAS/LAZ/PLY/CSV (not implemented; out of scope for the viewer)."""
    raise HTTPException(status_code=501, detail="Not implemented")
