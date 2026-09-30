import frappe

from webodm_core import datasets

# EXIF extraction moved to webodm_core.datasets when images became dataset
# rows; the old names stay importable from here.
_gps_to_decimal = datasets.gps_to_decimal
_extract_photo_meta = datasets.extract_photo_meta


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
    if task.status not in ("Pending", "Failed", "Cancelled", "Completed"):
        frappe.throw(f"Task {task_name} cannot be started (status: {task.status})")

    # Pending -> Queued is the explicit user handoff. The scheduler sweep only
    # picks up Queued, so this assignment is what actually starts the task;
    # without it the task sits parked forever. A Failed / Cancelled / Completed
    # task can be (re)started: counters reset so it gets a full set of dispatch
    # attempts again, and any previous compute instance link is dropped (a new
    # one is provisioned if needed). Re-processing reads the inputs from the
    # cache when warm and from object storage otherwise.
    #
    # Previous outputs are wiped first. The output relay resumes across polls
    # by treating "asset row with key + field set" as already collected, so
    # leftovers from an earlier run would make it skip the new results and
    # report success while still serving the old ones.
    from webodm_core.storage import assets as storage_assets
    storage_assets.reset_outputs(task)

    task.db_set({
        "status": "Queued", "progress": 1, "node_task_id": None, "compute_instance": None,
        "dispatch_attempts": 0, "poll_failures": 0, "next_attempt_at": None, "last_error": None,
    })

    from webodm_core.webodm_core.processing.task_runner import enqueue_process
    enqueue_process(task_name)

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
    # Queued and Provisioning are cancellable too: they are the window between
    # the user pressing Start and the node accepting the task, and would
    # otherwise be the states the user cannot back out of.
    if task.status not in ("Pending", "Queued", "Provisioning", "Running"):
        frappe.throw(f"Task {task_name} cannot be cancelled (status: {task.status})")

    from webodm_core.webodm_core.processing import task_runner

    node_task_id = task.node_task_id
    if node_task_id:
        node = task_runner._node_for_task(task)
        if node:
            client = task_runner._client_for(node)
            # Fail loud: if the node doesn't acknowledge the cancel, do NOT mark the
            # task Cancelled — otherwise the UI claims "Cancelled" while ODM keeps
            # running. Surface the error and leave the task in its current state.
            try:
                client.task_cancel(node_task_id)
            except Exception as e:
                frappe.log_error(f"Cancel failed for {task_name}: {e}", "WebODM Processing")
                frappe.throw(f"Could not cancel task on the processing node: {e}")

    # Terminal state: releases any on-demand node (best-effort, sweep retries).
    task_runner._finish(task, "Cancelled", progress=0)
    return f"Task {task_name} cancelled"


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
        from webodm_core.webodm_core.processing.task_runner import enqueue_process
        enqueue_process(task_name)
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


def _task_payload(task) -> dict:
    """``task.as_dict()`` plus the dataset summary and its image rows.

    Tasks no longer carry image rows, but the map view still needs thumbnails
    and GPS markers per task, so the payload keeps an ``images`` list (now the
    dataset's rows, each with a ``thumbnail`` URL) next to a ``dataset_summary``
    block. A task whose dataset is gone (only possible for rows written before
    the field became required) gets an empty list.
    """
    out = task.as_dict()
    dataset = frappe.get_doc(datasets.DOCTYPE, task.dataset) if task.dataset and frappe.db.exists(
        datasets.DOCTYPE, task.dataset) else None
    out["dataset_summary"] = datasets.summary(dataset) if dataset else None
    out["images"] = datasets.image_dicts(dataset) if dataset else []
    return out


@frappe.whitelist(allow_guest=False)
def list_tasks(project_id=None):
    """The tasks of a project with their dataset summary (title, image count, size).

    Replaces the frontend's direct ``/api/resource`` listing: image counts now
    live on the dataset, and joining them here saves the page one request per
    task. Goes through ``frappe.get_list`` so the org-scoping applies.
    """
    project_id = project_id or frappe.form_dict.get("project_id")
    if not project_id:
        frappe.throw("project_id is required")
    frappe.get_doc("WebODM Project", project_id).check_permission("read")
    tasks = frappe.get_list("WebODM Task", filters={"project": project_id}, fields=["*"],
                            order_by="creation desc", limit_page_length=0)
    names = {t.dataset for t in tasks if t.dataset}
    summaries = {}
    if names:
        for row in frappe.get_all(datasets.DOCTYPE, filters={"name": ["in", list(names)]},
                                  fields=["name", "title", "description", "image_count", "total_size",
                                          "created_by", "owner", "creation", "modified"]):
            summaries[row.name] = datasets.summary(frappe._dict(row))
    for t in tasks:
        t["dataset_summary"] = summaries.get(t.dataset)
    return tasks


@frappe.whitelist(allow_guest=False)
def upload_images():
    """Create a task, either over an existing dataset or from freshly uploaded images.

    Two modes, decided by the multipart form:

    * ``dataset=<name>`` — the task points at that dataset (must belong to the
      caller's organization); no files are needed and none are accepted.
    * ``files`` — a new dataset is created from the uploads first (title from
      ``dataset_title`` or the task title) and the task points at it. This is
      the pre-library behaviour with the images moved one level out, so the
      same photos can feed another task later without re-uploading.

    Either way the response is the task payload including ``dataset_summary``
    and ``images``. The endpoint keeps its historical name; it is the single
    task-creation entry point.
    """
    from webodm_core import tenancy
    tenancy.require_org()  # deny-by-default: a user with no org cannot upload

    # Frappe only sets max_content_length for /api/method/upload_file.
    # Our custom endpoint needs an explicit limit for large drone datasets.
    frappe.request.max_content_length = 10 * 1024 * 1024 * 1024  # 10 GB

    files = frappe.request.files.getlist("files")
    project_id = frappe.form_dict.get("project_id")
    options_raw = frappe.form_dict.get("options")
    dataset_name = frappe.form_dict.get("dataset")

    if not project_id:
        frappe.throw("project_id is required")
    if not files and not dataset_name:
        frappe.throw("Choose a dataset or provide files to upload")
    if files and dataset_name:
        frappe.throw("Provide either a dataset or files, not both")

    # Gate write access: without this, any user could upload images into another
    # user's project by supplying its id (get_doc alone enforces nothing).
    project = frappe.get_doc("WebODM Project", project_id)
    project.check_permission("write")
    task_count = frappe.db.count("WebODM Task", {"project": project_id})
    title = (frappe.form_dict.get("title") or "").strip() or f"{project.title} - Task {task_count + 1}"

    if dataset_name:
        # Read is enough to build on a dataset; the task controller re-checks
        # that it belongs to the same organization.
        dataset = frappe.get_doc(datasets.DOCTYPE, dataset_name)
        dataset.check_permission("read")
    else:
        dataset_title = (frappe.form_dict.get("dataset_title") or "").strip() or title
        dataset = datasets.create_from_uploads(files, title=dataset_title,
                                               description=frappe.form_dict.get("dataset_description"))

    task = frappe.get_doc({
        "doctype": "WebODM Task",
        "project": project_id,
        "title": title,
        "status": "Pending",
        "dataset": dataset.name,
    })

    encoded = _encode_processing_options(options_raw)
    if encoded is not None:
        task.processing_options = encoded

    try:
        task.save()
    except Exception:
        if not dataset_name:
            # The dataset was created for this task only; do not leave it behind.
            frappe.delete_doc(datasets.DOCTYPE, dataset.name, ignore_permissions=True, force=True,
                              delete_permanently=True)
        raise
    frappe.db.commit()

    _maybe_autostart(task.name)

    return _task_payload(task)


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

    from webodm_core.webodm_core.processing import task_runner
    from webodm_core.webodm_core.processing.node_client import NodeODMError

    node = task_runner._node_for_task(task)
    if not node:
        return result

    try:
        client = task_runner._client_for(node)
        lines = client.task_output(node_task_id, line)
        if isinstance(lines, list):
            result["lines"] = lines
            result["next_line"] = line + len(lines)
    except NodeODMError as e:
        # Console is best-effort; the caller keeps its offset and retries.
        result["error"] = str(e)

    return result


@frappe.whitelist(allow_guest=False)
def get_task_progress():
    """Return the task as the backend knows it.

    Read-only by design. The background ``poll_task`` job is the single writer
    of processing state; an earlier version of this endpoint also talked to the
    node and wrote status/progress inline, which raced with the worker and let
    a UI refresh flip a task's state. If the task is Running, a poll is nudged
    (deduplicated) so a watched task refreshes faster than the cron cadence.
    """
    raw = frappe.request.data
    if isinstance(raw, bytes):
        raw = raw.decode()
    data = frappe.parse_json(raw) if raw else frappe.form_dict
    task_name = data.get("task_name")
    if not task_name:
        frappe.throw("task_name is required")

    task = _get_task_checked(task_name, "read")
    if task.status == "Running":
        from webodm_core.webodm_core.processing.task_runner import enqueue_poll
        try:
            enqueue_poll(task_name)
        except Exception:
            frappe.log_error(f"Could not enqueue poll for {task_name}", "WebODM Processing")
    elif task.status == "Provisioning":
        # Same idea for a task waiting on a node: nudge the readiness check.
        from webodm_core.webodm_core.processing.compute import enqueue_provision_check
        try:
            enqueue_provision_check(task_name)
        except Exception:
            frappe.log_error(f"Could not enqueue provision check for {task_name}", "WebODM Processing")
    return _task_payload(task)


@frappe.whitelist(allow_guest=False)
def get_raster_metadata(task_name=None, dataset=None, refresh=0):
    """Normalized header metadata of a task's rasters (orthophoto / dsm / dtm).

    With ``dataset`` returns that raster's metadata dict (or ``None`` if the
    task has no such raster); without it returns ``{dataset: dict | None}`` for
    all three. The values come from the ``WebODM Raster Metadata`` rows filled
    when the outputs landed. A row that is missing or describes a different
    file than the task currently holds is (re-)extracted on the spot;
    ``refresh=1`` forces re-extraction (e.g. after a Failed row). Extraction
    problems never raise here: the returned dict has ``status: "Failed"`` and
    an ``error``.

    The shape is the one plugins see in ``context.task.rasters`` and the map
    / tile code can use for pixel size, bounds, band layout and nodata.
    """
    from frappe.utils import cint

    from webodm_core.webodm_core.processing import raster_metadata as rm

    raw = frappe.request.data if frappe.request else None
    if isinstance(raw, bytes):
        raw = raw.decode()
    data = frappe.parse_json(raw) if raw else frappe.form_dict
    task_name = task_name or data.get("task_name")
    dataset = dataset or data.get("dataset")
    refresh = cint(refresh if refresh else data.get("refresh"))
    if not task_name:
        frappe.throw("task_name is required")
    if dataset and dataset not in rm.RASTER_DATASETS:
        frappe.throw(f"Unknown dataset: {dataset}. Expected one of {', '.join(rm.RASTER_DATASETS)}.")

    task = _get_task_checked(task_name, "read")

    out = {}
    for ds in ([dataset] if dataset else rm.RASTER_DATASETS):
        file_url = task.get(ds)
        if not file_url:
            out[ds] = None
            continue
        row = rm.find_row(task.name, ds)
        stale = row is None or row.file_url != file_url
        if refresh or stale:
            # Reads are cheap (header only), but this is a synchronous call into
            # the geospatial service; only do it when the stored row cannot be
            # trusted, so a Failed row does not re-run on every viewer poll.
            row = rm.capture(task, ds, file_url) or row
        out[ds] = rm.row_to_dict(row, task) if row else None

    return out[dataset] if dataset else out
