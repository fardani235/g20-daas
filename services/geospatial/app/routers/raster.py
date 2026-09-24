"""Raster metadata API.

Header-only reads: the caller (Frappe) resolves a task raster to an absolute
shared-storage path and asks for its normalized metadata. Nothing is cached or
stored here; the service stays stateless.
"""

import os

from fastapi import APIRouter, HTTPException, Query
from rasterio.errors import RasterioIOError
from starlette.concurrency import run_in_threadpool

from app.utils import raster

router = APIRouter()


def _require_raster(path: str):
    if not os.path.isabs(path):
        raise HTTPException(status_code=400, detail="path must be absolute")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"raster not found: {path}")


@router.get("/metadata")
async def raster_metadata(path: str = Query(...)):
    """Normalized header metadata for a raster (see ``raster.read_metadata``).

    422 when the file exists but is not a readable raster (corrupt, truncated,
    unsupported format); the detail carries GDAL's reason so it can be stored
    against the task.
    """
    _require_raster(path)
    try:
        # Header reads are cheap but still blocking I/O; keep them off the loop.
        return await run_in_threadpool(raster.read_metadata, path)
    except (RasterioIOError, OSError, ValueError) as e:
        raise HTTPException(status_code=422, detail=f"not a readable raster: {e}")
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"metadata read failed: {e}")
