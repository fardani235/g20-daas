"""Importing this package registers every analysis operation in the registry."""

from app.analysis.ops import (  # noqa: F401
    contours,
    hillshade,
    object_detection,
    semantic_segmentation,
)
