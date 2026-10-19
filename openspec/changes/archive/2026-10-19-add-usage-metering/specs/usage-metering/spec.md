# Usage Metering Specification

## Purpose

Keeps the durable, per-organization record of what was consumed — machine
time, analysis runs, stored bytes — in exact natural units, so that "what did
this customer consume in this period?" has an answer that adds up from
records. It records; it does not price, hold a balance, show a screen or take
money.

## Requirements

### Requirement: Usage ledger

The system SHALL keep usage as rows of `WebODM Usage Record`, one row per
fact about one organization, with a meter (`compute`, `analysis_run`,
`storage`), a unit (`second`, `run`, `byte-day`), an integer `quantity`, an
integer `waived_quantity` with a `waived_reason`, the UTC interval the fact
covers, the UTC instant it was recorded, the source it was derived from, the
rule that derived it and the evidence that rule used. Quantities SHALL be
integers. No row SHALL hold a price, an amount, a currency or any value read
from the platform's cost estimates.

#### Scenario: Natural units only

- **WHEN** any usage record is written
- **THEN** its quantities are integers in its meter's unit and neither the row nor its evidence contains `estimated_hourly_cost`, `estimated_cost` or any monetary value

#### Scenario: Waived usage cannot be summed by accident

- **WHEN** usage is measured but must not count
- **THEN** the row has `quantity = 0`, the measured amount in `waived_quantity` and a `waived_reason`, so a sum of `quantity` never includes it

### Requirement: A fact is recorded once

Every usage record SHALL carry an idempotency key derived from the identity
of the fact, unique in the database. Recording a fact whose key exists SHALL
write nothing and leave the existing row untouched. This SHALL hold when two
recorders run at the same time, and a recorder that loses the race SHALL NOT
fail. A record's name SHALL NOT be derivable from what it records.

#### Scenario: Repeated sweep

- **WHEN** the sweep runs again over sources that were already recorded
- **THEN** no row is added and no row changes

#### Scenario: Concurrent recorders

- **WHEN** two processes record the same fact at the same moment
- **THEN** exactly one row exists afterwards and neither process fails

#### Scenario: Names are not guessable

- **WHEN** a storage record exists for an organization and a day
- **THEN** its name cannot be computed from the organization's name and the day

#### Scenario: Two routes to the same fact

- **WHEN** an analysis run is recorded by the sweep and is then deleted
- **THEN** the deletion hook finds the existing row and adds nothing

### Requirement: Records are immutable

A usage record SHALL NOT be changed or deleted through the application: the
controller SHALL refuse updates and deletes, inserts SHALL be accepted only
from the recorder, and no role SHALL hold write or delete permission. A
mistake SHALL be corrected by a reversal row that negates the original,
names it, is unique per original and states who reversed it and why. Each row
SHALL store a hash of its content so that a change made behind the
application is detectable, and a row SHALL be stored exactly as it was
hashed: no text is rewritten on the way in.

#### Scenario: Edit refused

- **WHEN** anything saves, `db_set`s or deletes an existing usage record through the document API
- **THEN** it is refused and the row is unchanged

#### Scenario: Reversal

- **WHEN** a platform admin reverses a record with a note
- **THEN** a second row with the negated quantities is added in the same period, totals net to zero, both rows remain, and a second reversal of the same record is refused

#### Scenario: Text that looks like markup

- **WHEN** a reversal note or a document title contains text such as `<OPS-1423>`
- **THEN** it is stored unchanged and the record verifies

#### Scenario: Tampering is visible

- **WHEN** a row is altered directly in the database
- **THEN** verification reports that row

### Requirement: Catch-up recording

Machine time and analysis runs SHALL be recorded by a scheduled sweep that
finds finished sources with no usage record and records them, oldest first.
The sweep SHALL be safe to run at any frequency, SHALL be the same code that
records history, and a source that fails to record SHALL be logged and
retried on the next run without stopping the others.

#### Scenario: History

- **WHEN** the sweep first runs on a site that already has closed instances and finished runs
- **THEN** each gets one record whose interval is in the past and whose recorded instant is now

#### Scenario: One bad source

- **WHEN** one source raises while being recorded
- **THEN** the remaining sources are still recorded and the failure is in the error log

### Requirement: Machine time

Each compute instance SHALL be recorded once, after it is closed. The
interval SHALL run from the instant the machine became Ready to the instant
the platform first asked for it to be destroyed, in whole seconds, start
rounded up and end rounded down. The time SHALL count only when the machine
became Ready, a job was dispatched to it, the platform neither failed nor
killed the instance, and both ends are known and in order. Otherwise it
SHALL be waived with a reason. The dimension SHALL be
`<provider>/<instance class>`.

#### Scenario: Destroy still being retried

- **WHEN** an instance has been released but its destroy has not been confirmed
- **THEN** it is recorded with the interval ending at the release, and confirmation later changes nothing

#### Scenario: Useful machine

- **WHEN** a machine became Ready, received a job and was released when the job ended
- **THEN** one `compute` record counts the whole seconds between Ready and release

#### Scenario: Never became useful

- **WHEN** a machine was requested and never became Ready
- **THEN** its record has `quantity = 0` and `waived_reason = never_ready`

#### Scenario: Job failed before any work

- **WHEN** a machine became Ready and the job was never accepted by it
- **THEN** its record has `quantity = 0` and `waived_reason = no_work_dispatched`

#### Scenario: The platform could not switch it off

- **WHEN** destroying a machine fails and is retried for an hour
- **THEN** the counted interval ends at the first request to destroy it, not at the confirmation

#### Scenario: The platform killed it

- **WHEN** a machine is destroyed by the lifetime budget
- **THEN** its record is waived with `platform_failure`

#### Scenario: Facts missing on an old row

- **WHEN** an instance closed before the release time was recorded and its destroy was retried
- **THEN** its time is waived with `end_time_not_recorded` rather than counted to an overstated end

#### Scenario: Waiting for a retry when metering is switched on

- **WHEN** an instance is already in `Terminating` with no release time when the release fields are introduced
- **THEN** the retry that finally destroys it writes no release time, and its time is waived

### Requirement: Analysis runs

Every analysis run SHALL end in exactly one usage record. A Completed run
SHALL count as one run; a Failed or Cancelled run and a run deleted before it
finished SHALL be waived with the reason. A run that is about to be deleted
SHALL be recorded first.

#### Scenario: Completed

- **WHEN** a plugin run completes
- **THEN** one `analysis_run` record with `quantity = 1` and the plugin as its dimension exists after the next sweep

#### Scenario: Failed

- **WHEN** a plugin run fails
- **THEN** its record has `quantity = 0`, `waived_quantity = 1`, `waived_reason = failed`

#### Scenario: Replaced by a re-run

- **WHEN** a plugin is run again on the same task before the sweep saw the first run
- **THEN** the first run is recorded as it is deleted and the second is recorded when it finishes

### Requirement: Storage measurement

The system SHALL measure every organization's stored bytes once per UTC day
and record the result as a `storage` usage record in byte-days. The
measurement SHALL be the sum of the sizes of the files registered against the
organization's datasets, tasks and analysis runs, each distinct file counted
once. It SHALL be taken from database records only, SHALL not depend on
object storage, prices or any other part of metering, and SHALL record zero
for an organization that stores nothing. The record SHALL list, per
document, the file count and bytes that make up the total, and SHALL state
what was and was not checked.

#### Scenario: Daily measurement

- **WHEN** the measurement job runs and an organization has no record for the current UTC day
- **THEN** one is written whose quantity equals the sum of its evidence lines

#### Scenario: Already measured today

- **WHEN** the job runs again the same day
- **THEN** nothing is written, whatever the organization now stores

#### Scenario: Deleted afterwards

- **WHEN** a dataset is deleted after a day's measurement
- **THEN** that day's record still names the dataset, its file count and its bytes

#### Scenario: Duplicate file rows

- **WHEN** two `File` rows point at the same file URL on one document
- **THEN** the file is counted once

#### Scenario: Object storage is down

- **WHEN** the bucket is unreachable or not configured
- **THEN** the measurement is taken as usual

### Requirement: No invented history

The system SHALL never write a storage measurement for any day other than
the current one, and SHALL never estimate, interpolate or carry forward a
quantity. A day without a measurement SHALL be reported as not measured.

#### Scenario: Scheduler was down for two days

- **WHEN** no measurement ran on two days and then runs again
- **THEN** only the current day is written, the two days stay without records, and a summary covering them lists them as not measured and adds nothing for them

### Requirement: Recording never harms what it records

No metering code SHALL be able to fail or alter a dispatch, a teardown, a
processing run, a plugin run or a deletion. Every metering write on a
pipeline path SHALL be isolated so that an error — including a database
error — is logged and leaves the surrounding transaction usable.

#### Scenario: Recorder fails during a delete

- **WHEN** recording a run raises while the run is being deleted
- **THEN** the run is deleted exactly as before

#### Scenario: Stamp fails during dispatch

- **WHEN** writing `dispatched_at` fails
- **THEN** the task is Running with its node task id, and the failure is logged

### Requirement: Answering for a period

For an organization and a period `(start, end]` the system SHALL return, per
meter, the totals of `quantity` and `waived_quantity` over the records whose
interval ended in the period, grouped by dimension, computed from the
records at request time. A record SHALL NOT be counted before its interval
has ended. No total SHALL be stored. The records behind a total SHALL be
retrievable, each with its evidence. Given an `as_of` instant not in the
future, the answer SHALL use only records recorded up to it and SHALL treat
that instant as now. A period that is ambiguous, out of order or longer than
400 days SHALL be refused.

#### Scenario: Adds up

- **WHEN** a summary is requested
- **THEN** each total equals the sum of the listed records for that meter

#### Scenario: Reproducible

- **WHEN** a summary is requested with `as_of` set to an earlier instant
- **THEN** it equals what a summary showed at that instant, for every record whose transaction had committed by then

#### Scenario: Future as_of

- **WHEN** `as_of` is later than now
- **THEN** the request is refused

#### Scenario: Unbounded period

- **WHEN** a period of thousands of years, or a date-time without a time zone, is requested
- **THEN** the request is refused rather than guessed or attempted

#### Scenario: Boundary

- **WHEN** a machine ran across the end of a period
- **THEN** its whole quantity is in the period in which it was released and in no other

### Requirement: Provisional figures are visible and separate

Work that has not finished SHALL NOT be in any total. A summary SHALL list
it separately, each item marked provisional with the instant it was computed,
and SHALL say that the answer is provisional — its totals can still change —
and why: the period has not ended, work is in progress, finished sources are
not yet recorded, or storage days of the period are still to be measured or
still running. A storage measurement whose day has not ended SHALL be listed
with its quantity, marked as fixed, and SHALL NOT be in the total until the
day is over. A storage day that ended unmeasured is not provisional: it
SHALL be listed as a gap.

#### Scenario: Machine still running

- **WHEN** a summary is requested while a machine is Ready
- **THEN** the machine appears under in-progress with its elapsed seconds marked provisional, and the compute total does not include it

#### Scenario: Today's storage measurement

- **WHEN** a summary of an open period is requested after today's measurement was taken
- **THEN** today is listed as in progress with its bytes, the storage total covers only days that have ended, and after midnight UTC the same request counts it

#### Scenario: A gap is not provisional

- **WHEN** a past period contains a day that was never measured and nothing else is outstanding
- **THEN** the summary is not provisional and lists the day under gaps

#### Scenario: Sweep has not caught up

- **WHEN** a finished run has no record yet
- **THEN** the summary counts it as unrecorded and is marked provisional

### Requirement: Verification

The system SHALL provide a check that, for a set of records, recomputes
each row's hash, re-derives each quantity from the row's own evidence,
confirms each storage quantity equals the sum of its lines, confirms every
reversal matches its original and re-adds the totals independently.

#### Scenario: Clean ledger

- **WHEN** verification runs over untouched records
- **THEN** it reports no problems

#### Scenario: Quantity no longer follows from evidence

- **WHEN** a row's quantity is changed in the database
- **THEN** verification reports both the hash mismatch and the quantity mismatch

### Requirement: Organization isolation

Usage records SHALL be organization-scoped by the same permission hooks as
every other tenant DocType. A member SHALL read only their own
organization's records through every route (the usage API, the REST
resource, list and count queries); nobody SHALL write. The usage API SHALL
take the organization from the session; an organization argument from a
caller who is not a platform admin SHALL be refused identically whether or
not that organization exists. A record SHALL contain nothing copied from
another organization's documents, whatever its source row links to.

#### Scenario: Another organization's record

- **WHEN** a member requests a record of another organization by name through the usage API
- **THEN** the answer is the same as for a name that does not exist

#### Scenario: Another organization's record over the REST resource

- **WHEN** a member requests it at `/api/resource/WebODM Usage Record/<name>`
- **THEN** it is refused (Frappe answers 403 for an existing document and 404 for a missing one, as for every DocType; names are random, so this tells a caller nothing they can aim at)

#### Scenario: A run linked to someone else's task

- **WHEN** a member creates a plugin run whose `task` or `plugin` belongs to another organization
- **THEN** the run's usage record carries no project and no plugin details from that organization

#### Scenario: Listing

- **WHEN** a member lists usage records through the REST resource
- **THEN** only their organization's rows are returned

#### Scenario: No organization

- **WHEN** a user without an organization calls the usage API
- **THEN** it is refused
