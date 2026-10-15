"""Findings a plan carries: what was checked, against what, and how far to trust it.

Two things are kept apart on purpose.

*Severity* says what the finding means for the mission:

- ``error``   – the plan cannot be flown as specified with the selected
                aircraft and camera (a published limit is exceeded, a
                required input is missing). Export is refused.
- ``warning`` – flyable, but something needs a decision by the pilot.
                Export requires an explicit acknowledgement.
- ``info``    – context.

*Basis* says how the finding was arrived at, so an estimate can never pass
for a verified fact:

- ``checked``     – arithmetic against the aircraft/camera profile and the
                    plan's own numbers. As good as the profile is.
- ``estimated``   – derived from a model with real uncertainty (terrain
                    data, endurance). Indicative only.
- ``advisory``    – compared with data that may be incomplete or out of date
                    (a rule preset, an operator-supplied zone file).
- ``not_checked`` – the platform has no data for this and says so.
- ``note``        – not a check at all: something the pilot needs to know to
                    use the plan or the exported file as intended.

Nothing here is verified flight-safety information; ``NOTICE`` is attached
to every plan and every exported file.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

SEVERITIES = ("error", "warning", "info")
BASES = ("checked", "estimated", "advisory", "not_checked", "note")
CATEGORIES = ("geometry", "capability", "photogrammetry", "terrain", "battery", "regulation", "airspace",
              "obstacles", "weather", "export")

NOTICE = (
    "Planning estimate only - not verified flight-safety information. Distances, times, photo counts, "
    "battery needs and terrain clearances are computed from nominal equipment data and elevation models and "
    "have not been checked against obstacles, airspace restrictions, NOTAMs, weather or the actual condition "
    "of the aircraft. The remote pilot is responsible for verifying the route on site and for complying "
    "with the applicable aviation rules."
)


@dataclass
class Issue:
    code: str
    severity: str
    category: str
    message: str
    basis: str = "checked"
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        if not d["detail"]:
            d.pop("detail")
        return d


@dataclass
class Assurance:
    """One line of the 'what was and was not checked' table."""

    topic: str          # one of CATEGORIES
    status: str         # one of BASES
    title: str
    summary: str

    def to_dict(self) -> dict:
        return asdict(self)


def error(code, category, message, basis="checked", **detail) -> Issue:
    return Issue(code, "error", category, message, basis, detail)


def warning(code, category, message, basis="checked", **detail) -> Issue:
    return Issue(code, "warning", category, message, basis, detail)


def info(code, category, message, basis="checked", **detail) -> Issue:
    return Issue(code, "info", category, message, basis, detail)


def worst(issues) -> str | None:
    """Highest severity present among issue dicts or ``Issue`` objects, or ``None``."""
    found = {(i.severity if isinstance(i, Issue) else i.get("severity")) for i in issues}
    for s in SEVERITIES:
        if s in found:
            return s
    return None


def counts(issues) -> dict:
    out = {s: 0 for s in SEVERITIES}
    for i in issues:
        s = i.severity if isinstance(i, Issue) else i.get("severity")
        if s in out:
            out[s] += 1
    return out
