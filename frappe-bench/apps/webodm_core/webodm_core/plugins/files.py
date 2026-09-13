"""Shared helpers for resolving Frappe File URLs to absolute on-disk paths.

The geospatial service reads/writes the shared sites volume directly and needs
absolute paths, while ``File.get_full_path()`` can return a bench-relative (or,
under the test runner, unexpectedly rooted) path depending on storage and CWD.
Centralised so tiles, the run worker, and the plugins API resolve files
identically.
"""

import os

import frappe
from frappe.utils import get_bench_path


def abs_path_for_file_url(file_url: str) -> str:
    file_doc = frappe.get_doc("File", {"file_url": file_url}, ignore_permissions=True)
    path = file_doc.get_full_path()
    if path and os.path.exists(path):
        return os.path.abspath(path)

    # Reconstruct from the site path, which is reliable across storage backends
    # and test CWDs.
    name = file_doc.file_name or os.path.basename(file_url)
    subdir = "private" if file_doc.is_private else "public"
    candidate = os.path.join(frappe.get_site_path(subdir, "files"), name)
    if os.path.exists(candidate):
        return os.path.abspath(candidate)

    if os.path.isabs(path):
        return path
    return os.path.abspath(os.path.normpath(os.path.join(get_bench_path(), "sites", path.lstrip("./"))))
