# Presets & Settings Wiring — Design

**Date:** 2026-07-21
**Status:** Approved (design)
**Scope:** Wire the placeholder **Presets** and **Settings** pages to the backend
so that saved processing presets and global settings apply as NodeODM/ODM
processing options when a task is created.

## 1. Overview

WebODM's `Presets.vue` and `Settings.vue` are currently pure placeholders with
hardcoded mock data (routed and in the nav, but no backend calls). Two DocTypes
already exist with schema but **zero wiring**:

- **WebODM Preset** (not single): `preset_name` (autoname via `field:preset_name`),
  `owner` (Link→User), `system` (Check), `options` (JSON).
- **WebODM Settings** (single): `default_basemap` (Link→WebODM Basemap),
  `enable_public_sharing`, `max_file_size_mb`, `max_project_count`,
  `slack_webhook_url`, `email_notifications`. **No processing fields yet.**

This feature makes a preset able to store **any valid ODM option** (rendered from
NodeODM's live `/options` catalog), lets the user pick a preset at upload and
override any field, and lets Settings define a **default preset** and
**auto-start** behavior applied to every new task.

Decisions locked during design:

- **Preset richness:** full dynamic ODM options (fetched from NodeODM `/options`),
  not a fixed subset. Matches real WebODM.
- **Apply model:** pick a preset, then override — the upload dialog seeds an
  editable dynamic form from the preset; the merged result is stored on the task.
- **Settings' role:** add `default_preset` (Link→WebODM Preset) and
  `auto_start_processing` (Check) to WebODM Settings, plus wire the existing
  Settings fields to save/load.
- **Options catalog source:** proxy live from the processing node's `GET /options`
  (session-authed, like the tile proxy). No caching, no bundled static catalog.
- **Permissions:** a user sees their own presets + `system` presets; only Admins
  create/edit `system` presets; a user manages only their own. Enforced
  server-side.

## 2. Architecture & Data Flow

```
Preset editor / Upload dialog
  └─GET─> webodm_core.api.presets.options()          (new, whitelisted)
            └─> NodeODMClient.get_options()            (new: GET /options)
                  └─> processing node → ODM option catalog
                        [{name, type, value(default), domain, help}, ...]

Presets.vue
  ├─GET─>  api.presets.list_presets()      → user's own + system presets
  ├─POST─> api.presets.save()      → create/update {preset_name, options[], system?}
  └─POST─> api.presets.delete()

Settings.vue
  ├─GET─>  api.settings.get()      → WebODM Settings single doc
  └─POST─> api.settings.save()

Task creation (MapView upload dialog)
  1. load settings.default_preset → preselect it
  2. preset dropdown → on pick, load preset.options[] into an editable dynamic form
  3. user overrides any field
  4. upload_images stores merged options[] on Task.processing_options (JSON)
  5. if settings.auto_start_processing → auto-fire process_task
        └─> task_runner: options already in NodeODM array form → create_task (no remap)
```

**Catalog-driven form:** the `/options` catalog drives field rendering — boolean →
checkbox, enum (`domain` is a list) → dropdown, int/float → number, string → text.

**Storage shape:** presets store options **already in NodeODM's array form**
`[{name, value}, ...]`. So preset-driven tasks need no translation:
`_build_node_options` becomes a pass-through for the list shape, keeping its current
5-key dict translation only as a fallback for the legacy simple-checkbox path.

**Why this shape:** it reuses the existing session-authed proxy pattern
(`tiles.py`), keeps the browser free of node credentials, stores options in the
exact form NodeODM consumes (no lossy remap), and leaves the legacy upload path
working.

## 3. Components & Interfaces

### 3.1 Backend — `webodm_core/api/presets.py` (new)

```python
@frappe.whitelist()
def options():
    """Proxy the processing node's GET /options catalog (session-authed).

    Picks the configured WebODM Processing Node, calls
    NodeODMClient.get_options(), returns the list. On NodeODMError, frappe.throw
    a clear "processing node offline" message.
    """

@frappe.whitelist()
def list_presets():
    """Presets visible to the session user: own (owner == user) + system.

    Returns [{name, preset_name, options, system, owner}]. options is parsed
    from JSON to a list.
    """

@frappe.whitelist()
def save(preset_name, options, system=0, name=None):
    """Create or update a preset. options is a JSON string of [{name, value}].

    Non-admins cannot create/edit system presets and can only edit their own
    (owner == session user); otherwise frappe.throw(PermissionError). New
    user presets get owner = session user, system = 0.
    """

@frappe.whitelist()
def delete(name):
    """Delete a preset the user owns; Admins may delete any. Else PermissionError."""
```

### 3.2 Backend — `webodm_core/api/settings.py` (new)

```python
@frappe.whitelist()
def get():
    """Return the WebODM Settings single doc as a dict. Tolerates a dangling
    default_preset link (returns the stored value regardless)."""

@frappe.whitelist()
def save(**fields):
    """Update allowed fields on the WebODM Settings single doc. Only the known
    field set is written; unknown keys ignored."""
```

### 3.3 Backend — `node_client.py` (extend)

```python
def get_options(self) -> list:
    return self._get("options")   # NodeODM GET /options
```

### 3.4 DocType change — WebODM Settings (+2 fields)

- `default_preset`: Link → WebODM Preset
- `auto_start_processing`: Check

(Added via the DocType JSON + a migration, per Frappe conventions.)

### 3.5 Backend — `task_runner.py` (`_build_node_options`)

- Signature: `_build_node_options(opts: dict | list) -> list[dict]`.
- If `opts` is a **list** (preset/dynamic path): pass through verbatim (it is
  already `[{name, value}]`).
- If `opts` is a **dict** (legacy 5-checkbox path): current translation logic,
  unchanged.

### 3.6 Frontend

- **`src/composables/useOdmOptions.js`** (new): fetches the `/options` catalog,
  exposes `{ catalog, load, fieldType(option) }` where `fieldType` maps a catalog
  entry to `'checkbox' | 'select' | 'number' | 'text'`.
- **`src/lib/presets.js`** (new): fetch wrappers for
  `options()/list_presets()/save()/delete()` and `settings.get()/save()`, with
  CSRF headers and Frappe `{message}` envelope unwrapping (mirrors existing calls).
- **`Presets.vue`**: replace mock data with real `list_presets()`; a New/Edit
  modal containing the dynamic options form (rendered from the catalog); delete
  action.
- **`Settings.vue`**: load/save real settings; add a Default Preset dropdown
  (from `list_presets()`) and an Auto-start checkbox; wire existing fields.
- **`MapView.vue`** upload dialog: a preset dropdown (preselecting
  `settings.default_preset`) → an editable dynamic options form seeded from the
  chosen preset → stores the merged `options[]` as the task's `processing_options`;
  auto-fires `process_task` when `settings.auto_start_processing` is set. The 5
  legacy output checkboxes are replaced by the dynamic form.

## 4. Error Handling & Edge Cases

- **Node offline when fetching `/options`:** `api.presets.options()` catches
  `NodeODMError` and returns/raises a clear message; the preset editor and upload
  dialog show "Processing node offline — can't load options" and disable the
  dynamic form. Existing presets still list and apply (their stored `options[]`
  don't require the catalog).
- **Default preset deleted but still referenced by Settings:** on task creation,
  fall back to no preset (empty form). `settings.get()` tolerates the dangling
  link.
- **Unknown option name in a stored preset (ODM version drift):** passed through
  to NodeODM, which ignores unknown options via its `filterOptions` — no crash. We
  do not hard-validate options against the catalog on save (catalog may be
  offline); type validation happens only in the UI form where the catalog is
  loaded.
- **Permissions:** non-admin saving/deleting a `system` preset or another user's
  preset → `frappe.throw(PermissionError)`. Enforced server-side, not just hidden.
- **Auto-start with no node / zero images:** reuses the existing `process_task`
  path, which already degrades gracefully; the task stays Pending, no data loss.
- **Legacy tasks** with dict-shaped `processing_options`: still work via the
  fallback branch in `_build_node_options`.

## 5. Testing

- **Frappe (bench run-tests):**
  - `_build_node_options` (`test_task_runner.py`): a list passes through verbatim;
    a dict still translates (the existing 9 cases stay green).
  - `presets` (`test_presets.py`): `list` returns own + system; a non-admin
    cannot write a `system` preset or another user's (PermissionError);
    `save`→`list` round-trips `options[]`; `delete` of own preset works.
  - `settings` (`test_settings.py`): single-doc `get`/`save` round-trip; a
    dangling `default_preset` is tolerated by `get`.
- **Frontend (Vitest):** `presets.js` wrappers (CSRF header present, envelope
  unwrapped); `useOdmOptions.fieldType` mapping (bool→checkbox, domain→select,
  int/float→number, string→text).
- **Manual/E2E (user):** create a preset with mixed options; set it as default +
  auto-start in Settings; upload images → dialog preselects the preset, override a
  field, confirm the task's `processing_options` and the ODM init log reflect the
  merged options.

## 6. Out of Scope (YAGNI)

- Plugins / Landing / Invoices pages (remain placeholders).
- Per-project preset defaults.
- Import/export of presets.
- Server-side validation of options against the live catalog on save.
- Caching or bundling the `/options` catalog.

## 7. Dependencies & Migration

- **No new runtime dependencies.** Reuses `NodeODMClient`, the session-authed
  proxy pattern, and the existing fetch/CSRF pattern in the frontend.
- **DocType migration:** two new fields on WebODM Settings (`default_preset`,
  `auto_start_processing`) via `bench migrate`. WebODM Preset already exists as-is.
- **New files:** `webodm_core/api/presets.py`, `webodm_core/api/settings.py`
  (+ their tests); `src/composables/useOdmOptions.js`, `src/lib/presets.js`.
  **Modified:** `node_client.py`, `task_runner.py`, WebODM Settings JSON,
  `Presets.vue`, `Settings.vue`, `MapView.vue`.
