import frappe
from frappe.model.document import Document


class WebODMPlugin(Document):
    """Catalog entry for an analysis plugin.

    Two kinds share this table, told apart by ``plugin_type``:

    - **System** rows are created and updated by the catalog sync from the
      geospatial service and are visible to every organization; platform
      admins may flip ``platform_enabled``. Users do not author these rows.
    - **User** rows are packages uploaded by an organization admin
      (``api/plugins.py:upload_plugin``) or installed from the marketplace
      (``api/marketplace.py:install_product``; ``source``/``product``/
      ``release`` record where the package came from). They carry
      ``organization`` and ``package`` and are only visible to that
      organization; the platform kill switch still applies.
    """

    def validate(self):
        if self.plugin_type == "User":
            if not self.organization:
                frappe.throw("User plugins must belong to an organization")
            if not self.source:
                self.source = "Upload"
            if self.source != "Marketplace":
                self.product = None
                self.release = None
        else:
            # A System row never carries tenant data or marketplace provenance.
            self.organization = None
            self.package = None
            self.package_hash = None
            self.source = None
            self.product = None
            self.release = None
