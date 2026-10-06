# Design

## Context

See `proposal.md`. The full record — reasoning, trade-offs, what was
deliberately left out — is `docs/tasks/README.md`; this is the short form.

Shaping facts: images live in a fixed, shared `WebODM Dataset` that a task
references; inputs flow cache-first / object-storage-second into
`NodeODMClient.create_task`, one multipart part per image; every post-insert
write to a task uses `db_set` (single-writer poll); `processing_options` is
a double-encoded JSON string scalar; NodeODM treats any `.txt` among the
uploaded images as the `--gcp` file.

## Decisions

### D1. Ground control belongs to the dataset

The file is measurements about the images (each row names an image and a
pixel); it is the same for every task over those images; it must flow
through the input pipeline (sync, cache, S3, delete) which is
dataset-owned; and it must follow duplicates and re-runs without being
re-attached. Rejected: per-task GCP (re-attach on every copy, duplicated
files, "which GCP?" on the wrong record). Limitation accepted: one GCP per
dataset; with/without is ODM's `use-exif` option; a genuinely different GCP
is a different dataset.

### D2. The GCP is the one input that may change later

Images stay fixed (same pixels for every task; keys/thumbnails/migration
stay simple). The GCP is kilobytes of metadata; freezing it would force a
gigabyte re-upload to add a text file. Tasks dispatched after a change use
the new file; a Running task keeps what it was given. Per-task snapshot of
the file used is a follow-up; a one-line guard can forbid changes once a
task has run if that is preferred.

### D3. Reuse NodeODM's convention, fixed upload name

Appended as the last `images` part, always as `gcp_list.txt` (so a `geo.txt`
or `align.*` name can never be misread by NodeODM); content type
`text/plain`. No node API change.

### D4. Validate shape, report content; fail loud if unreadable

Parser refuses non-format files with the line number, before any photo is
written. Unknown image names / few points are warnings in the summary. An
unreadable GCP at dispatch raises `InputUnavailable` → task Failed with the
reason (silently dropping it would answer a different question).

### D5. Progress via XMLHttpRequest, same endpoints

`uploadForm` keeps URL, FormData, CSRF header and envelope; adds progress,
abort. Phases: uploading (bytes/rate/eta) → processing (server registering)
→ done / failed / cancelled. No chunking (out of scope, stated).

### D6. Edit allowed outside the in-flight window; saving is inert

Editable: Pending, Completed, Failed, Cancelled. Refused: Queued (would race
the dispatch sweep), Provisioning, Running (node already has the options).
Save stores only; re-run goes through `process_task`, which resets outputs.
`update_task` writes with `db_set`: on PostgreSQL a full `save()` would
write the once-unwrapped options text back as a top-level array.

### D7. Duplicate = inputs + settings, no results

New Pending task: same project, dataset (images + GCP), re-encoded options,
"(copy)" title. Nothing a run produced or tracked is copied (object storage
copies cost; the next run wipes them anyway). Differs from WebODM, which
copies results; documented.

## Risks / Trade-offs

- A dataset's GCP record describes the current file, not past runs.
- Large uploads still fail as a whole on a dropped connection.
- `use-exif` as the "without GCP" switch is an ODM option the user must know
  about; the dataset page says so.
