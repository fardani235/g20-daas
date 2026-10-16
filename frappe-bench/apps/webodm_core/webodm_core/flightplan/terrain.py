"""Terrain model: a small elevation grid over the survey site.

Elevation services answer point queries, often with a batch limit and a rate
limit, so the planner asks once for a regular grid covering the site and
interpolates from it for every flight line, waypoint and parameter change.
The grid is plain data (``to_dict`` / ``from_dict``) so it can be cached.

A terrain model is always an *estimate*: it carries where it came from, how
coarse it is and how wrong it may be, and everything computed from it is
labelled accordingly. It knows nothing about trees, buildings, masts or
wires unless the source is a surface model, and even then only as of the day
that surface was captured.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .geo import haversine_m

MIN_GRID = 2


@dataclass
class TerrainSource:
    id: str                              # provider id, or "task:<name>:<dsm|dtm>"
    label: str
    kind: str = "global-dem"             # "global-dem" | "project-raster"
    resolution_m: float | None = None    # native resolution of the data behind the grid
    vertical_accuracy_m: float | None = None   # nominal +/- (see the provider's note)
    surface: str = "terrain"             # "terrain" (bare-earth-ish) | "surface" (includes canopy/buildings)
    note: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class TerrainModel:
    south: float
    west: float
    north: float
    east: float
    rows: int
    cols: int
    values: list[list[float | None]]     # rows south -> north, columns west -> east
    source: TerrainSource
    spacing_m: float = 0.0               # spacing of the grid actually sampled

    def __post_init__(self):
        if self.rows < MIN_GRID or self.cols < MIN_GRID:
            raise ValueError("a terrain grid needs at least 2 x 2 samples")
        if len(self.values) != self.rows or any(len(r) != self.cols for r in self.values):
            raise ValueError("terrain grid values do not match its shape")

    # -- queries ---------------------------------------------------------------

    def elevation(self, lng: float, lat: float) -> float | None:
        """Bilinear elevation at a point, ``None`` outside the grid or where data is missing."""
        if not (self.south <= lat <= self.north and self.west <= lng <= self.east):
            return None
        fy = (lat - self.south) / (self.north - self.south) * (self.rows - 1)
        fx = (lng - self.west) / (self.east - self.west) * (self.cols - 1)
        r0 = min(int(math.floor(fy)), self.rows - 2)
        c0 = min(int(math.floor(fx)), self.cols - 2)
        ty, tx = fy - r0, fx - c0
        corners = [
            (self.values[r0][c0], (1 - tx) * (1 - ty)),
            (self.values[r0][c0 + 1], tx * (1 - ty)),
            (self.values[r0 + 1][c0], (1 - tx) * ty),
            (self.values[r0 + 1][c0 + 1], tx * ty),
        ]
        known = [(v, w) for v, w in corners if v is not None]
        if not known:
            return None
        weight = sum(w for _v, w in known)
        if weight <= 1e-9:
            # Exactly on a corner whose own value is missing.
            return sum(v for v, _w in known) / len(known)
        return sum(v * w for v, w in known) / weight

    @property
    def coverage(self) -> float:
        """Fraction of grid nodes that have a value."""
        total = self.rows * self.cols
        return sum(1 for row in self.values for v in row if v is not None) / total if total else 0.0

    def range(self) -> tuple[float, float] | None:
        vals = [v for row in self.values for v in row if v is not None]
        return (min(vals), max(vals)) if vals else None

    def covers(self, lng: float, lat: float) -> bool:
        return self.elevation(lng, lat) is not None

    # -- serialisation ---------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "south": self.south, "west": self.west, "north": self.north, "east": self.east,
            "rows": self.rows, "cols": self.cols, "values": self.values,
            "spacing_m": self.spacing_m, "source": self.source.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TerrainModel":
        return cls(
            south=data["south"], west=data["west"], north=data["north"], east=data["east"],
            rows=data["rows"], cols=data["cols"], values=data["values"],
            spacing_m=data.get("spacing_m") or 0.0, source=TerrainSource(**data["source"]),
        )


@dataclass
class GridSpec:
    south: float
    west: float
    north: float
    east: float
    rows: int
    cols: int
    spacing_m: float

    def points(self) -> list[tuple[float, float]]:
        """``(lat, lng)`` of every node, row by row from the south-west corner."""
        out = []
        for r in range(self.rows):
            lat = self.south + (self.north - self.south) * r / (self.rows - 1)
            for c in range(self.cols):
                out.append((lat, self.west + (self.east - self.west) * c / (self.cols - 1)))
        return out


def grid_spec(bounds: tuple[float, float, float, float], *, max_points: int = 400, min_spacing_m: float = 30.0,
              pad_m: float = 60.0, snap_deg: float = 0.001) -> GridSpec:
    """A sampling grid over ``bounds`` = ``(west, south, east, north)`` in degrees.

    The box is padded (so the takeoff point and line overshoots stay inside)
    and snapped outward to ``snap_deg`` so that small edits of the survey
    boundary reuse the same cached grid. Nodes are no closer than
    ``min_spacing_m`` (sampling a 30 m DEM every 5 m buys nothing) and there
    are at most ``max_points`` of them.
    """
    west, south, east, north = bounds
    if east < west or north < south:
        raise ValueError("invalid bounds")
    mid_lat = (south + north) / 2.0
    m_per_deg_lat = 111320.0
    m_per_deg_lng = max(1.0, 111320.0 * math.cos(math.radians(mid_lat)))
    pad_lat = pad_m / m_per_deg_lat
    pad_lng = pad_m / m_per_deg_lng

    def down(v):
        return math.floor(v / snap_deg) * snap_deg

    def up(v):
        return math.ceil(v / snap_deg) * snap_deg

    south, north = max(-90.0, down(south - pad_lat)), min(90.0, up(north + pad_lat))
    west, east = down(west - pad_lng), up(east + pad_lng)
    height_m = max(1.0, haversine_m(west, south, west, north))
    width_m = max(1.0, haversine_m(west, mid_lat, east, mid_lat))

    max_points = max(MIN_GRID * MIN_GRID, int(max_points))
    spacing = max(min_spacing_m, 1.0)
    while True:
        rows = max(MIN_GRID, int(math.ceil(height_m / spacing)) + 1)
        cols = max(MIN_GRID, int(math.ceil(width_m / spacing)) + 1)
        if rows * cols <= max_points:
            break
        # Grow the spacing just enough for the node count to fit.
        spacing *= max(1.02, math.sqrt(rows * cols / max_points))
    actual = max(height_m / (rows - 1), width_m / (cols - 1))
    return GridSpec(south=south, west=west, north=north, east=east, rows=rows, cols=cols, spacing_m=actual)


def model_from_samples(spec: GridSpec, samples: list[float | None], source: TerrainSource) -> TerrainModel:
    if len(samples) != spec.rows * spec.cols:
        raise ValueError("elevation service returned a different number of samples than requested")
    values = [list(samples[r * spec.cols:(r + 1) * spec.cols]) for r in range(spec.rows)]
    return TerrainModel(south=spec.south, west=spec.west, north=spec.north, east=spec.east, rows=spec.rows,
                        cols=spec.cols, values=values, source=source, spacing_m=spec.spacing_m)
