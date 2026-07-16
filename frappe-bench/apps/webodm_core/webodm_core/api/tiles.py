"""Tile proxy: resolves a task's raster to an absolute path and proxies tile /
info requests to the geospatial FastAPI service.

Keeping the proxy in Frappe means tile URLs are same-origin and session-authed
(Leaflet <img> requests carry the session cookie), and the geospatial service
never needs to know about Frappe's File storage or permissions.
"""

import frappe
import requests

# Dataset name -> (Task field holding the raster, tile render "kind").
_DATASETS = {
    "orthophoto": ("orthophoto", "orthophoto"),
    "dsm": ("dsm", "dsm"),
    "dtm": ("dtm", "dtm"),
}


def _geospatial_url() -> str:
    return (
        frappe.conf.get("geospatial_url")
        or frappe.conf.get("webodm_geospatial_url")
        or "http://127.0.0.1:5000"
    )


def _resolve_raster_path(task_name: str, dataset: str) -> str:
    if dataset not in _DATASETS:
        frappe.throw(f"Unknown dataset: {dataset}")

    field, _kind = _DATASETS[dataset]
    # get_doc enforces the task's read permission for the session user.
    task = frappe.get_doc("WebODM Task", task_name)
    file_url = task.get(field)
    if not file_url:
        frappe.throw(f"Task has no {dataset}", frappe.DoesNotExistError)

    file_doc = frappe.get_doc("File", {"file_url": file_url}, ignore_permissions=True)
    import os
    from frappe.utils import get_bench_path

    p = file_doc.get_full_path()
    if not os.path.isabs(p):
        p = os.path.normpath(os.path.join(get_bench_path(), "sites", p.lstrip("./")))
    return p


@frappe.whitelist(allow_guest=False)
def info(task_name: str, dataset: str = "orthophoto"):
    """Return tiling info (bounds, zoom range, band stats) for a task raster."""
    path = _resolve_raster_path(task_name, dataset)
    try:
        resp = requests.get(
            f"{_geospatial_url().rstrip('/')}/tiles/info",
            params={"path": path},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        frappe.throw(f"Geospatial service unavailable: {e}")


# 1x1 transparent PNG, served when the geospatial service is unreachable so the
# map degrades to empty tiles instead of a wall of broken-image / error responses.
_EMPTY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c626001000000050001a5f645400000000049454e44ae426082"
)


def _png_response(content: bytes):
    # Return a real image/png (not Frappe's octet-stream "binary" type) so the
    # browser treats it as an inline map tile.
    from werkzeug.wrappers import Response

    return Response(
        content,
        mimetype="image/png",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@frappe.whitelist(allow_guest=False)
def serve(task_name: str, dataset: str, z: int, x: int, y: int):
    """Proxy a single XYZ tile PNG from the geospatial service.

    A tile request that fails (service down, transient error) returns a
    transparent PNG rather than an HTTP error, so a single bad tile never breaks
    the map view — the layer just shows blank where tiles are missing.
    """
    path = _resolve_raster_path(task_name, dataset)
    _field, kind = _DATASETS[dataset]
    try:
        resp = requests.get(
            f"{_geospatial_url().rstrip('/')}/tiles/tile/{int(z)}/{int(x)}/{int(y)}.png",
            params={"path": path, "kind": kind},
            timeout=30,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        frappe.log_error(f"tile fetch failed {dataset} {z}/{x}/{y}: {e}", "WebODM Tiles")
        return _png_response(_EMPTY_PNG)

    return _png_response(resp.content)
