"""Nadir photogrammetry arithmetic: what a camera sees from a height.

All of it follows from similar triangles through the lens:

    ground size / height above ground = sensor size / focal length

``orientation`` says which side of the sensor lies across the flight line.
``landscape`` (the usual gimbal mounting) puts the long side across track,
so lines can be further apart; ``portrait`` puts the short side across
track.
"""

from __future__ import annotations

from dataclasses import dataclass

from .profiles import Camera

ORIENTATIONS = ("landscape", "portrait")


@dataclass(frozen=True)
class Footprint:
    """Ground coverage of one nadir photo at a given height above ground."""

    height_agl_m: float
    gsd_m: float           # ground sample distance, metres per pixel
    across_m: float        # extent perpendicular to the flight line
    along_m: float         # extent along the flight line

    @property
    def gsd_cm(self) -> float:
        return self.gsd_m * 100.0


def gsd_m(camera: Camera, height_agl_m: float) -> float:
    """Ground sample distance (m/px) of a nadir photo from ``height_agl_m``.

    Computed from the sensor width; with the square pixels every mapping
    camera has, the height gives the same number.
    """
    return camera.sensor_width_mm * height_agl_m / (camera.focal_length_mm * camera.image_width_px)


def height_for_gsd(camera: Camera, gsd_metres: float) -> float:
    """Height above ground that gives ``gsd_metres`` per pixel."""
    return gsd_metres * camera.focal_length_mm * camera.image_width_px / camera.sensor_width_mm


def footprint(camera: Camera, height_agl_m: float, orientation: str = "landscape") -> Footprint:
    width = camera.sensor_width_mm * height_agl_m / camera.focal_length_mm
    height = camera.sensor_height_mm * height_agl_m / camera.focal_length_mm
    long_side, short_side = max(width, height), min(width, height)
    across, along = (long_side, short_side) if orientation != "portrait" else (short_side, long_side)
    return Footprint(height_agl_m=height_agl_m, gsd_m=gsd_m(camera, height_agl_m), across_m=across, along_m=along)


def line_spacing(fp: Footprint, side_overlap_pct: float) -> float:
    """Distance between adjacent flight lines for the wanted side overlap."""
    return fp.across_m * (1.0 - side_overlap_pct / 100.0)


def photo_spacing(fp: Footprint, front_overlap_pct: float) -> float:
    """Distance between consecutive exposures for the wanted front overlap."""
    return fp.along_m * (1.0 - front_overlap_pct / 100.0)


def overlap_at(base_m: float, extent_at_reference_m: float, reference_agl_m: float, actual_agl_m: float) -> float:
    """Overlap (percent) actually achieved where the ground is closer or further than planned.

    ``base_m`` is the fixed spacing flown (between lines or between photos),
    ``extent_at_reference_m`` the footprint extent at ``reference_agl_m``.
    The footprint scales with height above ground, the spacing does not, so
    rising ground eats overlap. May be negative: a gap.
    """
    if actual_agl_m <= 0 or reference_agl_m <= 0:
        return -100.0
    extent = extent_at_reference_m * actual_agl_m / reference_agl_m
    return (1.0 - base_m / extent) * 100.0


def motion_blur_px(speed_ms: float, shutter_s: float, gsd_metres: float) -> float:
    """Pixels the ground moves across the sensor during one exposure."""
    if gsd_metres <= 0:
        return 0.0
    return speed_ms * shutter_s / gsd_metres
