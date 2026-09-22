"""Model cards: what models exist, what inputs they need and how they run.

A model is described entirely by a JSON *model card* in ``models/``; the code
never hard-codes a model. Adding a model means adding a card (and, for ONNX
models, the ``.onnx`` file it names) — no plugin code changes. A card has:

- ``id``/``label``/``description``  shown to users
- ``backend``        which implementation runs it (``onnx``, ``height``, ``geomorphon``)
- ``inputs``         ``{"required": [...], "optional": [...]}`` or
                      ``{"one_of": [...], "optional": [...]}`` over the task
                      datasets ``orthophoto``/``dsm``/``dtm``
- ``classes``        ``[{"id", "name", "color", "background"?}]``, ids 0-254
- ``recommended``    defaults used when the run leaves a parameter at 0/auto
- ``auto_priority``  higher wins when ``model=auto`` and several cards fit
- backend-specific sections (``onnx``, ``height_fusion``)
"""

import json
import os
import re

from .errors import ModelError, InputError

DATASETS = ("orthophoto", "dsm", "dtm")
BACKENDS = ("onnx", "height", "geomorphon")
NODATA = 255

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class ModelCard:
    def __init__(self, data: dict, path: str):
        self.path = path
        self.raw = data
        self.id = data["id"]
        self.label = data.get("label") or self.id
        self.description = data.get("description") or ""
        self.backend = data["backend"]
        self.inputs = data["inputs"]
        self.classes = data["classes"]
        self.recommended = data.get("recommended") or {}
        self.auto_priority = int(data.get("auto_priority", 0))
        self.onnx = data.get("onnx")
        self.height_fusion = data.get("height_fusion")
        self.source = data.get("source") or {}

    @property
    def model_dir(self) -> str:
        return os.path.dirname(self.path)

    @property
    def class_ids(self) -> list[int]:
        return [c["id"] for c in self.classes]

    @property
    def class_names(self) -> list[str]:
        return [c["name"] for c in self.classes]

    @property
    def background_ids(self) -> set[int]:
        return {c["id"] for c in self.classes if c.get("background")}

    def class_by_name(self, name: str) -> dict | None:
        return next((c for c in self.classes if c["name"] == name), None)

    def required_inputs(self) -> list[str]:
        return list(self.inputs.get("required") or [])

    def one_of_inputs(self) -> list[str]:
        return list(self.inputs.get("one_of") or [])

    def optional_inputs(self) -> list[str]:
        return list(self.inputs.get("optional") or [])

    def compatible(self, selected: set[str]) -> bool:
        if not set(self.required_inputs()) <= selected:
            return False
        one_of = self.one_of_inputs()
        if one_of and not (set(one_of) & selected):
            return False
        return True

    def needs_text(self) -> str:
        parts = []
        if self.required_inputs():
            parts.append(" + ".join(self.required_inputs()))
        if self.one_of_inputs():
            parts.append(" or ".join(self.one_of_inputs()))
        if self.optional_inputs():
            parts.append(f"(optional: {', '.join(self.optional_inputs())})")
        return " ".join(parts) or "no inputs"

    def used_inputs(self, selected: set[str]) -> tuple[list[str], list[str]]:
        """Return (inputs this model will read, selected inputs it ignores)."""
        used = [d for d in self.required_inputs() if d in selected]
        one_of = [d for d in self.one_of_inputs() if d in selected]
        if one_of:
            used.append(one_of[0])  # card order expresses preference
        used += [d for d in self.optional_inputs() if d in selected and d not in used]
        ignored = [d for d in DATASETS if d in selected and d not in used]
        return used, ignored

    def summary(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "backend": self.backend,
            "inputs": self.inputs,
            "classes": [{"id": c["id"], "name": c["name"], "color": c["color"]} for c in self.classes],
            "source": self.source,
        }


def _require(cond, message):
    if not cond:
        raise ModelError(message)


def validate_card(data: dict, path: str) -> ModelCard:
    where = os.path.basename(path)
    _require(isinstance(data, dict), f"{where}: model card must be a JSON object")
    _require(isinstance(data.get("id"), str) and _ID_RE.match(data["id"]),
             f"{where}: 'id' must be a lowercase slug")
    _require(data.get("backend") in BACKENDS,
             f"{where}: 'backend' must be one of {', '.join(BACKENDS)}")

    inputs = data.get("inputs")
    _require(isinstance(inputs, dict), f"{where}: 'inputs' must be an object")
    for key in ("required", "one_of", "optional"):
        value = inputs.get(key, [])
        _require(isinstance(value, list) and all(v in DATASETS for v in value),
                 f"{where}: inputs.{key} must list task datasets ({', '.join(DATASETS)})")
    _require(inputs.get("required") or inputs.get("one_of"),
             f"{where}: a model needs at least one required or one_of input")

    classes = data.get("classes")
    _require(isinstance(classes, list) and classes, f"{where}: 'classes' must be a non-empty list")
    seen_ids, seen_names = set(), set()
    for c in classes:
        _require(isinstance(c, dict), f"{where}: each class must be an object")
        cid = c.get("id")
        _require(isinstance(cid, int) and 0 <= cid < NODATA, f"{where}: class ids must be 0-254")
        _require(cid not in seen_ids, f"{where}: duplicate class id {cid}")
        seen_ids.add(cid)
        name = c.get("name")
        _require(isinstance(name, str) and name and name not in seen_names,
                 f"{where}: class names must be unique non-empty strings")
        seen_names.add(name)
        _require(isinstance(c.get("color"), str) and _COLOR_RE.match(c["color"]),
                 f"{where}: class '{name}' needs a '#rrggbb' color")

    if data["backend"] == "onnx":
        onnx = data.get("onnx")
        _require(isinstance(onnx, dict) and isinstance(onnx.get("file"), str),
                 f"{where}: onnx models need an 'onnx' section with 'file'")
        _require(onnx.get("output_type", "probabilities") in ("probabilities", "logits", "labels"),
                 f"{where}: onnx.output_type must be probabilities, logits or labels")
        channels = onnx.get("channels") or ["red", "green", "blue"]
        _require(isinstance(channels, list) and channels and all(
            ch in ("red", "green", "blue", "alpha", "ndsm", "elevation") for ch in channels),
            f"{where}: onnx.channels may contain red, green, blue, alpha, ndsm, elevation")
        _require("/" not in onnx["file"] and ".." not in onnx["file"],
                 f"{where}: onnx.file must be a file name inside models/")

    fusion = data.get("height_fusion")
    if fusion is not None:
        _require(isinstance(fusion, dict) and isinstance(fusion.get("classes"), dict),
                 f"{where}: height_fusion needs a 'classes' map")
        for name, kind in fusion["classes"].items():
            _require(name in seen_names, f"{where}: height_fusion refers to unknown class '{name}'")
            _require(kind in ("elevated", "ground", "any"),
                     f"{where}: height_fusion class kinds are elevated, ground or any")
    return ModelCard(data, path)


class Catalog(dict):
    """Available model cards by id, plus the ids skipped and why.

    An ONNX card whose weight file is not in the package (e.g. a large model
    that was not fetched before packaging) is *unavailable* rather than an
    error: ``auto`` falls back to the next model and an explicit request
    explains what is missing.
    """

    def __init__(self):
        super().__init__()
        self.unavailable: dict[str, str] = {}


def load_cards(models_dir: str) -> Catalog:
    if not os.path.isdir(models_dir):
        raise ModelError(f"models directory not found: {models_dir}")
    cards = Catalog()
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
        if card.backend == "onnx" and not os.path.isfile(os.path.join(models_dir, card.onnx["file"])):
            cards.unavailable[card.id] = (
                f"model file models/{card.onnx['file']} is not in the package "
                f"(see tools/fetch_models.py)"
            )
            continue
        cards[card.id] = card
    if not cards:
        raise ModelError(f"no usable model cards found in {models_dir}")
    return cards


def select_model(cards: dict[str, ModelCard], requested: str, selected: set[str]) -> ModelCard:
    """Pick the card for ``requested`` (or the best fit for ``auto``).

    Errors say exactly which inputs each model needs, so a user who selected
    only a DTM learns why the RGB model cannot run instead of getting a crash.
    """
    selected = set(selected)
    if requested in (None, "", "auto"):
        fits = sorted((c for c in cards.values() if c.compatible(selected)),
                      key=lambda c: (-c.auto_priority, c.id))
        if not fits:
            options = "; ".join(f"{c.id} needs {c.needs_text()}" for c in cards.values())
            raise InputError(
                f"No model can run on the selected inputs ({', '.join(sorted(selected)) or 'none'}). "
                f"Available models: {options}"
            )
        return fits[0]

    card = cards.get(requested)
    if card is None:
        unavailable = getattr(cards, "unavailable", {})
        if requested in unavailable:
            raise ModelError(f"Model '{requested}' is unavailable: {unavailable[requested]}")
        raise ModelError(f"Unknown model '{requested}'. Available: {', '.join(sorted(cards))}")
    if not card.compatible(selected):
        raise InputError(
            f"Model '{card.id}' needs {card.needs_text()}; selected inputs: "
            f"{', '.join(sorted(selected)) or 'none'}"
        )
    return card
