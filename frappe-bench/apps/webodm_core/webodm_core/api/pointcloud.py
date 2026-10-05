"""Point cloud viewer API: the on-demand Potree octree of a task, and the
elevation profile computed from it.

The octree files themselves are ordinary private files attached to the task
and are served by Frappe's ``/private/files/<name>`` route (session cookie,
org permission via the task's ``has_permission``, ``Range`` requests answered
with ``206 Partial Content`` by werkzeug's conditional ``send_file``). This
module exposes the conversion state, starts the conversion, and proxies the
profile request to the geospatial service (which reads the octree by byte
range, locally or from object storage) so the browser never reaches that
service and every call is permission-checked through the task.
"""

from __future__ import annotations

import math

import frappe
from frappe.utils import cint, flt

from webodm_core.plugins import geospatial
from webodm_core.webodm_core.processing import potree

PROFILE_MAX_VERTICES = 50
PROFILE_WIDTH = (0.05, 200.0)
PROFILE_MIN_STEP = 0.01
PROFILE_TIMEOUT = 180


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


def _parse_line(line) -> list[list[float]]:
    """``[[x, y], ...]`` (2..50 finite pairs) from a JSON string or list."""
    if isinstance(line, str):
        try:
            line = frappe.parse_json(line)
        except Exception:
            frappe.throw("line must be a JSON list of [x, y] pairs")
    if not isinstance(line, (list, tuple)) or len(line) < 2:
        frappe.throw("line needs at least two vertices")
    if len(line) > PROFILE_MAX_VERTICES:
        frappe.throw(f"line has {len(line)} vertices; at most {PROFILE_MAX_VERTICES} are allowed")
    out = []
    for p in line:
        try:
            x, y = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError, KeyError):
            frappe.throw("line must be a list of [x, y] pairs")
        if not (math.isfinite(x) and math.isfinite(y)):
            frappe.throw("line coordinates must be finite numbers")
        out.append([x, y])
    return out


@frappe.whitelist(allow_guest=False, methods=["POST"])
def profile(task_name: str | None = None, line=None, width=1.0, step=None, ground_window=None):
    """Elevation profile along a transect over the task's point cloud.

    ``line`` is ``[[x, y], ...]`` in the cloud's CRS (the octree's recorded
    projection, i.e. what the viewer picks), ``width`` the corridor width in
    CRS units and ``step`` the bin size (omit for automatic). Requires read
    access to the task and a Ready octree (the viewer builds it on first
    open). The result is the geospatial service's document: per-bin
    ``distance`` / ``ground`` / ``ground_fill`` / ``top`` / ``height`` /
    ``min`` / ``max`` / ``count``, one layer per classification code in
    ``classes``, ``gaps``, ``stats`` and ``sampling``; see
    ``docs/point-cloud/elevation-profile.md``.
    """
    if not task_name:
        frappe.throw("task_name is required")
    task = _get_task_checked(task_name, "read")
    if not task.point_cloud:
        frappe.throw("The task has no point cloud", frappe.DoesNotExistError)
    vertices = _parse_line(line)
    w = flt(width) if width not in (None, "") else 1.0
    if not (PROFILE_WIDTH[0] <= w <= PROFILE_WIDTH[1]):
        frappe.throw(f"width must be between {PROFILE_WIDTH[0]} and {PROFILE_WIDTH[1]}")
    s = None
    if step not in (None, "", 0, "0"):
        s = flt(step)
        if s < PROFILE_MIN_STEP:
            frappe.throw(f"step must be at least {PROFILE_MIN_STEP}")
    gw = None
    if ground_window not in (None, ""):
        gw = max(0.0, flt(ground_window))
    try:
        files = potree.file_sources(task)
    except potree.OctreeNotReady as e:
        frappe.throw(str(e), frappe.ValidationError)
    try:
        return geospatial.pointcloud_profile(files, vertices, width=w, step=s, ground_window=gw,
                                             timeout=PROFILE_TIMEOUT)
    except geospatial.GeospatialUnavailable as e:  # subclass of GeospatialError: must come first
        frappe.throw(f"Geospatial service unavailable: {e}")
    except geospatial.GeospatialError as e:
        frappe.throw(str(e), frappe.ValidationError)
