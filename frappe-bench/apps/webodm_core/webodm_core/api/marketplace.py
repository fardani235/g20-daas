"""Marketplace API: public catalog reads, per-organization install/uninstall,
and artifact download.

Browsing is global — ``list_products`` / ``get_product`` / ``download_release``
allow guests and only ever expose Published data (see ``marketplace.catalog``).
Installing and uninstalling act within the caller's own organization and
require an organization admin, the same gate as uploading a plugin; the work
itself goes through ``marketplace.install`` → ``api.plugins.install_user_plugin``.
"""

import os
from urllib.parse import quote

import frappe
import werkzeug.utils

from webodm_core import tenancy
from webodm_core.marketplace import catalog
from webodm_core.marketplace import install as install_mod
from webodm_core.plugins import files as files_mod

_RELEASE = "WebODM Product Release"
_PRODUCT = "WebODM Product"


def _require_org_admin() -> str:
    org = tenancy.require_org()
    if not (tenancy.is_org_admin() or tenancy.is_platform_admin()):
        frappe.throw("Only organization admins can install marketplace products", frappe.PermissionError)
    return org


def _signed_in() -> bool:
    return bool(frappe.session.user) and frappe.session.user != "Guest"


def viewer_context(product: dict | None = None) -> dict:
    """What the caller may do: signed-in state, org and (per product) entitlement.

    Capability travels with the resource (see ``api/session.py``): the
    frontend never derives install rights from roles on its own.
    """
    ctx = {"signed_in": _signed_in(), "organization": None, "can_install": False}
    if not ctx["signed_in"]:
        return ctx
    org = tenancy.get_current_org()
    ctx["organization"] = org
    ctx["can_install"] = bool(org) and (tenancy.is_org_admin() or tenancy.is_platform_admin())
    if product is not None:
        ent = install_mod.org_entitlements(org).get(product["product_id"])
        latest = product.get("latest_release") or {}
        ctx["entitlement"] = ent and {
            **ent,
            "update_available": bool(latest) and ent["release"] != latest.get("name"),
        }
    return ctx


def _release_download_url(release_name: str) -> str:
    return f"/api/method/webodm_core.api.marketplace.download_release?release={quote(release_name)}"


def _with_download_urls(product: dict) -> dict:
    """Attach ``download_url`` to every release the caller may download."""
    signed_in = _signed_in()
    for rel in product.get("releases") or []:
        if rel["status"] == "Published" and (signed_in or rel["anonymous_download"]):
            rel["download_url"] = _release_download_url(rel["name"])
        else:
            rel["download_url"] = None
    latest = product.get("latest_release")
    if latest:
        latest["download_url"] = (
            _release_download_url(latest["name"]) if signed_in or latest["anonymous_download"] else None
        )
    return product


@frappe.whitelist(allow_guest=True, methods=["GET"])
def list_products(q: str | None = None, category: str | None = None, kind: str | None = None):
    """Published products (card data) plus the category list and viewer context."""
    products = catalog.list_products(q=q or None, category=category or None, kind=kind or None)
    return {
        "products": products,
        "categories": catalog.list_categories(),
        "viewer": viewer_context(),
    }


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_product(product: str):
    data = catalog.get_product(product)
    if data is None:
        frappe.throw(f"Unknown product: {product}", frappe.DoesNotExistError)
    data = _with_download_urls(data)
    data["viewer"] = viewer_context(data)
    return data


@frappe.whitelist(allow_guest=False, methods=["POST"])
def install_product(product: str, release: str | None = None):
    """Install ``product`` (latest or the named Published release) into the caller's organization."""
    org = _require_org_admin()
    return install_mod.install_product(org, product, release or None)


@frappe.whitelist(allow_guest=False, methods=["POST"])
def uninstall_product(product: str):
    org = _require_org_admin()
    return install_mod.uninstall_product(org, product)


@frappe.whitelist(allow_guest=False, methods=["GET"])
def my_entitlements():
    """The caller's organization's active entitlements keyed by product."""
    return install_mod.org_entitlements(tenancy.get_current_org())


def downloadable_release(release_name: str):
    """The release doc if the caller may download its artifact, else raise.

    Guests need the publisher's anonymous-download opt-in; signed-in users may
    download any Published release of a Published product. Unknown, Draft and
    Yanked releases read as unknown so nothing about them is confirmed.
    """
    if not frappe.db.exists(_RELEASE, release_name):
        frappe.throw(f"Unknown release: {release_name}", frappe.DoesNotExistError)
    release = frappe.get_doc(_RELEASE, release_name)
    product = frappe.db.get_value(
        _PRODUCT, release.product, ["status", "publisher", "allow_anonymous_download"], as_dict=True,
    )
    visible = (
        release.status == "Published"
        and product and product.status == "Published"
        and frappe.db.get_value("WebODM Publisher", product.publisher, "status") == "Active"
    )
    if not visible:
        frappe.throw(f"Unknown release: {release_name}", frappe.DoesNotExistError)
    if not _signed_in() and not product.allow_anonymous_download:
        frappe.throw("Sign in to download this artifact", frappe.PermissionError)
    return release


@frappe.whitelist(allow_guest=True, methods=["GET"])
def download_release(release: str):
    """Stream a release artifact as an attachment (range requests supported)."""
    doc = downloadable_release(release)
    file_doc = files_mod.file_doc_for_url(
        doc.artifact, attached_to_doctype=_RELEASE, attached_to_name=doc.name,
    )
    path = files_mod.abs_path_for_file_doc(file_doc)
    if not os.path.exists(path):
        frappe.throw("Artifact is not available", frappe.DoesNotExistError)

    ext = os.path.splitext(path)[1]
    # A werkzeug Response is passed through untouched by frappe.handler, so the
    # zip streams from disk instead of being read into `response.filecontent`.
    return werkzeug.utils.send_file(
        path,
        environ=frappe.local.request.environ,
        as_attachment=True,
        download_name=f"{doc.product}-{doc.version}{ext}",
        conditional=True,
        max_age=0,
    )
