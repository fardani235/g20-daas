"""Two copies of the recorder at the same time.

"Impossible rather than unlikely": these tests run recorders on separate
database connections, in threads, against committed rows — first with the
interleaving forced (one recorder holds an uncommitted record while the
other tries the same fact), then as a plain race of many sweeps and many
storage measurements started together. In every case each fact ends up with
exactly one record and no recorder fails.
"""

from __future__ import annotations

import threading

import frappe
from frappe.tests.utils import FrappeTestCase

from webodm_core.metering import ledger, rules, sweep
from webodm_core.metering import storage as storage_meter
from webodm_core.metering.testing import (
    as_user,
    at,
    make_instance,
    make_org,
    make_run,
    make_user,
    usage_rows,
    wipe,
)
from webodm_core.testing import make_dataset

RECORDERS = 8


def _on_own_connection(site, sites_path, work, box, key):
    """Run ``work`` in this thread with its own Frappe context and database connection."""
    try:
        frappe.init(site=site, sites_path=sites_path)
        frappe.connect()
        box[key] = work()
        frappe.db.commit()
    except Exception as e:  # reported to the test, which asserts there are none
        box[key] = e
        try:
            frappe.db.rollback()
        except Exception:
            pass
    finally:
        frappe.destroy()


class TestConcurrentRecorders(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.org = make_org("Meter Concurrency Org")
        cls.user = make_user("meter_concurrency@example.com", cls.org)
        cls.site, cls.sites_path = frappe.local.site, frappe.local.sites_path

    def setUp(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        wipe(self.org)

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        frappe.db.rollback()
        wipe(self.org)

    def run_together(self, work, count=RECORDERS):
        barrier = threading.Barrier(count)
        box = {}

        def synchronized():
            barrier.wait(timeout=30)
            return work()

        threads = [threading.Thread(target=_on_own_connection, args=(self.site, self.sites_path, synchronized, box, i))
                   for i in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
        self.assertFalse([t for t in threads if t.is_alive()], "a recorder is stuck")
        errors = [value for value in box.values() if isinstance(value, Exception)]
        self.assertEqual(errors, [], "no recorder may fail")
        return [box[i] for i in range(count)]

    def test_second_recorder_waits_then_finds_the_first_ones_record(self):
        """Forced interleaving: A has inserted but not committed when B records the same fact."""
        inputs = {"day": "2026-10-19", "sampled_at_utc": "2026-10-19T00:05:00Z", "basis": "file-records", "lines": []}
        fact = dict(organization=self.org, rule=rules.RULE_STORAGE, inputs=inputs,
                    result=rules.storage_usage(inputs), source_doctype="WebODM Organization",
                    source_name=self.org, idempotency_key=ledger.storage_key(self.org, "2026-10-19"))
        a_inserted, a_may_commit, b_started = threading.Event(), threading.Event(), threading.Event()
        box = {}

        def recorder_a():
            result = ledger.record(**fact)
            a_inserted.set()                     # the row exists, uncommitted, invisible to B
            a_may_commit.wait(timeout=30)
            return result

        def recorder_b():
            a_inserted.wait(timeout=30)
            b_started.set()
            return ledger.record(**fact)         # looks, sees nothing, inserts -> blocks on the unique index

        thread_a = threading.Thread(target=_on_own_connection, args=(self.site, self.sites_path, recorder_a, box, "a"))
        thread_b = threading.Thread(target=_on_own_connection, args=(self.site, self.sites_path, recorder_b, box, "b"))
        thread_a.start()
        thread_b.start()
        self.assertTrue(b_started.wait(timeout=30))
        thread_b.join(timeout=2)
        self.assertTrue(thread_b.is_alive(), "B must be waiting on A's uncommitted row, not have inserted its own")
        a_may_commit.set()
        thread_a.join(timeout=30)
        thread_b.join(timeout=30)

        self.assertNotIsInstance(box["a"], Exception, box["a"])
        self.assertNotIsInstance(box["b"], Exception, box["b"])
        name_a, created_a = box["a"]
        name_b, created_b = box["b"]
        self.assertEqual((created_a, created_b), (True, False))
        # B's transaction began before A committed, so (REPEATABLE READ) B
        # cannot read A's row: it knows the fact is recorded, not its name.
        self.assertIn(name_b, (name_a, None))
        frappe.db.rollback()  # start a new snapshot to see what the threads committed
        rows = usage_rows(self.org)
        self.assertEqual([r["name"] for r in rows], [name_a])

    def test_many_sweeps_at_once_record_each_source_once(self):
        instances = [make_instance(self.org) for _ in range(12)]
        runs = [make_run(self.user).name for _ in range(12)]
        frappe.db.commit()

        results = self.run_together(lambda: sweep.run())

        frappe.db.rollback()
        rows = usage_rows(self.org)
        self.assertEqual(sorted(r["source_name"] for r in rows), sorted(instances + runs))
        self.assertEqual(len({r["idempotency_key"] for r in rows}), 24)
        self.assertEqual(sum(r["quantity"] for r in rows if r["meter"] == "compute"), 12 * 3600)
        self.assertEqual(sum(r["quantity"] for r in rows if r["meter"] == "analysis_run"), 12)
        # Between them the sweeps created exactly one record per source...
        self.assertEqual(sum(s["instances"] for s in results), 12)
        self.assertEqual(sum(s["runs"] for s in results), 12)
        # ...and losing a race is not an error.
        self.assertEqual(sum(s["errors"] for s in results), 0)

    def test_many_storage_measurements_at_once_leave_one_per_day(self):
        as_user(self.user)
        make_dataset("Concurrency images", images=[("IMG_0001.JPG", b"\xff" * 4321)])
        frappe.set_user("Administrator")
        frappe.db.commit()

        with at("2026-10-19T00:00:30Z"):
            results = self.run_together(lambda: storage_meter.sample_all([self.org]))

        frappe.db.rollback()
        rows = usage_rows(self.org)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["quantity"], 4321)
        self.assertEqual(sum(s["measured"] for s in results), 1)
        self.assertEqual(sum(s["already_measured"] for s in results), RECORDERS - 1)
        self.assertEqual(sum(s["errors"] for s in results), 0)

    def test_sweep_racing_the_deletion_hook(self):
        runs = [make_run(self.user).name for _ in range(10)]
        frappe.db.commit()

        def delete_all():
            for name in runs:
                frappe.delete_doc("WebODM Plugin Run", name, force=True, ignore_permissions=True)
                frappe.db.commit()
            return "deleted"

        barrier = threading.Barrier(4)
        box = {}

        def start(work):
            def synchronized():
                barrier.wait(timeout=30)
                return work()
            return synchronized

        # Three sweeps and one user deleting every run, all at once.
        jobs = [start(lambda: sweep.run()), start(lambda: sweep.run()), start(lambda: sweep.run()), start(delete_all)]
        threads = [threading.Thread(target=_on_own_connection, args=(self.site, self.sites_path, job, box, i))
                   for i, job in enumerate(jobs)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
        self.assertEqual([v for v in box.values() if isinstance(v, Exception)], [])
        self.assertEqual(box[3], "deleted")   # the delete was never refused or slowed to a stop

        frappe.db.rollback()
        self.assertEqual(frappe.db.count("WebODM Plugin Run", {"name": ["in", runs]}), 0)
        rows = usage_rows(self.org)
        self.assertEqual(sorted(r["source_name"] for r in rows), sorted(runs))   # every run once, none twice
        self.assertEqual(sum(r["quantity"] for r in rows), 10)
