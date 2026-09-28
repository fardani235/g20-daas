"""Importing this package registers every analysis operation in the registry."""

from app.analysis.ops import (  # noqa: F401
    contours,
    hillshade,
    semantic_segmentation,
)
