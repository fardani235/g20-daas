"""Time handling for metering: UTC, whole seconds, and doubt resolved for the customer.

No site needed. The cases that matter are the ones where two reasonable
readings of a timestamp differ, because that difference would be money.
"""

import datetime as dt
import unittest

from webodm_core.metering import timeutil


def d(text):
    return dt.datetime.fromisoformat(text)


class TestRounding(unittest.TestCase):
    def test_start_rounds_up_end_rounds_down(self):
        self.assertEqual(timeutil.ceil_second(d("2026-10-19 10:00:00.000001")), d("2026-10-19 10:00:01"))
        self.assertEqual(timeutil.ceil_second(d("2026-10-19 10:00:00")), d("2026-10-19 10:00:00"))
        self.assertEqual(timeutil.floor_second(d("2026-10-19 10:00:00.999999")), d("2026-10-19 10:00:00"))

    def test_a_rounded_interval_is_never_longer_than_the_real_one(self):
        start, end = d("2026-10-19 10:00:00.400000"), d("2026-10-19 10:00:59.900000")
        real = (end - start).total_seconds()
        recorded = timeutil.whole_seconds(timeutil.ceil_second(start), timeutil.floor_second(end))
        self.assertEqual(recorded, 58)
        self.assertLessEqual(recorded, real)

    def test_whole_seconds_is_never_negative(self):
        self.assertEqual(timeutil.whole_seconds(d("2026-10-19 10:00:05"), d("2026-10-19 10:00:00")), 0)
        self.assertEqual(timeutil.whole_seconds(d("2026-10-01 00:00:00"), d("2026-11-01 00:00:00")), 31 * 86400)


class TestLocalToUtc(unittest.TestCase):
    def test_plain_conversion(self):
        utc, ambiguous = timeutil.local_to_utc(d("2026-10-19 10:00:00"), "Asia/Jakarta", edge="start")
        self.assertEqual(utc, d("2026-10-19 03:00:00"))
        self.assertFalse(ambiguous)
        utc, _ = timeutil.local_to_utc(d("2026-10-19 10:00:00.250000"), "UTC", edge="end")
        self.assertEqual(utc, d("2026-10-19 10:00:00.250000"))

    def test_repeated_hour_is_read_in_the_customers_favour(self):
        # 2026-11-01 01:30 happens twice in New York: 05:30 UTC (EDT) and 06:30 UTC (EST).
        local = d("2026-11-01 01:30:00")
        start, a1 = timeutil.local_to_utc(local, "America/New_York", edge="start")
        end, a2 = timeutil.local_to_utc(local, "America/New_York", edge="end")
        self.assertTrue(a1 and a2)
        self.assertEqual(start, d("2026-11-01 06:30:00"))  # a start is taken as late as it can be
        self.assertEqual(end, d("2026-11-01 05:30:00"))    # an end as early as it can be

    def test_interval_across_the_repeated_hour_is_not_overstated(self):
        # Really 01:10 EDT -> 01:50 EST = 100 minutes. Read naively as 40 minutes;
        # read with the wrong fold it could be 100 or even 160. We must never
        # exceed the real duration.
        start, _ = timeutil.local_to_utc(d("2026-11-01 01:10:00"), "America/New_York", edge="start")
        end, _ = timeutil.local_to_utc(d("2026-11-01 01:50:00"), "America/New_York", edge="end")
        self.assertLessEqual(timeutil.whole_seconds(start, end), 100 * 60)

    def test_skipped_hour(self):
        # 02:30 on 2026-03-08 does not exist in New York.
        start, ambiguous = timeutil.local_to_utc(d("2026-03-08 02:30:00"), "America/New_York", edge="start")
        end, _ = timeutil.local_to_utc(d("2026-03-08 02:30:00"), "America/New_York", edge="end")
        self.assertTrue(ambiguous)
        self.assertGreater(start, end)

    def test_edge_is_required(self):
        with self.assertRaises(ValueError):
            timeutil.local_to_utc(d("2026-10-19 10:00:00"), "UTC", edge="middle")

    def test_source_timestamps_must_be_naive(self):
        with self.assertRaises(ValueError):
            timeutil.parse_local(dt.datetime(2026, 10, 19, tzinfo=dt.UTC))
        self.assertIsNone(timeutil.parse_local(None))
        self.assertIsNone(timeutil.parse_local(""))
        self.assertEqual(timeutil.format_local(d("2026-10-19 10:00:00.123456")), "2026-10-19 10:00:00.123456")
        self.assertEqual(timeutil.parse_local("2026-10-19 10:00:00.123456"), d("2026-10-19 10:00:00.123456"))


class TestInstants(unittest.TestCase):
    def test_iso_round_trip(self):
        self.assertEqual(timeutil.iso_z(d("2026-10-19 03:04:05")), "2026-10-19T03:04:05Z")
        self.assertEqual(timeutil.from_iso_z("2026-10-19T03:04:05Z"), d("2026-10-19 03:04:05"))
        self.assertIsNone(timeutil.iso_z(None))

    def test_iso_exact_keeps_what_iso_z_drops(self):
        self.assertEqual(timeutil.iso_exact(d("2026-10-19 03:04:05")), "2026-10-19T03:04:05Z")
        self.assertEqual(timeutil.iso_exact(d("2026-10-19 03:04:05.500000")), "2026-10-19T03:04:05.500000Z")
        self.assertEqual(timeutil.iso_z(d("2026-10-19 03:04:05.500000")), "2026-10-19T03:04:05Z")
        self.assertIsNone(timeutil.iso_exact(None))

    def test_parse_instant_accepts_only_unambiguous_forms(self):
        self.assertEqual(timeutil.parse_instant("2026-10-01"), d("2026-10-01 00:00:00"))
        self.assertEqual(timeutil.parse_instant("2026-10-01T00:00:00Z"), d("2026-10-01 00:00:00"))
        self.assertEqual(timeutil.parse_instant("2026-10-01T00:00:00z"), d("2026-10-01 00:00:00"))
        self.assertEqual(timeutil.parse_instant("2026-10-01T00:00:00+07:00"), d("2026-09-30 17:00:00"))
        self.assertEqual(timeutil.parse_instant(dt.datetime(2026, 10, 1, 7, tzinfo=dt.timezone(dt.timedelta(hours=7)))),
                         d("2026-10-01 00:00:00"))
        self.assertEqual(timeutil.parse_instant(dt.date(2026, 10, 1)), d("2026-10-01 00:00:00"))

    def test_parse_instant_refuses_a_time_without_a_zone(self):
        # "Midnight on the 1st" is a different instant in every time zone;
        # guessing one is how two people get two totals for the same month.
        for bad in ("2026-10-01 00:00:00", "2026-10-01T00:00:00", "", None):
            with self.assertRaises(ValueError):
                timeutil.parse_instant(bad)
        with self.assertRaises(ValueError):
            timeutil.parse_instant(dt.datetime(2026, 10, 1))

    def test_utc_now_is_naive_whole_seconds(self):
        now = timeutil.utc_now()
        self.assertIsNone(now.tzinfo)
        self.assertEqual(now.microsecond, 0)
        self.assertLess(abs((now - dt.datetime.now(dt.UTC).replace(tzinfo=None)).total_seconds()), 5)


class TestDays(unittest.TestCase):
    def test_day_bounds(self):
        start, end = timeutil.day_bounds(dt.date(2026, 10, 31))
        self.assertEqual((start, end), (d("2026-10-31 00:00:00"), d("2026-11-01 00:00:00")))

    def test_a_day_belongs_to_the_period_in_which_it_ends(self):
        october = timeutil.days_ending_in(d("2026-10-01 00:00:00"), d("2026-11-01 00:00:00"))
        self.assertEqual(len(october), 31)
        self.assertEqual((october[0], october[-1]), (dt.date(2026, 10, 1), dt.date(2026, 10, 31)))
        # Adjacent periods share no day and miss none.
        november = timeutil.days_ending_in(d("2026-11-01 00:00:00"), d("2026-12-01 00:00:00"))
        self.assertEqual(november[0], dt.date(2026, 11, 1))
        self.assertFalse(set(october) & set(november))

    def test_a_period_that_ends_mid_day_does_not_include_that_day(self):
        days = timeutil.days_ending_in(d("2026-10-01 00:00:00"), d("2026-10-03 12:00:00"))
        self.assertEqual(days, [dt.date(2026, 10, 1), dt.date(2026, 10, 2)])

    def test_a_period_not_aligned_to_utc_days(self):
        # A Jakarta calendar day, 17:00Z to 17:00Z: exactly one UTC day ends in it.
        days = timeutil.days_ending_in(d("2026-10-01 17:00:00"), d("2026-10-02 17:00:00"))
        self.assertEqual(days, [dt.date(2026, 10, 1)])

    def test_empty_period(self):
        self.assertEqual(timeutil.days_ending_in(d("2026-10-02 00:00:00"), d("2026-10-02 00:00:00")), [])
        self.assertEqual(timeutil.days_ending_in(d("2026-10-02 00:00:00"), d("2026-10-01 00:00:00")), [])
