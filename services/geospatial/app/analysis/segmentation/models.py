"""Segmentation model management and session inspection.

Models and labels live in the same managed directory as object detection
(``OBJECT_DETECTION_MODELS_DIR``); configuration only ever names an asset inside
it, and the shared resolver rejects absolute paths, parent traversal, nested
paths, and symlink escapes. This module adds the segmentation contract: one
image input and a single per-class mask output.
"""

from dataclasses import dataclass

from app.analysis.detection.models import (  # shared managed dir + safe resolution
    MODELS_DIR_ENV,
    ModelError,
    _image_input,
    _static_size,
    load_session,
    models_dir,
    read_labels,
    resolve_asset,
)

__all__ = [
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
