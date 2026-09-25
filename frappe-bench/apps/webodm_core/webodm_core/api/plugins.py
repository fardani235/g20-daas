"""Analysis plugin API: catalog listing, per-organization enablement and user
plugin packages.

Execution endpoints live alongside these in the same module (``run_plugin`` et
al.). Everything is org-scoped through ``tenancy`` and the platform kill switch
is enforced here as well as in the data model. A user plugin (``plugin_type ==
"User"``) is only ever visible to the organization that uploaded it; every
lookup goes through ``_visible_plugin_row`` so that rule has one home.
"""

import json
import os
import tempfile

import frappe
from frappe.utils import get_site_path, now_datetime, sbool

from webodm_core import tenancy
from webodm_core.plugins import package as package_mod
from webodm_core.plugins import schema as schema_mod
from webodm_core.plugins.files import save_private_file_from_path
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


def _visible_plugin_row(plugin: str, org: str | None):
    """Like ``_plugin_row`` but a user plugin of another org reads as unknown.

    Answering "unknown" rather than "forbidden" avoids confirming that a
    plugin id exists in some other organization. No platform-admin bypass:
    these endpoints act within the caller's own organization (admins manage
    other tenants' rows through Desk, where the permission hooks apply).
    """
    doc = _plugin_row(plugin)
    if doc.plugin_type == "User" and doc.organization != org:
        frappe.throw(f"Unknown plugin: {plugin}", frappe.DoesNotExistError)
    return doc


def _get_or_create_setting(org: str, plugin: str):
    name = frappe.db.get_value(_SETTING, {"organization": org, "plugin": plugin}, "name")
    if name:
        return frappe.get_doc(_SETTING, name)
    return frappe.get_doc({"doctype": _SETTING, "organization": org, "plugin": plugin})


_LIST_FIELDS = [
    "name", "label", "description", "version", "plugin_type", "organization",
    "output_kind", "render_kind", "inputs", "platform_enabled", "available",
    "params_schema", "models",
]


def _serialize_plugin(p, setting) -> dict:
    enabled = bool(setting.enabled) if setting else False
    return {
        "name": p.name,
        "op_id": p.name,
        "label": p.label,
        "description": p.description,
        "version": p.version,
        "plugin_type": p.plugin_type or "System",
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
    }


@frappe.whitelist(allow_guest=False)
def list_plugins():
    """Return the catalog with the caller's organization enablement merged in.

    System plugins are listed for everyone; user plugins only for the
    organization that owns them.
    """
    org = tenancy.get_current_org()
    plugins = frappe.get_all(
        _PLUGIN,
        filters={"available": 1, "plugin_type": "System"},
        fields=_LIST_FIELDS,
        ignore_permissions=True,
    )
    if org:
        plugins += frappe.get_all(
            _PLUGIN,
            filters={"available": 1, "plugin_type": "User", "organization": org},
            fields=_LIST_FIELDS,
            ignore_permissions=True,
        )
    plugins.sort(key=lambda p: (p.label or "").lower())

    settings_by_plugin = {}
    if org:
        for row in frappe.get_all(
            _SETTING,
            filters={"organization": org},
            fields=["plugin", "enabled", "settings"],
            ignore_permissions=True,
        ):
            settings_by_plugin[row.plugin] = row

    return [_serialize_plugin(p, settings_by_plugin.get(p.name)) for p in plugins]


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

    plugin_doc = _visible_plugin_row(plugin, org)
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


# ---------------------------------------------------------------------------
# User plugins: upload / install / remove
# ---------------------------------------------------------------------------

def _require_org_admin() -> str:
    org = tenancy.require_org()
    if not (tenancy.is_org_admin() or tenancy.is_platform_admin()):
        frappe.throw("Only organization admins can manage plugins", frappe.PermissionError)
    return org


def _org_slug(org: str) -> str:
    slug = frappe.db.get_value("WebODM Organization", org, "slug")
    return slug or frappe.scrub(org).replace("_", "-")


def _spool_upload(upload, limit: int) -> str:
    """Stream a werkzeug upload to a temp file on the site volume; return its path."""
    out_dir = os.path.abspath(get_site_path("private", "files", "plugin_packages"))
    os.makedirs(out_dir, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix="upload-", suffix=".zip", dir=out_dir)
    size = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = upload.stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise package_mod.PackageError(
                        f"package exceeds {limit // (1024 * 1024)} MB"
                    )
                out.write(chunk)
    except Exception:
        _remove_quietly(path)
        raise
    return path


def _remove_quietly(path: str):
    try:
        os.remove(path)
    except OSError:
        pass


def install_user_plugin(package_path: str, org: str) -> dict:
    """Validate the zip at ``package_path`` and install (or upgrade) it for ``org``.

    The catalog row is ``<org-slug>.<manifest id>``; re-uploading the same id
    replaces the package and manifest in place and keeps the organization's
    enablement/settings and run history. ``package_path`` is consumed (moved).
    """
    try:
        manifest = package_mod.inspect_package(package_path)
    except package_mod.PackageError as e:
        _remove_quietly(package_path)
        frappe.throw(f"Invalid plugin package: {e}")

    plugin_id = package_mod.namespaced_id(_org_slug(org), manifest["id"])
    values = {
        "plugin_type": "User",
        "organization": org,
        "label": manifest["label"],
        "version": manifest["version"],
        "description": manifest["description"],
        "entrypoint": manifest["entrypoint"],
        "inputs": json.dumps(manifest["inputs"]),
        "params_schema": json.dumps(manifest["params_schema"]),
        "output_kind": manifest["output_kind"],
        "render_kind": manifest["render_kind"],
        "timeout_seconds": manifest["timeout_seconds"],
        "needs_validation": 0,
        "models": None,
        "available": 1,
    }

    created = False
    if frappe.db.exists(_PLUGIN, plugin_id):
        doc = frappe.get_doc(_PLUGIN, plugin_id)
        if doc.plugin_type != "User" or doc.organization != org:
            # Cannot happen with the org-slug namespace, but never overwrite
            # another tenant's (or the platform's) row.
            _remove_quietly(package_path)
            frappe.throw(f"Plugin id '{plugin_id}' is not available")
        for field, value in values.items():
            doc.set(field, value)
        doc.save(ignore_permissions=True)
        _delete_attachments(_PLUGIN, doc.name)
    else:
        doc = frappe.get_doc({"doctype": _PLUGIN, "plugin_id": plugin_id,
                              "platform_enabled": 1, **values})
        doc.insert(ignore_permissions=True)
        created = True

    package_hash = package_mod.sha256_of(package_path)
    file_doc = save_private_file_from_path(
        package_path,
        f"{plugin_id}-{manifest['version']}.zip",
        attached_to_doctype=_PLUGIN,
        attached_to_name=doc.name,
        attached_to_field="package",
        ignore_permissions=True,
    )
    doc.db_set("package", file_doc.file_url)
    doc.db_set("package_hash", package_hash)

    # The uploading organization obviously wants to use it: enable on first
    # install, leave an existing choice alone on upgrade.
    if created:
        setting = _get_or_create_setting(org, doc.name)
        setting.enabled = 1
        setting.save(ignore_permissions=True)

    doc.reload()
    setting_row = frappe.db.get_value(
        _SETTING, {"organization": org, "plugin": doc.name},
        ["enabled", "settings"], as_dict=True,
    )
    return {**_serialize_plugin(doc, setting_row), "created": created}


@frappe.whitelist(allow_guest=False)
def upload_plugin():
    """Upload a plugin package (multipart field ``file``) for the caller's organization."""
    org = _require_org_admin()

    # Frappe only sets max_content_length for its own upload endpoint.
    frappe.request.max_content_length = package_mod.MAX_PACKAGE_BYTES + 1024 * 1024

    upload = frappe.request.files.get("file")
    if upload is None:
        frappe.throw("No file provided (multipart field 'file')")

    try:
        path = _spool_upload(upload, package_mod.MAX_PACKAGE_BYTES)
    except package_mod.PackageError as e:
        frappe.throw(f"Invalid plugin package: {e}")
    return install_user_plugin(path, org)


@frappe.whitelist(allow_guest=False)
def remove_plugin(plugin: str):
    """Uninstall a user plugin: its runs and outputs, settings, package and row."""
    org = _require_org_admin()
    doc = _visible_plugin_row(plugin, org)
    if doc.plugin_type != "User":
        frappe.throw("System plugins cannot be removed; disable them instead")

    _delete_runs({"plugin": doc.name})
    for name in frappe.get_all(_SETTING, filters={"plugin": doc.name}, pluck="name"):
        frappe.delete_doc(_SETTING, name, force=True, ignore_permissions=True)
    _delete_attachments(_PLUGIN, doc.name)
    frappe.delete_doc(_PLUGIN, doc.name, force=True, ignore_permissions=True)
    return {"plugin": doc.name, "removed": True}


def _resolve_inputs(task, inputs_spec: list, selection: dict | None = None) -> dict:
    """Map each declared input to the task dataset that supplies it.

    ``selection`` (``{input name: dataset}``, from the run request) lets the
    user pick among the datasets an input accepts, or leave an *optional*
    input out with ``None``/``""``. When a selection is given it is complete:
    optional inputs it does not name are left out too. Without a selection
    (older clients) every input takes the first dataset the task has. A
    required input that resolves to nothing is an error. At least one input
    must resolve.
    """
    from webodm_core.plugins.runner import DATASET_FIELDS

    explicit = isinstance(selection, dict)
    selection = selection if explicit else {}
    known = {spec.get("name") for spec in inputs_spec or []}
    for name in selection:
        if name not in known:
            frappe.throw(f"Unknown input '{name}'")

    resolved = {}
    for spec in inputs_spec or []:
        name = spec.get("name")
        datasets = [d for d in (spec.get("datasets") or []) if d in DATASET_FIELDS]
        optional = bool(spec.get("optional"))

        if name in selection:
            chosen = selection[name]
            if chosen in (None, ""):
                if not optional:
                    frappe.throw(f"Input '{name}' is required (one of: {', '.join(datasets)})")
                continue
            if chosen not in datasets:
                frappe.throw(
                    f"Input '{name}' cannot use '{chosen}' (one of: {', '.join(datasets)})"
                )
            if not task.get(chosen):
                frappe.throw(f"Task has no '{chosen}' for input '{name}'")
        else:
            if optional and explicit:
                continue
            chosen = next((d for d in datasets if task.get(d)), None)
            if not chosen:
                if optional:
                    continue
                frappe.throw(
                    f"Task is missing the required input '{name}' "
                    f"(needs one of: {', '.join(datasets)})"
                )
        resolved[name] = chosen

    if not resolved:
        frappe.throw("Select at least one input dataset for this plugin")
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


def _delete_attachments(doctype: str, name: str):
    for file_name in frappe.get_all(
        "File",
        filters={"attached_to_doctype": doctype, "attached_to_name": name},
        pluck="name",
    ):
        frappe.delete_doc("File", file_name, force=True, ignore_permissions=True)


def _delete_runs(filters: dict):
    """Delete the runs matching ``filters``, including their output files."""
    for name in frappe.get_all("WebODM Plugin Run", filters=filters, pluck="name"):
        _delete_attachments("WebODM Plugin Run", name)
        frappe.delete_doc("WebODM Plugin Run", name, force=True, ignore_permissions=True)


def _delete_previous_runs(plugin: str, task_name: str):
    """Delete prior runs for the same plugin+task, including their output files."""
    _delete_runs({"plugin": plugin, "task": task_name})


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
    plugin_doc = _visible_plugin_row(plugin, org)

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

    resolved_inputs = _resolve_inputs(
        task, _parse_json(plugin_doc.inputs, []), payload.get("inputs")
    )
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
            "name", "plugin", "task", "status", "progress", "progress_message",
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
        "progress_message": doc.get("progress_message"),
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
    """Absolute path of a run's output, re-materialised from object storage if evicted."""
    from webodm_core.storage import assets as storage_assets
    from webodm_core.storage import cache

    if not doc.output_file:
        frappe.throw("Run has no output", frappe.DoesNotExistError)
    try:
        return storage_assets.ensure_run_output_local(doc)
    except cache.CacheMiss as e:
        frappe.throw(f"Run output is not available: {e}", frappe.DoesNotExistError)


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
