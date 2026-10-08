"""RQ job that executes a ``WebODM Plugin Run``.

Flow: mark Running -> resolve the task's input files to absolute paths -> call
the analysis backend -> persist the returned artifact as a private File -> mark
Completed (or Failed). A cancellation that lands while the operation is running
is honored by discarding the output. User plugins may publish intermediate
progress, which is written to the run row as it arrives.

A comparison run (``run.reference_task`` set) reads some of its inputs from a
second task of the same project; which ones is recorded on the run when it is
queued. Nothing else differs: the backend still receives named input files.

The backend depends on the plugin type: System plugins run in the geospatial
service (``geospatial.run_operation``), User plugins in the sandboxed plugin
runner (``sandbox.run_user_plugin``). Both return the same result shape, so
everything after the call is shared.
"""

import json
import os

import frappe
from frappe.utils import get_site_path, now_datetime

from webodm_core.plugins.files import save_private_file_from_path
from webodm_core.plugins.geospatial import GeospatialError, run_operation
from webodm_core.plugins.sandbox import run_user_plugin
from webodm_core.storage import assets as storage_assets
from webodm_core.storage import cache
from webodm_core.webodm_core.processing import raster_metadata

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


def _task_facts(task) -> dict:
    """Read-only facts about one task a user plugin may want (CRS, ODM options)."""
    options = task.get("processing_options")
    for _ in range(3):
        if isinstance(options, str):
            try:
                options = frappe.parse_json(options)
            except Exception:
                break
        else:
            break
    try:
        rasters = raster_metadata.rows_for_task(task)
    except Exception:
        rasters = {}
    return {
        "name": task.name,
        "title": task.get("title"),
        "epsg": task.get("epsg"),
        "wkt": task.get("wkt"),
        "resolution": task.get("resolution"),
        "processing_options": options if isinstance(options, (list, dict)) else [],
        # Normalized header metadata per raster the task has (orthophoto /
        # dsm / dtm): size, bands, dtype, CRS, pixel size, bounds, nodata,
        # tiling, overviews. Same shape as api.task.get_raster_metadata.
        "rasters": rasters,
    }


def task_context(task, reference_task=None, inputs: dict | None = None,
                 reference_inputs=()) -> dict:
    """What a user plugin is told about its run besides the input files.

    ``task`` is the task the run belongs to; ``reference_task`` (same shape)
    is present only for a comparison run. ``inputs`` says, per staged input,
    which dataset feeds it and which of the two surveys it comes from — an
    input file alone does not reveal whether it is the DSM or the DTM.
    """
    context = {"task": _task_facts(task)}
    if reference_task is not None:
        context["reference_task"] = _task_facts(reference_task)
    if inputs:
        context["inputs"] = {
            name: {"dataset": dataset,
                   "survey": "reference" if name in reference_inputs else "primary"}
            for name, dataset in inputs.items()
        }
    return context


def _reference_task(run, task):
    """The run's second survey, re-checked against the run's own task.

    ``api.plugins.run_plugin`` validated the pair when the run was queued;
    the worker runs without a session, so the two rules that keep another
    organization's data out are enforced again here on the stored records.
    """
    if not run.get("reference_task"):
        return None
    if not frappe.db.exists("WebODM Task", run.reference_task):
        raise GeospatialError("The comparison survey no longer exists")
    reference = frappe.get_doc("WebODM Task", run.reference_task)
    if reference.organization != run.organization or reference.organization != task.organization:
        raise GeospatialError("The comparison survey belongs to another organization")
    if reference.project != task.project:
        raise GeospatialError("The comparison survey is no longer in the same project")
    return reference


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
        reference = _reference_task(run, task)
        reference_inputs = set(payload.get("reference_inputs") or []) if reference else set()

        # Inputs are staged from the serving cache, re-materialised from object
        # storage when evicted: both the geospatial service (system plugins,
        # shared volume) and the plugin sandbox (user plugins, copied in, no
        # network egress of its own) need a real local file.
        inputs = {}
        for name, dataset in datasets.items():
            source = reference if name in reference_inputs else task
            owner = "The comparison survey" if source is reference else "Task"
            file_url = source.get(dataset)
            if not file_url:
                raise GeospatialError(
                    f"{owner} no longer has '{dataset}' for input '{name}'"
                )
            try:
                inputs[name] = storage_assets.ensure_asset_local(source, dataset)
            except cache.CacheMiss as e:
                raise GeospatialError(f"Input '{name}' ({dataset}) is not available: {e}") from e

        ext = _OUTPUT_EXT.get(plugin.output_kind, "dat")
        out_dir = os.path.abspath(get_site_path("private", "files", "plugin_runs"))
        os.makedirs(out_dir, exist_ok=True)
        tmp_path = os.path.join(out_dir, f"{run.name}.{ext}")

        if plugin.plugin_type == "User":
            result = run_user_plugin(
                plugin, inputs, params, tmp_path,
                timeout=int(plugin.timeout_seconds or 300),
                context=task_context(task, reference, datasets, reference_inputs),
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

        # Canonical copy in object storage (best-effort; the periodic backfill
        # retries if storage is down right now).
        storage_assets.store_plugin_output(run, file_doc)

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
