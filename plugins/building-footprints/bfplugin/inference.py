"""Running the ONNX model on one window.

The card describes the graph (tensor names, static input size, channel order,
scaling and normalisation, which output channel is the mask / boundary /
signed distance and the activation each needs). This module checks the file
(presence, sha-256), loads it with ONNX Runtime, verifies the graph matches
the card and turns one RGB window into the probability and distance planes.
Anything that does not match fails the run with a message that says what to
fix — there is no separate pre-run validation.
"""

import hashlib
import os

import numpy as np

from .errors import ModelError


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _activate(x: np.ndarray, activation: str) -> np.ndarray:
    if activation == "sigmoid":
        out = np.empty_like(x)
        pos = x >= 0
        out[pos] = 1.0 / (1.0 + np.exp(-x[pos]))
        e = np.exp(x[~pos])
        out[~pos] = e / (1.0 + e)
        return out
    if activation == "tanh":
        return np.tanh(x)
    return x


class OnnxModel:
    def __init__(self, card, progress=None):
        self.card = card
        spec = card.onnx
        path = card.model_path
        if not os.path.isfile(path):
            raise ModelError(
                f"model file missing from the package: models/{card.file} "
                f"(run tools/fetch_models.py before tools/build.sh, or add the file to models/)"
            )
        if card.sha256:
            actual = sha256_of(path)
            if actual != card.sha256:
                raise ModelError(
                    f"model file models/{card.file} does not match its card's sha256 "
                    f"(expected {card.sha256[:12]}…, got {actual[:12]}…); the download is corrupt or the "
                    f"card points at a different export — re-run tools/fetch_models.py or fix the card"
                )
        try:
            import onnxruntime as ort
        except ImportError as e:
            raise ModelError(
                "ONNX Runtime is not available in the plugin sandbox, so the footprint model cannot run. "
                "Ask the platform operator to include 'onnxruntime' in the plugin-runner image."
            ) from e

        self.threads = None
        try:
            self.session = self._open_session(ort, path)
        except Exception as e:
            if "pthread_create" not in str(e) and "thread" not in str(e).lower():
                raise ModelError(f"model models/{card.file} failed to load: {e}") from e
            # The sandbox caps processes/threads and address space; ONNX Runtime's
            # default pool (one thread per core) can exceed either. Run single-threaded.
            if progress is not None:
                progress.warn("could not create the inference thread pool; running single-threaded")
            try:
                self.session = self._open_session(ort, path, threads=1)
            except Exception as e2:
                raise ModelError(f"model models/{card.file} failed to load: {e2}") from e2

        self.channels = card.channels
        self.scale = spec.get("scale", "unit")
        self.mean = None if spec.get("mean") is None else np.asarray(spec["mean"], dtype="float32")[:, None, None]
        self.std = None if spec.get("std") is None else np.asarray(spec["std"], dtype="float32")[:, None, None]
        self.roles = {role: dict(s) for role, s in spec["outputs"].items()}
        self.input_size = card.input_size
        self._validate_io(spec)

    def _open_session(self, ort, path, threads: int | None = None):
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        if threads:
            opts.intra_op_num_threads = threads
            opts.inter_op_num_threads = 1
        self.threads = threads
        return ort.InferenceSession(path, sess_options=opts, providers=["CPUExecutionProvider"])

    def _validate_io(self, spec):
        inputs = {i.name: i for i in self.session.get_inputs()}
        outputs = {o.name: o for o in self.session.get_outputs()}
        self.input_name = spec.get("input") or next(iter(inputs))
        self.output_name = spec.get("output") or next(iter(outputs))
        if self.input_name not in inputs:
            raise ModelError(f"model has no input '{self.input_name}' (has: {', '.join(inputs)}); fix the card's onnx.input")
        if self.output_name not in outputs:
            raise ModelError(f"model has no output '{self.output_name}' (has: {', '.join(outputs)}); fix the card's onnx.output")
        shape = list(inputs[self.input_name].shape)
        if len(shape) != 4:
            raise ModelError(f"model input must be NCHW, got shape {shape}")
        _, c, h, w = shape
        if isinstance(c, int) and c != 3:
            raise ModelError(f"model expects {c} input channels; this plugin feeds RGB (3)")
        static = [d for d in (h, w) if isinstance(d, int)]
        if static:
            if len(set(static)) != 1:
                raise ModelError(f"only square static input sizes are supported, got {h}x{w}")
            if static[0] != self.input_size:
                raise ModelError(
                    f"card says onnx.input_size {self.input_size} but the model's static input is {static[0]} px"
                )
        oshape = list(outputs[self.output_name].shape)
        if len(oshape) != 4:
            raise ModelError(f"model output must be NCHW planes, got shape {oshape}")
        max_ch = max(s["channel"] for s in self.roles.values())
        if isinstance(oshape[1], int) and oshape[1] <= max_ch:
            raise ModelError(
                f"model emits {oshape[1]} output channels but the card uses channel {max_ch}; fix onnx.outputs"
            )
        self.output_shape = oshape

    def stack(self, rgb: np.ndarray) -> np.ndarray:
        """Model input (3, h, w) from RGB in [0, 1], in the card's channel order and normalisation."""
        order = [("red", "green", "blue").index(ch) for ch in self.channels]
        x = rgb[order].astype("float32", copy=True)
        if self.scale == "255":
            x *= 255.0
        if self.mean is not None:
            x -= self.mean
            x /= self.std
        return x

    def predict(self, rgb: np.ndarray) -> dict[str, np.ndarray]:
        """Planes for one window: ``{"mask": prob, "distance": signed distance[, "boundary": prob]}``."""
        x = self.stack(rgb)
        h, w = x.shape[1:]
        try:
            out = self.session.run([self.output_name], {self.input_name: x[None]})[0]
        except Exception as e:
            raise ModelError(f"model inference failed on a {w}x{h} window: {e}") from e
        out = np.asarray(out, dtype="float32")
        if out.ndim != 4 or out.shape[0] != 1 or out.shape[2:] != (h, w):
            raise ModelError(
                f"model returned output of shape {list(out.shape)} for a {w}x{h} window; the plugin expects "
                f"[1, channels, {h}, {w}] (one plane per output channel at the input size) — check the card's "
                f"onnx.input_size and the export"
            )
        max_ch = max(s["channel"] for s in self.roles.values())
        if out.shape[1] <= max_ch:
            raise ModelError(f"model emitted {out.shape[1]} channels but the card uses channel {max_ch}")
        return {role: _activate(out[0, s["channel"]], s.get("activation", "none")) for role, s in self.roles.items()}

    def describe(self) -> dict:
        return {
            "backend": "onnx",
            "file": self.card.file,
            "input": self.input_name,
            "output": self.output_name,
            "input_size": self.input_size,
            "threads": self.threads or "default",
            "channels": self.channels,
            "scale": self.scale,
            "normalized": self.mean is not None,
            "outputs": self.roles,
        }
