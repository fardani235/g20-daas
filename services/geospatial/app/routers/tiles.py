import os

from fastapi import APIRouter, HTTPException, Query, Response
from rio_tiler.errors import TileOutsideBounds

from app.utils import raster

router = APIRouter()

# 1x1 transparent PNG returned for tiles that fall outside the raster, so Leaflet
# renders empty space instead of broken-image tiles.
_EMPTY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c626001000000050001a5f645400000000049454e44ae426082"
)


def _require_raster(path: str):
    if not os.path.isabs(path):
        raise HTTPException(status_code=400, detail="path must be absolute")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"raster not found: {path}")


@router.get("/info")
async def tile_info(path: str = Query(...)):
    """Bounds (EPSG:4326), zoom range, and band stats for a raster."""
    _require_raster(path)
    try:
        return raster.tile_info(path)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"tile_info failed: {e}")


@router.get("/tile/{z}/{x}/{y}.png")
async def get_tile(
    z: int,
    x: int,
    y: int,
    path: str = Query(...),
    kind: str = Query("orthophoto"),
):
    """Serve a single XYZ raster tile as PNG (orthophoto RGB, or colored DEM)."""
    _require_raster(path)
    try:
        data = raster.render_tile(path, z, x, y, kind=kind)
    except TileOutsideBounds:
        return Response(content=_EMPTY_PNG, media_type="image/png")
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"tile render failed: {e}")
    return Response(content=data, media_type="image/png")
