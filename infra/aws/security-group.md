# Security group for on-demand NodeODM nodes

One group, referenced by `PROVISIONER_AWS_SECURITY_GROUP_IDS`.

**Inbound**

| Protocol | Port | Source | Why |
|---|---|---|---|
| TCP | 3000 (`PROVISIONER_NODEODM_PORT`) | `APP_EGRESS_IP/32` | The worker streams imagery in, polls, and downloads `all.zip`; the provisioner probes `/info` for readiness. Both leave the app host through this address. |
| TCP | 22 | your admin IP/32 (optional) | Only if `PROVISIONER_AWS_KEY_NAME` is set, for debugging a node. Remove afterwards. |

Nothing else. The node's API is additionally protected by the per-run bearer
token baked in at boot, but the group is the first wall: a public NodeODM
without both would process anyone's imagery on your bill.

**Outbound**: allow all (Docker image pull, apt on a non-prebaked AMI, and the
node never initiates a connection to the stack).

```bash
APP_EGRESS_IP=$(curl -s https://checkip.amazonaws.com)
SG=$(aws ec2 create-security-group --group-name webodm-nodes \
      --description "WebODM on-demand NodeODM nodes" --vpc-id "$VPC_ID" \
      --query GroupId --output text)
aws ec2 authorize-security-group-ingress --group-id "$SG" \
      --ip-permissions "IpProtocol=tcp,FromPort=3000,ToPort=3000,IpRanges=[{CidrIp=${APP_EGRESS_IP}/32,Description=webodm-app}]"
echo "PROVISIONER_AWS_SECURITY_GROUP_IDS=$SG"
```

If the app host's egress address changes (new NAT, moved VPS), update the rule
first: symptoms are tasks stuck in **Provisioning** until the provisioning
timeout, then retried and finally failed — see the troubleshooting guide.
