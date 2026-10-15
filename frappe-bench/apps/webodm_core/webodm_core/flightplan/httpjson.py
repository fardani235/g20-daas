"""One small HTTP helper for the outbound lookups (elevation, address search).

Kept apart so the modules that use it stay free of ``requests`` at import
time and can be handed a fake in tests. Errors are worded for the person
reading them in the planner, without naming a particular service: the
caller says which lookup failed.
"""

from __future__ import annotations


class HttpError(Exception):
    """The request did not produce a usable JSON answer; the message says why."""


def fetch_json(method: str, url: str, *, params: dict | None = None, json: dict | None = None,
               headers: dict | None = None, timeout: float = 15.0):
    """GET / POST and return the decoded JSON body. Raises ``HttpError`` on any failure."""
    import requests

    try:
        resp = requests.request(method, url, params=params, json=json, headers=headers or {}, timeout=timeout)
    except requests.RequestException as e:
        raise HttpError(f"the service could not be reached ({e.__class__.__name__})") from e
    if resp.status_code == 429:
        raise HttpError("the service's rate limit was reached; try again in a minute")
    if resp.status_code >= 400:
        raise HttpError(f"the service answered HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as e:
        raise HttpError("the service returned an unreadable answer") from e
