"""3D Reconstruction plugin library.

Turns a task's ODM outputs (orthophoto, DSM/DTM, LAZ point cloud, textured
mesh) into one web-ready, georeferenced GLB for the platform's 3D viewer.
See ``pipeline.run`` for the workflow selection and ``gltf`` for the output
conventions (Z-up, local origin + ``CESIUM_RTC``, ``webodm_georef`` extras).
"""

__version__ = "1.0.0"
