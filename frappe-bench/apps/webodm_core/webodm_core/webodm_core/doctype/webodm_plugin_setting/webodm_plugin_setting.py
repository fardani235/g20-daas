import frappe
from frappe.model.document import Document


class WebODMPluginSetting(Document):
    """Per-organization enablement and default parameters for a plugin.

    Organization is stamped from the acting user (never the payload). A given
    plugin may have at most one setting row per organization.
    """

    def validate(self):
        self._ensure_unique_per_org()

    def _ensure_unique_per_org(self):
        existing = frappe.db.get_value(
            "WebODM Plugin Setting",
            {"plugin": self.plugin, "organization": self.organization, "name": ["!=", self.name]},
            "name",
        )
        if existing:
            frappe.throw(
                f"Plugin '{self.plugin}' is already configured for this organization"
            )
