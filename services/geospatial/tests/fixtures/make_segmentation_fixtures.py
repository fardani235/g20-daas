"""Generate the tiny segmentation ONNX fixtures used by the tests.

Run from the repository root with the service venv:

    ./venv/bin/python tests/fixtures/make_segmentation_fixtures.py

The models emit a fixed spatial pattern (a square of class 1 on a class-0
background) so tests can assert geometry, tiling, and stitching deterministically
without depending on real weights. ``onnx`` is only needed to regenerate them;
tests load the committed files with onnxruntime.
"""

import os

import numpy as np
import onnx
from onnx import TensorProto, helper

HERE = os.path.dirname(os.path.abspath(__file__))

SIZE = 64
# Rows/cols of the class-1 square within each tile.
SQUARE = slice(16, 48)


def _pattern(channels: int, class_0: float = 0.9, class_1: float = 0.1) -> np.ndarray:
    pattern = np.zeros((1, channels, SIZE, SIZE), dtype=np.float32)
    if channels >= 2:
        pattern[:, 0, :, :] = class_0
        pattern[:, 1, :, :] = class_1
        pattern[:, 0, SQUARE, SQUARE] = class_1
        pattern[:, 1, SQUARE, SQUARE] = class_0
    else:
        # Single foreground channel: low everywhere, high on the square.
        pattern[:, 0, :, :] = class_1
        pattern[:, 0, SQUARE, SQUARE] = class_0
    return pattern


def _build(path: str, in_channels: int = 3, out_channels: int = 2,
           out_rank: int = 4) -> None:
    if out_rank == 4:
        pattern = _pattern(out_channels)
    else:
        pattern = _pattern(out_channels)[0, 0][None, :, :]

    nodes = [
        # Reference the input so it is not a dangling graph input, then cancel it.
        helper.make_node("ReduceMean", ["images"], ["mean"], axes=[0, 1, 2, 3], keepdims=1),
        helper.make_node("Mul", ["mean", "zero"], ["zeroed"]),
        helper.make_node("Add", ["pattern", "zeroed"], ["masks"]),
    ]
    initializers = [
        helper.make_tensor("pattern", TensorProto.FLOAT, list(pattern.shape), pattern.ravel()),
        helper.make_tensor("zero", TensorProto.FLOAT, [1], [0.0]),
    ]
    graph = helper.make_graph(
        nodes,
        "tiny_segmenter",
        [helper.make_tensor_value_info(
            "images", TensorProto.FLOAT, [1, in_channels, SIZE, SIZE])],
        [helper.make_tensor_value_info("masks", TensorProto.FLOAT, list(pattern.shape))],
        initializers,
    )
    model = helper.make_model(
        graph, opset_imports=[helper.make_opsetid("", 13)], producer_name="webodm-tests"
    )
    model.ir_version = 8
    onnx.checker.check_model(model)
    onnx.save(model, path)
    print(f"wrote {path} (out shape {list(pattern.shape)})")


def main() -> None:
    _build(os.path.join(HERE, "tiny_segmenter.onnx"), out_channels=2, out_rank=4)
    _build(os.path.join(HERE, "tiny_segmenter_binary.onnx"), out_channels=1, out_rank=4)
    _build(os.path.join(HERE, "tiny_segmenter_badrank.onnx"), out_channels=2, out_rank=3)


if __name__ == "__main__":
    main()
