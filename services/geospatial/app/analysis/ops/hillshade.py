"""Hillshade operation: render a shaded-relief raster from a DSM/DEM.

Implemented with numpy so it needs no GDAL CLI. The DEM is processed in
fixed-size blocks with a one-pixel halo, so peak memory is bounded by the block
size instead of the raster size — a whole-raster computation creates several
full-resolution float64 temporaries and would exhaust the service's memory
limit on large DEMs. The result is written as a Cloud Optimized GeoTIFF so the
tiles proxy can serve it directly.
"""

import numpy as np
import rasterio
from rasterio.windows import Window
from pydantic import BaseModel, Field

from app.analysis.registry import AnalysisOp, register
from app.utils import raster

# Output is computed in blocks of this many pixels per side. The one-pixel halo
# makes gradients at block seams identical to a whole-raster computation.
BLOCK_SIZE = 1024
_HALO = 1


class HillshadeParams(BaseModel):
    azimuth: float = Field(315.0, ge=0, lt=360, description="Light azimuth in degrees")
    altitude: float = Field(45.0, gt=0, le=90, description="Light altitude in degrees")
    zfactor: float = Field(1.0, gt=0, description="Vertical exaggeration")


def _shade(band: np.ndarray, cellsize: float, params: HillshadeParams) -> np.ndarray:
    """Shaded-relief values for one (halo-padded) block of elevation values."""
    x, y = np.gradient(band * params.zfactor, cellsize)

    slope = np.pi / 2.0 - np.arctan(np.sqrt(x * x + y * y))
    aspect = np.arctan2(-x, y)

    azimuth_rad = np.radians(360.0 - params.azimuth + 90.0)
    altitude_rad = np.radians(params.altitude)

    shaded = (
        np.sin(altitude_rad) * np.sin(slope)
        + np.cos(altitude_rad) * np.cos(slope) * np.cos((azimuth_rad - np.pi / 2.0) - aspect)
    )
    return np.nan_to_num(shaded, nan=0.0)


def run_hillshade(inputs: dict, params: HillshadeParams, output_path: str) -> dict:
    src = inputs.get("raster")
    if not src:
        raise ValueError("hillshade requires an input raster (DEM)")

    with rasterio.open(src) as ds:
        transform = ds.transform
        profile = ds.profile.copy()
        nodata = ds.nodata
        width, height = ds.width, ds.height
        cellsize = abs(transform.a) or 1.0

        profile.update(
            dtype="uint8",
            count=1,
            nodata=None,
            driver="GTiff",
            compress="deflate",
        )

        with rasterio.open(output_path, "w", **profile) as dst:
            for row_off in range(0, height, BLOCK_SIZE):
                for col_off in range(0, width, BLOCK_SIZE):
                    win_h = min(BLOCK_SIZE, height - row_off)
                    win_w = min(BLOCK_SIZE, width - col_off)

                    # Pad by the halo, clamped to the raster so gradients at the
                    # true edge stay one-sided, exactly like a whole-raster pass.
                    r0 = max(row_off - _HALO, 0)
                    c0 = max(col_off - _HALO, 0)
                    r1 = min(row_off + win_h + _HALO, height)
                    c1 = min(col_off + win_w + _HALO, width)

                    band = ds.read(
                        1, window=Window(c0, r0, c1 - c0, r1 - r0)
                    ).astype("float64")
                    if nodata is not None:
                        band = np.where(band == nodata, np.nan, band)

                    shaded = _shade(band, cellsize, params)

                    # Discard the halo to recover this block's own pixels.
                    top = row_off - r0
                    left = col_off - c0
                    block = shaded[top:top + win_h, left:left + win_w]

                    dst.write(
                        ((block + 1.0) * 127.5).clip(0, 255).astype("uint8"),
                        1,
                        window=Window(col_off, row_off, win_w, win_h),
                    )

    raster.to_cog(output_path)

    georef = raster.read_georef(output_path)
    return {
        "output_path": output_path,
        "metadata": {
            "azimuth": params.azimuth,
            "altitude": params.altitude,
            "zfactor": params.zfactor,
            "width": int(width),
            "height": int(height),
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
