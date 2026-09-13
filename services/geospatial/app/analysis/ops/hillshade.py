"""Hillshade operation: render a shaded-relief raster from a DSM/DEM.

Implemented with numpy so it needs no GDAL CLI. The result is written as a
Cloud Optimized GeoTIFF so the tiles proxy can serve it directly.
"""

import numpy as np
import rasterio
from pydantic import BaseModel, Field

from app.analysis.registry import AnalysisOp, register
from app.utils import raster


class HillshadeParams(BaseModel):
    azimuth: float = Field(315.0, ge=0, lt=360, description="Light azimuth in degrees")
    altitude: float = Field(45.0, gt=0, le=90, description="Light altitude in degrees")
    zfactor: float = Field(1.0, gt=0, description="Vertical exaggeration")


def run_hillshade(inputs: dict, params: HillshadeParams, output_path: str) -> dict:
    src = inputs.get("raster")
    if not src:
        raise ValueError("hillshade requires an input raster (DEM)")

    with rasterio.open(src) as ds:
        band = ds.read(1).astype("float64")
        transform = ds.transform
        profile = ds.profile.copy()
        nodata = ds.nodata

    if nodata is not None:
        band = np.where(band == nodata, np.nan, band)

    cellsize = abs(transform.a) or 1.0
    x, y = np.gradient(band * params.zfactor, cellsize)

    slope = np.pi / 2.0 - np.arctan(np.sqrt(x * x + y * y))
    aspect = np.arctan2(-x, y)

    azimuth_rad = np.radians(360.0 - params.azimuth + 90.0)
    altitude_rad = np.radians(params.altitude)

    shaded = (
        np.sin(altitude_rad) * np.sin(slope)
        + np.cos(altitude_rad) * np.cos(slope) * np.cos((azimuth_rad - np.pi / 2.0) - aspect)
    )
    shaded = np.nan_to_num(shaded, nan=0.0)

    profile.update(
        dtype="uint8",
        count=1,
        nodata=None,
        driver="GTiff",
        compress="deflate",
    )
    with rasterio.open(output_path, "w", **profile) as dst:
        dst.write(((shaded + 1.0) * 127.5).clip(0, 255).astype("uint8"), 1)

    raster.to_cog(output_path)

    georef = raster.read_georef(output_path)
    return {
        "output_path": output_path,
        "metadata": {
            "azimuth": params.azimuth,
            "altitude": params.altitude,
            "zfactor": params.zfactor,
            "width": int(profile["width"]),
            "height": int(profile["height"]),
            "extent": georef["extent"],
            "epsg": georef["epsg"],
            "bounds_4326": georef["bounds_4326"],
        },
    }


register(
    AnalysisOp(
        op_id="hillshade",
        label="Hillshade",
        description="Generate a shaded-relief rendering from a DSM/DEM",
        version="1.0.0",
        params_model=HillshadeParams,
        output_kind="raster",
        render_kind="dem",
        inputs=[{"name": "raster", "datasets": ["dsm", "dtm"]}],
        handler=run_hillshade,
    )
)
