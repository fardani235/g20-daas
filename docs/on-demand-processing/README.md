# On-demand processing and object storage

| Document | For whom |
|---|---|
| [User guide](user-guide.md) | anyone who presses Start: the Provisioning state, timing, how the machine class is picked, fallback, where outputs live |
| [Deployment and setup](deployment.md) | standing up MinIO (dev) or S3, the AWS identities and policies, the bucket layout, network/firewall, the provisioner; bucket-region trade-off |
| [Configuration reference](configuration.md) | every env var, secret and tunable with meaning, default and example |
| [Operations runbook](runbook.md) | the lifecycle and its safety nets, caps, what to watch, finding/killing instances, cache eviction, falling back to a static node |
| [Troubleshooting](troubleshooting.md) | symptom → cause → fix |
| [Architecture](architecture.md) | the technical description: seams, data model, state machine, security model |

Specs: [`openspec/specs/on-demand-processing`](../../openspec/specs/on-demand-processing/spec.md),
[`openspec/specs/object-storage`](../../openspec/specs/object-storage/spec.md).
AWS artefacts: [`infra/aws/`](../../infra/aws/README.md). Services:
[`services/provisioner`](../../services/provisioner/README.md),
[`services/geospatial`](../../services/geospatial/README.md).
