import io
import frappe
from PIL import Image
from PIL.ExifTags import GPSTAGS


@frappe.whitelist(allow_guest=False)
def process_task():
    raw = frappe.request.data
    if isinstance(raw, bytes):
        raw = raw.decode()
    data = frappe.parse_json(raw) if raw else frappe.form_dict
    task_name = data.get("task_name")
    if not task_name:
        frappe.throw("task_name is required")

    task = frappe.get_doc("WebODM Task", task_name)
    if task.status != "Pending":
        frappe.throw(f"Task {task_name} is not in Pending state")

    task.db_set("status", "Pending")
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

    task = frappe.get_doc("WebODM Task", task_name)
    if task.status not in ("Pending", "Running"):
        frappe.throw(f"Task {task_name} cannot be cancelled (status: {task.status})")

    opts = task.processing_options
    if isinstance(opts, str):
        opts = frappe.parse_json(opts)
    if isinstance(opts, dict):
        node_task_id = opts.get("_node_task_id")
        if node_task_id:
            from webodm_core.webodm_core.processing.node_client import NodeODMClient
            nodes = frappe.get_all("WebODM Processing Node", fields=["hostname", "port", "token"])
            if nodes:
                client = NodeODMClient(nodes[0]["hostname"], nodes[0]["port"], nodes[0].get("token"))
                try:
                    client.task_cancel(node_task_id)
                except Exception:
                    pass

    task.db_set("status", "Cancelled")
    task.db_set("progress", 0)
    return f"Task {task_name} cancelled"


def _gps_to_decimal(dms, ref):
    deg, min_, sec = dms
    decimal = float(deg) + float(min_) / 60 + float(sec) / 3600
    if ref in ("S", "W"):
        decimal = -decimal
    return round(decimal, 6)


def _extract_photo_meta(content: bytes):
    """Extract georeferencing/timing metadata from an image's EXIF.

    Returns a dict with keys ``lat``, ``lng``, ``altitude`` (metres, signed),
    and ``capture_time`` (Frappe ``YYYY-MM-DD HH:MM:SS`` string). Every field is
    independently optional: a missing or malformed tag yields ``None`` and never
    raises, so a bad tag can never block an upload.
    """
    meta = {"lat": None, "lng": None, "altitude": None, "capture_time": None}

    try:
        img = Image.open(io.BytesIO(content))
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
            meta["capture_time"] = (date_part + " " + time_part).strip()
    except Exception:
        pass

    return meta


def _save_task_image_file(content: bytes, file_name: str, task_name: str):
    """Save an uploaded image as a private File, guaranteeing the on-disk bytes
    are the untouched original.

    ODM georeferencing depends on per-image EXIF GPS. Frappe's File save strips
    EXIF when the ``strip_exif_metadata_from_uploaded_images`` system setting is
    on, which removes the geotags and collapses the reconstruction to a tiny
    local model. We defend against that regardless of the site setting: after
    saving, if the file written to disk differs from the original upload, we
    rewrite the original bytes in place and repair the File's hash/size.
    """
    import os
    from frappe.utils import get_bench_path

    file_doc = frappe.get_doc({
        "doctype": "File",
        "file_name": file_name,
        "is_private": 1,
        "content": content,
        "attached_to_doctype": "WebODM Task",
        "attached_to_name": task_name,
    })
    file_doc.save()

    path = file_doc.get_full_path()
    if not os.path.isabs(path):
        path = os.path.normpath(os.path.join(get_bench_path(), "sites", path.lstrip("./")))

    try:
        with open(path, "rb") as fh:
            on_disk = fh.read()
    except OSError:
        on_disk = content

    if on_disk != content:
        # Frappe (or a hook) altered the bytes — restore the untouched original
        # so ODM receives intact EXIF GPS.
        from frappe.core.doctype.file.utils import get_content_hash

        with open(path, "wb") as fh:
            fh.write(content)
        file_doc.db_set("content_hash", get_content_hash(content), update_modified=False)
        file_doc.db_set("file_size", len(content), update_modified=False)

    return file_doc


@frappe.whitelist(allow_guest=False)
def upload_images():    
    files = frappe.request.files.getlist("files")
    project_id = frappe.form_dict.get("project_id")
    options_raw = frappe.form_dict.get("options")

    if not files:
        frappe.throw("No files provided")
    if not project_id:
        frappe.throw("project_id is required")

    project = frappe.get_doc("WebODM Project", project_id)
    task_count = frappe.db.count("WebODM Task", {"project": project_id})
    task = frappe.get_doc({
        "doctype": "WebODM Task",
        "project": project_id,
        "title": f"{project.title} - Task {task_count + 1}",
        "status": "Pending",
    })

    if options_raw:
        try:
            opts = frappe.parse_json(options_raw)
            if isinstance(opts, dict):
                task.processing_options = frappe.as_json(opts)
        except Exception:
            pass

    task.save()

    for f in files:
        content = f.read()
        file_size = len(content)
        file_name = f.filename or f"unnamed_{frappe.generate_hash()[:6]}.jpg"

        file_doc = _save_task_image_file(content, file_name, task.name)

        meta = _extract_photo_meta(content)

        img_row = {
            "image": file_doc.file_url,
            "filename": file_name,
            "file_size": file_size,
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

    task = frappe.get_doc("WebODM Task", task_name)
    result = {"lines": [], "next_line": line, "status": task.status}

    opts = task.processing_options
    if isinstance(opts, str):
        opts = frappe.parse_json(opts)
    node_task_id = opts.get("_node_task_id") if isinstance(opts, dict) else None
    if not node_task_id:
        # Task has not been dispatched to a processing node yet — no console yet.
        return result

    nodes = frappe.get_all("WebODM Processing Node", fields=["hostname", "port", "token"])
    if not nodes:
        return result

    from webodm_core.webodm_core.processing.node_client import NodeODMClient, NodeODMError

    try:
        client = NodeODMClient(nodes[0]["hostname"], nodes[0]["port"], nodes[0].get("token"))
        lines = client.task_output(node_task_id, line)
        if isinstance(lines, list):
            result["lines"] = lines
            result["next_line"] = line + len(lines)
    except (NodeODMError, Exception):
        pass

    return result


@frappe.whitelist(allow_guest=False)
def get_task_progress():
    raw = frappe.request.data
    if isinstance(raw, bytes):
        raw = raw.decode()
    data = frappe.parse_json(raw) if raw else frappe.form_dict
    task_name = data.get("task_name")
    if not task_name:
        frappe.throw("task_name is required")

    task = frappe.get_doc("WebODM Task", task_name)
    result = task.as_dict()

    opts = task.processing_options
    if isinstance(opts, str):
        opts = frappe.parse_json(opts)
    if isinstance(opts, dict):
        node_task_id = opts.get("_node_task_id")
        if node_task_id:
            from webodm_core.webodm_core.processing.node_client import NodeODMClient
            nodes = frappe.get_all("WebODM Processing Node", fields=["hostname", "port", "token"])
            if nodes:
                try:
                    client = NodeODMClient(nodes[0]["hostname"], nodes[0]["port"], nodes[0].get("token"))
                    info = client.task_info(node_task_id)
                    raw_progress = info.get("progress", 0)
                    raw_status = info.get("status", 0)
                    if isinstance(raw_status, dict):
                        status_code = raw_status.get("code", 0)
                    else:
                        status_code = raw_status

                    result["node_progress"] = raw_progress
                    result["node_status_code"] = status_code

                    # Sync latest progress to Frappe DB
                    if raw_progress > 0 and status_code < 30:
                        pct = max(1, int(raw_progress)) if raw_progress > 1 else max(1, int(raw_progress * 100))
                        if pct != task.progress:
                            task.db_set("progress", pct)
                            task.progress = pct
                            result["progress"] = pct

                    # If node indicates completion, trigger background poll for asset download
                    if status_code >= 40 or status_code == 30:
                        if status_code >= 40:
                            prog_val = raw_progress if isinstance(raw_progress, (int, float)) else 0
                            if prog_val >= 100:
                                task.db_set("progress", 100)
                                result["progress"] = 100
                        frappe.enqueue(
                            "webodm_core.webodm_core.processing.task_runner.poll_task",
                            queue="short",
                            job_name=f"poll_{task_name}",
                            task_name=task_name,
                        )
                except Exception:
                    pass

    return result
