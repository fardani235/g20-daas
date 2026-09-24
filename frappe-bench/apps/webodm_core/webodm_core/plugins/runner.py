"""RQ job that executes a ``WebODM Plugin Run``.

Flow: mark Running -> resolve the task's input files to absolute paths -> call
the analysis backend -> persist the returned artifact as a private File -> mark
Completed (or Failed). A cancellation that lands while the operation is running
is honored by discarding the output. User plugins may publish intermediate
progress, which is written to the run row as it arrives.

The backend depends on the plugin type: System plugins run in the geospatial
service (``geospatial.run_operation``), User plugins in the sandboxed plugin
runner (``sandbox.run_user_plugin``). Both return the same result shape, so
everything after the call is shared.
"""

import json
import os

import frappe
from frappe.utils import get_site_path, now_datetime

from webodm_core.plugins.files import abs_path_for_file_url as _abs_path_for_file_url
from webodm_core.plugins.files import save_private_file_from_path
from webodm_core.plugins.geospatial import GeospatialError, run_operation
from webodm_core.plugins.sandbox import run_user_plugin

# Task fields that can supply an operation input.
DATASET_FIELDS = ("orthophoto", "dsm", "dtm", "point_cloud", "model")

_OUTPUT_EXT = {"raster": "tif", "vector": "geojson", "model": "glb"}


def _parameters(run) -> dict:
    value = run.parameters
    for _ in range(3):
        if isinstance(value, str):
            try:
                value = frappe.parse_json(value)
            except Exception:
                return {}
        else:
            break
    return value if isinstance(value, dict) else {}


def _as_json(value):
    return json.dumps(value) if value is not None else None


def _remove(path: str):
    try:
        os.remove(path)
    except OSError:
        pass


def task_context(task) -> dict:
    """Read-only facts about the task a user plugin may want (CRS, ODM options,
    per-raster header metadata)."""
    from webodm_core.webodm_core.processing import raster_metadata

    options = task.get("processing_options")
    for _ in range(3):
        if isinstance(options, str):
            try:
                options = frappe.parse_json(options)
            except Exception:
                break
        else:
            break
    # Rows already loaded with the task; failed extractions (error set) are left
    # out so a plugin can treat "present" as "trustworthy".
    rasters = {
        row.dataset: raster_metadata.to_public(row)
        for row in (task.get("raster_metadata") or [])
        if not row.get("error")
    }
    return {
        "task": {
            "name": task.name,
            "title": task.get("title"),
            "epsg": task.get("epsg"),
            "wkt": task.get("wkt"),
            "resolution": task.get("resolution"),
            "processing_options": options if isinstance(options, (list, dict)) else [],
            "raster_metadata": rasters,
        }
    }


def _progress_writer(run):
    """Persist plugin progress as it arrives (own commit: the job's transaction is long-lived)."""
    def on_progress(percent, message):
        run.db_set("progress", percent, update_modified=False)
        run.db_set("progress_message", message or "", update_modified=False)
        frappe.db.commit()
    return on_progress


def execute_run(run_name: str):
    run = frappe.get_doc("WebODM Plugin Run", run_name)
    if run.status != "Queued":
        return

    run.db_set("status", "Running")
    run.db_set("started_at", now_datetime())

    tmp_path = None
    try:
        plugin = frappe.get_doc("WebODM Plugin", run.plugin)
        task = frappe.get_doc("WebODM Task", run.task)
        payload = _parameters(run)
        params = payload.get("params", {})
        datasets = payload.get("inputs", {})

        inputs = {}
        for name, dataset in datasets.items():
            file_url = task.get(dataset)
            if not file_url:
                raise GeospatialError(
                    f"Task no longer has '{dataset}' for input '{name}'"
                )
            inputs[name] = _abs_path_for_file_url(file_url)

        ext = _OUTPUT_EXT.get(plugin.output_kind, "dat")
        out_dir = os.path.abspath(get_site_path("private", "files", "plugin_runs"))
        os.makedirs(out_dir, exist_ok=True)
        tmp_path = os.path.join(out_dir, f"{run.name}.{ext}")

        if plugin.plugin_type == "User":
            result = run_user_plugin(
                plugin, inputs, params, tmp_path,
                timeout=int(plugin.timeout_seconds or 300),
                context=task_context(task),
                on_progress=_progress_writer(run),
            )
        else:
            result = run_operation(
                plugin.name, inputs, params, tmp_path,
                timeout=int(plugin.timeout_seconds or 600),
            )

        # A cancel may have landed while the operation was running.
        run.reload()
        if run.status == "Cancelled":
            return

        # The output already lives on the site volume; rename it into
        # private/files rather than reading a multi-GB raster into memory.
        file_doc = save_private_file_from_path(
            tmp_path,
            f"{run.name}_{plugin.name}.{ext}",
            attached_to_doctype="WebODM Plugin Run",
            attached_to_name=run.name,
            ignore_permissions=True,
        )

        metadata = result.get("metadata", {}) or {}
        run.db_set("output_file", file_doc.file_url)
        run.db_set("output_kind", plugin.output_kind)
        run.db_set("render_kind", plugin.render_kind)
        run.db_set("output_extent", _as_json(metadata.get("extent")))
        run.db_set("output_metadata", _as_json(metadata))
        run.db_set("status", "Completed")
        run.db_set("progress", 100)
        run.db_set("progress_message", "")
        run.db_set("completed_at", now_datetime())
    except Exception as e:
        run.reload()
        if run.status == "Cancelled":
            return
        run.db_set("error", str(e))
        run.db_set("status", "Failed")
        run.db_set("completed_at", now_datetime())
        frappe.log_error(message=f"Plugin run {run_name} failed: {e}", title="WebODM Plugin Run")
    finally:
        if tmp_path:
            _remove(tmp_path)
