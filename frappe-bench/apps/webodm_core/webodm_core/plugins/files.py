"""Shared helpers for Frappe File <-> on-disk path handling.

Two concerns live here:

1. Resolving a File URL / doc to an absolute on-disk path. The geospatial
   service reads/writes the shared sites volume directly and needs absolute
   paths, while ``File.get_full_path()`` can return a bench-relative (or, under
   the test runner, unexpectedly rooted) path depending on storage and CWD.

2. Registering large blobs as private Files **without buffering them in
   memory**. ``File(content=...)`` requires the whole payload as ``bytes`` and
   Frappe then md5s, optionally re-encodes (EXIF strip) and writes it — three
   copies of a multi-GB orthophoto. ``save_private_file_from_stream`` instead
   streams to the private files dir, hashing as it goes, and registers the
   File with Frappe's ``copy_from_existing_file`` flag so the framework never
   touches the bytes. Side effect worth knowing: Frappe's content-hash dedup
   (which would silently make two tasks share one blob) does not apply.
"""

import hashlib
import os
import re
import shutil

import frappe
from frappe.core.doctype.file.utils import generate_file_name
from frappe.utils import get_bench_path, get_files_path

CHUNK_SIZE = 1024 * 1024

_UNSAFE_FILENAME_CHARS = re.compile(r"[/\\%?#]")


def abs_path_for_file_doc(file_doc) -> str:
    path = file_doc.get_full_path()
    if path and os.path.exists(path):
        return os.path.abspath(path)

    # Reconstruct from the site path, which is reliable across storage backends
    # and test CWDs.
    name = file_doc.file_name or os.path.basename(file_doc.file_url or "")
    subdir = "private" if file_doc.is_private else "public"
    candidate = os.path.join(frappe.get_site_path(subdir, "files"), name)
    if os.path.exists(candidate):
        return os.path.abspath(candidate)

    if os.path.isabs(path):
        return path
    return os.path.abspath(os.path.normpath(os.path.join(get_bench_path(), "sites", path.lstrip("./"))))


def abs_path_for_file_url(file_url: str) -> str:
    file_doc = frappe.get_doc("File", {"file_url": file_url}, ignore_permissions=True)
    return abs_path_for_file_doc(file_doc)


def file_doc_for_url(file_url: str, *, attached_to_doctype: str, attached_to_name: str):
    """The File behind ``file_url``, requiring it to be attached to that document.

    Attach/child-table fields are user-writable and the pipeline resolves them by
    URL with permissions bypassed, so without this a member could point their own
    task at another organization's private file and read it through the tile /
    download / dispatch paths. Every user-settable pointer to a stored blob goes
    through here (or ``abs_path_for_attached_file``).
    """
    file_doc = frappe.get_doc("File", {"file_url": file_url}, ignore_permissions=True)
    if (file_doc.attached_to_doctype != attached_to_doctype
            or file_doc.attached_to_name != attached_to_name):
        raise frappe.PermissionError(
            f"File {file_url} is not attached to {attached_to_doctype} {attached_to_name} "
            f"(attached to {file_doc.attached_to_doctype!r}/{file_doc.attached_to_name!r})"
        )
    return file_doc


def abs_path_for_attached_file(file_url: str, *, attached_to_doctype: str, attached_to_name: str) -> str:
    """``abs_path_for_file_doc`` for a File that must belong to the given document."""
    return abs_path_for_file_doc(
        file_doc_for_url(file_url, attached_to_doctype=attached_to_doctype, attached_to_name=attached_to_name)
    )


def _safe_private_name(file_name: str) -> str:
    # Same normalisation Frappe applies in save_file_on_filesystem, then make
    # the name conflict-free within private/files (random suffix on collision).
    safe = _UNSAFE_FILENAME_CHARS.sub("_", file_name.replace("/", ""))
    return generate_file_name(safe, is_private=True)


def _register_private_file(
    file_name: str,
    file_size: int,
    content_hash: str,
    *,
    attached_to_doctype: str | None,
    attached_to_name: str | None,
    attached_to_field: str | None,
    ignore_permissions: bool,
):
    file_doc = frappe.get_doc({
        "doctype": "File",
        "file_name": file_name,
        "file_url": f"/private/files/{file_name}",
        "is_private": 1,
        "file_size": file_size,
        "content_hash": content_hash,
        "attached_to_doctype": attached_to_doctype,
        "attached_to_name": attached_to_name,
        "attached_to_field": attached_to_field,
    })
    # Tell File.before_insert the blob is already at file_url: skips save_file()
    # (no read-into-memory, no EXIF re-encode, no hash dedup) while keeping
    # validate() -> path-under-private-files + exists-on-disk checks.
    file_doc.flags.copy_from_existing_file = True
    file_doc.insert(ignore_permissions=ignore_permissions)
    return file_doc


def save_private_file_from_stream(
    stream,
    file_name: str,
    *,
    attached_to_doctype: str | None = None,
    attached_to_name: str | None = None,
    attached_to_field: str | None = None,
    ignore_permissions: bool = False,
    chunk_size: int = CHUNK_SIZE,
):
    """Stream ``stream`` (any object with ``read(n)``) into private/files and
    return the registered File doc. Peak memory is one chunk.

    Writes to ``<name>.part`` first and renames on success, so a crash mid-copy
    never leaves a truncated file at the final name. If the File insert fails,
    the on-disk blob is removed again.
    """
    final_name = _safe_private_name(file_name)
    dest = get_files_path(final_name, is_private=True)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"

    md5 = hashlib.md5(usedforsecurity=False)  # nosec - Frappe's content_hash is md5
    size = 0
    try:
        with open(part, "wb") as out:
            while True:
                chunk = stream.read(chunk_size)
                if not chunk:
                    break
                out.write(chunk)
                md5.update(chunk)
                size += len(chunk)
        os.replace(part, dest)
    except Exception:
        _remove_quietly(part)
        raise

    try:
        return _register_private_file(
            final_name, size, md5.hexdigest(),
            attached_to_doctype=attached_to_doctype,
            attached_to_name=attached_to_name,
            attached_to_field=attached_to_field,
            ignore_permissions=ignore_permissions,
        )
    except Exception:
        _remove_quietly(dest)
        raise


def save_private_file_from_path(
    src_path: str,
    file_name: str,
    *,
    attached_to_doctype: str | None = None,
    attached_to_name: str | None = None,
    attached_to_field: str | None = None,
    ignore_permissions: bool = False,
    move: bool = True,
):
    """Register a file that already exists on disk as a private File.

    With ``move=True`` (default) the file is renamed into private/files, so a
    multi-GB output produced on the same volume costs one directory entry
    rather than a copy. Falls back to a copy when a rename is not possible.
    """
    final_name = _safe_private_name(file_name)
    dest = get_files_path(final_name, is_private=True)
    os.makedirs(os.path.dirname(dest), exist_ok=True)

    md5 = hashlib.md5(usedforsecurity=False)  # nosec
    with open(src_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK_SIZE), b""):
            md5.update(chunk)
    size = os.path.getsize(src_path)

    if move:
        try:
            os.replace(src_path, dest)
        except OSError:
            shutil.copyfile(src_path, dest)
            _remove_quietly(src_path)
    else:
        shutil.copyfile(src_path, dest)

    try:
        return _register_private_file(
            final_name, size, md5.hexdigest(),
            attached_to_doctype=attached_to_doctype,
            attached_to_name=attached_to_name,
            attached_to_field=attached_to_field,
            ignore_permissions=ignore_permissions,
        )
    except Exception:
        _remove_quietly(dest)
        raise


def _remove_quietly(path: str):
    try:
        os.remove(path)
    except OSError:
        pass
