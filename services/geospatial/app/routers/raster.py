"""Raster metadata: normalized, header-only facts about a raster on shared storage.

Frappe calls this right after an orthophoto / DSM / DTM lands (and on demand
for a refresh) and persists the normalized subset it needs. Nothing here reads
pixel data, so a multi-gigabyte orthophoto answers in milliseconds.
"""

import logging
import os
import warnings

from fastapi import APIRouter, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from app.utils import objectstore, raster

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
