"""Managed model directory and safe asset resolution.

Object-detection models and label files live in a single managed directory
(``OBJECT_DETECTION_MODELS_DIR``, default ``/opt/webodm/models``). Configuration
only ever names an asset *within* that directory: absolute paths, parent
traversal, nested paths, and symlink escapes are rejected so an organization
admin cannot point the operation at arbitrary files on shared storage.
"""

import os
from dataclasses import dataclass

MODELS_DIR_ENV = "OBJECT_DETECTION_MODELS_DIR"
DEFAULT_MODELS_DIR = "/opt/webodm/models"


class ModelError(ValueError):
    """A model or label asset is missing, unreadable, or outside the models dir."""


def models_dir() -> str:
    """Return the configured managed models directory (not necessarily existing)."""
    return os.environ.get(MODELS_DIR_ENV) or DEFAULT_MODELS_DIR


def resolve_asset(name: str) -> str:
    """Resolve ``name`` to an absolute path inside the managed models directory.

    ``name`` must be a bare filename. Raises ``ModelError`` for absolute paths,
    traversal, nested paths, symlink escapes, or a missing file.
    """
    if not name or name != os.path.basename(name) or name in (".", ".."):
        raise ModelError(f"invalid asset name: {name!r}")

    base = os.path.realpath(models_dir())
    candidate = os.path.realpath(os.path.join(base, name))

    # realpath resolves symlinks, so a link escaping the directory fails here.
    if os.path.commonpath([base, candidate]) != base:
        raise ModelError(f"asset escapes the models directory: {name!r}")
    if not os.path.isfile(candidate):
        raise ModelError(f"asset not found: {name!r}")
    return candidate


def read_labels(name: str) -> list[str]:
    """Load a one-class-per-line label file from the models directory."""
    path = resolve_asset(name)
    try:
        with open(path, encoding="utf-8") as f:
            labels = [line.strip() for line in f if line.strip()]
    except OSError as e:
        raise ModelError(f"cannot read labels {name!r}: {e}") from e
    if not labels:
        raise ModelError(f"labels file {name!r} is empty")
    return labels


@dataclass(frozen=True)
class ModelInfo:
    input_name: str
    output_name: str
    num_classes: int
    input_size: tuple[int, int] | None  # (height, width) when static, else None


@dataclass(frozen=True)
class ModelSpec:
    """How to feed and decode a detection model.

    ``family`` is ``"yolo"`` (single ``(N, 4+nc, anchors)`` tensor, 0-based
    classes) or ``"torchvision"`` (``boxes``/``scores``/``labels`` outputs,
    1-based classes, ImageNet-normalised input).
    """

    family: str
    input_name: str
    output_names: list[str]
    input_size: tuple[int, int]
    has_batch_dim: bool
    num_classes: int | None  # known for YOLO, else None


def load_session(name: str):
    """Load an ONNX model from the models directory as a CPU InferenceSession."""
    import onnxruntime as ort

    path = resolve_asset(name)
    try:
        return ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    except Exception as e:
        raise ModelError(f"cannot load model {name!r}: {e}") from e


def _image_input(shape):
    """Return ``(is_image_input, has_batch_dim)`` for an input shape."""
    # [N, C, H, W] with C in (1, 3)
    if len(shape) == 4 and isinstance(shape[1], int) and shape[1] in (1, 3):
        return True, True
    # [C, H, W] (torchvision exports drop the batch dimension)
    if len(shape) == 3 and isinstance(shape[0], int) and shape[0] in (1, 3):
        return True, False
    return False, False


def _static_size(shape, default):
    height, width = shape[-2], shape[-1]
    if isinstance(height, int) and isinstance(width, int):
        return (height, width)
    return default


def _yolo_num_classes(shape):
    # YOLO output: (N, 4 + num_classes, anchors) with num_classes >= 1.
    if len(shape) == 3 and isinstance(shape[1], int) and shape[1] > 4:
        return shape[1] - 4
    return None


def _torchvision_outputs(session):
    """Return ``(boxes, scores, labels)`` output names for a torchvision detector."""
    by_name = {o.name.lower(): o for o in session.get_outputs()}
    boxes = next((o for k, o in by_name.items() if "box" in k), None)
    scores = next((o for k, o in by_name.items() if "score" in k), None)
    labels = next((o for k, o in by_name.items() if "label" in k or "class" in k), None)
    if boxes is None or scores is None or labels is None:
        return None
    if len(boxes.shape) != 2 or boxes.shape[-1] != 4:
        return None
    if len(scores.shape) != 1 or len(labels.shape) != 1:
        return None
    return (boxes.name, scores.name, labels.name)


def inspect_session(session, labels: list[str], family: str = "auto") -> ModelSpec:
    """Identify a model's family, input shape, and outputs; validate its labels."""
    image_inputs = []
    for inp in session.get_inputs():
        is_image, has_batch = _image_input(inp.shape)
        if is_image:
            image_inputs.append((inp, has_batch))
    if len(image_inputs) != 1:
        raise ModelError(f"expected exactly one image input, found {len(image_inputs)}")
    image_input, has_batch = image_inputs[0]

    yolo_output = None
    for out in session.get_outputs():
        if _yolo_num_classes(out.shape):
            yolo_output = out
            break
    yolo_classes = _yolo_num_classes(yolo_output.shape) if yolo_output else None
    torchvision = _torchvision_outputs(session)

    chosen = family
    if family == "auto":
        if yolo_output is not None and torchvision is None:
            chosen = "yolo"
        elif torchvision is not None and yolo_output is None:
            chosen = "torchvision"
        elif yolo_output is not None and torchvision is not None:
            raise ModelError(
                "ambiguous detection model: exports both YOLO and boxes/scores/labels"
            )
        else:
            raise ModelError(
                "unrecognized detection model: no YOLO or boxes/scores/labels output"
            )

    if chosen == "yolo":
        if yolo_output is None:
            raise ModelError("model does not expose a YOLO detection output")
        if len(labels) != yolo_classes:
            raise ModelError(
                f"labels ({len(labels)}) do not match model classes ({yolo_classes})"
            )
        return ModelSpec(
            family="yolo",
            input_name=image_input.name,
            output_names=[yolo_output.name],
            input_size=_static_size(image_input.shape, (640, 640)),
            has_batch_dim=has_batch,
            num_classes=yolo_classes,
        )

    if torchvision is None:
        raise ModelError("model does not expose boxes/scores/labels outputs")
    return ModelSpec(
        family="torchvision",
        input_name=image_input.name,
        output_names=list(torchvision),
        input_size=_static_size(image_input.shape, (256, 256)),
        has_batch_dim=has_batch,
        num_classes=None,
    )


def validate_session(session, labels: list[str]) -> ModelInfo:
    """Backwards-compatible summary used by the pre-run validator."""
    spec = inspect_session(session, labels)
    return ModelInfo(
        input_name=spec.input_name,
        output_name=spec.output_names[0],
        num_classes=spec.num_classes if spec.num_classes is not None else len(labels),
        input_size=spec.input_size,
    )
