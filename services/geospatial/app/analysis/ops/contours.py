"""Contours operation: derive contour lines from a DEM raster.

Shells out to ``gdal_contour`` (available in the service image and on the host)
and reprojects the result to EPSG:4326 for web display.
"""

import os
import subprocess
import tempfile

import rasterio
from pydantic import BaseModel, Field

from app.analysis.registry import AnalysisOp, register
from app.utils.volume import _to_meters_factor

_GDAL_FORMATS = {"GeoJSON": "GeoJSON", "GPKG": "GPKG"}


class ContoursParams(BaseModel):
    interval_m: float = Field(
        5.0, gt=0, description="Vertical distance between contour lines, in metres"
    )
    output_format: str = Field(
        "GeoJSON", description="Vector output format: GeoJSON or GPKG"
    )
    simplify_m: float = Field(
        0.0, ge=0, description="Line simplification tolerance in metres (0 disables)"
    )


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return proc


def run_contours(inputs: dict, params: ContoursParams, output_path: str) -> dict:
    src = inputs.get("raster")
    if not src:
        raise ValueError("contours requires an input raster (DEM)")

    output_format = params.output_format if params.output_format in _GDAL_FORMATS else "GeoJSON"

    with rasterio.open(src) as ds:
        factor = _to_meters_factor(ds) or 1.0

    interval = params.interval_m / factor
    simplify = params.simplify_m / factor if params.simplify_m else 0.0

    tmp_dir = tempfile.mkdtemp(prefix="contours_")
    tmp_geojson = os.path.join(tmp_dir, "contours.geojson")

    proc = _run(
        ["gdal_contour", "-q", "-a", "level", "-3d", "-i", str(interval),
         "-f", "GeoJSON", src, tmp_geojson]
    )
    if proc.returncode != 0:
        raise ValueError(f"gdal_contour failed: {proc.stderr.strip()}")

    ogr_cmd = ["ogr2ogr", "-f", _GDAL_FORMATS[output_format], "-t_srs", "EPSG:4326"]
    if simplify:
        ogr_cmd += ["-simplify", str(simplify)]
    ogr_cmd += [output_path, tmp_geojson]

    proc = _run(ogr_cmd)
    if proc.returncode != 0:
        raise ValueError(f"ogr2ogr failed: {proc.stderr.strip()}")

    metadata = {
        "interval_m": params.interval_m,
        "output_format": output_format,
        "simplify_m": params.simplify_m,
        "crs": "EPSG:4326",
    }
    if output_format == "GeoJSON":
        import json

        with open(output_path, encoding="utf-8") as f:
            metadata["feature_count"] = len(json.load(f).get("features", []))

    return {"output_path": output_path, "metadata": metadata}


register(
    AnalysisOp(
        op_id="contours",
        label="Contours",
        description="Compute, preview and export contour lines from a DEM",
        version="1.0.0",
        params_model=ContoursParams,
        output_kind="vector",
        render_kind="contours",
        inputs=[{"name": "raster", "datasets": ["dsm", "dtm"]}],
        handler=run_contours,
    )
)
