# Object Storage — delta for the dataset library

## MODIFIED Requirements

### Requirement: Canonical store

Uploaded imagery is keyed by **dataset**, not task:
`orgs/<slug>/datasets/<id>/inputs/<file>`, with the key recorded on each
`WebODM Dataset Image` row. A dataset migrated from a pre-library task keeps
its `orgs/<slug>/tasks/<task>/inputs/` keys; the organization namespace is
the only thing the boundary check enforces.

### Requirement: Re-processing after eviction

Resetting or deleting a task never touches any `inputs/` object: inputs
belong to the task's dataset, which other tasks may reference.

### Requirement: Eviction

A dataset image counts as busy while **any** task referencing its dataset is
Queued, Provisioning or Running.

### Requirement: Backfill

The periodic backfill copies unsynced `WebODM Dataset Image` rows under the
dataset's prefix, taking the organization from the parent dataset, and skips
(with a log entry) a row whose file is not attached to that dataset instead
of stalling.
