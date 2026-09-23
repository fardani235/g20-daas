"""Build the node heightfield the mesher works on.

Sources, in the order ``auto`` prefers them: DSM (the top surface ODM already
computed), point cloud (binned to a surface), DTM (bare earth). Whatever the
primary source, the others fill its gaps when available: DSM no-data cells
take the DTM height (or the point-cloud height), and remaining *interior*
holes are interpolated. Areas outside the data footprint stay no-data and
produce no triangles, so an irregular survey outline is preserved.
"""

from dataclasses import dataclass, field

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import rasterize, shapes
from rasterio.fill import fillnodata
from rasterio.vrt import WarpedVRT

from .errors import InputError
from .grid import Grid
from .sources import RasterSource, nan_nodata_read


@dataclass
class Heightfield:
    grid: Grid
    z: np.ndarray                 # float32 (H, W), NaN = no data
    source: str                   # "dsm" | "dtm" | "point_cloud"
    fills: dict = field(default_factory=dict)   # what filled how many nodes
    rgb: np.ndarray | None = None  # point-cloud colours, (3, H, W) uint8, when binned

    @property
    def valid(self) -> np.ndarray:
        return np.isfinite(self.z)

    def z_range(self):
        v = self.z[self.valid]
        return (float(v.min()), float(v.max())) if v.size else (0.0, 0.0)


def warp_to_grid(source: RasterSource, grid: Grid, band: int = 1,
                 resampling: Resampling | None = None) -> np.ndarray:
    """Resample one raster band onto the grid nodes (NaN where no data)."""
    if resampling is None:
        # Averaging when the raster is finer than the grid keeps the surface
        # smooth; bilinear otherwise. GDAL reads from a matching overview.
        ratio = grid.res / max(source.res_in(grid.crs), 1e-9)
        resampling = Resampling.average if ratio > 1.5 else Resampling.bilinear
    with rasterio.open(source.path) as src:
        nodata = src.nodata
        kwargs = dict(
            crs=grid.crs, transform=grid.node_transform, width=grid.width, height=grid.height,
            resampling=resampling, dtype="float32", nodata=np.nan,
        )
        if nodata is not None:
            kwargs["src_nodata"] = nodata
        with WarpedVRT(src, **kwargs) as vrt:
            arr = nan_nodata_read(vrt, band)
    # Some DEMs mark no-data with an extreme value but no tag.
    arr[(arr < -1e4) | (arr > 1e5)] = np.nan
    return arr


def warp_mask_to_grid(source: RasterSource, grid: Grid) -> np.ndarray:
    """Boolean validity of a raster (alpha / nodata mask) on the grid nodes."""
    with rasterio.open(source.path) as src, WarpedVRT(
            src, crs=grid.crs, transform=grid.node_transform, width=grid.width,
            height=grid.height, resampling=Resampling.nearest) as vrt:
        mask = vrt.dataset_mask()
    return mask > 0


def interior_holes(valid: np.ndarray) -> np.ndarray:
    """No-data regions fully enclosed by data (not touching the grid border)."""
    holes = (~valid).astype(np.uint8)
    if not holes.any() or holes.all():
        return np.zeros_like(valid, dtype=bool)
    h, w = valid.shape
    interior = []
    for geom, value in shapes(holes, mask=holes.astype(bool), connectivity=4):
        coords = np.asarray(geom["coordinates"][0])
        if coords[:, 0].min() <= 0 or coords[:, 1].min() <= 0 or \
                coords[:, 0].max() >= w or coords[:, 1].max() >= h:
            continue  # touches the border: exterior
        interior.append(geom)
    if not interior:
        return np.zeros_like(valid, dtype=bool)
    return rasterize(interior, out_shape=valid.shape, fill=0, default_value=1, dtype="uint8").astype(bool)


def fill_interior(z: np.ndarray, max_distance: int = 512) -> tuple:
    """Interpolate interior holes in place; return (z, number of nodes filled)."""
    valid = np.isfinite(z)
    holes = interior_holes(valid)
    if not holes.any():
        return z, 0
    filled = fillnodata(np.nan_to_num(z, nan=0.0), mask=valid.astype(np.uint8),
                        max_search_distance=max_distance, smoothing_iterations=0)
    out = z.copy()
    out[holes] = filled[holes]
    # Nodes beyond the search distance remain no-data.
    out[holes & ~np.isfinite(filled)] = np.nan
    return out, int(holes.sum())


def build(grid: Grid, *, primary: str, dsm: RasterSource | None, dtm: RasterSource | None,
          point_cloud=None, point_stat: str = "max", fill_holes: bool = True,
          progress=None) -> Heightfield:
    """Assemble the heightfield from ``primary`` plus whatever can fill its gaps."""
    fills = {}
    rgb = None
    if primary == "dsm":
        if dsm is None:
            raise InputError("surface_source 'dsm' selected but the task has no DSM")
        z = warp_to_grid(dsm, grid)
    elif primary == "dtm":
        if dtm is None:
            raise InputError("surface_source 'dtm' selected but the task has no DTM")
        z = warp_to_grid(dtm, grid)
    elif primary == "point_cloud":
        if point_cloud is None:
            raise InputError("surface_source 'point_cloud' selected but the task has no usable point cloud")
        from . import pointcloud
        z, rgb = pointcloud.rasterize(point_cloud, grid, stat=point_stat, progress=progress)
    else:
        raise InputError(f"unknown surface source '{primary}'")

    if not np.isfinite(z).any():
        raise InputError(f"{primary} has no data inside the reconstruction area")

    # Gap filling from the other sources (only where the primary has none).
    if primary != "dtm" and dtm is not None:
        missing = ~np.isfinite(z)
        if missing.any():
            alt = warp_to_grid(dtm, grid)
            take = missing & np.isfinite(alt)
            if take.any():
                z[take] = alt[take]
                fills["dtm"] = int(take.sum())
    if primary == "point_cloud" and dsm is not None:
        missing = ~np.isfinite(z)
        if missing.any():
            alt = warp_to_grid(dsm, grid)
            take = missing & np.isfinite(alt)
            if take.any():
                z[take] = alt[take]
                fills["dsm"] = int(take.sum())

    if fill_holes:
        z, n = fill_interior(z)
        if n:
            fills["interpolated"] = n
    if progress and fills:
        progress.log("filled no-data nodes: " + ", ".join(f"{k}={v:,}" for k, v in fills.items()))
    return Heightfield(grid, z.astype(np.float32), primary, fills, rgb)
