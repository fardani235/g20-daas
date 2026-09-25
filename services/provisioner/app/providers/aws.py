"""AWS EC2 provider: one on-demand instance per task, CPU only for now.

Boots the configured AMI (prebaked with Docker + NodeODM, or a stock image
that the user-data bootstraps from scratch) with a per-run token and port,
tags it so the sweep can find orphans, and terminates it on destroy. The
instance is launched with ``InstanceInitiatedShutdownBehavior=terminate`` and
the bootstrap schedules a shutdown past the lifetime budget, so a node
outlives a dead control plane by at most the budget plus ten minutes.

Credentials: boto3's default chain (``AWS_ACCESS_KEY_ID`` /
``AWS_SECRET_ACCESS_KEY`` — ``*_FILE`` accepted — or an instance role). This
is the *provisioning* identity; it needs EC2 run/describe/terminate on
resources tagged by this deployment and nothing else (policy in
``infra/aws/iam/provisioner-policy.json``).

Every AWS knob is explicit config (see ``AwsSettings``); a missing required
value fails at startup, not at the first task.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime

from app.config import ConfigError, env, env_bool, env_int, env_json, env_list
from app.providers.base import (
    ClassSpec,
    InstanceSpec,
    InstanceState,
    Provider,
    ProviderError,
    ProviderUnavailable,
)

_USERDATA_TEMPLATE = os.path.join(os.path.dirname(__file__), "..", "bootstrap", "nodeodm-userdata.sh")

# EC2 state name -> normalised status.
_STATE_MAP = {
    "pending": "pending",
    "running": "running",
    "shutting-down": "terminated",
    "terminated": "terminated",
    "stopping": "terminated",
    "stopped": "terminated",  # we never stop; a stopped node is as good as gone
}


@dataclass
class AwsSettings:
    region: str
    ami_id: str
    instance_types: dict[str, str]              # class -> EC2 instance type
    hourly_costs: dict[str, float]              # class -> USD/h (estimate only)
    default_class: str
    subnet_id: str
    security_group_ids: list[str]
    root_volume_gb: int = 200
    key_name: str | None = None
    iam_instance_profile: str | None = None
    associate_public_ip: bool = True
    tag_prefix: str = "webodm"
    deployment: str = "default"                 # distinguishes stacks sharing an account
    ami_prebaked: bool = False                  # informational; the bootstrap copes either way
    endpoint_url: str | None = None             # tests / LocalStack
    extra_tags: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> AwsSettings:
        types = env_json("PROVISIONER_AWS_INSTANCE_TYPES", None)
        if not isinstance(types, dict) or not types:
            raise ConfigError(
                'PROVISIONER_AWS_INSTANCE_TYPES must be a JSON object, e.g. {"cpu": "c6i.2xlarge"}'
            )
        costs = env_json("PROVISIONER_AWS_HOURLY_COSTS", {}) or {}
        default_class = env("PROVISIONER_AWS_DEFAULT_CLASS", next(iter(types)))
        if default_class not in types:
            raise ConfigError(f"PROVISIONER_AWS_DEFAULT_CLASS={default_class!r} is not in PROVISIONER_AWS_INSTANCE_TYPES")
        groups = env_list("PROVISIONER_AWS_SECURITY_GROUP_IDS")
        if not groups:
            raise ConfigError("PROVISIONER_AWS_SECURITY_GROUP_IDS is required (comma-separated sg-... ids)")
        return cls(
            region=env("PROVISIONER_AWS_REGION", required=True),
            ami_id=env("PROVISIONER_AWS_AMI_ID", required=True),
            instance_types={str(k): str(v) for k, v in types.items()},
            hourly_costs={str(k): float(v) for k, v in costs.items()},
            default_class=default_class,
            subnet_id=env("PROVISIONER_AWS_SUBNET_ID", required=True),
            security_group_ids=groups,
            root_volume_gb=env_int("PROVISIONER_AWS_ROOT_VOLUME_GB", 200),
            key_name=env("PROVISIONER_AWS_KEY_NAME"),
            iam_instance_profile=env("PROVISIONER_AWS_IAM_INSTANCE_PROFILE"),
            associate_public_ip=env_bool("PROVISIONER_AWS_ASSOCIATE_PUBLIC_IP", True),
            tag_prefix=env("PROVISIONER_AWS_TAG_PREFIX", "webodm") or "webodm",
            deployment=env("PROVISIONER_DEPLOYMENT", "default") or "default",
            ami_prebaked=env_bool("PROVISIONER_AWS_AMI_PREBAKED", False),
            endpoint_url=env("PROVISIONER_AWS_ENDPOINT_URL"),
            extra_tags=env_json("PROVISIONER_AWS_EXTRA_TAGS", {}) or {},
        )


class AwsProvider(Provider):
    name = "aws"

    def __init__(self, settings: AwsSettings, *, nodeodm_image: str, nodeodm_port: int,
                 nodeodm_args: list[str] | None = None, client=None):
        self.settings = settings
        self.nodeodm_image = nodeodm_image
        self.nodeodm_port = nodeodm_port
        self.nodeodm_args = list(nodeodm_args or [])
        self._client = client
        self._root_device: str | None = None
        self._classes = {
            name: ClassSpec(name=name, flavor=itype, hourly_cost=settings.hourly_costs.get(name, 0.0),
                            description=f"EC2 {itype} in {settings.region}")
            for name, itype in settings.instance_types.items()
        }

    # -- boto -------------------------------------------------------------

    @property
    def client(self):
        if self._client is None:
            import boto3
            from botocore.config import Config

            self._client = boto3.client(
                "ec2",
                region_name=self.settings.region,
                endpoint_url=self.settings.endpoint_url,
                config=Config(retries={"max_attempts": 4, "mode": "standard"}, connect_timeout=10, read_timeout=60),
            )
        return self._client

    @staticmethod
    def _translate(exc: Exception, what: str) -> ProviderError:
        """Map botocore errors to ours without echoing request parameters."""
        try:
            from botocore.exceptions import BotoCoreError, ClientError
        except ImportError:  # pragma: no cover
            return ProviderError(f"{what} failed: {exc.__class__.__name__}")
        if isinstance(exc, ClientError):
            code = (exc.response or {}).get("Error", {}).get("Code", "")
            status = (exc.response or {}).get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
            if status and status >= 500 or code in ("RequestLimitExceeded", "Unavailable", "InternalError"):
                return ProviderUnavailable(f"{what} failed: EC2 {code or status}")
            return ProviderError(f"{what} failed: EC2 {code or 'error'}")
        if isinstance(exc, BotoCoreError):
            return ProviderUnavailable(f"{what} failed: {exc.__class__.__name__}")
        return ProviderError(f"{what} failed: {exc.__class__.__name__}")

    # -- Provider -----------------------------------------------------------

    @property
    def classes(self) -> dict[str, ClassSpec]:
        return self._classes

    @property
    def default_class(self) -> str:
        return self.settings.default_class

    def _tag(self, key: str) -> str:
        return f"{self.settings.tag_prefix}:{key}"

    def _managed_filters(self) -> list[dict]:
        return [
            {"Name": f"tag:{self._tag('managed')}", "Values": ["true"]},
            {"Name": f"tag:{self._tag('deployment')}", "Values": [self.settings.deployment]},
        ]

    def render_userdata(self, spec: InstanceSpec) -> str:
        with open(_USERDATA_TEMPLATE) as fh:
            template = fh.read()
        return (
            template.replace("__TOKEN__", spec.token.replace("'", ""))
            .replace("__PORT__", str(spec.port))
            .replace("__IMAGE__", self.nodeodm_image)
            .replace("__MAX_LIFETIME__", str(int(spec.max_lifetime_seconds or 0)))
            .replace("__EXTRA_ARGS__", " ".join(self.nodeodm_args))
        )

    def _root_device_name(self) -> str:
        if self._root_device is None:
            try:
                images = self.client.describe_images(ImageIds=[self.settings.ami_id]).get("Images", [])
            except Exception as e:
                raise self._translate(e, "describe AMI") from None
            if not images:
                raise ProviderError(f"AMI {self.settings.ami_id} not found in {self.settings.region}")
            self._root_device = images[0].get("RootDeviceName") or "/dev/sda1"
        return self._root_device

    def create(self, spec: InstanceSpec) -> InstanceState:
        cls = self.classes.get(spec.instance_class)
        if cls is None:
            raise ProviderError(f"unknown instance class {spec.instance_class!r}")
        s = self.settings
        tags = [
            {"Key": "Name", "Value": f"{s.tag_prefix}-node-{spec.labels.get('task', 'task')}"[:255]},
            {"Key": self._tag("managed"), "Value": "true"},
            {"Key": self._tag("deployment"), "Value": s.deployment},
            {"Key": self._tag("class"), "Value": spec.instance_class},
        ]
        for k, v in spec.labels.items():
            if v:
                tags.append({"Key": self._tag(k), "Value": str(v)[:255]})
        for k, v in s.extra_tags.items():
            tags.append({"Key": str(k), "Value": str(v)[:255]})

        params = {
            "ImageId": s.ami_id,
            "InstanceType": cls.flavor,
            "MinCount": 1,
            "MaxCount": 1,
            "UserData": self.render_userdata(spec),
            "InstanceInitiatedShutdownBehavior": "terminate",
            "NetworkInterfaces": [{
                "DeviceIndex": 0,
                "SubnetId": s.subnet_id,
                "Groups": s.security_group_ids,
                "AssociatePublicIpAddress": s.associate_public_ip,
                "DeleteOnTermination": True,
            }],
            "BlockDeviceMappings": [{
                "DeviceName": self._root_device_name(),
                "Ebs": {"VolumeSize": int(s.root_volume_gb), "VolumeType": "gp3", "DeleteOnTermination": True},
            }],
            "TagSpecifications": [
                {"ResourceType": "instance", "Tags": tags},
                {"ResourceType": "volume", "Tags": tags},
            ],
            "MetadataOptions": {"HttpTokens": "required", "HttpEndpoint": "enabled"},
        }
        if s.key_name:
            params["KeyName"] = s.key_name
        if s.iam_instance_profile:
            key = "Arn" if s.iam_instance_profile.startswith("arn:") else "Name"
            params["IamInstanceProfile"] = {key: s.iam_instance_profile}

        try:
            resp = self.client.run_instances(**params)
        except Exception as e:
            raise self._translate(e, "run_instances") from None
        inst = (resp.get("Instances") or [{}])[0]
        instance_id = inst.get("InstanceId")
        if not instance_id:
            raise ProviderError("run_instances returned no instance id")
        return InstanceState(
            handle=instance_id, status=_STATE_MAP.get((inst.get("State") or {}).get("Name", "pending"), "pending"),
            public_ip=inst.get("PublicIpAddress"), launched_at=inst.get("LaunchTime"), labels=dict(spec.labels),
        )

    def describe(self, handle: str) -> InstanceState:
        try:
            resp = self.client.describe_instances(InstanceIds=[handle])
        except Exception as e:
            err = self._translate(e, "describe_instances")
            if "InvalidInstanceID" in str(err):
                return InstanceState(handle=handle, status="terminated", detail="instance not found")
            raise err from None
        for reservation in resp.get("Reservations", []):
            for inst in reservation.get("Instances", []):
                return self._state(inst)
        return InstanceState(handle=handle, status="terminated", detail="instance not found")

    def _state(self, inst: dict) -> InstanceState:
        ec2_state = (inst.get("State") or {}).get("Name", "pending")
        labels = {}
        prefix = self._tag("")
        for tag in inst.get("Tags", []) or []:
            if tag.get("Key", "").startswith(prefix):
                labels[tag["Key"][len(prefix):]] = tag.get("Value", "")
        return InstanceState(
            handle=inst["InstanceId"],
            status=_STATE_MAP.get(ec2_state, "pending"),
            public_ip=inst.get("PublicIpAddress"),
            launched_at=inst.get("LaunchTime") if isinstance(inst.get("LaunchTime"), datetime) else None,
            labels=labels,
            detail=ec2_state,
        )

    def destroy(self, handle: str) -> None:
        try:
            self.client.terminate_instances(InstanceIds=[handle])
        except Exception as e:
            err = self._translate(e, "terminate_instances")
            if "InvalidInstanceID" in str(err):
                return  # already gone: idempotent
            # Terminating an instance that is already shutting down can error
            # on some backends; if EC2 says it is gone, the job is done.
            try:
                if self.describe(handle).status == "terminated":
                    return
            except ProviderError:
                pass
            raise err from None

    def list_managed(self) -> list[InstanceState]:
        filters = self._managed_filters() + [
            {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]},
        ]
        out: list[InstanceState] = []
        try:
            paginator = self.client.get_paginator("describe_instances")
            for page in paginator.paginate(Filters=filters):
                for reservation in page.get("Reservations", []):
                    for inst in reservation.get("Instances", []):
                        out.append(self._state(inst))
        except Exception as e:
            raise self._translate(e, "describe_instances") from None
        return out
