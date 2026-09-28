"""Model cards: which detectors exist and how to run each one.

A detector is described entirely by a JSON *model card* in ``models/``; the
code never hard-codes a model. Adding a detector means adding a card and the
``.onnx`` file it names, then rebuilding the package (``tools/build.sh`` also
refreshes the ``model`` enum in ``plugin.json`` from the cards). A card has:

- ``id``/``label``/``description``  shown to users
- ``file``          the ONNX file in ``models/`` (bare file name)
- ``sha256``        optional; when present the weights are verified before use
- ``family``        ``yolo`` (one ``(N, 4 + classes, anchors)`` tensor),
                    ``torchvision`` (``boxes``/``scores``/``labels`` outputs) or
                    ``auto`` (inferred from the model's outputs)
- ``classes``       class names in class-id order (the model's label set)
- ``label_offset``  subtracted from model labels to index ``classes``
                    (1 for standard torchvision detectors that reserve 0 for
                    background, 0 for YOLO and DeepForest exports)
- ``normalize``     ``unit`` ([0, 1] input) or ``imagenet`` (mean/std
                    normalised); defaults per family
- ``recommended``   defaults used when the run leaves a parameter at 0/-1:
                    ``confidence``, ``iou``, ``tile_size``, ``overlap``,
                    ``tile_size_m``, ``overlap_m``
- ``colors``        optional ``{class: "#rrggbb"}`` legend colours
- ``default``       ``true`` on the card selected when the run names no model
- ``source``        provenance: ``name``, ``url``, ``license``, ``export`` (free text)
"""

import json
import os
import re

from .errors import ModelError

FAMILIES = ("auto", "yolo", "torchvision")
NORMALIZATIONS = ("unit", "imagenet")
DEFAULT_NORMALIZE = {"yolo": "unit", "torchvision": "imagenet", "auto": None}
RECOMMENDED_KEYS = ("confidence", "iou", "tile_size", "overlap", "tile_size_m", "overlap_m")

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
        self.family = data.get("family") or "auto"
        self.classes = list(data["classes"])
        self.label_offset = int(data.get("label_offset", 0))
        self.normalize = data.get("normalize")
        self.recommended = data.get("recommended") or {}
        self.colors = data.get("colors") or {}
        self.default = bool(data.get("default", False))
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

    def class_name(self, class_id: int) -> str | None:
        if 0 <= class_id < len(self.classes):
            return self.classes[class_id]
        return None

    def normalization_for(self, family: str) -> str:
        return self.normalize or DEFAULT_NORMALIZE.get(family) or "unit"

    def legend(self) -> list[dict]:
        return [{"name": name, "color": self.colors.get(name)} for name in self.classes]

    def summary(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "file": self.file,
            "family": self.family,
            "classes": list(self.classes),
            "label_offset": self.label_offset,
            "recommended": dict(self.recommended),
            "source": dict(self.source),
        }


def _require(cond, message):
    if not cond:
        raise ModelError(message)


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
    _require(data.get("family", "auto") in FAMILIES,
             f"{where}: 'family' must be one of {', '.join(FAMILIES)}")
    normalize = data.get("normalize")
    _require(normalize is None or normalize in NORMALIZATIONS,
             f"{where}: 'normalize' must be one of {', '.join(NORMALIZATIONS)}")

    classes = data.get("classes")
    _require(isinstance(classes, list) and classes, f"{where}: 'classes' must be a non-empty list of names")
    seen = set()
    for name in classes:
        _require(isinstance(name, str) and name.strip() and name not in seen,
                 f"{where}: class names must be unique non-empty strings")
        seen.add(name)

    offset = data.get("label_offset", 0)
    _require(isinstance(offset, int) and not isinstance(offset, bool) and 0 <= offset <= 10,
             f"{where}: 'label_offset' must be an integer between 0 and 10")

    rec = data.get("recommended", {})
    _require(isinstance(rec, dict), f"{where}: 'recommended' must be an object")
    for key, value in rec.items():
        _require(key in RECOMMENDED_KEYS,
                 f"{where}: recommended.{key} is not a parameter ({', '.join(RECOMMENDED_KEYS)})")
        _require(value is None or (isinstance(value, (int, float)) and not isinstance(value, bool)),
                 f"{where}: recommended.{key} must be a number")

    colors = data.get("colors", {})
    _require(isinstance(colors, dict), f"{where}: 'colors' must map class names to '#rrggbb'")
    for name, color in colors.items():
        _require(name in seen, f"{where}: colors refers to unknown class '{name}'")
        _require(isinstance(color, str) and _COLOR_RE.match(color),
                 f"{where}: color for '{name}' must be '#rrggbb'")
    _require(isinstance(data.get("default", False), bool), f"{where}: 'default' must be true or false")
    _require(isinstance(data.get("source", {}), dict), f"{where}: 'source' must be an object")
    return ModelCard(data, path)


class Catalog(dict):
    """Available model cards by id, plus the ids skipped and why.

    A card whose weight file is not in the package (a large model that was not
    fetched before packaging) is *unavailable* rather than an error, so the
    other models still run and an explicit request explains what is missing.
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
    """Pick the card for ``requested`` (or the default when none is named).

    Errors name the available models and, for weights that were not packaged,
    say how to add them — so a customer who uploaded a card without its file
    reads what to do instead of a stack trace.
    """
    if requested in (None, ""):
        requested = default_model_id(cards)
    card = cards.get(requested)
    if card is None:
        if requested in cards.unavailable:
            raise ModelError(f"Model '{requested}' is unavailable: {cards.unavailable[requested]}")
        available = ", ".join(sorted(cards)) or "none"
        raise ModelError(f"Unknown model '{requested}'. Models in this package: {available}")
    return card
