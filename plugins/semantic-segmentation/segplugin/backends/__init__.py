"""Model backends. ``build_backend`` maps a model card's ``backend`` to an
implementation; adding a backend means adding a module here and a name below."""

from ..errors import ModelError


def build_backend(card, params, progress):
    if card.backend == "onnx":
        from .onnx_backend import OnnxBackend
        return OnnxBackend(card, params, progress)
    if card.backend == "height":
        from .height_classes import HeightClassesBackend
        return HeightClassesBackend(card, params, progress)
    if card.backend == "geomorphon":
        from .geomorphon import GeomorphonBackend
        return GeomorphonBackend(card, params, progress)
    raise ModelError(f"unsupported backend '{card.backend}' in model '{card.id}'")
