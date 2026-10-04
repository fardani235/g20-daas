"""Run parameters: defaults, types, cross-checks and the "use the card" convention.

The manifest's ``params_schema`` validates scalars server-side; this module
turns the (already validated) ``params`` object into a typed ``Params`` and
resolves the sentinel values against the selected model card:

* ``0`` means "card's recommended value" for parameters where zero is not a
  meaningful setting (resolution, tile size, thresholds, seed distance, ...);
* ``-1`` means "card's recommended value" for parameters where ``0`` is a
  legitimate choice (``overlap``, ``min_area_m2``, ``simplify_m``,
  ``regularize_overlap_tol_m2``);
* the string ``"auto"`` (or an empty value) means the same for any parameter,
  including ``model``.
"""

from dataclasses import dataclass, field, fields

from .errors import ParameterError

AUTO = ("auto", "")


@dataclass
class Params:
    model: str = "auto"
    resolution_m: float = 0.0
    tile_size: int = 0
    overlap: int = -1
    confidence: float = 0.0
    seed_min_distance: int = 0
    large_blob_area_px: int = 0
    h_maxima_depth: float = 0.0
    min_area_m2: float = -1.0
    simplify_m: float = -1.0
    regularize_area_threshold: float = 0.0
    regularize_overlap_tol_m2: float = -1.0
    unknown: dict = field(default_factory=dict)

    @classmethod
    def from_request(cls, raw: dict | None) -> "Params":
        raw = dict(raw or {})
        values = {}
        for f in fields(cls):
            if f.name == "unknown":
                continue
            if f.name not in raw:
                continue
            value = raw.pop(f.name)
            if value is None or (isinstance(value, str) and value.strip().lower() in AUTO):
                continue  # sentinel: keep the dataclass default, resolved against the card later
            try:
                values[f.name] = f.type(value) if f.type in (int, float) else str(value)
            except (TypeError, ValueError):
                raise ParameterError(f"parameter '{f.name}' must be a {f.type.__name__} (or 'auto')")
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
        if not (0 <= self.confidence <= 1):
            raise ParameterError("confidence must be between 0 and 1 (0 = model default)")
        if self.seed_min_distance < 0:
            raise ParameterError("seed_min_distance must be 0 (model default) or positive")
        if self.large_blob_area_px < 0:
            raise ParameterError("large_blob_area_px must be 0 (model default) or positive")
        if not (0 <= self.h_maxima_depth <= 2):
            raise ParameterError("h_maxima_depth must be between 0 (model default) and 2")
        if self.min_area_m2 < -1:
            raise ParameterError("min_area_m2 must be -1 (model default) or >= 0")
        if self.simplify_m < -1:
            raise ParameterError("simplify_m must be -1 (model default) or >= 0")
        if not (0 <= self.regularize_area_threshold <= 1):
            raise ParameterError("regularize_area_threshold must be between 0 (model default) and 1")
        if self.regularize_overlap_tol_m2 < -1:
            raise ParameterError("regularize_overlap_tol_m2 must be -1 (model default) or >= 0")

    def effective(self, card) -> dict:
        """Resolve the sentinels against the card's ``recommended`` values."""
        rec = card.recommended

        def pick(name, value, sentinel):
            use_card = value == sentinel if sentinel == -1 else value <= 0
            if not use_card:
                return value
            if name not in rec:
                raise ParameterError(
                    f"parameter '{name}' is left at its model default but model card '{card.id}' "
                    f"has no recommended.{name}; set a value"
                )
            return rec[name]

        tile = self.tile_size or rec.get("tile_size") or card.input_size
        return {
            "resolution_m": float(pick("resolution_m", self.resolution_m, 0)),
            "tile_size": int(tile),
            "overlap": int(pick("overlap", self.overlap, -1)),
            "confidence": float(pick("confidence", self.confidence, 0)),
            "seed_min_distance": int(pick("seed_min_distance", self.seed_min_distance, 0)),
            "large_blob_area_px": int(pick("large_blob_area_px", self.large_blob_area_px, 0)),
            "h_maxima_depth": float(pick("h_maxima_depth", self.h_maxima_depth, 0)),
            "min_area_m2": float(pick("min_area_m2", self.min_area_m2, -1)),
            "simplify_m": float(pick("simplify_m", self.simplify_m, -1)),
            "regularize_area_threshold": float(pick("regularize_area_threshold", self.regularize_area_threshold, 0)),
            "regularize_overlap_tol_m2": float(pick("regularize_overlap_tol_m2", self.regularize_overlap_tol_m2, -1)),
        }

    def raw(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "unknown"}
