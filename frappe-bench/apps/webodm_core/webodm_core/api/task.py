import io
import os

import frappe
from PIL import Image
from PIL.ExifTags import GPSTAGS

from webodm_core.plugins.files import abs_path_for_file_doc, save_private_file_from_stream


def _get_task_checked(task_name: str, ptype: str = "read"):
    """Load a WebODM Task and enforce the session user's permission on it.

    ``frappe.get_doc`` does NOT check permissions on its own, so every custom
    endpoint must gate access explicitly — otherwise any authenticated user can
    read/act on another user's task just by knowing its id (the owner-scoping in
    permissions.py only auto-applies to list queries, not direct get_doc).
    Raises ``frappe.PermissionError`` if the user is not the owner (or admin).
    """
    task = frappe.get_doc("WebODM Task", task_name)
    task.check_permission(ptype)
    return task


@frappe.whitelist(allow_guest=False)
def process_task():
    raw = frappe.request.data
    if isinstance(raw, bytes):
        raw = raw.decode()
    data = frappe.parse_json(raw) if raw else frappe.form_dict
    task_name = data.get("task_name")
    if not task_name:
        frappe.throw("task_name is required")

    task = _get_task_checked(task_name, "write")
    if task.status != "Pending":
        frappe.throw(f"Task {task_name} is not in Pending state")

    # Pending -> Queued is the explicit user handoff. The scheduler sweep only
    # picks up Queued, so this assignment is what actually starts the task;
    # without it the task sits parked forever.
    task.db_set("status", "Queued")
    task.db_set("progress", 1)

    frappe.enqueue(
        "webodm_core.webodm_core.processing.task_runner.process_task",
        queue="long",
        job_name=f"process_{task_name}",
        task_name=task_name,
    )

    return f"Processing started for {task_name}"


@frappe.whitelist(allow_guest=False)
def cancel_task():
    raw = frappe.request.data
    if isinstance(raw, bytes):
        raw = raw.decode()
    data = frappe.parse_json(raw) if raw else frappe.form_dict
    task_name = data.get("task_name")
    if not task_name:
        frappe.throw("task_name is required")

    task = _get_task_checked(task_name, "write")
    # Queued is cancellable too: it is the window between the user pressing
    # Start and the node accepting the task, and it would otherwise be the one
    # state the user cannot back out of.
    if task.status not in ("Pending", "Queued", "Running"):
        frappe.throw(f"Task {task_name} cannot be cancelled (status: {task.status})")

    node_task_id = task.node_task_id
    if node_task_id:
        from webodm_core.webodm_core.processing.task_runner import _node_client
        client = _node_client()
        if client is not None:
            # Fail loud: if the node doesn't acknowledge the cancel, do NOT mark the
            # task Cancelled — otherwise the UI claims "Cancelled" while ODM keeps
            # running. Surface the error and leave the task in its current state.
            try:
                client.task_cancel(node_task_id)
            except Exception as e:
                frappe.log_error(f"Cancel failed for {task_name}: {e}", "WebODM Processing")
                frappe.throw(f"Could not cancel task on the processing node: {e}")

    task.db_set("status", "Cancelled")
    task.db_set("progress", 0)
    return f"Task {task_name} cancelled"


def _gps_to_decimal(dms, ref):
    deg, min_, sec = dms
    decimal = float(deg) + float(min_) / 60 + float(sec) / 3600
    if ref in ("S", "W"):
        decimal = -decimal
    return round(decimal, 6)


def _extract_photo_meta(source):
    """Extract georeferencing/timing metadata from an image's EXIF.

    ``source`` is raw ``bytes`` or an on-disk path. Pillow reads only the
    headers it needs for ``getexif()``, so passing a path never loads the
    full image into memory.

    Returns a dict with keys ``lat``, ``lng``, ``altitude`` (metres, signed),
    and ``capture_time`` (Frappe ``YYYY-MM-DD HH:MM:SS`` string). Every field is
    independently optional: a missing or malformed tag yields ``None`` and never
    raises, so a bad tag can never block an upload.
    """
    meta = {"lat": None, "lng": None, "altitude": None, "capture_time": None}

    try:
        with Image.open(io.BytesIO(source) if isinstance(source, bytes) else source) as img:
            exif = img.getexif()
    except Exception:
        return meta
    if not exif:
        return meta

    # --- GPS: latitude / longitude / altitude (GPS IFD 34853) ---
    try:
        gps_ifd = exif.get_ifd(34853)
        if gps_ifd:
            gps_info = {}
            for k, v in gps_ifd.items():
                tag = GPSTAGS.get(k)
                if tag:
                    gps_info[tag] = v

            if "GPSLatitude" in gps_info and "GPSLongitude" in gps_info:
                meta["lat"] = _gps_to_decimal(
                    gps_info["GPSLatitude"], gps_info.get("GPSLatitudeRef", "N")
                )
                meta["lng"] = _gps_to_decimal(
                    gps_info["GPSLongitude"], gps_info.get("GPSLongitudeRef", "E")
                )

            if "GPSAltitude" in gps_info:
                try:
                    alt = float(gps_info["GPSAltitude"])
                    ref = gps_info.get("GPSAltitudeRef", 0)
                    # GPSAltitudeRef == 1 (or b"\x01") means below sea level.
                    if ref in (1, b"\x01"):
                        alt = -alt
                    meta["altitude"] = round(alt, 3)
                except (TypeError, ValueError):
                    pass
    except Exception:
        pass

    # --- Capture time: DateTimeOriginal (Exif IFD), then DateTime (base IFD) ---
    try:
        dto = None
        exif_ifd = exif.get_ifd(34665)  # ExifIFD
        if exif_ifd:
            dto = exif_ifd.get(36867)  # DateTimeOriginal
        if not dto:
            dto = exif.get(306)  # DateTime
        if dto:
            # EXIF "YYYY:MM:DD HH:MM:SS" -> Frappe "YYYY-MM-DD HH:MM:SS".
            s = str(dto).strip()
            date_part, _, time_part = s.partition(" ")
            date_part = date_part.replace(":", "-")
            candidate = (date_part + " " + time_part).strip()
            # Only keep a value Frappe's Datetime field can actually store, so a
            # malformed-but-truthy tag can never raise at task.save() and block the
            # whole upload batch. Unparseable -> leave capture_time None.
            from frappe.utils import get_datetime

            get_datetime(candidate)
            meta["capture_time"] = candidate
    except Exception:
        pass

    return meta


def _save_task_image_file(stream, file_name: str, task_name: str):
    """Save an uploaded image as a private File with its bytes untouched.

    ODM georeferencing depends on per-image EXIF GPS. Frappe's ``File.save_file``
    strips EXIF from JPEGs when ``strip_exif_metadata_from_uploaded_images`` is
    on, which removes the geotags and collapses the reconstruction to a tiny
    local model. Streaming the upload straight to disk and registering the File
    against the existing blob bypasses ``save_file`` entirely, so the setting
    cannot affect task images — and the file is never held in memory.
    """
    if hasattr(stream, "seek"):
        try:
            stream.seek(0)
        except (OSError, ValueError):
            pass
    return save_private_file_from_stream(
        stream,
        file_name,
        attached_to_doctype="WebODM Task",
        attached_to_name=task_name,
    )


def _maybe_autostart(task_name: str):
    """Enqueue processing right after upload if WebODM Settings enables it.

    Runs after the upload has committed, so any failure here must not fail the
    upload — the task and its images are already saved and can be started
    manually. Log and degrade instead of raising."""
    try:
        from webodm_core.api import settings as settings_api
        if not settings_api.get().get("auto_start_processing"):
            return
        # Same Pending -> Queued handoff the Start button performs: the sweep in
        # task_runner only picks up Queued, and the enqueued job re-checks the
        # status, so skipping this would make auto-start a silent no-op.
        frappe.db.set_value("WebODM Task", task_name, "status", "Queued")
        frappe.db.commit()
        frappe.enqueue(
            "webodm_core.webodm_core.processing.task_runner.process_task",
            queue="long",
            job_name=f"process_{task_name}",
            task_name=task_name,
        )
    except Exception:
        frappe.log_error(f"Auto-start failed for {task_name}", "WebODM Processing")


def _encode_processing_options(options_raw):
    """Encode the upload dialog's ``options`` payload for Task.processing_options.

    Accepts the raw request value (a JSON string, or already-parsed dict/list).
    Presets/dynamic forms send a NodeODM array ``[{name, value}, ...]``; the
    legacy path sends a dict. Both are stored **double-encoded** as a JSON string
    scalar: on PostgreSQL a top-level array left in a JSON column reloads as a
    Python ``list`` and Frappe's delete snapshot then raises "cannot be a list"
    (same reason presets store options this way). The dispatch read-path already
    ``parse_json``'s a string value back to the list/dict, so no remap is needed.

    Returns the encoded string, or ``None`` if there is nothing valid to store.
    """
    if not options_raw:
        return None
    try:
        opts = frappe.parse_json(options_raw) if isinstance(options_raw, str) else options_raw
    except Exception:
        return None
    if not isinstance(opts, (dict, list)):
        return None
    return frappe.as_json(frappe.as_json(opts))


@frappe.whitelist(allow_guest=False)
def upload_images():
    from webodm_core import tenancy
    tenancy.require_org()  # deny-by-default: a user with no org cannot upload

    # Frappe only sets max_content_length for /api/method/upload_file.
    # Our custom endpoint needs an explicit limit for large drone datasets.
    frappe.request.max_content_length = 10 * 1024 * 1024 * 1024  # 10 GB

    files = frappe.request.files.getlist("files")
    project_id = frappe.form_dict.get("project_id")
    options_raw = frappe.form_dict.get("options")

    if not files:
        frappe.throw("No files provided")
    if not project_id:
        frappe.throw("project_id is required")

    # Gate write access: without this, any user could upload images into another
    # user's project by supplying its id (get_doc alone enforces nothing).
    project = frappe.get_doc("WebODM Project", project_id)
    project.check_permission("write")
    task_count = frappe.db.count("WebODM Task", {"project": project_id})
    task = frappe.get_doc({
        "doctype": "WebODM Task",
        "project": project_id,
        "title": f"{project.title} - Task {task_count + 1}",
        "status": "Pending",
    })

    encoded = _encode_processing_options(options_raw)
    if encoded is not None:
        task.processing_options = encoded

    task.save()

    for f in files:
        file_name = f.filename or f"unnamed_{frappe.generate_hash()[:6]}.jpg"

        # Werkzeug has already spooled large parts to a temp file; stream that
        # to its final location instead of f.read()-ing it into memory.
        file_doc = _save_task_image_file(f.stream, file_name, task.name)

        meta = _extract_photo_meta(abs_path_for_file_doc(file_doc))

        img_row = {
            "image": file_doc.file_url,
            "filename": file_name,
            "file_size": file_doc.file_size,
        }
        if meta["lat"] is not None and meta["lng"] is not None:
            img_row["latitude"] = meta["lat"]
            img_row["longitude"] = meta["lng"]
        if meta["altitude"] is not None:
            img_row["altitude"] = meta["altitude"]
        if meta["capture_time"]:
            img_row["capture_time"] = meta["capture_time"]
        task.append("images", img_row)

    task.save()
    frappe.db.commit()

    _maybe_autostart(task.name)

    return task.as_dict()


@frappe.whitelist(allow_guest=False)
def get_task_console():
    """Return real NodeODM console output for a task, incrementally.

    Accepts ``task_name`` and an optional ``line`` offset (the number of lines the
    caller has already received). Returns the lines from that offset onward plus
    the new offset, so the frontend can poll for just the tail.
    """
    raw = frappe.request.data
    if isinstance(raw, bytes):
        raw = raw.decode()
    data = frappe.parse_json(raw) if raw else frappe.form_dict
    task_name = data.get("task_name")
    if not task_name:
        frappe.throw("task_name is required")

    try:
        line = int(data.get("line") or 0)
    except (TypeError, ValueError):
        line = 0

    task = _get_task_checked(task_name, "read")
    result = {"lines": [], "next_line": line, "status": task.status}

    node_task_id = task.node_task_id
    if not node_task_id:
        # Task has not been dispatched to a processing node yet — no console yet.
        return result

    from webodm_core.webodm_core.processing.task_runner import _node_client

    client = _node_client()
    if client is None:
        return result

    try:
        lines = client.task_output(node_task_id, line)
        if isinstance(lines, list):
            result["lines"] = lines
            result["next_line"] = line + len(lines)
    except Exception as e:
        # Console is best-effort; the task state is authoritative elsewhere.
        frappe.log_error(f"Console fetch failed for {task_name}: {e}", "WebODM Processing")

    return result


@frappe.whitelist(allow_guest=False)
def get_task_progress():
    """Current task state, refreshed from the node if it is Running.

    Delegates to ``task_runner.sync_task_with_node`` -- the same code path the
    scheduler's ``poll_task`` uses -- so there is exactly one state machine.
    If a background poll holds the per-task lock this returns DB state as-is.
    """
    raw = frappe.request.data
    if isinstance(raw, bytes):
        raw = raw.decode()
    data = frappe.parse_json(raw) if raw else frappe.form_dict
    task_name = data.get("task_name")
    if not task_name:
        frappe.throw("task_name is required")

    task = _get_task_checked(task_name, "read")

    if task.status == "Running" and task.node_task_id:
        from webodm_core.webodm_core.processing.task_runner import sync_task_with_node

        try:
            synced = sync_task_with_node(task)
        except Exception:
            frappe.log_error(f"get_task_progress sync failed for {task_name}", "WebODM Processing")
            synced = {}
        task.reload()
        result = task.as_dict()
        for k in ("node_progress", "node_status_code"):
            if k in synced:
                result[k] = synced[k]
        return result

    return task.as_dict()
