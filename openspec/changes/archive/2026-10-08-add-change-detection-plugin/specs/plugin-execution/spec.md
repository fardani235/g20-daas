# Plugin Execution — delta

## ADDED Requirements

### Requirement: Comparison runs

A plugin whose manifest declares at least one input with `survey:
"reference"` SHALL be run against two tasks: the task the run belongs to and
a *reference task* named in the run request. The reference task MUST be
readable by the caller, belong to the caller's organization and to the same
project as the run's task, be completed and differ from the run's task; a
reference task of another organization and one that does not exist MUST be
refused with the same message. A run request for such a plugin without a
reference task, and a reference task given to any other plugin, SHALL be
rejected. Each input SHALL resolve against its own task (dataset selection,
optional inputs and required-input errors as for single-task runs). The
same-organization and same-project rules SHALL also be enforced on the run
record itself (whatever creates or edits it) and again by the worker before
any file is staged. The run record SHALL store the reference task; the
plugin SHALL receive the reference task's facts and, per input, the dataset
and survey that feed it. Exactly one result SHALL be kept per plugin, task
and reference task, and deleting either task SHALL delete the run.

#### Scenario: Comparison accepted

- **WHEN** a member runs a comparison plugin on a completed task naming
  another completed task of the same project as reference
- **THEN** a Queued run is created that records the reference task and which
  inputs are read from it

#### Scenario: Another organization's survey

- **WHEN** the reference task belongs to another organization, or does not
  exist
- **THEN** the request is rejected with the same "not found" answer in both
  cases and no run is created

#### Scenario: Survey of another project

- **WHEN** the reference task is in a different project of the same
  organization
- **THEN** the request is rejected

#### Scenario: Run record created outside the run API

- **WHEN** a run record naming a reference task of another organization or
  project is inserted or edited directly
- **THEN** the save is refused

#### Scenario: Stored pair no longer valid

- **WHEN** the worker picks up a run whose reference task is no longer in
  the run's organization and the task's project
- **THEN** the run fails without staging any input

#### Scenario: Inputs staged per survey

- **WHEN** a comparison run executes
- **THEN** inputs marked `reference` are read from the reference task, the
  others from the run's task, and the plugin's context names both tasks

#### Scenario: One result per pair

- **WHEN** the same plugin is run on the same task against two different
  reference tasks, then again against the first
- **THEN** both comparisons are kept and the repeated one replaces its
  earlier result only

#### Scenario: Either survey deleted

- **WHEN** the run's task or its reference task is deleted
- **THEN** the run and its output are deleted with it
