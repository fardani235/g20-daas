"""Run parameters: defaults, types and cross-checks.

The manifest's ``params_schema`` validates scalars server-side; this module
turns the (already validated) ``params`` object into a typed ``Params`` and
resolves the "0 = model recommended" conventions against the selected model.
"""

from dataclasses import dataclass, field, fields

from .errors import ParameterError

DEFAULTS = {
    "model": "auto",
    "resolution_m": 0.0,
    "tile_size": 0,
    "overlap": -1,
    "min_segment_area_m2": 2.0,
    "smoothing_radius_px": 1,
    "class_filter": "",
    "simplify_tolerance_m": 0.25,
    "height_weight": 1.0,
    "elevated_threshold_m": 0.0,
    "ground_window_m": 40.0,
    "low_threshold_m": 0.5,
    "tall_threshold_m": 2.5,
    "vegetation_index_threshold": 0.06,
    "geomorphon_search_m": 20.0,
    "geomorphon_skip_m": 0.0,
    "geomorphon_flatness_deg": 1.0,
}


@dataclass
class Params:
    model: str = "auto"
    resolution_m: float = 0.0
    tile_size: int = 0
    overlap: int = -1
    min_segment_area_m2: float = 2.0
    smoothing_radius_px: int = 1
    class_filter: str = ""
    simplify_tolerance_m: float = 0.25
    height_weight: float = 1.0
    elevated_threshold_m: float = 0.0
    ground_window_m: float = 40.0
    low_threshold_m: float = 0.5
    tall_threshold_m: float = 2.5
    vegetation_index_threshold: float = 0.06
    geomorphon_search_m: float = 20.0
    geomorphon_skip_m: float = 0.0
    geomorphon_flatness_deg: float = 1.0
    unknown: dict = field(default_factory=dict)

    @classmethod
    def from_request(cls, raw: dict | None) -> "Params":
        raw = dict(raw or {})
        values = {}
        for f in fields(cls):
            if f.name == "unknown":
                continue
            if f.name in raw:
                value = raw.pop(f.name)
                if value is None or value == "":
                    continue
                try:
                    values[f.name] = f.type(value) if f.type in (int, float) else str(value)
                except (TypeError, ValueError):
                    raise ParameterError(f"parameter '{f.name}' must be a {f.type.__name__}")
        p = cls(**values)
        p.unknown = raw
        p._check()
        return p

    def _check(self):
        if self.resolution_m < 0:
            raise ParameterError("resolution_m must be 0 (model default) or positive")
        if self.tile_size and not (64 <= self.tile_size <= 4096):
            raise ParameterError("tile_size must be 0 (model default) or between 64 and 4096")
        if self.overlap < -1:
            raise ParameterError("overlap must be -1 (model default) or >= 0")
        if self.min_segment_area_m2 < 0:
            raise ParameterError("min_segment_area_m2 must be >= 0")
        if not (0 <= self.smoothing_radius_px <= 8):
            raise ParameterError("smoothing_radius_px must be between 0 and 8")
        if self.height_weight < 0:
            raise ParameterError("height_weight must be >= 0")
        if self.ground_window_m <= 0:
            raise ParameterError("ground_window_m must be positive")

    def class_filter_names(self) -> list[str]:
        return [s.strip() for s in self.class_filter.replace(";", ",").split(",") if s.strip()]

    def effective(self, card) -> dict:
        """Resolve 0/auto conventions against the model card's recommendations."""
        rec = card.recommended or {}
        return {
            "resolution_m": self.resolution_m if self.resolution_m > 0 else float(rec.get("resolution_m") or 0.0),
            "tile_size": int(self.tile_size or rec.get("tile_size") or 0),
            "overlap": int(self.overlap if self.overlap >= 0 else rec.get("overlap", 64)),
            "elevated_threshold_m": self.elevated_threshold_m if self.elevated_threshold_m > 0 else None,
        }
