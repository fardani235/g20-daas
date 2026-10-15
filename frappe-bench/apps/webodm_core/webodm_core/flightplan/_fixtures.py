"""Shared builders for the flight-planning tests. Not imported by production code."""

from __future__ import annotations

import math

from .geo import LocalFrame
from .profiles import builtin_cameras, builtin_drones
from .terrain import GridSpec, TerrainModel, TerrainSource, grid_spec, model_from_samples

# A site in the hills north of Bandung, Indonesia.
SITE = (107.6100, -6.8200)   # lng, lat


def drone(drone_id: str = "dji-mavic-3e"):
    return builtin_drones()[drone_id]


def camera(camera_id: str | None = None, drone_id: str = "dji-mavic-3e"):
    return builtin_cameras()[camera_id or builtin_drones()[drone_id].default_camera]


def frame(origin=SITE) -> LocalFrame:
    return LocalFrame(origin[1], origin[0])


def lnglat(points_m, origin=SITE):
    """Local metres (x east, y north) around ``origin`` -> ``[lng, lat]`` pairs."""
    f = frame(origin)
    return [list(f.inverse(x, y)) for x, y in points_m]


def rect(width=400.0, height=300.0, origin=SITE, rotate_deg=0.0):
    """GeoJSON polygon of a rectangle centred on ``origin``."""
    hw, hh = width / 2, height / 2
    pts = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
    if rotate_deg:
        a = math.radians(rotate_deg)
        pts = [(x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)) for x, y in pts]
    ring = lnglat(pts, origin)
    return {"type": "Polygon", "coordinates": [ring + [ring[0]]]}


def polygon(points_m, origin=SITE, holes=()):
    ring = lnglat(points_m, origin)
    rings = [ring + [ring[0]]]
    for hole in holes:
        h = lnglat(hole, origin)
        rings.append(h + [h[0]])
    return {"type": "Polygon", "coordinates": rings}


def line(points_m, origin=SITE):
    return {"type": "LineString", "coordinates": lnglat(points_m, origin)}


def area_plan(geometry=None, takeoff_m=None, **params):
    plan = {"type": "area", "geometry": geometry or rect(), "params": params}
    if takeoff_m is not None:
        plan["takeoff"] = lnglat([takeoff_m])[0]
    return plan


def corridor_plan(points_m=((-600, 0), (0, 0), (500, 300)), width=120.0, takeoff_m=None, **params):
    plan = {"type": "corridor", "geometry": line(points_m), "corridor_width_m": width, "params": params}
    if takeoff_m is not None:
        plan["takeoff"] = lnglat([takeoff_m])[0]
    return plan


def terrain(surface, *, half_extent_m=1500.0, spacing_m=30.0, origin=SITE, max_points=40000,
            source: TerrainSource | None = None, hole=None) -> TerrainModel:
    """A terrain model sampled from ``surface(x, y) -> elevation`` (local metres around ``origin``).

    ``hole(x, y) -> bool`` marks nodes with no data.
    """
    f = frame(origin)
    west, south = f.inverse(-half_extent_m, -half_extent_m)
    east, north = f.inverse(half_extent_m, half_extent_m)
    spec = grid_spec((west, south, east, north), max_points=max_points, min_spacing_m=spacing_m, pad_m=0.0,
                     snap_deg=1e-9)
    samples = []
    for lat, lng in spec.points():
        x, y = f.forward(lng, lat)
        samples.append(None if hole and hole(x, y) else float(surface(x, y)))
    return model_from_samples(spec, samples, source or TerrainSource(
        id="test-dem", label="Test DEM", resolution_m=spacing_m, vertical_accuracy_m=5.0))


def flat(elevation=700.0):
    return lambda x, y: elevation


def slope(base=700.0, per_metre_east=0.1, per_metre_north=0.0):
    return lambda x, y: base + per_metre_east * x + per_metre_north * y


def ridge(base=700.0, height=60.0, half_width=120.0):
    """A north-south ridge through the middle of the site."""
    return lambda x, y: base + height * max(0.0, 1.0 - abs(x) / half_width)


def codes(mission, severity=None):
    return [i["code"] for i in mission["issues"] if severity is None or i["severity"] == severity]


def assurance(mission, topic):
    return next(a for a in mission["assurance"] if a["topic"] == topic)
