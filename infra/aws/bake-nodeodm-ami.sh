#!/usr/bin/env bash
# Bake the prebaked NodeODM AMI: a stock Ubuntu 24.04 image with Docker
# installed and the NodeODM image already pulled, so an on-demand node boots
# in about a minute with only the per-run `docker run` left to do.
#
# This is optional. Until the AMI exists, point PROVISIONER_AWS_AMI_ID at a
# stock Ubuntu 24.04 AMI and the same bootstrap installs everything on first
# boot (slower, a few minutes, one apt + one docker pull per node).
#
# Usage:
#   AWS_REGION=eu-central-1 SUBNET_ID=subnet-... SECURITY_GROUP_ID=sg-... \
#     [BASE_AMI_ID=ami-...] [NODEODM_IMAGE=opendronemap/nodeodm:latest] \
#     [INSTANCE_TYPE=c6i.large] infra/aws/bake-nodeodm-ami.sh
#
# Prints the new AMI id at the end; put it in PROVISIONER_AWS_AMI_ID and set
# PROVISIONER_AWS_AMI_PREBAKED=true. Re-run whenever you want a newer NodeODM.
set -euo pipefail

: "${AWS_REGION:?AWS_REGION is required}"
: "${SUBNET_ID:?SUBNET_ID is required (a public subnet)}"
: "${SECURITY_GROUP_ID:?SECURITY_GROUP_ID is required (outbound internet is enough)}"
NODEODM_IMAGE="${NODEODM_IMAGE:-opendronemap/nodeodm:latest}"
INSTANCE_TYPE="${INSTANCE_TYPE:-c6i.large}"
NAME="webodm-nodeodm-$(date -u +%Y%m%d-%H%M%S)"

if [ -z "${BASE_AMI_ID:-}" ]; then
  # Canonical's current Ubuntu 24.04 LTS (amd64, gp3) via SSM public parameter.
  BASE_AMI_ID=$(aws ssm get-parameter --region "$AWS_REGION" \
    --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
    --query Parameter.Value --output text)
fi
echo "base AMI: $BASE_AMI_ID  nodeodm image: $NODEODM_IMAGE"

# Same bootstrap the provisioner uses, with a placeholder token and an
# immediate stop of the container: we only want Docker + the image on disk.
USERDATA=$(sed -e "s|__TOKEN__|bake|" -e "s|__PORT__|3000|" \
               -e "s|__IMAGE__|${NODEODM_IMAGE}|" -e "s|__MAX_LIFETIME__|0|" -e "s|__EXTRA_ARGS__||" \
               "$(dirname "$0")/../../services/provisioner/app/bootstrap/nodeodm-userdata.sh")
USERDATA+=$'\n'"docker rm -f nodeodm; rm -rf /opt/nodeodm/data; cloud-init clean --logs || true; touch /var/tmp/webodm-bake-done"$'\n'

INSTANCE_ID=$(aws ec2 run-instances --region "$AWS_REGION" \
  --image-id "$BASE_AMI_ID" --instance-type "$INSTANCE_TYPE" --count 1 \
  --subnet-id "$SUBNET_ID" --security-group-ids "$SECURITY_GROUP_ID" --associate-public-ip-address \
  --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=30,VolumeType=gp3,DeleteOnTermination=true}' \
  --user-data "$USERDATA" \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=${NAME}-builder},{Key=webodm:bake,Value=true}]" \
  --query 'Instances[0].InstanceId' --output text)
echo "builder instance: $INSTANCE_ID"
trap 'echo "terminating builder $INSTANCE_ID"; aws ec2 terminate-instances --region "$AWS_REGION" --instance-ids "$INSTANCE_ID" >/dev/null' EXIT

aws ec2 wait instance-running --region "$AWS_REGION" --instance-ids "$INSTANCE_ID"
# Give the bootstrap time to finish. Without SSM/SSH access to the box the
# simplest robust signal is the console: poll until the marker appears or a
# generous deadline passes (docker install + a ~1.5 GB pull).
echo "waiting for bootstrap (up to 15 min)..."
for _ in $(seq 1 90); do
  sleep 10
  if aws ec2 get-console-output --region "$AWS_REGION" --instance-id "$INSTANCE_ID" --output text 2>/dev/null \
      | grep -q "nodeodm started"; then
    echo "bootstrap finished"; break
  fi
done
sleep 30  # let the trailing cleanup commands run

aws ec2 stop-instances --region "$AWS_REGION" --instance-ids "$INSTANCE_ID" >/dev/null
aws ec2 wait instance-stopped --region "$AWS_REGION" --instance-ids "$INSTANCE_ID"

AMI_ID=$(aws ec2 create-image --region "$AWS_REGION" --instance-id "$INSTANCE_ID" \
  --name "$NAME" --description "WebODM NodeODM node: Docker + ${NODEODM_IMAGE}" \
  --tag-specifications "ResourceType=image,Tags=[{Key=webodm:nodeodm-image,Value=${NODEODM_IMAGE}}]" \
  --query ImageId --output text)
echo "creating AMI $AMI_ID ..."
aws ec2 wait image-available --region "$AWS_REGION" --image-ids "$AMI_ID"

echo
echo "PROVISIONER_AWS_AMI_ID=$AMI_ID"
echo "PROVISIONER_AWS_AMI_PREBAKED=true"
