# Design: Dataset Library

## Context

- Inputs were `WebODM Task Image` rows on `WebODM Task.images`, with the
  private `File` attached to the task and (when a bucket is configured) an
  object under `orgs/<slug>/tasks/<task>/inputs/`. `storage.assets`
  (`sync_inputs`, `input_sources`, `sync_pending`), `storage.cache`
  (`storage_key_for_file_url`, `_candidates`) and `task_runner._get_task_images`
  all iterated `task.images` and resolved Files against the task.
- `WebODMTask.on_trash` deleted the whole task prefix — including `inputs/`.
- `reset_outputs` already spared `inputs/`; that becomes a cross-task
  guarantee once inputs are shared.
- Tenancy: every DocType with an `organization` field must be in both
  permission hook maps and stamped in `before_insert`
  (`api/test_tenant_doctype_coverage.py`).

## Decisions

### One dataset per upload, referenced by name

`WebODM Task.dataset` is a plain Link. Nothing is copied when a second task
uses a dataset; both read the same rows, Files and objects. The dataset is
the owner of the images in every sense: Files are attached to it, its rows
carry the keys, and only its `on_trash` deletes them.

### Images are immutable; datasets are small documents

`WebODMDataset._check_images_fixed` compares the `(row name, image)` list
against `get_doc_before_save()` and refuses any difference. Two callers
legitimately save an empty shell first and append rows in the same request
— the upload path (Files must be attached to the real name) and the
backfill of a legacy task without images — and use `flags.allow_empty` /
`flags.images_fixed = False`. `storage_key` is written with `db_set` and
never passes through `validate`.

### Task ↔ dataset organization check

`WebODMTask.validate` requires the dataset's organization to equal the
task's. `organization` is stamped in `before_insert`, which Frappe runs
before `validate`, so the check is against the actor's org, not the
payload. `input_sources` keeps `assert_org_key(key, task.organization)`.

### Deletion contract

- `storage.task_output_prefixes(task)` = `raw/` + `assets/`; both
  `delete_task_objects` and `reset_outputs` use it. `inputs/` is never
  derived from a task.
- `storage.assets.delete_dataset_objects` deletes by the key on each row,
  checking `assert_org_key` first and skipping (logging) a tampered key —
  works for `datasets/<id>/inputs/` and migrated `tasks/<task>/inputs/`.
- `WebODMDataset.on_trash` raises `DatasetInUse` naming the tasks *before*
  Frappe's link check (Frappe runs `on_trash` first and `force=True` skips
  only the link check, so the guard holds for every client).
- `datasets.remove_image_blobs` removes each image's on-disk blob
  explicitly: Frappe's `File._delete_file_on_disk` keeps a blob when another
  File shares its `content_hash`, but our streamed uploads never share a
  blob, so identical photos uploaded twice would otherwise leak an orphan
  the eviction job cannot see.

### Object storage

Key layout gains `datasets/<id>/inputs/`; the task layout keeps `raw/` and
`assets/`. Migrated rows keep `tasks/<task>/inputs/` keys — valid because
`assert_org_key` only enforces the `orgs/<slug>/` namespace. `sync_inputs`
skips rows that already have a key, which is exactly what keeps those keys
in place. `_candidates` marks a dataset image busy when any busy task's
`dataset` equals its parent.

### Thumbnails

`datasets.ensure_thumbnail` resolves the original through `cache.ensure_local`
(cache first, S3 second), decodes with Pillow's JPEG `draft` mode (the
decoder returns the smallest DCT scale that covers the target, so a 20 MP
frame is reduced 1/8 while decoding), honours EXIF orientation and writes a
plain JPEG to `sites/<site>/private/thumbnails/<dataset>/<row>_<size>.jpg`.
Sizes snap to `{128, 256, 512}` so the disk cache and browser cache stay
bounded. The endpoint returns a werkzeug `send_file` Response (conditional
GET, `Cache-Control: private, max-age=7d`); thumbnails are derived data, not
Files, so eviction ignores them and the dataset delete removes the folder.

### Task payload

`get_task_progress` (and `upload_images`) return `task.as_dict()` plus
`dataset_summary` and `images` (dataset rows + `thumbnail` URL), so the map
view's thumbnails / GPS markers and the Console's image count keep working.
`list_tasks(project_id)` replaces the frontend's `/api/resource` listing and
joins the dataset summaries in one query.

### Migration in stages

1. Model sync adds the DocTypes and `WebODM Task.dataset`. Frappe never adds
   `NOT NULL` for a Link, so `reqd: 1` is enforced at save time only and
   existing rows are untouched.
2. `patches.backfill_task_datasets` (post_model_sync, own transaction):
   reads `tabWebODM Task Image` with plain SQL (works after the DocType row
   is gone), inserts one dataset per dataset-less task with
   `flags.organization_from_source` (the stamping hook honours it only under
   `frappe.flags.in_patch`/`in_migrate`), moves the image Files by URL,
   sets `task.dataset`. Idempotent by construction; `report()` previews,
   `rollback(names)` re-attaches Files for a code revert.
3. `patches.drop_task_image_table` verifies (every task has an existing
   dataset, dataset rows ≥ legacy rows per task, no image File still on a
   task) and only then drops the table; a failure raises and aborts the
   migrate with the backfill already committed. Frappe's own
   `remove_orphan_doctypes` deletes the `WebODM Task Image` DocType row when
   the module folder is gone, but keeps the table — hence the explicit drop.

### File naming

`plugins.files._safe_private_name` now also checks the `File` table: Frappe's
`generate_file_name` only looks at the disk, and since `private/files` is a
cache, an evicted blob's URL would otherwise be reused by a new upload
(hijacking it for a different owner or letting a later cache fill overwrite
the new upload).

## Alternatives considered

- Keep `WebODM Task Image` and add a dataset link on tasks lazily — leaves
  two sources of truth for inputs and two deletion paths; rejected.
- Copy rows per task ("materialised dataset") — defeats the storage saving
  and reintroduces the per-task deletion hazard; rejected.
- Client-side thumbnails (browser downscales the original) — the browser
  still downloads the original; rejected.
- Drop the legacy table in the same patch as the backfill — no verification
  point between the additive and destructive steps; rejected.
