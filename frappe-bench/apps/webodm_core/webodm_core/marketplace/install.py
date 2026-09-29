"""Entitlement + install/uninstall of marketplace products for one organization.

The catalog is global; everything here is scoped to the organization passed
in by the API layer (which has already applied the org-admin gate). For the
``plugin`` kind the actual installation is ``api/plugins.install_user_plugin``
— the single install seam — reached through ``kinds.install``. This module
only prepares a verified private copy of the release artifact, calls the
seam and records the ``WebODM Entitlement``.
"""

import hashlib
import os
import shutil
import tempfile

import frappe
from frappe.utils import get_site_path, now_datetime

from webodm_core.marketplace import kinds
from webodm_core.plugins import files as files_mod

_PRODUCT = "WebODM Product"
_RELEASE = "WebODM Product Release"
_ENTITLEMENT = "WebODM Entitlement"
_CHUNK = 1024 * 1024


class InstallError(frappe.ValidationError):
    """The product/release cannot be installed (state, kind or integrity)."""


def _throw(message: str, exc=InstallError):
    frappe.throw(message, exc)


def installable_release(product_name: str, release_name: str | None = None):
    """Return ``(product, release)`` docs after checking they can be installed.

    Publishers must be Active and the product Published; the release (given,
    or the latest Published one) must be Published — Yanked releases are not
    offered even by explicit name.
    """
    if not frappe.db.exists(_PRODUCT, product_name):
        frappe.throw(f"Unknown product: {product_name}", frappe.DoesNotExistError)
    product = frappe.get_doc(_PRODUCT, product_name)
    if product.status != "Published":
        frappe.throw(f"Unknown product: {product_name}", frappe.DoesNotExistError)
    if frappe.db.get_value("WebODM Publisher", product.publisher, "status") != "Active":
        _throw("This publisher is suspended; the product cannot be installed")
    if not kinds.is_installable(product.artifact_kind):
        _throw(f"{kinds.label(product.artifact_kind)} products cannot be installed yet")

    if release_name:
        if not frappe.db.exists(_RELEASE, {"name": release_name, "product": product.name}):
            frappe.throw(f"Unknown release: {release_name}", frappe.DoesNotExistError)
        release = frappe.get_doc(_RELEASE, release_name)
        if release.status != "Published":
            _throw(f"Release {release.version} is not available for installation")
    else:
        release = latest_published_release(product.name)
        if release is None:
            _throw("This product has no installable release")
    return product, release


def latest_published_release(product_name: str):
    name = frappe.db.get_value(
        _RELEASE, {"product": product_name, "status": "Published"}, "name",
        order_by="published_on desc, creation desc",
    )
    return frappe.get_doc(_RELEASE, name) if name else None


def _spool_dir() -> str:
    out_dir = os.path.abspath(get_site_path("private", "files", "plugin_packages"))
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def copy_artifact(release) -> str:
    """Copy the release artifact into the install spool and verify its sha-256.

    The install seam *moves* its input into the organization's package file,
    so the canonical release artifact is copied rather than handed over. The
    hash check makes the copy — not just the catalog row — the thing that is
    trusted.
    """
    file_doc = files_mod.file_doc_for_url(
        release.artifact, attached_to_doctype=_RELEASE, attached_to_name=release.name,
    )
    src = files_mod.abs_path_for_file_doc(file_doc)
    if not os.path.exists(src):
        _throw(f"Release artifact for {release.version} is missing on disk")

    fd, dest = tempfile.mkstemp(prefix="market-", suffix=".zip", dir=_spool_dir())
    digest = hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as out, open(src, "rb") as inp:
            for chunk in iter(lambda: inp.read(_CHUNK), b""):
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != (release.artifact_hash or ""):
            _throw(f"Release artifact for {release.version} failed its integrity check")
    except Exception:
        try:
            os.remove(dest)
        except OSError:
            pass
        raise
    return dest


def entitlement_row(org: str, product_name: str):
    name = frappe.db.get_value(_ENTITLEMENT, {"organization": org, "product": product_name}, "name")
    return frappe.get_doc(_ENTITLEMENT, name) if name else None


def install_product(org: str, product_name: str, release_name: str | None = None) -> dict:
    """Install ``product`` (its latest or the named Published release) into ``org``.

    Runs as the acting org admin: the seam stamps the plugin setting and the
    entitlement from the session, so the caller must already have checked
    that ``org`` is the session user's organization.
    """
    product, release = installable_release(product_name, release_name)
    path = copy_artifact(release)
    installed = kinds.install(product.artifact_kind, release, org, path)

    ent = entitlement_row(org, product.name)
    if ent is None:
        ent = frappe.get_doc({"doctype": _ENTITLEMENT, "organization": org, "product": product.name})
    ent.release = release.name
    ent.plugin = installed.get("name") if product.artifact_kind == "plugin" else None
    ent.status = "Active"
    ent.installed_by = frappe.session.user
    ent.installed_on = now_datetime()
    ent.removed_on = None
    ent.save(ignore_permissions=True)

    return {
        "product": product.name,
        "release": release.name,
        "version": release.version,
        "entitlement": ent.name,
        "installed": installed,
    }


def uninstall_product(org: str, product_name: str) -> dict:
    """Remove the organization's install of ``product`` through the removal seam."""
    from webodm_core.api import plugins as plugins_api

    ent = entitlement_row(org, product_name)
    if ent is None or ent.status != "Active":
        frappe.throw(f"{product_name} is not installed", frappe.DoesNotExistError)

    plugin = ent.plugin
    if plugin and frappe.db.exists("WebODM Plugin", plugin):
        # remove_plugin re-checks the org gate and calls detach_plugin().
        plugins_api.remove_plugin(plugin)
    else:
        _mark_removed(ent)
    return {"product": product_name, "removed": True}


def _mark_removed(ent):
    ent.status = "Removed"
    ent.plugin = None
    ent.removed_on = now_datetime()
    ent.save(ignore_permissions=True)


def detach_plugin(plugin_name: str):
    """The plugin row no longer comes from the marketplace: close its entitlement.

    Called by ``remove_plugin`` (uninstall from either UI) and by
    ``install_user_plugin`` when a manual upload replaces a marketplace
    install, so "Installed" on a product page always means "the current row
    is this product".
    """
    for name in frappe.get_all(_ENTITLEMENT, filters={"plugin": plugin_name, "status": "Active"}, pluck="name"):
        _mark_removed(frappe.get_doc(_ENTITLEMENT, name))


def org_entitlements(org: str | None) -> dict[str, dict]:
    """Active entitlements of ``org`` keyed by product, with the installed version."""
    if not org:
        return {}
    rows = frappe.get_all(
        _ENTITLEMENT,
        filters={"organization": org, "status": "Active"},
        fields=["name", "product", "release", "plugin", "installed_on"],
        ignore_permissions=True,
    )
    out = {}
    for row in rows:
        version = frappe.db.get_value(_RELEASE, row.release, "version") if row.release else None
        out[row.product] = {
            "name": row.name,
            "release": row.release,
            "version": version,
            "plugin": row.plugin,
            "installed_on": row.installed_on,
        }
    return out


def install_counts() -> dict[str, int]:
    """Active entitlements per product (one row per org, so this is "orgs using it")."""
    counts: dict[str, int] = {}
    for product in frappe.get_all(_ENTITLEMENT, filters={"status": "Active"}, pluck="product"):
        counts[product] = counts.get(product, 0) + 1
    return counts
