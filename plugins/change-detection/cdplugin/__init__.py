"""Change Detection user plugin for WebODM.

Compares two surveys of the same site: brings their elevation surfaces onto
one grid, removes the constant height offset (and, optionally, the tilt) that
independent processing runs carry, measures the noise on ground that did not
move and reports only the change that stands out from it — as cut and fill
regions with areas and volumes. The orthophotos are used to keep vegetation
out of the earthwork numbers and, secondarily, to flag places that look
different although their height is the same.

No machine learning: every step is a classical, explainable raster operation
(numpy, scipy, rasterio, shapely).
"""

__version__ = "1.0.0"
