"""Run parameters: defaults, types and cross-checks.

The manifest's ``params_schema`` validates scalars server-side; this module
turns the (already validated) ``params`` object into a typed ``Params``. A
missing, empty or ``"auto"`` value keeps the default, so the run dialog's
"Default" choice and API clients that send nothing behave the same.
"""

from dataclasses import dataclass, field, fields

from .errors import ParameterError

AUTO = ("auto", "")

REFERENCE_ROLES = ("before", "after")
VERTICAL_MODES = ("auto", "tilt", "none")
HORIZONTAL_MODES = ("auto", "off")
# Two-sided normal quantiles: a height change must exceed k standard
# deviations of the measured uncertainty to count.
CONFIDENCE_K = {"80": 1.282, "95": 1.960, "99": 2.576}

_BOOL_TRUE = ("true", "1", "yes", "on")
_BOOL_FALSE = ("false", "0", "no", "off")


@dataclass
class Params:
    reference_role: str = "before"
    vertical_alignment: str = "auto"
    horizontal_alignment: str = "auto"
    max_shift_m: float = 3.0
    confidence: str = "95"
    min_height_change_m: float = 0.0       # 0 = derived from the measured noise
    min_area_m2: float = 4.0
    registration_tolerance_m: float = -1.0  # -1 = one grid cell, 0 = off
    edge_buffer_m: float = 2.0
    resolution_m: float = 0.0               # 0 = the coarser survey's own resolution
    vegetation_filter: bool = True
    appearance_change: bool = True
    mask_insignificant: bool = True         # raster output only
    unknown: dict = field(default_factory=dict)

    @classmethod
    def from_request(cls, raw: dict | None) -> "Params":
        raw = dict(raw or {})
        values = {}
        for f in fields(cls):
            if f.name == "unknown" or f.name not in raw:
                continue
            value = raw.pop(f.name)
            if value is None:
                continue
            if f.type is bool:
                values[f.name] = _as_bool(f.name, value)
                continue
            # "auto" is itself a value of the two alignment enums.
            if isinstance(value, str) and value.strip().lower() in AUTO and f.type is not str:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            try:
                values[f.name] = float(value) if f.type is float else str(value).strip().lower()
            except (TypeError, ValueError):
                raise ParameterError(f"parameter '{f.name}' must be a number")
        p = cls(**values)
        p.unknown = raw
        p._check()
        return p

    def _check(self):
        self.confidence = self.confidence.rstrip("%").strip()
        _one_of("reference_role", self.reference_role, REFERENCE_ROLES)
        _one_of("vertical_alignment", self.vertical_alignment, VERTICAL_MODES)
        _one_of("horizontal_alignment", self.horizontal_alignment, HORIZONTAL_MODES)
        _one_of("confidence", self.confidence, tuple(CONFIDENCE_K))
        if not (0 <= self.max_shift_m <= 20):
            raise ParameterError("max_shift_m must be between 0 and 20")
        if self.min_height_change_m < 0:
            raise ParameterError("min_height_change_m must be 0 (automatic) or positive")
        if self.min_area_m2 < 0:
            raise ParameterError("min_area_m2 must be 0 or positive")
        if self.registration_tolerance_m < 0 and self.registration_tolerance_m != -1:
            raise ParameterError("registration_tolerance_m must be -1 (automatic), 0 (off) or positive")
        if self.edge_buffer_m < 0:
            raise ParameterError("edge_buffer_m must be 0 or positive")
        if self.resolution_m < 0:
            raise ParameterError("resolution_m must be 0 (automatic) or positive")

    @property
    def k(self) -> float:
        return CONFIDENCE_K[self.confidence]

    def raw(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "unknown"}


def _one_of(name, value, allowed):
    if value not in allowed:
        raise ParameterError(f"parameter '{name}' must be one of: {', '.join(allowed)}")


def _as_bool(name, value) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _BOOL_TRUE:
        return True
    if text in _BOOL_FALSE:
        return False
    raise ParameterError(f"parameter '{name}' must be true or false")
