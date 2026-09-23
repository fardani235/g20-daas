"""User plugin sandbox service.

Frappe stages a run (package zip, copies of the input files, an empty output
location) inside the shared sandbox volume and calls ``POST /run``; this service
executes the plugin in a limited subprocess and answers with the result. Like
the geospatial service it is stateless and holds no job state — the run record
lives in Frappe.

Every path in a request must be inside ``SANDBOX_DIR``: the container mounts
nothing else, and refusing anything outside it keeps a bug on the caller's side
from ever pointing a plugin at the wrong file.
"""

import asyncio
import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app import sandbox

MAX_CONCURRENT_RUNS = int(os.environ.get("PLUGIN_MAX_CONCURRENT_RUNS", 1))

app = FastAPI(
    title="WebODM Plugin Runner",
    description="Sandboxed execution of user analysis plugins",
    version="0.1.0",
)

_slots = asyncio.Semaphore(MAX_CONCURRENT_RUNS)


class RunRequest(BaseModel):
    package_path: str
    inputs: dict[str, str]
    params: dict = {}
    output_path: str
    output_kind: str = "raster"
    run_dir: str
    timeout_seconds: int | None = None
    # Free-form facts about the task (name, CRS, ODM options) forwarded to the
    # plugin in request.json; never interpreted here.
    context: dict = {}


def _inside_sandbox(path: str) -> bool:
    real = os.path.realpath(path)
    return real == sandbox.SANDBOX_DIR or real.startswith(sandbox.SANDBOX_DIR + os.sep)


def _check_paths(req: RunRequest):
    paths = {"package_path": req.package_path, "output_path": req.output_path, "run_dir": req.run_dir}
    paths.update({f"input '{k}'": v for k, v in req.inputs.items()})
    for label, path in paths.items():
        if not os.path.isabs(path):
            raise HTTPException(status_code=400, detail=f"{label} must be absolute")
        if not _inside_sandbox(path):
            raise HTTPException(status_code=400, detail=f"{label} is outside the sandbox")
    if not os.path.isdir(req.run_dir):
        raise HTTPException(status_code=400, detail="run_dir does not exist")
    if not os.path.isfile(req.package_path):
        raise HTTPException(status_code=404, detail="package not found")
    if req.output_kind not in sandbox.OUTPUT_KINDS:
        raise HTTPException(status_code=400, detail=f"output_kind must be one of {', '.join(sandbox.OUTPUT_KINDS)}")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "webodm-plugin-runner",
        "sandbox_dir": sandbox.SANDBOX_DIR,
        "max_concurrent_runs": MAX_CONCURRENT_RUNS,
    }


@app.post("/run")
async def run(req: RunRequest):
    """Execute one staged plugin run synchronously."""
    _check_paths(req)
    async with _slots:
        try:
            # The subprocess wait is blocking; keep it off the event loop so
            # health checks keep answering while a plugin runs.
            result = await run_in_threadpool(
                sandbox.run_package,
                req.package_path, req.inputs, req.params, req.output_path, req.run_dir,
                output_kind=req.output_kind, timeout_seconds=req.timeout_seconds,
                context=req.context,
            )
        except sandbox.PluginError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except sandbox.SandboxError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"sandbox failure: {e}")
    return result
