# Migrating an existing site to the dataset library

Before the library a task owned its images (`WebODM Task.images` →
`WebODM Task Image`). The migration turns every existing task's images into
a dataset the task references. It is staged so each destructive step happens
only after the previous one has been verified, and the early steps are
additive so reverting the code leaves the data usable.

## What `bench migrate` does

Everything runs from one `bench --site <site> migrate`, in this order:

1. **Model sync (additive).** Creates `WebODM Dataset` and
   `WebODM Dataset Image`, adds `WebODM Task.dataset`, removes the `images`
   table field from the task. The `tabWebODM Task Image` table and its rows
   are untouched (Frappe never drops a table on sync). `dataset` is marked
   required, but Frappe enforces `reqd` on save only — existing rows without
   it are not touched and keep loading.
2. **`webodm_core.patches.backfill_task_datasets` (additive, idempotent).**
   For every task with no `dataset`:
   - creates a `WebODM Dataset` in the task's organization with the task's
     title, description `Migrated from task <name>`, `created_by`/`owner`
     and `creation` copied from the task;
   - copies each legacy image row verbatim (file, filename, size,
     **existing `storage_key`**, latitude, longitude, altitude, capture
     time) — nothing is copied or moved in the bucket;
   - re-attaches the image `File` documents from the task to the dataset
     (matched by URL, so output Files such as the orthophoto stay on the
     task);
   - sets `task.dataset`.
   A legacy task that never had images gets an empty dataset (the only way
   an empty dataset can exist; it can still be renamed and deleted). The
   patch runs in its own transaction, so a failure rolls the whole backfill
   back; re-running it only processes tasks still without a dataset.
3. **`webodm_core.patches.drop_task_image_table` (destructive, verified).**
   Re-checks that every task has an existing dataset, that each dataset has
   at least as many rows as the legacy table holds for its task, and that no
   image File is still attached to a task. If any check fails it raises,
   listing the offending tasks, and the migrate stops **with the backfill
   already committed and the legacy table intact**. Only when everything
   passes does it `DROP TABLE "tabWebODM Task Image"`.
4. Frappe's own `remove_orphan_doctypes` deletes the `WebODM Task Image`
   DocType row (its module folder is gone). On its own that step keeps the
   table, which is why step 3 exists.

On a fresh site (no legacy table) steps 2–3 are no-ops.

## Recommended rollout

```sh
# 0. back up
bench --site <site> backup --with-files

# 1. preview what the backfill will do (read-only)
bench --site <site> execute webodm_core.patches.backfill_task_datasets.report

# 2. migrate (runs sync → backfill → verified drop)
bench --site <site> migrate

# 3. spot-check
bench --site <site> execute frappe.db.count --args '["WebODM Dataset"]'
bench --site <site> execute frappe.db.sql --args '["select count(*) from \"tabWebODM Task\" where dataset is null"]'
```

If step 2 stops in `drop_task_image_table`, the site is fully functional on
the new code (tasks read their images through the datasets the backfill
created). Fix the listed tasks — usually by re-running
`backfill_task_datasets.execute` after correcting a task without an
organization — and run `bench migrate` again; only the drop step re-runs.

To run the drop's checks by hand without dropping anything:

```sh
bench --site <site> execute webodm_core.patches.drop_task_image_table.verify
```

## Rollback

- **Before the drop step has run** (or on a site where it refused): revert
  the code and `bench migrate`. The legacy rows are still in
  `tabWebODM Task Image`; the old code reads them again. The one data change
  the backfill made is the File re-attachment; undo it with

  ```sh
  bench --site <site> execute webodm_core.patches.backfill_task_datasets.rollback
  ```

  (pass `--kwargs '{"names": [...]}'` to limit it to specific datasets).
  The `WebODM Dataset` rows and `task.dataset` values may stay — old code
  ignores them, and a later re-run of the backfill skips those tasks.
- **After the drop step**: the legacy table is gone; restore from the
  backup taken in step 0. This is the one non-reversible step, which is why
  it verifies first and runs last.

## Object keys after migration

Migrated dataset images keep `orgs/<slug>/tasks/<task>/inputs/...` keys.
Nothing needs to move: the organization prefix is what the boundary check
enforces, `sync_inputs` skips rows that already have a key, dispatch streams
them as before, and `delete_dataset_objects` deletes by the recorded key.
Deleting the *task* those keys were named after does **not** delete them
(task deletion only removes `raw/` and `assets/`).

## Verifying on a copy

The change was exercised on the throwaway test site with seeded pre-library
tasks (3, 2 and 0 images; one row per task already carrying a task-scoped
key): the backfill produced one dataset per task with matching titles,
owners, counts and sizes, copied the keys and photo details, moved exactly
the image Files, set `task.dataset`, and the drop step removed the table
only after verification. `webodm_core/patches/test_backfill_task_datasets.py`
recreates the legacy table with SQL and covers the same path, including the
refusal cases and idempotency.
