# AWS artefacts for on-demand processing and object storage

Everything the AWS side of the stack needs, as files you can apply with the
CLI. Placeholders in CAPITALS (`REGION`, `ACCOUNT_ID`, `BUCKET`, `PREFIX`,
`DEPLOYMENT`, `AMI_ID`, `SUBNET_ID`, `SECURITY_GROUP_ID`) must be replaced;
`PREFIX` is the optional key prefix (`WEBODM_S3_PREFIX`, e.g. `prod/`, or
empty). The full walkthrough is in
[`docs/on-demand-processing/deployment.md`](../../docs/on-demand-processing/deployment.md).

| File | Identity / purpose |
|---|---|
| `iam/provisioner-policy.json` | **Provisioning** identity used by `services/provisioner`: launch, describe and terminate EC2 instances — only ones tagged `webodm:managed=true` + this deployment. No S3 access. |
| `iam/storage-app-policy.json` | **App writer** identity (`frappe-web`/`worker`/`scheduler`): read/write/delete under `PREFIXorgs/*`. Uploads inputs, relays outputs, fills the cache, deletes a task's prefix. |
| `iam/storage-geospatial-policy.json` | **Raster converter** identity (`geospatial`): read raw rasters and assets, write only `assets/*.tif` (the COGs). No delete, no access to `inputs/`. |
| `iam/storage-signer-policy.json` | **Signer** identity for presigned GET URLs of outputs, should you enable them. Read only, outputs only. Not used by the shipped code paths (the app serves through its own session-authenticated routes); kept so the privilege split is complete. |
| `iam/bucket-policy.json` | Bucket policy: deny non-TLS access. The bucket must also have *Block Public Access* fully on and default encryption (SSE-S3 or KMS). |
| `bake-nodeodm-ami.sh` | Builds the prebaked NodeODM AMI (Docker + image) so nodes boot in ~1 min instead of ~5. Optional: the bootstrap handles a stock AMI. |
| `security-group.md` | The one security group rule the nodes need. |

## Bucket region vs. provider region

Egress from S3 to an EC2 instance in the **same region** is free; cross-region
is billed (both directions, per GB). The worker streams every input image to
the node and every output back, so for AWS nodes put the bucket in
`PROVISIONER_AWS_REGION`. The trade-off changes once GPU nodes run outside AWS
(vast.ai): every byte to and from those nodes is internet egress from S3
whatever the region, and the deciding factor becomes latency/throughput to
the app host, which is where the relay actually happens (host -> node and
node -> host -> S3). Decide once, write it down in the deployment record, and
keep the bucket close to the *app host* if the providers are mixed. See the
deployment guide, section "Where the bucket sits".
