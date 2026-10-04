"""Model cards: which footprint models exist and how to run each one.

A model is described entirely by a JSON *model card* in ``models/``; the code
never hard-codes a model. Swapping the model means editing a card (and
fetching the weights it names), not changing code. A card has:

- ``id``/``label``/``description``  shown to users
- ``file``          the ONNX file in ``models/`` (bare file name)
- ``sha256``        hex digest the weights are verified against before use
                    (required for anything ``tools/fetch_models.py`` downloads)
- ``default``       ``true`` on the card used when the run names no model
- ``class``         ``{"name", "color"}`` of the single output class
- ``onnx``          the graph contract: ``input``/``output`` tensor names,
                    static ``input_size`` (square), input ``channels``
                    (``red``/``green``/``blue`` order), ``scale`` (``unit`` for
                    [0, 1] or ``255``), per-channel ``mean``/``std`` applied
                    after scaling (``null`` when the graph normalises itself),
                    and ``outputs``: which output channel is the building
                    ``mask``, the optional ``boundary`` and the signed
                    ``distance``, each with the ``activation`` the plugin must
                    apply (``sigmoid``, ``tanh`` or ``none``)
- ``recommended``   defaults used when the run leaves a parameter at 0/-1/auto
- ``source``        provenance: ``name``, ``url``, ``license``, ``paper``, ``export``
"""

import json
import os
import re

from .errors import ModelError

RECOMMENDED_KEYS = (
    "resolution_m", "tile_size", "overlap", "confidence", "seed_min_distance", "large_blob_area_px",
    "h_maxima_depth", "min_area_m2", "simplify_m", "regularize_area_threshold", "regularize_overlap_tol_m2",
)
OUTPUT_ROLES = ("mask", "boundary", "distance")
ACTIVATIONS = ("sigmoid", "tanh", "none")
CHANNELS = ("red", "green", "blue")

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class ModelCard:
    def __init__(self, data: dict, path: str):
        self.path = path
        self.raw = data
        self.id = data["id"]
        self.label = data.get("label") or self.id
        self.description = data.get("description") or ""
        self.file = data["file"]
        self.sha256 = (data.get("sha256") or "").lower() or None
        self.default = bool(data.get("default", False))
        self.class_name = data["class"]["name"]
        self.class_color = data["class"]["color"]
        self.onnx = data["onnx"]
        self.recommended = dict(data.get("recommended") or {})
        self.source = data.get("source") or {}

    @property
    def model_dir(self) -> str:
        return os.path.dirname(self.path)

    @property
    def model_path(self) -> str:
        return os.path.join(self.model_dir, self.file)

    @property
    def license(self) -> str:
        return str(self.source.get("license") or "unknown")

    @property
    def input_size(self) -> int:
        return int(self.onnx["input_size"])

    @property
    def channels(self) -> list[str]:
        return list(self.onnx.get("channels") or CHANNELS)

    def output(self, role: str) -> dict | None:
        return (self.onnx.get("outputs") or {}).get(role)

    def summary(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "file": self.file,
            "class": {"name": self.class_name, "color": self.class_color},
            "onnx": {k: v for k, v in self.onnx.items() if k != "mean" and k != "std"} | {
                "mean": self.onnx.get("mean"), "std": self.onnx.get("std")},
            "recommended": dict(self.recommended),
            "source": dict(self.source),
        }


def _require(cond, message):
    if not cond:
        raise ModelError(message)


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_card(data: dict, path: str) -> ModelCard:
    where = os.path.basename(path)
    _require(isinstance(data, dict), f"{where}: model card must be a JSON object")
    _require(isinstance(data.get("id"), str) and _ID_RE.match(data["id"]),
             f"{where}: 'id' must be a lowercase slug (letters, digits, hyphens)")
    file = data.get("file")
    _require(isinstance(file, str) and file and file == os.path.basename(file) and file not in (".", ".."),
             f"{where}: 'file' must be the name of an .onnx file inside models/")
    sha = data.get("sha256")
    _require(sha is None or (isinstance(sha, str) and _SHA_RE.match(sha)),
             f"{where}: 'sha256' must be a 64-character hex digest")
    _require(isinstance(data.get("default", False), bool), f"{where}: 'default' must be true or false")

    cls = data.get("class")
    _require(isinstance(cls, dict) and isinstance(cls.get("name"), str) and cls["name"].strip(),
             f"{where}: 'class' needs a non-empty 'name'")
    _require(isinstance(cls.get("color"), str) and _COLOR_RE.match(cls["color"]),
             f"{where}: class '{cls.get('name')}' needs a '#rrggbb' color")

    onnx = data.get("onnx")
    _require(isinstance(onnx, dict), f"{where}: 'onnx' section is required")
    for key in ("input", "output"):
        _require(onnx.get(key) is None or (isinstance(onnx[key], str) and onnx[key]),
                 f"{where}: onnx.{key} must be a tensor name")
    size = onnx.get("input_size")
    _require(isinstance(size, int) and not isinstance(size, bool) and 32 <= size <= 4096,
             f"{where}: onnx.input_size must be an integer between 32 and 4096")
    channels = onnx.get("channels", list(CHANNELS))
    _require(isinstance(channels, list) and sorted(channels) == sorted(CHANNELS),
             f"{where}: onnx.channels must be a permutation of red, green, blue")
    _require(onnx.get("scale", "unit") in ("unit", "255"), f"{where}: onnx.scale must be 'unit' or '255'")
    for key in ("mean", "std"):
        v = onnx.get(key)
        _require(v is None or (isinstance(v, list) and len(v) == 3 and all(_number(x) for x in v)),
                 f"{where}: onnx.{key} must be null or a list of 3 numbers")
    _require((onnx.get("mean") is None) == (onnx.get("std") is None),
             f"{where}: onnx.mean and onnx.std must be given together")
    if onnx.get("std") is not None:
        _require(all(x > 0 for x in onnx["std"]), f"{where}: onnx.std values must be positive")

    outputs = onnx.get("outputs")
    _require(isinstance(outputs, dict), f"{where}: onnx.outputs must map mask/boundary/distance to channels")
    for role in ("mask", "distance"):
        _require(role in outputs, f"{where}: onnx.outputs needs a '{role}' entry")
    seen = set()
    for role, spec in outputs.items():
        _require(role in OUTPUT_ROLES, f"{where}: onnx.outputs.{role} is not one of {', '.join(OUTPUT_ROLES)}")
        _require(isinstance(spec, dict), f"{where}: onnx.outputs.{role} must be an object")
        ch = spec.get("channel")
        _require(isinstance(ch, int) and not isinstance(ch, bool) and 0 <= ch < 64,
                 f"{where}: onnx.outputs.{role}.channel must be a small non-negative integer")
        _require(ch not in seen, f"{where}: onnx.outputs channel {ch} is used twice")
        seen.add(ch)
        _require(spec.get("activation", "none") in ACTIVATIONS,
                 f"{where}: onnx.outputs.{role}.activation must be one of {', '.join(ACTIVATIONS)}")

    rec = data.get("recommended", {})
    _require(isinstance(rec, dict), f"{where}: 'recommended' must be an object")
    for key, value in rec.items():
        _require(key in RECOMMENDED_KEYS,
                 f"{where}: recommended.{key} is not a parameter ({', '.join(RECOMMENDED_KEYS)})")
        _require(_number(value), f"{where}: recommended.{key} must be a number")
    _require(_number(rec.get("resolution_m")) and rec["resolution_m"] > 0,
             f"{where}: recommended.resolution_m (metres) is required and must be positive")
    if "confidence" in rec:
        _require(0 < rec["confidence"] < 1, f"{where}: recommended.confidence must be between 0 and 1")
    if "regularize_area_threshold" in rec:
        _require(0 < rec["regularize_area_threshold"] <= 1,
                 f"{where}: recommended.regularize_area_threshold must be in (0, 1]")
    _require(isinstance(data.get("source", {}), dict), f"{where}: 'source' must be an object")
    return ModelCard(data, path)


class Catalog(dict):
    """Available model cards by id, plus the ids skipped and why.

    A card whose weight file is not in the package (weights are fetched at
    build time, never committed) is *unavailable* rather than an error, so an
    explicit request for it explains what is missing instead of crashing.
    """

    def __init__(self):
        super().__init__()
        self.unavailable: dict[str, str] = {}

    @property
    def all_ids(self) -> list[str]:
        return sorted(set(self) | set(self.unavailable))


def load_cards(models_dir: str, require_files: bool = True) -> Catalog:
    """Load every ``*.json`` card in ``models_dir``.

    With ``require_files`` cards whose weights are absent go to
    ``catalog.unavailable``; without it (manifest tooling) every valid card is
    returned.
    """
    if not os.path.isdir(models_dir):
        raise ModelError(f"models directory not found: {models_dir}")
    cards = Catalog()
    defaults = []
    for name in sorted(os.listdir(models_dir)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(models_dir, name)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            raise ModelError(f"model card {name} is not valid JSON: {e}") from e
        card = validate_card(data, path)
        if card.id in cards or card.id in cards.unavailable:
            raise ModelError(f"duplicate model id '{card.id}' in {name}")
        if card.default:
            defaults.append(card.id)
        if require_files and not os.path.isfile(card.model_path):
            cards.unavailable[card.id] = (
                f"model file models/{card.file} is not in the package "
                f"(run tools/fetch_models.py before tools/build.sh, or add your own weights)"
            )
            continue
        cards[card.id] = card
    if len(defaults) > 1:
        raise ModelError(f"more than one model card is marked default: {', '.join(defaults)}")
    if not cards and not cards.unavailable:
        raise ModelError(f"no model cards found in {models_dir}")
    return cards


def default_model_id(cards: Catalog) -> str:
    """The card marked ``default``, else the first id alphabetically."""
    for cid in cards.all_ids:
        card = cards.get(cid)
        if card is not None and card.default:
            return cid
    return cards.all_ids[0]


def select_model(cards: Catalog, requested: str | None) -> ModelCard:
    """Pick the card for ``requested`` (``auto``/empty = the default card).

    Errors name the available models and, for weights that were not packaged,
    say how to add them.
    """
    if requested in (None, "", "auto"):
        requested = default_model_id(cards)
    card = cards.get(requested)
    if card is None:
        if requested in cards.unavailable:
            raise ModelError(f"Model '{requested}' is unavailable: {cards.unavailable[requested]}")
        available = ", ".join(sorted(cards)) or "none"
        raise ModelError(f"Unknown model '{requested}'. Models in this package: {available}")
    return card
