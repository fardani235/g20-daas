"""DJI WPML route file (``.kmz``): DJI Pilot 2, FlightHub 2 and DJI Fly waypoint flight.

A WPML route is a zip holding ``wpmz/template.kml`` (the editable
definition) and ``wpmz/waylines.wpml`` (what the aircraft executes). This
exporter writes the ``waypoint`` template: explicit waypoints, so the
aircraft flies the lines shown in the planner rather than lines the app
re-derives from an area.

Field names, enumerations and structure follow DJI's published WPML
reference (developer.dji.com, Cloud API > DJI WPML). Heights use
``relativeToStartPoint``: every waypoint height is relative to where the
aircraft takes off, which is how the planner computes them (including the
per-waypoint heights of a terrain-following plan). That only holds if the
aircraft is launched from the planned takeoff point, or from the same
elevation; ``check`` says so on every export.

Aircraft and payload ids come from the profiles (``wpml`` blocks). Ids DJI
has not published are marked ``community`` there and flagged here.
"""

from __future__ import annotations

import io
import zipfile

from ..issues import info, warning
from ..issues import error as issue_error
from .base import STOP_AND_SHOOT_S, ExportContext, Exporter, Route, _ground_distance, fmt, xml_escape

NS = 'xmlns="http://www.opengis.net/kml/2.2" xmlns:wpml="http://www.dji.com/wpmz/1.0.2"'
FINISH = {"rth": "goHome", "hover": "noAction", "land": "autoLand"}
STOP_TURN = "toPointAndStopWithDiscontinuityCurvature"
PASS_TURN = "toPointAndPassWithContinuityCurvature"
# WPML value ranges (DJI WPML reference, waylines.wpml > mission information).
MAX_TRANSIT_SPEED = 15.0
MIN_TAKEOFF_HEIGHT, MAX_TAKEOFF_HEIGHT = 1.2, 1500.0
DEFAULT_TAKEOFF_HEIGHT = 20.0
MAX_INDEX = 65535


class DjiWpml(Exporter):
    id = "dji-wpml"
    label = "DJI WPML route (.kmz)"
    extension = "kmz"
    mime = "application/vnd.google-earth.kmz"
    kind = "mission"
    description = ("Waypoint route for DJI Pilot 2 and FlightHub 2 (Enterprise aircraft). DJI Fly's Waypoint "
                   "Flight reads the same format, but only by replacing a saved mission file on the controller.")
    triggers = ("distance", "time", "waypoint", "manual")
    max_waypoints = MAX_INDEX
    instructions = (
        "DJI Pilot 2: copy the .kmz to the controller (microSD or USB), open Flight Route > Import Route (KMZ/KML) "
        "and pick the file. Check the aircraft, payload and every waypoint height on the map before uploading.",
        "FlightHub 2: Wayline Library > Import, then assign the route to the device.",
        "DJI Fly (no import function): create and save a placeholder waypoint mission on the controller, then "
        "replace its .kmz (Android/data/dji.go.v5/files/waypoint/<id>/<id>.kmz) with the exported file, keeping "
        "the placeholder's file name. Reopen the mission and review it before flying.",
        "Take off from the planned takeoff point: all heights in the file are relative to the launch position.",
    )

    def check(self, ctx: ExportContext, routes: list[Route]):
        issues = []
        drone, camera = ctx.drone, ctx.camera_profile
        wpml = (drone.get("mission") or {}).get("wpml")
        if not wpml or wpml.get("drone_enum") is None:
            issues.append(issue_error(
                "WPML_NO_AIRCRAFT_ID", "export",
                f"No DJI WPML aircraft id is known for the {drone.get('label')}; a route file without it is "
                "rejected by DJI's apps. For a custom profile, enter the id on the drone profile."))
        elif wpml.get("source") != "dji-docs":
            issues.append(info(
                "WPML_AIRCRAFT_ID_UNOFFICIAL", "export",
                f"The aircraft id written for the {drone.get('label')} ({wpml['drone_enum']}) is not in DJI's "
                "public WPML tables; it comes from route files reported by users. If the app refuses the file, "
                "plan the area in the app from the boundary KML instead.", basis="note"))
        cam_wpml = camera.get("wpml")
        if cam_wpml and cam_wpml.get("source") != "dji-docs":
            issues.append(info(
                "WPML_PAYLOAD_ID_UNOFFICIAL", "export",
                f"The payload id written for the {camera.get('label')} ({cam_wpml['payload_enum']}) is not in "
                "DJI's public WPML tables. Confirm the payload shown after import.", basis="note"))
        if ctx.takeoff.get("assumed"):
            issues.append(warning(
                "WPML_TAKEOFF_ASSUMED", "export",
                "Heights in the file are relative to the launch position, and no takeoff point is set in the "
                "plan. Launch at the first waypoint's elevation or set the takeoff point and export again.", basis="note"))
        else:
            issues.append(info(
                "WPML_RELATIVE_HEIGHTS", "export",
                "Heights in the file are relative to the launch position. Take off from the planned takeoff "
                "point (or the same elevation); launching higher or lower shifts the whole route by that much.", basis="note"))
        if ctx.trigger == "manual":
            issues.append(warning(
                "TRIGGER_MANUAL", "export",
                f"The file contains no photo trigger. Before starting the mission, set the camera to interval "
                f"shooting every {max(1, int(ctx.camera['photo_interval_s']))} s or less "
                f"(the plan needs a photo every {ctx.camera['photo_interval_s']:g} s at {ctx.speed:g} m/s).", basis="note"))
        if ctx.trigger == "waypoint":
            stops = sum(1 for r in routes for p in r.points if p.take_photo)
            issues.append(info(
                "TRIGGER_STOP_AND_SHOOT", "export",
                f"The aircraft stops for each of the {stops} photos, adding roughly "
                f"{stops * STOP_AND_SHOOT_S / 60:.0f} min to the estimated flight time; more batteries may be needed.", basis="note"))
        low = min((p.alt for r in routes for p in r.points), default=0.0)
        if ctx.transit_speed > MAX_TRANSIT_SPEED:
            issues.append(info(
                "WPML_TRANSIT_SPEED", "export",
                f"WPML limits the speed to the first waypoint to {MAX_TRANSIT_SPEED:g} m/s; the file uses that.", basis="note"))
        if low < 0:
            issues.append(warning(
                "WPML_NEGATIVE_HEIGHT", "export",
                "Some waypoints are below the launch position. DJI apps may refuse negative heights.", basis="note"))
        return issues

    # -- rendering ---------------------------------------------------------------

    def render(self, ctx: ExportContext, route: Route) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(_entry("wpmz/template.kml"), self._template(ctx, route))
            z.writestr(_entry("wpmz/waylines.wpml"), self._waylines(ctx, route))
        return buf.getvalue()

    def _mission_config(self, ctx: ExportContext, *, template: bool) -> str:
        drone = ctx.drone
        wpml = (drone.get("mission") or {}).get("wpml") or {}
        cam_wpml = ctx.camera_profile.get("wpml") or {}
        take = ctx.takeoff
        lines = [
            "    <wpml:missionConfig>",
            "      <wpml:flyToWaylineMode>safely</wpml:flyToWaylineMode>",
            f"      <wpml:finishAction>{FINISH.get(ctx.params.get('finish_action'), 'goHome')}</wpml:finishAction>",
            "      <wpml:exitOnRCLost>executeLostAction</wpml:exitOnRCLost>",
            "      <wpml:executeRCLostAction>goBack</wpml:executeRCLostAction>",
            f"      <wpml:takeOffSecurityHeight>{fmt(_takeoff_height(ctx))}</wpml:takeOffSecurityHeight>",
        ]
        if template and not take.get("assumed"):
            ground = take.get("ground_m")
            lines.append(f"      <wpml:takeOffRefPoint>{fmt(take['lat'], 7)},{fmt(take['lng'], 7)},"
                         f"{fmt(ground or 0.0, 1)}</wpml:takeOffRefPoint>")
            lines.append("      <wpml:takeOffRefPointAGLHeight>0</wpml:takeOffRefPointAGLHeight>")
        lines += [
            f"      <wpml:globalTransitionalSpeed>{fmt(min(ctx.transit_speed, MAX_TRANSIT_SPEED))}</wpml:globalTransitionalSpeed>",
            f"      <wpml:globalRTHHeight>{fmt(max(ctx.rth_altitude, MIN_TAKEOFF_HEIGHT), 1)}</wpml:globalRTHHeight>",
            "      <wpml:droneInfo>",
            f"        <wpml:droneEnumValue>{int(wpml.get('drone_enum', 0))}</wpml:droneEnumValue>",
            f"        <wpml:droneSubEnumValue>{int(wpml.get('drone_sub_enum', 0))}</wpml:droneSubEnumValue>",
            "      </wpml:droneInfo>",
        ]
        if cam_wpml.get("payload_enum") is not None:
            lines += [
                "      <wpml:payloadInfo>",
                f"        <wpml:payloadEnumValue>{int(cam_wpml['payload_enum'])}</wpml:payloadEnumValue>",
                "        <wpml:payloadPositionIndex>0</wpml:payloadPositionIndex>",
                "      </wpml:payloadInfo>",
            ]
        lines.append("    </wpml:missionConfig>")
        return "\n".join(lines)

    def _template(self, ctx: ExportContext, route: Route) -> str:
        out = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f"<kml {NS}>",
            "  <Document>",
            f"    <wpml:author>{xml_escape(ctx.author)}</wpml:author>",
            f"    <wpml:createTime>{int(ctx.created_ms)}</wpml:createTime>",
            f"    <wpml:updateTime>{int(ctx.created_ms)}</wpml:updateTime>",
            self._mission_config(ctx, template=True),
            "    <Folder>",
            "      <wpml:templateType>waypoint</wpml:templateType>",
            "      <wpml:templateId>0</wpml:templateId>",
            "      <wpml:waylineCoordinateSysParam>",
            "        <wpml:coordinateMode>WGS84</wpml:coordinateMode>",
            "        <wpml:heightMode>relativeToStartPoint</wpml:heightMode>",
            "        <wpml:positioningType>GPS</wpml:positioningType>",
            "      </wpml:waylineCoordinateSysParam>",
            f"      <wpml:autoFlightSpeed>{fmt(ctx.speed)}</wpml:autoFlightSpeed>",
            f"      <wpml:globalHeight>{fmt(ctx.camera['altitude_m'], 1)}</wpml:globalHeight>",
            "      <wpml:gimbalPitchMode>usePointSetting</wpml:gimbalPitchMode>",
            "      <wpml:globalWaypointHeadingParam>",
            "        <wpml:waypointHeadingMode>followWayline</wpml:waypointHeadingMode>",
            "        <wpml:waypointHeadingAngle>0</wpml:waypointHeadingAngle>",
            "        <wpml:waypointPoiPoint>0.000000,0.000000,0.000000</wpml:waypointPoiPoint>",
            "        <wpml:waypointHeadingPathMode>followBadArc</wpml:waypointHeadingPathMode>",
            "      </wpml:globalWaypointHeadingParam>",
            f"      <wpml:globalWaypointTurnMode>{STOP_TURN}</wpml:globalWaypointTurnMode>",
        ]
        groups = _action_groups(ctx, route)
        pitch = fmt(ctx.camera.get("gimbal_pitch_deg", -90), 1)
        for i, p in enumerate(route.points):
            out += [
                "      <Placemark>",
                "        <Point>",
                f"          <coordinates>{fmt(p.lng, 7)},{fmt(p.lat, 7)}</coordinates>",
                "        </Point>",
                f"        <wpml:index>{i}</wpml:index>",
                f"        <wpml:ellipsoidHeight>{fmt(p.alt, 1)}</wpml:ellipsoidHeight>",
                f"        <wpml:height>{fmt(p.alt, 1)}</wpml:height>",
                "        <wpml:useGlobalHeight>0</wpml:useGlobalHeight>",
                "        <wpml:useGlobalSpeed>1</wpml:useGlobalSpeed>",
                "        <wpml:useGlobalHeadingParam>1</wpml:useGlobalHeadingParam>",
            ]
            if p.stop:
                out.append("        <wpml:useGlobalTurnParam>1</wpml:useGlobalTurnParam>")
            else:
                out += [
                    "        <wpml:useGlobalTurnParam>0</wpml:useGlobalTurnParam>",
                    "        <wpml:waypointTurnParam>",
                    f"          <wpml:waypointTurnMode>{PASS_TURN}</wpml:waypointTurnMode>",
                    f"          <wpml:waypointTurnDampingDist>{fmt(_damping(route, i))}</wpml:waypointTurnDampingDist>",
                    "        </wpml:waypointTurnParam>",
                    "        <wpml:useStraightLine>1</wpml:useStraightLine>",
                ]
            out.append(f"        <wpml:gimbalPitchAngle>{pitch}</wpml:gimbalPitchAngle>")
            out += groups.get(i, [])
            out.append("      </Placemark>")
        out += ["    </Folder>", "  </Document>", "</kml>", ""]
        return "\n".join(out)

    def _waylines(self, ctx: ExportContext, route: Route) -> str:
        out = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f"<kml {NS}>",
            "  <Document>",
            self._mission_config(ctx, template=False),
            "    <Folder>",
            "      <wpml:templateId>0</wpml:templateId>",
            "      <wpml:executeHeightMode>relativeToStartPoint</wpml:executeHeightMode>",
            "      <wpml:waylineId>0</wpml:waylineId>",
            f"      <wpml:autoFlightSpeed>{fmt(ctx.speed)}</wpml:autoFlightSpeed>",
        ]
        groups = _action_groups(ctx, route)
        for i, p in enumerate(route.points):
            out += [
                "      <Placemark>",
                "        <Point>",
                f"          <coordinates>{fmt(p.lng, 7)},{fmt(p.lat, 7)}</coordinates>",
                "        </Point>",
                f"        <wpml:index>{i}</wpml:index>",
                f"        <wpml:executeHeight>{fmt(p.alt, 1)}</wpml:executeHeight>",
                f"        <wpml:waypointSpeed>{fmt(ctx.speed)}</wpml:waypointSpeed>",
                "        <wpml:waypointHeadingParam>",
                "          <wpml:waypointHeadingMode>followWayline</wpml:waypointHeadingMode>",
                "          <wpml:waypointHeadingAngle>0</wpml:waypointHeadingAngle>",
                "          <wpml:waypointPoiPoint>0.000000,0.000000,0.000000</wpml:waypointPoiPoint>",
                "          <wpml:waypointHeadingPathMode>followBadArc</wpml:waypointHeadingPathMode>",
                "        </wpml:waypointHeadingParam>",
                "        <wpml:waypointTurnParam>",
                f"          <wpml:waypointTurnMode>{STOP_TURN if p.stop else PASS_TURN}</wpml:waypointTurnMode>",
                f"          <wpml:waypointTurnDampingDist>{'0' if p.stop else fmt(_damping(route, i))}</wpml:waypointTurnDampingDist>",
                "        </wpml:waypointTurnParam>",
            ]
            if not p.stop:
                out.append("        <wpml:useStraightLine>1</wpml:useStraightLine>")
            out += groups.get(i, [])
            out.append("      </Placemark>")
        out += ["    </Folder>", "  </Document>", "</kml>", ""]
        return "\n".join(out)


def _entry(name: str) -> zipfile.ZipInfo:
    # Fixed timestamp: the same plan always exports byte-identical files.
    zi = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
    zi.compress_type = zipfile.ZIP_DEFLATED
    zi.external_attr = 0o644 << 16
    return zi


def _takeoff_height(ctx: ExportContext) -> float:
    return min(MAX_TAKEOFF_HEIGHT, max(MIN_TAKEOFF_HEIGHT, DEFAULT_TAKEOFF_HEIGHT))


def _damping(route: Route, i: int) -> float:
    """Turn damping for a fly-through waypoint: well inside both neighbouring segments."""
    pts = route.points
    before = _ground_distance(pts[i - 1], pts[i]) if i > 0 else 10.0
    after = _ground_distance(pts[i], pts[i + 1]) if i + 1 < len(pts) else 10.0
    return max(0.2, min(5.0, min(before, after) / 4.0))


def _action(action_id: int, func: str, params: list[tuple[str, str]]) -> list[str]:
    out = [
        "          <wpml:action>",
        f"            <wpml:actionId>{action_id}</wpml:actionId>",
        f"            <wpml:actionActuatorFunc>{func}</wpml:actionActuatorFunc>",
        "            <wpml:actionActuatorFuncParam>",
    ]
    out += [f"              <wpml:{k}>{v}</wpml:{k}>" for k, v in params]
    out += ["            </wpml:actionActuatorFuncParam>", "          </wpml:action>"]
    return out


def _group(group_id: int, start: int, end: int, trigger: str, param: str | None, actions: list[str]) -> list[str]:
    out = [
        "        <wpml:actionGroup>",
        f"          <wpml:actionGroupId>{group_id}</wpml:actionGroupId>",
        f"          <wpml:actionGroupStartIndex>{start}</wpml:actionGroupStartIndex>",
        f"          <wpml:actionGroupEndIndex>{end}</wpml:actionGroupEndIndex>",
        "          <wpml:actionGroupMode>sequence</wpml:actionGroupMode>",
        "          <wpml:actionTrigger>",
        f"            <wpml:actionTriggerType>{trigger}</wpml:actionTriggerType>",
    ]
    if param is not None:
        out.append(f"            <wpml:actionTriggerParam>{param}</wpml:actionTriggerParam>")
    out.append("          </wpml:actionTrigger>")
    out += actions
    out.append("        </wpml:actionGroup>")
    return out


def _action_groups(ctx: ExportContext, route: Route) -> dict[int, list[str]]:
    """Action groups keyed by the waypoint index they are attached to."""
    groups: dict[int, list[str]] = {}
    gid = 0
    pitch = fmt(ctx.camera.get("gimbal_pitch_deg", -90), 1)
    gimbal = _action(0, "gimbalRotate", [
        ("gimbalHeadingYawBase", "north"),
        ("gimbalRotateMode", "absoluteAngle"),
        ("gimbalPitchRotateEnable", "1"), ("gimbalPitchRotateAngle", pitch),
        ("gimbalRollRotateEnable", "0"), ("gimbalRollRotateAngle", "0"),
        ("gimbalYawRotateEnable", "0"), ("gimbalYawRotateAngle", "0"),
        ("gimbalRotateTimeEnable", "0"), ("gimbalRotateTime", "0"),
        ("payloadPositionIndex", "0"),
    ])
    groups.setdefault(0, []).extend(_group(gid, 0, 0, "reachPoint", None, gimbal))
    gid += 1
    photo = _action(0, "takePhoto", [
        ("fileSuffix", "survey"), ("payloadPositionIndex", "0"), ("useGlobalPayloadLensIndex", "1"),
    ])

    pts = route.points
    if ctx.trigger in ("distance", "time"):
        kind = "multipleDistance" if ctx.trigger == "distance" else "multipleTiming"
        value = fmt(ctx.camera["photo_spacing_m"] if ctx.trigger == "distance" else ctx.camera["photo_interval_s"])
        i = 0
        while i < len(pts):
            if not pts[i].trigger_on:
                i += 1
                continue
            j = i
            while j + 1 < len(pts) and pts[j].trigger_on and pts[j + 1].leg == pts[i].leg:
                j += 1
            if j > i:
                groups.setdefault(i, []).extend(_group(gid, i, j, kind, value, photo))
                gid += 1
            i = j if j > i else i + 1
    elif ctx.trigger == "waypoint":
        for i, p in enumerate(pts):
            if p.take_photo:
                groups.setdefault(i, []).extend(_group(gid, i, i, "reachPoint", None, photo))
                gid += 1
    return groups
