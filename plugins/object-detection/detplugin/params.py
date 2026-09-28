"""Run parameters: defaults, types and cross-checks.

The manifest's ``params_schema`` validates scalars server-side; this module
turns the (already validated) ``params`` object into a typed ``Params`` and
resolves the "0 = model recommended" conventions against the selected model
card, so a customer can tune one model's thresholds in its card without
touching the plugin code or the manifest.
"""

from dataclasses import dataclass, field, fields

from .errors import ParameterError

TILE_MIN, TILE_MAX = 64, 4096

# Used when neither the run nor the model card says otherwise.
FALLBACK = {"confidence": 0.25, "iou": 0.45, "overlap": 64}


@dataclass
class Params:
    model: str = ""
    confidence: float = 0.0
    iou: float = 0.0
    tile_size: int = 0
    overlap: int = -1
    tile_size_m: float = 0.0
    overlap_m: float = -1.0
    max_detections: int = 5000
    class_filter: str = ""
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
        if not (0 <= self.confidence <= 1):
            raise ParameterError("confidence must be between 0 (model default) and 1")
        if not (0 <= self.iou <= 1):
            raise ParameterError("iou must be between 0 (model default) and 1")
        if self.tile_size and not (TILE_MIN <= self.tile_size <= TILE_MAX):
            raise ParameterError(f"tile_size must be 0 (model default) or between {TILE_MIN} and {TILE_MAX}")
        if self.overlap < -1:
            raise ParameterError("overlap must be -1 (model default) or >= 0")
        if self.tile_size_m < 0:
            raise ParameterError("tile_size_m must be 0 (use tile_size) or positive")
        if self.overlap_m < -1:
            raise ParameterError("overlap_m must be -1 (use overlap) or >= 0")
        if self.tile_size_m and self.overlap_m >= self.tile_size_m:
            raise ParameterError("overlap_m must be smaller than tile_size_m")
        if self.max_detections < 1:
            raise ParameterError("max_detections must be at least 1")

    def class_filter_names(self) -> list[str]:
        return [s.strip() for s in self.class_filter.replace(";", ",").replace("\n", ",").split(",") if s.strip()]

    def effective(self, card, model_input_size: tuple[int, int]) -> dict:
        """Resolve the 0/-1 conventions against the card's ``recommended`` block.

        ``model_input_size`` (height, width) is the fallback tile size: running
        the model at its native input needs no rescaling of the tile.
        """
        rec = card.recommended or {}

        def pick(name, fallback, unset):
            mine = getattr(self, name)
            if mine != unset:
                return mine
            value = rec.get(name)
            return fallback if value in (None, unset) else value

        tile_size = int(pick("tile_size", max(model_input_size), 0))
        tile_size_m = float(pick("tile_size_m", 0.0, 0.0))
        overlap_m = float(pick("overlap_m", -1.0, -1.0))
        return {
            "confidence": float(pick("confidence", FALLBACK["confidence"], 0.0)),
            "iou": float(pick("iou", FALLBACK["iou"], 0.0)),
            "tile_size": max(TILE_MIN, min(TILE_MAX, tile_size)),
            "overlap": int(pick("overlap", FALLBACK["overlap"], -1)),
            "tile_size_m": tile_size_m if tile_size_m > 0 else None,
            "overlap_m": overlap_m if overlap_m >= 0 else None,
            "max_detections": int(self.max_detections),
        }
