"""Ground control point (GCP) files: the format ODM reads, parsed and checked.

A GCP file is the plain-text sidecar an experienced WebODM user attaches next
to the photos when the images carry no GPS or survey-grade accuracy is
needed. ODM's format (``opendronemap/ODM`` ``opendm/gcp.py``)::

    <CRS: proj4 string | EPSG:xxxx | "WGS84 UTM 33N">
    geo_x geo_y geo_z im_x im_y image_name [gcp_name] [extra1] [extra2]
    ...

Lines are whitespace-separated; blank lines and ``#`` comments are ignored.
The file travels to the node as one more upload named ``gcp_list.txt``
(NodeODM passes any ``.txt`` it finds among the images to ODM as ``--gcp``).

This module has no Frappe dependency so the parser is testable on its own.
It is deliberately strict about *shape* and lenient about *content*: a file
that is not the format at all is refused with the offending line number,
while plausible-but-unusual values (a CRS string ODM may or may not accept,
fewer points than ODM would like) are reported in the summary and left to the
engine, exactly as self-hosted WebODM does.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# A GCP file is a few kilobytes; anything beyond this is not one.
MAX_GCP_BYTES = 5 * 1024 * 1024
# The name the node sees, whatever the user called the file. A fixed name
# keeps NodeODM's sidecar detection unambiguous: ``geo.txt`` would be read as
# an image geolocation file and ``align.*`` as an alignment reference.
UPLOAD_NAME = "gcp_list.txt"
# ODM recommends at least this many points, each visible in several images;
# fewer is accepted but flagged so the UI can warn before a long run.
RECOMMENDED_MIN_POINTS = 3


class GCPFormatError(ValueError):
    """The content is not a GCP file. ``line`` is 1-based (``None`` for whole-file problems)."""

    def __init__(self, message: str, line: int | None = None):
        super().__init__(message)
        self.line = line

    def __str__(self) -> str:
        base = super().__str__()
        return f"line {self.line}: {base}" if self.line else base


@dataclass
class GCPPoint:
    geo: tuple[float, float, float]
    pixel: tuple[float, float]
    image: str
    label: str | None = None
    line: int = 0


@dataclass
class GCPInfo:
    crs: str
    points: list[GCPPoint] = field(default_factory=list)

    @property
    def images(self) -> list[str]:
        """Distinct image names referenced, in first-seen order."""
        seen = []
        for p in self.points:
            if p.image not in seen:
                seen.append(p.image)
        return seen

    @property
    def labels(self) -> list[str]:
        seen = []
        for p in self.points:
            if p.label and p.label not in seen:
                seen.append(p.label)
        return seen

    def unknown_images(self, known: list[str] | set[str]) -> list[str]:
        """Image names the file references that are not in ``known`` (exact match, like ODM)."""
        known_set = set(known)
        return [name for name in self.images if name not in known_set]

    def summary(self, known_images: list[str] | set[str] | None = None) -> dict:
        """The JSON-friendly block stored on the dataset and shown in the UI."""
        out = {
            "crs": self.crs,
            "points": len(self.points),
            "labels": len(self.labels),
            "images": self.images,
            "image_count": len(self.images),
            "few_points": len(self.labels or self.points) < RECOMMENDED_MIN_POINTS,
        }
        if known_images is not None:
            out["unknown_images"] = self.unknown_images(known_images)
        return out


def _is_number(token: str) -> bool:
    try:
        float(token)
    except ValueError:
        return False
    return True


def decode(data: bytes) -> str:
    """Bytes -> text. UTF-8 (BOM tolerated) or Latin-1; binary content is refused."""
    if not data or not data.strip():
        raise GCPFormatError("the file is empty")
    if len(data) > MAX_GCP_BYTES:
        raise GCPFormatError(f"the file is larger than {MAX_GCP_BYTES // (1024 * 1024)} MB; this is not a GCP file")
    if b"\x00" in data:
        raise GCPFormatError("the file is binary, not a text GCP file")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def parse(text: str | bytes) -> GCPInfo:
    """Parse a GCP file. Raises :class:`GCPFormatError` on anything that is not the format."""
    if isinstance(text, bytes):
        text = decode(text)

    crs = None
    points: list[GCPPoint] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if crs is None:
            tokens = line.split()
            if len(tokens) >= 5 and all(_is_number(t) for t in tokens[:5]):
                raise GCPFormatError(
                    "the first line must be the coordinate reference system "
                    "(a proj4 string, EPSG:<code> or e.g. 'WGS84 UTM 33N'), not a point", number)
            crs = line
            continue
        tokens = line.split()
        if len(tokens) < 6:
            raise GCPFormatError(
                "expected 'geo_x geo_y geo_z im_x im_y image_name [gcp_name]' (at least 6 columns)", number)
        bad = next((i for i, t in enumerate(tokens[:5]) if not _is_number(t)), None)
        if bad is not None:
            what = ("geo_x", "geo_y", "geo_z", "im_x", "im_y")[bad]
            raise GCPFormatError(f"{what} is not a number: {tokens[bad]!r}", number)
        image = tokens[5]
        if _is_number(image):
            raise GCPFormatError("the sixth column must be the image file name", number)
        label = tokens[6] if len(tokens) > 6 else None
        points.append(GCPPoint(
            geo=(float(tokens[0]), float(tokens[1]), float(tokens[2])),
            pixel=(float(tokens[3]), float(tokens[4])),
            image=image, label=label, line=number,
        ))

    if crs is None:
        raise GCPFormatError("the file has no content")
    if not points:
        raise GCPFormatError("the file names a coordinate system but contains no points")
    return GCPInfo(crs=crs, points=points)
