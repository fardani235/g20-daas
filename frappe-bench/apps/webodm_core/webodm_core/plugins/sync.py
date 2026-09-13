"""Catalog synchronization.

The geospatial service is the source of truth for which analysis operations
exist; Frappe persists them as ``WebODM Plugin`` catalog rows. Sync is additive
and non-destructive: new operations are created platform-enabled, operations
that disappear upstream are marked unavailable (never deleted, so run history
survives), and a failure to reach the service leaves the previous catalog
intact.
"""

import json

import frappe

from webodm_core import tenancy
from webodm_core.plugins.geospatial import GeospatialUnavailable, fetch_catalog

_CATALOG_FIELDS = (
    "label",
    "version",
    "description",
    "params_schema",
    "output_kind",
    "render_kind",
    "inputs",
)


def _as_json_text(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _catalog_values(op: dict) -> dict:
    return {
        "label": op.get("label") or op.get("op_id"),
        "version": op.get("version"),
        "description": op.get("description"),
        "params_schema": _as_json_text(op.get("params_schema")),
        "output_kind": op.get("output_kind") or "raster",
        "render_kind": op.get("render_kind"),
        "inputs": _as_json_text(op.get("inputs")),
        "available": 1,
    }


def upsert_catalog(operations: list[dict]) -> dict:
    """Create/refresh catalog rows for ``operations`` and hide missing ones.

    ``platform_enabled`` is never touched here, so an admin's kill-switch
    choice survives a sync. Existing rows are updated in place (no duplicates).
    """
    seen: set[str] = set()

    for op in operations:
        op_id = op.get("op_id")
        if not op_id:
            continue
        seen.add(op_id)
        values = _catalog_values(op)

        if frappe.db.exists("WebODM Plugin", op_id):
            doc = frappe.get_doc("WebODM Plugin", op_id)
            for field, value in values.items():
                doc.set(field, value)
            doc.save(ignore_permissions=True)
        else:
            doc = frappe.get_doc({
                "doctype": "WebODM Plugin",
                "plugin_id": op_id,
                "platform_enabled": 1,
                **values,
            })
            doc.insert(ignore_permissions=True)

    # Mark operations that vanished upstream as unavailable without deleting
    # them, so their runs and outputs remain queryable.
    unavailable = []
    for name in frappe.get_all("WebODM Plugin", pluck="name"):
        if name not in seen:
            frappe.db.set_value("WebODM Plugin", name, "available", 0)
            unavailable.append(name)

    frappe.db.commit()
    return {"synced": sorted(seen), "unavailable": sorted(unavailable)}


def sync_catalog() -> dict:
    """Fetch the live catalog and persist it. Raises if the service is down."""
    return upsert_catalog(fetch_catalog())


def sync_catalog_safe() -> dict:
    """Scheduler-friendly sync: never raises, leaves the catalog intact on error."""
    try:
        result = sync_catalog()
        result["ok"] = True
        return result
    except GeospatialUnavailable as e:
        frappe.log_error(message=str(e), title="WebODM plugin catalog sync failed")
        return {"ok": False, "error": str(e)}


@frappe.whitelist()
def sync_now() -> dict:
    """Manual catalog sync trigger (platform admins only)."""
    if not tenancy.is_platform_admin():
        frappe.throw("Only platform admins can sync the plugin catalog", frappe.PermissionError)
    return sync_catalog_safe()
