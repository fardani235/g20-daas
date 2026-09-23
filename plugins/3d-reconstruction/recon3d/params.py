"""Run parameters: defaults, quality presets and cross-checks.

The manifest's ``params_schema`` validates scalar types and enums server-side.
This module turns the validated ``params`` object into a typed ``Params`` and
resolves the "0 = automatic / from preset" conventions.

Quality presets play the role ODM presets play for processing: a named bundle
of tuned values a user can pick without understanding every knob. Any value set
explicitly (non-zero) overrides the preset.
"""

from dataclasses import dataclass, field, fields

from .errors import ParameterError

WORKFLOWS = ("auto", "terrain", "optimize-model")
SURFACE_SOURCES = ("auto", "dsm", "dtm", "point_cloud")
TEXTURE_SOURCES = ("auto", "orthophoto", "point_cloud", "hillshade")
MESH_METHODS = ("adaptive", "grid")
POINT_STATS = ("max", "mean", "min")

# name -> (max_triangles, texture_size, max texture tiles per side, jpeg quality)
PRESETS = {
    "web-light": {"max_triangles": 150_000, "texture_size": 2048, "texture_tiles": 1, "texture_quality": 80},
    "balanced": {"max_triangles": 500_000, "texture_size": 4096, "texture_tiles": 2, "texture_quality": 85},
    "high-detail": {"max_triangles": 1_500_000, "texture_size": 4096, "texture_tiles": 4, "texture_quality": 90},
}
DEFAULT_PRESET = "balanced"

# Largest square heightfield the adaptive mesher works on (2^k + 1 cells per
# side). 2049² is 4.2 M cells / up to 8.4 M candidate triangles and fits the
# sandbox's 2 GB address-space limit with room for textures.
MAX_GRID_SIZE = 2049
MIN_GRID_SIZE = 17

TEXTURE_TILE_CHOICES = (0, 1, 2, 4)


@dataclass
class Params:
    workflow: str = "auto"
    quality: str = DEFAULT_PRESET
    surface_source: str = "auto"
    texture_source: str = "auto"
    mesh_method: str = "adaptive"
    resolution_m: float = 0.0
    max_triangles: int = 0
    max_error_m: float = 0.0
    texture_size: int = 0
    texture_tiles: int = 0
    texture_quality: int = 0
    quantize: bool = True
    fill_holes: bool = True
    clip_to_texture: bool = True
    point_cloud_cell_m: float = 0.0
    point_cloud_stat: str = "max"
    unknown: dict = field(default_factory=dict)

    @classmethod
    def from_request(cls, raw: dict | None) -> "Params":
        raw = dict(raw or {})
        values = {}
        for f in fields(cls):
            if f.name == "unknown" or f.name not in raw:
                continue
            value = raw.pop(f.name)
            if value is None or value == "":
                continue
            try:
                if f.type is bool:
                    values[f.name] = _as_bool(value)
                elif f.type is int:
                    values[f.name] = int(value)
                elif f.type is float:
                    values[f.name] = float(value)
                else:
                    values[f.name] = str(value)
            except (TypeError, ValueError):
                raise ParameterError(f"parameter '{f.name}' must be a {f.type.__name__}")
        p = cls(**values)
        p.unknown = raw
        p._check()
        return p

    def _check(self):
        _enum("workflow", self.workflow, WORKFLOWS)
        _enum("quality", self.quality, tuple(PRESETS))
        _enum("surface_source", self.surface_source, SURFACE_SOURCES)
        _enum("texture_source", self.texture_source, TEXTURE_SOURCES)
        _enum("mesh_method", self.mesh_method, MESH_METHODS)
        _enum("point_cloud_stat", self.point_cloud_stat, POINT_STATS)
        if self.resolution_m < 0:
            raise ParameterError("resolution_m must be 0 (automatic) or positive")
        if self.max_triangles < 0:
            raise ParameterError("max_triangles must be 0 (preset) or positive")
        if self.max_error_m < 0:
            raise ParameterError("max_error_m must be 0 (automatic) or positive")
        if self.texture_size and not (256 <= self.texture_size <= 8192):
            raise ParameterError("texture_size must be 0 (preset) or between 256 and 8192")
        if self.texture_tiles not in TEXTURE_TILE_CHOICES:
            raise ParameterError("texture_tiles must be 0 (automatic), 1, 2 or 4")
        if self.texture_quality and not (1 <= self.texture_quality <= 100):
            raise ParameterError("texture_quality must be 0 (preset) or between 1 and 100")
        if self.point_cloud_cell_m < 0:
            raise ParameterError("point_cloud_cell_m must be 0 (automatic) or positive")

    def preset(self) -> dict:
        return PRESETS[self.quality]

    def effective(self) -> dict:
        """Resolve the 0/auto conventions against the quality preset."""
        p = self.preset()
        return {
            "max_triangles": self.max_triangles or p["max_triangles"],
            "texture_size": self.texture_size or p["texture_size"],
            "max_texture_tiles": self.texture_tiles or p["texture_tiles"],
            "texture_tiles_fixed": bool(self.texture_tiles),
            "texture_quality": self.texture_quality or p["texture_quality"],
        }


def _enum(name, value, choices):
    if value not in choices:
        raise ParameterError(f"{name} must be one of: {', '.join(choices)}")


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    raise ValueError(value)
