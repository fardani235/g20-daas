"""Provisioner service: on-demand NodeODM instances behind one small HTTP API.

The Frappe app calls this over the internal network exactly the way it calls
the geospatial and plugin-runner services. This service owns everything
cloud-related — provider SDKs, provisioning credentials, machine images,
networking — so none of it exists in the app process. It keeps **no state**:
every call is answered from the provider's API plus a readiness probe, and
the app's ``WebODM Compute Instance`` records are the lifecycle of record.

API::

    GET    /health
    GET    /provider                 what is configured (enabled, classes, defaults)
    POST   /instances                {instance_class?, token, labels, max_lifetime_seconds?}
    GET    /instances                every instance this deployment created (for the sweep)
    GET    /instances/{handle}       state; "ready" once NodeODM answers (X-Node-Token optional)
    DELETE /instances/{handle}       terminate; idempotent

Only reachable inside the stack (compose network). ``PROVISIONER_API_TOKEN``
adds a bearer token on top; when set, every request must carry it.
"""

from __future__ import annotations

import logging
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.config import ConfigError, Settings
from app.core import Core, UnknownHandle
from app.providers import build_provider
from app.providers.base import ProviderError, ProviderUnavailable

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("provisioner")


class CreateInstanceRequest(BaseModel):
    instance_class: str | None = None
    token: str = Field(min_length=16, description="NodeODM bearer token to bake into the node")
    labels: dict[str, str] = {}
    max_lifetime_seconds: int | None = Field(default=None, ge=60)


def build_app(settings: Settings | None = None, provider=None, *, core: Core | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if core is None:
        if provider is None:
            provider = build_provider(settings)
        core = Core(settings, provider)

    app = FastAPI(title="WebODM Provisioner", description="On-demand NodeODM compute", version="0.1.0")
    app.state.core = core
    app.state.settings = settings

    def require_auth(authorization: str | None = Header(default=None)):
        token = settings.api_token
        if not token:
            return
        supplied = ""
        if authorization and authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
        if not supplied or not secrets.compare_digest(supplied, token):
            raise HTTPException(status_code=401, detail="missing or invalid provisioner token")

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "webodm-provisioner",
                "provider": core.provider.name if core.provider else None}

    @app.get("/provider", dependencies=[Depends(require_auth)])
    async def provider_info():
        return core.describe_provider()

    @app.post("/instances", status_code=201, dependencies=[Depends(require_auth)])
    async def create_instance(req: CreateInstanceRequest):
        try:
            return await run_in_threadpool(core.create, req.instance_class, req.token, req.labels,
                                           req.max_lifetime_seconds)
        except ProviderUnavailable as e:
            raise HTTPException(status_code=503, detail=str(e))
        except ProviderError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/instances", dependencies=[Depends(require_auth)])
    async def list_instances():
        try:
            return {"instances": await run_in_threadpool(core.list_managed)}
        except ProviderUnavailable as e:
            raise HTTPException(status_code=503, detail=str(e))
        except ProviderError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/instances/{handle}", dependencies=[Depends(require_auth)])
    async def describe_instance(handle: str, x_node_token: str | None = Header(default=None)):
        try:
            return await run_in_threadpool(core.describe, handle, x_node_token)
        except UnknownHandle as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ProviderUnavailable as e:
            raise HTTPException(status_code=503, detail=str(e))
        except ProviderError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.delete("/instances/{handle}", dependencies=[Depends(require_auth)])
    async def destroy_instance(handle: str):
        try:
            return await run_in_threadpool(core.destroy, handle)
        except UnknownHandle as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ProviderUnavailable as e:
            raise HTTPException(status_code=503, detail=str(e))
        except ProviderError as e:
            raise HTTPException(status_code=400, detail=str(e))

    return app


def _export_file_secrets():
    """Resolve ``AWS_*_FILE`` Docker secrets into the env boto3's default chain reads.

    Empty files are ignored so a deployment using an instance role can mount
    placeholder secrets.
    """
    import os

    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        path = os.environ.get(f"{var}_FILE")
        if not path or os.environ.get(var):
            continue
        try:
            with open(path) as fh:
                value = fh.read().strip()
        except OSError as e:
            raise ConfigError(f"{var}_FILE={path} is not readable: {e.__class__.__name__}") from None
        if value:
            os.environ[var] = value


def _startup_app() -> FastAPI:
    try:
        _export_file_secrets()
        return build_app()
    except ConfigError as e:
        # Fail loud at boot: a misconfigured provisioner must not sit there
        # silently accepting tasks it cannot place.
        log.error("configuration error: %s", e)
        raise


app = _startup_app()
