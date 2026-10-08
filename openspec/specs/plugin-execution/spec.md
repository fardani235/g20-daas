# Plugin Execution Specification

## Purpose

Lets users run an enabled analysis plugin against a completed task and tracks
each execution to completion.

## Requirements

### Requirement: Run eligibility

The system SHALL permit a run only when the plugin is enabled for the caller's
organization, platform-enabled, the task belongs to the same organization and is
completed, and the task provides the inputs the operation requires.

#### Scenario: Eligible run accepted

- **WHEN** a member starts an enabled plugin on a completed task in their
  organization with the required inputs present
- **THEN** a run record is created with status Queued

#### Scenario: Plugin not enabled for organization

- **WHEN** a member starts a plugin their organization has not enabled
- **THEN** the request is rejected and no run record is created

#### Scenario: Task not completed

- **WHEN** a member starts a plugin on a task that is not completed
- **THEN** the request is rejected

#### Scenario: Cross-organization task

- **WHEN** a member starts a plugin on a task owned by another organization
- **THEN** the request is rejected

#### Scenario: Missing required input

- **WHEN** a member starts a plugin whose required input is absent from the task
- **THEN** the request is rejected with an indication of the missing input

### Requirement: Parameter handling

Run parameters SHALL default to the organization's saved plugin settings and
SHALL be validated against the operation's parameter schema, with run-specific
values overriding defaults.

#### Scenario: Defaults applied

- **WHEN** a run is started without explicit parameters
- **THEN** the organization's saved settings are used

#### Scenario: Override applied

- **WHEN** a run supplies a parameter valid per the schema
- **THEN** the supplied value overrides the default for that run only

#### Scenario: Invalid parameter rejected

- **WHEN** a run supplies a parameter that violates the schema
- **THEN** the request is rejected and no run is created

### Requirement: Asynchronous execution lifecycle

Each accepted run SHALL execute asynchronously and transition through Queued,
Running, and exactly one terminal state of Completed, Failed, or Cancelled,
updating progress while running.

#### Scenario: Successful lifecycle

- **WHEN** a queued run begins and its operation succeeds
- **THEN** the run transitions Queued to Running to Completed

#### Scenario: Operation failure

- **WHEN** the analysis operation fails
- **THEN** the run transitions to Failed and records an error message

#### Scenario: Cancellation

- **WHEN** a user cancels a queued or running run
- **THEN** the run transitions to Cancelled and any partial output is discarded

#### Scenario: Progress reporting

- **WHEN** a run is running and the operation reports progress
- **THEN** the run's progress reflects the latest reported value

#### Scenario: Live progress from a user plugin

- **WHEN** a user plugin writes `{"percent", "message"}` to its
  `progress_path` while the sandbox run is executing
- **THEN** within a few seconds the run's `progress` and `progress_message`
  reflect it (committed independently of the job's transaction) and the run
  list exposes both; on completion `progress` is 100 and the message cleared

### Requirement: Run record retention and history

The system SHALL retain a durable record of every run, including plugin, task,
organization, parameters, status, timestamps, and output references, and SHALL
allow members to list runs for tasks in their organization.

#### Scenario: Repeated runs recorded separately

- **WHEN** the same plugin is run repeatedly on a task
- **THEN** each execution is recorded separately and remains queryable

#### Scenario: Organization-scoped visibility

- **WHEN** a member lists runs
- **THEN** only runs belonging to their organization are returned

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
