"""KML: the survey boundary for vendor apps, and the whole plan for review.

Two formats share this module because they share the writer and differ in
purpose:

- ``kml-boundary`` holds exactly one geometry, the survey polygon (or the
  corridor centre line). It is what the mapping apps of aircraft that plan
  their own lines import: DJI Pilot 2 and GS RTK, WingtraPilot, eMotion,
  Autel's apps. Nothing else is in the file, because importers take the
  first geometry they find.
- ``kml`` holds boundary, flight path, waypoints and takeoff point, for
  Google Earth and for briefing. It is a picture of the plan, not a file an
  aircraft flies.
"""

from __future__ import annotations

from ..issues import NOTICE, info
from .base import ExportContext, Exporter, fmt, xml_escape

MAX_WAYPOINT_PLACEMARKS = 2000


def _ring(coords) -> str:
    return " ".join(f"{fmt(c[0], 7)},{fmt(c[1], 7)},0" for c in coords)


def _boundary_placemark(geometry: dict, name: str, style: str = "") -> list[str]:
    out = ["    <Placemark>", f"      <name>{xml_escape(name)}</name>"]
    if style:
        out.append(f"      <styleUrl>#{style}</styleUrl>")
    if geometry["type"] == "Polygon":
        rings = geometry["coordinates"]
        out += ["      <Polygon>", "        <tessellate>1</tessellate>",
                "        <outerBoundaryIs><LinearRing>",
                f"          <coordinates>{_ring(_closed(rings[0]))}</coordinates>",
                "        </LinearRing></outerBoundaryIs>"]
        for hole in rings[1:]:
            out += ["        <innerBoundaryIs><LinearRing>",
                    f"          <coordinates>{_ring(_closed(hole))}</coordinates>",
                    "        </LinearRing></innerBoundaryIs>"]
        out.append("      </Polygon>")
    else:
        out += ["      <LineString>", "        <tessellate>1</tessellate>",
                f"        <coordinates>{_ring(geometry['coordinates'])}</coordinates>", "      </LineString>"]
    out.append("    </Placemark>")
    return out


def _closed(ring):
    ring = list(ring)
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring


def _document(name: str, description: str, body: list[str], styles: list[str] | None = None) -> bytes:
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<kml xmlns="http://www.opengis.net/kml/2.2">',
           "  <Document>", f"    <name>{xml_escape(name)}</name>"]
    if description:
        out.append(f"    <description>{xml_escape(description)}</description>")
    out += styles or []
    out += body
    out += ["  </Document>", "</kml>", ""]
    return "\n".join(out).encode()


class KmlBoundary(Exporter):
    id = "kml-boundary"
    label = "Survey boundary (.kml)"
    extension = "kml"
    mime = "application/vnd.google-earth.kml+xml"
    kind = "boundary"
    description = ("Only the survey area (or corridor centre line), for flight apps that import a boundary and "
                   "plan their own flight lines.")
    instructions = (
        "Import the .kml as an area (or linear / corridor) mission in the aircraft's flight app, then set the "
        "height, overlap and speed there using the values from this plan.",
        "DJI Pilot 2: Flight Route > Import Route (KMZ/KML), choose Mapping (or Linear Flight for a corridor). "
        "DJI GS RTK / GS Pro, WingtraPilot, eMotion and Autel's apps have an equivalent KML import.",
        "The app plans its own flight lines: its line count, flight time and battery estimate replace the ones here.",
    )

    def check(self, ctx: ExportContext, routes):
        cam = ctx.camera
        return [info(
            "BOUNDARY_PARAMETERS", "export",
            f"The file holds the boundary only. Enter these in the flight app: height {cam['altitude_m']:g} m, "
            f"front overlap {ctx.params['front_overlap']:g}%, side overlap {ctx.params['side_overlap']:g}%, "
            f"speed {ctx.speed:g} m/s"
            + (f", corridor width {ctx.options.get('corridor_width_m'):g} m" if ctx.options.get("corridor_width_m") else "")
            + ".", basis="note")]

    def render_plan(self, ctx: ExportContext) -> bytes:
        return _document(ctx.title, "", _boundary_placemark(ctx.options["geometry"], ctx.title))


class KmlPlan(Exporter):
    id = "kml"
    label = "Plan overview (.kml)"
    extension = "kml"
    mime = "application/vnd.google-earth.kml+xml"
    kind = "reference"
    description = "Boundary, flight path, waypoints and takeoff point for Google Earth and briefings. Not flyable."
    instructions = (
        "Open in Google Earth (or any KML viewer) to review the route against imagery and terrain.",
        "This file is a picture of the plan. Do not load it into a flight app as a mission.",
    )

    def render_plan(self, ctx: ExportContext) -> bytes:
        m = ctx.mission
        stats, cam = m["stats"], m["camera"]
        description = (
            f"{m['equipment']['drone']['label']} + {m['equipment']['camera']['label']}. "
            f"Height {cam['altitude_m']:g} m, GSD {cam['gsd_cm']:g} cm/px, {stats['line_count']} lines, "
            f"{stats['photo_count']} photos, {stats['total_distance_m'] / 1000:.1f} km, "
            f"{stats['total_duration_s'] / 60:.0f} min, {stats['batteries']} batter{'y' if stats['batteries'] == 1 else 'ies'} "
            f"(estimates). {NOTICE}"
        )
        styles = [
            '    <Style id="boundary"><LineStyle><color>ffeb6325</color><width>2</width></LineStyle>'
            '<PolyStyle><color>33eb6325</color></PolyStyle></Style>',
            '    <Style id="path"><LineStyle><color>ff0b9ef5</color><width>2.5</width></LineStyle></Style>',
        ]
        body = _boundary_placemark(ctx.options["geometry"], "Survey boundary", "boundary")
        take = m["takeoff"]
        body += ["    <Placemark>", "      <name>Takeoff point</name>",
                 f"      <description>{'Assumed (not set in the plan)' if take.get('assumed') else 'Planned'}</description>",
                 f"      <Point><coordinates>{fmt(take['lng'], 7)},{fmt(take['lat'], 7)},0</coordinates></Point>",
                 "    </Placemark>"]
        wps = m["waypoints"]
        # Heights are relative to the takeoff point; with terrain known the
        # path is drawn at its estimated height above ground instead.
        use_agl = all(wp.get("agl") is not None for wp in wps)
        coords = " ".join(
            f"{fmt(wp['lng'], 7)},{fmt(wp['lat'], 7)},{fmt(wp['agl'] if use_agl else wp['alt'], 1)}" for wp in wps)
        body += ["    <Placemark>", "      <name>Flight path</name>", "      <styleUrl>#path</styleUrl>",
                 "      <LineString>", "        <altitudeMode>relativeToGround</altitudeMode>",
                 f"        <coordinates>{coords}</coordinates>", "      </LineString>", "    </Placemark>"]
        if len(wps) <= MAX_WAYPOINT_PLACEMARKS:
            body.append("    <Folder>")
            body.append("      <name>Waypoints</name>")
            for wp in wps:
                body += [
                    "      <Placemark>", f"        <name>{wp['index'] + 1}</name>",
                    f"        <description>Line {wp['line'] + 1}, {wp['role']}; {fmt(wp['alt'], 1)} m above takeoff"
                    + (f", about {fmt(wp['agl'], 0)} m above ground" if wp.get("agl") is not None else "")
                    + "</description>",
                    "        <Point><altitudeMode>relativeToGround</altitudeMode>"
                    f"<coordinates>{fmt(wp['lng'], 7)},{fmt(wp['lat'], 7)},"
                    f"{fmt(wp['agl'] if use_agl else wp['alt'], 1)}</coordinates></Point>",
                    "      </Placemark>",
                ]
            body.append("    </Folder>")
        return _document(ctx.title, description, body, styles)
