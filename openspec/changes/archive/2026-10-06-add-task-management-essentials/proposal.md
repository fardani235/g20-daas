# Proposal: Task management essentials (ground control, upload feedback, edit, duplicate)

## Why

Someone who used self-hosted WebODM hits four walls here within the first
hour: there is no way to attach a ground control file with the imagery (so
images without GPS, or survey-grade work, are impossible); the upload says
"uploading" and nothing else, for what may be many gigabytes, and files
cannot be dragged in; a task's processing options cannot be changed once it
exists, so a wrong setting means re-uploading; and there is no way to
duplicate a task, which is how people compare the same images under
different settings. These are the moments a user brings imagery in and
manages the job afterwards; they should not feel like seams.

## What Changes

- **Ground control on the dataset.** `WebODM Dataset` gains an optional
  `gcp_file` (+ `gcp_filename`, parsed `gcp_summary`, `gcp_storage_key`).
  It can be uploaded with the images (`create_dataset`, `upload_images` in
  upload mode) or attached / replaced / removed later (`set_gcp`,
  `remove_gcp`); the images stay fixed. It is validated against ODM's
  `gcp_list.txt` format before any photo is written, synced to object
  storage and deleted like an image, and sent to NodeODM as one more part of
  the image upload named `gcp_list.txt` — NodeODM's existing convention for
  `--gcp`. A GCP that cannot be read anywhere fails the task instead of
  being dropped. Dataset summaries and task payloads carry a `gcp` block.
- **Upload feedback.** The two multipart uploads move from `fetch` to an
  XMLHttpRequest wrapper with the same URL, body, CSRF header and envelope,
  so the dialogs show bytes sent, speed, time left, a "finishing" phase
  after the last byte, and a Cancel button; a drop zone accepts files and
  folders and picks a dropped `gcp_list.txt` up as ground control.
- **Edit after creation.** `update_task(task_name, title?, options?)`
  changes a task's title / options in Pending, Completed, Failed and
  Cancelled; refused (`TaskBusy`) in Queued, Provisioning, Running. Saving
  never starts or resets; "Save and re-run" uses the existing
  `process_task`. Payloads gain `options` (decoded list) and `can_edit`.
- **Duplicate.** `duplicate_task(task_name, title?)` creates a Pending task
  in the same project over the same dataset with the same options and a
  "(copy)" title; nothing a run produced is copied.
- **Frontend.** Drop zone, progress bar, GCP field, task settings dialog;
  Edit and Duplicate buttons on the task card; GCP mark on cards and dataset
  rows; GCP section on the dataset page; GCP in the console header.

## Out of scope

Chunked / resumable uploads, a processing-node picker, per-task record of
the GCP file a run used, two GCP files on one dataset, duplicating with
results, a GCP editor.
