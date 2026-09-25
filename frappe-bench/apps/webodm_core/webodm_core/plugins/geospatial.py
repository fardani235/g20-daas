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


def raster_metadata(path: str, timeout: int = 60) -> dict:
    """Header-only raster metadata via ``GET /raster/metadata``.

    Raises ``GeospatialError`` when the service rejected the file (corrupt,
    not a raster: HTTP 4xx with a ``detail``) and ``GeospatialUnavailable``
    when it could not be reached. A raster without a CRS is *not* an error;
    the returned document says so in ``georeference``.
    """
    url = f"{geospatial_url().rstrip('/')}/raster/metadata"
    try:
        resp = requests.get(url, params={"path": path}, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except requests.HTTPError as e:
        detail = ""
        try:
            detail = e.response.json().get("detail", "")
        except Exception:
            detail = e.response.text if e.response is not None else ""
        raise GeospatialError(f"raster metadata failed: {detail or e}") from e
    except Exception as e:
        raise GeospatialUnavailable(f"geospatial service unreachable: {e}") from e
    if not isinstance(data, dict):
        raise GeospatialError("raster metadata failed: unexpected response")
    return data


def cogify(path: str, output_path: str | None = None, timeout: int = 900) -> dict:
    """Convert a raster to a Cloud-Optimized GeoTIFF via ``POST /export/cogify``.

    ``path`` and ``output_path`` may be absolute paths on the shared volume or
    ``s3://bucket/key`` URIs; with both in S3 the conversion is S3 -> S3 and
    never touches the host. Returns the service's georeferencing dict (epsg,
    wkt, extent, embedded ``metadata`` read from the *output*). Raises
    ``GeospatialError`` on a 4xx (bad raster) and ``GeospatialUnavailable``
    when the service cannot be reached or errors.
    """
    url = f"{geospatial_url().rstrip('/')}/export/cogify"
    body = {"path": path}
    if output_path:
        body["output_path"] = output_path
    try:
        resp = requests.post(url, json=body, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as e:
        detail = ""
        try:
            detail = e.response.json().get("detail", "")
        except Exception:
            detail = e.response.text if e.response is not None else ""
        if e.response is not None and e.response.status_code >= 500:
            raise GeospatialUnavailable(f"cogify failed: {detail or e}") from e
        raise GeospatialError(f"cogify failed: {detail or e}") from e
    except Exception as e:
        raise GeospatialUnavailable(f"geospatial service unreachable: {e}") from e
