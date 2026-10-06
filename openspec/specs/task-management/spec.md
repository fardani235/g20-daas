# Task Management Specification

## Purpose

What a user can do with a task after it exists — change its settings and
duplicate it — and how the upload that creates datasets and tasks reports
itself. Complements `datasets` (inputs, ground control) and
`on-demand-processing` (dispatch and lifecycle).

## Requirements

### Requirement: Upload feedback

The dialogs that upload imagery (new dataset, new task from uploads) SHALL
accept files by drag and drop (including a dropped folder where the browser
exposes it) as well as by picker, SHALL merge repeated drops without
duplicates, SHALL pick up a `.txt` dropped with the photos as the ground
control file, and SHALL show progress as bytes sent of total with speed and
time remaining, followed by a distinct "finishing" state after the last
byte until the server answers. The user SHALL be able to cancel an upload
in flight; a cancelled upload MUST leave no dataset or task behind. The
upload endpoints, bodies and headers SHALL be unchanged (progress comes
from the transport, not a new protocol); chunked or resumable uploads are
out of scope.

#### Scenario: Progress and finishing
- **WHEN** 1 GB is being uploaded and 250 MB have been sent
- **THEN** the bar shows 25 %, the bytes, an average speed and an estimate; when 1 GB has been sent and the response is pending it shows the finishing state without a percentage

#### Scenario: Cancel
- **WHEN** the user cancels mid-upload
- **THEN** the request is aborted, the UI says nothing was created, and no dataset or task exists

#### Scenario: Mixed drop
- **WHEN** a folder with photos, `gcp_list.txt` and a PDF is dropped
- **THEN** the photos are selected, the GCP field is filled, and one file is reported as skipped

### Requirement: Editing a task's settings

A task's title and processing options SHALL be editable after creation
while the task is Pending, Completed, Failed or Cancelled, and MUST be
refused while it is Queued, Provisioning or Running with a message telling
the user to cancel first. Saving SHALL change only the keys sent, SHALL NOT
start, reset or wipe anything, and SHALL keep the stored options in the
delete-safe encoding on every database backend. The UI SHALL offer "Save"
and "Save and start / re-run"; the latter uses the existing start path,
which replaces the previous outputs. Payloads SHALL expose `can_edit` and
the decoded `options` list.

#### Scenario: Edit a finished task
- **WHEN** the options of a Completed task are changed and saved
- **THEN** the task stays Completed with its outputs until it is re-run, and the re-run dispatches the new options

#### Scenario: Refused in flight
- **WHEN** an edit is attempted on a Queued, Provisioning or Running task
- **THEN** it is refused, nothing changes, and the reason names the status

#### Scenario: Organization boundary
- **WHEN** a member of another organization attempts the edit
- **THEN** it is denied

### Requirement: Duplicating a task

A task SHALL be duplicable from any status into a new Pending task in the
same project that references the same dataset (hence the same images and
ground control) and carries the same processing options and a "(copy)"
title unique within the project (or a given title). Nothing a run produced
or tracked MUST be copied: status, progress, outputs, asset rows, extents,
CRS, raster metadata, viewer octree state, node task id, compute instance,
retry counters, errors, plugin runs, derived resolution. The source task and
the dataset MUST be unchanged.

#### Scenario: Duplicate a completed task
- **WHEN** a Completed task with outputs is duplicated
- **THEN** the copy is Pending, has no outputs or counters, points at the same dataset, has equal options and the title "<title> (copy)"; the source is untouched and no dataset was created

#### Scenario: Ground control follows
- **WHEN** the dataset has a GCP file
- **THEN** dispatching the copy sends `gcp_list.txt` without any upload

#### Scenario: Permission
- **WHEN** the caller cannot read the source or write to its project
- **THEN** the duplicate is refused
