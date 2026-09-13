"""HTTP client for the geospatial service's analysis API.

Kept separate from the sync and runner logic so it is the single place that
knows the service URL and error semantics.
"""

import frappe
import requests


class GeospatialError(Exception):
    """Base error for geospatial service failures."""


class GeospatialUnavailable(GeospatialError):
    """The geospatial service could not be reached or returned an error."""


def geospatial_url() -> str:
    return (
        frappe.conf.get("geospatial_url")
        or frappe.conf.get("webodm_geospatial_url")
        or "http://127.0.0.1:5000"
    )


def fetch_catalog(timeout: int = 15) -> list[dict]:
    """Return the list of analysis operations from ``GET /analysis``.

    Raises ``GeospatialUnavailable`` if the service is down or responds badly,
    so callers can leave their previous catalog untouched.
    """
    url = f"{geospatial_url().rstrip('/')}/analysis"
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        raise GeospatialUnavailable(f"catalog fetch failed: {e}") from e
    return data.get("operations", [])


def run_operation(
    op_id: str,
    inputs: dict[str, str],
    params: dict,
    output_path: str,
    timeout: int = 600,
) -> dict:
    """Execute ``POST /analysis/{op_id}/run`` and return its result dict."""
    url = f"{geospatial_url().rstrip('/')}/analysis/{op_id}/run"
    try:
        resp = requests.post(
            url,
            json={"inputs": inputs, "params": params, "output_path": output_path},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as e:
        detail = ""
        try:
            detail = e.response.json().get("detail", "")
        except Exception:
            detail = e.response.text if e.response is not None else ""
        raise GeospatialError(f"analysis run failed: {detail or e}") from e
    except Exception as e:
        raise GeospatialUnavailable(f"analysis service unreachable: {e}") from e


def vector_to_geojson(path: str, output_path: str, timeout: int = 120) -> dict:
    """Convert a vector dataset to GeoJSON via ``POST /export/vector-to-geojson``."""
    url = f"{geospatial_url().rstrip('/')}/export/vector-to-geojson"
    try:
        resp = requests.post(
            url,
            json={"path": path, "output_path": output_path},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as e:
        detail = ""
        try:
            detail = e.response.json().get("detail", "")
        except Exception:
            detail = e.response.text if e.response is not None else ""
        raise GeospatialError(f"vector conversion failed: {detail or e}") from e
    except Exception as e:
        raise GeospatialUnavailable(f"analysis service unreachable: {e}") from e


def validate_operation(op_id: str, params: dict, timeout: int = 30) -> dict:
    """Ask the analysis service to validate params/preconditions without running."""
    url = f"{geospatial_url().rstrip('/')}/analysis/{op_id}/validate"
    try:
        resp = requests.post(url, json={"params": params}, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as e:
        detail = ""
        try:
            detail = e.response.json().get("detail", "")
        except Exception:
            detail = e.response.text if e.response is not None else ""
        raise GeospatialError(str(detail or e)) from e
    except Exception as e:
        raise GeospatialUnavailable(f"analysis service unreachable: {e}") from e
