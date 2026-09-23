"""Heightfield -> triangle mesh (one sub-mesh per texture tile).

The mesh lives in a *local* metric frame: X east, Y north, Z up, in the units
of the working (projected) CRS, relative to ``origin`` (the centre of the grid
footprint at height 0). Small local coordinates keep float32 GPU maths precise
and 16-bit quantization lossless enough; the origin and CRS travel with the GLB
so the model stays geospatially anchored (see ``gltf.py``).
"""

from dataclasses import dataclass

import numpy as np

from . import rtin
from .grid import Grid


@dataclass
class MeshTile:
    tile_x: int              # tile column / row in the tile grid
    tile_y: int
    col0: int                # first grid node column / row covered by the tile
    row0: int
    cells: int               # tile size in grid cells (nodes - 1)
    positions: np.ndarray    # (N, 3) float64, local frame
    uvs: np.ndarray          # (N, 2) float32 in [0, 1], v down from the north edge
    indices: np.ndarray      # (M, 3) uint32, counter-clockwise seen from +Z

    @property
    def triangle_count(self) -> int:
        return int(len(self.indices))

    @property
    def vertex_count(self) -> int:
        return int(len(self.positions))


@dataclass
class TerrainMesh:
    tiles: list
    tiles_per_side: int
    max_error: float
    origin: tuple            # (x, y, z) in the working CRS

    @property
    def triangle_count(self) -> int:
        return sum(t.triangle_count for t in self.tiles)

    @property
    def vertex_count(self) -> int:
        return sum(t.vertex_count for t in self.tiles)


# Validity is fed to the error map as a 0/1 field scaled by this, so any
# triangle straddling the data boundary has an "error" far above any real
# height error and is split down to grid resolution.
_MASK_PENALTY = 1e6


def build_terrain_mesh(grid: Grid, z: np.ndarray, *, max_triangles: int, tiles_per_side: int = 1,
                       max_error: float = 0.0, method: str = "adaptive", progress=None) -> TerrainMesh:
    """Mesh the node heights ``z`` (NaN = no data) on ``grid`` (a 2^k+1 square).

    ``max_error`` 0 means "as fine as the triangle budget allows"; a positive
    value is used directly, coarsened only if the budget is exceeded.
    ``method="grid"`` emits every grid cell as two triangles (no simplification).
    """
    size = grid.width
    if grid.height != size or not rtin.is_valid_size(size):
        raise ValueError("terrain grid must be a square of 2^k+1 nodes")
    if tiles_per_side < 1 or (size - 1) % tiles_per_side:
        raise ValueError("tiles_per_side must divide the grid")
    tile_cells = (size - 1) // tiles_per_side

    valid = np.isfinite(z)
    if not valid.any():
        raise ValueError("heightfield has no valid data")
    z_err = np.where(valid, z, float(np.nanmean(z))).astype(np.float32)
    mask_field = valid.astype(np.float32) * _MASK_PENALTY

    errors = rtin.compute_errors([z_err, mask_field], size)
    z_range = float(np.nanmax(z) - np.nanmin(z)) or 1.0

    if method == "grid":
        chosen = -1.0
    elif max_error > 0:
        chosen = float(max_error)
        if len(rtin.extract(errors, chosen, tile_cells, valid)) > max_triangles:
            chosen = rtin.choose_error(errors, max_triangles, floor=chosen, tile_cells=tile_cells,
                                       valid=valid, z_range=z_range)
            if progress:
                progress.warn(f"max_error_m raised to {chosen:.3f} m to stay within {max_triangles:,} triangles")
    else:
        chosen = rtin.choose_error(errors, max_triangles, tile_cells=tile_cells, valid=valid, z_range=z_range)

    tris = rtin.extract(errors, chosen, tile_cells, valid)
    if method == "grid" and len(tris) > max_triangles and progress:
        progress.warn(f"grid mesh has {len(tris):,} triangles, above the {max_triangles:,} budget; "
                      f"use a coarser resolution_m or the adaptive method")
    if not len(tris):
        raise ValueError("no triangles left after removing no-data areas")

    origin = (grid.x0 + (size - 1) * grid.res / 2.0, grid.y0 - (size - 1) * grid.res / 2.0, 0.0)
    tiles = []
    # Every triangle lies inside one tile (forced by extract); classify by its
    # minimum corner. Nodes on tile borders are duplicated per tile so each
    # tile's UVs stay within [0, 1] of its own texture.
    minx = tris[:, [0, 2, 4]].min(axis=1)
    miny = tris[:, [1, 3, 5]].min(axis=1)
    tile_ids = (miny // tile_cells) * tiles_per_side + (minx // tile_cells)
    order = np.argsort(tile_ids, kind="stable")
    tris, tile_ids = tris[order], tile_ids[order]
    bounds = np.searchsorted(tile_ids, np.arange(tiles_per_side ** 2 + 1))

    for t in range(tiles_per_side ** 2):
        chunk = tris[bounds[t]:bounds[t + 1]]
        if not len(chunk):
            continue
        ty, tx = divmod(t, tiles_per_side)
        col0, row0 = tx * tile_cells, ty * tile_cells
        corners = chunk.reshape(-1, 3, 2)                         # (M, 3, [x, y])
        flat = corners[:, :, 1] * size + corners[:, :, 0]         # node index
        uniq, inverse = np.unique(flat.ravel(), return_inverse=True)
        indices = inverse.reshape(-1, 3).astype(np.uint32)
        rows, cols = np.divmod(uniq, size)
        xs = grid.x0 + cols * grid.res - origin[0]
        ys = grid.y0 - rows * grid.res - origin[1]
        zs = z[rows, cols] - origin[2]
        positions = np.stack([xs, ys, zs], axis=1).astype(np.float64)
        uvs = np.stack([(cols - col0) / tile_cells, (rows - row0) / tile_cells], axis=1).astype(np.float32)
        indices = _wind_up(positions, indices)
        tiles.append(MeshTile(tx, ty, col0, row0, tile_cells, positions, uvs, indices))

    return TerrainMesh(tiles, tiles_per_side, float(max(chosen, 0.0)), origin)


def _wind_up(positions: np.ndarray, indices: np.ndarray) -> np.ndarray:
    """Make triangles counter-clockwise when seen from above (+Z)."""
    a, b, c = (positions[indices[:, i], :2] for i in range(3))
    signed = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    flip = signed < 0
    if flip.any():
        indices = indices.copy()
        indices[flip, 1], indices[flip, 2] = indices[flip, 2], indices[flip, 1]
    return indices
