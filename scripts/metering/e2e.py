"""End-to-end check of usage metering on the throwaway test site.

    scripts/metering/test-site.sh up
    scripts/metering/test-site.sh py scripts/metering/e2e.py

What it does, in order, printing as it goes:

1. builds one small customer (a dataset, a task with outputs, a completed and a
   failed analysis run, a useful machine, a machine that never came up, a
   machine that is still running);
2. calls the two functions the scheduler calls — ``storage.sample_job`` and
   ``sweep.run_job`` — twice each, and checks the second pass adds nothing;
3. asks the usage API, as a member, what the organization consumed, and checks
   the totals equal the sum of the records it is given;
4. runs ``verify`` and ``trace``;
5. deletes the task and the dataset and checks the storage record still
   explains itself;
6. moves the clock past midnight UTC and checks today's storage now counts;
7. times the storage measurement of a large organization (200,000 files) and
   the sweep over a backlog of 2,000 closed machines.

It asserts as it goes and removes everything it created. Bulk rows are inserted
and removed with SQL: deleting 200,000 File documents one by one through the
document API takes the better part of an hour.
"""
import json
import time

import frappe

frappe.init(site="ci.localhost")
frappe.connect()

import datetime as dt
from unittest.mock import patch

from webodm_core.api import usage as usage_api
from webodm_core.metering import ledger, report, sweep, timeutil, verify
from webodm_core.metering import storage as storage_meter
from webodm_core.metering.testing import (
    INSTANCE,
    as_user,
    attach_file,
    make_instance,
    make_org,
    make_run,
    make_user,
    site_local,
    wipe,
)
from webodm_core.metering.timeutil import iso_z, utc_now
from webodm_core.testing import make_dataset, make_task

SAMPLE_JOB = frappe.get_attr("webodm_core.metering.storage.sample_job")
SWEEP_JOB = frappe.get_attr("webodm_core.metering.sweep.run_job")


def section(title):
    print(f"\n=== {title} ===")


org = make_org("E2E Meter Co")
other = make_org("E2E Other Co")
user = make_user("e2e_meter@example.com", org, "Member")
wipe(org, other)
now = utc_now()
today = now.date().isoformat()

# --- a customer with a bit of everything -------------------------------------
as_user(user)
if not frappe.db.exists("WebODM Project", "E2E Meter Project"):
    frappe.get_doc({"doctype": "WebODM Project", "title": "E2E Meter Project"}).insert()
dataset = make_dataset("Quarry, 19 Oct", images=[(f"DJI_{i:04d}.JPG", b"\xff" * (4_000 + i)) for i in range(3)])
task = make_task("E2E Meter Project", dataset=dataset.name, title="Quarry survey")
frappe.set_user("Administrator")
attach_file("WebODM Task", task.name, 250_000, f"{task.name}_orthophoto.tif")
attach_file("WebODM Task", task.name, 90_000, f"{task.name}_dsm.tif")
run = make_run(user, plugin="acme.change-detection", task=task.name,
               started_at=site_local(iso_z(now)), completed_at=site_local(iso_z(now)))
attach_file("WebODM Plugin Run", run.name, 12_345)
failed_run = make_run(user, plugin="acme.change-detection", task="no-such-task", status="Failed",
                      started_at=site_local(iso_z(now)), completed_at=site_local(iso_z(now)))



def t(**ago):
    """A pipeline-style (site-local) timestamp, that long before now."""
    return site_local(iso_z(now - dt.timedelta(**ago)))


useful = make_instance(org, task=task.name, requested_at=t(minutes=130), ready_at=t(minutes=125),
                       dispatched_at=t(minutes=124), released_at=t(minutes=65), terminated_at=t(minutes=65),
                       instance_class="cpu-large")
dud = make_instance(org, status="Failed", requested_at=t(minutes=60), ready_at=None, dispatched_at=None,
                    released_at=t(minutes=45), terminated_at=t(minutes=45), release_reason="provisioning-timeout")
running = make_instance(org, status="Ready", requested_at=t(minutes=25), ready_at=t(minutes=20),
                        dispatched_at=t(minutes=19), released_at=None, terminated_at=None, release_reason=None)
frappe.db.commit()

section("scheduler entry points, each called twice")
before = frappe.db.count(ledger.DOCTYPE, {"organization": org})
SAMPLE_JOB()
SWEEP_JOB()
after_first = frappe.db.count(ledger.DOCTYPE, {"organization": org})
SAMPLE_JOB()
SWEEP_JOB()
after_second = frappe.db.count(ledger.DOCTYPE, {"organization": org})
print(f"usage records for the organization: before={before} after first pass={after_first} after second pass={after_second}")
assert after_first == after_second == 5, (after_first, after_second)

section("the member asks: what did we consume? (webodm_core.api.usage.summary)")
as_user(user)
start = (now.date() - dt.timedelta(days=1)).isoformat()
end = (now.date() + dt.timedelta(days=1)).isoformat()
answer = usage_api.summary(start=start, end=end)
frappe.set_user("Administrator")
print(json.dumps(answer, indent=1))

section("the records behind it")
as_user(user)
page = usage_api.records(start=start, end=end, with_evidence=1)
frappe.set_user("Administrator")
for r in page["records"]:
    print(f'{r["meter"]:13} {r["dimension"]:24} quantity={r["quantity"]:>8} {r["unit"]:8} waived={r["waived_quantity"]:>6} '
          f'{r["waived_reason"] or "":22} {r["period_start_utc"]} -> {r["period_end_utc"]}  {r["source_doctype"]} {r["source_name"]}')
for meter in report.METERS:
    total = sum(r["quantity"] for r in page["records"] if r["meter"] == meter)
    assert total == answer["meters"][meter]["quantity"], meter
print("totals equal the sum of the records, per meter: yes")
# Today's storage measurement exists but its day is not over, so it is not among the counted records.
assert not [r for r in page["records"] if r["meter"] == "storage"]
storage_record = ledger.get(ledger.exists(ledger.storage_key(org, today)))
print("today's storage record (not yet counted): quantity", storage_record["quantity"], storage_record["unit"],
      storage_record["period_start_utc"], "->", storage_record["period_end_utc"])
print("storage evidence lines:", json.dumps(storage_record["evidence"]["inputs"]["lines"]))

section("verify")
check = verify.verify(organization=org)
print(json.dumps({k: check[k] for k in ("ok", "checked", "problems", "totals")}))
assert check["ok"]

section("trace one record")
print(json.dumps(verify.trace(ledger.exists(ledger.compute_key(useful))), indent=1)[:1800])

section("delete the dataset's task and dataset; the storage record keeps its evidence")
frappe.delete_doc("WebODM Task", task.name, force=True, ignore_permissions=True)
frappe.delete_doc("WebODM Dataset", dataset.name, force=True, ignore_permissions=True)
frappe.db.commit()
kept = ledger.get(storage_record["name"])
print("quantity still", kept["quantity"], "lines still", len(kept["evidence"]["inputs"]["lines"]))
print("verify still ok:", verify.verify(organization=org)["ok"])
print("the run deleted with the task was already recorded:", ledger.exists(ledger.analysis_run_key(run.name)) is not None)

section("the same question after midnight UTC (clock moved forward): today's storage now counts")
tomorrow = dt.datetime.combine(now.date() + dt.timedelta(days=1), dt.time(0, 0, 1))
with patch.object(timeutil, "utc_now", return_value=tomorrow):
    later = report.summary(org, start, end)
print(json.dumps({"storage_quantity_now": answer["meters"]["storage"]["quantity"],
                  "storage_quantity_after_midnight": later["meters"]["storage"]["quantity"],
                  "days_after_midnight": later["meters"]["storage"]["days"]}))

section("health")
health = report.health()
print(json.dumps({"storage_day": health["storage"]["day"], "organizations": health["storage"]["organizations"],
                  "not_measured_today": len(health["storage"]["not_measured_today"]),
                  "unrecorded": health["unrecorded"], "records": health["records"]}))

# --- timings -------------------------------------------------------------------
section("timing: storage measurement of a large organization")
big = make_org("E2E Big Co")


def drop_bulk():
    """Bulk fixtures were inserted with SQL; remove them the same way (200k File deletes through
    the document API take the better part of an hour)."""
    frappe.db.delete("File", {"attached_to_doctype": "WebODM Dataset", "attached_to_name": ["like", "e2ebig-%"]})
    frappe.db.delete("WebODM Dataset", {"name": ["like", "e2ebig-%"]})
    frappe.db.delete(INSTANCE, {"name": ["like", "e2ebig-ci-%"]})
    frappe.db.delete(ledger.DOCTYPE, {"organization": big})
    frappe.db.commit()


drop_bulk()
DATASETS, FILES = 400, 500
stamp = frappe.utils.now()
frappe.db.bulk_insert(
    "WebODM Dataset",
    fields=["name", "creation", "modified", "owner", "modified_by", "docstatus", "idx", "title", "organization"],
    values=[(f"e2ebig-{d:04d}", stamp, stamp, "Administrator", "Administrator", 0, 0, f"Survey {d}", big)
            for d in range(DATASETS)],
)
rows = []
for d in range(DATASETS):
    for f in range(FILES):
        rows.append((f"e2ebig-{d:04d}-{f:04d}", stamp, stamp, "Administrator", "Administrator", 0, 0,
                     f"IMG_{f:04d}.JPG", f"/private/files/e2ebig-{d:04d}-{f:04d}.JPG", 1, 0, 8_000_000 + f,
                     "WebODM Dataset", f"e2ebig-{d:04d}"))
frappe.db.bulk_insert(
    "File",
    fields=["name", "creation", "modified", "owner", "modified_by", "docstatus", "idx", "file_name", "file_url",
            "is_private", "is_folder", "file_size", "attached_to_doctype", "attached_to_name"],
    values=rows, chunk_size=5000,
)
frappe.db.commit()
frappe.db.sql('analyze "tabFile"')
frappe.db.commit()
timings = []
for _ in range(3):
    t0 = time.perf_counter()
    lines = storage_meter.measure(big)
    timings.append(round(time.perf_counter() - t0, 3))
measure_s = min(timings)
print("measure() three times, seconds:", timings)
t0 = time.perf_counter()
name, created = storage_meter.sample(big)
sample_s = time.perf_counter() - t0
frappe.db.commit()
rec = ledger.get(name)
evidence_bytes = len(frappe.db.get_value(ledger.DOCTYPE, name, "evidence").encode())
expected = sum(8_000_000 + f for f in range(FILES)) * DATASETS
print(json.dumps({"documents": len(lines), "files": DATASETS * FILES, "measure_seconds": round(measure_s, 3),
                  "sample_seconds": round(sample_s, 3), "quantity": rec["quantity"], "expected": expected,
                  "exact": rec["quantity"] == expected, "evidence_bytes": evidence_bytes}))
assert rec["quantity"] == expected

section("timing: sweep over a backlog of closed machines")
BACKLOG = 2000
drop_bulk()
frappe.db.bulk_insert(
    INSTANCE,
    fields=["name", "creation", "modified", "owner", "modified_by", "docstatus", "idx", "task", "organization",
            "status", "provider", "instance_class", "handle", "requested_at", "ready_at", "dispatched_at",
            "released_at", "terminated_at", "release_reason", "destroy_attempts", "port", "max_lifetime_seconds",
            "estimated_hourly_cost", "estimated_cost"],
    values=[(f"e2ebig-ci-{i:05d}", stamp, stamp, "Administrator", "Administrator", 0, 0, "no-such-task", big,
             "Terminated", "aws", "cpu", f"aws:i-{i}", "2026-10-01 08:00:00", "2026-10-01 08:05:00",
             "2026-10-01 08:06:00", "2026-10-01 09:05:00", "2026-10-01 09:05:04", "task:Completed", 0, 3000, 3600,
             0.34, 0.34) for i in range(BACKLOG)],
)
frappe.db.commit()
t0 = time.perf_counter()
stats = sweep.run(max_records=BACKLOG, max_seconds=3600)
sweep_s = time.perf_counter() - t0
t0 = time.perf_counter()
again = sweep.run()
idle_s = time.perf_counter() - t0
total = sum(int(r.quantity) for r in frappe.get_all(ledger.DOCTYPE, filters={"organization": big, "meter": "compute"},
                                                    fields=["quantity"], limit_page_length=0))
t0 = time.perf_counter()
summary = report.summary(big, "2026-10-01", "2026-11-01")
summary_s = time.perf_counter() - t0
t0 = time.perf_counter()
checked = verify.verify(organization=big)
verify_s = time.perf_counter() - t0
print(json.dumps({"backlog": BACKLOG, "recorded": stats["instances"], "errors": stats["errors"],
                  "sweep_seconds": round(sweep_s, 2), "records_per_second": round(BACKLOG / sweep_s),
                  "second_sweep_recorded": again["instances"], "second_sweep_seconds": round(idle_s, 3),
                  "total_seconds_counted": total, "expected": BACKLOG * 3600,
                  "summary_seconds": round(summary_s, 3), "summary_quantity": summary["meters"]["compute"]["quantity"],
                  "verify_seconds": round(verify_s, 2), "verify_ok": checked["ok"], "verify_checked": checked["checked"]}))
assert total == BACKLOG * 3600 and checked["ok"]

# --- clean up ------------------------------------------------------------------
drop_bulk()
wipe(org, other, big)
print("\ncleaned up")
