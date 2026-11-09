"""Time for metering. No Frappe.

Usage records hold **UTC**, in **whole seconds**. Everything the platform
already stores is something else: Frappe's ``now_datetime()`` is naive
wall-clock time in the site's time zone, with microseconds. This module is
the one place the two meet, and it resolves every doubt the same way — in
the customer's favour:

* an interval's start is rounded *up* to the second and its end *down*, so a
  recorded duration is never longer than the real one;
* a wall-clock time that happened twice (the hour repeated when daylight
  saving ends) or not at all (the hour skipped when it starts) is read as the
  later instant for a start and the earlier one for an end.

Internally an instant is a naive ``datetime`` in UTC — the form a Frappe
``Datetime`` column stores — and is written as ``2026-10-19T03:04:05Z``
wherever it leaves the database.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

UTC = dt.UTC
DAY_SECONDS = 86400
_ONE_SECOND = dt.timedelta(seconds=1)
_ONE_DAY = dt.timedelta(days=1)


def utc_now() -> dt.datetime:
    """The current instant: naive UTC, whole seconds."""
    return dt.datetime.now(UTC).replace(tzinfo=None, microsecond=0)


def parse_local(value) -> dt.datetime | None:
    """A source timestamp as a naive ``datetime``; ``None`` when empty.

    Accepts what Frappe hands back (a ``datetime``) and what a record's
    evidence stores (``YYYY-MM-DD HH:MM:SS[.ffffff]``).
    """
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        if value.tzinfo is not None:
            raise ValueError("a source timestamp must be naive local time")
        return value
    return dt.datetime.fromisoformat(str(value).strip())


def format_local(value) -> str | None:
    """The form a source timestamp is kept in as evidence (microseconds preserved)."""
    value = parse_local(value)
    return value.isoformat(sep=" ") if value is not None else None


def local_to_utc(value: dt.datetime, tz_name: str, *, edge: str) -> tuple[dt.datetime, bool]:
    """Convert naive local ``value`` in ``tz_name`` to naive UTC.

    ``edge`` is ``"start"`` or ``"end"`` and decides which instant an
    ambiguous or non-existent wall-clock time stands for (see the module
    docstring). Returns ``(utc, ambiguous)``.
    """
    if edge not in ("start", "end"):
        raise ValueError("edge must be 'start' or 'end'")
    zone = ZoneInfo(tz_name)
    first = value.replace(tzinfo=zone, fold=0).astimezone(UTC).replace(tzinfo=None)
    second = value.replace(tzinfo=zone, fold=1).astimezone(UTC).replace(tzinfo=None)
    if first == second:
        return first, False
    return (max(first, second) if edge == "start" else min(first, second)), True


def ceil_second(value: dt.datetime) -> dt.datetime:
    return value if value.microsecond == 0 else value.replace(microsecond=0) + _ONE_SECOND


def floor_second(value: dt.datetime) -> dt.datetime:
    return value.replace(microsecond=0)


def whole_seconds(start: dt.datetime, end: dt.datetime) -> int:
    """Whole seconds from ``start`` to ``end`` (both whole-second instants); never negative."""
    delta = end - start
    return max(delta.days * DAY_SECONDS + delta.seconds, 0)


def iso_z(value: dt.datetime | None) -> str | None:
    """``2026-10-19T03:04:05Z`` for a naive UTC instant."""
    if value is None:
        return None
    return value.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_exact(value: dt.datetime | None) -> str | None:
    """Like :func:`iso_z`, but a sub-second part is kept instead of dropped.

    Recorded instants are whole seconds, so this normally equals ``iso_z``.
    It exists for the record hash: an instant that has been nudged by half a
    second behind the application's back must not hash like the original.
    """
    if value is None:
        return None
    if not value.microsecond:
        return iso_z(value)
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def from_iso_z(value: str) -> dt.datetime:
    """Inverse of :func:`iso_z`."""
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")


def as_utc(value) -> dt.datetime | None:
    """A value read back from a ``*_utc`` column as a naive UTC ``datetime``."""
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.replace(tzinfo=None)
    return dt.datetime.fromisoformat(str(value).strip())


def parse_instant(value) -> dt.datetime:
    """An instant given by a caller, as naive UTC.

    Accepts an aware ``datetime``, an ISO-8601 string with ``Z`` or an
    explicit offset, or a bare date ``YYYY-MM-DD`` (midnight UTC). A date-time
    *without* an offset is refused: it does not say which instant it means,
    and guessing a time zone is how two people get two totals for one month.
    """
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            raise ValueError("an instant needs a time zone: pass an aware datetime or an ISO string with an offset")
        return value.astimezone(UTC).replace(tzinfo=None)
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day)
    text = str(value or "").strip()
    if not text:
        raise ValueError("an instant is required")
    if len(text) == 10:
        day = dt.date.fromisoformat(text)
        return dt.datetime(day.year, day.month, day.day)
    parsed = dt.datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text)
    if parsed.tzinfo is None:
        raise ValueError(f"'{text}' has no time zone; write it as e.g. 2026-10-01T00:00:00Z or 2026-10-01T00:00:00+07:00")
    return parsed.astimezone(UTC).replace(tzinfo=None)


# -- UTC days (the grain of a storage measurement) ------------------------------


def day_bounds(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    """``(start, end)`` of a UTC day; the end is the next midnight."""
    start = dt.datetime(day.year, day.month, day.day)
    return start, start + _ONE_DAY


def parse_day(value) -> dt.date:
    return value if isinstance(value, dt.date) and not isinstance(value, dt.datetime) else dt.date.fromisoformat(str(value))


def days_ending_in(start: dt.datetime, end: dt.datetime) -> list[dt.date]:
    """The UTC days that belong to the period ``(start, end]``.

    A day belongs to the period in which it *ends* (the same rule as every
    other record): day D is included when ``start < midnight after D <= end``.
    """
    if end <= start:
        return []
    # First midnight strictly after `start`.
    first_end = dt.datetime(start.year, start.month, start.day) + _ONE_DAY
    out = []
    day_end = first_end
    while day_end <= end:
        out.append((day_end - _ONE_DAY).date())
        day_end += _ONE_DAY
    return out
