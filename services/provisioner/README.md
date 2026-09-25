# WebODM Provisioner

On-demand NodeODM compute behind one small internal HTTP API. The Frappe app
asks this service for a processing node when a task starts, polls until
NodeODM answers, dispatches the task to it, and asks for it to be destroyed
when the task ends. Everything cloud-related — provider SDKs, provisioning
credentials, machine images, bootstrap, networking — lives here and nowhere
else in the stack.

The service is **stateless**: every answer is derived from the provider's API
plus a readiness probe. The app's `WebODM Compute Instance` records are the
lifecycle of record.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | liveness (no auth) |
| `GET` | `/provider` | `{enabled, provider, classes, default_class, nodeodm_port, max_lifetime_seconds}` |
| `POST` | `/instances` | `{instance_class?, token, labels, max_lifetime_seconds?}` → `{handle, status: "pending", hostname, port, hourly_cost, …}` |
| `GET` | `/instances` | every instance this deployment created (by tag), for the app's orphan sweep |
| `GET` | `/instances/{handle}` | `{status: pending\|ready\|terminated\|failed, hostname, port, launched_at, detail}`; `ready` only once NodeODM answers `/info` (send `X-Node-Token` to require an authenticated answer) |
| `DELETE` | `/instances/{handle}` | terminate; idempotent |

Handles are `<provider>:<provider id>` (`aws:i-0abc…`). With
`PROVISIONER_API_TOKEN` set, every route except `/health` requires
`Authorization: Bearer <token>`.

## Providers

`app/providers/base.py` is the whole contract: `create`, `describe`,
`destroy`, `list_managed`, plus the classes offered. Nothing above it knows
which cloud produced a node.

| Name | Module | Notes |
|---|---|---|
| `aws` | `providers/aws.py` | EC2 on-demand instances, CPU classes. Tags `webodm:managed`, `webodm:deployment`, `webodm:task`, `webodm:org`, `webodm:class`. `InstanceInitiatedShutdownBehavior=terminate`; the bootstrap self-schedules `shutdown -h` at lifetime + 10 min. Every setting is explicit (`AwsSettings.from_env`). |
| `fixed` | `providers/fixed.py` | Development only: "provisions" one configured endpoint (`PROVISIONER_FIXED_ENDPOINT`). Lets the app's whole lifecycle run against the compose `nodeodm` container. |
| `none` | — | Disabled: `/provider` reports `enabled: false`, the app falls back to its static node. |

Adding a provider (vast.ai next): implement the ABC, register it in
`app/providers/__init__.py`, document its env. No app change.

## Node bootstrap

`app/bootstrap/nodeodm-userdata.sh` is rendered per instance with the token,
port, image, lifetime and extra NodeODM args. It works on a prebaked AMI
(`infra/aws/bake-nodeodm-ami.sh`: Docker + image already present, boot ~1 min)
and on a stock Ubuntu 24.04 / Amazon Linux 2023 AMI (installs Docker, pulls
the image; 3–5 min). Nothing else runs on the node; readiness is observed by
this service, not reported by an agent.

## Configuration

See [`docs/on-demand-processing/configuration.md`](../../docs/on-demand-processing/configuration.md)
for every variable. Minimum for AWS:

```
PROVISIONER_PROVIDER=aws
PROVISIONER_AWS_REGION=eu-central-1
PROVISIONER_AWS_AMI_ID=ami-…
PROVISIONER_AWS_INSTANCE_TYPES={"cpu": "c6i.2xlarge", "cpu-large": "c6i.8xlarge"}
PROVISIONER_AWS_SUBNET_ID=subnet-…
PROVISIONER_AWS_SECURITY_GROUP_IDS=sg-…
AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (or *_FILE, or an instance role)
```

A missing required value fails at startup with the variable named.

## Development

```bash
python -m venv venv && ./venv/bin/pip install -r requirements.txt
./venv/bin/python -m pytest -q                      # core (fake provider), AWS (moto), API (TestClient)
PROVISIONER_PROVIDER=fixed PROVISIONER_FIXED_ENDPOINT=127.0.0.1:3000 \
  ./venv/bin/uvicorn app.main:app --port 5002 --reload
```

## Security

* Only reachable on the compose `backend` network; never published through Caddy.
* Holds the provisioning identity only (`infra/aws/iam/provisioner-policy.json`:
  run/describe/terminate instances tagged by this deployment; no S3).
* Node tokens arrive in the create request, go into user-data, and are never
  logged. Error messages carry EC2 error codes, never request parameters.
* Runs as an unprivileged user on a read-only rootfs with all capabilities dropped.
