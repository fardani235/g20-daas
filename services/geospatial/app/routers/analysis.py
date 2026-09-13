"""Analysis plugin API: catalog listing and operation execution.

The service stays stateless: the caller (Frappe) resolves input paths to
absolute shared-storage paths, provides an absolute ``output_path``, and stores
the result. This router validates and executes; it holds no job state.
"""

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError

from app.analysis import catalog, get_op

router = APIRouter()


class AnalysisRunRequest(BaseModel):
    inputs: dict[str, str]
    params: dict = {}
    output_path: str


class AnalysisValidateRequest(BaseModel):
    params: dict = {}


@router.get("")
async def list_analysis():
    """Return the registered analysis catalog."""
    return catalog()


@router.post("/{op_id}/validate")
async def validate_analysis(op_id: str, req: AnalysisValidateRequest):
    """Validate params (and any op-specific preconditions) without running.

    Lets the caller reject an impossible run before creating it — e.g. a
    detection model that is missing, unreadable, or label-mismatched.
    """
    op = get_op(op_id)
    if op is None:
        raise HTTPException(status_code=404, detail=f"unknown analysis op: {op_id}")

    try:
        params = op.params_model(**req.params)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"invalid parameters: {e}")

    if op.validator is not None:
        try:
            op.validator(params)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    return {"ok": True}


@router.post("/{op_id}/run")
async def run_analysis(op_id: str, req: AnalysisRunRequest):
    """Validate and execute one analysis operation synchronously."""
    op = get_op(op_id)
    if op is None:
        raise HTTPException(status_code=404, detail=f"unknown analysis op: {op_id}")

    if not os.path.isabs(req.output_path):
        raise HTTPException(status_code=400, detail="output_path must be absolute")

    for name, path in req.inputs.items():
        if not os.path.isabs(path):
            raise HTTPException(status_code=400, detail=f"input '{name}' must be absolute")
        if not os.path.isfile(path):
            raise HTTPException(status_code=404, detail=f"input '{name}' not found: {path}")

    try:
        params = op.params_model(**req.params)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"invalid parameters: {e}")

    try:
        result = op.handler(req.inputs, params, req.output_path)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"analysis failed: {e}")

    return {
        "op_id": op.op_id,
        "output_kind": op.output_kind,
        "render_kind": op.render_kind,
        **result,
    }
