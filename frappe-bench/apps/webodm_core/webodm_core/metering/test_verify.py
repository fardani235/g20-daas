"""Verification: the ledger checked against itself.

A total that cannot be rebuilt from its records is wrong. These tests make
the ledger wrong on purpose — behind the application's back, the only way
it can be done — and assert that verification says so, and says what.
They also pin what verification does *not* catch, because a check that is
trusted for more than it does is worse than no check.
"""

from __future__ import annotations

import json

import frappe
from frappe.tests.utils import FrappeTestCase

from webodm_core.metering import ledger, rules, sweep, verify
from webodm_core.metering import storage as storage_meter
from webodm_core.metering.testing import (
    INSTANCE,
    RUN,
    as_user,
    at,
    make_instance,
    make_org,
    make_run,
    make_user,
    wipe,
)
from webodm_core.testing import make_dataset


def _problems(result):
    return {(p["record"], p["problem"]) for p in result["problems"]}


def _kinds(result):
    return {p["problem"] for p in result["problems"]}


class _Base(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.org = make_org("Meter Verify Org")
        cls.other_org = make_org("Meter Verify Other")
        cls.user = make_user("meter_verify@example.com", cls.org)

    def setUp(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        wipe(self.org, self.other_org)
        self.instance = make_instance(self.org)
        self.waived = make_instance(self.org, dispatched_at=None)
        self.run = make_run(self.user)
        as_user(self.user)
        make_dataset("Verify images", images=[("IMG_0001.JPG", b"\xff" * 2000), ("IMG_0002.JPG", b"\xff" * 3000)])
        frappe.set_user("Administrator")
        with at("2026-10-19T00:05:00Z"):
            self.storage = storage_meter.sample(self.org)[0]
        with at("2026-10-19T12:00:00Z"):
            sweep.run()
        self.compute = ledger.exists(ledger.compute_key(self.instance))
        self.analysis = ledger.exists(ledger.analysis_run_key(self.run.name))

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        wipe(self.org, self.other_org)

    def tamper(self, name, **values):
        """Change a row the only way it can be changed: around the document API."""
        frappe.db.set_value(ledger.DOCTYPE, name, values, update_modified=False)

    def check(self, **kw):
        return verify.verify(organization=self.org, **kw)


class TestCleanLedger(_Base):
    def test_untouched_records_verify(self):
        result = self.check()
        self.assertTrue(result["ok"], result["problems"])
        self.assertEqual(result["checked"], 4)
        self.assertEqual(result["totals"], {
            "compute": {"quantity": 3600, "waived_quantity": 3600, "records": 2},
            "analysis_run": {"quantity": 1, "waived_quantity": 0, "records": 1},
            "storage": {"quantity": 5000, "waived_quantity": 0, "records": 1},
        })

    def test_a_reversal_verifies(self):
        ledger.reverse(self.compute, "recorded against the wrong machine class")
        result = self.check()
        self.assertTrue(result["ok"], result["problems"])
        self.assertEqual(result["totals"]["compute"], {"quantity": 0, "waived_quantity": 3600, "records": 3})

    def test_scope(self):
        self.assertEqual(self.check(start="2026-10-19", end="2026-10-20")["checked"], 4)
        self.assertEqual(self.check(start="2026-11-01", end="2026-12-01")["checked"], 0)
        self.assertEqual(verify.verify(organization=self.other_org)["checked"], 0)
        everything = verify.verify()
        self.assertGreaterEqual(everything["checked"], 4)


class TestTampering(_Base):
    def test_a_changed_quantity_is_caught_twice(self):
        self.tamper(self.compute, quantity=60)
        result = self.check()
        self.assertFalse(result["ok"])
        self.assertIn((self.compute, "hash_mismatch"), _problems(result))
        self.assertIn((self.compute, "quantity_mismatch"), _problems(result))
        # Only that record is implicated.
        self.assertEqual({p["record"] for p in result["problems"]}, {self.compute})

    def test_a_forger_who_also_fixes_the_hash_is_still_caught_by_the_evidence(self):
        row = frappe.db.get_value(ledger.DOCTYPE, self.compute, list(ledger.ALL_FIELDS), as_dict=True)
        row.quantity = 60
        self.tamper(self.compute, quantity=60, record_hash=rules.record_hash(row))
        kinds = {p["problem"] for p in self.check()["problems"] if p["record"] == self.compute}
        self.assertEqual(kinds, {"quantity_mismatch"})

    def test_usage_un_waived_is_caught(self):
        self.tamper(ledger.exists(ledger.compute_key(self.waived)), quantity=3600, waived_quantity=0, waived_reason=None)
        kinds = _kinds(self.check())
        self.assertTrue({"hash_mismatch", "quantity_mismatch", "waived_quantity_mismatch",
                         "waived_reason_mismatch"} <= kinds, kinds)

    def test_changed_evidence_is_caught(self):
        evidence = json.loads(frappe.db.get_value(ledger.DOCTYPE, self.storage, "evidence"))
        evidence["inputs"]["lines"][0]["bytes"] = 1
        self.tamper(self.storage, evidence=rules.canonical_json(evidence))
        kinds = _kinds(self.check())
        self.assertTrue({"hash_mismatch", "quantity_mismatch", "storage_lines_do_not_add_up"} <= kinds, kinds)

    def test_a_moved_interval_is_caught(self):
        self.tamper(self.compute, period_end_utc="2026-09-30 23:59:59")
        kinds = _kinds(verify.verify())
        self.assertTrue({"hash_mismatch", "period_end_utc_mismatch"} <= kinds, kinds)

    def test_a_record_moved_to_another_organization_is_caught(self):
        self.tamper(self.storage, organization=self.other_org)
        moved = verify.verify(organization=self.other_org)
        self.assertEqual(_problems(moved), {(self.storage, "hash_mismatch"), (self.storage, "key_mismatch")})

    def test_a_changed_dimension_or_rule_is_caught(self):
        self.tamper(self.compute, dimension="aws/cpu-xlarge")
        self.tamper(self.analysis, rule="analysis_run/v9")
        problems = _problems(self.check())
        self.assertIn((self.compute, "dimension_mismatch"), problems)
        self.assertIn((self.analysis, "rule_mismatch"), problems)

    def test_unreadable_evidence_is_caught(self):
        self.tamper(self.analysis, evidence="not json")
        self.assertIn((self.analysis, "evidence_unreadable"), _problems(self.check()))

    def test_an_instant_nudged_by_less_than_a_second_is_caught(self):
        row = frappe.db.get_value(ledger.DOCTYPE, self.compute, ["recorded_at_utc"], as_dict=True)
        self.tamper(self.compute, recorded_at_utc=row.recorded_at_utc.replace(microsecond=500000))
        self.assertIn((self.compute, "hash_mismatch"), _problems(self.check()))

    def test_a_reversal_that_does_not_negate_is_caught(self):
        reversal = ledger.reverse(self.compute, "why")
        self.tamper(reversal, quantity=-1)
        problems = _problems(self.check())
        self.assertIn((reversal, "reversal_does_not_negate"), problems)
        self.assertIn((reversal, "hash_mismatch"), problems)

    def test_an_original_changed_after_its_reversal_is_caught(self):
        reversal = ledger.reverse(self.compute, "why")
        row = frappe.db.get_value(ledger.DOCTYPE, self.compute, list(ledger.ALL_FIELDS), as_dict=True)
        row.note = "edited later"
        self.tamper(self.compute, note="edited later", record_hash=rules.record_hash(row))
        self.assertIn((reversal, "original_changed_since_reversal"), _problems(self.check()))

    def test_a_reversal_whose_original_is_gone_is_caught(self):
        reversal = ledger.reverse(self.compute, "why")
        frappe.db.delete(ledger.DOCTYPE, {"name": self.compute})
        self.assertIn((reversal, "reversal_without_original"), _problems(self.check()))

    def test_what_verification_cannot_see(self):
        # A row deleted behind the application leaves nothing to check: the
        # remaining rows still verify. Deletion is prevented by the controller
        # and by role permissions, not detected here. (docs/metering: limits.)
        frappe.db.delete(ledger.DOCTYPE, {"name": self.analysis})
        result = self.check()
        self.assertTrue(result["ok"])
        self.assertEqual(result["checked"], 3)
        # The sweep notices, though: the run has no record again, and gets one.
        self.assertEqual(sweep.outstanding_runs(self.org), [self.run.name])


class TestTrace(_Base):
    def test_a_record_explains_itself(self):
        out = verify.trace(self.compute)
        self.assertEqual(out["problems"], [])
        self.assertEqual(out["rederived"]["quantity"], 3600)
        self.assertTrue(out["rederived"]["matches_record"])
        self.assertEqual(out["source"], {"exists": True, "differs": [], "now": {}, "when_recorded": {}})
        self.assertEqual(out["record"]["evidence"]["inputs"]["ready_at"], "2026-10-19 10:00:00")

    def test_a_source_edited_after_recording_is_shown_not_applied(self):
        frappe.db.set_value(RUN, self.run.name, "status", "Failed", update_modified=False)
        frappe.db.set_value(INSTANCE, self.instance, "instance_class", "cpu-large", update_modified=False)
        run_trace = verify.trace(self.analysis)
        self.assertEqual(run_trace["source"]["differs"], ["status"])
        self.assertEqual((run_trace["source"]["when_recorded"], run_trace["source"]["now"]),
                         ({"status": "Completed"}, {"status": "Failed"}))
        self.assertEqual(run_trace["record"]["quantity"], 1)        # the record is not revised
        self.assertEqual(verify.trace(self.compute)["source"]["differs"], ["instance_class"])
        self.assertTrue(self.check()["ok"])                         # and still verifies: it says what was true then

    def test_a_deleted_source(self):
        frappe.delete_doc(RUN, self.run.name, force=True, ignore_permissions=True)
        out = verify.trace(self.analysis)
        self.assertFalse(out["source"]["exists"])
        self.assertEqual(out["rederived"]["quantity"], 1)

    def test_storage_and_missing_records(self):
        out = verify.trace(self.storage)
        self.assertIsNone(out["source"]["exists"])
        self.assertEqual(out["rederived"]["quantity"], 5000)
        self.assertIsNone(verify.trace("no-such-record"))

    def test_trace_survives_the_damage_it_is_asked_about(self):
        from webodm_core.api import usage as usage_api

        self.tamper(self.analysis, evidence="not json")
        out = verify.trace(self.analysis)
        self.assertIn("evidence_unreadable", {p["problem"] for p in out["problems"]})
        self.assertIsNone(out["record"]["evidence"])
        self.assertTrue(out["record"]["evidence_unreadable"])
        self.assertIn("error", out["rederived"])
        # The API can still show the row, and says its evidence cannot be read.
        shown = usage_api.record(name=self.analysis)
        self.assertEqual((shown["quantity"], shown["evidence"], shown["evidence_unreadable"]), (1, None, True))

    def test_a_changed_hash_alone_does_not_say_the_quantity_is_wrong(self):
        self.tamper(self.compute, record_hash="0" * 64)
        out = verify.trace(self.compute)
        self.assertEqual({p["problem"] for p in out["problems"]}, {"hash_mismatch"})
        self.assertTrue(out["rederived"]["matches_record"])

    def test_trace_reports_a_tampered_record(self):
        self.tamper(self.compute, quantity=1)
        out = verify.trace(self.compute)
        self.assertEqual({p["problem"] for p in out["problems"]}, {"hash_mismatch", "quantity_mismatch"})
        self.assertFalse(out["rederived"]["matches_record"])
        self.assertEqual(out["rederived"]["quantity"], 3600)        # what the evidence says it should be
