#!/usr/bin/env python3
"""Seeded faults for usage metering: break one thing at a time on a COPY of the
app and confirm that the test suite notices.

A passing test suite says the code does what the tests ask. It does not say the
tests would notice if the code stopped. This script answers that for the
properties metering exists to guarantee: each entry below removes a guard,
flips a sign or drops a filter, runs the tests that ought to fail, and reports
whether they did.

    scripts/metering/test-site.sh up
    python3 scripts/metering/seeded_faults.py            # all (about 25 minutes)
    python3 scripts/metering/seeded_faults.py dup-not-recognised retry-stamps-release

Each fault is ``(id, file, old, new, kind, test modules)`` with an optional
seventh item, ``EQUIVALENT``. ``old`` must occur exactly once in ``file``; if
the code has moved on and it no longer does, the fault is reported as NOT
APPLIED — fix the pattern, do not delete the entry. ``kind`` is ``pure``
(tests that need no site, run with the local Python) or ``frappe`` (run on the
throwaway site, see test-site.sh).

Outcomes:

    caught      a named test failed — what we want
    SURVIVED    every named test still passed — a gap in the tests, unless the
                entry is marked EQUIVALENT (the change alters no behaviour on
                its own; the entry says why) or is one of the two controls

The working tree is never modified: every fault is applied to a fresh copy
under a temporary directory.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = Path(os.environ.get("WEBODM_CORE_DIR", ROOT / "frappe-bench/apps/webodm_core"))
PREFIX = os.environ.get("METER_PREFIX", "meter")
IMG = os.environ.get("FRAPPE_IMAGE", "ghcr.io/fardani235/webodm-frappe:16.34.0")
ENV = (f"SITE_NAME=ci.localhost DB_HOST={PREFIX}-pg DB_PORT=5432 DB_NAME=webodm_ci DB_USER=webodm_ci "
       "DB_PASSWORD=webodm_ci ADMIN_PASSWORD=admin FRAPPE_ROOT_USER=postgres FRAPPE_ROOT_PASSWORD=postgres "
       f"REDIS_CACHE_HOST={PREFIX}-redis-cache REDIS_CACHE_PORT=6379 REDIS_QUEUE_HOST={PREFIX}-redis-queue "
       "REDIS_QUEUE_PORT=6379 FRAPPE_ROLE=exec").split()

EQUIVALENT = "equivalent"

M = "webodm_core/metering/"
FAULTS = [
    # Harness check: a change that breaks nothing must be reported as SURVIVED.
    # If either control is "caught", the tests are failing for another reason
    # and nothing below means anything.
    ("control", M + "guard.py", 'LOG_TITLE = "WebODM Metering"', 'LOG_TITLE = "WebODM Metering"  # control', "frappe",
     ["webodm_core.metering.test_no_harm"]),
    ("control-pure", M + "rules.py", 'METER_COMPUTE = "compute"', 'METER_COMPUTE = "compute"  # control', "pure",
     ["webodm_core.metering.test_rules"]),
    # --- a double count -------------------------------------------------------
    ("dup-not-recognised", M + "ledger.py", "        if _is_duplicate_key(e):", "        if False:", "frappe",
     ["webodm_core.metering.test_concurrency"]),
    ("look-first-removed-and-dup-raises", M + "ledger.py",
     "            frappe.clear_last_message()\n            return exists(key), False\n        raise",
     "            raise\n        raise", "frappe", ["webodm_core.metering.test_ledger"]),
    ("storage-no-file-dedupe", M + "storage.py",
     "                GROUP BY f.attached_to_name, COALESCE(f.file_url, f.name)",
     "                GROUP BY f.attached_to_name, f.name, f.file_url", "frappe", ["webodm_core.metering.test_storage"]),
    ("storage-twice-a-day", M + "storage.py", "    key = ledger.storage_key(organization, day)",
     "    key = ledger.storage_key(organization, day) + now.strftime('T%H')", "frappe",
     ["webodm_core.metering.test_storage"]),
    ("period-lower-bound-inclusive", M + "report.py", '        ["period_end_utc", ">", after],',
     '        ["period_end_utc", ">=", after],', "frappe", ["webodm_core.metering.test_report"]),
    ("hook-and-sweep-different-keys", M + "sweep.py",
     "    key = ledger.analysis_run_key(row.name)\n",
     "    key = ledger.analysis_run_key(row.name) + (':deleted' if deleting else '')\n", "frappe",
     ["webodm_core.metering.test_sweep"]),
    # --- over-charging --------------------------------------------------------
    ("end-at-terminated", M + "rules.py",
     "    if released is not None:\n        end, end_basis = released, \"released_at\"\n    elif terminated is not None:",
     "    if terminated is not None:\n        end, end_basis = terminated, \"terminated_at\"\n    elif released is not None:\n        end, end_basis = released, \"released_at\"\n    elif terminated is not None:",
     "pure", ["webodm_core.metering.test_rules"]),
    ("no-work-counts", M + "rules.py", "    elif not inputs.get(\"work_evidence\"):\n        reason = WAIVED_NO_WORK\n",
     "", "pure", ["webodm_core.metering.test_rules"]),
    ("platform-kill-counts", M + "rules.py", "    elif platform_killed:\n        reason = WAIVED_PLATFORM_FAILURE\n",
     "", "pure", ["webodm_core.metering.test_rules"]),
    ("old-row-retried-counts", M + "rules.py",
     "    elif end_basis == \"terminated_at\" and retried:\n        reason = WAIVED_END_NOT_RECORDED\n", "",
     "pure", ["webodm_core.metering.test_rules"]),
    ("start-rounds-down", M + "rules.py",
     "        return timeutil.ceil_second(utc) if edge == \"start\" else timeutil.floor_second(utc)\n\n    ready",
     "        return timeutil.floor_second(utc)\n\n    ready", "pure", ["webodm_core.metering.test_rules"]),
    ("fold-ignored", M + "timeutil.py",
     "    return (max(first, second) if edge == \"start\" else min(first, second)), True",
     "    return first, True", "pure", ["webodm_core.metering.test_timeutil", "webodm_core.metering.test_rules"]),
    ("failed-run-counts", M + "rules.py", "    elif status == \"Failed\":\n        reason = WAIVED_FAILED",
     "    elif status == \"Failed\":\n        reason = None", "pure", ["webodm_core.metering.test_rules"]),
    ("waived-in-quantity", M + "rules.py",
     "        quantity=measured if reason is None else 0,\n        waived_quantity=0 if reason is None else measured,",
     "        quantity=measured,\n        waived_quantity=0,", "pure", ["webodm_core.metering.test_rules"]),
    ("open-machine-recorded", M + "sweep.py",
     "        WHERE (i.released_at IS NOT NULL OR i.terminated_at IS NOT NULL) {condition}",
     "        WHERE 1 = 1 {condition}", "frappe", ["webodm_core.metering.test_sweep"]),
    ("unfinished-run-recorded", M + "rules.py",
     "        raise ValueError(f\"the run is not finished (status {status!r})\")",
     "        reason = WAIVED_DELETED_BEFORE_FINISHING", "pure", ["webodm_core.metering.test_rules"]),
    ("cost-estimate-copied", M + "sweep.py", '        "task": row.task or None,\n        "site_time_zone": site_time_zone(),\n        "work_evidence": work_evidence,',
     '        "task": row.task or None,\n        "estimated_cost": frappe.db.get_value(INSTANCE, row.name, "estimated_cost"),\n        "site_time_zone": site_time_zone(),\n        "work_evidence": work_evidence,',
     "frappe", ["webodm_core.metering.test_sweep"]),
    ("sum-through-float", M + "storage.py", "                   CONCAT(COALESCE(SUM(x.size), 0), '') AS bytes,",
     "                   COALESCE(SUM(x.size), 0) AS bytes,", "frappe", ["webodm_core.metering.test_storage"]),
    # --- inventing history ----------------------------------------------------
    ("sample-yesterday", M + "storage.py", "    day = now.date()\n    key = ledger.storage_key",
     "    import datetime as _dt\n    day = now.date() - _dt.timedelta(days=1)\n    key = ledger.storage_key", "frappe",
     ["webodm_core.metering.test_storage"]),
    ("gap-reported-as-measured", M + "report.py",
     "            else:\n                out[\"not_measured\"].append(day.isoformat())",
     "            else:\n                out[\"measured\"] += 1", "frappe", ["webodm_core.metering.test_report"]),
    ("in-progress-in-totals", M + "report.py",
     "        running = in_progress(organization, now=now) if end > now else []",
     "        running = in_progress(organization, now=now) if end > now else []\n"
     "        meters[rules.METER_COMPUTE][\"quantity\"] += sum(i[\"elapsed_so_far\"] for i in running)",
     "frappe", ["webodm_core.metering.test_report"]),
    # --- a broken job ---------------------------------------------------------
    ("guard-no-rollback", M + "guard.py", "                frappe.db.rollback(save_point=name)", "                pass",
     "frappe", ["webodm_core.metering.test_no_harm"]),
    ("hook-not-guarded", M + "hooks.py",
     "        with guard.isolated(f\"recording {what} {doc.name} before it is deleted\") as ok:\n            if ok:\n                getattr(sweep, function)(doc, deleting=True)",
     "        getattr(sweep, function)(doc, deleting=True)\n        raise_if_broken = None", "frappe",
     ["webodm_core.metering.test_no_harm"]),
    ("hook-imports-at-top", M + "hooks.py", "import frappe\n\n\ndef before_run_delete",
     "import frappe\n\nfrom webodm_core.metering import guard, sweep\n\n\ndef before_run_delete", "frappe",
     ["webodm_core.metering.test_no_harm"]),
    ("stamp-no-savepoint-rollback", "webodm_core/webodm_core/processing/compute.py",
     "        try:\n            frappe.db.rollback(save_point=savepoint)\n        except Exception:\n            pass\n        try:\n            frappe.log_error(f\"{instance_name}: could not record {field}",
     "        try:\n            frappe.log_error(f\"{instance_name}: could not record {field}", "frappe",
     ["webodm_core.metering.test_no_harm"]),
    ("dispatch-stamp-unguarded", "webodm_core/webodm_core/processing/compute.py",
     "    try:\n        stamp_once((node or {}).get(\"compute_instance\"), \"dispatched_at\")\n    except Exception:\n        pass",
     "    stamp_once((node or {}).get(\"compute_instance\"), \"dispatched_at\")", "frappe",
     ["webodm_core.metering.test_no_harm"]),
    ("release-stamp-unguarded", "webodm_core/webodm_core/processing/compute.py",
     "    try:\n        stamp_once(instance_name, \"released_at\")\n        if reason:\n            stamp_once(instance_name, \"release_reason\", reason)\n    except Exception:\n        pass",
     "    stamp_once(instance_name, \"released_at\")\n    if reason:\n        stamp_once(instance_name, \"release_reason\", reason)",
     "frappe", ["webodm_core.metering.test_no_harm"]),
    ("sweep-one-failure-stops-all", M + "sweep.py",
     "            with guard.isolated(f\"recording {kind[:-1]} {name}\", once_key=f\"{kind}:{name}\") as ok:\n                if ok:\n                    _, outcome[\"created\"] = record_one(name)",
     "            _, outcome[\"created\"] = record_one(name)", "frappe", ["webodm_core.metering.test_sweep"]),
    # --- the pipeline's facts ---------------------------------------------------
    ("no-dispatch-stamp", "webodm_core/webodm_core/processing/task_runner.py", "    compute.mark_dispatched(node)\n", "",
     "frappe", ["webodm_core.metering.test_pipeline", "webodm_core.metering.test_no_harm"]),
    ("no-release-stamp", "webodm_core/webodm_core/processing/compute.py",
     "    if inst.status != \"Terminating\" and not cint(inst.destroy_attempts):\n        mark_released(inst.name, reason)\n", "",
     "frappe", ["webodm_core.metering.test_pipeline"]),
    ("retry-stamps-release", "webodm_core/webodm_core/processing/compute.py",
     "    if inst.status != \"Terminating\" and not cint(inst.destroy_attempts):\n", "    if True:\n",
     "frappe", ["webodm_core.metering.test_sweep"]),
    ("stamp-overwrites", "webodm_core/webodm_core/processing/compute.py",
     "        unset = f\"`{field}` IS NULL\" if kind == \"time\" else f\"(`{field}` IS NULL OR `{field}` = '')\"",
     "        unset = \"1 = 1\"", "frappe", ["webodm_core.metering.test_sweep", "webodm_core.metering.test_pipeline"]),
    ("reaper-filter-reverted", "webodm_core/webodm_core/processing/compute.py",
     '        filters={"status": "Failed", "terminated_at": None, "handle": ["!=", ""]},',
     '        filters={"status": "Failed", "terminated_at": ["is", "not set"], "handle": ["!=", ""]},', "frappe",
     ["webodm_core.webodm_core.processing.test_compute_database"]),
    ("no-capture-on-delete", M + "hooks.py", "    _capture(\"record_run\", doc, \"plugin run\")", "    return",
     "frappe", ["webodm_core.metering.test_sweep"]),
    # --- leaking between customers ---------------------------------------------
    ("list-not-scoped", "webodm_core/permissions.py",
     '    return _org_query_conditions("WebODM Usage Record", user or frappe.session.user)', "    return None",
     "frappe", ["webodm_core.api.test_usage"]),
    ("doc-not-scoped", "webodm_core/permissions.py",
     "    if ptype not in (\"read\", \"select\", \"report\", \"export\", \"print\", \"email\"):\n        return False\n    return _org_has_permission(doc, user or frappe.session.user)",
     "    if ptype not in (\"read\", \"select\", \"report\", \"export\", \"print\", \"email\"):\n        return False\n    return True",
     "frappe", ["webodm_core.api.test_usage"]),
    ("write-allowed-by-hook", "webodm_core/permissions.py",
     "    if ptype not in (\"read\", \"select\", \"report\", \"export\", \"print\", \"email\"):\n        return False\n",
     "", "frappe", ["webodm_core.metering.test_ledger"]),
    ("api-trusts-org-param", "webodm_core/api/usage.py",
     "    if requested and requested != own:\n        frappe.throw(_NOT_PERMITTED, frappe.PermissionError)\n    return own",
     "    return requested or own", "frappe", ["webodm_core.api.test_usage"]),
    ("api-record-not-scoped", "webodm_core/api/usage.py",
     "    if not row or (not is_admin and row[\"organization\"] != own):", "    if not row:", "frappe",
     ["webodm_core.api.test_usage"]),
    # The storage query restricts to the organization twice: in the subquery
    # (so it does not aggregate every organization's files) and in the join.
    # Removing either one alone changes no result; removing both leaks, and
    # is the fault after these two.
    ("storage-all-orgs", M + "storage.py", "            WHERE d.organization = %(organization)s\n            GROUP BY d.name",
     "            WHERE 1 = 1\n            GROUP BY d.name", "frappe", ["webodm_core.metering.test_storage"], EQUIVALENT),
    ("storage-inner-all-orgs", M + "storage.py",
     "                      SELECT name FROM `tab{doctype}` WHERE organization = %(organization)s\n",
     "                      SELECT name FROM `tab{doctype}`\n", "frappe", ["webodm_core.metering.test_storage"], EQUIVALENT),
    ("storage-no-org-filter", M + "storage.py",
     ["            WHERE d.organization = %(organization)s\n            GROUP BY d.name",
      "                      SELECT name FROM `tab{doctype}` WHERE organization = %(organization)s\n"],
     ["            WHERE 1 = 1\n            GROUP BY d.name", "                      SELECT name FROM `tab{doctype}`\n"],
     "frappe", ["webodm_core.metering.test_storage"]),
    ("guard-reraises", M + "guard.py", "        log(f\"{what}: {e.__class__.__name__}: {e}\", once_key)\n    else:",
     "        log(f\"{what}: {e.__class__.__name__}: {e}\", once_key)\n        raise\n    else:", "frappe",
     ["webodm_core.metering.test_guard", "webodm_core.metering.test_sweep", "webodm_core.metering.test_storage"]),
    ("summary-all-orgs", M + "report.py", '        ["organization", "=", organization],\n        ["period_end_utc", ">", after],',
     '        ["period_end_utc", ">", after],', "frappe", ["webodm_core.metering.test_report"]),
    ("cross-org-project", M + "sweep.py",
     '    return frappe.db.get_value(TASK, {"name": task, "organization": organization}, "project")',
     '    return frappe.db.get_value(TASK, task, "project")', "frappe", ["webodm_core.metering.test_sweep"]),
    ("cross-org-plugin-facts", M + "sweep.py",
     '    if not plugin or not (plugin.plugin_type == "System" or plugin.organization == row.organization):',
     '    if not plugin:', "frappe", ["webodm_core.metering.test_sweep"]),
    # --- found by the independent review -----------------------------------------
    ("future-as-of-accepted", M + "report.py", "    if as_of > now:", "    if False:", "frappe",
     ["webodm_core.metering.test_report"]),
    ("open-day-counted", M + "report.py", "    counted_until = min(end, reference)", "    counted_until = end", "frappe",
     ["webodm_core.metering.test_report"]),
    ("period-unbounded", M + "report.py", "    if (end - start).days > MAX_PERIOD_DAYS:", "    if False:", "frappe",
     ["webodm_core.metering.test_report"]),
    ("no-check-after-insert", "webodm_core/webodm_core/doctype/webodm_usage_record/webodm_usage_record.py",
     "        # its hash is never left behind.\n        self._check_hash()", "        # its hash is never left behind.\n        pass",
     "frappe", ["webodm_core.metering.test_ledger"]),
    ("hash-drops-subseconds", M + "rules.py", "            value = timeutil.iso_exact(timeutil.as_utc(value))",
     "            value = timeutil.iso_z(timeutil.as_utc(value))", "pure", ["webodm_core.metering.test_rules"]),
    ("trace-crashes-on-bad-evidence", M + "ledger.py", "    except ValueError:\n        return None, True",
     "    except ZeroDivisionError:\n        return None, True", "frappe", ["webodm_core.metering.test_verify"]),
    ("sweep-ignores-time-budget", M + "sweep.py", "            if recorded >= max_records or time.monotonic() >= deadline:",
     "            if recorded >= max_records:", "frappe", ["webodm_core.metering.test_sweep"]),
    # --- a total that cannot be rebuilt / immutability ------------------------
    ("save-allowed", "webodm_core/webodm_core/doctype/webodm_usage_record/webodm_usage_record.py",
     "    def validate(self):\n        if not self.is_new():\n            frappe.throw(IMMUTABLE_MESSAGE, UsageRecordImmutable)",
     "    def validate(self):\n        pass", "frappe", ["webodm_core.metering.test_ledger"]),
    ("delete-allowed", "webodm_core/webodm_core/doctype/webodm_usage_record/webodm_usage_record.py",
     "    def on_trash(self):\n        frappe.throw(IMMUTABLE_MESSAGE, UsageRecordImmutable)", "    def on_trash(self):\n        pass",
     "frappe", ["webodm_core.metering.test_ledger"]),
    ("anyone-inserts", "webodm_core/webodm_core/doctype/webodm_usage_record/webodm_usage_record.py",
     "        if not self.flags.from_ledger:", "        if False:", "frappe", ["webodm_core.metering.test_ledger"]),
    ("verify-skips-hash", M + "verify.py", "    if rules.record_hash(row) != row[\"record_hash\"]:", "    if False:", "frappe",
     ["webodm_core.metering.test_verify"]),
    ("verify-skips-rederive", M + "verify.py", "        if not _same(stored, again[field]):", "        if False:", "frappe",
     ["webodm_core.metering.test_verify"]),
    ("hash-ignores-quantity", M + "rules.py", '    "quantity", "waived_quantity", "waived_reason",\n    "period_start_utc"',
     '    "waived_quantity", "waived_reason",\n    "period_start_utc"', "pure", ["webodm_core.metering.test_rules"]),
    ("reversal-wrong-sign", M + "ledger.py", '        "quantity": -int(original.quantity or 0),',
     '        "quantity": int(original.quantity or 0),', "frappe", ["webodm_core.metering.test_ledger"]),
    ("evidence-drops-inputs", M + "rules.py", '        "inputs": inputs,\n        "derived": result["derived"],',
     '        "inputs": {},\n        "derived": result["derived"],', "pure", ["webodm_core.metering.test_rules"]),
]



CONTROLS = ("control", "control-pure")


def run_tests(kind, modules, copy):
    if kind == "pure":
        proc = subprocess.run([sys.executable, "-m", "unittest", *modules], cwd=copy, capture_output=True, text=True)
        failed = [line.split()[1] for line in proc.stderr.splitlines() if line.startswith(("FAIL: ", "ERROR: "))]
        return proc.returncode == 0, ", ".join(dict.fromkeys(failed))[:300]
    ok, failed = True, []
    for module in modules:
        cmd = ["docker", "run", "--rm", "--network", f"{PREFIX}-test",
               "-v", f"{PREFIX}-sites:/workspace/frappe-bench/sites", "-v", f"{copy}:/workspace/webodm_core"]
        for item in ENV:
            cmd += ["-e", item]
        cmd += [IMG, "/workspace/frappe-bench/env/bin/python", "-m", "frappe.utils.bench_helper", "frappe", "--site",
                "ci.localhost", "run-tests", "--app", "webodm_core", "--module", module]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        out = proc.stdout + proc.stderr
        ok = ok and proc.returncode == 0 and "FAILED" not in out
        failed += [line.split("\u2716")[-1].strip(" \x1b[0m") for line in out.splitlines() if "\u2716" in line]
    return ok, ", ".join(dict.fromkeys(failed))[:300]


def main():
    wanted = set(sys.argv[1:])
    unknown = wanted - {fault[0] for fault in FAULTS}
    if unknown:
        sys.exit(f"unknown fault id(s): {sorted(unknown)}")
    results = []
    with tempfile.TemporaryDirectory(prefix="metering-faults-") as tmp:
        copy = Path(tmp) / "webodm_core"
        for ident, rel, old, new, kind, modules, *flags in FAULTS:
            if wanted and ident not in wanted:
                continue
            if copy.exists():
                shutil.rmtree(copy)
            shutil.copytree(APP, copy, ignore=shutil.ignore_patterns("__pycache__", ".git"))
            path = copy / rel
            text = path.read_text()
            pairs = list(zip(old, new, strict=True)) if isinstance(old, list) else [(old, new)]
            missing = [o for o, _ in pairs if text.count(o) != 1]
            if missing:
                results.append((ident, "NOT APPLIED", f"pattern not found exactly once: {missing[0][:70]!r}"))
            else:
                for o, n in pairs:
                    text = text.replace(o, n)
                path.write_text(text)
                passed, detail = run_tests(kind, modules, copy)
                if not passed:
                    outcome = "caught"
                elif ident in CONTROLS:
                    outcome = "survived (control: expected)"
                elif EQUIVALENT in flags:
                    outcome = "survived (equivalent: expected)"
                else:
                    outcome = "SURVIVED"
                results.append((ident, outcome, detail))
            print(f"{results[-1][0]:36} {results[-1][1]:32} {results[-1][2][:150]}", flush=True)

    faults = [r for r in results if r[0] not in CONTROLS]
    caught = [r for r in faults if r[1] == "caught"]
    bad = [r for r in results if r[1] in ("SURVIVED", "NOT APPLIED")
           or (r[0] in CONTROLS and r[1] == "caught")]
    print(f"\n{len(faults)} faults: {len(caught)} caught, "
          f"{sum(1 for r in faults if 'equivalent' in r[1])} equivalent, {len(bad)} to look at")
    for r in bad:
        print("  ", r)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
