# Plugin Outputs — delta

## ADDED Requirements

### Requirement: Result summary on the run row

When a completed run's metadata contains `summary` (a list of `{label,
value}`) the run row SHALL offer to show those rows, followed by the
metadata's `warnings` (a list of strings), exactly as reported. A comparison
run's row and map layer SHALL be labelled with the plugin and the title of
the survey it was compared with, and the newest run SHALL be shown per
plugin and reference task. Vector outputs with `render_kind` `change` SHALL
be drawn with fixed class colours (fill, cut, vegetation, appearance), a
legend with the volume or area per class, and a popup with the clicked
region's own numbers; values taken from the output SHALL be escaped.

#### Scenario: Summary shown

- **WHEN** a completed run reports summary rows and a warning
- **THEN** "Results" on its row reveals the rows and the warning

#### Scenario: Two comparisons on one task

- **WHEN** a task has completed runs of one plugin against two reference
  tasks
- **THEN** both have a row and a layer, each labelled with its reference
