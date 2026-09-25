"""Transparent cache fill for Frappe's ``/private/files/<name>`` route.

The 3D viewer and download links fetch task outputs and plugin models by their
private-file URL, which Frappe serves straight off disk from ``frappe.app``
(no hook point of its own). This ``before_request`` hook runs ahead of that
handler: if the requested blob has been evicted from the serving cache but has
an S3 copy, it is re-materialised first, so the URL keeps working. A cold
fetch is slower (the download happens inside the request) but never wrong.

Only logged-in users trigger a fill, and only for URLs that are registered as
cache-backed rows; Frappe's own permission check on the ``File`` still gates
the actual download afterwards.
"""

from __future__ import annotations

import os

import frappe

from webodm_core import storage
from webodm_core.storage import cache

_PRIVATE_PREFIX = "/private/files/"


def materialize_private_file():
    request = getattr(frappe.local, "request", None)
    if request is None or not storage.configured():
        return
    path = request.path or ""
    if not path.startswith(_PRIVATE_PREFIX) or frappe.session.user in (None, "", "Guest"):
        return
    name = os.path.basename(path)
    if not name or name != path[len(_PRIVATE_PREFIX):]:
        return  # nested paths are not cache entries
    local = frappe.get_site_path("private", "files", name)
    if os.path.isfile(local):
        cache.touch(local)
        return
    found = cache.storage_key_for_file_url(_PRIVATE_PREFIX + name)
    if not found:
        return
    key, org = found
    try:
        cache.fill(key, local, org)
    except storage.StorageError as e:
        # Fall through: Frappe answers 404 for the missing file, the error is logged.
        frappe.log_error(f"cache fill for {path} failed: {e}", "WebODM Storage")
