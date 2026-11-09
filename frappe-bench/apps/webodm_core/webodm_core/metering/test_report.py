"""Answering "what did this organization consume in this period?".

Pinned here:

* a total is exactly the sum of the records behind it — checked by paging
  through the records and by summing in the database a second way;
* every record is in exactly one period, whatever the boundaries;
* what is still running is visible, marked provisional and in no total;
* a storage day that was not measured is named and counts nothing;
* an answer can be reproduced as it stood at an earlier instant;
* one organization's answer contains nothing of another's.
"""

from __future__ import annotations

import frappe
from frappe.tests.utils import FrappeTestCase

from webodm_core.metering import ledger, report, rules, sweep
from webodm_core.metering import storage as storage_meter
from webodm_core.metering.testing import (
    INSTANCE,
    as_user,
    at,
    make_instance,
    make_org,
    make_run,
    make_user,
    site_local,
    usage_rows,
    wipe,
)
from webodm_core.testing import make_dataset

OCTOBER = ("2026-10-01", "2026-11-01")


def _machine(org, ready_utc, released_utc, **fields):
    """A useful machine whose interval is given in UTC (stored, like the real ones, in site-local time)."""
    values = dict(requested_at=site_local(ready_utc), ready_at=site_local(ready_utc),
                  dispatched_at=site_local(ready_utc), released_at=site_local(released_utc),
                  terminated_at=site_local(released_utc))
    values.update(fields)
    return make_instance(org, **values)


class _Base(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.org = make_org("Meter Report Org")
        cls.other_org = make_org("Meter Report Other")
        cls.user = make_user("meter_report@example.com", cls.org)
        cls.other_user = make_user("meter_report_other@example.com", cls.other_org)

    def setUp(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        wipe(self.org, self.other_org)

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        wipe(self.org, self.other_org)

    def dataset(self, user, size, title="Report images"):
        as_user(user)
        ds = make_dataset(title, images=[("IMG_0001.JPG", b"\xff" * size)])
        frappe.set_user("Administrator")
        return ds

    def all_records(self, org, start, end, **kw):
        out, offset = [], 0
        while True:
            page = report.records(org, start, end, limit=2, offset=offset, **kw)
            out += page["records"]
            offset += 2
            if offset >= page["total"]:
                return out

    def seed_october(self):
        """A month with a bit of everything, recorded at a fixed instant."""
        self.cpu = _machine(self.org, "2026-10-05T08:00:00Z", "2026-10-05T09:00:00Z")
        self.large = _machine(self.org, "2026-10-06T08:00:00Z", "2026-10-06T08:10:00Z", instance_class="cpu-large")
        self.dud = make_instance(self.org, status="Failed", ready_at=None, dispatched_at=None,
                                 requested_at=site_local("2026-10-07T08:00:00Z"),
                                 released_at=site_local("2026-10-07T08:15:00Z"), terminated_at=None,
                                 release_reason="provisioning-timeout")
        self.idle = _machine(self.org, "2026-10-08T08:00:00Z", "2026-10-08T08:05:00Z", dispatched_at=None)
        when = dict(started_at=site_local("2026-10-09T10:00:00Z"), completed_at=site_local("2026-10-09T10:05:00Z"))
        self.runs = [
            make_run(self.user, plugin="acme.change-detection", **when),
            make_run(self.user, plugin="acme.change-detection", **when),
            make_run(self.user, plugin="acme.footprints", **when),
            make_run(self.user, plugin="acme.footprints", status="Failed", **when),
        ]
        self.dataset(self.user, 4000)
        for day in ("01", "02", "04"):          # the 3rd is never measured
            with at(f"2026-10-{day}T00:10:00Z"):
                storage_meter.sample(self.org)
        with at("2026-10-10T00:00:00Z"):
            sweep.run()


class TestTotals(_Base):
    def test_totals_are_the_sum_of_the_records(self):
        self.seed_october()
        with at("2026-11-02T00:00:00Z"):
            answer = report.summary(self.org, *OCTOBER)
        compute = answer["meters"]["compute"]
        self.assertEqual((compute["unit"], compute["quantity"]), ("second", 3600 + 600))
        self.assertEqual(compute["waived_quantity"], 15 * 60 + 5 * 60)
        self.assertEqual(compute["waived_by_reason"], {"never_ready": 900, "no_work_dispatched": 300})
        self.assertEqual(compute["by_dimension"], [
            {"dimension": "aws/cpu", "quantity": 3600, "waived_quantity": 1200, "records": 3},
            {"dimension": "aws/cpu-large", "quantity": 600, "waived_quantity": 0, "records": 1},
        ])
        runs = answer["meters"]["analysis_run"]
        self.assertEqual((runs["unit"], runs["quantity"], runs["waived_quantity"]), ("run", 3, 1))
        self.assertEqual(runs["by_dimension"], [
            {"dimension": "acme.change-detection", "quantity": 2, "waived_quantity": 0, "records": 2},
            {"dimension": "acme.footprints", "quantity": 1, "waived_quantity": 1, "records": 2},
        ])
        self.assertEqual((answer["meters"]["storage"]["unit"], answer["meters"]["storage"]["quantity"]),
                         ("byte-day", 3 * 4000))
        self.assertEqual(answer["record_count"], 4 + 4 + 3)

        # 1. The records the API hands out add up to the totals, meter by meter.
        for meter in report.METERS:
            rows = self.all_records(self.org, *OCTOBER, meter=meter)
            self.assertEqual(sum(r["quantity"] for r in rows), answer["meters"][meter]["quantity"], meter)
            self.assertEqual(sum(r["waived_quantity"] for r in rows), answer["meters"][meter]["waived_quantity"], meter)
            self.assertEqual(len(rows), answer["meters"][meter]["records"])
        self.assertEqual(len(self.all_records(self.org, *OCTOBER)), answer["record_count"])

        # 2. And so does a sum done in the database, with no code of ours in between.
        for meter, quantity, waived in frappe.db.sql(
            """select meter, concat(sum(quantity), ''), concat(sum(waived_quantity), '')
               from `tabWebODM Usage Record` where organization = %s group by meter""", (self.org,)):
            self.assertEqual(int(quantity), answer["meters"][meter]["quantity"])
            self.assertEqual(int(waived), answer["meters"][meter]["waived_quantity"])

    def test_every_figure_traces_to_a_source(self):
        self.seed_october()
        sources = {self.cpu, self.large, self.dud, self.idle, *(r.name for r in self.runs), self.org}
        for row in self.all_records(self.org, *OCTOBER, with_evidence=True):
            self.assertIn(row["source_name"], sources)
            self.assertTrue(row["evidence"]["inputs"])
            # ...and the number on the row is what its own evidence yields.
            again = rules.rederive(row["evidence"])
            self.assertEqual((again["quantity"], again["waived_quantity"]), (row["quantity"], row["waived_quantity"]))

    def test_nothing_is_stored_but_the_records(self):
        _machine(self.org, "2026-10-05T08:00:00Z", "2026-10-05T09:00:00Z")
        sweep.run()
        first = report.summary(self.org, *OCTOBER)["meters"]["compute"]["quantity"]
        _machine(self.org, "2026-10-06T08:00:00Z", "2026-10-06T08:30:00Z")
        sweep.run()
        second = report.summary(self.org, *OCTOBER)["meters"]["compute"]["quantity"]
        self.assertEqual((first, second), (3600, 5400))

    def test_a_past_period_fully_recorded_is_not_provisional(self):
        self.seed_october()
        with at("2026-11-02T00:00:00Z"):
            answer = report.summary(self.org, *OCTOBER)
        self.assertFalse(answer["provisional"])
        self.assertEqual(answer["provisional_reasons"], [])
        self.assertEqual(answer["in_progress"], [])
        self.assertEqual(answer["unrecorded"], {"compute": 0, "analysis_run": 0})

    def test_display_is_for_reading_only(self):
        self.seed_october()
        with at("2026-11-02T00:00:00Z"):
            display = report.summary(self.org, *OCTOBER)["display"]
        self.assertEqual(display["compute"], "1 h 10 min 0 s of machine time counted")
        self.assertEqual(display["analysis_run"], "3 analysis run(s) counted")
        self.assertEqual(display["storage"], "0.000 GB on average over 3 measured day(s)")
        self.assertIn("integer quantities", display["note"])

    def test_another_organizations_usage_is_not_in_the_answer(self):
        self.seed_october()
        theirs = _machine(self.other_org, "2026-10-05T08:00:00Z", "2026-10-05T20:00:00Z")
        make_run(self.other_user, plugin="other.secret-plugin")
        self.dataset(self.other_user, 999_999, title="Other images")
        with at("2026-10-04T00:20:00Z"):
            storage_meter.sample(self.other_org)
        sweep.run()
        with at("2026-11-02T00:00:00Z"):
            mine = report.summary(self.org, *OCTOBER)
            other = report.summary(self.other_org, *OCTOBER)
        self.assertEqual(mine["meters"]["compute"]["quantity"], 4200)
        self.assertEqual(other["meters"]["compute"]["quantity"], 12 * 3600)
        self.assertEqual(mine["meters"]["storage"]["quantity"], 12_000)
        text = frappe.as_json(mine) + frappe.as_json(self.all_records(self.org, *OCTOBER, with_evidence=True))
        for needle in (theirs, "other.secret-plugin", "Other images", "999999", self.other_org):
            self.assertNotIn(needle, text)


class TestPeriods(_Base):
    def test_a_record_belongs_to_the_period_in_which_it_ended(self):
        on_the_dot = _machine(self.org, "2026-10-31T23:00:00Z", "2026-11-01T00:00:00Z")   # ends exactly at the boundary
        just_after = _machine(self.org, "2026-10-31T23:00:00Z", "2026-11-01T00:00:01Z")   # one second into November
        across = _machine(self.org, "2026-10-31T22:00:00Z", "2026-11-01T02:00:00Z")        # mostly October... still November
        sweep.run()
        with at("2026-12-02T00:00:00Z"):
            october = self.all_records(self.org, "2026-10-01", "2026-11-01")
            november = self.all_records(self.org, "2026-11-01", "2026-12-01")
            both = report.summary(self.org, "2026-10-01", "2026-12-01")["meters"]["compute"]["quantity"]
            oct_total = report.summary(self.org, "2026-10-01", "2026-11-01")["meters"]["compute"]["quantity"]
            nov_total = report.summary(self.org, "2026-11-01", "2026-12-01")["meters"]["compute"]["quantity"]
        self.assertEqual({r["source_name"] for r in october}, {on_the_dot})
        self.assertEqual({r["source_name"] for r in november}, {just_after, across})
        # Whole, never split: the four-hour machine is four hours in November.
        self.assertEqual(next(r for r in november if r["source_name"] == across)["quantity"], 4 * 3600)
        # Adjacent periods share nothing and lose nothing.
        self.assertEqual(oct_total + nov_total, both)
        self.assertEqual(both, 3600 + 3601 + 4 * 3600)

    def test_periods_need_not_follow_utc_days(self):
        # A calendar month in Jakarta (UTC+7): the boundary is 17:00Z.
        before = _machine(self.org, "2026-10-31T16:00:00Z", "2026-10-31T16:59:59Z")
        after = _machine(self.org, "2026-10-31T16:00:00Z", "2026-10-31T17:00:01Z")
        sweep.run()
        with at("2026-12-02T00:00:00Z"):
            october_jakarta = self.all_records(self.org, "2026-10-01T00:00:00+07:00", "2026-11-01T00:00:00+07:00")
            november_jakarta = self.all_records(self.org, "2026-11-01T00:00:00+07:00", "2026-12-01T00:00:00+07:00")
        self.assertEqual({r["source_name"] for r in october_jakarta}, {before})
        self.assertEqual({r["source_name"] for r in november_jakarta}, {after})

    def test_an_ambiguous_period_is_refused_not_guessed(self):
        for start, end in (("2026-10-01 00:00:00", "2026-11-01"), ("2026-10-01", "2026-11-01T00:00:00"),
                           (None, "2026-11-01"), ("2026-11-01", "2026-10-01"), ("2026-10-01", "2026-10-01"),
                           ("yesterday", "today")):
            with self.assertRaises(report.InvalidPeriod):
                report.summary(self.org, start, end)
        with self.assertRaises(report.InvalidPeriod):
            report.summary(self.org, *OCTOBER, as_of="2026-10-15 12:00:00")

    def test_a_period_has_to_be_a_sensible_size(self):
        # A period is also a list of days; "year 1 to year 9999" is 3.6 million
        # of them, and the year 9999 overflows the arithmetic outright.
        for start, end in (("0001-01-01", "9999-12-30"), ("2026-10-01", "9999-12-31"), ("2020-01-01", "2026-01-01")):
            for call in (report.summary, report.records):
                with self.assertRaises(report.InvalidPeriod):
                    call(self.org, start, end)
        with at("2027-02-01T00:00:00Z"):
            report.summary(self.org, "2026-01-01", "2027-01-01")   # a whole year is fine

    def test_as_of_cannot_be_in_the_future(self):
        # "As it stood at a moment that has not happened" would otherwise hide
        # that the period is open and file days still to come as lost.
        with at("2026-10-19T12:00:00Z"):
            for call in (report.summary, report.records):
                with self.assertRaises(report.InvalidPeriod):
                    call(self.org, *OCTOBER, as_of="2026-12-01")
            report.summary(self.org, *OCTOBER, as_of="2026-10-19T12:00:00Z")

    def test_the_answer_as_it_stood_can_be_reproduced(self):
        _machine(self.org, "2026-10-05T08:00:00Z", "2026-10-05T09:00:00Z")
        with at("2026-10-06T00:00:00Z"):
            sweep.run()
        with at("2026-11-02T00:00:00Z"):
            then = report.summary(self.org, *OCTOBER)
        # Found weeks later: a machine from October the sweep had not seen
        # (history backfill, a sweep that was down). It lands in October.
        _machine(self.org, "2026-10-20T08:00:00Z", "2026-10-20T08:30:00Z")
        with at("2026-11-20T00:00:00Z"):
            sweep.run()
        with at("2026-12-01T00:00:00Z"):
            now = report.summary(self.org, *OCTOBER)
            as_it_stood = report.summary(self.org, *OCTOBER, as_of="2026-11-02T00:00:00Z")
        self.assertEqual(now["meters"]["compute"]["quantity"], 5400)
        self.assertEqual(as_it_stood["meters"], then["meters"])
        self.assertEqual(as_it_stood["record_count"], then["record_count"])
        self.assertEqual(as_it_stood["as_of_utc"], "2026-11-02T00:00:00Z")
        self.assertNotIn("in_progress", as_it_stood)
        # The late record says when it was written, so "late" is visible on it.
        with at("2026-12-01T00:00:00Z"):
            late = [r for r in self.all_records(self.org, *OCTOBER) if r["recorded_at_utc"] > "2026-11-02"]
        self.assertEqual(len(late), 1)
        self.assertEqual(late[0]["recorded_at_utc"], "2026-11-20T00:00:00Z")


class TestProvisional(_Base):
    def test_a_running_machine_is_shown_and_not_counted(self):
        finished = _machine(self.org, "2026-10-19T08:00:00Z", "2026-10-19T09:00:00Z")
        running = make_instance(self.org, status="Ready", requested_at=site_local("2026-10-19T09:55:00Z"),
                                ready_at=site_local("2026-10-19T10:00:00Z"),
                                dispatched_at=site_local("2026-10-19T10:01:00Z"),
                                released_at=None, terminated_at=None, release_reason=None)
        booting = make_instance(self.org, status="Provisioning", requested_at=site_local("2026-10-19T10:20:00Z"),
                                ready_at=None, dispatched_at=None, released_at=None, terminated_at=None,
                                release_reason=None)
        queued = make_run(self.user, status="Queued", started_at=None, completed_at=None)
        sweep.run()
        with at("2026-10-19T10:30:00Z"):
            answer = report.summary(self.org, *OCTOBER)

        self.assertEqual(answer["meters"]["compute"]["quantity"], 3600)        # the finished one only
        self.assertEqual(answer["meters"]["analysis_run"]["quantity"], 0)
        items = {item["source_name"]: item for item in answer["in_progress"]}
        self.assertEqual(set(items), {running, booting, queued.name})
        self.assertNotIn(finished, items)
        self.assertTrue(all(item["provisional"] is True for item in items.values()))
        self.assertTrue(all(item["as_of_utc"] == "2026-10-19T10:30:00Z" for item in items.values()))
        self.assertEqual((items[running]["elapsed_so_far"], items[running]["work_dispatched"]), (1800, True))
        self.assertEqual((items[booting]["elapsed_so_far"], items[booting]["state"]), (0, "Provisioning"))
        self.assertEqual(items[queued.name]["meter"], "analysis_run")
        self.assertTrue(answer["provisional"])
        self.assertIn("work_in_progress", [r["code"] for r in answer["provisional_reasons"]])
        self.assertIn("period_open", [r["code"] for r in answer["provisional_reasons"]])
        # No in-progress figure is anywhere in the totals.
        self.assertEqual(sum(m["quantity"] for m in answer["meters"].values()), 3600)

        # Released: after the next sweep it is a record, and no longer "in progress".
        frappe.db.set_value(INSTANCE, running, {"status": "Terminated", "release_reason": "task:Completed",
                                                "released_at": site_local("2026-10-19T10:45:00Z"),
                                                "terminated_at": site_local("2026-10-19T10:45:03Z")})
        with at("2026-10-19T10:50:00Z"):
            before_sweep = report.summary(self.org, *OCTOBER)
            sweep.run()
            after_sweep = report.summary(self.org, *OCTOBER)
        self.assertEqual(before_sweep["meters"]["compute"]["quantity"], 3600)
        self.assertEqual(before_sweep["unrecorded"], {"compute": 1, "analysis_run": 0})
        self.assertIn("sources_not_yet_recorded", [r["code"] for r in before_sweep["provisional_reasons"]])
        self.assertEqual(after_sweep["meters"]["compute"]["quantity"], 3600 + 45 * 60)
        self.assertEqual(after_sweep["unrecorded"], {"compute": 0, "analysis_run": 0})
        self.assertNotIn(running, [item["source_name"] for item in after_sweep["in_progress"]])

    def test_work_in_progress_is_not_reported_for_a_period_that_is_over(self):
        make_instance(self.org, status="Ready", ready_at=site_local("2026-11-05T10:00:00Z"), released_at=None,
                      terminated_at=None, release_reason=None)
        with at("2026-11-05T11:00:00Z"):
            answer = report.summary(self.org, *OCTOBER)
        # It will end in November; October's answer is not waiting for it.
        self.assertEqual(answer["in_progress"], [])
        self.assertFalse(answer["provisional"])


class TestStorageDays(_Base):
    def test_a_day_that_was_not_measured_is_named_and_counts_nothing(self):
        self.dataset(self.user, 5000)
        for day in ("03", "04", "06", "07"):               # the 5th is missing
            with at(f"2026-10-{day}T00:10:00Z"):
                storage_meter.sample(self.org)
        with at("2026-10-09T00:00:00Z"):
            answer = report.summary(self.org, "2026-10-01", "2026-10-08")
        storage = answer["meters"]["storage"]
        self.assertEqual(storage["quantity"], 4 * 5000)    # four measured days; nothing for the others
        days = storage["days"]
        self.assertEqual((days["in_period"], days["measured"]), (7, 4))
        self.assertEqual(days["not_measured"], ["2026-10-05"])
        self.assertEqual(days["before_first_measurement"], ["2026-10-01", "2026-10-02"])
        self.assertEqual(days["first_measured_day"], "2026-10-03")
        self.assertEqual((days["pending"], days["not_started"]), ([], []))
        self.assertEqual(answer["gaps"]["storage_days_not_measured"], ["2026-10-05", "2026-10-01", "2026-10-02"])
        self.assertTrue(answer["gaps"]["note"])
        # A gap is permanent, not pending: the answer is final, and incomplete.
        self.assertFalse(answer["provisional"])
        self.assertEqual(answer["display"]["storage"], "0.000 GB on average over 4 measured day(s)")
        # No row exists for the missing day, under any name.
        self.assertEqual(len(usage_rows(self.org, meter="storage")), 4)

    def test_today_not_yet_measured_is_pending_not_missing(self):
        self.dataset(self.user, 5000)
        with at("2026-10-18T00:10:00Z"):
            storage_meter.sample(self.org)
        with at("2026-10-19T00:03:00Z"):
            answer = report.summary(self.org, "2026-10-18", "2026-10-21")
        days = answer["meters"]["storage"]["days"]
        self.assertEqual((days["measured"], days["pending"], days["not_started"], days["not_measured"]),
                         (1, ["2026-10-19"], ["2026-10-20"], []))
        self.assertIn("storage_days_to_come", [r["code"] for r in answer["provisional_reasons"]])

    def test_todays_measurement_counts_once_the_day_has_ended(self):
        self.dataset(self.user, 5000)
        with at("2026-10-18T00:10:00Z"):
            storage_meter.sample(self.org)
        with at("2026-10-19T00:10:00Z"):
            today = storage_meter.sample(self.org)[0]

        # Noon on the 19th, asking about the whole (still open) month: the
        # 18th is over and counts; the 19th is measured, shown, and not counted.
        with at("2026-10-19T12:00:00Z"):
            month = report.summary(self.org, *OCTOBER)
            so_far = report.summary(self.org, "2026-10-01", "2026-10-19T12:00:00Z")
            listed = self.all_records(self.org, *OCTOBER, meter="storage")
        storage = month["meters"]["storage"]
        self.assertEqual((storage["quantity"], storage["records"]), (5000, 1))
        self.assertEqual((storage["days"]["measured"], storage["days"]["in_progress"]), (1, ["2026-10-19"]))
        self.assertEqual(storage["days"]["pending"], [])
        self.assertEqual(sum(r["quantity"] for r in listed), storage["quantity"])   # records and total agree
        (item,) = [i for i in month["in_progress"] if i["meter"] == "storage"]
        self.assertEqual((item["source_name"], item["day"], item["quantity_when_the_day_ends"]),
                         (today, "2026-10-19", 5000))
        self.assertEqual((item["provisional"], item["counts_from_utc"]), (False, "2026-10-20T00:00:00Z"))
        self.assertIn("storage_day_in_progress", [r["code"] for r in month["provisional_reasons"]])
        self.assertNotIn("work_in_progress", [r["code"] for r in month["provisional_reasons"]])
        self.assertEqual(month["period"]["counted_until_utc"], "2026-10-19T12:00:00Z")
        self.assertEqual(so_far["meters"]["storage"]["quantity"], 5000)

        with at("2026-10-20T00:00:00Z"):
            day_over = report.summary(self.org, *OCTOBER)
        self.assertEqual(day_over["meters"]["storage"]["quantity"], 10_000)
        self.assertEqual(day_over["meters"]["storage"]["days"]["in_progress"], [])
        self.assertEqual([i for i in day_over["in_progress"] if i["meter"] == "storage"], [])

        # And looking back: at noon on the 19th the day was not over then either.
        with at("2026-11-02T00:00:00Z"):
            back_then = report.summary(self.org, *OCTOBER, as_of="2026-10-19T12:00:00Z")
        self.assertEqual(back_then["meters"]["storage"]["quantity"], 5000)
        self.assertEqual(back_then["meters"]["storage"]["days"]["in_progress"], ["2026-10-19"])

    def test_a_reversed_measurement_counts_nothing_and_is_listed(self):
        self.dataset(self.user, 5000)
        names = []
        for day in ("03", "04"):
            with at(f"2026-10-{day}T00:10:00Z"):
                names.append(storage_meter.sample(self.org)[0])
        ledger.reverse(names[1], "restore test: files were registered twice that night")
        with at("2026-10-09T00:00:00Z"):
            answer = report.summary(self.org, "2026-10-03", "2026-10-05")
        storage = answer["meters"]["storage"]
        self.assertEqual((storage["quantity"], storage["records"], storage["reversals"]), (5000, 2, 1))
        self.assertEqual((storage["days"]["measured"], storage["days"]["reversed"]), (1, ["2026-10-04"]))
        self.assertEqual(answer["gaps"]["storage_days_reversed"], ["2026-10-04"])


class TestRecordsAndHealth(_Base):
    def test_pages_filters_and_evidence(self):
        for hour in range(5):
            _machine(self.org, f"2026-10-05T0{hour}:00:00Z", f"2026-10-05T0{hour}:30:00Z")
        make_run(self.user)
        sweep.run()
        page = report.records(self.org, *OCTOBER, meter="compute", limit=2, offset=2)
        self.assertEqual((page["total"], page["limit"], page["offset"], len(page["records"])), (5, 2, 2, 2))
        self.assertEqual([r["period_end_utc"] for r in page["records"]],
                         ["2026-10-05T02:30:00Z", "2026-10-05T03:30:00Z"])   # oldest first, stable
        self.assertNotIn("evidence", page["records"][0])
        with_evidence = report.records(self.org, *OCTOBER, meter="analysis_run", with_evidence=True)
        self.assertEqual(with_evidence["records"][0]["evidence"]["rule"], "analysis_run/v1")
        with self.assertRaises(frappe.ValidationError):
            report.records(self.org, *OCTOBER, meter="money")
        self.assertEqual(report.records(self.org, *OCTOBER, limit=10 ** 9)["limit"], report.MAX_PAGE)

    def test_health_shows_whether_measurement_is_keeping_up(self):
        self.dataset(self.user, 100)
        _machine(self.org, "2026-10-05T08:00:00Z", "2026-10-05T09:00:00Z")
        with at("2026-10-19T00:05:00Z"):
            before = report.health()
            storage_meter.sample_all([self.org])
            sweep.run()
            after = report.health()
        self.assertIn(self.org, before["storage"]["not_measured_today"])
        self.assertNotIn(self.org, after["storage"]["not_measured_today"])
        self.assertIn(self.other_org, after["storage"]["not_measured_today"])
        self.assertGreaterEqual(before["unrecorded"]["compute"], 1)
        self.assertEqual(after["unrecorded"], {"compute": 0, "analysis_run": 0})
        self.assertEqual(after["storage"]["day"], "2026-10-19")
        self.assertGreaterEqual(after["records"]["latest_recorded_at_utc"], "2026-10-19T00:05:00Z")
        self.assertEqual(after["records"]["total"], before["records"]["total"] + 2)
