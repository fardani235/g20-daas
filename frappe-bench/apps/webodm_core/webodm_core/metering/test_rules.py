"""The metering rules: source facts in, a quantity out. No site needed.

These pin what counts and what the platform absorbs. Every case is a fact
pattern the pipeline can actually produce (see ``processing/compute.py``,
``plugins/runner.py``), plus the ones a rule must refuse.
"""

import datetime as dt
import json
import unittest

from webodm_core.metering import rules, timeutil


def d(text):
    return dt.datetime.fromisoformat(text)


def instance(**kw):
    """A machine that became Ready, did a job for an hour and was released."""
    facts = {
        "status": "Terminated",
        "requested_at": "2026-10-19 09:55:00",
        "ready_at": "2026-10-19 10:00:00",
        "dispatched_at": "2026-10-19 10:00:40",
        "released_at": "2026-10-19 11:00:00",
        "terminated_at": "2026-10-19 11:00:05",
        "destroy_attempts": 0,
        "release_reason": "task:Completed",
        "provider": "aws",
        "instance_class": "cpu",
        "task": "T-1",
        "site_time_zone": "UTC",
        "work_evidence": "dispatched_at",
        "captured": rules.CAPTURED_BY_SWEEP,
        "observed_at_utc": "2026-10-19T11:03:00Z",
    }
    facts.update(kw)
    return facts


def run(**kw):
    facts = {
        "status": "Completed",
        "creation": "2026-10-19 10:00:00",
        "started_at": "2026-10-19 10:00:02",
        "completed_at": "2026-10-19 10:05:02",
        "modified": "2026-10-19 10:05:02",
        "plugin": "acme.change-detection",
        "site_time_zone": "UTC",
        "captured": rules.CAPTURED_BY_SWEEP,
        "observed_at_utc": "2026-10-19T10:06:00Z",
    }
    facts.update(kw)
    return facts


class TestComputeCounts(unittest.TestCase):
    def test_useful_machine_counts_ready_to_release(self):
        out = rules.compute_usage(instance())
        self.assertEqual(out["quantity"], 3600)
        self.assertEqual((out["waived_quantity"], out["waived_reason"]), (0, None))
        self.assertEqual(out["unit"], "second")
        self.assertEqual(out["dimension"], "aws/cpu")
        self.assertEqual(out["period_start_utc"], d("2026-10-19 10:00:00"))
        self.assertEqual(out["period_end_utc"], d("2026-10-19 11:00:00"))
        self.assertEqual(out["caveats"], [])

    def test_time_before_ready_is_never_counted(self):
        # Five minutes of boot are the platform's, not the customer's.
        self.assertEqual(rules.compute_usage(instance(requested_at="2026-10-19 08:00:00"))["quantity"], 3600)

    def test_interval_ends_at_release_not_at_confirmed_destroy(self):
        # The destroy failed and was retried for an hour: our problem.
        out = rules.compute_usage(instance(terminated_at="2026-10-19 12:00:05", destroy_attempts=7))
        self.assertEqual(out["quantity"], 3600)
        self.assertEqual(out["derived"]["end_basis"], "released_at")

    def test_recorded_while_destroy_is_still_being_retried(self):
        out = rules.compute_usage(instance(status="Terminating", terminated_at=None, destroy_attempts=3))
        self.assertEqual(out["quantity"], 3600)

    def test_sub_second_edges_round_toward_the_customer(self):
        out = rules.compute_usage(instance(ready_at="2026-10-19 10:00:00.000001",
                                           released_at="2026-10-19 11:00:00.999999"))
        self.assertEqual(out["quantity"], 3599)

    def test_site_time_zone_is_applied(self):
        out = rules.compute_usage(instance(site_time_zone="Asia/Jakarta"))
        self.assertEqual(out["quantity"], 3600)
        self.assertEqual(out["period_end_utc"], d("2026-10-19 04:00:00"))

    def test_cancelled_and_failed_after_work_started_count(self):
        # Built to the letter of the brief ("failed *before* doing any work");
        # see design.md Q1. The reason stays on the record either way.
        for reason in ("task:Cancelled", "task:Failed", "task-deleted", "task-reassigned"):
            self.assertEqual(rules.compute_usage(instance(release_reason=reason))["quantity"], 3600, reason)


class TestComputeWaived(unittest.TestCase):
    def assertWaived(self, out, reason, seconds):
        self.assertEqual(out["quantity"], 0)
        self.assertEqual(out["waived_reason"], reason)
        self.assertEqual(out["waived_quantity"], seconds)

    def test_never_ready(self):
        out = rules.compute_usage(instance(status="Failed", ready_at=None, dispatched_at=None, work_evidence=None,
                                           released_at="2026-10-19 10:10:00", release_reason="provisioning-timeout"))
        self.assertWaived(out, rules.WAIVED_NEVER_READY, 15 * 60)  # requested -> released: what we absorbed

    def test_create_call_failed_no_machine_ever(self):
        out = rules.compute_usage(instance(status="Failed", ready_at=None, dispatched_at=None, work_evidence=None,
                                           released_at=None, terminated_at="2026-10-19 09:55:00.500000",
                                           release_reason=None))
        self.assertWaived(out, rules.WAIVED_NEVER_READY, 0)

    def test_no_work_dispatched(self):
        out = rules.compute_usage(instance(dispatched_at=None, work_evidence=None, release_reason="task:Failed"))
        self.assertWaived(out, rules.WAIVED_NO_WORK, 3600)

    def test_killed_by_the_lifetime_budget(self):
        out = rules.compute_usage(instance(release_reason="lifetime-exceeded"))
        self.assertWaived(out, rules.WAIVED_PLATFORM_FAILURE, 3600)

    def test_killed_by_the_budget_while_destroy_was_retried(self):
        # destroy_instance overwrites status Failed with Terminating when the
        # destroy fails; the reason is what survives.
        out = rules.compute_usage(instance(status="Terminated", release_reason="lifetime-exceeded", destroy_attempts=2))
        self.assertWaived(out, rules.WAIVED_PLATFORM_FAILURE, 3600)

    def test_closed_as_failed_without_a_reason(self):
        out = rules.compute_usage(instance(status="Failed", release_reason=None))
        self.assertWaived(out, rules.WAIVED_PLATFORM_FAILURE, 3600)

    def test_old_row_whose_destroy_was_retried_has_no_trustworthy_end(self):
        out = rules.compute_usage(instance(released_at=None, release_reason=None, destroy_attempts=2,
                                           terminated_at="2026-10-19 13:00:00"))
        self.assertWaived(out, rules.WAIVED_END_NOT_RECORDED, 3 * 3600)

    def test_old_row_destroyed_first_time_uses_terminated_at(self):
        out = rules.compute_usage(instance(released_at=None, release_reason=None, destroy_attempts=0,
                                           dispatched_at=None, work_evidence="task_link"))
        self.assertEqual(out["quantity"], 3605)
        self.assertEqual(len(out["caveats"]), 2)  # both fallbacks are stated on the record

    def test_end_before_start(self):
        out = rules.compute_usage(instance(released_at="2026-10-19 09:59:00"))
        self.assertWaived(out, rules.WAIVED_INVALID_INTERVAL, 0)
        self.assertEqual(out["period_start_utc"], out["period_end_utc"])

    def test_interval_inside_a_repeated_hour_is_waived_not_guessed(self):
        out = rules.compute_usage(instance(site_time_zone="America/New_York",
                                           requested_at="2026-11-01 01:05:00", ready_at="2026-11-01 01:10:00",
                                           dispatched_at="2026-11-01 01:11:00", released_at="2026-11-01 01:50:00",
                                           terminated_at="2026-11-01 01:50:05"))
        self.assertEqual(out["quantity"], 0)
        self.assertTrue(out["derived"]["ambiguous_local_time"])

    def test_interval_touching_a_repeated_hour_is_shortened_and_says_so(self):
        # 00:30 EDT -> "01:30" (either 05:30Z or 06:30Z). Real duration is 1 h or 2 h; we record 1 h.
        out = rules.compute_usage(instance(site_time_zone="America/New_York",
                                           requested_at="2026-11-01 00:25:00", ready_at="2026-11-01 00:30:00",
                                           dispatched_at="2026-11-01 00:31:00", released_at="2026-11-01 01:30:00",
                                           terminated_at="2026-11-01 01:30:05"))
        self.assertEqual(out["quantity"], 3600)
        self.assertTrue(out["caveats"])

    def test_waived_usage_is_never_in_quantity(self):
        for facts in (instance(ready_at=None, work_evidence=None), instance(work_evidence=None),
                      instance(release_reason="lifetime-exceeded")):
            out = rules.compute_usage(facts)
            self.assertEqual(out["quantity"], 0)
            self.assertTrue(out["waived_reason"])


class TestComputeRefusals(unittest.TestCase):
    def test_a_machine_that_is_still_up_is_not_recordable(self):
        with self.assertRaises(ValueError):
            rules.compute_usage(instance(status="Ready", released_at=None, terminated_at=None))

    def test_unless_it_is_being_deleted(self):
        out = rules.compute_usage(instance(status="Ready", released_at=None, terminated_at=None,
                                           captured=rules.CAPTURED_BEFORE_DELETE))
        self.assertEqual(out["quantity"], 0)
        self.assertEqual(out["waived_reason"], rules.WAIVED_DELETED_BEFORE_RELEASE)
        self.assertEqual(out["waived_quantity"], 63 * 60)  # ready -> the moment the recorder looked
        self.assertEqual(out["period_end_utc"], d("2026-10-19 11:03:00"))

    def test_dimension_never_empty(self):
        self.assertEqual(rules.compute_dimension(None, ""), "unknown/unknown")
        self.assertEqual(rules.compute_dimension("aws", "cpu-large"), "aws/cpu-large")


class TestAnalysisRun(unittest.TestCase):
    def test_completed_counts_one(self):
        out = rules.analysis_run_usage(run())
        self.assertEqual((out["quantity"], out["waived_quantity"], out["unit"]), (1, 0, "run"))
        self.assertEqual(out["dimension"], "acme.change-detection")
        self.assertEqual(out["period_start_utc"], d("2026-10-19 10:00:02"))
        self.assertEqual(out["period_end_utc"], d("2026-10-19 10:05:02"))
        self.assertEqual(out["derived"]["duration_seconds"], 300)

    def test_failed_and_cancelled_are_waived(self):
        for status, reason in (("Failed", rules.WAIVED_FAILED), ("Cancelled", rules.WAIVED_CANCELLED)):
            out = rules.analysis_run_usage(run(status=status))
            self.assertEqual((out["quantity"], out["waived_quantity"], out["waived_reason"]), (0, 1, reason))

    def test_cancelled_before_it_started(self):
        out = rules.analysis_run_usage(run(status="Cancelled", started_at=None))
        self.assertEqual(out["period_start_utc"], out["period_end_utc"])
        self.assertEqual(out["derived"]["duration_seconds"], 0)

    def test_unfinished_run_is_refused(self):
        for status in ("Queued", "Running", None, "Whatever"):
            with self.assertRaises(ValueError):
                rules.analysis_run_usage(run(status=status, completed_at=None))

    def test_unfinished_run_being_deleted_is_waived(self):
        out = rules.analysis_run_usage(run(status="Running", completed_at=None,
                                           captured=rules.CAPTURED_BEFORE_DELETE))
        self.assertEqual((out["quantity"], out["waived_quantity"]), (0, 1))
        self.assertEqual(out["waived_reason"], rules.WAIVED_DELETED_BEFORE_FINISHING)
        self.assertEqual(out["period_end_utc"], d("2026-10-19 10:06:00"))

    def test_completed_run_being_deleted_still_counts(self):
        out = rules.analysis_run_usage(run(captured=rules.CAPTURED_BEFORE_DELETE))
        self.assertEqual(out["quantity"], 1)

    def test_missing_completed_at_falls_back_and_says_so(self):
        out = rules.analysis_run_usage(run(completed_at=None, modified="2026-10-19 10:07:00"))
        self.assertEqual(out["period_end_utc"], d("2026-10-19 10:07:00"))
        self.assertTrue(out["caveats"])


class TestStorage(unittest.TestCase):
    def inputs(self, lines=None, **kw):
        base = {"day": "2026-10-19", "sampled_at_utc": "2026-10-19T00:07:00Z", "basis": "file-records",
                "lines": lines if lines is not None else [
                    {"doctype": "WebODM Dataset", "name": "ds1", "label": "Quarry", "files": 3, "bytes": 3000, "unsized": 0},
                    {"doctype": "WebODM Task", "name": "t1", "label": "Survey", "files": 2, "bytes": 7_000_000_000, "unsized": 0},
                ]}
        base.update(kw)
        return base

    def test_quantity_is_the_sum_of_the_lines(self):
        out = rules.storage_usage(self.inputs())
        self.assertEqual(out["quantity"], 7_000_003_000)
        self.assertEqual(out["unit"], "byte-day")
        self.assertEqual(out["dimension"], "standard")
        self.assertEqual((out["period_start_utc"], out["period_end_utc"]),
                         (d("2026-10-19 00:00:00"), d("2026-10-20 00:00:00")))
        self.assertEqual(out["derived"]["by_doctype"]["WebODM Task"], {"documents": 1, "files": 2, "bytes": 7_000_000_000})
        self.assertEqual(out["derived"]["files"], 5)

    def test_nothing_stored_is_a_measurement_of_zero(self):
        out = rules.storage_usage(self.inputs(lines=[]))
        self.assertEqual(out["quantity"], 0)
        self.assertEqual(out["derived"]["documents"], 0)

    def test_sizes_beyond_32_and_53_bits_stay_exact(self):
        big = 2 ** 60 + 1  # not representable as a float
        out = rules.storage_usage(self.inputs(lines=[
            {"doctype": "WebODM Task", "name": "t1", "label": "x", "files": 1, "bytes": big, "unsized": 0},
            {"doctype": "WebODM Task", "name": "t2", "label": "y", "files": 1, "bytes": 1, "unsized": 0},
        ]))
        self.assertEqual(out["quantity"], 2 ** 60 + 2)

    def test_unsized_files_are_a_visible_caveat(self):
        out = rules.storage_usage(self.inputs(lines=[
            {"doctype": "WebODM Dataset", "name": "ds1", "label": "x", "files": 4, "bytes": 10, "unsized": 2}]))
        self.assertEqual(out["derived"]["unsized_files"], 2)
        self.assertIn("2 file(s)", out["caveats"][0])

    def test_a_document_listed_twice_is_refused(self):
        line = {"doctype": "WebODM Dataset", "name": "ds1", "label": "x", "files": 1, "bytes": 10, "unsized": 0}
        with self.assertRaises(ValueError):
            rules.storage_usage(self.inputs(lines=[line, dict(line)]))

    def test_negative_sizes_are_refused(self):
        with self.assertRaises(ValueError):
            rules.storage_usage(self.inputs(lines=[
                {"doctype": "WebODM Dataset", "name": "ds1", "label": "x", "files": 1, "bytes": -1, "unsized": 0}]))

    def test_a_measurement_cannot_be_filed_under_another_day(self):
        with self.assertRaises(ValueError):
            rules.storage_usage(self.inputs(day="2026-10-18"))
        with self.assertRaises(ValueError):
            rules.storage_usage(self.inputs(sampled_at_utc="2026-10-20T00:00:00Z"))


class TestOutcomeInvariants(unittest.TestCase):
    def test_counted_and_waived_are_exclusive(self):
        with self.assertRaises(ValueError):
            rules.outcome(meter="compute", dimension="x", quantity=1, waived_quantity=1, waived_reason="r",
                          period_start_utc=d("2026-10-19 00:00:00"), period_end_utc=d("2026-10-19 00:00:01"))

    def test_waived_needs_a_reason_and_nothing_is_negative(self):
        common = dict(meter="compute", dimension="x", period_start_utc=d("2026-10-19 00:00:00"),
                      period_end_utc=d("2026-10-19 00:00:01"))
        with self.assertRaises(ValueError):
            rules.outcome(quantity=0, waived_quantity=5, waived_reason=None, **common)
        with self.assertRaises(ValueError):
            rules.outcome(quantity=-1, waived_quantity=0, waived_reason=None, **common)
        with self.assertRaises(ValueError):
            rules.outcome(meter="compute", dimension="x", quantity=0, waived_quantity=0, waived_reason=None,
                          period_start_utc=d("2026-10-19 00:00:01"), period_end_utc=d("2026-10-19 00:00:00"))


class TestEvidence(unittest.TestCase):
    """A record must be rebuildable from what it stores, with the source gone."""

    def test_rederive_reproduces_every_rule(self):
        cases = [
            (rules.RULE_COMPUTE, instance()),
            (rules.RULE_COMPUTE, instance(work_evidence=None, dispatched_at=None)),
            (rules.RULE_ANALYSIS_RUN, run()),
            (rules.RULE_ANALYSIS_RUN, run(status="Failed")),
            (rules.RULE_STORAGE, TestStorage().inputs()),
        ]
        for rule, inputs in cases:
            first = rules.RULES[rule](inputs)
            # Through JSON and back: this is what the database gives us years later.
            evidence = json.loads(rules.canonical_json(rules.evidence_for(rule, inputs, first)))
            again = rules.rederive(evidence)
            for field in ("meter", "unit", "dimension", "quantity", "waived_quantity", "waived_reason",
                          "period_start_utc", "period_end_utc"):
                self.assertEqual(first[field], again[field], (rule, field))
            self.assertTrue(evidence["not_checked"], "every record states what it did not check")

    def test_unknown_rule(self):
        with self.assertRaises(ValueError):
            rules.rederive({"rule": "compute/v9", "inputs": {}})

    def test_no_cost_figure_is_ever_an_input(self):
        # The platform's cost estimates are floats for a progress screen. They
        # must not reach a usage record by any road (design.md F6).
        text = rules.canonical_json(rules.evidence_for(rules.RULE_COMPUTE, instance(), rules.compute_usage(instance())))
        for word in ("cost", "price", "amount", "currency"):
            self.assertNotIn(word, text.lower())


class TestHash(unittest.TestCase):
    def row(self, **kw):
        row = {
            "organization": "Acme", "meter": "compute", "dimension": "aws/cpu", "entry_type": "usage", "unit": "second",
            "quantity": 3600, "waived_quantity": 0, "waived_reason": None,
            "period_start_utc": d("2026-10-19 10:00:00"), "period_end_utc": d("2026-10-19 11:00:00"),
            "recorded_at_utc": d("2026-10-19 11:03:00"),
            "source_doctype": "WebODM Compute Instance", "source_name": "ci1", "task": "T-1", "project": "P",
            "rule": "compute/v1", "idempotency_key": "compute:ci1", "reverses": None, "note": None,
            "caveats": None, "recorded_by": "Administrator", "evidence": "{}",
        }
        row.update(kw)
        return row

    def test_stable_across_the_forms_the_database_returns(self):
        base = rules.record_hash(self.row())
        # datetime vs string, int vs numeric string, None vs "" must not matter.
        self.assertEqual(base, rules.record_hash(self.row(period_end_utc="2026-10-19 11:00:00", quantity="3600",
                                                          waived_reason="", note="")))
        self.assertEqual(len(base), 64)

    def test_every_hashed_field_changes_the_hash(self):
        base = rules.record_hash(self.row())
        changes = {
            "organization": "Other", "meter": "storage", "dimension": "aws/cpu-large", "entry_type": "reversal",
            "unit": "run", "quantity": 3601, "waived_quantity": 1, "waived_reason": "x",
            "period_start_utc": d("2026-10-19 10:00:01"), "period_end_utc": d("2026-10-19 11:00:01"),
            "recorded_at_utc": d("2026-10-19 11:03:01"), "source_doctype": "X", "source_name": "ci2",
            "task": "T-2", "project": "Q", "rule": "compute/v2", "idempotency_key": "compute:ci2",
            "reverses": "abc", "note": "n", "caveats": "c", "recorded_by": "someone", "evidence": '{"a":1}',
        }
        self.assertEqual(set(changes), set(rules.HASHED_FIELDS))
        for field, value in changes.items():
            self.assertNotEqual(base, rules.record_hash(self.row(**{field: value})), field)

    def test_a_sub_second_nudge_to_an_instant_changes_the_hash(self):
        base = rules.record_hash(self.row())
        for field in ("period_start_utc", "period_end_utc", "recorded_at_utc"):
            nudged = self.row()[field] + dt.timedelta(microseconds=999999)
            self.assertNotEqual(base, rules.record_hash(self.row(**{field: nudged})), field)

    def test_canonical_json_is_order_independent(self):
        self.assertEqual(rules.canonical_json({"b": 1, "a": [1, {"d": 2, "c": "é"}]}),
                         '{"a":[1,{"c":"é","d":2}],"b":1}')
