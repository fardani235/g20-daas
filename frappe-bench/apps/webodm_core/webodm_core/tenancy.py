# webodm_core/tenancy.py
"""Single source of truth for a request's tenant (organization) context.

Every stamping hook, permission hook, and custom endpoint resolves org context
through THIS module and nowhere else. Deny-by-default: a user with no active
membership resolves to None and must be denied.
"""
import frappe

_PLATFORM_ROLES = {"System Manager", "Administrator"}


class OrgContextError(frappe.PermissionError):
    """Raised when an action needs an organization but the actor has none."""
    http_status_code = 403


def _cache():
    if not hasattr(frappe.local, "webodm_org_cache") or frappe.local.webodm_org_cache is None:
        frappe.local.webodm_org_cache = {}
    return frappe.local.webodm_org_cache


def get_current_org(user=None):
    """Return the user's organization name, or None (no membership / suspended)."""
    user = user or frappe.session.user
    cache = _cache()
    if user in cache:
        return cache[user]

    org = None
    row = frappe.db.get_value("WebODM Org Membership", {"user": user},
                              ["organization"], as_dict=True)
    if row and row.organization:
        status = frappe.db.get_value("WebODM Organization", row.organization, "status")
        if status == "Active":
            org = row.organization

    cache[user] = org
    return org


def require_org(user=None):
    """Return the user's org, or raise OrgContextError (deny-by-default)."""
    org = get_current_org(user)
    if not org:
        raise OrgContextError("no_organization")
    return org


def is_org_admin(user=None):
    user = user or frappe.session.user
    role = frappe.db.get_value("WebODM Org Membership", {"user": user}, "role")
    return role == "Owner"


def is_platform_admin(user=None):
    user = user or frappe.session.user
    return bool(set(frappe.get_roles(user)) & _PLATFORM_ROLES)
