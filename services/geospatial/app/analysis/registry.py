"""Analysis operation registry.

An analysis operation is a named, versioned transformation of task outputs
(orthophoto / DSM / DTM / point cloud) into a new artifact. Operations are
defined in this service so the algorithm and the schema describing its
parameters live together; Frappe syncs the catalog and drives execution.

An op declares:

- ``op_id``       stable identifier used in URLs and persisted catalog rows
- ``label``       human-readable name
- ``description`` short explanation for the plugin list
- ``version``     semantic version of the operation
- ``params_model`` pydantic model validating the op's parameters and yielding
  a JSON Schema for the frontend form
- ``output_kind`` ``"raster"`` or ``"vector"``
- ``render_kind`` how the output should be rendered on the map
- ``inputs``      required inputs, each ``{"name": ..., "datasets": [...]}`` naming
  the task datasets that can supply it (first available wins)
- ``handler``     ``handler(inputs, params, output_path) -> dict``

The handler receives resolved absolute input paths, a validated params
instance, and an absolute ``output_path`` to write, and returns a dict with
``output_path`` and optional ``geojson`` / ``metadata``.
"""

from dataclasses import dataclass
from typing import Callable, Literal

from pydantic import BaseModel

OutputKind = Literal["raster", "vector"]

# Bumped when the shape of the catalog response changes, so consumers can detect
# an incompatible contract instead of silently dropping operations.
CATALOG_SCHEMA_VERSION = 2

# Handler signature: (input name -> absolute path, validated params, output path).
AnalysisHandler = Callable[[dict[str, str], BaseModel, str], dict]

# Optional pre-run validator: raises ValueError when the operation cannot run.
AnalysisValidator = Callable[[BaseModel], None]


@dataclass(frozen=True)
class AnalysisOp:
    op_id: str
    label: str
    description: str
    version: str
    params_model: type[BaseModel]
    output_kind: OutputKind
    render_kind: str
    inputs: list[dict]
    handler: AnalysisHandler
    timeout_seconds: int | None = None
    validator: AnalysisValidator | None = None

    def catalog_entry(self) -> dict:
        """JSON-serializable catalog description of this operation."""
        return {
            "op_id": self.op_id,
            "label": self.label,
            "description": self.description,
            "version": self.version,
            "params_schema": self.params_model.model_json_schema(),
            "output_kind": self.output_kind,
            "render_kind": self.render_kind,
            "inputs": self.inputs,
            "timeout_seconds": self.timeout_seconds,
            "needs_validation": self.validator is not None,
        }


_REGISTRY: dict[str, AnalysisOp] = {}


def register(op: AnalysisOp) -> AnalysisOp:
    """Register an operation. Re-registering the same ``op_id`` is an error."""
    if op.op_id in _REGISTRY:
        raise ValueError(f"analysis op already registered: {op.op_id}")
    _REGISTRY[op.op_id] = op
    return op


def get_op(op_id: str) -> AnalysisOp | None:
    return _REGISTRY.get(op_id)


def all_ops() -> list[AnalysisOp]:
    return [_REGISTRY[key] for key in sorted(_REGISTRY)]


def catalog() -> dict:
    """The full catalog response returned by ``GET /analysis``."""
    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "operations": [op.catalog_entry() for op in all_ops()],
    }
