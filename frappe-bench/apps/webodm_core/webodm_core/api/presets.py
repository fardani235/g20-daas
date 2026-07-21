import frappe

_DOCTYPE = "WebODM Preset"


def _is_admin() -> bool:
    roles = set(frappe.get_roles(frappe.session.user))
    return bool(roles & {"System Manager", "Administrator"})


def _node_client():
    """Build a NodeODMClient for the first configured processing node, or None."""
    from webodm_core.webodm_core.processing.node_client import NodeODMClient
    nodes = frappe.get_all("WebODM Processing Node", fields=["hostname", "port", "token"])
    if not nodes:
        return None
    n = nodes[0]
    return NodeODMClient(n["hostname"], n["port"], n.get("token"))


@frappe.whitelist(allow_guest=False)
def options():
    """Proxy the processing node's GET /options catalog (live)."""
    from webodm_core.webodm_core.processing.node_client import NodeODMError
    client = _node_client()
    if client is None:
        frappe.throw("Processing node offline — can't load options")
    try:
        return client.get_options()
    except NodeODMError:
        frappe.throw("Processing node offline — can't load options")


def _decode_options(value):
    """Recover the [{name, value}] list from a stored ``options`` field.

    ``options`` is persisted as a JSON *string scalar* (see ``_encode_options``),
    so the DB column never holds a top-level array. Depending on the DB backend
    the read value may be that scalar (MariaDB returns the raw string) or already
    one level unwrapped (Postgres auto-parses JSON columns), so parse until we
    reach the list.
    """
    if value is None or value == "":
        return []
    for _ in range(3):
        if isinstance(value, str):
            value = frappe.parse_json(value)
        else:
            break
    return value if isinstance(value, list) else []


def _encode_options(options) -> str:
    """Serialise options for storage as a JSON string scalar.

    The ``options`` field is a JSON DocField holding a top-level array. On
    PostgreSQL the driver auto-parses JSON columns back into Python objects, so a
    stored array reloads as a ``list`` — and Frappe's ``get_valid_dict`` throws
    "Value ... cannot be a list" when the delete flow snapshots the doc into a
    Deleted Document. Encoding the array as a JSON *string scalar* keeps the
    column value a ``str`` on read, which round-trips cleanly on both backends.
    """
    canonical = options if isinstance(options, str) else frappe.as_json(options)
    return frappe.as_json(canonical)


@frappe.whitelist(allow_guest=False)
def list_presets():
    """Presets visible to the session user: their own + system presets."""
    user = frappe.session.user
    rows = frappe.get_all(
        _DOCTYPE,
        filters=[["system", "=", 1]],
        fields=["name", "preset_name", "options", "system", "owner"],
    ) + frappe.get_all(
        _DOCTYPE,
        filters=[["owner", "=", user], ["system", "=", 0]],
        fields=["name", "preset_name", "options", "system", "owner"],
    )
    for r in rows:
        r["options"] = _decode_options(r.get("options"))
    return rows


@frappe.whitelist(allow_guest=False)
def save(preset_name, options, system=0, name=None):
    """Create or update a preset. options is a JSON string of [{name, value}]."""
    system = int(system or 0)
    user = frappe.session.user

    if system and not _is_admin():
        frappe.throw("Only administrators can manage system presets", frappe.PermissionError)

    if name and frappe.db.exists(_DOCTYPE, name):
        doc = frappe.get_doc(_DOCTYPE, name)
        if doc.system and not _is_admin():
            frappe.throw("Only administrators can edit system presets", frappe.PermissionError)
        if not doc.system and doc.owner != user and not _is_admin():
            frappe.throw("You can only edit your own presets", frappe.PermissionError)
        doc.preset_name = preset_name
        doc.options = _encode_options(options)
        doc.system = system
        doc.save(ignore_permissions=True)
    else:
        doc = frappe.get_doc({
            "doctype": _DOCTYPE,
            "preset_name": preset_name,
            "owner": user,
            "system": system,
            "options": _encode_options(options),
        })
        doc.insert(ignore_permissions=True)

    return {"name": doc.name, "preset_name": doc.preset_name}


@frappe.whitelist(allow_guest=False)
def delete(name):
    """Delete a preset the user owns; admins may delete any."""
    if not frappe.db.exists(_DOCTYPE, name):
        return {"ok": True}
    doc = frappe.get_doc(_DOCTYPE, name)
    user = frappe.session.user
    if doc.system and not _is_admin():
        frappe.throw("Only administrators can delete system presets", frappe.PermissionError)
    if not doc.system and doc.owner != user and not _is_admin():
        frappe.throw("You can only delete your own presets", frappe.PermissionError)
    frappe.delete_doc(_DOCTYPE, name, ignore_permissions=True)
    return {"ok": True}
