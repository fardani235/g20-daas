"""ONNX segmentation models.

Any model exported to ONNX can be dropped in with a card describing its
contract: input/output names, the channel recipe (``red``/``green``/``blue``,
``ndsm`` for the normalized height, ``elevation``), the value scale and
optional per-channel mean/std, the static input size, and whether the output
is probabilities, logits or label ids. Height channels make *early fusion*
(FuseNet-style RGB+nDSM networks, Audebert et al. 2018) possible without
code changes; RGB-only models can still use height through the late fusion
in ``segplugin.fusion``.

ONNX Runtime is provided by the sandbox (see services/plugin-runner). If it is
missing the model reports that clearly instead of crashing on import.
"""

import hashlib
import os

import numpy as np

from ..errors import ModelError
from .base import Backend, TileData

# Height channel scale: 30 m maps typical building/tree heights into ~[0, 1].
NDSM_SCALE_M = 30.0


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class OnnxBackend(Backend):
    def __init__(self, card, params, progress):
        super().__init__(card, params, progress)
        spec = card.onnx
        self.channels = list(spec.get("channels") or ["red", "green", "blue"])
        self.needs = frozenset(
            {"rgb"} if any(c in ("red", "green", "blue", "alpha") for c in self.channels) else set()
        ) | frozenset({"ndsm"} if "ndsm" in self.channels else set()) \
          | frozenset({"elevation"} if "elevation" in self.channels else set())
        self.scale = spec.get("scale", "unit")
        self.mean = spec.get("mean")
        self.std = spec.get("std")
        self.output_type = spec.get("output_type", "probabilities")
        self.fixed_size = spec.get("input_size")
        self.default_tile = int(self.fixed_size or card.recommended.get("tile_size") or 512)

        path = os.path.join(card.model_dir, spec["file"])
        if not os.path.isfile(path):
            raise ModelError(f"model file missing from the package: models/{spec['file']}")
        expected = spec.get("sha256")
        if expected:
            actual = _sha256(path)
            if actual.lower() != expected.lower():
                raise ModelError(f"model file models/{spec['file']} is corrupt (sha256 mismatch)")

        try:
            import onnxruntime as ort
        except ImportError as e:
            raise ModelError(
                "ONNX Runtime is not available in the plugin sandbox, so ONNX models cannot run. "
                "Ask the platform operator to include 'onnxruntime' in the plugin-runner image, "
                "or choose a non-ONNX model (height-classes, geomorphons)."
            ) from e

        opts = ort.SessionOptions()
        threads = int(os.environ.get("SEGMENTATION_THREADS", "0") or 0)
        if threads > 0:
            opts.intra_op_num_threads = threads
            opts.inter_op_num_threads = 1
        opts.log_severity_level = 3
        try:
            self.session = ort.InferenceSession(path, sess_options=opts, providers=["CPUExecutionProvider"])
        except Exception as e:
            raise ModelError(f"model models/{spec['file']} failed to load: {e}") from e
        self._validate_io(spec)

    def _validate_io(self, spec):
        inputs = {i.name: i for i in self.session.get_inputs()}
        outputs = {o.name: o for o in self.session.get_outputs()}
        self.input_name = spec.get("input") or next(iter(inputs))
        self.output_name = spec.get("output") or next(iter(outputs))
        if self.input_name not in inputs:
            raise ModelError(f"model has no input '{self.input_name}' (has: {', '.join(inputs)})")
        if self.output_name not in outputs:
            raise ModelError(f"model has no output '{self.output_name}' (has: {', '.join(outputs)})")
        shape = list(inputs[self.input_name].shape)
        if len(shape) != 4:
            raise ModelError(f"model input must be NCHW, got shape {shape}")
        n, c, h, w = shape
        if isinstance(c, int) and c != len(self.channels):
            raise ModelError(
                f"model expects {c} input channels but the card lists {len(self.channels)} "
                f"({', '.join(self.channels)})"
            )
        static = [d for d in (h, w) if isinstance(d, int)]
        if static:
            if len(set(static)) != 1:
                raise ModelError("only square static input sizes are supported")
            if self.fixed_size and self.fixed_size != static[0]:
                raise ModelError(f"card input_size {self.fixed_size} differs from the model's {static[0]}")
            self.fixed_size = static[0]
            self.default_tile = self.fixed_size
        oshape = list(outputs[self.output_name].shape)
        if self.output_type in ("probabilities", "logits"):
            if len(oshape) != 4:
                raise ModelError(f"model output must be NCHW class scores, got shape {oshape}")
            if isinstance(oshape[1], int) and oshape[1] != len(self.class_ids):
                raise ModelError(
                    f"model emits {oshape[1]} classes but the card lists {len(self.class_ids)}"
                )
        elif len(oshape) not in (3, 4):
            raise ModelError(f"label output must be NHW or N1HW, got shape {oshape}")

    def _stack(self, tile: TileData) -> tuple[np.ndarray, np.ndarray]:
        h, w = None, None
        planes = []
        valid = None
        for ch in self.channels:
            if ch in ("red", "green", "blue"):
                idx = ("red", "green", "blue").index(ch)
                plane = tile.rgb[idx]
                if self.scale == "255":
                    plane = plane * 255.0
                v = tile.rgb_valid
            elif ch == "alpha":
                plane = tile.rgb_valid.astype("float32")
                v = None
            elif ch == "ndsm":
                nd = tile.ndsm
                if nd is None:
                    nd = np.zeros_like(tile.rgb[0]) if tile.rgb is not None else None
                plane = np.nan_to_num(nd / NDSM_SCALE_M, nan=0.0).astype("float32")
                v = None
            else:  # elevation
                el = tile.elevation
                plane = np.nan_to_num(el, nan=0.0).astype("float32")
                plane = (plane - np.nanmean(el)) / 100.0 if np.isfinite(el).any() else plane
                v = ~np.isnan(el)
            planes.append(plane)
            if v is not None:
                valid = v if valid is None else (valid & v)
        x = np.stack(planes).astype("float32")
        if self.mean is not None and self.std is not None:
            mean = np.asarray(self.mean, dtype="float32")[:, None, None]
            std = np.asarray(self.std, dtype="float32")[:, None, None]
            x = (x - mean) / std
        if valid is None:
            valid = np.ones(x.shape[1:], dtype=bool)
        return x, valid

    def predict(self, tile: TileData) -> tuple[np.ndarray, np.ndarray]:
        x, valid = self._stack(tile)
        if not valid.any():
            return np.zeros((len(self.class_ids),) + valid.shape, dtype="float32"), valid
        out = self.session.run([self.output_name], {self.input_name: x[None]})[0]
        out = np.asarray(out, dtype="float32")
        if self.output_type == "labels":
            labels = out.reshape(out.shape[-2], out.shape[-1]).astype("int64")
            scores = self.one_hot(_resize_nearest(labels, valid.shape), valid)
            return scores, valid
        scores = out[0]
        if self.output_type == "logits":
            scores = scores - scores.max(axis=0, keepdims=True)
            np.exp(scores, out=scores)
            scores /= scores.sum(axis=0, keepdims=True)
        if scores.shape[1:] != valid.shape:
            scores = _resize_bilinear(scores, valid.shape)
        scores = scores * valid[None]
        return scores.astype("float32", copy=False), valid

    def describe(self) -> dict:
        return {
            "backend": "onnx",
            "file": self.card.onnx["file"],
            "channels": self.channels,
            "input_size": self.fixed_size,
            "output_type": self.output_type,
        }


def _resize_nearest(a: np.ndarray, shape) -> np.ndarray:
    h, w = shape
    rows = (np.arange(h) * a.shape[-2] / h).astype(int).clip(0, a.shape[-2] - 1)
    cols = (np.arange(w) * a.shape[-1] / w).astype(int).clip(0, a.shape[-1] - 1)
    return a[..., rows[:, None], cols[None, :]]


def _resize_bilinear(a: np.ndarray, shape) -> np.ndarray:
    """Bilinear resize of the trailing two axes (align_corners=False)."""
    h, w = shape
    sh, sw = a.shape[-2], a.shape[-1]
    ys = (np.arange(h) + 0.5) * sh / h - 0.5
    xs = (np.arange(w) + 0.5) * sw / w - 0.5
    y0 = np.floor(ys).astype(int).clip(0, sh - 1)
    x0 = np.floor(xs).astype(int).clip(0, sw - 1)
    y1 = (y0 + 1).clip(0, sh - 1)
    x1 = (x0 + 1).clip(0, sw - 1)
    wy = (ys - y0).clip(0, 1).astype("float32")
    wx = (xs - x0).clip(0, 1).astype("float32")
    top = a[..., y0[:, None], x0[None, :]] * (1 - wx) + a[..., y0[:, None], x1[None, :]] * wx
    bottom = a[..., y1[:, None], x0[None, :]] * (1 - wx) + a[..., y1[:, None], x1[None, :]] * wx
    return top * (1 - wy)[:, None] + bottom * wy[:, None]
