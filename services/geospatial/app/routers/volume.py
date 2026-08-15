from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.routers.tiles import _require_raster
from app.utils.volume import DEFAULT_BASE_METHOD, compute_volume

router = APIRouter()


class VolumeRequest(BaseModel):
    path: str
    polygon: dict
    # One of app.utils.volume.BASE_METHODS. Blank is treated as the default so
    # callers can forward an unset UI selection verbatim.
    method: str = DEFAULT_BASE_METHOD


@router.post("")
async def volume(req: VolumeRequest):
    """Compute fill/cut/net volume of a polygon over a DSM raster."""
    _require_raster(req.path)
    try:
        return compute_volume(
            req.path, req.polygon, base_method=req.method or DEFAULT_BASE_METHOD
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
