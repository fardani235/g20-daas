"""Run parameters, quality profiles and what the task's ODM preset implies.

Users pick a ``quality`` profile (or leave it on ``auto``) and may override
individual budgets. ``auto`` reads the ODM processing options the task was
run with — a task processed with the "3D Model" preset (``pc-quality high``,
``mesh-size 300000``) deserves a denser web model than a "Fast Orthophoto"
task — so the plugin follows the fidelity the operator already chose.
"""

from dataclasses import dataclass, field

from .errors import ParameterError

WORKFLOWS = ("auto", "terrain", "point-cloud", "points", "mesh")
QUALITIES = ("auto", "web-lite", "balanced", "high-detail")
SURFACES = ("dsm", "dtm")
TEXTURE_SOURCES = ("auto", "orthophoto", "point-cloud", "shaded-relief")
STATISTICS = ("max", "mean", "min")
COMPRESSIONS = ("draco", "none")

# Budgets per quality profile.
PROFILES = {
    "web-lite": {"max_triangles": 150_000, "texture_size": 2048, "max_points": 500_000, "texture_budget_mp": 16},
    "balanced": {"max_triangles": 500_000, "texture_size": 4096, "max_points": 2_000_000, "texture_budget_mp": 48},
    "high-detail": {"max_triangles": 1_500_000, "texture_size": 8192, "max_points": 5_000_000, "texture_budget_mp": 128},
}

DEFAULTS = {
    "workflow": "auto",
    "quality": "auto",
    "surface": "dsm",
    "resolution_m": 0.0,
    "max_triangles": 0,
    "texture_size": 0,
    "texture_quality": 85,
    "texture_source": "auto",
    "texture_budget_mp": 0,
    "fill_holes": True,
    "point_classes": "",
    "point_statistic": "max",
    "max_points": 0,
    "compression": "draco",
}

# ODM options whose values hint at the fidelity the operator asked for.
_HIGH_PC_QUALITY = {"high", "ultra"}
_LOW_PC_QUALITY = {"low", "lowest"}


@dataclass
class Params:
    workflow: str
    quality: str  # resolved profile name (never "auto")
    quality_reason: str
    surface: str
    resolution_m: float
    max_triangles: int
    texture_size: int
    texture_quality: int
    texture_source: str
    texture_budget_mp: int
    fill_holes: bool
    point_classes: str
    point_statistic: str
    max_points: int
    compression: str
    overrides: dict = field(default_factory=dict)

    @property
    def draco(self) -> bool:
        return self.compression == "draco"

    def summary(self) -> dict:
        return {
            "workflow": self.workflow, "quality": self.quality, "quality_reason": self.quality_reason,
            "surface": self.surface, "resolution_m": self.resolution_m, "max_triangles": self.max_triangles,
            "texture_size": self.texture_size, "texture_quality": self.texture_quality,
            "texture_source": self.texture_source, "texture_budget_mp": self.texture_budget_mp,
            "fill_holes": self.fill_holes, "point_classes": self.point_classes,
            "point_statistic": self.point_statistic, "max_points": self.max_points,
            "compression": self.compression,
        }


def odm_options(context: dict | None) -> dict:
    """The task's ODM options as ``{name: value}`` whatever shape the platform stored them in."""
    task = (context or {}).get("task") or {}
    raw = task.get("processing_options")
    if isinstance(raw, str):
        import json
        for _ in range(3):
            try:
                raw = json.loads(raw)
            except (TypeError, ValueError):
                break
            if not isinstance(raw, str):
                break
    if isinstance(raw, list):
        return {str(o.get("name")): o.get("value") for o in raw if isinstance(o, dict) and o.get("name")}
    if isinstance(raw, dict):
        return {str(k): v for k, v in raw.items()}
    return {}


def _as_int(value, default=0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def quality_from_odm(options: dict) -> tuple[str, str]:
    """Pick a profile from the ODM options; returns ``(profile, reason)``."""
    pcq = str(options.get("pc-quality", "")).lower()
    mesh_size = _as_int(options.get("mesh-size"), 0)
    octree = _as_int(options.get("mesh-octree-depth"), 0)
    fast = str(options.get("fast-orthophoto", "")).lower() in ("true", "1", "yes")
    if pcq in _HIGH_PC_QUALITY or mesh_size >= 300_000 or octree >= 12:
        return "high-detail", f"ODM options ask for a dense model (pc-quality={pcq or 'default'}, mesh-size={mesh_size or 'default'}, mesh-octree-depth={octree or 'default'})"
    if fast or pcq in _LOW_PC_QUALITY:
        return "web-lite", f"ODM options favour speed (fast-orthophoto={fast}, pc-quality={pcq or 'default'})"
    if options:
        return "balanced", "ODM options are default-ish"
    return "balanced", "no ODM options available"


def _choice(name, value, allowed):
    value = str(value)
    if value not in allowed:
        raise ParameterError(f"{name} must be one of {', '.join(allowed)}, got {value!r}")
    return value


def _number(name, value, lo=None, hi=None, integer=False):
    try:
        v = int(float(value)) if integer else float(value)
    except (TypeError, ValueError) as e:
        raise ParameterError(f"{name} must be a number") from e
    if lo is not None and v < lo:
        raise ParameterError(f"{name} must be >= {lo}")
    if hi is not None and v > hi:
        raise ParameterError(f"{name} must be <= {hi}")
    return v


def parse(raw: dict | None, context: dict | None = None) -> Params:
    p = {**DEFAULTS, **(raw or {})}
    unknown = set(p) - set(DEFAULTS)
    if unknown:
        raise ParameterError(f"unknown parameter(s): {', '.join(sorted(unknown))}")

    quality = _choice("quality", p["quality"], QUALITIES)
    if quality == "auto":
        quality, reason = quality_from_odm(odm_options(context))
    else:
        reason = "chosen by the user"
    profile = PROFILES[quality]

    def budget(name, lo, hi):
        v = _number(name, p[name], 0, hi, integer=True)
        return v if v else profile[name], v != 0

    max_triangles, o1 = budget("max_triangles", 0, 20_000_000)
    texture_size, o2 = budget("texture_size", 0, 8192)
    max_points, o3 = budget("max_points", 0, 50_000_000)
    texture_budget_mp, o4 = budget("texture_budget_mp", 0, 1024)
    if texture_size < 256:
        raise ParameterError("texture_size must be 0 (profile default) or at least 256")
    if max_triangles < 1000:
        raise ParameterError("max_triangles must be 0 (profile default) or at least 1000")
    if max_points < 1000:
        raise ParameterError("max_points must be 0 (profile default) or at least 1000")

    fill = p["fill_holes"]
    if isinstance(fill, str):
        fill = fill.strip().lower() in ("1", "true", "yes", "on")

    return Params(
        workflow=_choice("workflow", p["workflow"], WORKFLOWS),
        quality=quality,
        quality_reason=reason,
        surface=_choice("surface", p["surface"], SURFACES),
        resolution_m=_number("resolution_m", p["resolution_m"], 0.0, 1000.0),
        max_triangles=max_triangles,
        texture_size=texture_size,
        texture_quality=_number("texture_quality", p["texture_quality"], 40, 95, integer=True),
        texture_source=_choice("texture_source", p["texture_source"], TEXTURE_SOURCES),
        texture_budget_mp=texture_budget_mp,
        fill_holes=bool(fill),
        point_classes=str(p["point_classes"] or ""),
        point_statistic=_choice("point_statistic", p["point_statistic"], STATISTICS),
        max_points=max_points,
        compression=_choice("compression", p["compression"], COMPRESSIONS),
        overrides={k: True for k, v in (("max_triangles", o1), ("texture_size", o2), ("max_points", o3),
                                        ("texture_budget_mp", o4)) if v},
    )
