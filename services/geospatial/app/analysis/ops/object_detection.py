"""Object detection operation.

Runs a YOLO-style ONNX detector over a task's orthophoto and emits detections as
a GeoJSON bounding-box collection. See ``app.analysis.detection.*`` for the
model management and inference implementation.
"""
from app.analysis.detection import models
from app.analysis.detection.detector import DetectionParams, run_detection
from app.analysis.registry import AnalysisOp, register


def validate_detection(params: DetectionParams) -> None:
    """Pre-run check: the model and labels load and agree on the class count."""
    session = models.load_session(params.model)
    labels = models.read_labels(params.labels)
    models.validate_session(session, labels)


register(
    AnalysisOp(
        op_id="object-detection",
        label="Object Detection",
        description="Detect objects on the orthophoto with a YOLO ONNX model",
        version="1.0.0",
        params_model=DetectionParams,
        output_kind="vector",
        render_kind="detections",
        inputs=[{"name": "raster", "datasets": ["orthophoto"]}],
        handler=run_detection,
        timeout_seconds=1800,
        validator=validate_detection,
    )
)
