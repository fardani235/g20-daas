"""Semantic segmentation operation.

Runs a per-class ONNX segmentation model over a task's orthophoto and emits the
classified regions as a GeoJSON polygon collection. See
``app.analysis.segmentation.*`` for the model inspection, tiled inference, mask
stitching, and vectorization implementation.
"""

from app.analysis.segmentation import models
from app.analysis.segmentation.detector import (
    DEFAULT_LABELS,
    DEFAULT_MODEL,
    SegmentationParams,
    run_segmentation,
)
from app.analysis.registry import AnalysisOp, register

# Curated models the UI can offer with sensible starting parameters. The default
# artifact is provisioned by ops; users may still supply a custom model inside
# the managed directory.
KNOWN_MODELS = [
    {
        "id": "default",
        "label": "General segmentation (default)",
        "model": DEFAULT_MODEL,
        "labels": DEFAULT_LABELS,
        "recommended": {
            "tile_size": 512,
            "overlap": 64,
            "threshold": 0.5,
            "min_segment_area": 64,
        },
    },
]


def validate_segmentation(params: SegmentationParams) -> None:
    """Pre-run check: the model loads and matches its label set."""
    session = models.load_session(params.model)
    labels = models.read_labels(params.labels)
    models.validate_session(session, labels)


register(
    AnalysisOp(
        op_id="semantic-segmentation",
        label="Semantic Segmentation",
        description=(
            "Classify land cover on the orthophoto with a per-class ONNX "
            "segmentation model and emit regions as polygons"
        ),
        version="1.0.0",
        params_model=SegmentationParams,
        output_kind="vector",
        render_kind="segmentation",
        inputs=[{"name": "raster", "datasets": ["orthophoto"]}],
        handler=run_segmentation,
        timeout_seconds=1800,
        validator=validate_segmentation,
        models=KNOWN_MODELS,
    )
)
