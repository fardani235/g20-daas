"""Segmentation model management and session inspection.

Models and labels live in one managed directory (``OBJECT_DETECTION_MODELS_DIR``
— the name dates from when object detection was a system operation; it is now
a user plugin, see ``plugins/object-detection``). Configuration only ever
names an asset inside that directory: absolute paths, parent traversal, nested
paths and symlink escapes are rejected so an organization admin cannot point
the operation at arbitrary files on shared storage. On top of that this module
adds the segmentation contract: one image input and a single per-class mask
output.
"""

import os
from dataclasses import dataclass

MODELS_DIR_ENV = "OBJECT_DETECTION_MODELS_DIR"
DEFAULT_MODELS_DIR = "/opt/webodm/models"

__all__ = [
    "DEFAULT_MODELS_DIR",
    "MODELS_DIR_ENV",
    "ModelError",
    "SegmentationSpec",
    "inspect_session",
    "load_session",
    "models_dir",
    "read_labels",
    "resolve_asset",
    "validate_session",
]


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
    # [C, H, W] (exports that drop the batch dimension)
    if len(shape) == 3 and isinstance(shape[0], int) and shape[0] in (1, 3):
        return True, False
    return False, False


def _static_size(shape, default):
    height, width = shape[-2], shape[-1]
    if isinstance(height, int) and isinstance(width, int):
        return (height, width)
    return default


@dataclass(frozen=True)
class SegmentationSpec:
    """How to feed and decode a segmentation model.

    ``mode`` is ``"multiclass"`` when the mask output has one channel per label
    (``argmax`` over channels) or ``"binary"`` when it has a single foreground
    channel and the label file has one or two entries. ``foreground_index`` is
    the label the single channel maps to in binary mode.
    """

    input_name: str
    output_name: str
    input_size: tuple[int, int]  # (height, width) when static, else a default
    has_batch_dim: bool
    mode: str
    num_classes: int  # channels of the mask output
    foreground_index: int  # label index for the single channel in binary mode


def _mask_output(session):
    """Return the single rank-4 per-class mask output, or raise."""
    masks = [out for out in session.get_outputs() if len(out.shape) == 4]
    if len(masks) != 1:
        raise ModelError(
            "expected exactly one per-class mask output (rank 4), "
            f"found {len(masks)}"
        )
    return masks[0]


def inspect_session(session, labels: list[str]) -> SegmentationSpec:
    """Identify a segmentation model's input/output and validate its labels.

    Raises ``ModelError`` when the model does not expose exactly one image input
    and one rank-4 per-class mask output, or when its class count and the label
    file disagree.
    """
    if not labels:
        raise ModelError("labels file is empty")

    image_inputs = []
    for inp in session.get_inputs():
        is_image, has_batch = _image_input(inp.shape)
        if is_image:
            image_inputs.append((inp, has_batch))
    if len(image_inputs) != 1:
        raise ModelError(f"expected exactly one image input, found {len(image_inputs)}")
    image_input, has_batch = image_inputs[0]

    output = _mask_output(session)
    channels = output.shape[1]
    if not isinstance(channels, int):
        raise ModelError("model mask output has an unknown class dimension")

    if channels >= 2 and channels == len(labels):
        mode = "multiclass"
        foreground_index = 0
    elif channels == 1 and len(labels) in (1, 2):
        mode = "binary"
        foreground_index = 1 if len(labels) == 2 else 0
    else:
        raise ModelError(
            f"labels ({len(labels)}) do not match model classes ({channels})"
        )

    return SegmentationSpec(
        input_name=image_input.name,
        output_name=output.name,
        input_size=_static_size(image_input.shape, (512, 512)),
        has_batch_dim=has_batch,
        mode=mode,
        num_classes=channels,
        foreground_index=foreground_index,
    )


def validate_session(session, labels: list[str]) -> SegmentationSpec:
    """Pre-run check: the model's contract matches the label file."""
    return inspect_session(session, labels)
