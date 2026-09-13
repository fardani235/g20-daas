import frappe
from frappe.model.document import Document


class WebODMPlugin(Document):
    """Catalog entry for an analysis plugin.

    Rows are created and updated by the catalog sync from the geospatial
    service; platform admins may flip ``platform_enabled``. Users do not author
    these rows.
    """
