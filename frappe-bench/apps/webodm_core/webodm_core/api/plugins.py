"""Analysis plugin API: catalog listing and per-organization enablement.

Execution endpoints live alongside these in the same module (``run_plugin`` et
al.). Everything is org-scoped through ``tenancy`` and the platform kill switch
is enforced here as well as in the data model.
"""

import json
import os

import frappe
from frappe.utils import now_datetime, sbool

from webodm_core import tenancy
from webodm_core.plugins import schema as schema_mod
from webodm_core.plugins.geospatial import (
    GeospatialError,
    GeospatialUnavailable,
    validate_operation,
)

_PLUGIN = "WebODM Plugin"
_SETTING = "WebODM Plugin Setting"

# Seconds. Used when an operation does not declare its own timeout; ML ops set
# a longer one so their RQ jobs are not killed mid-inference.
_RUN_DEFAULT_TIMEOUT = 300


def _parse_json(value, default=None):
    """Parse a JSON value that may be a string (Code field) or already decoded."""
    if value in (None, ""):
        return default
    for _ in range(3):
        if isinstance(value, str):
            try:
                value = frappe.parse_json(value)
            except Exception:
                return default
        else:
            break
    return value if value is not None else default


def _load_payload(kwargs: dict) -> dict:
    """Merge kwargs with a JSON request body, if one is present."""
    try:
        raw = frappe.request.data
    except RuntimeError:
        raw = None
    if raw:
        if isinstance(raw, bytes):
            raw = raw.decode()
        try:
            parsed = frappe.parse_json(raw)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            return {**kwargs, **parsed}
    return kwargs


def _plugin_row(plugin: str):
    if not frappe.db.exists(_PLUGIN, plugin):
        frappe.throw(f"Unknown plugin: {plugin}", frappe.DoesNotExistError)
    return frappe.get_doc(_PLUGIN, plugin)


def _get_or_create_setting(org: str, plugin: str):
    name = frappe.db.get_value(_SETTING, {"organization": org, "plugin": plugin}, "name")
    if name:
        return frappe.get_doc(_SETTING, name)
    return frappe.get_doc({"doctype": _SETTING, "organization": org, "plugin": plugin})


@frappe.whitelist(allow_guest=False)
def list_plugins():
    """Return the catalog with the caller's organization enablement merged in."""
    org = tenancy.get_current_org()
    plugins = frappe.get_all(
        _PLUGIN,
        filters={"available": 1},
        fields=[
            "name", "label", "description", "version",
            "output_kind", "render_kind", "inputs", "platform_enabled", "available",
            "params_schema", "models",
        ],
        order_by="label",
        ignore_permissions=True,
    )

    settings_by_plugin = {}
    if org:
        for row in frappe.get_all(
            _SETTING,
            filters={"organization": org},
            fields=["plugin", "enabled", "settings"],
            ignore_permissions=True,
        ):
            settings_by_plugin[row.plugin] = row

    result = []
    for p in plugins:
        setting = settings_by_plugin.get(p.name)
        enabled = bool(setting.enabled) if setting else False
        result.append({
            "name": p.name,
            "op_id": p.name,
            "label": p.label,
            "description": p.description,
            "version": p.version,
            "output_kind": p.output_kind,
            "render_kind": p.render_kind,
            "platform_enabled": bool(p.platform_enabled),
            "available": bool(p.available),
            "enabled": enabled,
            "runnable": bool(p.platform_enabled and p.available and enabled),
            "settings": _parse_json(setting.settings, {}) if setting else {},
            "params_schema": _parse_json(p.params_schema, {}),
            "inputs": _parse_json(p.inputs, []),
            "models": _parse_json(p.models, []),
        })
    return result


@frappe.whitelist(allow_guest=False)
def save_plugin_setting(**kwargs):
    """Enable/disable and/or configure a plugin for the caller's organization."""
    payload = _load_payload(kwargs)
    plugin = payload.get("plugin")
    enabled = payload.get("enabled")
    settings = payload.get("settings")

    if not plugin:
        frappe.throw("plugin is required")

    org = tenancy.require_org()
    if not (tenancy.is_org_admin() or tenancy.is_platform_admin()):
        frappe.throw("Only organization admins can change plugin settings", frappe.PermissionError)

    plugin_doc = _plugin_row(plugin)
    if not plugin_doc.available:
        frappe.throw(f"Plugin '{plugin}' is not available")

    parsed_settings = None
    if settings is not None:
        parsed_settings = settings if isinstance(settings, dict) else _parse_json(settings, {})
        try:
            schema_mod.validate(parsed_settings, _parse_json(plugin_doc.params_schema, {}))
        except schema_mod.SchemaValidationError as e:
            frappe.throw(str(e))

    setting = _get_or_create_setting(org, plugin)
    if enabled is not None:
        setting.enabled = 1 if sbool(enabled) else 0
    if parsed_settings is not None:
        setting.settings = json.dumps(parsed_settings)
    setting.save(ignore_permissions=True)

    return {
        "plugin": plugin,
        "enabled": bool(setting.enabled),
        "settings": _parse_json(setting.settings, {}),
    }


def _resolve_inputs(task, inputs_spec: list) -> dict:
    """Map each required input to the first task dataset that supplies it."""
    from webodm_core.plugins.runner import DATASET_FIELDS

    resolved = {}
    for spec in inputs_spec or []:
        name = spec.get("name")
        datasets = spec.get("datasets") or []
        chosen = next(
            (d for d in datasets if d in DATASET_FIELDS and task.get(d)),
            None,
        )
        if not chosen:
            frappe.throw(
                f"Task is missing the required input '{name}' "
                f"(needs one of: {', '.join(datasets)})"
            )
        resolved[name] = chosen
    return resolved


def _reject_if_active(plugin: str, task_name: str):
    """Refuse a new run while one for the same plugin+task is in flight."""
    active = frappe.get_all(
        "WebODM Plugin Run",
        filters={"plugin": plugin, "task": task_name,
                 "status": ["in", ("Queued", "Running")]},
        pluck="name",
    )
    if active:
        frappe.throw(f"'{plugin}' is already running on this task; cancel it first")


def _delete_previous_runs(plugin: str, task_name: str):
    """Delete prior runs for the same plugin+task, including their output files."""
    names = frappe.get_all(
        "WebODM Plugin Run",
        filters={"plugin": plugin, "task": task_name},
        pluck="name",
    )
    for name in names:
        for file_name in frappe.get_all(
            "File",
            filters={"attached_to_doctype": "WebODM Plugin Run", "attached_to_name": name},
            pluck="name",
        ):
            frappe.delete_doc("File", file_name, force=True, ignore_permissions=True)
        frappe.delete_doc("WebODM Plugin Run", name, force=True, ignore_permissions=True)


@frappe.whitelist(allow_guest=False)
def run_plugin(**kwargs):
    """Validate eligibility and queue an analysis run for a completed task."""
    payload = _load_payload(kwargs)
    plugin = payload.get("plugin")
    task_name = payload.get("task")
    overrides = payload.get("params") or payload.get("parameters") or {}

    if not plugin or not task_name:
        frappe.throw("plugin and task are required")

    org = tenancy.require_org()
    plugin_doc = _plugin_row(plugin)

    if not plugin_doc.available:
        frappe.throw(f"Plugin '{plugin}' is not available")
    if not plugin_doc.platform_enabled:
        frappe.throw(f"Plugin '{plugin}' is disabled platform-wide", frappe.PermissionError)

    setting = frappe.db.get_value(
        _SETTING, {"organization": org, "plugin": plugin},
        ["name", "enabled", "settings"], as_dict=True,
    )
    if not setting or not setting.enabled:
        frappe.throw(
            f"Plugin '{plugin}' is not enabled for your organization",
            frappe.PermissionError,
        )

    task = frappe.get_doc("WebODM Task", task_name)
    task.check_permission("read")
    if task.organization != org:
        frappe.throw("Task not found", frappe.PermissionError)
    if task.status != "Completed":
        frappe.throw("Plugin can only run on a completed task")

    resolved_inputs = _resolve_inputs(task, _parse_json(plugin_doc.inputs, []))
    defaults = _parse_json(setting.settings, {}) or {}
    effective = {**defaults, **(overrides if isinstance(overrides, dict) else {})}
    try:
        schema_mod.validate(effective, _parse_json(plugin_doc.params_schema, {}))
    except schema_mod.SchemaValidationError as e:
        frappe.throw(str(e))

    # Ops that declare it are validated by the analysis service *before* a run
    # exists, so e.g. a missing/unreadable model rejects the request up front.
    if plugin_doc.needs_validation:
        try:
            validate_operation(plugin, effective)
        except (GeospatialError, GeospatialUnavailable) as e:
            frappe.throw(str(e))

    # One run per (plugin, task): refuse while one is active, otherwise replace
    # the previous result (and its output) so the panel/layers never accumulate.
    _reject_if_active(plugin, task.name)
    _delete_previous_runs(plugin, task.name)

    run = frappe.get_doc({
        "doctype": "WebODM Plugin Run",
        "plugin": plugin,
        "task": task.name,
        "status": "Queued",
        "progress": 0,
        "parameters": json.dumps({"params": effective, "inputs": resolved_inputs}),
        "output_kind": plugin_doc.output_kind,
        "render_kind": plugin_doc.render_kind,
    })
    run.insert()

    frappe.enqueue(
        "webodm_core.plugins.runner.execute_run",
        queue="long",
        job_name=f"plugin_run_{run.name}",
        timeout=int(plugin_doc.timeout_seconds or _RUN_DEFAULT_TIMEOUT),
        run_name=run.name,
    )
    return {"run": run.name, "status": run.status}


@frappe.whitelist(allow_guest=False)
def list_runs(task: str | None = None):
    """List the caller's organization's plugin runs (optionally for one task)."""
    filters = {}
    if task:
        filters["task"] = task
    return frappe.get_list(
        "WebODM Plugin Run",
        filters=filters,
        fields=[
            "name", "plugin", "task", "status", "progress",
            "output_kind", "render_kind", "output_file", "output_extent",
            "output_metadata", "error", "creation", "started_at", "completed_at",
        ],
        order_by="creation desc",
        limit_page_length=100,
    )


def _serialize_run(doc) -> dict:
    return {
        "name": doc.name,
        "plugin": doc.plugin,
        "task": doc.task,
        "status": doc.status,
        "progress": doc.progress,
        "parameters": _parse_json(doc.parameters, {}),
        "output_kind": doc.output_kind,
        "render_kind": doc.render_kind,
        "output_file": doc.output_file,
        "output_extent": _parse_json(doc.output_extent, None),
        "output_metadata": _parse_json(doc.output_metadata, {}),
        "error": doc.error,
        "started_at": doc.started_at,
        "completed_at": doc.completed_at,
    }


@frappe.whitelist(allow_guest=False)
def get_run(name: str):
    doc = frappe.get_doc("WebODM Plugin Run", name)
    doc.check_permission("read")
    return _serialize_run(doc)


@frappe.whitelist(allow_guest=False)
def cancel_run(name: str):
    doc = frappe.get_doc("WebODM Plugin Run", name)
    doc.check_permission("write")
    if doc.status in ("Queued", "Running"):
        doc.db_set("status", "Cancelled")
        doc.db_set("completed_at", now_datetime())
    return {"name": doc.name, "status": frappe.db.get_value("WebODM Plugin Run", doc.name, "status")}


def _run_file_path(doc) -> str:
    from webodm_core.plugins.files import abs_path_for_file_url

    if not doc.output_file:
        frappe.throw(f"Run {doc.name} has no output", frappe.DoesNotExistError)
    return abs_path_for_file_url(doc.output_file)


def _convert_to_geojson(path: str) -> dict:
    from frappe.utils import get_site_path

    from webodm_core.plugins.geospatial import vector_to_geojson

    out_dir = os.path.abspath(get_site_path("private", "files", "plugin_runs"))
    os.makedirs(out_dir, exist_ok=True)
    tmp = os.path.join(out_dir, f"{frappe.generate_hash(length=12)}.geojson")
    try:
        vector_to_geojson(path, tmp)
        with open(tmp, encoding="utf-8") as f:
            return json.load(f)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


@frappe.whitelist(allow_guest=False)
def get_run_geojson(run_name: str):
    """Return a run's vector output as GeoJSON (converting from GPKG if needed)."""
    doc = frappe.get_doc("WebODM Plugin Run", run_name)
    doc.check_permission("read")
    if doc.output_kind != "vector":
        frappe.throw(f"Run {run_name} does not have a vector output")

    path = _run_file_path(doc)
    if path.lower().endswith((".geojson", ".json")):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("type") == "FeatureCollection":
                return data
        except (OSError, ValueError):
            pass

    return _convert_to_geojson(path)


@frappe.whitelist(allow_guest=False)
def download_run_output(run_name: str):
    """Stream a run's output artifact as a download."""
    doc = frappe.get_doc("WebODM Plugin Run", run_name)
    doc.check_permission("read")
    path = _run_file_path(doc)

    with open(path, "rb") as f:
        content = f.read()

    frappe.local.response.filename = os.path.basename(path)
    frappe.local.response.filecontent = content
    frappe.local.response.type = "download"
    return
