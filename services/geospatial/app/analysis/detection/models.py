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


def load_session(name: str):
    """Load an ONNX model from the models directory as a CPU InferenceSession."""
    import onnxruntime as ort

    path = resolve_asset(name)
    try:
        return ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    except Exception as e:
        raise ModelError(f"cannot load model {name!r}: {e}") from e


def _image_input(shape) -> bool:
    # (N, C, H, W) with C in (1, 3).
    return len(shape) == 4 and isinstance(shape[1], int) and shape[1] in (1, 3)


def _detection_classes(shape):
    # YOLO output: (N, 4 + num_classes, anchors) with num_classes >= 1.
    if len(shape) != 3 or not isinstance(shape[1], int) or shape[1] <= 4:
        return None
    return shape[1] - 4


def validate_session(session, labels: list[str]) -> ModelInfo:
    """Check the model has one image input, one detection output, and matching labels."""
    image_inputs = [i for i in session.get_inputs() if _image_input(i.shape)]
    if len(image_inputs) != 1:
        raise ModelError(
            f"expected exactly one image input, found {len(image_inputs)}"
        )

    detections = []
    for out in session.get_outputs():
        num_classes = _detection_classes(out.shape)
        if num_classes is not None:
            detections.append((out, num_classes))
    if len(detections) != 1:
        raise ModelError(
            f"expected exactly one detection output, found {len(detections)}"
        )

    output, num_classes = detections[0]
    if len(labels) != num_classes:
        raise ModelError(
            f"labels ({len(labels)}) do not match model classes ({num_classes})"
        )

    shape = image_inputs[0].shape
    height, width = shape[2], shape[3]
    size = (height, width) if isinstance(height, int) and isinstance(width, int) else None
    return ModelInfo(
        input_name=image_inputs[0].name,
        output_name=output.name,
        num_classes=num_classes,
        input_size=size,
    )
