"""Object detection user plugin for WebODM.

Everything model-specific lives in ``models/*.json`` (model cards): which ONNX
file to run, how its output is laid out (YOLO or torchvision family), its class
names and the parameters that work well for it. The package itself is the
geospatial plumbing shared by every detector: opening the orthophoto, tiling
it with overlap, letterboxing tiles into the model input, mapping boxes back to
raster pixels, merging duplicates across tiles and writing GeoJSON.
"""

__version__ = "1.0.0"
