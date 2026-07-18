from pydantic import BaseModel
from typing import Optional


class RasterExportRequest(BaseModel):
    task_id: str
    dataset: str  # orthophoto, dsm, dtm
    output_format: str  # tif, png, kmz, mbtiles
    crs: Optional[str] = None
    bounds: Optional[list[float]] = None  # [minx, miny, maxx, maxy]
    resolution: Optional[float] = None


class PointCloudExportRequest(BaseModel):
    task_id: str
    output_format: str  # las, laz, ply, csv
    crs: Optional[str] = None
    bounds: Optional[list[float]] = None


class ColormapRequest(BaseModel):
    task_id: str
    dataset: str
    colormap: str  # name or custom JSON
    min_value: Optional[float] = None
    max_value: Optional[float] = None


class FormulaRequest(BaseModel):
    task_id: str
    dataset: str
    formula: str  # e.g., "(B4 - B3) / (B4 + B3)" for NDVI
