"""Building Footprints user plugin for WebODM.

Extracts one polygon per building from a task's orthophoto with a multi-task
segmentation model (mask + boundary + signed distance) and a watershed on the
distance plane that separates touching buildings. Everything model-specific
lives in ``models/*.json`` (model cards); the rest of the package is the
geospatial plumbing: grid alignment, Gaussian-weighted tiling, instance
separation, vectorisation and GeoJSON output.
"""

__version__ = "1.0.0"
