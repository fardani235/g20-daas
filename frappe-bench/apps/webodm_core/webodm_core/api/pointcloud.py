"""Point cloud viewer API: the on-demand Potree octree of a task.

The octree files themselves are ordinary private files attached to the task
and are served by Frappe's ``/private/files/<name>`` route (session cookie,
org permission via the task's ``has_permission``, ``Range`` requests answered
with ``206 Partial Content`` by werkzeug's conditional ``send_file``). This
module only exposes the conversion state and starts the conversion.
"""

from __future__ import annotations

import frappe
from frappe.utils import cint

from webodm_core.webodm_core.processing import potree


def _get_task_checked(task_name: str, ptype: str = "read"):
    task = frappe.get_doc("WebODM Task", task_name)
    task.check_permission(ptype)
    return task


@frappe.whitelist(allow_guest=False, methods=["GET", "POST"])
def potree_state(task_name: str | None = None, start=0, retry=0):
    """State of a task's viewer octree, optionally starting the conversion.

    ``start=1`` (what the viewer sends when the point cloud is opened) kicks
    off the conversion when there is none yet — idempotent across concurrent
    callers — and ``retry=1`` restarts a Failed one. Returns ``status``
    (``""`` | Queued | Running | Ready | Failed), ``error``, ``summary``
    (points, bounds, spacing, projection, attribute ranges), ``files``
    (name -> private URL, when Ready), ``cache`` (``warm`` | ``warming``
    when the host copy is being refetched from object storage), the
    ``point_cloud`` URL and ``has_dsm`` (volume measurement needs a DSM).
    """
    if not task_name:
        frappe.throw("task_name is required")
    task = _get_task_checked(task_name, "read")
    if cint(start) or cint(retry):
        return potree.ensure(task, retry=bool(cint(retry)))
    return potree.state(task)
