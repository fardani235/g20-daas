import logging
import os
import subprocess

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.utils import objectstore, raster

log = logging.getLogger("webodm.geospatial.export")

router = APIRouter()


def _run_ogr2ogr(path: str, output_path: str) -> None:
    proc = subprocess.run(
        ["ogr2ogr", "-f", "GeoJSON", "-t_srs", "EPSG:4326", output_path, path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        raise ValueError(f"ogr2ogr failed: {proc.stderr.strip()}")


class CogifyRequest(BaseModel):
    # Source raster: absolute path on shared storage or an s3:// URI.
    path: str
    # Optional separate destination (absolute path or s3:// URI); defaults to
    # converting in place. Required when the source is an object URI.
    # ``output_path`` is the canonical name; ``dst_path`` is kept for callers
    # of the original API.
    output_path: str | None = None
    dst_path: str | None = None

    @property
    def destination(self) -> str | None:
        return self.output_path or self.dst_path


class VectorToGeoJSONRequest(BaseModel):
    # Absolute path to a vector dataset (GPKG, Shapefile, ...) on shared storage.
    path: str
    # Absolute path to write the GeoJSON result to.
    output_path: str


@router.post("/cogify")
async def cogify(req: CogifyRequest):
    """Convert a raster to a Cloud Optimized GeoTIFF and return its georeferencing.

    Returns the output path plus extent (GeoJSON Polygon, EPSG:4326), epsg, and wkt
    so the caller (Frappe) can persist them on the task without needing GDAL itself,
    and ``metadata`` (``raster.read_metadata`` of the output; null if that failed).
    """
    dst = req.destination
    for label, p in (("path", req.path), ("output_path", dst)):
        if p is None:
            continue
        if objectstore.is_object_uri(p):
            try:
                objectstore.parse_uri(p)
            except objectstore.ObjectStoreError as e:
                raise HTTPException(status_code=400, detail=f"{label}: {e}")
        elif not os.path.isabs(p):
            raise HTTPException(status_code=400, detail=f"{label} must be absolute or an s3:// URI")
    if objectstore.is_object_uri(req.path):
        if dst is None:
            raise HTTPException(status_code=400, detail="output_path is required when path is an s3:// URI")
        try:
            if not await run_in_threadpool(objectstore.exists, req.path):
                raise HTTPException(status_code=404, detail="raster not found in object storage")
        except objectstore.ObjectStoreError as e:
            raise HTTPException(status_code=502, detail=f"object storage: {e}")
    elif not os.path.isfile(req.path):
        raise HTTPException(status_code=404, detail=f"raster not found: {req.path}")

    try:
        # GDAL work is blocking; keep it off the event loop.
        out_path = await run_in_threadpool(raster.to_cog, req.path, dst)
        georef = await run_in_threadpool(raster.read_georef, out_path)
    except objectstore.ObjectStoreError as e:
        raise HTTPException(status_code=502, detail=f"object storage: {e}")
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"cogify failed: {e}")

    # Full header metadata of the *output* in the same round trip, so the
    # caller can persist it without a second request. Best-effort: a metadata
    # failure must not turn a successful conversion into an error.
    metadata = None
    try:
        metadata = await run_in_threadpool(raster.read_metadata, out_path)
    except Exception as e:
        log.warning("metadata after cogify failed for %s: %s", out_path, e)

    return {
        "path": out_path,
        "is_cog": True,
        "epsg": georef["epsg"],
        "wkt": georef["wkt"],
        "extent": georef["extent"],
        "bounds_4326": georef["bounds_4326"],
        "band_count": georef["band_count"],
        "width": georef["width"],
        "height": georef["height"],
        "metadata": metadata,
    }


@router.post("/vector-to-geojson")
async def vector_to_geojson(req: VectorToGeoJSONRequest):
    """Convert a vector dataset to GeoJSON (reprojected to EPSG:4326)."""
    if not os.path.isabs(req.path):
        raise HTTPException(status_code=400, detail="path must be absolute")
    if not os.path.isfile(req.path):
        raise HTTPException(status_code=404, detail=f"vector not found: {req.path}")
    if not os.path.isabs(req.output_path):
        raise HTTPException(status_code=400, detail="output_path must be absolute")

    try:
        await run_in_threadpool(_run_ogr2ogr, req.path, req.output_path)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return {"path": req.output_path, "format": "GeoJSON", "crs": "EPSG:4326"}


@router.post("/raster")
async def export_raster():
    """Export raster as GeoTIFF, PNG, KMZ, or MBTiles"""
    raise HTTPException(status_code=501, detail="Not implemented")


@router.post("/hillshade")
async def generate_hillshade():
    """Generate hillshade from DEM"""
    raise HTTPException(status_code=501, detail="Not implemented")


@router.post("/colormap")
async def apply_colormap():
    """Apply custom colormap to raster"""
    raise HTTPException(status_code=501, detail="Not implemented")


@router.post("/formula")
async def apply_formula():
    """Apply band formula (e.g., NDVI)"""
    raise HTTPException(status_code=501, detail="Not implemented")
