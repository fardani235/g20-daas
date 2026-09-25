from frappe.model.document import Document


class WebODMTaskAsset(Document):
    """One row per task output (orthophoto / dsm / dtm / point_cloud / model)
    linking the host serving-cache copy (``file_url``) to its canonical object
    in storage (``storage_key``). Written by
    ``webodm_core.storage.assets.record_asset``; the cache reaper only evicts
    blobs whose row carries a key."""
