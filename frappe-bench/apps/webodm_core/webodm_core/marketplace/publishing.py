"""Publishing helpers for platform admins and CI.

Desk is the publishing UI in this version; these functions are the scriptable
equivalent, e.g. after ``plugins/<name>/tools/build.sh`` produced a zip::

    bench --site <site> execute webodm_core.marketplace.publishing.publish_release \\
        --kwargs '{"product": "object-detection", "artifact_path": "/tmp/object-detection-1.0.0.zip",
                   "license": "MIT", "license_notes": "Weights: DeepForest (MIT)"}'

Both functions run as the current session user (``bench execute`` =
Administrator, which commits when the call returns); they are not whitelisted.
"""

import os

import frappe

from webodm_core.marketplace import kinds
from webodm_core.plugins import files as files_mod

_PRODUCT = "WebODM Product"
_RELEASE = "WebODM Product Release"


def upsert_product(product_id: str, *, title: str, publisher: str, artifact_kind: str = "plugin",
                   status: str | None = None, categories: list[str] | None = None, **fields):
    """Create or update a product. ``fields`` are any other product fields
    (summary, description, docs, docs_url, homepage_url, allow_anonymous_download...)."""
    if frappe.db.exists(_PRODUCT, product_id):
        doc = frappe.get_doc(_PRODUCT, product_id)
    else:
        doc = frappe.get_doc({"doctype": _PRODUCT, "product_id": product_id})
    doc.title = title
    doc.publisher = publisher
    doc.artifact_kind = artifact_kind
    if status:
        doc.status = status
    for key, value in fields.items():
        doc.set(key, value)
    if categories is not None:
        doc.set("categories", [{"category": c} for c in categories])
    doc.save(ignore_permissions=True)
    return doc


def publish_release(product: str, artifact_path: str, *, license: str, version: str | None = None,
                    release_notes: str = "", license_notes: str = "", status: str = "Published"):
    """Store ``artifact_path`` as a new release of ``product`` and (by default) publish it.

    ``version`` defaults to the manifest version for plugin artifacts. The
    file is copied (not moved) into private files, unattached first — the
    release's ``validate`` looks the File up by URL — and attached to the new
    release afterwards. Re-running for an existing version is an error: a
    published release is immutable, publish the next version instead.
    """
    if not os.path.exists(artifact_path):
        raise FileNotFoundError(artifact_path)
    product_doc = frappe.get_doc(_PRODUCT, product)

    meta = kinds.inspect(product_doc.artifact_kind, artifact_path)
    version = version or meta.get("version")
    if not version:
        raise ValueError("version is required for this artifact kind")
    name = f"{product}-{version}"
    if frappe.db.exists(_RELEASE, name):
        raise ValueError(f"release {name} already exists; publish a new version instead")

    ext = os.path.splitext(artifact_path)[1] or ".bin"
    file_doc = files_mod.save_private_file_from_path(
        artifact_path, f"{product}-{version}{ext}", ignore_permissions=True, move=False,
    )
    try:
        release = frappe.get_doc({
            "doctype": _RELEASE,
            "product": product,
            "version": version,
            "status": status,
            "artifact": file_doc.file_url,
            "license": license,
            "license_notes": license_notes,
            "release_notes": release_notes,
        }).insert(ignore_permissions=True)
    except Exception:
        frappe.delete_doc("File", file_doc.name, force=True, ignore_permissions=True)
        raise

    file_doc.db_set({
        "attached_to_doctype": _RELEASE,
        "attached_to_name": release.name,
        "attached_to_field": "artifact",
    })
    return release
