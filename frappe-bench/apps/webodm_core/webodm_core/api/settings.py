import frappe

_DOCTYPE = "WebODM Settings"

# Fields the API is allowed to write (mirrors the DocType's editable fields).
_ALLOWED = {
    "default_basemap",
    "enable_public_sharing",
    "max_file_size_mb",
    "max_project_count",
    "slack_webhook_url",
    "email_notifications",
    "default_preset",
    "auto_start_processing",
}


@frappe.whitelist(allow_guest=False)
def get():
    """Return the WebODM Settings single doc as a plain dict."""
    doc = frappe.get_single(_DOCTYPE)
    return {f: doc.get(f) for f in _ALLOWED}


@frappe.whitelist(allow_guest=False)
def save(**fields):
    """Update the allowed fields on the WebODM Settings single doc."""
    try:
        raw = frappe.request.data
    except RuntimeError:
        # In test context, frappe.request is not bound
        raw = None

    if raw:
        if isinstance(raw, bytes):
            raw = raw.decode()
        parsed = frappe.parse_json(raw)
        if isinstance(parsed, dict):
            fields = {**fields, **parsed}

    doc = frappe.get_single(_DOCTYPE)
    for k, v in fields.items():
        if k in _ALLOWED:
            doc.set(k, v)
    doc.save(ignore_permissions=True)
    return get()
