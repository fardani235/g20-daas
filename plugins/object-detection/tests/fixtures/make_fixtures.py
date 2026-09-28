"""Generate the tiny detector ONNX fixtures used by the tests.

    python3 -m venv /tmp/onnxvenv && /tmp/onnxvenv/bin/pip install onnx numpy
    /tmp/onnxvenv/bin/python tests/fixtures/make_fixtures.py

Each model returns a fixed set of boxes whatever the image (the input is still
consumed, so the graphs are valid), which lets the tests assert tiling,
mapping, merging and GeoJSON output deterministically without real weights.
``onnx`` is only needed to regenerate them; tests load the committed files
with onnxruntime.
"""

import os

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

HERE = os.path.dirname(os.path.abspath(__file__))
OPSET = 13


def _consume(input_name):
    """Nodes producing a scalar 0.0 that depends on the image (keeps the input used)."""
    zero = numpy_helper.from_array(np.zeros((), dtype=np.float32), "zero")
    return [
        helper.make_node("ReduceMean", [input_name], ["mean"], keepdims=0),
        helper.make_node("Mul", ["mean", "zero"], ["nil"]),
    ], [zero]


def _save(graph, path):
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", OPSET)])
    model.ir_version = 8
    onnx.checker.check_model(model)
    onnx.save(model, path)
    print("wrote", os.path.relpath(path, HERE))


def yolo_model(path, anchors: np.ndarray, num_classes: int):
    """YOLOv8-style detector: images[1,3,640,640] -> output0[1, 4+nc, anchors]."""
    n = anchors.shape[1]
    nodes, inits = _consume("images")
    inits.append(numpy_helper.from_array(anchors.astype(np.float32)[None], "pred"))
    nodes.append(helper.make_node("Add", ["pred", "nil"], ["output0"]))
    graph = helper.make_graph(
        nodes, "tiny_yolo",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, 640, 640])],
        [helper.make_tensor_value_info("output0", TensorProto.FLOAT, [1, 4 + num_classes, n])],
        initializer=inits,
    )
    _save(graph, path)


def badrank_model(path):
    """A model whose output is not a detection tensor."""
    nodes, inits = _consume("images")
    inits.append(numpy_helper.from_array(np.zeros((1, 84), dtype=np.float32), "pred"))
    nodes.append(helper.make_node("Add", ["pred", "nil"], ["output0"]))
    graph = helper.make_graph(
        nodes, "tiny_badrank",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, 640, 640])],
        [helper.make_tensor_value_info("output0", TensorProto.FLOAT, [1, 84])],
        initializer=inits,
    )
    _save(graph, path)


def torchvision_model(path, boxes, scores, labels):
    """torchvision-style detector without batch dim: image[3,256,256] -> boxes/scores/labels."""
    nodes, inits = _consume("image")
    inits += [
        numpy_helper.from_array(np.asarray(boxes, dtype=np.float32), "boxes0"),
        numpy_helper.from_array(np.asarray(scores, dtype=np.float32), "scores0"),
        numpy_helper.from_array(np.asarray(labels, dtype=np.int64), "labels"),
    ]
    nodes += [
        helper.make_node("Add", ["boxes0", "nil"], ["boxes"]),
        helper.make_node("Add", ["scores0", "nil"], ["scores"]),
    ]
    n = len(scores)
    graph = helper.make_graph(
        nodes, "tiny_torchvision",
        [helper.make_tensor_value_info("image", TensorProto.FLOAT, [3, 256, 256])],
        [
            helper.make_tensor_value_info("boxes", TensorProto.FLOAT, [n, 4]),
            helper.make_tensor_value_info("scores", TensorProto.FLOAT, [n]),
            helper.make_tensor_value_info("labels", TensorProto.INT64, [n]),
        ],
        initializer=inits,
    )
    _save(graph, path)


def main():
    # 80 classes, 3 anchors: a confident class-0 box in the tile centre, a
    # weaker class-1 box near the top-left corner, and an empty anchor.
    pred = np.zeros((84, 3), dtype=np.float32)
    pred[:4, 0] = [320, 320, 100, 100]
    pred[4 + 0, 0] = 0.9
    pred[:4, 1] = [100, 100, 40, 40]
    pred[4 + 1, 1] = 0.6
    yolo_model(os.path.join(HERE, "tiny_yolo.onnx"), pred, 80)
    yolo_model(os.path.join(HERE, "tiny_yolo_silent.onnx"), np.zeros((84, 3), dtype=np.float32), 80)
    yolo_model(os.path.join(HERE, "tiny_yolo_6cls.onnx"), np.zeros((10, 3), dtype=np.float32), 6)
    badrank_model(os.path.join(HERE, "tiny_badrank.onnx"))
    torchvision_model(os.path.join(HERE, "tiny_torchvision.onnx"),
                      [[30, 30, 80, 80], [100, 100, 140, 140]], [0.9, 0.4], [0, 0])
    torchvision_model(os.path.join(HERE, "tiny_torchvision_1based.onnx"),
                      [[30, 30, 80, 80], [100, 100, 140, 140]], [0.9, 0.4], [1, 1])


if __name__ == "__main__":
    main()
