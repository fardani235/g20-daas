from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.routers.tiles import _require_raster
from app.utils.volume import compute_volume

router = APIRouter()


class VolumeRequest(BaseModel):
    path: str
    polygon: dict


@router.post("")
async def volume(req: VolumeRequest):
    """Compute fill/cut/net volume of a polygon over a DSM raster."""
    _require_raster(req.path)
    try:
        return compute_volume(req.path, req.polygon)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
