"""Flight planning: drone mapping missions for an area or a corridor.

The package is pure Python with no Frappe dependency, so it can be tested
and reused on its own. Layers, lowest first:

- ``geo`` / ``geometry``      local metric frame and planar geometry
- ``profiles`` + ``catalog/`` drone and camera data (built-in JSON, custom documents)
- ``photogrammetry``          GSD, footprint, spacing
- ``survey``                  flight-line layout (area grid, corridor passes)
- ``terrain`` / ``elevation`` elevation grid and the services that fill it
- ``mission``                 ``plan_mission``: layout + altitudes + estimates
- ``checks`` / ``issues``     capability, imagery, terrain and rule findings
- ``airspace``                advisory check against an operator's zone file
- ``exporters/``              mission file formats, one module each
- ``geocode``                 address / coordinate search
- ``simulation``              the elevation grid, shaped for the flight simulator (read-only)

``webodm_core.api.flightplan`` is the only place that knows about Frappe,
documents, permissions, caching and site configuration.
"""

from .issues import NOTICE
from .mission import PLANNER_VERSION, append_issue, plan_mission
from .params import PlanError

__all__ = ["NOTICE", "PLANNER_VERSION", "PlanError", "append_issue", "plan_mission"]
