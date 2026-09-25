"""Object storage: the canonical store for task inputs and outputs.

S3 (or any S3-compatible service such as MinIO) is the system of record for
every dataset and every result once ``storage_bucket`` is configured. The
host's ``private/files`` directory becomes a serving cache that can be thrown
away at any time (see :mod:`webodm_core.storage.cache`). With no bucket
configured nothing here is used and the pipeline behaves exactly as before:
host disk is the only store.

Configuration split, on purpose:

* **Non-secret settings** come from site config (written by
  ``infra/frappe/configure_site.py`` from ``WEBODM_S3_*`` env vars):
  ``storage_bucket``, ``storage_prefix``, ``storage_region``,
  ``storage_endpoint_url``, ``storage_force_path_style``,
  ``storage_presign_ttl``.
* **Credentials** are read from the process environment only
  (``WEBODM_S3_ACCESS_KEY_ID`` / ``WEBODM_S3_SECRET_ACCESS_KEY`` /
  ``WEBODM_S3_SESSION_TOKEN``, or the default boto3 chain — instance role,
  ``AWS_*`` — when unset). They are never written to site config, the
  database, logs or error messages. The same rule applies to the geospatial
  service, which has its own (read + convert) identity.

Key layout — every key is namespaced by organization so no path can cross an
org boundary (``assert_org_key`` enforces it on every read and write that
carries an org context)::

    <prefix>orgs/<org-slug>/tasks/<task>/inputs/<file name>     uploaded imagery
    <prefix>orgs/<org-slug>/tasks/<task>/raw/all.zip            transient node output
    <prefix>orgs/<org-slug>/tasks/<task>/raw/<asset>            transient pre-COG raster
    <prefix>orgs/<org-slug>/tasks/<task>/assets/<asset>         canonical outputs (COGs, LAZ, GLB)
    <prefix>orgs/<org-slug>/plugin-runs/<run>/<file name>       plugin outputs

The provider (AWS S3, MinIO, ...) is invisible above this module.
"""

from __future__ import annotations

import io
import os
import re
from collections.abc import Iterable

import frappe
from frappe.utils import cint

CHUNK_SIZE = 8 * 1024 * 1024
# Multipart settings for the boto3 transfer manager: large enough that a
# 2 GB orthophoto is ~32 parts, small enough that a failed part is cheap.
MULTIPART_THRESHOLD = 64 * 1024 * 1024
MULTIPART_CHUNKSIZE = 64 * 1024 * 1024
MAX_CONCURRENCY = 4

DEFAULT_PRESIGN_TTL = 15 * 60

_UNSAFE_KEY_CHARS = re.compile(r"[^A-Za-z0-9._\-]")


class StorageError(Exception):
    """Object storage answered with an error (permission denied, missing key...)."""


class StorageNotConfigured(StorageError):
    """No bucket configured: the host disk is the only store."""


class StorageUnavailable(StorageError):
    """Object storage could not be reached. Usually transient; callers retry."""


class OrgBoundaryError(StorageError):
    """A key outside the caller's organization namespace was requested."""


def configured() -> bool:
    """True when a bucket is configured and object storage is the canonical store."""
    return bool(_conf("storage_bucket"))


def _conf(key: str, default=None):
    return frappe.conf.get(key, default)


def _env_credentials() -> dict:
    """Credentials from the environment only. Empty dict -> boto3 default chain."""
    creds = {}
    key_id = os.environ.get("WEBODM_S3_ACCESS_KEY_ID")
    secret = os.environ.get("WEBODM_S3_SECRET_ACCESS_KEY")
    if key_id and secret:
        creds["aws_access_key_id"] = key_id
        creds["aws_secret_access_key"] = secret
        token = os.environ.get("WEBODM_S3_SESSION_TOKEN")
        if token:
            creds["aws_session_token"] = token
    return creds


def safe_segment(value: str) -> str:
    """Make ``value`` safe to use as one key segment (no separators, no traversal)."""
    segment = _UNSAFE_KEY_CHARS.sub("_", (value or "").replace("/", "_").replace("\\", "_"))
    segment = segment.strip(".")
    return segment or "_"


def org_namespace(org: str) -> str:
    """The stable, URL-safe segment that namespaces an organization's objects.

    Uses the organization's slug (unique, set at insert); falls back to a
    scrubbed name for rows that predate slugs. Keys are stored explicitly on
    every row, so a later slug change only affects newly written objects.
    """
    if not org:
        raise OrgBoundaryError("object storage keys require an organization")
    slug = frappe.db.get_value("WebODM Organization", org, "slug")
    return safe_segment(slug or frappe.scrub(org).replace("_", "-"))


def org_prefix(org: str) -> str:
    return f"{_conf('storage_prefix') or ''}orgs/{org_namespace(org)}/"


def task_prefix(task) -> str:
    return f"{org_prefix(task.organization)}tasks/{safe_segment(task.name)}/"


def task_key(task, *parts: str) -> str:
    return task_prefix(task) + "/".join(safe_segment(p) for p in parts)


def plugin_run_prefix(run) -> str:
    return f"{org_prefix(run.organization)}plugin-runs/{safe_segment(run.name)}/"


def plugin_run_key(run, *parts: str) -> str:
    return plugin_run_prefix(run) + "/".join(safe_segment(p) for p in parts)


def assert_org_key(key: str, org: str):
    """Refuse any key that does not live under ``org``'s namespace."""
    prefix = org_prefix(org)
    if not key or not key.startswith(prefix):
        raise OrgBoundaryError("object key is outside the organization's namespace")


def uri(key: str) -> str:
    """``s3://bucket/key`` form used to hand a key to the geospatial service."""
    bucket = _conf("storage_bucket")
    if not bucket:
        raise StorageNotConfigured("storage_bucket is not configured")
    return f"s3://{bucket}/{key}"


def key_from_uri(value: str) -> str | None:
    bucket = _conf("storage_bucket")
    prefix = f"s3://{bucket}/"
    if bucket and value and value.startswith(prefix):
        return value[len(prefix):]
    return None


class ObjectStorage:
    """Thin, boto3-backed client scoped to the configured bucket.

    Every method translates botocore failures into :class:`StorageError`
    subclasses so callers never see (or log) provider-specific exceptions,
    which may echo request parameters.
    """

    def __init__(self):
        if not configured():
            raise StorageNotConfigured("storage_bucket is not configured")
        self.bucket = _conf("storage_bucket")
        self.region = _conf("storage_region") or None
        self.endpoint_url = _conf("storage_endpoint_url") or None
        self.force_path_style = bool(cint(_conf("storage_force_path_style") or 0)) or bool(self.endpoint_url)
        self.presign_ttl = cint(_conf("storage_presign_ttl") or DEFAULT_PRESIGN_TTL)
        self._client = None

    # -- client -----------------------------------------------------------

    @property
    def client(self):
        if self._client is None:
            try:
                import boto3
                from botocore.config import Config
            except ImportError as e:  # pragma: no cover - image always ships boto3
                raise StorageUnavailable("boto3 is not installed") from e
            cfg = Config(
                signature_version="s3v4",
                s3={"addressing_style": "path" if self.force_path_style else "auto"},
                retries={"max_attempts": 4, "mode": "standard"},
                connect_timeout=10,
                read_timeout=120,
            )
            self._client = boto3.client(
                "s3",
                region_name=self.region,
                endpoint_url=self.endpoint_url,
                config=cfg,
                **_env_credentials(),
            )
        return self._client

    def _transfer_config(self):
        from boto3.s3.transfer import TransferConfig

        return TransferConfig(
            multipart_threshold=MULTIPART_THRESHOLD,
            multipart_chunksize=MULTIPART_CHUNKSIZE,
            max_concurrency=MAX_CONCURRENCY,
            use_threads=True,
        )

    @staticmethod
    def _translate(exc: Exception, what: str) -> StorageError:
        """Map a botocore exception to our hierarchy without leaking details."""
        try:
            from botocore.exceptions import (
                BotoCoreError,
                ClientError,
                EndpointConnectionError,
                NoCredentialsError,
                ReadTimeoutError,
            )
        except ImportError:  # pragma: no cover
            return StorageError(f"{what} failed: {exc.__class__.__name__}")
        if isinstance(exc, ClientError):
            code = (exc.response or {}).get("Error", {}).get("Code", "")
            status = (exc.response or {}).get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
            if code in ("NoSuchKey", "404", "NotFound") or status == 404:
                return StorageError(f"{what} failed: object not found")
            if code in ("AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch", "403") or status == 403:
                return StorageError(f"{what} failed: access denied ({code or status})")
            if status and status >= 500:
                return StorageUnavailable(f"{what} failed: storage returned {status}")
            return StorageError(f"{what} failed: {code or 'client error'}")
        if isinstance(exc, (EndpointConnectionError, ReadTimeoutError)):
            return StorageUnavailable(f"{what} failed: storage unreachable")
        if isinstance(exc, NoCredentialsError):
            return StorageError(f"{what} failed: no storage credentials available")
        if isinstance(exc, BotoCoreError):
            return StorageUnavailable(f"{what} failed: {exc.__class__.__name__}")
        if isinstance(exc, (OSError, IOError)):
            return StorageUnavailable(f"{what} failed: {exc.__class__.__name__}")
        return StorageError(f"{what} failed: {exc.__class__.__name__}")

    # -- objects ----------------------------------------------------------

    def head(self, key: str) -> dict | None:
        """``{"size", "etag", "content_type"}`` or ``None`` when the key is absent."""
        try:
            resp = self.client.head_object(Bucket=self.bucket, Key=key)
        except Exception as e:
            err = self._translate(e, "head")
            if "not found" in str(err):
                return None
            raise err from None
        return {
            "size": int(resp.get("ContentLength") or 0),
            "etag": (resp.get("ETag") or "").strip('"'),
            "content_type": resp.get("ContentType") or "",
        }

    def exists(self, key: str) -> bool:
        return self.head(key) is not None

    def put_path(self, local_path: str, key: str, content_type: str | None = None) -> dict:
        extra = {"ContentType": content_type} if content_type else {}
        try:
            self.client.upload_file(
                local_path, self.bucket, key, ExtraArgs=extra or None, Config=self._transfer_config()
            )
        except Exception as e:
            raise self._translate(e, "upload") from None
        return self.head(key) or {"size": os.path.getsize(local_path), "etag": "", "content_type": content_type or ""}

    def put_stream(self, stream, key: str, content_type: str | None = None) -> dict:
        """Stream any ``read(n)`` object into ``key`` (multipart, never buffered whole)."""
        extra = {"ContentType": content_type} if content_type else {}
        try:
            self.client.upload_fileobj(
                _ReadAdapter(stream), self.bucket, key, ExtraArgs=extra or None, Config=self._transfer_config()
            )
        except Exception as e:
            raise self._translate(e, "upload") from None
        return self.head(key) or {"size": 0, "etag": "", "content_type": content_type or ""}

    def put_bytes(self, data: bytes, key: str, content_type: str | None = None) -> dict:
        return self.put_stream(io.BytesIO(data), key, content_type)

    def get_to_path(self, key: str, dest_path: str) -> str:
        """Download ``key`` to ``dest_path`` atomically (``.part`` then rename)."""
        os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
        part = dest_path + ".part"
        try:
            with open(part, "wb") as fh:
                self.client.download_fileobj(self.bucket, key, fh, Config=self._transfer_config())
            os.replace(part, dest_path)
        except Exception as e:
            try:
                os.remove(part)
            except OSError:
                pass
            raise self._translate(e, "download") from None
        return dest_path

    def open_stream(self, key: str):
        """A streaming, sequential ``read(n)`` body for ``key``."""
        try:
            resp = self.client.get_object(Bucket=self.bucket, Key=key)
        except Exception as e:
            raise self._translate(e, "get") from None
        return resp["Body"]

    def read_range(self, key: str, start: int, end_inclusive: int) -> bytes:
        try:
            resp = self.client.get_object(Bucket=self.bucket, Key=key, Range=f"bytes={start}-{end_inclusive}")
            return resp["Body"].read()
        except Exception as e:
            raise self._translate(e, "range read") from None

    def open_seekable(self, key: str, buffer_size: int = CHUNK_SIZE):
        """A seekable, buffered file object over ``key`` (range reads).

        Lets ``zipfile`` walk a multi-GB archive in S3 without ever downloading
        it: the central directory is a couple of range reads and each member is
        streamed in ``buffer_size`` chunks.
        """
        from webodm_core.storage.s3file import S3RangeFile

        meta = self.head(key)
        if meta is None:
            raise StorageError("open failed: object not found")
        return io.BufferedReader(S3RangeFile(self, key, meta["size"]), buffer_size=buffer_size)

    def copy(self, src_key: str, dest_key: str, content_type: str | None = None) -> dict:
        """Server-side copy within the bucket (multipart for large objects)."""
        extra = {"ContentType": content_type, "MetadataDirective": "REPLACE"} if content_type else None
        try:
            self.client.copy(
                {"Bucket": self.bucket, "Key": src_key}, self.bucket, dest_key,
                ExtraArgs=extra, Config=self._transfer_config(),
            )
        except Exception as e:
            raise self._translate(e, "copy") from None
        return self.head(dest_key) or {}

    def delete(self, key: str):
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as e:
            err = self._translate(e, "delete")
            if "not found" in str(err):
                return
            raise err from None

    def list_keys(self, prefix: str) -> Iterable[str]:
        try:
            paginator = self.client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                for obj in page.get("Contents", []) or []:
                    yield obj["Key"]
        except Exception as e:
            raise self._translate(e, "list") from None

    def delete_prefix(self, prefix: str) -> int:
        """Delete everything under ``prefix``; returns the number of objects removed."""
        if not prefix or not prefix.endswith("/"):
            raise StorageError("delete_prefix requires a directory-style prefix")
        keys = list(self.list_keys(prefix))
        deleted = 0
        try:
            for i in range(0, len(keys), 1000):
                batch = [{"Key": k} for k in keys[i:i + 1000]]
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": batch, "Quiet": True})
                deleted += len(batch)
        except Exception as e:
            raise self._translate(e, "delete") from None
        return deleted

    def presign_get(self, key: str, ttl: int | None = None) -> str:
        """Short-lived, single-object GET URL. Never longer than ``storage_presign_ttl``."""
        ttl = min(cint(ttl) or self.presign_ttl, self.presign_ttl)
        try:
            return self.client.generate_presigned_url(
                "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=ttl
            )
        except Exception as e:
            raise self._translate(e, "presign") from None


class _ReadAdapter:
    """Give boto3 the plain ``read(n)`` surface it expects from arbitrary streams
    (zipfile members, urllib3 responses, StreamingBody...)."""

    def __init__(self, stream):
        self._stream = stream

    def read(self, n=-1):
        if n is None or n < 0:
            return self._stream.read()
        return self._stream.read(n)


_INSTANCE_KEY = "webodm_object_storage"


def get() -> ObjectStorage:
    """The process-local storage client (one per request/job)."""
    store = getattr(frappe.local, _INSTANCE_KEY, None)
    if store is None:
        store = ObjectStorage()
        setattr(frappe.local, _INSTANCE_KEY, store)
    return store


def content_type_for(filename: str) -> str:
    name = (filename or "").lower()
    if name.endswith((".tif", ".tiff")):
        return "image/tiff"
    if name.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if name.endswith(".png"):
        return "image/png"
    if name.endswith(".laz"):
        return "application/vnd.laszip"
    if name.endswith(".las"):
        return "application/vnd.las"
    if name.endswith(".glb"):
        return "model/gltf-binary"
    if name.endswith(".zip"):
        return "application/zip"
    if name.endswith(".geojson") or name.endswith(".json"):
        return "application/geo+json" if name.endswith(".geojson") else "application/json"
    return "application/octet-stream"
