"""LAS/LAZ -> Potree 2.0 octree conversion with PotreeConverter.

PotreeConverter 2.x (``POTREE_CONVERTER_BIN``, default ``PotreeConverter`` on
``PATH``) turns a LAS/LAZ file into three files — ``metadata.json``,
``hierarchy.bin`` and ``octree.bin`` — that a Potree 2.0 loader streams with
HTTP range requests. The conversion is out-of-core and keeps the standard
attributes (position, rgb, intensity, classification, returns, gps-time, ...).

This module wraps the binary and adds what it lacks:

* ``read_header`` parses the LAS public header + VLRs (uncompressed in LAZ
  too) without any point-cloud library: point count, format, bounds and the
  CRS (WKT VLR 2112 or the GeoTIFF key directory 34735 -> EPSG code).
* The converter is run into a scratch directory and the result is moved into
  place atomically, so a half-written octree is never served.
* ``projection`` is written into ``metadata.json`` by us: the converter's own
  ``--projection`` flag embeds the string unescaped, which breaks the JSON for
  any WKT (quotes), so it is never used.
* ``log.txt`` (the converter's run log) is dropped from the output.

Environment:

    POTREE_CONVERTER_BIN      converter binary (default: PotreeConverter)
    POTREE_TIMEOUT_SECONDS    kill the converter after this long (default 7200)
    POTREE_MAX_CONCURRENT     conversions per worker process (default 1)
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import tempfile
import time

OUTPUT_FILES = ("metadata.json", "hierarchy.bin", "octree.bin")
CONTENT_TYPES = {
    "metadata.json": "application/json",
    "hierarchy.bin": "application/octet-stream",
    "octree.bin": "application/octet-stream",
}

# LAS point data record formats that carry RGB / GPS time (LAS 1.4 R15, 2.6).
_RGB_FORMATS = {2, 3, 5, 7, 8, 10}
_GPS_FORMATS = {1, 3, 4, 5, 6, 7, 8, 9, 10}

_VLR_HEADER = struct.Struct("<H16sHH32s")
_GEOKEY_PROJECTED_CS = 3072
_GEOKEY_GEOGRAPHIC_CS = 2048


class PointCloudError(ValueError):
    """Unreadable input or a converter failure (message is safe to show)."""


class ConverterUnavailable(PointCloudError):
    pass


# ------------------------------------------------------------------ header


def _cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("ascii", "replace").strip()


def _parse_geokeys(data: bytes) -> int | None:
    """EPSG code from a GeoTIFF key directory (projected first, else geographic)."""
    if len(data) < 8:
        return None
    _version, _rev, _minor, count = struct.unpack_from("<4H", data, 0)
    found: dict[int, int] = {}
    for i in range(count):
        off = 8 + i * 8
        if off + 8 > len(data):
            break
        key_id, location, _n, value = struct.unpack_from("<4H", data, off)
        if location == 0 and key_id in (_GEOKEY_PROJECTED_CS, _GEOKEY_GEOGRAPHIC_CS):
            found[key_id] = value
    for key in (_GEOKEY_PROJECTED_CS, _GEOKEY_GEOGRAPHIC_CS):
        code = found.get(key)
        # 32767 = user-defined, 0 = undefined.
        if code and code != 32767:
            return code
    return None


def read_header(path: str) -> dict:
    """Public header + CRS of a LAS/LAZ file (no point data is read)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(375)
            if len(head) < 227 or head[:4] != b"LASF":
                raise PointCloudError("not a LAS/LAZ file (missing LASF signature)")
            major, minor = head[24], head[25]
            header_size, point_offset, vlr_count = struct.unpack_from("<HIL", head, 94)
            fmt_raw = head[104]
            record_length = struct.unpack_from("<H", head, 105)[0]
            legacy_points = struct.unpack_from("<I", head, 107)[0]
            scale = struct.unpack_from("<3d", head, 131)
            offset = struct.unpack_from("<3d", head, 155)
            max_x, min_x, max_y, min_y, max_z, min_z = struct.unpack_from("<6d", head, 179)
            points = legacy_points
            if (major, minor) >= (1, 4) and len(head) >= 375:
                points = struct.unpack_from("<Q", head, 247)[0] or legacy_points

            wkt = ""
            epsg = None
            fh.seek(header_size)
            for _ in range(vlr_count):
                raw = fh.read(_VLR_HEADER.size)
                if len(raw) < _VLR_HEADER.size:
                    break
                _reserved, user_id, record_id, length, _desc = _VLR_HEADER.unpack(raw)
                payload = fh.read(length)
                if _cstr(user_id) != "LASF_Projection":
                    continue
                if record_id == 2112 and not wkt:
                    wkt = _cstr(payload)
                elif record_id == 34735 and epsg is None:
                    epsg = _parse_geokeys(payload)
    except OSError as e:
        raise PointCloudError(f"cannot read {os.path.basename(path)}: {e.strerror or e}") from None

    fmt = fmt_raw & 0x3F
    return {
        "version": f"{major}.{minor}",
        "point_format": fmt,
        "compressed": bool(fmt_raw & 0x80),
        "record_length": record_length,
        "points": int(points),
        "scale": list(scale),
        "offset": list(offset),
        "bounds": [min_x, min_y, min_z, max_x, max_y, max_z],
        "has_rgb": fmt in _RGB_FORMATS,
        "has_gps_time": fmt in _GPS_FORMATS,
        "wkt": wkt,
        "epsg": epsg,
        "point_data_offset": point_offset,
    }


def projection_for(header: dict) -> str:
    """What to record as the octree's ``projection``: WKT if present, else ``EPSG:n``."""
    if header.get("wkt"):
        return header["wkt"]
    if header.get("epsg"):
        return f"EPSG:{header['epsg']}"
    return ""


# --------------------------------------------------------------- converter


def converter_bin() -> str:
    return os.environ.get("POTREE_CONVERTER_BIN") or "PotreeConverter"


def converter_path() -> str | None:
    """Absolute path of the converter, or ``None`` when it is not installed."""
    candidate = converter_bin()
    if os.path.isabs(candidate):
        return candidate if os.access(candidate, os.X_OK) else None
    return shutil.which(candidate)


def converter_available() -> bool:
    return converter_path() is not None


def timeout_seconds() -> int:
    try:
        return max(60, int(os.environ.get("POTREE_TIMEOUT_SECONDS") or 7200))
    except ValueError:
        return 7200


def max_concurrent() -> int:
    try:
        return max(1, int(os.environ.get("POTREE_MAX_CONCURRENT") or 1))
    except ValueError:
        return 1


def _tail(text: str, lines: int = 6, limit: int = 600) -> str:
    rows = [r for r in (text or "").strip().splitlines() if r.strip()]
    return "\n".join(rows[-lines:])[-limit:]


def convert(src_path: str, out_dir: str, *, projection: str | None = None,
            name: str | None = None, timeout: int | None = None) -> dict:
    """Run PotreeConverter on ``src_path`` and place the octree in ``out_dir``.

    The converter writes into a temporary sibling directory; only a complete
    result (all three files present, metadata parseable) is renamed into
    ``out_dir`` (replacing any previous octree). ``projection`` overrides the
    CRS recorded in metadata (default: taken from the LAS header). Returns the
    summary (``summarize``) of the written metadata plus the output sizes.
    """
    binary = converter_path()
    if binary is None:
        raise ConverterUnavailable(
            f"PotreeConverter is not installed on the geospatial service ({converter_bin()!r} not found)")
    if not os.path.isfile(src_path):
        raise PointCloudError(f"point cloud not found: {src_path}")

    header = read_header(src_path)
    if header["points"] <= 0:
        raise PointCloudError("the point cloud is empty")
    if projection is None:
        projection = projection_for(header)

    parent = os.path.dirname(os.path.abspath(out_dir)) or "."
    os.makedirs(parent, exist_ok=True)
    tmp_dir = tempfile.mkdtemp(prefix=".potree-", dir=parent)
    started = time.monotonic()
    try:
        # PotreeConverter wants a directory it owns; it creates it if missing.
        work = os.path.join(tmp_dir, "out")
        cmd = [binary, src_path, "-o", work, "--encoding", "UNCOMPRESSED", "--method", "poisson"]
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                  timeout=timeout or timeout_seconds(), cwd=tmp_dir)
        except subprocess.TimeoutExpired:
            raise PointCloudError(f"PotreeConverter exceeded {timeout or timeout_seconds()} s") from None
        except OSError as e:
            raise ConverterUnavailable(f"PotreeConverter could not be started: {e.strerror or e}") from None
        if proc.returncode != 0:
            raise PointCloudError(f"PotreeConverter failed (exit {proc.returncode}): {_tail(proc.stdout)}")
        missing = [f for f in OUTPUT_FILES if not os.path.isfile(os.path.join(work, f))]
        if missing:
            raise PointCloudError(f"PotreeConverter produced no {', '.join(missing)}: {_tail(proc.stdout)}")

        meta_path = os.path.join(work, "metadata.json")
        try:
            with open(meta_path, encoding="utf-8") as fh:
                metadata = json.load(fh)
        except (OSError, ValueError) as e:
            raise PointCloudError(f"PotreeConverter wrote unreadable metadata: {e}") from None
        if metadata.get("encoding") not in (None, "DEFAULT", "UNCOMPRESSED"):
            raise PointCloudError(f"unexpected octree encoding {metadata.get('encoding')!r}")
        metadata["projection"] = projection or ""
        metadata["encoding"] = "UNCOMPRESSED"
        if name:
            metadata["name"] = name
        metadata["source"] = {
            "points": header["points"],
            "point_format": header["point_format"],
            "las_version": header["version"],
            "epsg": header["epsg"],
        }
        with open(meta_path, "w", encoding="utf-8") as fh:
            json.dump(metadata, fh, indent=1)
        for extra in os.listdir(work):
            if extra not in OUTPUT_FILES:
                target = os.path.join(work, extra)
                shutil.rmtree(target, ignore_errors=True) if os.path.isdir(target) else os.unlink(target)

        # Atomic swap into place: a reader sees the old octree or the new one.
        if os.path.isdir(out_dir):
            stale = os.path.join(tmp_dir, "stale")
            os.rename(out_dir, stale)
        os.rename(work, out_dir)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    summary = summarize(metadata)
    summary["files"] = {f: {"path": os.path.join(out_dir, f), "size": os.path.getsize(os.path.join(out_dir, f))}
                        for f in OUTPUT_FILES}
    summary["duration_s"] = round(time.monotonic() - started, 2)
    return summary


def summarize(metadata: dict) -> dict:
    """Compact, JSON-safe description of an octree's ``metadata.json``."""
    attributes = []
    for attr in metadata.get("attributes") or []:
        entry = {
            "name": attr.get("name"),
            "type": attr.get("type"),
            "num_elements": attr.get("numElements"),
            "min": attr.get("min"),
            "max": attr.get("max"),
        }
        hist = attr.get("histogram")
        if isinstance(hist, list):
            entry["present_values"] = [i for i, n in enumerate(hist) if n]
        attributes.append(entry)
    return {
        "version": metadata.get("version"),
        "points": metadata.get("points"),
        "spacing": metadata.get("spacing"),
        "bounding_box": metadata.get("boundingBox"),
        "offset": metadata.get("offset"),
        "scale": metadata.get("scale"),
        "projection": metadata.get("projection") or "",
        "encoding": metadata.get("encoding"),
        "attributes": attributes,
    }
