"""Seed the default processing node (NodeODM) for the docker-compose stack.

Idempotent — skips if a node already exists, so manual registrations are
never overwritten.
"""

import frappe

_DOCTYPE = "WebODM Processing Node"

_DEFAULT_NODE = {
    "node_name": "Local NodeODM",
    "hostname": "nodeodm",
    "port": 3000,
}


def execute():
    if frappe.db.exists(_DOCTYPE, _DEFAULT_NODE["node_name"]):
        return
    frappe.get_doc({
        "doctype": _DOCTYPE,
        **_DEFAULT_NODE,
    }).insert(ignore_permissions=True)
    frappe.db.commit()
