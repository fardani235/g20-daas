# User Plugins — delta

## ADDED Requirements

### Requirement: Reference-survey inputs

A manifest input MAY carry `survey` with the value `primary` (default) or
`reference`; any other value SHALL reject the package. The normalized
manifest SHALL keep `survey` only when it is `reference`. The run dialog
SHALL, for a plugin with a reference input, offer the other completed tasks
of the project as the survey to compare with, resolve each input's dataset
choices against its own survey, and refuse to start without a choice.

#### Scenario: Manifest with a reference input

- **WHEN** a package declares `{"name": "reference_surface", "datasets":
  ["dsm"], "survey": "reference"}`
- **THEN** it installs and the stored input keeps `survey: "reference"`

#### Scenario: Unknown survey value

- **WHEN** an input declares `survey: "before"`
- **THEN** the upload is rejected naming the input and the allowed values

#### Scenario: Run dialog

- **WHEN** a member opens the run dialog of a comparison plugin on a task
- **THEN** a "Compare with" picker lists the project's other completed
  tasks, the reference inputs offer the picked task's datasets, and the run
  request carries the picked task as `reference_task`
