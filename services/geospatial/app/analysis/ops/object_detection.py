"""Object detection operation.

Runs a YOLO-style ONNX detector over a task's orthophoto and emits detections as
a GeoJSON bounding-box collection. See ``app.analysis.detection.*`` for the
model management and inference implementation.
"""
from app.analysis.detection import models
from app.analysis.detection.detector import DetectionParams, run_detection
from app.analysis.registry import AnalysisOp, register

# Curated models the UI can offer, with their labels, family, and sensible
# starting parameters. Users may still supply a custom model instead.
KNOWN_MODELS = [
    {
        "id": "coco",
        "label": "COCO — general objects (default)",
        "model": "yolov8n.onnx",
        "labels": "coco.txt",
        "family": "yolo",
        "label_offset": 0,
        "recommended": {"tile_size": 640, "overlap": 64, "confidence": 0.25},
    },
    {
        "id": "visdrone",
        "label": "VisDrone — aerial vehicles & people",
        "model": "visdrone-yolov11s.onnx",
        "labels": "visdrone.txt",
        "family": "yolo",
        "label_offset": 0,
        "recommended": {"tile_size": 640, "overlap": 128, "confidence": 0.4},
    },
    {
        "id": "deepforest-tree",
        "label": "DeepForest — tree crowns",
        "model": "deepforest.onnx",
        "labels": "tree.txt",
        "family": "torchvision",
        "label_offset": 0,
        "recommended": {"tile_size": 256, "overlap": 64, "confidence": 0.3},
    },
]


def validate_detection(params: DetectionParams) -> None:
    """Pre-run check: the model and labels load and agree on the class count."""
    session = models.load_session(params.model)
    labels = models.read_labels(params.labels)
    models.validate_session(session, labels)


register(
    AnalysisOp(
        op_id="object-detection",
        label="Object Detection",
        description="Detect objects on the orthophoto with a YOLO or DeepForest ONNX model",
        version="1.0.0",
        params_model=DetectionParams,
        output_kind="vector",
        render_kind="detections",
        inputs=[{"name": "raster", "datasets": ["orthophoto"]}],
        handler=run_detection,
        timeout_seconds=1800,
        validator=validate_detection,
        models=KNOWN_MODELS,
    )
)
