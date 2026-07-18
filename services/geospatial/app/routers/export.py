import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.utils import raster

router = APIRouter()


class CogifyRequest(BaseModel):
    # Absolute path to the source raster on shared storage.
    path: str
    # Optional separate destination; defaults to converting in place.
    dst_path: str | None = None


@router.post("/cogify")
async def cogify(req: CogifyRequest):
    """Convert a raster to a Cloud Optimized GeoTIFF and return its georeferencing.

    Returns the output path plus extent (GeoJSON Polygon, EPSG:4326), epsg, and wkt
    so the caller (Frappe) can persist them on the task without needing GDAL itself.
    """
    if not os.path.isabs(req.path):
        raise HTTPException(status_code=400, detail="path must be absolute")
    if not os.path.isfile(req.path):
        raise HTTPException(status_code=404, detail=f"raster not found: {req.path}")

    try:
        out_path = raster.to_cog(req.path, req.dst_path)
        georef = raster.read_georef(out_path)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"cogify failed: {e}")

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
    }


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
