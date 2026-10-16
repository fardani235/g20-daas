"""MAVLink missions: QGroundControl ``.plan`` and the plain-text ``QGC WPL 110`` list.

Both carry the same command sequence, built once in ``mission_items``:

    takeoff -> set speed -> point gimbal down
    per flight line:  waypoint (line start) -> camera trigger on
                      [waypoints along the line] -> waypoint (line end) -> camera trigger off
    return to launch / land

Altitudes use ``MAV_FRAME_GLOBAL_RELATIVE_ALT`` (relative to home), which is
how the planner computes them; a terrain-following plan carries its own
per-waypoint heights rather than relying on the autopilot's terrain
database. Command numbers are MAVLink ``MAV_CMD`` ids.

PX4 and ArduPilot differ in which commands they honour (camera trigger
setup in particular depends on the vehicle's camera configuration), so the
file is a correct mission, not a guarantee the vehicle is set up to shoot.
"""

from __future__ import annotations

import json

from ..issues import info, warning
from .base import ExportContext, Exporter, Route, fmt

# MAV_CMD
NAV_WAYPOINT = 16
NAV_RETURN_TO_LAUNCH = 20
NAV_LAND = 21
NAV_TAKEOFF = 22
DO_CHANGE_SPEED = 178
DO_MOUNT_CONTROL = 205
DO_SET_CAM_TRIGG_DIST = 206
IMAGE_START_CAPTURE = 2000
IMAGE_STOP_CAPTURE = 2001
# MAV_FRAME
FRAME_GLOBAL = 0
FRAME_MISSION = 2
FRAME_GLOBAL_RELATIVE_ALT = 3
# MAV_MOUNT_MODE
MOUNT_MAVLINK_TARGETING = 2
# MAV_AUTOPILOT / MAV_TYPE
AUTOPILOT = {"px4": 12, "ardupilot": 3}
VEHICLE = {"multirotor": 2, "fixed-wing": 1, "vtol": 22}

MIN_TAKEOFF_ALT = 10.0


def mission_items(ctx: ExportContext, route: Route) -> list[dict]:
    """The command list for one route: ``{"command", "frame", "params": [7]}`` dicts."""
    pts = route.points
    first = pts[0]
    items: list[dict] = []

    def add(command, frame, *params):
        p = list(params) + [0] * (7 - len(params))
        items.append({"command": command, "frame": frame, "params": p})

    takeoff_alt = max(MIN_TAKEOFF_ALT, first.alt)
    add(NAV_TAKEOFF, FRAME_GLOBAL_RELATIVE_ALT, 0, 0, 0, None, first.lat, first.lng, takeoff_alt)
    # Speed type 1 = ground speed; throttle -1 = no change.
    add(DO_CHANGE_SPEED, FRAME_MISSION, 1, ctx.speed, -1, 0, 0, 0, 0)
    add(DO_MOUNT_CONTROL, FRAME_MISSION, ctx.camera.get("gimbal_pitch_deg", -90), 0, 0, 0, 0, 0,
        MOUNT_MAVLINK_TARGETING)

    triggering = False

    def trigger(on: bool):
        nonlocal triggering
        if on == triggering:
            return
        triggering = on
        if ctx.trigger == "distance":
            # param3 = 1: also trigger once immediately.
            add(DO_SET_CAM_TRIGG_DIST, FRAME_MISSION, ctx.camera["photo_spacing_m"] if on else 0, 0, 1 if on else 0)
        elif ctx.trigger == "time":
            if on:
                # param2 interval (s), param3 0 = capture until stopped.
                add(IMAGE_START_CAPTURE, FRAME_MISSION, 0, ctx.camera["photo_interval_s"], 0, 0)
            else:
                add(IMAGE_STOP_CAPTURE, FRAME_MISSION, 0)

    for p in pts:
        add(NAV_WAYPOINT, FRAME_GLOBAL_RELATIVE_ALT, 0, 0, 0, None, p.lat, p.lng, p.alt)
        if ctx.trigger in ("distance", "time"):
            trigger(p.trigger_on)
    trigger(False)

    action = ctx.params.get("finish_action")
    if action == "land":
        last = pts[-1]
        add(NAV_LAND, FRAME_GLOBAL_RELATIVE_ALT, 0, 0, 0, None, last.lat, last.lng, 0)
    elif action != "hover":
        add(NAV_RETURN_TO_LAUNCH, FRAME_MISSION)
    return items


def _checks(ctx: ExportContext, app: str):
    issues = []
    if ctx.takeoff.get("assumed"):
        issues.append(warning(
            "MAV_TAKEOFF_ASSUMED", "export",
            "Altitudes are relative to the home position, and no takeoff point is set in the plan. The first "
            "waypoint is used as the planned home; set the takeoff point and export again for a correct home.", basis="note"))
    issues.append(info(
        "MAV_RELATIVE_ALT", "export",
        "Altitudes are relative to the home position (where the vehicle arms). Arming on higher or lower ground "
        "than the planned takeoff point shifts the whole mission by that much.", basis="note"))
    if ctx.trigger == "distance":
        issues.append(info(
            "MAV_CAMERA_TRIGGER", "export",
            f"Photos are requested every {ctx.camera['photo_spacing_m']:g} m with MAV_CMD_DO_SET_CAM_TRIGG_DIST. "
            "The vehicle must have its camera trigger configured (PX4: TRIG_* parameters or a MAVLink camera; "
            f"ArduPilot: CAM_* parameters); confirm in {app} that the camera fires.", basis="note"))
    if ctx.params.get("finish_action") == "hover":
        issues.append(info(
            "MAV_NO_RTL", "export",
            "The mission ends at the last waypoint without a return command; what the vehicle does next depends "
            "on its own configuration.", basis="note"))
    return issues


class QgcPlan(Exporter):
    id = "qgc-plan"
    label = "QGroundControl plan (.plan)"
    extension = "plan"
    mime = "application/json"
    kind = "mission"
    description = "Mission plan for PX4 and ArduPilot vehicles, opened in QGroundControl and its derivatives."
    triggers = ("distance", "time", "manual")
    instructions = (
        "QGroundControl: Plan view > File > Open, choose the .plan file, review it, then Upload to the vehicle.",
        "Check that the firmware and vehicle type shown match your vehicle; the file states the planned ones.",
        "Arm at the planned takeoff point: altitudes are relative to the home position.",
    )

    def check(self, ctx, routes):
        return _checks(ctx, "QGroundControl")

    def render(self, ctx: ExportContext, route: Route) -> bytes:
        items = []
        for n, it in enumerate(mission_items(ctx, route), start=1):
            item = {
                "autoContinue": True,
                "command": it["command"],
                "doJumpId": n,
                "frame": it["frame"],
                "params": it["params"],
                "type": "SimpleItem",
            }
            # QGroundControl expects the three altitude keys together, and
            # only on items that have a position.
            if it["frame"] == FRAME_GLOBAL_RELATIVE_ALT:
                item.update({"AMSLAltAboveTerrain": None, "Altitude": it["params"][6], "AltitudeMode": 1})
            items.append(item)
        take = ctx.takeoff
        firmware = str(ctx.options.get("firmware") or "px4").lower()
        plan = {
            "fileType": "Plan",
            "geoFence": {"circles": [], "polygons": [], "version": 2},
            "groundStation": "QGroundControl",
            "mission": {
                "cruiseSpeed": ctx.speed,
                "firmwareType": AUTOPILOT.get(firmware, AUTOPILOT["px4"]),
                "globalPlanAltitudeMode": 1,
                "hoverSpeed": ctx.speed,
                "items": items,
                "plannedHomePosition": [take["lat"], take["lng"], take.get("ground_m") or 0],
                "vehicleType": VEHICLE.get(ctx.drone.get("airframe"), VEHICLE["multirotor"]),
                "version": 2,
            },
            "rallyPoints": {"points": [], "version": 2},
            "version": 1,
        }
        return (json.dumps(plan, indent=4) + "\n").encode()


class MavlinkWpl(Exporter):
    id = "mavlink-wpl"
    label = "MAVLink waypoint list (.waypoints)"
    extension = "waypoints"
    mime = "text/plain"
    kind = "mission"
    description = ("Plain-text mission (QGC WPL 110) read by Mission Planner, QGroundControl, MAVProxy and "
                   "other MAVLink tools.")
    triggers = ("distance", "time", "manual")
    instructions = (
        "Mission Planner: Plan screen > Load File, choose the .waypoints file, review it, then Write.",
        "QGroundControl: Plan view > File > Open also reads this format.",
        "Arm at the planned takeoff point: altitudes are relative to the home position.",
    )

    def check(self, ctx, routes):
        return _checks(ctx, "your ground station")

    def render(self, ctx: ExportContext, route: Route) -> bytes:
        take = ctx.takeoff
        lines = ["QGC WPL 110"]
        # Row 0 is the home position (absolute altitude), marked current.
        lines.append("\t".join([
            "0", "1", str(FRAME_GLOBAL), str(NAV_WAYPOINT), "0", "0", "0", "0",
            fmt(take["lat"], 7), fmt(take["lng"], 7), fmt(take.get("ground_m") or 0, 1), "1",
        ]))
        for n, it in enumerate(mission_items(ctx, route), start=1):
            p = [0 if v is None else v for v in it["params"]]
            lines.append("\t".join([
                str(n), "0", str(it["frame"]), str(it["command"]),
                fmt(p[0], 3), fmt(p[1], 3), fmt(p[2], 3), fmt(p[3], 3),
                fmt(p[4], 7), fmt(p[5], 7), fmt(p[6], 2), "1",
            ]))
        return ("\n".join(lines) + "\n").encode()
