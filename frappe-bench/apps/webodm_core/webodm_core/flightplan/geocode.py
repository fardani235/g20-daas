"""Finding the survey site: coordinates typed by hand, or an address looked up.

``parse_coordinates`` understands the ways people actually write a
position (decimal degrees, hemisphere letters, degrees-minutes-seconds, a
pasted map link) and never needs a network. Anything else is treated as an
address and sent to a Nominatim-compatible geocoder (``search``).

No Frappe dependency; the HTTP call is injectable for tests.
"""

from __future__ import annotations

import re
from typing import Callable

MAX_QUERY = 200


class GeocodeError(Exception):
    """The address lookup failed (service down, refused, bad answer)."""


_NUM = r"[-+]?\d+(?:[.,]\d+)?"
_HEMI = "NSEW"
# A pasted map URL: ...@-6.9147,107.6098,15z or ?q=-6.9147,107.6098
_URL_AT = re.compile(r"[@=](-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)")


def _to_float(text: str) -> float:
    return float(text.replace(",", "."))


def _one_angle(text: str) -> tuple[float, str | None] | None:
    """Parse one coordinate (any of D, D M, D M S, with an optional hemisphere letter)."""
    s = text.strip().upper()
    if not s:
        return None
    hemi = None
    if s[0] in _HEMI:
        hemi, s = s[0], s[1:].strip()
    elif s[-1] in _HEMI:
        hemi, s = s[-1], s[:-1].strip()
    s = s.replace("°", " ").replace("º", " ").replace("′", " ").replace("'", " ").replace("″", " ") \
         .replace('"', " ").replace("D", " ").replace("M", " ")
    parts = s.split()
    if not 1 <= len(parts) <= 3:
        return None
    try:
        nums = [_to_float(p) for p in parts]
    except ValueError:
        return None
    if any(n < 0 for n in nums[1:]) or any(n >= 60 for n in nums[1:]):
        return None
    sign = -1.0 if nums[0] < 0 or str(parts[0]).startswith("-") else 1.0
    value = abs(nums[0]) + (nums[1] / 60.0 if len(nums) > 1 else 0.0) + (nums[2] / 3600.0 if len(nums) > 2 else 0.0)
    if hemi in ("S", "W"):
        sign = -1.0
    elif hemi in ("N", "E") and sign < 0:
        return None  # "-6 N" contradicts itself
    return sign * value, hemi


def _split_pair(text: str) -> list[tuple[str, str]]:
    """Candidate ways to cut ``text`` into two coordinates."""
    s = text.strip()
    out = []
    for sep in (";", "/", "|"):
        if s.count(sep) == 1:
            a, b = s.split(sep)
            out.append((a, b))
    # A comma separates the pair unless it is a decimal comma ("6,91 S 107,60 E").
    if s.count(",") == 1:
        a, b = s.split(",")
        out.append((a, b))
    # Hemisphere letters mark the boundary: "6°54'52.9"S 107°36'35.3"E" or "N40.7 W74.0".
    m = re.match(r"^\s*(.*?[NSEWnsew])[\s,]+(.*)$", s)
    if m:
        out.append((m.group(1), m.group(2)))
    m = re.match(r"^\s*([NSEWnsew].*?)\s+([NSEWnsew].*)$", s)
    if m:
        out.append((m.group(1), m.group(2)))
    tokens = s.replace(",", " ").split()
    if len(tokens) in (2, 4, 6):
        half = len(tokens) // 2
        out.append((" ".join(tokens[:half]), " ".join(tokens[half:])))
    return out


def parse_coordinates(text: str) -> dict | None:
    """``{"lat", "lng", "label", "note"}`` if ``text`` is a position, else ``None``.

    Latitude first, as people write it. The pair is swapped only when that is
    the single reading that makes sense (hemisphere letters say so, or the
    first number cannot be a latitude); the result then says so in ``note``.
    """
    if not text or len(text) > MAX_QUERY:
        return None
    s = text.strip()
    m = _URL_AT.search(s) if ("://" in s or s.startswith("@")) else None
    if m:
        lat, lng = float(m.group(1)), float(m.group(2))
        if abs(lat) <= 90 and abs(lng) <= 180:
            return _result(lat, lng, "")
        return None
    if re.search(r"[A-Za-z]{2,}", re.sub(r"(?i)\b(deg|min|sec)\b", "", s)):
        return None  # words: an address, not a position
    for a, b in _split_pair(s):
        first, second = _one_angle(a), _one_angle(b)
        if not first or not second:
            continue
        (v1, h1), (v2, h2) = first, second
        note = ""
        if h1 in ("E", "W") and h2 in ("N", "S"):
            lat, lng, note = v2, v1, "Read as longitude, latitude (from the hemisphere letters)."
        elif (h1 in ("E", "W")) or (h2 in ("N", "S")):
            continue  # letters on the wrong axis without a consistent swap
        elif h1 is None and h2 is None and abs(v1) > 90 and abs(v2) <= 90:
            lat, lng, note = v2, v1, "Read as longitude, latitude: the first number is too large for a latitude."
        else:
            lat, lng = v1, v2
        if abs(lat) <= 90 and abs(lng) <= 180:
            return _result(lat, lng, note)
    return None


def _result(lat: float, lng: float, note: str) -> dict:
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lng >= 0 else "W"
    return {"lat": round(lat, 7), "lng": round(lng, 7), "label": f"{abs(lat):.6f}° {ns}, {abs(lng):.6f}° {ew}",
            "note": note, "source": "coordinates", "bbox": None}


def search(query: str, *, url: str, fetch: Callable, user_agent: str, limit: int = 5, language: str | None = None,
           timeout: float = 10.0) -> list[dict]:
    """Address lookup against a Nominatim-compatible ``/search`` endpoint.

    ``fetch(method, url, params=, headers=, timeout=)`` returns decoded JSON
    (see ``httpjson.fetch_json``). Results: ``{"lat", "lng", "label",
    "bbox": [west, south, east, north] | None, "source": "geocoder", "kind"}``.
    """
    q = (query or "").strip()
    if not q:
        return []
    if len(q) > MAX_QUERY:
        raise GeocodeError("The search text is too long")
    headers = {"User-Agent": user_agent, "Accept": "application/json"}
    if language:
        headers["Accept-Language"] = language
    try:
        data = fetch("GET", f"{url.rstrip('/')}/search",
                     params={"q": q, "format": "jsonv2", "limit": int(limit), "addressdetails": 0},
                     headers=headers, timeout=timeout)
    except Exception as e:
        raise GeocodeError(f"Address search is unavailable ({e})") from e
    if not isinstance(data, list):
        raise GeocodeError("Address search returned an unexpected answer")
    out = []
    for item in data[:limit]:
        try:
            lat, lng = float(item["lat"]), float(item["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        bbox = None
        bb = item.get("boundingbox")
        if isinstance(bb, (list, tuple)) and len(bb) == 4:
            try:
                south, north, west, east = (float(v) for v in bb)
                bbox = [west, south, east, north]
            except (TypeError, ValueError):
                bbox = None
        out.append({
            "lat": lat, "lng": lng, "label": str(item.get("display_name") or f"{lat:.5f}, {lng:.5f}")[:300],
            "bbox": bbox, "source": "geocoder", "kind": str(item.get("type") or item.get("category") or ""),
            "note": "",
        })
    return out
