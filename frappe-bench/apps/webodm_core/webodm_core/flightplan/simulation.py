"""Simulation support: the planner's elevation grid, shaped for the flight simulator.

The simulator (``webodm_frontend`` ``src/lib/flightsim``) replays a mission
that ``plan_mission`` has already computed. It plans nothing and stores
nothing: the mission stays the planner's, and the only thing the simulator
needs that is not already in the mission is the ground under the route
*away from* the waypoints - the transit, the turns, the terrain drawn in 3D.

That ground has to be the very grid the planner interpolated from, or the
simulated height above ground would disagree with the plan's own
``agl`` / ``clearance`` figures. So this module does one thing: turn a
``TerrainModel`` into a payload a client can interpolate from with the same
bilinear rule (``TerrainModel.elevation``). Nodes are not resampled,
smoothed or decimated; the grid is at most the planner's own budget.

No Frappe dependency.
"""

from __future__ import annotations

from .terrain import TerrainModel

# Centimetres are far below what any elevation source resolves, and keep the
# payload of a 10 000 node grid around 70 kB.
ELEVATION_DECIMALS = 2

DATUM_NOTE = (
    "Elevations are in the datum of the elevation source. The simulator uses differences between them "
    "(ground under the aircraft minus ground at the takeoff point), so the datum cancels."
)


def terrain_payload(model: TerrainModel) -> dict:
    """``model`` as plain JSON: bounds, shape, rows south to north, source and what is missing.

    ``values`` keeps ``None`` for nodes without data, exactly as the planner
    sees them, so a client reproduces "unknown" instead of inventing ground.
    """
    values = [[None if v is None else round(float(v), ELEVATION_DECIMALS) for v in row] for row in model.values]
    span = model.range()
    return {
        "south": model.south, "west": model.west, "north": model.north, "east": model.east,
        "rows": model.rows, "cols": model.cols,
        "values": values,
        "spacing_m": round(float(model.spacing_m), 2),
        "min_m": None if span is None else round(span[0], ELEVATION_DECIMALS),
        "max_m": None if span is None else round(span[1], ELEVATION_DECIMALS),
        "coverage": round(model.coverage, 4),
        "source": model.source.to_dict(),
        "datum_note": DATUM_NOTE,
    }
