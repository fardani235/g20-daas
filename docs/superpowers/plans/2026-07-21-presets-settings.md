# Presets & Settings Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the placeholder Presets and Settings pages to the backend so saved processing presets (full dynamic ODM options) and global settings (a default preset + auto-start) apply as NodeODM options when a task is created.

**Architecture:** Presets store options in NodeODM's exact array form `[{name,value}]`, so preset-driven tasks need no translation. New whitelisted Frappe endpoints proxy the node's `GET /options` catalog and CRUD the existing WebODM Preset / WebODM Settings DocTypes. The frontend renders a dynamic options form from the catalog; the upload dialog seeds it from a chosen preset, and Settings supplies a default preset + auto-start.

**Tech Stack:** Frappe v16 (Python 3.14), NodeODM HTTP API, Vue 3 + frappe-ui, Vitest + jsdom, Frappe test runner (`bench run-tests`).

## Global Constraints

- NodeODM `POST /task/new` requires `options` as a JSON **array** of `{"name":..., "value":...}` objects; a dict is silently dropped. Presets and dynamic-form output MUST be this array shape.
- ODM has **no `--orthophoto` flag**: orthophoto is default-on; suppress with `skip-orthophoto: true`. (Only relevant to the legacy dict fallback.)
- Preset visibility/permissions: a user sees their own presets (`owner == session user`) + `system` presets. Only users with the **System Manager** or **Administrator** role may create/edit/delete `system` presets; a non-admin may create/edit/delete only presets they own. Enforced server-side with `frappe.throw(..., frappe.PermissionError)`.
- Options catalog is fetched **live** from the processing node's `GET /options` (no caching, no bundled static file). Node offline → clear error, dynamic form disabled, existing presets still list/apply.
- Frontend calls use the existing pattern: `fetch('/api/method/...')`, `X-Frappe-CSRF-Token` header from `window.csrf_token` when present, and unwrap Frappe's `{message: ...}` envelope.
- Whitelisted endpoints use `@frappe.whitelist(allow_guest=False)` and read POST bodies via `frappe.form_dict` / `frappe.request.data` (JSON) exactly as existing `api/task.py` endpoints do.
- DRY, YAGNI, TDD, frequent commits. Out of scope: Plugins/Landing/Invoices pages, per-project defaults, preset import/export, server-side validation of options against the live catalog, catalog caching.
- Repo: `/home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_core` (branch `main`) and `/home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend` (branch `main`). Frappe tests run from `/home/ridwan/workspaces/frappe-webodm/frappe-bench` via `bench --site webodm.local run-tests --module <mod>`. Frontend tests run from `.../webodm_frontend/frontend` via `npm test -- --run`.

---

## File Structure

**webodm_core (Python):**
- `webodm_core/webodm_core/processing/node_client.py` — MODIFY: add `get_options()`.
- `webodm_core/webodm_core/doctype/webodm_task/webodm_task.json` — MODIFY: add `node_task_id` field.
- `webodm_core/webodm_core/doctype/webodm_settings/webodm_settings.json` — MODIFY: add `default_preset`, `auto_start_processing`.
- `webodm_core/webodm_core/processing/task_runner.py` — MODIFY: `_build_node_options` accepts list|dict; node task id moves to its own field.
- `webodm_core/api/task.py` — MODIFY: read node task id from the field; auto-start hook.
- `webodm_core/api/presets.py` — CREATE: `options`, `list_presets`, `save`, `delete`.
- `webodm_core/api/settings.py` — CREATE: `get`, `save`.
- `webodm_core/webodm_core/processing/test_task_runner.py` — MODIFY: list pass-through + dict fallback.
- `webodm_core/api/test_presets.py`, `webodm_core/api/test_settings.py` — CREATE.

**webodm_frontend (Vue):**
- `frontend/src/lib/presets.js` — CREATE: fetch wrappers.
- `frontend/src/lib/presets.test.js` — CREATE.
- `frontend/src/composables/useOdmOptions.js` — CREATE: catalog loader + `fieldType`.
- `frontend/src/composables/useOdmOptions.test.js` — CREATE.
- `frontend/src/pages/Presets.vue` — MODIFY: real CRUD + dynamic form.
- `frontend/src/pages/Settings.vue` — MODIFY: real load/save + default-preset/auto-start.
- `frontend/src/pages/MapView.vue` — MODIFY: upload dialog preset picker + dynamic form + auto-start.

---

## Task 1: `node_client.get_options()`

**Files:**
- Modify: `/home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_core/webodm_core/webodm_core/processing/node_client.py`
- Test: `/home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_core/webodm_core/webodm_core/processing/test_node_client.py` (create)

**Interfaces:**
- Consumes: existing `NodeODMClient._get(path)` (returns parsed JSON, raises `NodeODMError`).
- Produces: `NodeODMClient.get_options() -> list` — calls `GET /options`, returns the catalog list.

- [ ] **Step 1: Write the failing test**

Create `test_node_client.py`:
```python
import unittest
from unittest.mock import patch

from webodm_core.webodm_core.processing.node_client import NodeODMClient


class TestGetOptions(unittest.TestCase):
    def test_get_options_calls_options_endpoint(self):
        c = NodeODMClient("localhost", 3000)
        with patch.object(c, "_get", return_value=[{"name": "dsm", "type": "bool"}]) as g:
            out = c.get_options()
        g.assert_called_once_with("options")
        self.assertEqual(out, [{"name": "dsm", "type": "bool"}])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `/home/ridwan/workspaces/frappe-webodm/frappe-bench`):
```bash
bench --site webodm.local run-tests --module webodm_core.webodm_core.processing.test_node_client
```
Expected: FAIL — `AttributeError: 'NodeODMClient' object has no attribute 'get_options'`.

- [ ] **Step 3: Add the method**

In `node_client.py`, immediately after the `version` method (after line 42, `return self._get("version")`), add:
```python

    def get_options(self) -> list:
        return self._get("options")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `bench --site webodm.local run-tests --module webodm_core.webodm_core.processing.test_node_client`
Expected: PASS — 1 test OK.

- [ ] **Step 5: Commit**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_core
git add webodm_core/webodm_core/processing/node_client.py webodm_core/webodm_core/processing/test_node_client.py
git commit -m "feat: add NodeODMClient.get_options() for the ODM option catalog"
```

---

## Task 2: Move node task id to its own Task field

**Why:** `_node_task_id` is currently stored as a key **inside** `processing_options` and read at 4 sites. Once presets make `processing_options` a JSON **list**, `opts.get("_node_task_id")` breaks. This task moves the node task id to a dedicated `node_task_id` field on WebODM Task so options can be any shape.

**Files:**
- Modify: `/home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_core/webodm_core/webodm_core/doctype/webodm_task/webodm_task.json`
- Modify: `/home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_core/webodm_core/webodm_core/processing/task_runner.py`
- Modify: `/home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_core/webodm_core/api/task.py`

**Interfaces:**
- Produces: `WebODM Task.node_task_id` (Data field). Written in `task_runner.dispatch_task` after `create_task`; read in `task_runner.poll_task`, `api.task.cancel_task`, `api.task.get_task_console`, `api.task.get_task_progress`.

- [ ] **Step 1: Add the field to the DocType JSON**

In `webodm_task.json`, add `"node_task_id"` to `field_order` immediately after `"processing_options"`, and add this field object to the `fields` array (place it right after the `processing_options` field object):
```json
  {
   "fieldname": "node_task_id",
   "fieldtype": "Data",
   "label": "Node Task ID",
   "read_only": 1,
   "hidden": 1
  }
```

- [ ] **Step 2: Apply the schema change**

Run (from `/home/ridwan/workspaces/frappe-webodm/frappe-bench`):
```bash
bench --site webodm.local migrate
```
Expected: migrate completes; the `node_task_id` column is added to `tabWebODM Task`.

- [ ] **Step 3: Write the node task id on dispatch**

In `task_runner.py`, replace the post-`create_task` block (currently lines ~137-144, the block that reads `node_opts = task.processing_options or {}` … `node_opts[NODE_TASK_ID_KEY] = node_task_id` … `task.db_set("processing_options", frappe.as_json(node_opts))` … `task.db_set("status", "Running")`) with:
```python
    task.db_set("node_task_id", node_task_id)
    task.db_set("status", "Running")
```

- [ ] **Step 4: Read the node task id from the field in poll_task**

In `task_runner.py` `poll_task` (currently ~lines 218-224), replace:
```python
    opts = task.processing_options
    if isinstance(opts, str):
        opts = frappe.parse_json(opts)
    if not isinstance(opts, dict):
        return

    node_task_id = opts.get(NODE_TASK_ID_KEY)
```
with:
```python
    node_task_id = task.node_task_id
```

- [ ] **Step 5: Remove the now-unused constant**

In `task_runner.py`, delete the line `NODE_TASK_ID_KEY = "_node_task_id"` (line 5).

- [ ] **Step 6: Read the node task id from the field in api/task.py (3 sites)**

In `api/task.py` `cancel_task` (~lines 48-52), replace:
```python
    opts = task.processing_options
    if isinstance(opts, str):
        opts = frappe.parse_json(opts)
    if isinstance(opts, dict):
        node_task_id = opts.get("_node_task_id")
        if node_task_id:
```
with:
```python
    node_task_id = task.node_task_id
    if node_task_id:
```
(Keep the existing indented body under `if node_task_id:` unchanged.)

In `get_task_console` (~lines 282-285), replace:
```python
    opts = task.processing_options
    if isinstance(opts, str):
        opts = frappe.parse_json(opts)
    node_task_id = opts.get("_node_task_id") if isinstance(opts, dict) else None
```
with:
```python
    node_task_id = task.node_task_id
```

In `get_task_progress` (~lines 321-325), replace:
```python
    opts = task.processing_options
    if isinstance(opts, str):
        opts = frappe.parse_json(opts)
    if isinstance(opts, dict):
        node_task_id = opts.get("_node_task_id")
        if node_task_id:
```
with:
```python
    node_task_id = task.node_task_id
    if node_task_id:
```
(Keep the existing indented body unchanged.)

- [ ] **Step 7: Verify no stale references remain**

Run (from `/home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_core`):
```bash
grep -rn "_node_task_id\|NODE_TASK_ID_KEY" --include=*.py webodm_core | grep -v __pycache__
```
Expected: **no output** (all references removed).

- [ ] **Step 8: Run the existing suites to confirm no regression**

Run (from `/home/ridwan/workspaces/frappe-webodm/frappe-bench`):
```bash
bench --site webodm.local run-tests --module webodm_core.api.test_task
bench --site webodm.local run-tests --module webodm_core.webodm_core.processing.test_task_runner
```
Expected: both PASS (test_task 6/6, test_task_runner 9/9).

- [ ] **Step 9: Commit**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_core
git add webodm_core/webodm_core/doctype/webodm_task/webodm_task.json webodm_core/webodm_core/processing/task_runner.py webodm_core/api/task.py
git commit -m "refactor: store node task id in its own Task field so options can be a list"
```

---

## Task 3: `_build_node_options` accepts a list (pass-through)

**Files:**
- Modify: `/home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_core/webodm_core/webodm_core/processing/task_runner.py`
- Test: `/home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_core/webodm_core/webodm_core/processing/test_task_runner.py`

**Interfaces:**
- Consumes: `task.processing_options` (may be a dict — legacy — or a list — preset path).
- Produces: `_build_node_options(opts: dict | list) -> list[dict]` returning the NodeODM array. A list input is validated and passed through; a dict input keeps the existing translation.

- [ ] **Step 1: Write the failing tests**

Append to `test_task_runner.py` (inside the existing `TestBuildNodeOptions` class, before the final `if __name__` guard):
```python
    def test_list_input_passes_through_verbatim(self):
        opts = [{"name": "dsm", "value": True}, {"name": "feature-quality", "value": "ultra"}]
        self.assertEqual(_build_node_options(opts), opts)

    def test_list_input_drops_malformed_entries(self):
        opts = [{"name": "dsm", "value": True}, {"bad": "x"}, "nope"]
        self.assertEqual(_build_node_options(opts), [{"name": "dsm", "value": True}])
```

- [ ] **Step 2: Run to verify they fail**

Run (from `/home/ridwan/workspaces/frappe-webodm/frappe-bench`):
```bash
bench --site webodm.local run-tests --module webodm_core.webodm_core.processing.test_task_runner
```
Expected: FAIL — the list is currently treated as a dict-like and mishandled (the two new tests error/fail).

- [ ] **Step 3: Add the list branch**

In `task_runner.py`, change the `_build_node_options` signature and add a list branch at the very top of the function body (immediately after the docstring, before `result: list[dict] = []`):
```python
def _build_node_options(opts: dict | list) -> list[dict]:
```
and, as the first statements of the body:
```python
    # Preset / dynamic path: options already in NodeODM array form — pass through,
    # keeping only well-formed {name, value} entries.
    if isinstance(opts, list):
        return [o for o in opts if isinstance(o, dict) and "name" in o and "value" in o]
```
(Leave the existing dict-translation logic below unchanged as the fallback.)

- [ ] **Step 4: Update the dispatch call site to pass lists through**

In `task_runner.py` `dispatch_task` (~lines 116-123), replace:
```python
    options = task.processing_options or {}
    if isinstance(options, str):
        options = frappe.parse_json(options)

    node_opts = {}
    if isinstance(options, dict):
        node_opts = _build_node_options(options)
```
with:
```python
    options = task.processing_options or {}
    if isinstance(options, str):
        options = frappe.parse_json(options)

    node_opts = _build_node_options(options) if isinstance(options, (dict, list)) else []
```

- [ ] **Step 5: Run to verify all pass**

Run: `bench --site webodm.local run-tests --module webodm_core.webodm_core.processing.test_task_runner`
Expected: PASS — 11 tests (9 existing + 2 new).

- [ ] **Step 6: Commit**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_core
git add webodm_core/webodm_core/processing/task_runner.py webodm_core/webodm_core/processing/test_task_runner.py
git commit -m "feat: _build_node_options passes list-shaped preset options through verbatim"
```

---

## Task 4: WebODM Settings — new processing fields

**Files:**
- Modify: `/home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_core/webodm_core/webodm_core/doctype/webodm_settings/webodm_settings.json`

**Interfaces:**
- Produces: `WebODM Settings.default_preset` (Link → WebODM Preset), `WebODM Settings.auto_start_processing` (Check, default 0).

- [ ] **Step 1: Add fields to the DocType JSON**

In `webodm_settings.json`, add `"default_preset"` and `"auto_start_processing"` to `field_order` immediately after `"max_project_count"` (i.e. before `"section_notifications"`). Add a `section_processing` break as well so they group cleanly. New `field_order` segment:
```json
  "max_project_count",
  "section_processing",
  "default_preset",
  "auto_start_processing",
  "section_notifications",
```
And add these field objects to `fields` (after the `max_project_count` object):
```json
  {
   "fieldname": "section_processing",
   "fieldtype": "Section Break",
   "label": "Processing"
  },
  {
   "fieldname": "default_preset",
   "fieldtype": "Link",
   "label": "Default Preset",
   "options": "WebODM Preset"
  },
  {
   "default": "0",
   "fieldname": "auto_start_processing",
   "fieldtype": "Check",
   "label": "Auto-start processing after upload"
  }
```

- [ ] **Step 2: Apply the schema change**

Run (from `/home/ridwan/workspaces/frappe-webodm/frappe-bench`):
```bash
bench --site webodm.local migrate
```
Expected: migrate completes without error.

- [ ] **Step 3: Verify the fields exist**

Run (from `/home/ridwan/workspaces/frappe-webodm/frappe-bench`):
```bash
bench --site webodm.local execute frappe.client.get_list --kwargs "{'doctype':'DocField','filters':{'parent':'WebODM Settings','fieldname':['in',['default_preset','auto_start_processing']]},'fields':['fieldname']}"
```
Expected: output lists both `default_preset` and `auto_start_processing`.

- [ ] **Step 4: Commit**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_core
git add webodm_core/webodm_core/doctype/webodm_settings/webodm_settings.json
git commit -m "feat: add default_preset and auto_start_processing to WebODM Settings"
```

---

## Task 5: `api/settings.py` — get/save

**Files:**
- Create: `/home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_core/webodm_core/api/settings.py`
- Test: `/home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_core/webodm_core/api/test_settings.py`

**Interfaces:**
- Consumes: WebODM Settings single doc (with fields from Task 4).
- Produces: `settings.get() -> dict`; `settings.save(**fields) -> dict`. Frontend calls `/api/method/webodm_core.api.settings.get` and `.save`.

- [ ] **Step 1: Write the failing test**

Create `test_settings.py`:
```python
import unittest

import frappe

from webodm_core.api import settings


class TestSettings(unittest.TestCase):
    def test_get_returns_known_fields(self):
        out = settings.get()
        self.assertIn("default_preset", out)
        self.assertIn("auto_start_processing", out)
        self.assertIn("max_file_size_mb", out)

    def test_save_persists_allowed_field(self):
        settings.save(max_file_size_mb=321, auto_start_processing=1)
        frappe.db.commit()
        out = settings.get()
        self.assertEqual(int(out["max_file_size_mb"]), 321)
        self.assertEqual(int(out["auto_start_processing"]), 1)

    def test_save_ignores_unknown_field(self):
        # Must not raise even if the caller passes a field that isn't on the doc.
        settings.save(not_a_real_field="x", max_file_size_mb=222)
        frappe.db.commit()
        self.assertEqual(int(settings.get()["max_file_size_mb"]), 222)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `bench --site webodm.local run-tests --module webodm_core.api.test_settings`
Expected: FAIL — `ModuleNotFoundError: No module named 'webodm_core.api.settings'`.

- [ ] **Step 3: Write the implementation**

Create `settings.py`:
```python
import frappe

_DOCTYPE = "WebODM Settings"

# Fields the API is allowed to write (mirrors the DocType's editable fields).
_ALLOWED = {
    "default_basemap",
    "enable_public_sharing",
    "max_file_size_mb",
    "max_project_count",
    "slack_webhook_url",
    "email_notifications",
    "default_preset",
    "auto_start_processing",
}


@frappe.whitelist(allow_guest=False)
def get():
    """Return the WebODM Settings single doc as a plain dict."""
    doc = frappe.get_single(_DOCTYPE)
    return {f: doc.get(f) for f in _ALLOWED}


@frappe.whitelist(allow_guest=False)
def save(**fields):
    """Update the allowed fields on the WebODM Settings single doc."""
    raw = frappe.request.data
    if raw:
        if isinstance(raw, bytes):
            raw = raw.decode()
        parsed = frappe.parse_json(raw)
        if isinstance(parsed, dict):
            fields = {**fields, **parsed}

    doc = frappe.get_single(_DOCTYPE)
    for k, v in fields.items():
        if k in _ALLOWED:
            doc.set(k, v)
    doc.save(ignore_permissions=True)
    return get()
```

- [ ] **Step 4: Run to verify it passes**

Run: `bench --site webodm.local run-tests --module webodm_core.api.test_settings`
Expected: PASS — 3 tests OK.

- [ ] **Step 5: Commit**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_core
git add webodm_core/api/settings.py webodm_core/api/test_settings.py
git commit -m "feat: add settings get/save API for WebODM Settings"
```

---

## Task 6: `api/presets.py` — options/list/save/delete

**Files:**
- Create: `/home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_core/webodm_core/api/presets.py`
- Test: `/home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_core/webodm_core/api/test_presets.py`

**Interfaces:**
- Consumes: `NodeODMClient.get_options()` (Task 1); WebODM Preset DocType (`preset_name`, `owner`, `system`, `options`).
- Produces: `presets.options() -> list`; `presets.list_presets() -> list[dict]`; `presets.save(preset_name, options, system=0, name=None) -> dict`; `presets.delete(name) -> dict`. Frontend calls `/api/method/webodm_core.api.presets.<fn>`.

- [ ] **Step 1: Write the failing tests**

Create `test_presets.py`:
```python
import json
import unittest
from unittest.mock import patch, MagicMock

import frappe

from webodm_core.api import presets


def _cleanup(names):
    for n in names:
        if frappe.db.exists("WebODM Preset", n):
            frappe.delete_doc("WebODM Preset", n, force=True, ignore_permissions=True)
    frappe.db.commit()


class TestPresets(unittest.TestCase):
    def tearDown(self):
        _cleanup(["Test User Preset", "Test System Preset"])
        frappe.set_user("Administrator")

    def test_options_proxies_node_catalog(self):
        fake = [{"name": "dsm", "type": "bool", "domain": None}]
        client = MagicMock()
        client.get_options.return_value = fake
        with patch.object(presets, "_node_client", return_value=client):
            self.assertEqual(presets.options(), fake)

    def test_options_node_offline_throws(self):
        with patch.object(presets, "_node_client", return_value=None):
            with self.assertRaises(frappe.ValidationError):
                presets.options()

    def test_save_and_list_roundtrip_owns_options(self):
        opts = [{"name": "dsm", "value": True}]
        presets.save(preset_name="Test User Preset", options=json.dumps(opts))
        frappe.db.commit()
        listed = {p["preset_name"]: p for p in presets.list_presets()}
        self.assertIn("Test User Preset", listed)
        self.assertEqual(listed["Test User Preset"]["options"], opts)

    def test_non_admin_cannot_create_system_preset(self):
        # Administrator is admin; simulate a non-admin by role check patch.
        with patch.object(presets, "_is_admin", return_value=False):
            with self.assertRaises(frappe.PermissionError):
                presets.save(preset_name="Test System Preset", options="[]", system=1)

    def test_delete_removes_own_preset(self):
        presets.save(preset_name="Test User Preset", options="[]")
        frappe.db.commit()
        presets.delete("Test User Preset")
        frappe.db.commit()
        self.assertFalse(frappe.db.exists("WebODM Preset", "Test User Preset"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `bench --site webodm.local run-tests --module webodm_core.api.test_presets`
Expected: FAIL — `ModuleNotFoundError: No module named 'webodm_core.api.presets'`.

- [ ] **Step 3: Write the implementation**

Create `presets.py`:
```python
import frappe

_DOCTYPE = "WebODM Preset"


def _is_admin() -> bool:
    roles = set(frappe.get_roles(frappe.session.user))
    return bool(roles & {"System Manager", "Administrator"})


def _node_client():
    """Build a NodeODMClient for the first configured processing node, or None."""
    from webodm_core.webodm_core.processing.node_client import NodeODMClient
    nodes = frappe.get_all("WebODM Processing Node", fields=["hostname", "port", "token"])
    if not nodes:
        return None
    n = nodes[0]
    return NodeODMClient(n["hostname"], n["port"], n.get("token"))


@frappe.whitelist(allow_guest=False)
def options():
    """Proxy the processing node's GET /options catalog (live)."""
    from webodm_core.webodm_core.processing.node_client import NodeODMError
    client = _node_client()
    if client is None:
        frappe.throw("Processing node offline — can't load options")
    try:
        return client.get_options()
    except NodeODMError:
        frappe.throw("Processing node offline — can't load options")


@frappe.whitelist(allow_guest=False)
def list_presets():
    """Presets visible to the session user: their own + system presets."""
    user = frappe.session.user
    rows = frappe.get_all(
        _DOCTYPE,
        filters=[["system", "=", 1]],
        fields=["name", "preset_name", "options", "system", "owner"],
    ) + frappe.get_all(
        _DOCTYPE,
        filters=[["owner", "=", user], ["system", "=", 0]],
        fields=["name", "preset_name", "options", "system", "owner"],
    )
    for r in rows:
        r["options"] = frappe.parse_json(r["options"]) if r.get("options") else []
    return rows


@frappe.whitelist(allow_guest=False)
def save(preset_name, options, system=0, name=None):
    """Create or update a preset. options is a JSON string of [{name, value}]."""
    system = int(system or 0)
    user = frappe.session.user

    if system and not _is_admin():
        frappe.throw("Only administrators can manage system presets", frappe.PermissionError)

    if name and frappe.db.exists(_DOCTYPE, name):
        doc = frappe.get_doc(_DOCTYPE, name)
        if doc.system and not _is_admin():
            frappe.throw("Only administrators can edit system presets", frappe.PermissionError)
        if not doc.system and doc.owner != user and not _is_admin():
            frappe.throw("You can only edit your own presets", frappe.PermissionError)
        doc.preset_name = preset_name
        doc.options = options if isinstance(options, str) else frappe.as_json(options)
        doc.system = system
        doc.save(ignore_permissions=True)
    else:
        doc = frappe.get_doc({
            "doctype": _DOCTYPE,
            "preset_name": preset_name,
            "owner": user,
            "system": system,
            "options": options if isinstance(options, str) else frappe.as_json(options),
        })
        doc.insert(ignore_permissions=True)

    return {"name": doc.name, "preset_name": doc.preset_name}


@frappe.whitelist(allow_guest=False)
def delete(name):
    """Delete a preset the user owns; admins may delete any."""
    if not frappe.db.exists(_DOCTYPE, name):
        return {"ok": True}
    doc = frappe.get_doc(_DOCTYPE, name)
    user = frappe.session.user
    if doc.system and not _is_admin():
        frappe.throw("Only administrators can delete system presets", frappe.PermissionError)
    if not doc.system and doc.owner != user and not _is_admin():
        frappe.throw("You can only delete your own presets", frappe.PermissionError)
    frappe.delete_doc(_DOCTYPE, name, ignore_permissions=True)
    return {"ok": True}
```

- [ ] **Step 4: Run to verify it passes**

Run: `bench --site webodm.local run-tests --module webodm_core.api.test_presets`
Expected: PASS — 5 tests OK.

- [ ] **Step 5: Commit**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_core
git add webodm_core/api/presets.py webodm_core/api/test_presets.py
git commit -m "feat: add presets API (options proxy, list, save, delete) with permission rules"
```

---

## Task 7: Auto-start hook in `upload_images`

**Files:**
- Modify: `/home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_core/webodm_core/api/task.py`

**Interfaces:**
- Consumes: `WebODM Settings.auto_start_processing` (Task 4); the existing `frappe.enqueue(... task_runner.process_task ...)` dispatch used by `process_task`.
- Produces: after `upload_images` saves a task, if auto-start is enabled it enqueues processing (same as manual Start), so the returned task may already be dispatching.

- [ ] **Step 1: Write the failing test**

Append to `/home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_core/webodm_core/api/test_task.py` (add the import at the top if absent, and a new test class at the end):
```python
class TestAutoStart(unittest.TestCase):
    def test_auto_start_helper_enqueues_when_enabled(self):
        from unittest.mock import patch
        from webodm_core.api import task as task_api
        with patch("frappe.get_single") as gs, patch("frappe.enqueue") as enq:
            gs.return_value.auto_start_processing = 1
            task_api._maybe_autostart("SOME-TASK")
            enq.assert_called_once()

    def test_auto_start_helper_noop_when_disabled(self):
        from unittest.mock import patch
        from webodm_core.api import task as task_api
        with patch("frappe.get_single") as gs, patch("frappe.enqueue") as enq:
            gs.return_value.auto_start_processing = 0
            task_api._maybe_autostart("SOME-TASK")
            enq.assert_not_called()
```

- [ ] **Step 2: Run to verify it fails**

Run: `bench --site webodm.local run-tests --module webodm_core.api.test_task`
Expected: FAIL — `AttributeError: module 'webodm_core.api.task' has no attribute '_maybe_autostart'`.

- [ ] **Step 3: Add the helper and call it**

In `api/task.py`, add this helper (place it just above the `upload_images` function definition, ~line 199):
```python
def _maybe_autostart(task_name: str):
    """Enqueue processing right after upload if WebODM Settings enables it."""
    settings = frappe.get_single("WebODM Settings")
    if not settings.auto_start_processing:
        return
    frappe.enqueue(
        "webodm_core.webodm_core.processing.task_runner.process_task",
        queue="long",
        job_name=f"process_{task_name}",
        task_name=task_name,
    )
```
Then, in `upload_images`, immediately after `frappe.db.commit()` and before `return task.as_dict()` (the last two lines of the function), insert:
```python
    _maybe_autostart(task.name)
```

- [ ] **Step 4: Run to verify it passes**

Run: `bench --site webodm.local run-tests --module webodm_core.api.test_task`
Expected: PASS — 8 tests (6 existing + 2 new).

- [ ] **Step 5: Commit**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_core
git add webodm_core/api/task.py webodm_core/api/test_task.py
git commit -m "feat: auto-start processing after upload when enabled in settings"
```

---

## Task 8: Frontend `lib/presets.js` fetch wrappers

**Files:**
- Create: `/home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend/src/lib/presets.js`
- Test: `/home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend/src/lib/presets.test.js`

**Interfaces:**
- Produces: `listPresets()`, `savePreset(payload)`, `deletePreset(name)`, `fetchOptions()`, `getSettings()`, `saveSettings(fields)` — all async, returning the unwrapped `message`. Consumed by Presets.vue, Settings.vue, MapView.vue.

- [ ] **Step 1: Write the failing test**

Create `presets.test.js`:
```js
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { listPresets, savePreset, fetchOptions } from '@/lib/presets'

describe('presets lib', () => {
  beforeEach(() => {
    global.window.csrf_token = 'tok'
    global.fetch = vi.fn(async () => ({
      ok: true,
      json: async () => ({ message: [{ preset_name: 'X', options: [] }] }),
    }))
  })
  afterEach(() => { vi.restoreAllMocks() })

  it('listPresets unwraps the message envelope', async () => {
    const out = await listPresets()
    expect(out).toEqual([{ preset_name: 'X', options: [] }])
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/method/webodm_core.api.presets.list_presets',
      expect.objectContaining({ method: 'GET' }),
    )
  })

  it('savePreset sends CSRF header and JSON body', async () => {
    await savePreset({ preset_name: 'X', options: [{ name: 'dsm', value: true }] })
    const [, opts] = global.fetch.mock.calls[0]
    expect(opts.method).toBe('POST')
    expect(opts.headers['X-Frappe-CSRF-Token']).toBe('tok')
    expect(JSON.parse(opts.body).preset_name).toBe('X')
  })

  it('fetchOptions calls the options endpoint', async () => {
    await fetchOptions()
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/method/webodm_core.api.presets.options',
      expect.objectContaining({ method: 'GET' }),
    )
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run (from `.../webodm_frontend/frontend`):
```bash
npm test -- --run presets
```
Expected: FAIL — cannot resolve `@/lib/presets`.

- [ ] **Step 3: Write the implementation**

Create `presets.js`:
```js
// Fetch wrappers for the presets/settings backend. Each returns the unwrapped
// Frappe `message` payload. POSTs carry the CSRF token like the rest of the app.

function headers(json = false) {
  const h = {}
  if (json) h['Content-Type'] = 'application/json'
  if (window.csrf_token) h['X-Frappe-CSRF-Token'] = window.csrf_token
  return h
}

async function unwrap(res) {
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.message || 'Request failed')
  }
  const data = await res.json()
  return data.message !== undefined ? data.message : data
}

function get(method) {
  return fetch(`/api/method/${method}`, { method: 'GET', headers: headers() }).then(unwrap)
}

function post(method, body) {
  return fetch(`/api/method/${method}`, {
    method: 'POST',
    headers: headers(true),
    body: JSON.stringify(body || {}),
  }).then(unwrap)
}

export const listPresets = () => get('webodm_core.api.presets.list_presets')
export const fetchOptions = () => get('webodm_core.api.presets.options')
export const savePreset = payload => post('webodm_core.api.presets.save', payload)
export const deletePreset = name => post('webodm_core.api.presets.delete', { name })
export const getSettings = () => get('webodm_core.api.settings.get')
export const saveSettings = fields => post('webodm_core.api.settings.save', fields)
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm test -- --run presets`
Expected: PASS — 3 tests.

- [ ] **Step 5: Commit**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git add frontend/src/lib/presets.js frontend/src/lib/presets.test.js
git commit -m "feat: add presets/settings fetch wrappers"
```

---

## Task 9: Frontend `composables/useOdmOptions.js`

**Files:**
- Create: `/home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend/src/composables/useOdmOptions.js`
- Test: `/home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend/src/composables/useOdmOptions.test.js`

**Interfaces:**
- Consumes: `fetchOptions` from `@/lib/presets` (Task 8).
- Produces: `useOdmOptions()` returning `{ catalog (ref), error (ref), loading (ref), load(), fieldType(option) }`. `fieldType` maps a catalog entry to `'checkbox' | 'select' | 'number' | 'text'`.

- [ ] **Step 1: Write the failing test**

Create `useOdmOptions.test.js`:
```js
import { describe, it, expect } from 'vitest'
import { fieldType } from '@/composables/useOdmOptions'

describe('fieldType', () => {
  it('bool -> checkbox', () => {
    expect(fieldType({ name: 'dsm', type: 'bool' })).toBe('checkbox')
  })
  it('enum (domain array) -> select', () => {
    expect(fieldType({ name: 'feature-quality', type: 'enum', domain: ['low', 'high'] })).toBe('select')
  })
  it('int/float -> number', () => {
    expect(fieldType({ name: 'min-num-features', type: 'int' })).toBe('number')
    expect(fieldType({ name: 'gps-accuracy', type: 'float' })).toBe('number')
  })
  it('string/other -> text', () => {
    expect(fieldType({ name: 'name', type: 'string' })).toBe('text')
    expect(fieldType({ name: 'x', type: 'mystery' })).toBe('text')
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm test -- --run useOdmOptions`
Expected: FAIL — cannot resolve `@/composables/useOdmOptions`.

- [ ] **Step 3: Write the implementation**

Create `useOdmOptions.js`:
```js
import { ref } from 'vue'
import { fetchOptions } from '@/lib/presets'

// Map a NodeODM /options catalog entry to a form field kind. NodeODM types seen
// in practice: bool, int, float, enum (with a `domain` list), string.
export function fieldType(option) {
  const t = (option?.type || '').toLowerCase()
  if (t === 'bool') return 'checkbox'
  if (t === 'enum' || Array.isArray(option?.domain)) return 'select'
  if (t === 'int' || t === 'float') return 'number'
  return 'text'
}

export function useOdmOptions() {
  const catalog = ref([])
  const error = ref('')
  const loading = ref(false)

  async function load() {
    loading.value = true
    error.value = ''
    try {
      catalog.value = await fetchOptions()
    } catch (e) {
      error.value = e.message || 'Could not load options'
      catalog.value = []
    } finally {
      loading.value = false
    }
  }

  return { catalog, error, loading, load, fieldType }
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm test -- --run useOdmOptions`
Expected: PASS — 4 tests.

- [ ] **Step 5: Commit**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git add frontend/src/composables/useOdmOptions.js frontend/src/composables/useOdmOptions.test.js
git commit -m "feat: add useOdmOptions composable (catalog loader + field-type mapping)"
```

---

## Task 10: `Settings.vue` — real load/save + default preset + auto-start

**Files:**
- Modify: `/home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend/src/pages/Settings.vue`

**Interfaces:**
- Consumes: `getSettings`, `saveSettings`, `listPresets` from `@/lib/presets`.
- Produces: a working Settings page persisting to WebODM Settings, including a Default Preset dropdown and an Auto-start checkbox.

- [ ] **Step 1: Rewrite Settings.vue**

Replace the entire contents of `Settings.vue` with:
```vue
<template>
  <div class="p-6 space-y-6 max-w-2xl">
    <h2 class="text-lg font-semibold text-gray-900 dark:text-gray-100">Settings</h2>

    <!-- Processing -->
    <div class="bg-white dark:bg-gray-900 rounded-xl border dark:border-gray-700 p-6 space-y-4">
      <h3 class="font-medium text-gray-900 dark:text-gray-100">Processing</h3>
      <div class="space-y-3">
        <div>
          <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Default Preset</label>
          <select v-model="form.default_preset" class="w-full rounded-lg border dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 px-3 py-2 text-sm">
            <option :value="null">None</option>
            <option v-for="p in presets" :key="p.name" :value="p.name">{{ p.preset_name }}</option>
          </select>
        </div>
        <div class="flex items-center gap-2">
          <input type="checkbox" id="auto-process" v-model="form.auto_start_processing" class="rounded border-gray-300 dark:border-gray-600" />
          <label for="auto-process" class="text-sm text-gray-700 dark:text-gray-300">Auto-start processing after upload</label>
        </div>
      </div>
    </div>

    <!-- Limits -->
    <div class="bg-white dark:bg-gray-900 rounded-xl border dark:border-gray-700 p-6 space-y-4">
      <h3 class="font-medium text-gray-900 dark:text-gray-100">Limits</h3>
      <div class="space-y-3">
        <div>
          <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Max Upload Size (MB)</label>
          <input type="number" v-model.number="form.max_file_size_mb" class="w-full rounded-lg border dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 px-3 py-2 text-sm" />
        </div>
      </div>
    </div>

    <!-- Notifications -->
    <div class="bg-white dark:bg-gray-900 rounded-xl border dark:border-gray-700 p-6 space-y-4">
      <h3 class="font-medium text-gray-900 dark:text-gray-100">Notifications</h3>
      <div class="flex items-center gap-2">
        <input type="checkbox" id="email-done" v-model="form.email_notifications" class="rounded border-gray-300 dark:border-gray-600" />
        <label for="email-done" class="text-sm text-gray-700 dark:text-gray-300">Email notifications</label>
      </div>
    </div>

    <div class="flex justify-end">
      <Button variant="solid" theme="blue" :loading="saving" @click="onSave">
        <template #prefix><FeatherIcon name="check" class="h-4 w-4" /></template>
        Save Settings
      </Button>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Button, FeatherIcon, toast } from 'frappe-ui'
import { getSettings, saveSettings, listPresets } from '@/lib/presets'

const presets = ref([])
const saving = ref(false)
const form = ref({
  default_preset: null,
  auto_start_processing: false,
  max_file_size_mb: 200,
  email_notifications: true,
})

onMounted(async () => {
  try {
    presets.value = await listPresets()
  } catch (e) {
    presets.value = []
  }
  try {
    const s = await getSettings()
    form.value = {
      default_preset: s.default_preset || null,
      auto_start_processing: !!s.auto_start_processing,
      max_file_size_mb: s.max_file_size_mb ?? 200,
      email_notifications: !!s.email_notifications,
    }
  } catch (e) {
    toast.error(e.message || 'Failed to load settings')
  }
})

async function onSave() {
  saving.value = true
  try {
    await saveSettings({
      default_preset: form.value.default_preset,
      auto_start_processing: form.value.auto_start_processing ? 1 : 0,
      max_file_size_mb: form.value.max_file_size_mb,
      email_notifications: form.value.email_notifications ? 1 : 0,
    })
    toast.success('Settings saved')
  } catch (e) {
    toast.error(e.message || 'Failed to save settings')
  } finally {
    saving.value = false
  }
}
</script>
```

- [ ] **Step 2: Verify the SFC compiles**

Run (from `.../webodm_frontend/frontend`):
```bash
node -e "
const fs=require('fs');
const {parse,compileScript,compileTemplate}=require('@vue/compiler-sfc');
const src=fs.readFileSync('src/pages/Settings.vue','utf8');
const {descriptor,errors}=parse(src,{filename:'Settings.vue'});
if(errors.length){console.error('PARSE',errors);process.exit(1);}
const s=compileScript(descriptor,{id:'x'});
const t=compileTemplate({source:descriptor.template.content,filename:'Settings.vue',id:'x',compilerOptions:{bindingMetadata:s.bindings}});
if(t.errors.length){console.error('TEMPLATE',t.errors);process.exit(1);}
console.log('SFC compiles OK');
"
```
Expected: `SFC compiles OK`.

- [ ] **Step 3: Run the full frontend suite (no regression)**

Run: `npm test -- --run`
Expected: all suites pass.

- [ ] **Step 4: Commit**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git add frontend/src/pages/Settings.vue
git commit -m "feat: wire Settings page to backend with default preset + auto-start"
```

---

## Task 11: `Presets.vue` — real CRUD + dynamic options form

**Files:**
- Modify: `/home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend/src/pages/Presets.vue`

**Interfaces:**
- Consumes: `listPresets`, `savePreset`, `deletePreset` from `@/lib/presets`; `useOdmOptions` (Task 9).
- Produces: a Presets page that lists real presets, and a create/edit modal whose dynamic form is rendered from the ODM catalog and saves `options` as `[{name,value}]`.

- [ ] **Step 1: Rewrite Presets.vue**

Replace the entire contents of `Presets.vue` with:
```vue
<template>
  <div class="p-6 space-y-6">
    <div class="flex items-center justify-between">
      <h2 class="text-lg font-semibold text-gray-900 dark:text-gray-100">Processing Presets</h2>
      <Button variant="solid" theme="blue" @click="openCreate">
        <template #prefix><FeatherIcon name="plus" class="h-4 w-4" /></template>
        New Preset
      </Button>
    </div>

    <div class="bg-white dark:bg-gray-900 rounded-xl border dark:border-gray-700 overflow-hidden">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b dark:border-gray-700 text-left text-gray-500 dark:text-gray-400">
            <th class="px-4 py-3 font-medium">Name</th>
            <th class="px-4 py-3 font-medium">Options</th>
            <th class="px-4 py-3 font-medium">Scope</th>
            <th class="px-4 py-3 font-medium text-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in presets" :key="p.name" class="border-b dark:border-gray-700 last:border-0 hover:bg-gray-50 dark:hover:bg-gray-800">
            <td class="px-4 py-3 font-medium text-gray-900 dark:text-gray-100">{{ p.preset_name }}</td>
            <td class="px-4 py-3 text-gray-600 dark:text-gray-400">{{ p.options.length }} option(s)</td>
            <td class="px-4 py-3">
              <span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium"
                :class="p.system ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/50 dark:text-blue-300' : 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300'">
                {{ p.system ? 'System' : 'User' }}
              </span>
            </td>
            <td class="px-4 py-3 text-right space-x-2">
              <button class="text-gray-400 hover:text-blue-600" @click="openEdit(p)"><FeatherIcon name="edit-2" class="h-4 w-4" /></button>
              <button class="text-gray-400 hover:text-red-600" @click="onDelete(p)"><FeatherIcon name="trash-2" class="h-4 w-4" /></button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Create/Edit modal -->
    <div v-if="showModal" class="fixed inset-0 z-[10000] flex items-center justify-center bg-black/50" @click.self="showModal = false">
      <div class="bg-white dark:bg-gray-900 rounded-xl shadow-xl w-full max-w-lg mx-4 p-6 max-h-[85vh] overflow-y-auto">
        <h3 class="text-lg font-semibold mb-4 text-gray-900 dark:text-gray-100">{{ editing ? 'Edit' : 'New' }} Preset</h3>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Name</label>
        <input v-model="draft.preset_name" class="w-full rounded-lg border dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 px-3 py-2 text-sm mb-4" />

        <p v-if="odm.error.value" class="text-sm text-red-600 mb-3">{{ odm.error.value }}</p>
        <p v-else-if="odm.loading.value" class="text-sm text-gray-500 mb-3">Loading options…</p>

        <div v-else class="space-y-2">
          <div v-for="opt in odm.catalog.value" :key="opt.name" class="flex items-center gap-2">
            <label class="text-sm text-gray-700 dark:text-gray-300 w-48 truncate" :title="opt.help">{{ opt.name }}</label>
            <input v-if="odm.fieldType(opt) === 'checkbox'" type="checkbox" v-model="values[opt.name]" class="rounded" />
            <select v-else-if="odm.fieldType(opt) === 'select'" v-model="values[opt.name]" class="flex-1 rounded border dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 px-2 py-1 text-sm">
              <option v-for="d in opt.domain" :key="d" :value="d">{{ d }}</option>
            </select>
            <input v-else-if="odm.fieldType(opt) === 'number'" type="number" v-model.number="values[opt.name]" class="flex-1 rounded border dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 px-2 py-1 text-sm" />
            <input v-else type="text" v-model="values[opt.name]" class="flex-1 rounded border dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 px-2 py-1 text-sm" />
          </div>
        </div>

        <div class="flex justify-end gap-2 mt-6">
          <Button variant="ghost" @click="showModal = false">Cancel</Button>
          <Button variant="solid" theme="blue" :loading="saving" @click="onSave">Save</Button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { Button, FeatherIcon, toast } from 'frappe-ui'
import { listPresets, savePreset, deletePreset } from '@/lib/presets'
import { useOdmOptions } from '@/composables/useOdmOptions'

const presets = ref([])
const showModal = ref(false)
const editing = ref(null)
const saving = ref(false)
const draft = reactive({ preset_name: '' })
const values = ref({})
const odm = useOdmOptions()

async function refresh() {
  try {
    presets.value = await listPresets()
  } catch (e) {
    toast.error(e.message || 'Failed to load presets')
  }
}
refresh()

async function openCreate() {
  editing.value = null
  draft.preset_name = ''
  values.value = {}
  showModal.value = true
  await odm.load()
}

async function openEdit(p) {
  editing.value = p
  draft.preset_name = p.preset_name
  values.value = Object.fromEntries((p.options || []).map(o => [o.name, o.value]))
  showModal.value = true
  await odm.load()
}

// Serialize only the options the user set to a non-empty / non-false value,
// into NodeODM's [{name, value}] array form.
function toOptionsArray() {
  const out = []
  for (const [name, value] of Object.entries(values.value)) {
    if (value === '' || value === null || value === undefined || value === false) continue
    out.push({ name, value })
  }
  return out
}

async function onSave() {
  if (!draft.preset_name) { toast.error('Name is required'); return }
  saving.value = true
  try {
    await savePreset({
      name: editing.value?.name || null,
      preset_name: draft.preset_name,
      options: toOptionsArray(),
      system: editing.value?.system || 0,
    })
    toast.success('Preset saved')
    showModal.value = false
    await refresh()
  } catch (e) {
    toast.error(e.message || 'Failed to save preset')
  } finally {
    saving.value = false
  }
}

async function onDelete(p) {
  try {
    await deletePreset(p.name)
    toast.success('Preset deleted')
    await refresh()
  } catch (e) {
    toast.error(e.message || 'Failed to delete preset')
  }
}
</script>
```

- [ ] **Step 2: Verify the SFC compiles**

Run (from `.../webodm_frontend/frontend`):
```bash
node -e "
const fs=require('fs');
const {parse,compileScript,compileTemplate}=require('@vue/compiler-sfc');
const src=fs.readFileSync('src/pages/Presets.vue','utf8');
const {descriptor,errors}=parse(src,{filename:'Presets.vue'});
if(errors.length){console.error('PARSE',errors);process.exit(1);}
const s=compileScript(descriptor,{id:'x'});
const t=compileTemplate({source:descriptor.template.content,filename:'Presets.vue',id:'x',compilerOptions:{bindingMetadata:s.bindings}});
if(t.errors.length){console.error('TEMPLATE',t.errors);process.exit(1);}
console.log('SFC compiles OK');
"
```
Expected: `SFC compiles OK`.

- [ ] **Step 3: Run the full frontend suite (no regression)**

Run: `npm test -- --run`
Expected: all suites pass.

- [ ] **Step 4: Commit**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git add frontend/src/pages/Presets.vue
git commit -m "feat: wire Presets page to backend with dynamic ODM options form"
```

---

## Task 12: `MapView.vue` upload dialog — preset picker + dynamic form + auto-start

**Files:**
- Modify: `/home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend/frontend/src/pages/MapView.vue`

**Interfaces:**
- Consumes: `listPresets`, `getSettings` from `@/lib/presets`; `useOdmOptions` (Task 9). Existing: `outputOpts` (removed), `uploadFiles`, `route`, `toast`, `Button`, `FeatherIcon`.
- Produces: the upload dialog preselects the settings default preset, renders an editable dynamic options form seeded from the chosen preset, and sends `options` as `[{name,value}]`. Auto-start is handled server-side (Task 7), so the frontend just refreshes tasks.

- [ ] **Step 1: Replace the imports and reactive state**

In `MapView.vue` `<script setup>`, add near the other `@/lib` / composable imports:
```js
import { listPresets, getSettings } from '@/lib/presets'
import { useOdmOptions } from '@/composables/useOdmOptions'
```
Remove the line:
```js
const outputOpts = ref({ orthophoto: true, dsm: false, dtm: false, model: false, pointCloud: false, orthophotoResolution: null })
```
and add in its place:
```js
const uploadPresets = ref([])
const selectedPreset = ref(null)
const uploadValues = ref({})   // { optionName: value }
const uploadOdm = useOdmOptions()

async function loadUploadForm() {
  try {
    uploadPresets.value = await listPresets()
  } catch (e) {
    uploadPresets.value = []
  }
  try {
    const s = await getSettings()
    selectedPreset.value = s.default_preset || null
  } catch (e) {
    selectedPreset.value = null
  }
  await uploadOdm.load()
  applyPreset()
}

function applyPreset() {
  const p = uploadPresets.value.find(x => x.name === selectedPreset.value)
  uploadValues.value = p ? Object.fromEntries((p.options || []).map(o => [o.name, o.value])) : {}
}

function uploadOptionsArray() {
  const out = []
  for (const [name, value] of Object.entries(uploadValues.value)) {
    if (value === '' || value === null || value === undefined || value === false) continue
    out.push({ name, value })
  }
  return out
}
```

- [ ] **Step 2: Load the form when the dialog opens**

Find where `showUpload` is set to `true` (the "Add Task" button handler, `@click="showUpload = true"` in the sidebar template around line 71). Change it to call a method. In the template, replace `@click="showUpload = true"` with `@click="openUpload"`, and add this function in the script:
```js
function openUpload() {
  showUpload.value = true
  loadUploadForm()
}
```

- [ ] **Step 3: Replace the Outputs block in the upload dialog with the preset picker + dynamic form**

In the upload dialog template, replace the entire `<div class="mt-4 space-y-2">` Outputs block (the `<p>Outputs</p>` paragraph and the five `<label>` checkboxes plus the resolution `<div>`, currently lines ~184-197) with:
```html
        <div class="mt-4 space-y-2">
          <label class="block text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">Preset</label>
          <select v-model="selectedPreset" @change="applyPreset" class="w-full rounded-lg border dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 px-3 py-2 text-sm">
            <option :value="null">None (defaults)</option>
            <option v-for="p in uploadPresets" :key="p.name" :value="p.name">{{ p.preset_name }}</option>
          </select>

          <p v-if="uploadOdm.error.value" class="text-xs text-red-600">{{ uploadOdm.error.value }}</p>
          <p v-else-if="uploadOdm.loading.value" class="text-xs text-gray-500">Loading options…</p>
          <div v-else class="max-h-64 overflow-y-auto space-y-1.5 pr-1">
            <div v-for="opt in uploadOdm.catalog.value" :key="opt.name" class="flex items-center gap-2">
              <label class="text-xs text-gray-600 dark:text-gray-400 w-40 truncate" :title="opt.help">{{ opt.name }}</label>
              <input v-if="uploadOdm.fieldType(opt) === 'checkbox'" type="checkbox" v-model="uploadValues[opt.name]" class="rounded" />
              <select v-else-if="uploadOdm.fieldType(opt) === 'select'" v-model="uploadValues[opt.name]" class="flex-1 rounded border dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 px-2 py-1 text-xs">
                <option v-for="d in opt.domain" :key="d" :value="d">{{ d }}</option>
              </select>
              <input v-else-if="uploadOdm.fieldType(opt) === 'number'" type="number" v-model.number="uploadValues[opt.name]" class="flex-1 rounded border dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 px-2 py-1 text-xs" />
              <input v-else type="text" v-model="uploadValues[opt.name]" class="flex-1 rounded border dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 px-2 py-1 text-xs" />
            </div>
          </div>
        </div>
```

- [ ] **Step 4: Send the options array in `uploadFiles`**

In `uploadFiles` (~line 606), replace:
```js
    formData.append('options', JSON.stringify(outputOpts.value))
```
with:
```js
    formData.append('options', JSON.stringify(uploadOptionsArray()))
```

- [ ] **Step 5: Verify the SFC compiles and no stale `outputOpts` remains**

Run (from `.../webodm_frontend/frontend`):
```bash
grep -n "outputOpts" src/pages/MapView.vue || echo "no outputOpts references (good)"
node -e "
const fs=require('fs');
const {parse,compileScript,compileTemplate}=require('@vue/compiler-sfc');
const src=fs.readFileSync('src/pages/MapView.vue','utf8');
const {descriptor,errors}=parse(src,{filename:'MapView.vue'});
if(errors.length){console.error('PARSE',errors);process.exit(1);}
const s=compileScript(descriptor,{id:'x'});
const t=compileTemplate({source:descriptor.template.content,filename:'MapView.vue',id:'x',compilerOptions:{bindingMetadata:s.bindings}});
if(t.errors.length){console.error('TEMPLATE',t.errors);process.exit(1);}
console.log('SFC compiles OK');
"
```
Expected: `no outputOpts references (good)` and `SFC compiles OK`.

- [ ] **Step 6: Run the full frontend suite (no regression)**

Run: `npm test -- --run`
Expected: all suites pass.

- [ ] **Step 7: Commit**

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench/apps/webodm_frontend
git add frontend/src/pages/MapView.vue
git commit -m "feat: upload dialog uses preset picker + dynamic ODM options form"
```

---

## Manual Verification (after all tasks)

With a processing node online, the geospatial service running, and `npm run dev`:
1. **Presets page:** create a preset (e.g. name "High Quality", check `dsm`, set `feature-quality: ultra`), save; it appears in the list with the right option count. Edit it; values reload. Delete a user preset.
2. **Settings page:** set Default Preset = "High Quality", enable Auto-start, Save; reload the page and confirm both persisted.
3. **Upload:** open Add Task — the preset dropdown is preselected to "High Quality" and the dynamic form is seeded from it. Override a field, upload images. Confirm the task's `processing_options` (JSON, array form) reflects the merged options and, with auto-start on, the task begins processing without clicking Start. Check the ODM init log reflects the chosen options (e.g. `dsm: True`, `feature_quality: ultra`).
4. **Node offline:** stop the node, open the preset editor — it shows "Processing node offline — can't load options" and the dynamic form is disabled; existing presets still list.
