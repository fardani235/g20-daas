"""AWS provider against moto's EC2 mock: launch parameters, tags, user-data,
describe/terminate semantics, orphan listing and error mapping."""

import base64
import os

import boto3
import pytest
from moto import mock_aws

from app.config import ConfigError
from app.providers.aws import AwsProvider, AwsSettings
from app.providers.base import InstanceSpec, ProviderError

REGION = "eu-central-1"


@pytest.fixture
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    with mock_aws():
        ec2 = boto3.client("ec2", region_name=REGION)
        vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
        subnet = ec2.create_subnet(VpcId=vpc, CidrBlock="10.0.1.0/24")["Subnet"]["SubnetId"]
        sg = ec2.create_security_group(GroupName="webodm-nodes", Description="nodes", VpcId=vpc)["GroupId"]
        ami = ec2.describe_images(Owners=["amazon"])["Images"][0]["ImageId"]
        yield {"ec2": ec2, "subnet": subnet, "sg": sg, "ami": ami}


def _settings(env, **over):
    base = dict(
        region=REGION, ami_id=env["ami"],
        instance_types={"cpu": "c6i.2xlarge", "cpu-large": "c6i.8xlarge"},
        hourly_costs={"cpu": 0.34}, default_class="cpu",
        subnet_id=env["subnet"], security_group_ids=[env["sg"]],
        root_volume_gb=150, deployment="test-stack",
    )
    base.update(over)
    return AwsSettings(**base)


def _provider(env, **over):
    return AwsProvider(_settings(env, **over), nodeodm_image="opendronemap/nodeodm:3.5.6", nodeodm_port=3000,
                       nodeodm_args=["--max_concurrency", "8"])


def _spec(**over):
    base = dict(instance_class="cpu", token="tok-" + "x" * 30, port=3000, max_lifetime_seconds=7200,
                labels={"task": "T-1", "org": "acme", "site": "webodm.local"})
    base.update(over)
    return InstanceSpec(**base)


def test_create_launches_tagged_instance_with_userdata(aws_env):
    p = _provider(aws_env)
    state = p.create(_spec())
    assert state.handle.startswith("i-")
    assert state.status in ("pending", "running")

    inst = aws_env["ec2"].describe_instances(InstanceIds=[state.handle])["Reservations"][0]["Instances"][0]
    assert inst["InstanceType"] == "c6i.2xlarge"
    assert inst["ImageId"] == aws_env["ami"]
    tags = {t["Key"]: t["Value"] for t in inst["Tags"]}
    assert tags["webodm:managed"] == "true"
    assert tags["webodm:deployment"] == "test-stack"
    assert tags["webodm:task"] == "T-1"
    assert tags["webodm:org"] == "acme"
    assert tags["webodm:class"] == "cpu"
    assert tags["Name"] == "webodm-node-T-1"
    assert inst["NetworkInterfaces"][0]["SubnetId"] == aws_env["subnet"]
    assert {g["GroupId"] for g in inst["NetworkInterfaces"][0]["Groups"]} == {aws_env["sg"]}
    # shutdown = terminate so the in-box self-shutdown can never leave a stopped, billed volume
    attr = aws_env["ec2"].describe_instance_attribute(InstanceId=state.handle,
                                                      Attribute="instanceInitiatedShutdownBehavior")
    assert attr["InstanceInitiatedShutdownBehavior"]["Value"] == "terminate"

    ud = aws_env["ec2"].describe_instance_attribute(InstanceId=state.handle, Attribute="userData")
    script = base64.b64decode(ud["UserData"]["Value"]).decode()
    assert "TOKEN='tok-" in script
    assert "PORT='3000'" in script
    assert "IMAGE='opendronemap/nodeodm:3.5.6'" in script
    assert "MAX_LIFETIME='7200'" in script
    assert "EXTRA_ARGS='--max_concurrency 8'" in script
    assert "shutdown -h" in script  # belt-and-braces self-destruct
    assert "install_docker" in script  # bootstrap path for a non-prebaked AMI


def test_render_userdata_strips_quotes_from_token(aws_env):
    p = _provider(aws_env)
    script = p.render_userdata(_spec(token="ab'c'def" + "x" * 20))
    assert "TOKEN='abcdef" in script


def test_root_volume_size_and_type(aws_env):
    p = _provider(aws_env, root_volume_gb=321)
    state = p.create(_spec())
    vols = aws_env["ec2"].describe_volumes(Filters=[{"Name": "attachment.instance-id", "Values": [state.handle]}])
    sizes = {v["Size"] for v in vols["Volumes"]}
    assert 321 in sizes


def test_unknown_class_is_rejected_before_calling_aws(aws_env):
    p = _provider(aws_env)
    with pytest.raises(ProviderError):
        p.create(_spec(instance_class="gpu"))
    assert aws_env["ec2"].describe_instances()["Reservations"] == []


def test_describe_and_destroy(aws_env):
    p = _provider(aws_env)
    handle = p.create(_spec()).handle
    st = p.describe(handle)
    assert st.status == "running"  # moto goes straight to running
    assert st.public_ip
    assert st.labels["task"] == "T-1"
    assert st.launched_at is not None

    p.destroy(handle)
    assert p.describe(handle).status == "terminated"
    p.destroy(handle)  # idempotent
    p.destroy("i-0000000000deadbeef")  # unknown: no-op, not an error


def test_describe_unknown_is_terminated(aws_env):
    p = _provider(aws_env)
    st = p.describe("i-0000000000deadbeef")
    assert st.status == "terminated"


def test_list_managed_filters_by_tags_and_deployment(aws_env):
    p = _provider(aws_env)
    other = _provider(aws_env, deployment="other-stack")
    h1 = p.create(_spec(labels={"task": "A"})).handle
    h2 = p.create(_spec(labels={"task": "B"})).handle
    other.create(_spec(labels={"task": "C"}))
    # an unrelated instance in the same account
    aws_env["ec2"].run_instances(ImageId=aws_env["ami"], InstanceType="t3.micro", MinCount=1, MaxCount=1)

    handles = {s.handle for s in p.list_managed()}
    assert handles == {h1, h2}
    p.destroy(h1)
    assert {s.handle for s in p.list_managed()} == {h2}


def test_settings_from_env_requires_explicit_values(monkeypatch):
    for k in list(os.environ):
        if k.startswith("PROVISIONER_AWS_"):
            monkeypatch.delenv(k)
    with pytest.raises(ConfigError):
        AwsSettings.from_env()  # no instance types
    monkeypatch.setenv("PROVISIONER_AWS_INSTANCE_TYPES", '{"cpu": "c6i.2xlarge"}')
    with pytest.raises(ConfigError):
        AwsSettings.from_env()  # no security group
    monkeypatch.setenv("PROVISIONER_AWS_SECURITY_GROUP_IDS", "sg-1,sg-2")
    with pytest.raises(ConfigError):
        AwsSettings.from_env()  # no region
    monkeypatch.setenv("PROVISIONER_AWS_REGION", REGION)
    monkeypatch.setenv("PROVISIONER_AWS_AMI_ID", "ami-123")
    monkeypatch.setenv("PROVISIONER_AWS_SUBNET_ID", "subnet-1")
    monkeypatch.setenv("PROVISIONER_AWS_HOURLY_COSTS", '{"cpu": "0.34"}')
    s = AwsSettings.from_env()
    assert s.security_group_ids == ["sg-1", "sg-2"]
    assert s.hourly_costs == {"cpu": 0.34}
    assert s.default_class == "cpu"
    monkeypatch.setenv("PROVISIONER_AWS_DEFAULT_CLASS", "gpu")
    with pytest.raises(ConfigError):
        AwsSettings.from_env()


def test_file_secrets_are_read(monkeypatch, tmp_path):
    from app.config import env

    secret = tmp_path / "tok"
    secret.write_text("from-file\n")
    monkeypatch.setenv("PROVISIONER_API_TOKEN_FILE", str(secret))
    monkeypatch.delenv("PROVISIONER_API_TOKEN", raising=False)
    assert env("PROVISIONER_API_TOKEN") == "from-file"
