import frappe
from webodm_core import tenancy

_DOCTYPE = "WebODM Settings"

_ALLOWED = {
    "default_basemap", "enable_public_sharing", "max_file_size_mb",
    "max_project_count", "slack_webhook_url", "email_notifications",
    "default_preset", "auto_start_processing",
}


def _get_or_create_org_settings(org):
    name = frappe.db.get_value(_DOCTYPE, {"organization": org})
    if name:
        return frappe.get_doc(_DOCTYPE, name)
    doc = frappe.get_doc({"doctype": _DOCTYPE, "organization": org})
    doc.insert(ignore_permissions=True)
    return doc


@frappe.whitelist(allow_guest=False)
def get():
    """Return the caller's organization Settings row as a plain dict."""
    org = tenancy.require_org()
    doc = _get_or_create_org_settings(org)
    return {f: doc.get(f) for f in _ALLOWED}


@frappe.whitelist(allow_guest=False)
def save(**fields):
    """Update the caller's org Settings. Only org admins (Owner) may write."""
    org = tenancy.require_org()
    if not (tenancy.is_org_admin() or tenancy.is_platform_admin()):
        frappe.throw("Only organization admins can change settings", frappe.PermissionError)

    try:
        raw = frappe.request.data
    except RuntimeError:
        raw = None
    if raw:
        if isinstance(raw, bytes):
            raw = raw.decode()
        parsed = frappe.parse_json(raw)
        if isinstance(parsed, dict):
            fields = {**fields, **parsed}

    doc = _get_or_create_org_settings(org)
    for k, v in fields.items():
        if k in _ALLOWED:
            doc.set(k, v)
    doc.save(ignore_permissions=True)
    return get()
