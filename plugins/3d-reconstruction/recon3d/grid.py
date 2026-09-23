"""The working grid: a square lattice of *nodes* in a projected CRS.

Node (col, row) sits at ``(x0 + col*res, y0 - row*res)``. Rasters are resampled
so that their pixel centres coincide with the nodes (``node_transform``), which
makes vertex positions and texture coordinates exact by construction: a tile
covering nodes ``[c0, c0+n]`` spans world ``[x0 + c0*res, x0 + (c0+n)*res]`` and
a texture rendered for that extent maps to it with ``u = (col - c0) / n``.
"""

import math
from dataclasses import dataclass

from rasterio.crs import CRS
from rasterio.transform import Affine

from . import rtin


@dataclass(frozen=True)
class Grid:
    crs: CRS
    x0: float        # x of node column 0 (west edge)
    y0: float        # y of node row 0 (north edge)
    res: float       # node spacing in CRS units (metres)
    width: int       # nodes per row
    height: int      # nodes per column

    @property
    def node_transform(self) -> Affine:
        """Raster transform whose pixel centres are the nodes."""
        return Affine(self.res, 0.0, self.x0 - self.res / 2.0, 0.0, -self.res, self.y0 + self.res / 2.0)

    @property
    def bounds(self):
        """Footprint of the nodes (west, south, east, north)."""
        return (self.x0, self.y0 - (self.height - 1) * self.res, self.x0 + (self.width - 1) * self.res, self.y0)

    @property
    def extent_m(self) -> float:
        return max(self.width, self.height) * self.res

    def sub_extent(self, col0: int, row0: int, cells: int):
        """World bounds of the square of ``cells`` cells starting at node (col0, row0)."""
        west = self.x0 + col0 * self.res
        north = self.y0 - row0 * self.res
        return (west, north - cells * self.res, west + cells * self.res, north)

    @staticmethod
    def square_for(crs: CRS, bounds, res: float, max_size: int, min_size: int = 3) -> "Grid":
        """Smallest 2^k+1 node square at spacing ``res`` covering ``bounds``.

        The square is anchored at the north-west corner; nodes beyond the
        data become no-data and produce no triangles. If ``res`` would need
        more than ``max_size`` nodes the spacing is coarsened to fit.
        """
        west, south, east, north = bounds
        extent = max(east - west, north - south)
        if extent <= 0 or res <= 0:
            raise ValueError("empty bounds or non-positive resolution")
        size = rtin.next_valid_size(math.ceil(extent / res) + 1)
        size = max(size, min_size)
        if size > max_size:
            size = max_size
            res = extent / (size - 1)
        return Grid(crs, float(west), float(north), float(res), size, size)
