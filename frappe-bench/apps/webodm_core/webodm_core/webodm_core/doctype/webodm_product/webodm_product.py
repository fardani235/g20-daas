import frappe
from frappe.model.document import Document

from webodm_core.marketplace import kinds
from webodm_core.plugins import package as package_mod

MAX_SUMMARY_CHARS = 200


def redistribution_blockers(product_name: str) -> list[str]:
    """Published releases of ``product_name`` whose license forbids redistribution.

    Returns ``"<version> (<license>)"`` strings so callers can name them.
    Shared by the product (flipping the switch on) and the release
    (publishing under a product with the switch on) validations.
    """
    rows = frappe.get_all(
        "WebODM Product Release",
        filters={"product": product_name, "status": "Published"},
        fields=["version", "license"],
    )
    blockers = []
    for row in rows:
        if not row.license:
            blockers.append(f"{row.version} (no license)")
            continue
        if not frappe.db.get_value("WebODM License", row.license, "permits_redistribution"):
            blockers.append(f"{row.version} ({row.license})")
    return blockers


class WebODMProduct(Document):
    """A marketplace listing. Global: carries no organization and is readable
    by everyone (signed-out visitors included, through the guest endpoints in
    ``api/marketplace.py``). What an organization *has* is a ``WebODM
    Entitlement`` plus, for plugins, its own ``WebODM Plugin`` row.

    Written by platform admins only (Desk or ``marketplace.publishing``).
    """

    def validate(self):
        if not package_mod.is_valid_id(self.product_id):
            frappe.throw("Product ID must be 2-64 lowercase letters, digits or hyphens")
        if self.artifact_kind not in kinds.KINDS:
            frappe.throw(f"Unknown artifact kind '{self.artifact_kind}'")
        if self.summary and len(self.summary) > MAX_SUMMARY_CHARS:
            frappe.throw(f"Summary must be at most {MAX_SUMMARY_CHARS} characters")

        if self.status == "Published" and not kinds.is_installable(self.artifact_kind):
            frappe.throw(
                f"{kinds.label(self.artifact_kind)} products cannot be published yet: "
                "no installer exists for this artifact kind"
            )

        # The switch lets *anyone* download the artifact, so every published
        # version's license has to allow handing the bytes on. Checked here and
        # again when a release is published (webodm_product_release.py).
        if self.allow_anonymous_download and not self.is_new():
            blockers = redistribution_blockers(self.name)
            if blockers:
                frappe.throw(
                    "Anonymous download cannot be enabled: the license of release(s) "
                    + ", ".join(blockers) + " does not permit redistribution"
                )
