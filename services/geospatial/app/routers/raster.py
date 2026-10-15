"""Raster metadata: normalized, header-only facts about a raster on shared storage.

Frappe calls this right after an orthophoto / DSM / DTM lands (and on demand
for a refresh) and persists the normalized subset it needs. Nothing here reads
pixel data, so a multi-gigabyte orthophoto answers in milliseconds.
"""

import logging
import os
import warnings

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.utils import elevation, objectstore, raster

log = logging.getLogger("webodm.geospatial.raster")

router = APIRouter()


def _read_metadata_quiet(path: str) -> dict:
    # Un-georeferenced inputs make rasterio warn on open; that is a legitimate
    # case here (reported via ``georeference``), not something to spam logs with.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return raster.read_metadata(path)


@router.get("/metadata")
async def raster_metadata(path: str = Query(..., description="Absolute path of the raster")):
    """Metadata of a raster (see ``raster.read_metadata`` for the shape).

    400 for a relative path, 404 if the file is missing, 422 if GDAL cannot open
    it (corrupt / not a raster). Georeferencing problems are *not* errors: a
    raster without a CRS still returns 200 with ``georeference: "no_crs"``.
    """
    if objectstore.is_object_uri(path):
        try:
            objectstore.parse_uri(path)
        except objectstore.ObjectStoreError as e:
            raise HTTPException(status_code=400, detail=str(e))
    else:
        if not os.path.isabs(path):
            raise HTTPException(status_code=400, detail="path must be absolute or an s3:// URI")
        if not os.path.isfile(path):
            raise HTTPException(status_code=404, detail=f"raster not found: {path}")

    try:
        return await run_in_threadpool(_read_metadata_quiet, path)
    except raster.RasterMetadataError as e:
        if "not found" in str(e):
            raise HTTPException(status_code=404, detail=str(e))
        log.warning("metadata failed for %s: %s", path, e)
        raise HTTPException(status_code=422, detail=f"metadata failed: {e}")
    except Exception as e:  # pragma: no cover - defensive
        log.exception("unexpected metadata failure for %s", path)
        raise HTTPException(status_code=422, detail=f"metadata failed: {e}")


class SampleRequest(BaseModel):
    path: str
    # [lng, lat] pairs in EPSG:4326.
    points: list[list[float]]
    # Size of the ground cell each value summarises (the caller's grid spacing).
    spacing_m: float = 30.0
    # "max" for a surface model (clear the highest thing in the cell),
    # "mean" for a terrain model, "min" for completeness.
    statistic: str = "max"


@router.post("/sample")
async def raster_sample(req: SampleRequest):
    """Elevation of a DSM / DTM at a list of points (see ``elevation.sample_points``).

    400 for a relative path or a disallowed bucket, 404 if the file is
    missing, 422 for bad points / an un-georeferenced or unreadable raster.
    Points outside the raster or on nodata come back as ``null``; that is an
    answer, not an error.
    """
    if objectstore.is_object_uri(req.path):
        try:
            objectstore.parse_uri(req.path)
        except objectstore.ObjectStoreError as e:
            raise HTTPException(status_code=400, detail=str(e))
    else:
        if not os.path.isabs(req.path):
            raise HTTPException(status_code=400, detail="path must be absolute or an s3:// URI")
        if not os.path.isfile(req.path):
            raise HTTPException(status_code=404, detail=f"raster not found: {req.path}")
    try:
        return await run_in_threadpool(
            elevation.sample_points, req.path, req.points, req.spacing_m, req.statistic)
    except elevation.ElevationSampleError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        log.warning("elevation sampling failed for %s: %s", req.path, e)
        raise HTTPException(status_code=422, detail=f"sampling failed: {e}")
