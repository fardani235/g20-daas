"""Loading an ONNX detector and working out how to feed and read it.

There is deliberately no pre-run validation step in the platform for user
plugins: a bad or mismatched model fails *the run*, here, with a message the
customer can act on (which output the model exposes, how many classes it has
versus the card, which file's checksum differs).
"""

import hashlib
from dataclasses import dataclass

from .errors import ModelError
from .registry import ModelCard

# Model input size assumed when the graph leaves height/width dynamic.
DYNAMIC_INPUT_DEFAULT = {"yolo": (640, 640), "torchvision": (256, 256)}


@dataclass(frozen=True)
class ModelSpec:
    """How to feed and decode a detection model.

    ``family`` is ``"yolo"`` (single ``(N, 4+nc, anchors)`` tensor) or
    ``"torchvision"`` (``boxes``/``scores``/``labels`` outputs).
    """

    family: str
    input_name: str
    output_names: list[str]
    input_size: tuple[int, int]  # (height, width)
    has_batch_dim: bool
    num_classes: int | None  # known for YOLO, else None
    normalize: str

    def describe(self) -> dict:
        return {
            "family": self.family,
            "input": self.input_name,
            "outputs": list(self.output_names),
            "input_size": list(self.input_size),
            "batch_dim": self.has_batch_dim,
            "normalize": self.normalize,
        }


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_checksum(card: ModelCard) -> None:
    """Fail clearly when the packaged weights are not the file the card describes."""
    if not card.sha256:
        return
    actual = sha256_of(card.model_path)
    if actual != card.sha256:
        raise ModelError(
            f"model file models/{card.file} does not match the checksum in its card "
            f"({card.id}): expected {card.sha256}, got {actual}. Update the card's sha256 "
            f"if you replaced the weights on purpose."
        )


def load_session(card: ModelCard):
    """Load the card's ONNX file as a CPU inference session."""
    try:
        import onnxruntime as ort
    except ImportError as e:  # pragma: no cover - the sandbox ships onnxruntime
        raise ModelError("onnxruntime is not installed in this environment") from e
    try:
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        return ort.InferenceSession(card.model_path, sess_options=opts, providers=["CPUExecutionProvider"])
    except Exception as e:
        raise ModelError(f"cannot load model models/{card.file} ({card.id}): {e}") from e


def _image_input(shape):
    """Return ``(is_image_input, has_batch_dim)`` for an input shape."""
    if len(shape) == 4 and isinstance(shape[1], int) and shape[1] in (1, 3):
        return True, True  # [N, C, H, W]
    if len(shape) == 3 and isinstance(shape[0], int) and shape[0] in (1, 3):
        return True, False  # [C, H, W] (torchvision exports drop the batch dimension)
    return False, False


def _static_size(shape, default):
    height, width = shape[-2], shape[-1]
    if isinstance(height, int) and isinstance(width, int) and height > 0 and width > 0:
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


def _shapes(session) -> str:
    outs = ", ".join(f"{o.name}{list(o.shape)}" for o in session.get_outputs())
    return f"outputs: {outs or 'none'}"


def inspect_session(session, card: ModelCard) -> ModelSpec:
    """Identify the model's family, input and outputs, and check them against the card."""
    image_inputs = []
    for inp in session.get_inputs():
        is_image, has_batch = _image_input(inp.shape)
        if is_image:
            image_inputs.append((inp, has_batch))
    if len(image_inputs) != 1:
        ins = ", ".join(f"{i.name}{list(i.shape)}" for i in session.get_inputs())
        raise ModelError(
            f"model '{card.id}' must have exactly one image input (N,3,H,W) or (3,H,W); "
            f"found {len(image_inputs)} (inputs: {ins or 'none'})"
        )
    image_input, has_batch = image_inputs[0]

    yolo_output = next((o for o in session.get_outputs() if _yolo_num_classes(o.shape)), None)
    yolo_classes = _yolo_num_classes(yolo_output.shape) if yolo_output else None
    torchvision = _torchvision_outputs(session)

    chosen = card.family
    if chosen == "auto":
        if yolo_output is not None and torchvision is None:
            chosen = "yolo"
        elif torchvision is not None and yolo_output is None:
            chosen = "torchvision"
        elif yolo_output is not None and torchvision is not None:
            raise ModelError(
                f"model '{card.id}' exposes both a YOLO tensor and boxes/scores/labels; "
                f"set 'family' in its card ({_shapes(session)})"
            )
        else:
            raise ModelError(
                f"model '{card.id}' is not a supported detector: no (N, 4+classes, anchors) "
                f"YOLO output and no boxes/scores/labels outputs ({_shapes(session)})"
            )

    if chosen == "yolo":
        if yolo_output is None:
            raise ModelError(
                f"model '{card.id}' is declared family 'yolo' but has no (N, 4+classes, anchors) "
                f"output ({_shapes(session)})"
            )
        if len(card.classes) != yolo_classes:
            raise ModelError(
                f"model '{card.id}' predicts {yolo_classes} classes but its card lists "
                f"{len(card.classes)}; fix the card's 'classes'"
            )
        return ModelSpec(
            family="yolo",
            input_name=image_input.name,
            output_names=[yolo_output.name],
            input_size=_static_size(image_input.shape, DYNAMIC_INPUT_DEFAULT["yolo"]),
            has_batch_dim=has_batch,
            num_classes=yolo_classes,
            normalize=card.normalization_for("yolo"),
        )

    if torchvision is None:
        raise ModelError(
            f"model '{card.id}' is declared family 'torchvision' but has no boxes/scores/labels "
            f"outputs ({_shapes(session)})"
        )
    return ModelSpec(
        family="torchvision",
        input_name=image_input.name,
        output_names=list(torchvision),
        input_size=_static_size(image_input.shape, DYNAMIC_INPUT_DEFAULT["torchvision"]),
        has_batch_dim=has_batch,
        num_classes=None,
        normalize=card.normalization_for("torchvision"),
    )
