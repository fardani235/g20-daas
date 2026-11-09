"""The usage ledger: a fact goes in once, and nothing changes it afterwards.

Failure modes pinned here:

* the same fact recorded twice (double count) — refused by the database;
* a record edited or deleted after the fact — refused by every route the
  document API offers, and detected when done behind it;
* a record that does not say what its hash says.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from webodm_core.metering import ledger, rules, timeutil
from webodm_core.metering.testing import as_user, at, make_org, make_user, purge, usage_rows
from webodm_core.webodm_core.doctype.webodm_usage_record.webodm_usage_record import UsageRecordImmutable


def _storage_fact(org, day="2026-10-19", size=1000):
    inputs = {"day": day, "sampled_at_utc": f"{day}T00:05:00Z", "basis": "file-records",
              "lines": [{"doctype": "WebODM Dataset", "name": "ds1", "label": "x", "files": 1, "bytes": size, "unsized": 0}]}
    return dict(organization=org, rule=rules.RULE_STORAGE, inputs=inputs, result=rules.storage_usage(inputs),
                source_doctype="WebODM Organization", source_name=org,
                idempotency_key=ledger.storage_key(org, day))


class _Base(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.org = make_org("Meter Ledger Org")
        cls.member = make_user("meter_ledger_member@example.com", cls.org, "Member")
        frappe.db.commit()  # fixtures outlive the per-test rollback below

    def setUp(self):
        frappe.set_user("Administrator")
        purge(self.org)

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.db.rollback()


class TestRecordOnce(_Base):
    def test_first_record_is_created_with_everything_on_it(self):
        with at("2026-10-19T00:05:07Z"):
            name, created = ledger.record(**_storage_fact(self.org))
        self.assertTrue(created)
        row = ledger.get(name)
        self.assertEqual(row["organization"], self.org)
        self.assertEqual((row["meter"], row["unit"], row["quantity"], row["waived_quantity"]),
                         ("storage", "byte-day", 1000, 0))
        self.assertEqual(row["entry_type"], "usage")
        self.assertEqual(row["period_start_utc"], "2026-10-19T00:00:00Z")
        self.assertEqual(row["period_end_utc"], "2026-10-20T00:00:00Z")
        self.assertEqual(row["recorded_at_utc"], "2026-10-19T00:05:07Z")
        self.assertEqual(row["rule"], "storage/v1")
        self.assertEqual(row["evidence"]["inputs"]["lines"][0]["bytes"], 1000)
        self.assertTrue(row["evidence"]["not_checked"])

    def test_the_same_fact_again_writes_nothing(self):
        first, created = ledger.record(**_storage_fact(self.org))
        again, created_again = ledger.record(**_storage_fact(self.org, size=999_999))  # even with other content
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first, again)
        rows = usage_rows(self.org)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["quantity"], 1000)  # the first committed row stands

    def _forged_row(self, key):
        row = {"organization": self.org, "meter": "storage", "dimension": "standard", "entry_type": "usage",
               "quantity": 5, "unit": "byte-day", "waived_quantity": 0, "waived_reason": None,
               "period_start_utc": timeutil.parse_instant("2026-10-19"),
               "period_end_utc": timeutil.parse_instant("2026-10-20"),
               "source_doctype": "x", "source_name": "y", "rule": "storage/v1", "task": None, "project": None,
               "idempotency_key": key, "reverses": None, "note": None, "caveats": None,
               "evidence": "{}", "recorded_at_utc": timeutil.utc_now(), "recorded_by": "Administrator"}
        row["record_hash"] = rules.record_hash(row)
        doc = frappe.get_doc({"doctype": ledger.DOCTYPE, **row})
        doc.flags.from_ledger = True
        return doc

    def test_the_database_itself_refuses_a_second_row_for_the_key(self):
        # The guarantee is the unique index, not the "look first" in
        # record(): go around the look and the insert must still fail.
        fact = _storage_fact(self.org)
        ledger.record(**fact)
        frappe.db.savepoint("dup_key")
        with self.assertRaises(frappe.UniqueValidationError):
            self._forged_row(fact["idempotency_key"]).insert(ignore_permissions=True)
        frappe.db.rollback(save_point="dup_key")
        self.assertEqual(len(usage_rows(self.org)), 1)

    def test_names_cannot_be_computed_from_what_they_record(self):
        # A name that could be derived from "storage:<organization>:<day>"
        # would let anyone probe /api/resource for which organizations exist.
        first, _ = ledger.record(**_storage_fact(self.org, day="2026-10-19"))
        second, _ = ledger.record(**_storage_fact(self.org, day="2026-10-20"))
        self.assertNotEqual(first, second)
        for name in (first, second):
            self.assertNotIn(self.org, name)
            self.assertNotIn("2026", name)
            self.assertNotEqual(name, rules.sha256_hex(ledger.storage_key(self.org, "2026-10-19"))[:len(name)])

    def test_unique_index_exists_in_the_schema(self):
        if frappe.db.db_type != "postgres":
            self.skipTest("index introspection written for PostgreSQL")
        defs = [r[0] for r in frappe.db.sql(
            "select indexdef from pg_indexes where tablename = %s", ("tabWebODM Usage Record",))]
        self.assertTrue(any("UNIQUE" in d and "(idempotency_key)" in d for d in defs), defs)

    def test_a_losing_race_reports_the_winner_and_leaves_the_transaction_usable(self):
        # "Another recorder inserted between the look and the insert": the
        # look sees nothing, the insert is refused by the database.
        # (test_concurrency.py does this with real connections.)
        from unittest.mock import patch

        fact = _storage_fact(self.org)
        winner, _ = ledger.record(**fact)
        real_exists, looks = ledger.exists, []

        def blind_at_first(key):
            looks.append(key)
            return None if len(looks) == 1 else real_exists(key)

        with patch.object(ledger, "exists", side_effect=blind_at_first):
            name, created = ledger.record(**fact)
        self.assertEqual((name, created), (winner, False))
        # PostgreSQL aborts a transaction on a failed statement; the savepoint
        # must have absorbed it or this query raises InFailedSqlTransaction.
        self.assertEqual(frappe.db.count(ledger.DOCTYPE, {"organization": self.org}), 1)

    def test_a_winner_this_transaction_cannot_see_is_still_a_recorded_fact(self):
        # REPEATABLE READ: after losing the race the loser may not be able to
        # read the winner's row at all. The refusal is the proof; there is
        # just no name to report.
        from unittest.mock import patch

        fact = _storage_fact(self.org)
        ledger.record(**fact)
        with patch.object(ledger, "exists", return_value=None):
            name, created = ledger.record(**fact)
        self.assertEqual((name, created), (None, False))
        self.assertEqual(frappe.db.count(ledger.DOCTYPE, {"organization": self.org}), 1)

    def test_an_insert_that_fails_for_another_reason_is_not_mistaken_for_recorded(self):
        from unittest.mock import patch

        fact = _storage_fact(self.org)
        with patch("frappe.model.document.Document.insert", side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                ledger.record(**fact)
        self.assertFalse(usage_rows(self.org))
        self.assertEqual(frappe.db.sql("select 1")[0][0], 1)
        self.assertEqual(ledger.record(**fact)[1], True)

    def test_a_fact_without_an_organization_is_refused(self):
        fact = _storage_fact(self.org)
        fact["organization"] = None
        with self.assertRaises(ValueError):
            ledger.record(**fact)

    def test_keys(self):
        self.assertEqual(ledger.compute_key("abc"), "compute:abc")
        self.assertEqual(ledger.analysis_run_key("r1"), "analysis_run:r1")
        self.assertEqual(ledger.storage_key("Acme", "2026-10-19"), "storage:Acme:2026-10-19")
        self.assertEqual(ledger.reversal_key("u1"), "reversal:u1")


class TestImmutable(_Base):
    def _record(self):
        return ledger.record(**_storage_fact(self.org))[0]

    def test_save_is_refused(self):
        doc = frappe.get_doc(ledger.DOCTYPE, self._record())
        doc.quantity = 1
        with self.assertRaises(UsageRecordImmutable):
            doc.save(ignore_permissions=True)
        self.assertEqual(frappe.db.get_value(ledger.DOCTYPE, doc.name, "quantity"), 1000)

    def test_db_set_is_refused(self):
        doc = frappe.get_doc(ledger.DOCTYPE, self._record())
        with self.assertRaises(UsageRecordImmutable):
            doc.db_set("quantity", 1)
        self.assertEqual(frappe.db.get_value(ledger.DOCTYPE, doc.name, "quantity"), 1000)

    def test_delete_is_refused_even_when_forced(self):
        name = self._record()
        with self.assertRaises(UsageRecordImmutable):
            frappe.delete_doc(ledger.DOCTYPE, name, force=True, ignore_permissions=True)
        self.assertTrue(frappe.db.exists(ledger.DOCTYPE, name))

    def test_rename_is_refused(self):
        name = self._record()
        with self.assertRaises(UsageRecordImmutable):
            frappe.rename_doc(ledger.DOCTYPE, name, "renamed-usage", force=True)
        self.assertTrue(frappe.db.exists(ledger.DOCTYPE, name))

    def test_nothing_but_the_ledger_can_insert(self):
        # Desk, /api/resource and a stray get_doc().insert() all land here.
        doc = frappe.get_doc({"doctype": ledger.DOCTYPE, "organization": self.org, "meter": "storage",
                              "quantity": 10 ** 12, "idempotency_key": "forged:1",
                              "period_start_utc": "2026-10-19 00:00:00", "period_end_utc": "2026-10-20 00:00:00",
                              "recorded_at_utc": "2026-10-19 00:00:00"})
        with self.assertRaises(frappe.PermissionError):
            doc.insert(ignore_permissions=True)
        self.assertFalse(usage_rows(self.org))

    def test_no_role_can_write_or_delete(self):
        for perm in frappe.get_meta(ledger.DOCTYPE).permissions:
            for right in ("write", "create", "delete", "submit", "cancel", "amend", "import"):
                self.assertFalse(perm.get(right), f"{perm.role} must not have {right}")

    def test_member_cannot_write_through_the_permission_hook(self):
        doc = frappe.get_doc(ledger.DOCTYPE, self._record())
        as_user(self.member)
        self.assertTrue(frappe.has_permission(ledger.DOCTYPE, "read", doc))
        for ptype in ("write", "delete", "create"):
            self.assertFalse(frappe.has_permission(ledger.DOCTYPE, ptype, doc), ptype)

    def test_the_permission_hook_alone_denies_every_write(self):
        # Role permissions already give nobody write; the hook must not depend
        # on that staying true (a role edited in Desk would undo it).
        from webodm_core import permissions

        doc = frappe.get_doc(ledger.DOCTYPE, self._record())
        for user in (self.member, "Administrator"):
            self.assertTrue(permissions.has_usage_record_permission(doc, "read", user), user)
            for ptype in ("write", "create", "delete", "submit", "cancel", "amend", "share", "import"):
                self.assertFalse(permissions.has_usage_record_permission(doc, ptype, user), (user, ptype))

    def test_a_hash_that_does_not_match_the_content_is_refused_on_insert(self):
        fact = _storage_fact(self.org)
        row = {"organization": self.org, "meter": "storage", "dimension": "standard", "entry_type": "usage",
               "quantity": 5, "unit": "byte-day", "waived_quantity": 0,
               "period_start_utc": timeutil.parse_instant("2026-10-19"),
               "period_end_utc": timeutil.parse_instant("2026-10-20"),
               "idempotency_key": fact["idempotency_key"], "evidence": "{}",
               "recorded_at_utc": timeutil.utc_now(), "recorded_by": "Administrator", "record_hash": "0" * 64}
        doc = frappe.get_doc({"doctype": ledger.DOCTYPE, **row})
        doc.flags.from_ledger = True
        with self.assertRaises(UsageRecordImmutable):
            doc.insert(ignore_permissions=True)

    def test_text_that_looks_like_markup_is_stored_exactly_as_recorded(self):
        # Frappe's HTML sanitizer rewrites such text on save ("<OPS-1423>" is
        # dropped as an unknown tag). In a ledger that would change what was
        # recorded after its hash was taken.
        awkward = "Quarry <north> & <b>pit</b>, a > b"
        inputs = {"day": "2026-10-19", "sampled_at_utc": "2026-10-19T00:05:00Z", "basis": "file-records",
                  "lines": [{"doctype": "WebODM Dataset", "name": "ds1", "label": awkward, "files": 1,
                             "bytes": 10, "unsized": 0}]}
        name, _ = ledger.record(organization=self.org, rule=rules.RULE_STORAGE, inputs=inputs,
                                result=rules.storage_usage(inputs), source_doctype="WebODM Organization",
                                source_name=self.org, idempotency_key=ledger.storage_key(self.org, "2026-10-19"),
                                task=awkward, project=awkward)
        note = "see ticket <OPS-1423>; customer <acme@example.com> disputed, a > b"
        reversal = ledger.reverse(name, note)
        for record in (name, reversal):
            stored = frappe.db.get_value(ledger.DOCTYPE, record, list(ledger.ALL_FIELDS), as_dict=True)
            self.assertEqual(stored.record_hash, rules.record_hash(stored), record)
        self.assertEqual(frappe.db.get_value(ledger.DOCTYPE, reversal, "note"), note)
        row = ledger.get(name)
        self.assertEqual((row["task"], row["project"]), (awkward, awkward))
        self.assertEqual(row["evidence"]["inputs"]["lines"][0]["label"], awkward)

    def test_a_row_frappe_altered_on_the_way_in_is_not_kept(self):
        # If a field ever stopped opting out of the sanitizer, the insert must
        # fail rather than leave a row that does not match its hash.
        from unittest.mock import patch

        fact = _storage_fact(self.org)

        def meddle(doc, *args, **kwargs):
            doc.note = "rewritten after the hash was taken"

        with patch("frappe.model.document.Document._sanitize_content", autospec=True, side_effect=meddle):
            with self.assertRaises(UsageRecordImmutable):
                ledger.record(**fact)
        self.assertFalse(usage_rows(self.org))
        self.assertEqual(frappe.db.sql("select 1")[0][0], 1)

    def test_stored_hash_matches_what_the_database_returns(self):
        # The hash is computed before the insert; it must still match after a
        # round trip through the column types (bigint, timestamp, text).
        name = self._record()
        stored = frappe.db.get_value(ledger.DOCTYPE, name, list(ledger.ALL_FIELDS), as_dict=True)
        self.assertEqual(stored.record_hash, rules.record_hash(stored))


class TestReversal(_Base):
    def test_reversal_negates_in_the_same_period_and_keeps_both_rows(self):
        name, _ = ledger.record(**_storage_fact(self.org))
        reversal = ledger.reverse(name, "measured during a restore; files were double-registered")
        rows = {r["name"]: r for r in usage_rows(self.org)}
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[name]["quantity"], 1000)            # the original is untouched
        self.assertEqual(rows[reversal]["quantity"], -1000)
        self.assertEqual(rows[reversal]["entry_type"], "reversal")
        self.assertEqual(rows[reversal]["reverses"], name)
        self.assertEqual(rows[reversal]["period_end_utc"], rows[name]["period_end_utc"])
        self.assertEqual(rows[reversal]["recorded_by"], "Administrator")
        self.assertIn("double-registered", rows[reversal]["note"])
        self.assertEqual(sum(r["quantity"] for r in rows.values()), 0)
        evidence = ledger.get(reversal)["evidence"]
        self.assertEqual(evidence["inputs"]["original_hash"], rows[name]["record_hash"])

    def test_a_record_is_reversed_once(self):
        name, _ = ledger.record(**_storage_fact(self.org))
        ledger.reverse(name, "first")
        with self.assertRaises(ledger.AlreadyReversed):
            ledger.reverse(name, "second")
        self.assertEqual(len(usage_rows(self.org)), 2)

    def test_a_reversal_cannot_be_reversed(self):
        name, _ = ledger.record(**_storage_fact(self.org))
        reversal = ledger.reverse(name, "oops")
        with self.assertRaises(frappe.ValidationError):
            ledger.reverse(reversal, "undo the undo")

    def test_needs_a_note_and_an_existing_record(self):
        name, _ = ledger.record(**_storage_fact(self.org))
        with self.assertRaises(frappe.ValidationError):
            ledger.reverse(name, "   ")
        with self.assertRaises(frappe.DoesNotExistError):
            ledger.reverse("no-such-record", "why")

    def test_members_cannot_reverse(self):
        name, _ = ledger.record(**_storage_fact(self.org))
        as_user(self.member)
        with self.assertRaises(frappe.PermissionError):
            ledger.reverse(name, "I would like to pay less")
        frappe.set_user("Administrator")
        self.assertEqual(len(usage_rows(self.org)), 1)

    def test_waived_usage_reverses_too(self):
        inputs = {"status": "Failed", "creation": "2026-10-19 10:00:00", "started_at": "2026-10-19 10:00:02",
                  "completed_at": "2026-10-19 10:05:02", "modified": "2026-10-19 10:05:02", "plugin": "p",
                  "site_time_zone": "UTC", "captured": "sweep", "observed_at_utc": "2026-10-19T10:06:00Z"}
        name, _ = ledger.record(organization=self.org, rule=rules.RULE_ANALYSIS_RUN, inputs=inputs,
                                result=rules.analysis_run_usage(inputs), source_doctype="WebODM Plugin Run",
                                source_name="r1", idempotency_key=ledger.analysis_run_key("r1"))
        reversal = ledger.get(ledger.reverse(name, "why"))
        self.assertEqual((reversal["quantity"], reversal["waived_quantity"], reversal["waived_reason"]),
                         (0, -1, "failed"))
