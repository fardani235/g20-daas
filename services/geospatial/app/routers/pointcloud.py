from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.post("/export")
async def export_pointcloud():
    """Export point cloud as LAS/LAZ/PLY/CSV"""
    raise HTTPException(status_code=501, detail="Not implemented")


@router.post("/to-potree")
async def convert_to_potree():
    """Convert LAS/LAZ to Potree format"""
    raise HTTPException(status_code=501, detail="Not implemented")
