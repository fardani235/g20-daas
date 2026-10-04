"""Generate the tiny ONNX fixtures used by the tests.

    /tmp/bfvenv/bin/python tests/fixtures/make_fixtures.py     # needs onnx + numpy

The fixture "model" is a single 1x1 convolution that reads the answer off the
image: the red channel is the building mask, the green channel encodes the
signed distance and blue the boundary (see ``tests/conftest.py`` for how the
synthetic orthophoto is drawn). It has the real model's contract —
``image[1,3,256,256]`` → ``logits[1,3,256,256]`` with raw logits per channel
(sigmoid/tanh applied by the plugin) — so the tests exercise tiling, blending,
thresholding, the watershed and vectorisation deterministically without the
220 MB weights. ``onnx`` is only needed to regenerate the files; the tests
load the committed ones with onnxruntime.
"""

import os

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

HERE = os.path.dirname(os.path.abspath(__file__))
OPSET = 13
SIZE = 256


def _save(graph, path):
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", OPSET)])
    model.ir_version = 8
    onnx.checker.check_model(model)
    onnx.save(model, path)
    print("wrote", os.path.relpath(path, HERE))


def footprint_model(path, out_channels=3, stride=1):
    """logits = Conv1x1(image): ch0 = 12·R − 6, ch1 = 12·B − 6, ch2 = 6·G − 3."""
    weight = np.zeros((out_channels, 3, 1, 1), dtype=np.float32)
    bias = np.zeros(out_channels, dtype=np.float32)
    gains = [(0, 12.0, -6.0), (2, 12.0, -6.0), (1, 6.0, -3.0)]  # (input channel, gain, bias) per output
    for o in range(out_channels):
        ch, gain, b = gains[o]
        weight[o, ch, 0, 0] = gain
        bias[o] = b
    node = helper.make_node("Conv", ["image", "weight", "bias"], ["logits"], kernel_shape=[1, 1],
                            strides=[stride, stride])
    out = SIZE // stride
    graph = helper.make_graph(
        [node], "tiny_footprints",
        [helper.make_tensor_value_info("image", TensorProto.FLOAT, [1, 3, SIZE, SIZE])],
        [helper.make_tensor_value_info("logits", TensorProto.FLOAT, [1, out_channels, out, out])],
        initializer=[numpy_helper.from_array(weight, "weight"), numpy_helper.from_array(bias, "bias")],
    )
    _save(graph, path)


def main():
    footprint_model(os.path.join(HERE, "tiny_footprints.onnx"))
    footprint_model(os.path.join(HERE, "tiny_footprints_badshape.onnx"), stride=2)   # output 128x128
    footprint_model(os.path.join(HERE, "tiny_footprints_2ch.onnx"), out_channels=2)  # no distance channel


if __name__ == "__main__":
    main()
