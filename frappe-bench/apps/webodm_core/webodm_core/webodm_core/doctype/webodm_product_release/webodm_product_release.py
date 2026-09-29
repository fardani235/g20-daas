import json
import os

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from webodm_core.marketplace import kinds
from webodm_core.plugins import files as files_mod
from webodm_core.plugins import package as package_mod

# Fields that describe *the bytes* of a published release; they never change.
# Everything else (status Published <-> Yanked, notes) stays editable.
FROZEN_FIELDS = ("artifact", "artifact_hash", "artifact_size", "license", "manifest")


def artifact_file_doc(file_url: str):
    """The private File behind a release's ``artifact`` URL.

    Looked up by URL rather than by attachment because Desk attaches files to
    a new document only after insert (``attach_files_to_document`` runs on
    ``on_update``), i.e. after this validation. Only platform admins can write
    releases, so the "point my row at someone else's private file" concern
    behind ``files.file_doc_for_url`` does not apply at publish time; the
    download path (``api/marketplace.download_release``) does require the
    attachment.
    """
    if not file_url:
        frappe.throw("Artifact is required")
    name = frappe.db.get_value("File", {"file_url": file_url}, "name")
    if not name:
        frappe.throw(f"Artifact {file_url} is not a stored file")
    file_doc = frappe.get_doc("File", name)
    if not file_doc.is_private:
        frappe.throw("Artifact must be a private file")
    return file_doc


class WebODMProductRelease(Document):
    """One version of a marketplace product with an immutable artifact.

    ``validate`` resolves the artifact, records its sha-256 and size, runs the
    kind's inspector (for plugins: the same package validation an upload
    gets, keeping the normalized manifest) and enforces the license rules.
    Once published, the artifact and license are frozen; a release can be
    Yanked to stop new installs without touching existing ones.
    """

    def validate(self):
        if not package_mod.is_valid_version(self.version):
            frappe.throw("Version must be a dotted version string such as 1.0.0")

        product = frappe.get_doc("WebODM Product", self.product)
        before = None if self.is_new() else self.get_doc_before_save()
        frozen = bool(before and before.status in ("Published", "Yanked"))

        if frozen:
            for field in FROZEN_FIELDS:
                if (self.get(field) or None) != (before.get(field) or None):
                    frappe.throw(
                        f"Release {self.version} has been published; its {field} cannot change. "
                        "Publish a new version instead."
                    )
        else:
            self._inspect_artifact(product.artifact_kind)

        self._check_manifest_consistency()

        if self.status == "Published":
            if not self.license:
                frappe.throw("A license is required to publish a release")
            if product.allow_anonymous_download and not frappe.db.get_value(
                "WebODM License", self.license, "permits_redistribution"
            ):
                frappe.throw(
                    f"Cannot publish {self.version} under license {self.license}: the product "
                    "allows anonymous download and this license does not permit redistribution"
                )
            if not self.published_on:
                self.published_on = now_datetime()

    def _inspect_artifact(self, kind: str):
        file_doc = artifact_file_doc(self.artifact)
        path = files_mod.abs_path_for_file_doc(file_doc)
        if not os.path.exists(path):
            frappe.throw(f"Artifact file {file_doc.file_name} is missing on disk")

        try:
            meta = kinds.inspect(kind, path)
        except kinds.KindError as e:
            frappe.throw(f"Invalid {kinds.label(kind).lower()} artifact: {e}")

        if kind == "plugin" and meta.get("version") != self.version:
            frappe.throw(
                f"Manifest version {meta.get('version')!r} does not match release version {self.version!r}"
            )

        self.artifact_hash = package_mod.sha256_of(path)
        self.artifact_size = os.path.getsize(path)
        self.manifest = json.dumps(meta) if meta else None

    def _check_manifest_consistency(self):
        """Every release of a product must describe the same plugin id."""
        manifest = self.manifest_dict()
        plugin_id = manifest.get("id")
        if not plugin_id:
            return
        for row in frappe.get_all(
            "WebODM Product Release",
            filters={"product": self.product, "name": ["!=", self.name or ""]},
            fields=["version", "manifest"],
        ):
            other = frappe.parse_json(row.manifest or "{}") or {}
            if other.get("id") and other["id"] != plugin_id:
                frappe.throw(
                    f"Manifest id {plugin_id!r} differs from release {row.version} ({other['id']!r}); "
                    "all releases of a product must ship the same plugin"
                )

    def manifest_dict(self) -> dict:
        try:
            return frappe.parse_json(self.manifest or "{}") or {}
        except Exception:
            return {}
