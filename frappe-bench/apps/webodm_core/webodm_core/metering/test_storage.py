"""The daily storage measurement.

Storage is the one meter that cannot be reconstructed, so these tests pin the
properties that make each day's number trustworthy on its own:

* it adds up exactly from the files that exist, each counted once;
* it is taken once per organization per UTC day and never for another day;
* a day that was not measured stays empty (no invented history);
* the record still explains itself after the files are deleted;
* nothing outside the database can stop it;
* one organization's files never appear in another's measurement.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from webodm_core import storage as object_storage
from webodm_core.metering import ledger, rules
from webodm_core.metering import storage as storage_meter
from webodm_core.metering.testing import (
    as_user,
    at,
    attach_file,
    make_org,
    make_run,
    make_user,
    usage_rows,
    wipe,
)
from webodm_core.plugins.files import abs_path_for_file_url
from webodm_core.testing import delete_dataset_with_tasks, make_dataset, make_task


def _project(title):
    if frappe.db.exists("WebODM Project", title):
        return title
    return frappe.get_doc({"doctype": "WebODM Project", "title": title}).insert().name


class _Base(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.org_a = make_org("Meter Storage A")
        cls.org_b = make_org("Meter Storage B")
        cls.empty_org = make_org("Meter Storage Empty")
        cls.user_a = make_user("meter_storage_a@example.com", cls.org_a)
        cls.user_b = make_user("meter_storage_b@example.com", cls.org_b)

    def setUp(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        wipe(self.org_a, self.org_b, self.empty_org)

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        wipe(self.org_a, self.org_b, self.empty_org)

    # -- fixtures ---------------------------------------------------------

    def dataset(self, user, sizes, title="Meter images"):
        as_user(user)
        ds = make_dataset(title, images=[(f"IMG_{i:04d}.JPG", b"\xff" * size) for i, size in enumerate(sizes)])
        frappe.set_user("Administrator")
        return ds

    def task(self, user, dataset, title="Meter survey", project="Meter Storage Project A"):
        as_user(user)
        task = make_task(_project(project), dataset=dataset, title=title)
        frappe.set_user("Administrator")
        return task

    def plugin_run(self, user, task):
        return make_run(user, task=task)

    def record(self, org):
        name, created = storage_meter.sample(org)
        return ledger.get(name), created


class TestMeasure(_Base):
    def test_adds_up_from_datasets_tasks_and_runs(self):
        ds = self.dataset(self.user_a, [1000, 2000, 3000])
        task = self.task(self.user_a, ds.name)
        attach_file("WebODM Task", task.name, 50_000, f"{task.name}_orthophoto.tif")
        attach_file("WebODM Task", task.name, 7_000, f"{task.name}_dsm.tif")
        run = self.plugin_run(self.user_a, task.name)
        attach_file("WebODM Plugin Run", run.name, 123)

        lines = {(line["doctype"], line["name"]): line for line in storage_meter.measure(self.org_a)}
        self.assertEqual(lines[("WebODM Dataset", ds.name)]["files"], 3)
        self.assertEqual(lines[("WebODM Dataset", ds.name)]["bytes"], 6000)
        self.assertEqual(lines[("WebODM Dataset", ds.name)]["label"], "Meter images")
        self.assertEqual(lines[("WebODM Task", task.name)]["bytes"], 57_000)
        self.assertEqual(lines[("WebODM Task", task.name)]["files"], 2)
        self.assertEqual(lines[("WebODM Plugin Run", run.name)]["bytes"], 123)
        self.assertEqual(len(lines), 3)

        with at("2026-10-19T00:10:00Z"):
            record, created = self.record(self.org_a)
        self.assertTrue(created)
        self.assertEqual(record["quantity"], 6000 + 57_000 + 123)
        self.assertEqual((record["meter"], record["unit"], record["dimension"]), ("storage", "byte-day", "standard"))
        self.assertEqual(record["quantity"], sum(line["bytes"] for line in record["evidence"]["inputs"]["lines"]))
        self.assertEqual(record["evidence"]["derived"]["files"], 6)
        self.assertEqual(record["evidence"]["inputs"]["basis"], "file-records")

    def test_two_file_rows_for_one_url_are_counted_once(self):
        # Frappe's attach_files_to_document creates a second File row for an
        # Attach value saved without one. Counting rows would double the file.
        ds = self.dataset(self.user_a, [4000])
        original = frappe.get_doc("File", {"attached_to_doctype": "WebODM Dataset", "attached_to_name": ds.name})
        twin = frappe.get_doc({"doctype": "File", "file_name": original.file_name, "file_url": original.file_url,
                               "is_private": 1, "file_size": original.file_size,
                               "attached_to_doctype": "WebODM Dataset", "attached_to_name": ds.name,
                               "attached_to_field": "gcp_file"})
        twin.flags.copy_from_existing_file = True
        twin.insert(ignore_permissions=True)
        self.assertEqual(frappe.db.count("File", {"attached_to_doctype": "WebODM Dataset",
                                                  "attached_to_name": ds.name}), 2)
        (line,) = storage_meter.measure(self.org_a)
        self.assertEqual((line["files"], line["bytes"]), (1, 4000))

    def test_sizes_past_32_bits_and_past_float_precision_stay_exact(self):
        ds = self.dataset(self.user_a, [10, 10])
        files = frappe.get_all("File", filters={"attached_to_doctype": "WebODM Dataset",
                                                "attached_to_name": ds.name}, pluck="name", order_by="name")
        huge = 2 ** 60 + 1  # a float cannot hold this
        frappe.db.set_value("File", files[0], "file_size", huge, update_modified=False)
        frappe.db.set_value("File", files[1], "file_size", 3_000_000_000, update_modified=False)  # > 2^31
        with at("2026-10-19T00:10:00Z"):
            record, _ = self.record(self.org_a)
        self.assertEqual(record["quantity"], huge + 3_000_000_000)
        stored = frappe.db.get_value(ledger.DOCTYPE, record["name"], "quantity")
        self.assertEqual(int(stored), huge + 3_000_000_000)

    def test_a_file_without_a_size_is_counted_as_zero_and_says_so(self):
        ds = self.dataset(self.user_a, [500, 500])
        name = frappe.get_all("File", filters={"attached_to_name": ds.name}, pluck="name")[0]
        frappe.db.set_value("File", name, "file_size", 0, update_modified=False)
        with at("2026-10-19T00:10:00Z"):
            record, _ = self.record(self.org_a)
        self.assertEqual(record["quantity"], 500)
        self.assertEqual(record["evidence"]["derived"]["unsized_files"], 1)
        self.assertIn("1 file(s) have no recorded size", record["caveats"][0])

    def test_an_evicted_blob_is_still_stored_and_still_counted(self):
        # The serving cache may drop the bytes; the object (and its row) remains.
        ds = self.dataset(self.user_a, [2500])
        os.remove(abs_path_for_file_url(ds.images[0].image))
        (line,) = storage_meter.measure(self.org_a)
        self.assertEqual(line["bytes"], 2500)

    def test_plugin_packages_and_unattached_files_are_not_measured(self):
        ds = self.dataset(self.user_a, [100])
        attach_file("WebODM Plugin", "meter-a.some-plugin", 9_999_999)
        attach_file("WebODM Project", "Meter Storage Project A", 8_888_888)
        self.assertEqual(sum(line["bytes"] for line in storage_meter.measure(self.org_a)), 100)
        self.assertEqual({line["name"] for line in storage_meter.measure(self.org_a)}, {ds.name})

    def test_states_what_was_and_was_not_checked(self):
        with at("2026-10-19T00:10:00Z"):
            record, _ = self.record(self.org_a)
        self.assertTrue(record["evidence"]["checked"])
        self.assertTrue(any("bucket" in item for item in record["evidence"]["not_checked"]))


class TestIsolation(_Base):
    def test_another_organizations_files_never_enter_a_measurement(self):
        ds_a = self.dataset(self.user_a, [1111], title="A images")
        ds_b = self.dataset(self.user_b, [2222, 3333], title="B images")
        task_b = self.task(self.user_b, ds_b.name, title="B survey", project="Meter Storage Project B")
        attach_file("WebODM Task", task_b.name, 44_444)

        with at("2026-10-19T00:10:00Z"):
            rec_a, _ = self.record(self.org_a)
            rec_b, _ = self.record(self.org_b)
        self.assertEqual(rec_a["quantity"], 1111)
        self.assertEqual(rec_b["quantity"], 2222 + 3333 + 44_444)
        names_a = {line["name"] for line in rec_a["evidence"]["inputs"]["lines"]}
        names_b = {line["name"] for line in rec_b["evidence"]["inputs"]["lines"]}
        self.assertEqual(names_a, {ds_a.name})
        self.assertEqual(names_b, {ds_b.name, task_b.name})
        # Nothing of B — name, title or size — anywhere in A's evidence.
        text_a = frappe.db.get_value(ledger.DOCTYPE, rec_a["name"], "evidence")
        for needle in (ds_b.name, task_b.name, "B images", "B survey", "2222", "44444"):
            self.assertNotIn(needle, text_a)
        self.assertEqual(rec_a["organization"], self.org_a)
        self.assertEqual(rec_b["organization"], self.org_b)


class TestOncePerDay(_Base):
    def test_nothing_stored_is_recorded_as_zero(self):
        with at("2026-10-19T00:10:00Z"):
            record, created = self.record(self.empty_org)
        self.assertTrue(created)
        self.assertEqual(record["quantity"], 0)
        self.assertEqual(record["evidence"]["inputs"]["lines"], [])

    def test_a_second_measurement_the_same_day_writes_nothing(self):
        self.dataset(self.user_a, [1000])
        with at("2026-10-19T00:10:00Z"):
            first, created = self.record(self.org_a)
        self.dataset(self.user_a, [9000], title="uploaded later that day")
        with at("2026-10-19T23:59:59Z"):
            again, created_again = self.record(self.org_a)
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(again["name"], first["name"])
        self.assertEqual(again["quantity"], 1000)
        self.assertEqual(len(usage_rows(self.org_a)), 1)

    def test_the_next_utc_day_gets_its_own_measurement(self):
        self.dataset(self.user_a, [1000])
        with at("2026-10-19T23:59:59Z"):
            self.record(self.org_a)
        self.dataset(self.user_a, [9000], title="more")
        with at("2026-10-20T00:00:00Z"):
            second, created = self.record(self.org_a)
        self.assertTrue(created)
        self.assertEqual(second["quantity"], 10_000)
        self.assertEqual(second["period_start_utc"], "2026-10-20T00:00:00Z")
        self.assertEqual(second["period_end_utc"], "2026-10-21T00:00:00Z")
        self.assertEqual(second["evidence"]["inputs"]["sampled_at_utc"], "2026-10-20T00:00:00Z")

    def test_days_that_were_not_measured_stay_empty(self):
        # Scheduler down on the 20th and 21st. When it comes back it measures
        # the 22nd and nothing else: no row is made up for the days between.
        self.dataset(self.user_a, [1000])
        with at("2026-10-19T00:10:00Z"):
            self.record(self.org_a)
        with at("2026-10-22T08:00:00Z"):
            storage_meter.sample_all([self.org_a])
            storage_meter.sample_all([self.org_a])
        days = [row["period_start_utc"].date().isoformat() for row in usage_rows(self.org_a)]
        self.assertEqual(days, ["2026-10-19", "2026-10-22"])

    def test_sample_has_no_way_to_be_asked_for_another_day(self):
        import inspect

        self.assertEqual(list(inspect.signature(storage_meter.sample).parameters), ["organization"])


class TestEvidenceOutlivesTheFiles(_Base):
    def test_deleting_a_dataset_does_not_erase_what_it_occupied(self):
        ds = self.dataset(self.user_a, [1500, 2500], title="Quarry, October")
        with at("2026-10-19T00:10:00Z"):
            before, _ = self.record(self.org_a)
        delete_dataset_with_tasks(ds.name)
        self.assertFalse(frappe.db.exists("WebODM Dataset", ds.name))
        self.assertFalse(frappe.db.exists("File", {"attached_to_name": ds.name}))

        after = ledger.get(before["name"])
        self.assertEqual(after["quantity"], 4000)
        (line,) = after["evidence"]["inputs"]["lines"]
        self.assertEqual((line["doctype"], line["name"], line["label"], line["files"], line["bytes"]),
                         ("WebODM Dataset", ds.name, "Quarry, October", 2, 4000))
        # And the record can still be rebuilt from itself.
        self.assertEqual(rules.rederive(after["evidence"])["quantity"], 4000)

        with at("2026-10-20T00:10:00Z"):
            next_day, _ = self.record(self.org_a)
        self.assertEqual(next_day["quantity"], 0)


class TestNothingHoldsItUp(_Base):
    def test_works_without_a_bucket(self):
        self.dataset(self.user_a, [700])
        with patch.dict(frappe.conf, {"storage_bucket": None}), at("2026-10-19T00:10:00Z"):
            record, _ = self.record(self.org_a)
        self.assertEqual(record["quantity"], 700)
        self.assertFalse(record["evidence"]["inputs"]["object_storage_configured"])

    def test_works_while_object_storage_is_unreachable(self):
        self.dataset(self.user_a, [700])
        boom = object_storage.StorageUnavailable("storage unreachable")
        with patch.dict(frappe.conf, {"storage_bucket": "some-bucket"}), \
                patch.object(object_storage, "get", side_effect=boom), \
                patch.object(object_storage, "ObjectStorage", side_effect=boom), \
                at("2026-10-19T00:10:00Z"):
            record, _ = self.record(self.org_a)
        self.assertEqual(record["quantity"], 700)
        self.assertTrue(record["evidence"]["inputs"]["object_storage_configured"])

    def test_one_organization_failing_does_not_stop_the_others(self):
        self.dataset(self.user_b, [321])
        real_measure = storage_meter.measure

        def measure(org):
            if org == self.org_a:
                raise RuntimeError("bad row")
            return real_measure(org)

        with patch.object(storage_meter, "measure", side_effect=measure), \
                patch("frappe.log_error") as log, at("2026-10-19T00:10:00Z"):
            stats = storage_meter.sample_all([self.org_a, self.org_b, self.empty_org])
        self.assertEqual(stats, {"organizations": 3, "measured": 2, "already_measured": 0, "errors": 1})
        self.assertFalse(usage_rows(self.org_a))
        self.assertEqual(usage_rows(self.org_b)[0]["quantity"], 321)
        self.assertEqual(usage_rows(self.empty_org)[0]["quantity"], 0)
        self.assertIn("bad row", log.call_args.args[0])

        # The failed organization is simply picked up by the next run.
        with at("2026-10-19T00:20:00Z"):
            stats = storage_meter.sample_all([self.org_a, self.org_b, self.empty_org])
        self.assertEqual((stats["measured"], stats["already_measured"], stats["errors"]), (1, 2, 0))

    def test_a_database_error_in_one_measurement_leaves_the_run_usable(self):
        # A failed statement aborts a PostgreSQL transaction; without the
        # savepoint every later organization would fail too.
        self.dataset(self.user_b, [321])
        real_measure = storage_meter.measure

        def measure(org):
            if org == self.org_a:
                frappe.db.sql("select * from tab_no_such_table_for_metering")
            return real_measure(org)

        with patch.object(storage_meter, "measure", side_effect=measure), patch("frappe.log_error"), \
                at("2026-10-19T00:10:00Z"):
            stats = storage_meter.sample_all([self.org_a, self.org_b])
        self.assertEqual((stats["measured"], stats["errors"]), (1, 1))
        self.assertEqual(usage_rows(self.org_b)[0]["quantity"], 321)

    def test_a_suspended_organization_is_still_measured(self):
        self.dataset(self.user_a, [900])
        frappe.db.set_value("WebODM Organization", self.org_a, "status", "Suspended")
        try:
            with at("2026-10-19T00:10:00Z"):
                storage_meter.sample_all([self.org_a])
            self.assertEqual(usage_rows(self.org_a)[0]["quantity"], 900)
        finally:
            frappe.db.set_value("WebODM Organization", self.org_a, "status", "Active")

    def test_by_default_every_organization_is_measured(self):
        seen = []
        with patch.object(storage_meter, "sample", side_effect=lambda org: (seen.append(org), (None, True))[1]):
            stats = storage_meter.sample_all()
        self.assertEqual(set(seen), set(frappe.get_all("WebODM Organization", pluck="name")))
        self.assertTrue({self.org_a, self.org_b, self.empty_org} <= set(seen))
        self.assertEqual(stats["measured"], len(seen))

    def test_the_scheduler_entry_point_never_raises(self):
        with patch.object(storage_meter, "sample_all", side_effect=RuntimeError("everything is on fire")), \
                patch("frappe.log_error") as log:
            storage_meter.sample_job()
        self.assertIn("everything is on fire", log.call_args.args[0])
