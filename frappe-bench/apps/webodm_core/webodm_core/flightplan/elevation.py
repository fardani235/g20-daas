"""Elevation providers: where terrain heights come from.

A provider answers one question — the ground elevation at a list of points —
and describes its own quality. Everything else (gridding, caching, terrain
following, clearance) is built on that, so supporting another DEM service or
an in-house tile store is one small class here plus an entry in
``PROVIDERS``.

Providers are deliberately dumb about HTTP: they are handed a ``fetch``
callable, which keeps this module free of Frappe and lets the tests run
without a network.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlsplit

from .httpjson import HttpError, fetch_json
from .terrain import GridSpec, TerrainModel, TerrainSource, model_from_samples

LatLng = tuple[float, float]


class ElevationError(Exception):
    """Elevation data could not be obtained (service down, bad answer, no coverage)."""


def default_fetch(method: str, url: str, *, params: dict | None = None, json: dict | None = None,
                  headers: dict | None = None, timeout: float = 15.0) -> dict:
    """GET/POST returning the decoded JSON body; raises ``ElevationError`` on any failure."""
    try:
        return fetch_json(method, url, params=params, json=json, headers=headers, timeout=timeout)
    except HttpError as e:
        raise ElevationError(str(e)) from e


@dataclass
class ElevationProvider:
    """Base class. Subclasses implement ``_batch`` for at most ``batch_size`` points."""

    url: str = ""
    dataset: str = ""
    fetch: Callable = default_fetch
    sleep: Callable = time.sleep
    user_agent: str = "webodm-flight-planner"
    timeout: float = 15.0
    resolution_m: float | None = None
    vertical_accuracy_m: float | None = None

    id = "base"
    label = "Elevation service"
    batch_size = 100
    # Seconds to wait between batches. Public instances ask for this; a
    # self-hosted one does not need it.
    min_interval_s = 0.0
    surface = "terrain"
    note = ""

    def _batch(self, points: list[LatLng]) -> list[float | None]:  # pragma: no cover - abstract
        raise NotImplementedError

    def sample(self, points: list[LatLng]) -> list[float | None]:
        out: list[float | None] = []
        for i in range(0, len(points), self.batch_size):
            if i and self.min_interval_s > 0:
                self.sleep(self.min_interval_s)
            chunk = points[i:i + self.batch_size]
            values = self._batch(chunk)
            if len(values) != len(chunk):
                raise ElevationError("elevation service returned a different number of points than requested")
            out.extend(_clean(v) for v in values)
        return out

    def source(self) -> TerrainSource:
        return TerrainSource(
            id=self.id, label=self.describe(), kind="global-dem", resolution_m=self.resolution_m,
            vertical_accuracy_m=self.vertical_accuracy_m, surface=self.surface, note=self.note,
        )

    def describe(self) -> str:
        host = urlsplit(self.url).hostname or ""
        return f"{self.label}{f' · {self.dataset}' if self.dataset else ''}{f' ({host})' if host else ''}"

    def _headers(self) -> dict:
        return {"User-Agent": self.user_agent, "Accept": "application/json"}


def _clean(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    # -32768 / large negatives are the usual DEM void markers.
    if v != v or v < -1000.0 or v > 9500.0:
        return None
    return v


@dataclass
class OpenTopoData(ElevationProvider):
    """Open Topo Data (https://www.opentopodata.org) — public instance or self-hosted."""

    url: str = "https://api.opentopodata.org/v1"
    dataset: str = "mapzen"
    resolution_m: float | None = 30.0
    vertical_accuracy_m: float | None = 10.0

    id = "opentopodata"
    label = "Open Topo Data"
    batch_size = 100
    note = ("Global ~30 m elevation model. It describes the ground only coarsely and knows nothing about "
            "vegetation, buildings, masts or wires.")

    def __post_init__(self):
        # The public instance allows one request per second.
        if (urlsplit(self.url).hostname or "") == "api.opentopodata.org":
            self.min_interval_s = 1.05

    def _batch(self, points):
        locations = "|".join(f"{lat:.6f},{lng:.6f}" for lat, lng in points)
        data = self.fetch("GET", f"{self.url.rstrip('/')}/{self.dataset}", params={"locations": locations},
                          headers=self._headers(), timeout=self.timeout)
        if not isinstance(data, dict) or data.get("status") != "OK":
            raise ElevationError(str((data or {}).get("error") or "elevation service refused the request")[:200])
        return [r.get("elevation") for r in data.get("results") or []]


@dataclass
class OpenElevation(ElevationProvider):
    """Open-Elevation (https://open-elevation.com) — public instance or self-hosted."""

    url: str = "https://api.open-elevation.com/api/v1"
    resolution_m: float | None = 30.0
    vertical_accuracy_m: float | None = 16.0

    id = "open-elevation"
    label = "Open-Elevation"
    batch_size = 200
    note = "SRTM-based elevation model; coarse, ground only, no vegetation or structures."

    def _batch(self, points):
        body = {"locations": [{"latitude": lat, "longitude": lng} for lat, lng in points]}
        data = self.fetch("POST", f"{self.url.rstrip('/')}/lookup", json=body, headers=self._headers(),
                          timeout=self.timeout)
        results = (data or {}).get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            raise ElevationError("elevation service returned an unexpected answer")
        return [r.get("elevation") for r in results]


@dataclass
class OpenMeteo(ElevationProvider):
    """Open-Meteo elevation API (Copernicus GLO-90). Free tier is for non-commercial use."""

    url: str = "https://api.open-meteo.com/v1"
    resolution_m: float | None = 90.0
    vertical_accuracy_m: float | None = 4.0

    id = "open-meteo"
    label = "Open-Meteo (Copernicus GLO-90)"
    batch_size = 100
    surface = "surface"
    note = ("Copernicus GLO-90 is a 90 m surface model: it follows large forest canopy and built-up areas "
            "only as a smoothed average and misses individual obstacles.")

    def _batch(self, points):
        params = {
            "latitude": ",".join(f"{lat:.6f}" for lat, _ in points),
            "longitude": ",".join(f"{lng:.6f}" for _, lng in points),
        }
        data = self.fetch("GET", f"{self.url.rstrip('/')}/elevation", params=params, headers=self._headers(),
                          timeout=self.timeout)
        values = (data or {}).get("elevation") if isinstance(data, dict) else None
        if not isinstance(values, list):
            raise ElevationError(str((data or {}).get("reason") or "elevation service returned an unexpected answer")[:200])
        return values


@dataclass
class CallableProvider(ElevationProvider):
    """Wraps a ``points -> elevations`` function (project rasters, tests)."""

    func: Callable[[list[LatLng]], list[float | None]] | None = None
    provider_id: str = "callable"
    provider_label: str = "Elevation"
    kind: str = "project-raster"
    provider_surface: str = "terrain"
    provider_note: str = ""
    batch_size = 5000

    def _batch(self, points):
        return list(self.func(points))

    def source(self) -> TerrainSource:
        return TerrainSource(
            id=self.provider_id, label=self.provider_label, kind=self.kind, resolution_m=self.resolution_m,
            vertical_accuracy_m=self.vertical_accuracy_m, surface=self.provider_surface, note=self.provider_note,
        )


PROVIDERS: dict[str, type[ElevationProvider]] = {
    OpenTopoData.id: OpenTopoData,
    OpenElevation.id: OpenElevation,
    OpenMeteo.id: OpenMeteo,
}


def provider_from_config(conf: dict, **kwargs) -> ElevationProvider | None:
    """Build the configured global provider, or ``None`` when elevation lookups are switched off.

    ``conf`` keys: ``provider`` (one of ``PROVIDERS`` or ``none``), ``url``,
    ``dataset``, ``resolution_m``, ``vertical_accuracy_m``, ``user_agent``.
    """
    name = str(conf.get("provider") or OpenTopoData.id).strip().lower()
    if name in ("none", "off", "disabled", ""):
        return None
    cls = PROVIDERS.get(name)
    if cls is None:
        raise ElevationError(f"unknown elevation provider: {name}")
    init = dict(kwargs)
    for key in ("url", "dataset", "user_agent"):
        if conf.get(key):
            init[key] = str(conf[key])
    for key in ("resolution_m", "vertical_accuracy_m"):
        if conf.get(key) not in (None, ""):
            init[key] = float(conf[key])
    return cls(**init)


def build_terrain(provider: ElevationProvider, spec: GridSpec) -> TerrainModel:
    """Sample ``spec`` through ``provider``. Raises ``ElevationError`` if nothing usable comes back."""
    samples = provider.sample(spec.points())
    model = model_from_samples(spec, samples, provider.source())
    if model.coverage <= 0.0:
        raise ElevationError("the elevation source has no data for this area")
    return model
