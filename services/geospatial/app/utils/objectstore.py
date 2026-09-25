"""Object storage for the geospatial service: read rasters straight from S3,
write COGs back to S3.

Callers (the Frappe tile proxy and pipeline) pass either an absolute path on
the shared volume — the serving cache — or an ``s3://bucket/key`` URI. For
URIs, GDAL's ``/vsis3/`` virtual filesystem does the reading, which is what
makes tiling over object storage viable: a Cloud-Optimized GeoTIFF is served
with a handful of HTTP range reads per tile, never a whole-file download.

Configuration is environment only (this service has its own, read + convert,
identity — separate from the app's writer identity and the provisioner's):

    S3_BUCKETS            comma-separated buckets this service may touch (allowlist)
    S3_ENDPOINT_URL       custom endpoint (MinIO, other S3-compatible); empty for AWS
    AWS_REGION            region for AWS S3 (AWS_DEFAULT_REGION also honoured)
    AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (+ *_FILE)   credentials; unset -> default chain
    S3_FORCE_PATH_STYLE   "true" for MinIO-style path addressing (default: true when an endpoint is set)
    COG_SCRATCH_DIR       scratch for S3->S3 conversion (container-local, default /tmp)

GDAL tuning for range reads is applied through ``gdal_env()`` around every
open: ``GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR`` (no directory listing per
open), merged consecutive ranges, a VSI cache and a larger initial header
read so a COG's IFDs come in one request.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from urllib.parse import urlparse

import rasterio


class ObjectStoreError(ValueError):
    """Bad URI, bucket not allowed, or object storage rejected the request."""


class ObjectStoreNotConfigured(ObjectStoreError):
    pass


def _env(name: str, default: str | None = None) -> str | None:
    file_var = os.environ.get(f"{name}_FILE")
    if file_var:
        try:
            with open(file_var) as fh:
                return fh.read().strip()
        except OSError:
            return default
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def allowed_buckets() -> set[str]:
    raw = _env("S3_BUCKETS", "") or ""
    return {b.strip() for b in raw.split(",") if b.strip()}


def configured() -> bool:
    return bool(allowed_buckets())


def is_object_uri(path: str) -> bool:
    return bool(path) and path.startswith("s3://")


def parse_uri(uri: str) -> tuple[str, str]:
    """``("bucket", "key")`` from ``s3://bucket/key``; validates against the allowlist."""
    if not is_object_uri(uri):
        raise ObjectStoreError(f"not an object URI: {uri!r}")
    parsed = urlparse(uri)
    bucket, key = parsed.netloc, parsed.path.lstrip("/")
    if not bucket or not key:
        raise ObjectStoreError("object URI must be s3://bucket/key")
    if ".." in key.split("/"):
        raise ObjectStoreError("object key must not contain '..'")
    if not configured():
        raise ObjectStoreNotConfigured("object storage is not configured on the geospatial service (S3_BUCKETS)")
    if bucket not in allowed_buckets():
        raise ObjectStoreError("bucket is not in the allowed list")
    return bucket, key


def to_gdal_path(uri: str) -> str:
    """``s3://b/k`` -> ``/vsis3/b/k`` (after allowlist validation)."""
    bucket, key = parse_uri(uri)
    return f"/vsis3/{bucket}/{key}"


def gdal_options() -> dict:
    """GDAL config for ``rasterio.Env`` (no credentials): endpoint flags + range-read tuning.

    Credentials and the endpoint host go through ``aws_session()`` because
    rasterio refuses ``AWS_ACCESS_KEY_ID`` as a plain option.
    """
    opts = {
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
        "GDAL_HTTP_MULTIRANGE": "YES",
        "GDAL_HTTP_MAX_RETRY": "4",
        "GDAL_HTTP_RETRY_DELAY": "1",
        "GDAL_INGESTED_BYTES_AT_OPEN": "32768",
        "VSI_CACHE": "TRUE",
        "VSI_CACHE_SIZE": str(64 * 1024 * 1024),
        "CPL_VSIL_CURL_CACHE_SIZE": str(64 * 1024 * 1024),
    }
    endpoint = _env("S3_ENDPOINT_URL")
    if endpoint:
        parsed = urlparse(endpoint)
        opts["AWS_HTTPS"] = "YES" if parsed.scheme == "https" else "NO"
        opts["AWS_VIRTUAL_HOSTING"] = "FALSE" if _force_path_style(default=True) else "TRUE"
    elif _force_path_style(default=False):
        opts["AWS_VIRTUAL_HOSTING"] = "FALSE"
    return opts


def gdal_env() -> dict:
    """Everything GDAL ends up configured with (for logging/tests): options + session credentials."""
    opts = dict(gdal_options())
    opts.update(aws_session().get_credential_options())
    return opts


def aws_session():
    """``rasterio.session.AWSSession`` from the env (explicit keys, else boto3's default chain)."""
    from rasterio.session import AWSSession

    key_id, secret = _env("AWS_ACCESS_KEY_ID"), _env("AWS_SECRET_ACCESS_KEY")
    endpoint = _env("S3_ENDPOINT_URL")
    kwargs = {"region_name": _env("AWS_REGION") or _env("AWS_DEFAULT_REGION")}
    if endpoint:
        kwargs["endpoint_url"] = urlparse(endpoint).netloc or endpoint
    if key_id and secret:
        kwargs.update(aws_access_key_id=key_id, aws_secret_access_key=secret,
                      aws_session_token=_env("AWS_SESSION_TOKEN"))
    return AWSSession(**kwargs)


def _force_path_style(default: bool) -> bool:
    raw = _env("S3_FORCE_PATH_STYLE")
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@contextmanager
def open_env():
    """``rasterio.Env`` with the object-store settings; cheap, use around every open.

    Without an S3 configuration this is a plain ``rasterio.Env`` (no boto3
    session is created), so local-only deployments pay nothing.
    """
    if not configured():
        with rasterio.Env():
            yield
        return
    with rasterio.Env(session=aws_session(), **gdal_options()):
        yield


def resolve_read_path(path: str) -> str:
    """Turn a caller-supplied path into something ``rasterio.open`` accepts.

    Local absolute paths pass through untouched (callers still check they
    exist); ``s3://`` URIs become ``/vsis3/`` paths after validation.
    """
    if is_object_uri(path):
        return to_gdal_path(path)
    return path


# -- boto3 side: existence, size, upload --------------------------------------


def _client():
    try:
        import boto3
        from botocore.config import Config
    except ImportError as e:  # pragma: no cover
        raise ObjectStoreError("boto3 is not installed") from e
    endpoint = _env("S3_ENDPOINT_URL")
    kwargs = {}
    key_id, secret = _env("AWS_ACCESS_KEY_ID"), _env("AWS_SECRET_ACCESS_KEY")
    if key_id and secret:
        kwargs["aws_access_key_id"] = key_id
        kwargs["aws_secret_access_key"] = secret
        if _env("AWS_SESSION_TOKEN"):
            kwargs["aws_session_token"] = _env("AWS_SESSION_TOKEN")
    path_style = _force_path_style(default=bool(endpoint))
    return boto3.client(
        "s3",
        region_name=_env("AWS_REGION") or _env("AWS_DEFAULT_REGION"),
        endpoint_url=endpoint or None,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path" if path_style else "auto"},
                      retries={"max_attempts": 4, "mode": "standard"}),
        **kwargs,
    )


def object_size(uri: str) -> int | None:
    """Size in bytes, or ``None`` when the object does not exist."""
    bucket, key = parse_uri(uri)
    try:
        return int(_client().head_object(Bucket=bucket, Key=key).get("ContentLength") or 0)
    except Exception as e:
        code = getattr(e, "response", {}).get("Error", {}).get("Code", "") if hasattr(e, "response") else ""
        if code in ("404", "NoSuchKey", "NotFound"):
            return None
        raise ObjectStoreError(f"head failed: {code or e.__class__.__name__}") from None


def exists(uri: str) -> bool:
    return object_size(uri) is not None


def upload_file(local_path: str, uri: str, content_type: str = "image/tiff"):
    bucket, key = parse_uri(uri)
    try:
        _client().upload_file(local_path, bucket, key, ExtraArgs={"ContentType": content_type})
    except Exception as e:
        raise ObjectStoreError(f"upload failed: {e.__class__.__name__}") from None


def scratch_dir() -> str:
    return _env("COG_SCRATCH_DIR", tempfile.gettempdir()) or tempfile.gettempdir()
