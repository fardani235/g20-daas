"""Semantic segmentation user plugin for WebODM.

Everything model-specific lives in ``models/*.json`` (model cards) and
``segplugin/backends``; the rest of the package is the geospatial plumbing
shared by every model: input validation and alignment, tiling with overlap
blending, height derivation, post-processing and output writing.
"""

__version__ = "1.0.0"
