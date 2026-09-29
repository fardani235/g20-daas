from frappe.model.document import Document


class WebODMEntitlement(Document):
    """An organization acquired a marketplace product.

    One row per (organization, product). ``release`` is the installed
    version and ``plugin`` the org-scoped ``WebODM Plugin`` row that
    ``api/plugins.install_user_plugin`` produced; ``status`` flips to
    ``Removed`` when the plugin is uninstalled (from either the marketplace or
    the Plugins page) or replaced by a manual upload. Written only by
    ``webodm_core.marketplace.install``; org-scoped through the permission
    hooks and stamped from the session like every tenant-owned row."""
