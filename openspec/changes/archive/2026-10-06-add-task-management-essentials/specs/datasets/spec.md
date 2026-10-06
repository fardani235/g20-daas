# Datasets — delta

## ADDED Requirements

### Requirement: Ground control travels with the dataset

A dataset MAY carry one ground control file in ODM's `gcp_list.txt` format
(`gcp_file`, `gcp_filename`, parsed `gcp_summary`, `gcp_storage_key`). It
SHALL be attachable with the upload that creates the dataset and SHALL be
attachable, replaceable and removable afterwards by any member of the
organization; the images remain fixed. The file MUST be validated against
the format (CRS line; `geo_x geo_y geo_z im_x im_y image_name [gcp_name]`
rows) before any photo of an upload is written, with the offending line
number on refusal; image names not present in the dataset and fewer than
three points SHALL be reported, not refused. The file SHALL be stored,
synced to object storage, re-materialised, backfilled and deleted like an
image, and the previous file SHALL be deleted on replace. Dispatch SHALL
send it to the node as the last part of the image upload under the fixed
name `gcp_list.txt`; a GCP file readable from neither the cache nor object
storage MUST fail the task with a message, never be dropped. Dataset
summaries and task payloads SHALL carry a `gcp` block (or `null`).

#### Scenario: Uploaded with the images
- **WHEN** a dataset is created with files and a valid `gcp` part
- **THEN** the dataset has the file attached, a summary with the CRS, point count and the image names not in the dataset, and tasks over it send `gcp_list.txt` last

#### Scenario: Invalid file
- **WHEN** the `gcp` part is not a GCP file (binary, no CRS line, five columns, a geo.txt)
- **THEN** the request is refused naming the line, and neither a dataset nor any photo File exists

#### Scenario: Attach, replace, remove later
- **WHEN** a member attaches, then replaces, then removes the GCP of an existing dataset
- **THEN** each step succeeds, the replaced file / blob / object are gone, the images are unchanged, and a member of another organization is denied

#### Scenario: Not with an existing dataset from the task dialog
- **WHEN** a task is created over an existing dataset with a `gcp` part
- **THEN** the request is refused pointing at the dataset page

#### Scenario: Unreadable at dispatch
- **WHEN** the GCP blob is missing and no object copy exists
- **THEN** the task becomes Failed with an error naming the ground control file and nothing is sent to the node

#### Scenario: Cold cache
- **WHEN** the host copy was evicted but the object exists
- **THEN** dispatch streams the GCP from object storage and the private-file route can re-materialise it
