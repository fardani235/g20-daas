# Bringing imagery in and managing a task afterwards

This is the design record and user guide for four things a self-hosted WebODM
user expects at the moment they upload a survey and in the hours after a task
exists: **ground control with the imagery**, **an honest upload** (drag and
drop, a real progress bar, cancel), **editing a task's settings after the
fact**, and **duplicating a task**. All four are additive: nothing that existed
before behaves differently.

The normative behaviour lives in `openspec/specs/task-management/spec.md` and
the ground-control requirement in `openspec/specs/datasets/spec.md`; the
change proposal is `openspec/changes/archive/2026-10-06-add-task-management-essentials/`.

## The model, briefly

```
WebODM Project ─< WebODM Task ──(dataset, required)──> WebODM Dataset
                  │  title, status, processing_options,    │  title, images (fixed),
                  │  outputs, counters, node ids …         │  gcp_file (optional, replaceable)
                  └── Pending → Queued → Provisioning → Running → Completed | Failed | Cancelled
```

Images are **not** uploaded onto a task. They form a dataset — a reusable,
organization-scoped, fixed set — and any number of tasks point at it. A task
owns its settings and everything a run produces; a dataset owns the inputs.
That split is what makes the four features below cheap: a duplicate is a new
pointer plus a copy of the settings, and ground control attached once reaches
every task over the same photos.

## 1. Ground control

### What you can do

- Attach a `gcp_list.txt` **with the upload** (Datasets page or the task
  dialog's *Upload new images*): pick it in the *Ground control* field, or drop
  it with the photos — the drop zone recognises a `.txt` among images.
- Attach, **replace or remove it later** on the dataset page.
- See what was parsed: CRS, number of points, image names referenced, and
  which of those names are **not in the dataset** (ODM matches names
  exactly); a warning when there are fewer than three points.
- Every task over the dataset — new, re-run, duplicated — sends it to the
  engine. The task card shows a *GCP* mark, the console header the file name
  and point count.
- To process the same images **without** the GCP, enable ODM's `use-exif`
  option on that task (it is in the options catalog already).

### Decision: the GCP lives on the dataset, not on the task

The ground control file is a list of measurements *about the images*: every
row names an image file and a pixel in it. Detached from the image set it is
meaningless, and the same file is correct for every task that processes those
images. Putting it on the task would mean re-attaching it to every copy and
re-run (the first thing an experienced user would forget on a duplicate), and
would put "which GCP did this run use?" on the task while the photos it
describes live elsewhere. On the dataset it is uploaded once, travels with
the images through the same storage pipeline (private File, object key,
cache fill, backfill, deletion) and reaches the node with them.

The trade-off is that a dataset carries one GCP file: two tasks over one
dataset cannot use two different GCP files. That case is rare (a corrected
file replaces the old one; "with vs. without" is `use-exif`), and when it is
real, a second dataset is the honest answer — the images *are* being
georeferenced differently.

### Decision: the GCP may change after the dataset exists

Datasets are deliberately fixed: the rule guarantees that every task over a
dataset processed the same pixels, and it keeps object keys, thumbnails and
the migration simple. The GCP file is a few kilobytes of metadata about those
pixels. Freezing it would force a user who forgot it, or whose surveyor sent
a corrected file, to re-upload gigabytes to add a text file — precisely the
seam this work is meant to remove. So the images stay fixed and the GCP is the
one input that can be attached, replaced or removed later. Consequences, all
documented in the UI:

- Tasks dispatched **after** the change use the new file. A task already
  Running keeps the file it was given; the console shows what ODM used.
- The dataset record describes the *current* file, not every past run.
  Recording the file used per task is a possible follow-up (`gcp_hash` at
  dispatch); it was left out to keep the task schema untouched.
- If you would rather forbid changes once a task has run, the guard is one
  line in `api.dataset.set_gcp` / `remove_gcp` (refuse when
  `datasets.referencing_tasks(name)` is non-empty).

### Decision: reuse NodeODM's existing contract

NodeODM has no "GCP" endpoint. It decides what a file *is* from its name: any
`.txt` uploaded among the images is passed to ODM as `--gcp` (`geo.txt` is an
image-geolocation file, `align.*` an alignment reference). Self-hosted WebODM
uses exactly this. So dispatch appends the GCP as one more part of the
`/task/new/upload/<uuid>` loop, **always named `gcp_list.txt`** whatever the
user called it, so a user's `geo.txt` can never be misread as a different
sidecar. The only client change is the part's content type (`text/plain`
for `.txt`, cosmetic).

### Decision: validate the format, not the survey

The parser (`webodm_core/gcp.py`) refuses what is *not the format* — empty
or binary content, a missing CRS line, fewer than six columns, non-numeric
coordinates, a `geo.txt`-shaped file — with the offending line number, and
for uploads it runs **before any photo is written**, so a bad file costs
nothing. It does **not** second-guess the survey: an unusual CRS string, a
point in an image the dataset lacks or too few points are reported in the
summary and shown as warnings, and the engine gets the final say, as in
WebODM.

### Decision: an unreadable GCP fails the task

If the GCP file can be read neither from the cache nor from object storage
at dispatch (lost volume, no bucket), the task is marked Failed with a message
saying so. Silently processing without it would hand the user a result that
answers a different question than the one asked, and they could not tell
until they measured it. A missing *image* is still skipped and logged, as
before.

## 2. Upload progress and drag-and-drop

### What you can do

Both upload dialogs (new dataset, new task from uploads) have a **drop
zone**: drag photos, or a whole folder, or click to choose (a folder picker
is offered where the browser supports it). A `gcp_list.txt` in the drop lands
in the ground-control field. Repeated drops merge without duplicates;
non-image files are counted and skipped. While uploading, a **progress bar**
shows bytes sent of total, average speed, estimated time left and a
percentage; when the last byte is out it switches to an indeterminate
*Finishing up* state ("the server is registering the images…"), because that
is what is happening — EXIF is read and rows are written before the response
comes back. **Cancel** aborts the request; a cancelled upload creates
nothing (the server deletes a half-built dataset).

### Decision: XMLHttpRequest behind the same endpoints

`fetch` cannot report upload progress in today's browsers. The two multipart
calls (`create_dataset`, `upload_images`) now go through a small
XMLHttpRequest wrapper (`lib/upload.js: uploadForm`) that sends the same
FormData to the same URL with the same CSRF header and unwraps the same
`message` / error envelope, so no backend endpoint changed and the JSON
client is untouched. Progress is `xhr.upload.onprogress`.

### Decision: no chunked or resumable upload

Explicitly out of scope. A multi-gigabyte upload still fails as a whole on a
dropped connection and must be restarted. Resumable uploads need either a
chunk protocol on the server or direct-to-bucket presigned multipart uploads;
both are real design work and neither was asked for. The progress bar is
honest about what the single request is doing, which was the ask.

## 3. Editing a task's settings

### What you can do

*Edit* on a task card opens the task's title, the presets and the full ODM
option catalog, pre-filled with the task's current options. **Save** stores
them; **Save and start** (Pending) / **Save and re-run** (Completed, Failed,
Cancelled) also starts processing. Re-running uses the existing restart path,
which wipes the previous outputs first and resets the retry counters.

### Decision: allowed in Pending, Completed, Failed, Cancelled; refused in Queued, Provisioning, Running

Queued, Provisioning and Running are the window between the user pressing
Start and the node finishing: the dispatch sweep reads the options when it
hands the task over, and after that the node has them. Editing in Queued
would race the sweep (the user cannot tell which version ran); editing in
Running would describe a run that is happening with other settings. The
endpoint refuses with "cancel it before changing its settings"; the card
disables the button with the same reason. Cancel first, then edit, then
re-run — the same sequence WebODM users already know.

### Decision: saving never starts or resets anything

A Completed task keeps serving its outputs after an edit until the user
re-runs; the dialog says so. This matches WebODM's own edit-then-restart flow
and keeps "what produced these outputs" unambiguous: outputs are only ever
replaced by a run. The payload carries `can_edit` and `options` (the decoded
`[{name, value}]` list) so the UI never guesses the column encoding.

### Implementation note: `db_set`, not `save()`

`processing_options` is stored as a JSON *string scalar* (see
`api.task._encode_processing_options`). On PostgreSQL the loaded value is
already one level unwrapped, and a full `Document.save()` would write that
text back verbatim — turning the column into a top-level array, which the
next load returns as a `list` and Frappe's delete snapshot refuses. Every
post-insert write to a task therefore goes through `db_set`, including the
edit endpoint; a duplicate re-encodes through `_decode` → `_encode` for the
same reason.

## 4. Duplicating a task

### What you can do

*Duplicate* on a task card creates a fresh **Pending** task in the same
project over the **same dataset** (so the images *and* the ground control
come along with no upload), with the same processing options and the title
suffixed "(copy)" / "(copy 2)" …. The copy is selected and its settings
dialog opens, because the point of a duplicate is to change something before
running it.

### Decision: copy inputs and settings, nothing a run produced

Not copied: status, progress, outputs (orthophoto, DSM, DTM, point cloud,
model), asset rows, extents, CRS, raster metadata, the viewer octree state,
node task id, compute instance, retry counters, errors, plugin runs,
`resolution` (derived from a run's orthophoto). Self-hosted WebODM's
duplicate copies the whole task directory including results; here outputs
live in object storage, a copy costs real money and time, and the next run
would wipe them anyway (`process_task` resets outputs before queueing). The
case the user named — comparing the same images under different settings —
needs none of it. If someone later wants "clone with results" it is a
separate, clearly named operation.

## Permissions

Everything is organization-scoped like the rest of the task and dataset
APIs: editing and duplicating need write on the task / create on its project
(any member of the organization), attaching or removing a GCP needs write on
the dataset (any member, like renaming it). A member of another organization
is denied by name, as before. `gcp_file` is user-writable through the REST
resource, so the dataset controller refuses any URL that is not a File
attached to that dataset.

## API

| Method | Purpose |
| --- | --- |
| `webodm_core.api.task.update_task` (`task_name`, `title?`, `options?`) | change settings; `TaskBusy` in Queued / Provisioning / Running |
| `webodm_core.api.task.duplicate_task` (`task_name`, `title?`) | fresh Pending copy over the same dataset with the same options |
| `webodm_core.api.task.get_task_progress` / `list_tasks` | now also `options` (decoded list, progress payload only) and `can_edit` |
| `webodm_core.api.dataset.set_gcp` (multipart `name`, `gcp`) | attach or replace the ground control file |
| `webodm_core.api.dataset.remove_gcp` (`name`) | detach and delete it |
| `webodm_core.api.dataset.create_dataset` / `webodm_core.api.task.upload_images` | accept an optional `gcp` part (the latter only with `files`) |

Dataset summaries (list rows, `get_dataset`, a task's `dataset_summary`)
carry `gcp: {filename, url, upload_name, crs, points, labels, image_count,
unknown_images, few_points} | null`.

## Where the code is

- `webodm_core/gcp.py` — the parser (no Frappe), `webodm_core/test_gcp.py`.
- `webodm_core/datasets.py` — `attach_gcp`, `remove_gcp`, `gcp_block`,
  `create_from_uploads(..., gcp_upload=)`; dataset controller `_check_gcp_fields`.
- `webodm_core/storage/assets.py` — `_sync_gcp`, `_gcp_input_source` (appended
  last by `input_sources`), `InputUnavailable`, `delete_dataset_gcp_object`,
  backfill; `storage/cache.py` lookup for the cache fill;
  `processing/task_runner.py` fails the task on `InputUnavailable`;
  `processing/node_client.py` `upload_content_type`.
- `webodm_core/api/task.py` — `update_task`, `duplicate_task`,
  `EDITABLE_STATUSES`, `_decode_processing_options`; `api/dataset.py` —
  `set_gcp`, `remove_gcp`.
- Frontend: `lib/upload.js` (XHR uploader, drop handling, progress maths,
  tracker), `lib/tasks.js` (client + status rules), `components/UploadDropzone.vue`,
  `UploadProgress.vue`, `GcpFileField.vue`, `TaskSettingsDialog.vue`; wired in
  `pages/MapView.vue`, `Datasets.vue`, `DatasetDetail.vue`, `Console.vue`.
- Tests: `api/test_gcp.py` (18), `api/test_task_management.py` (15),
  `test_gcp.py` (12), node client (+2); frontend `lib/upload.test.js`,
  `lib/tasks.test.js`, the three component tests, dataset client tests.

## Not done, on purpose

- Chunked / resumable uploads and direct-to-bucket uploads.
- A processing-node picker (nodes are provisioned automatically).
- Per-task record of the GCP file a run used.
- Editing a dataset's images; two GCP files on one dataset.
- Duplicating with results; cross-project duplication (the copy stays in the
  source project — move it by editing `project` if that ever becomes a UI).
- A GCP editor (WebODM's GCP Interface plugin); this takes the file as input.
