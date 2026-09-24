from frappe.model.document import Document


class WebODMRasterMetadata(Document):
    """One row per task raster (orthophoto / dsm / dtm) holding the normalized
    header metadata read by the geospatial service. Written by
    ``webodm_core.webodm_core.processing.raster_metadata``; read through
    ``api.task.get_raster_metadata`` and the task document itself."""
