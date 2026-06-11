# EC2 Troubleshooting

> **Category:** AWS | EC2 | Compute
> **Difficulty:** Basic to Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#aws` `#ec2` `#compute` `#oncall`

---

## Table of Contents

1. [Instance Not Reachable — SSH Debug Flow](#instance-not-reachable--ssh-debug-flow)
2. [EC2 Console Screenshot](#ec2-console-screenshot)
3. [Instance Metadata Service (IMDS)](#instance-metadata-service-imds)
4. [CloudWatch Agent](#cloudwatch-agent)
5. [EBS Volume Performance](#ebs-volume-performance)
6. [T-Series Burstable Instances](#t-series-burstable-instances)
7. [ENI Limits and IP Exhaustion](#eni-limits-and-ip-exhaustion)
8. [Placement Groups](#placement-groups)
9. [AWS CLI EC2 Diagnostic Script](#aws-cli-ec2-diagnostic-script)

---

## Instance Not Reachable — SSH Debug Flow

When an EC2 instance is unreachable, work through these layers from outside-in. Do NOT skip steps — each one eliminates a failure domain.

### The 10-Layer SSH Diagnostic

#### Layer 1: Security Group — Inbound SSH

```bash
aws ec2 describe-security-groups \
  --group-ids sg-xxxxxxxxx \
  --query "SecurityGroups[0].IpPermissions[?FromPort==\`22\`]"
```

Check:
- Is port 22 open to your current IP?
- Is it open to `0.0.0.0/0` (anywhere — should be restricted)?
- Is the protocol TCP (not UDP)?

```text
Missing rule example:
  "IpPermissions": [      ← Empty or missing port 22
      {
          "FromPort": 80,
          "ToPort": 80,
          "IpProtocol": "tcp",
          "IpRanges": [{"CidrIp": "0.0.0.0/0"}]
      }
  ]
  → SSH (port 22) is NOT allowed. Add an inbound rule for TCP 22.
```

#### Layer 2: NACL — Ephemeral Ports

```bash
aws ec2 describe-network-acls \
  --network-acl-ids acl-xxxxxxxxx \
  --query "NetworkAcls[0].Entries"
```

Network ACLs are **stateless**. If you allow inbound TCP 22, you MUST also allow outbound ephemeral ports (1024-65535) for the return traffic.

```text
Common mistake:
  Inbound:  TCP 22 from 0.0.0.0/0  ALLOW  ✓
  Outbound: (default deny all)           ✗

  The SSH SYN reaches the instance, but the SYN-ACK is blocked
  by the outbound NACL rule. The SSH handshake never completes.

Fix: Add outbound rule for ephemeral ports:
  Outbound: TCP 1024-65535 to 0.0.0.0/0 ALLOW
```

#### Layer 3: Route Table — Gateway Routing

```bash
aws ec2 describe-route-tables \
  --route-table-ids rtb-xxxxxxxxx \
  --query "RouteTables[0].Routes"
```

```text
PUBLIC SUBNET (should have IGW route):
  Destination: 0.0.0.0/0 → Target: igw-xxxxxxxxx     ✓

PRIVATE SUBNET (should have NAT route):
  Destination: 0.0.0.0/0 → Target: nat-xxxxxxxxx      ✓

MISSING ROUTE:
  No 0.0.0.0/0 route at all → no way to reach the internet
  → Cannot SSH in from outside the VPC
```

#### Layer 4: Internet Gateway — Attachment

```bash
aws ec2 describe-internet-gateways \
  --filters Name=attachment.vpc-id,Values=vpc-xxxxxxxxx
```

```text
If no IGW is attached to the VPC:
  → A public subnet with a route to igw-xxx points to nothing
  → No internet traffic can enter or leave the VPC

Check: is the IGW in the "attached" state?
  "Attachments": [{"State": "attached", "VpcId": "vpc-xxx"}]
```

#### Layer 5: Key Pair — PEM File

```bash
# Local check: do you have the correct key?
ls -la ~/.ssh/my-key.pem
# -rw-------  1 user  staff  1692 Jun 11 10:00 my-key.pem
#           ^^^^^^^^ MUST be 0600 or 0400

# Fix permissions:
chmod 400 ~/.ssh/my-key.pem

# Test the key against the instance fingerprint:
ssh-keygen -y -f ~/.ssh/my-key.pem

# Verify the instance uses this key:
aws ec2 describe-instances --instance-ids i-xxx \
  --query "Reservations[0].Instances[0].KeyName"
```

If the key pair was lost or the wrong key was specified at launch:

```bash
# Recovery option 1: EC2 Instance Connect (if enabled)
aws ec2-instance-connect send-ssh-public-key \
  --instance-id i-xxx \
  --instance-os-user ec2-user \
  --ssh-public-key file://~/.ssh/id_rsa.pub

# Recovery option 2: Stop instance, detach root volume,
# attach to another instance, modify authorized_keys,
# reattach, start

# Recovery option 3: Use SSM Session Manager (if SSM agent is running)
aws ssm start-session --target i-xxx
```

#### Layer 6: Instance State — Is It Running?

```bash
aws ec2 describe-instances --instance-ids i-xxx \
  --query "Reservations[0].Instances[0].State.Name"
```

```text
Possible states:
  pending    — Launching, not yet running
  running    — ✓ Should be accessible
  stopping   — Being stopped (graceful shutdown)
  stopped    — Not running. start-instances to boot it
  shutting-down — Being terminated
  terminated — Gone forever. Cannot recover without snapshot/AMI.
```

#### Layer 7: Status Checks

```bash
aws ec2 describe-instance-status --instance-ids i-xxx
```

Two independent checks:

```text
System Status Check (AWS's responsibility):
  ✓ "ok" — underlying hardware, networking, power are fine
  ✗ "impaired" — AWS hypervisor/hardware issue
     Action: STOP and START the instance (moves to new hardware)
     DO NOT just reboot — that keeps it on the same physical host.

Instance Status Check (your responsibility):
  ✓ "ok" — OS is running, network stack is up
  ✗ "impaired" — OS crash, kernel panic, OOM, filesystem full
     Action: Check console screenshot, CloudWatch logs, then reboot
```

#### Layer 8: EC2 Instance Connect

```bash
# Try browser-based SSH (must have instance-connect installed)
aws ec2-instance-connect send-ssh-public-key \
  --instance-id i-xxx \
  --instance-os-user ec2-user \
  --ssh-public-key file://~/.ssh/id_rsa.pub

# Then SSH within 60 seconds:
ssh ec2-user@<instance-ip>
```

#### Layer 9: Console Screenshot

```bash
aws ec2 get-console-screenshot --instance-id i-xxx \
  --output text | base64 -D > screenshot.jpg
```

This captures whatever is currently on the virtual display. Look for:
- Kernel panic stack traces
- Filesystem errors (EXT4-fs error, XFS corruption)
- OOM killer messages
- "Give root password for maintenance" (fsck required)
- "No bootable device" (corrupted bootloader/volume)

#### Layer 10: CloudWatch Metrics

```bash
aws cloudwatch get-metric-statistics \
  --namespace AWS/EC2 \
  --metric-name CPUUtilization \
  --dimensions Name=InstanceId,Value=i-xxx \
  --start-time 2026-06-11T09:00:00Z \
  --end-time 2026-06-11T10:00:00Z \
  --period 300 --statistics Average

# Check memory (if CloudWatch agent is configured):
aws cloudwatch get-metric-statistics \
  --namespace CWAgent \
  --metric-name mem_used_percent \
  --dimensions Name=InstanceId,Value=i-xxx \
  --start-time 2026-06-11T09:00:00Z \
  --end-time 2026-06-11T10:00:00Z \
  --period 300 --statistics Average
```

---

## EC2 Console Screenshot

The console screenshot is an underused but powerful debugging tool. It captures exactly what's on the virtual display — including boot errors, kernel panics, and login prompts.

```bash
# Retrieve screenshot and save as JPEG
aws ec2 get-console-screenshot \
  --instance-id i-xxx \
  --query "ImageData" \
  --output text | base64 -D > console.jpg

# If the output is "The instance does not have console output":
# The instance is either stopped or still launching. Wait and retry.
```

### What to Look For

```text
NORMAL:
  - Login prompt: "Amazon Linux 2023" / "Ubuntu 22.04 LTS"
  - Boot messages scrolling
  - Clean filesystem mount output

ABNORMAL:
  - "Kernel panic - not syncing: VFS: Unable to mount root fs"
    → Corrupted root volume, wrong device in fstab, missing kernel module
  - "No bootable device"
    → Volume detached, wrong boot mode (BIOS vs UEFI), GRUB corrupted
  - "Enter root password for maintenance"
    → Filesystem errors requiring manual fsck
  - "Out of memory: Killed process XXXX"
    → OOM killer active — check memory metrics
  - Blank screen
    → Instance is stopped, or OS hasn't started booting yet
  - "WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!"
    → (Seen from YOUR terminal, not the screenshot)
    → The instance was rebuilt, new SSH host key generated
    → Remove old key: ssh-keygen -R <hostname>
```

---

## Instance Metadata Service (IMDS)

IMDS provides instance-specific data at `http://169.254.169.254/`. It's accessible ONLY from within the instance — no authentication needed within the host.

### IMDSv2 vs IMDSv1

```text
IMDSv1 (legacy, no token required):
  curl http://169.254.169.254/latest/meta-data/
  → Vulnerable to SSRF attacks if app makes arbitrary HTTP requests

IMDSv2 (session-oriented, token required):
  TOKEN=$(curl -X PUT "http://169.254.169.254/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
  curl -H "X-aws-ec2-metadata-token: $TOKEN" \
    http://169.254.169.254/latest/meta-data/
  → Protected against SSRF (PUT with custom header blocks simple GET-based SSRF)
```

### Essential IMDS Queries

```bash
TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 300")

# Basic instance info
curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/instance-id
  # i-0abcd1234efgh5678

curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/instance-type
  # m6i.xlarge

curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/placement/availability-zone
  # us-east-1a

# Networking
curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/public-ipv4
  # 52.10.5.20

curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/local-ipv4
  # 10.0.1.50

curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/mac
  # 0a:1b:2c:3d:4e:5f

# IAM Role credentials (temporary, auto-rotated)
curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/iam/security-credentials/my-role
# Returns: AccessKeyId, SecretAccessKey, Token, Expiration

# User data (the launch script)
curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/user-data
```

### IMDS Hop Limit

The IMDS hop limit controls how many network hops the token can travel (relevant for containers on EC2):

```bash
# Default hop limit is 1 — only the EC2 instance itself can use IMDS
# If Docker containers need IMDS, increase hop limit to 2:

aws ec2 modify-instance-metadata-options \
  --instance-id i-xxx \
  --http-put-response-hop-limit 2 \
  --http-tokens required \
  --http-endpoint enabled
```

---

## CloudWatch Agent

The CloudWatch agent collects OS-level metrics (memory, disk, swap, processes) that EC2 doesn't expose natively. If metrics are missing, the agent is the first thing to check.

### Agent Status

```bash
# Linux
systemctl status amazon-cloudwatch-agent
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a status

# Windows
Get-Service -Name "AmazonCloudWatchAgent"

# Logs
journalctl -u amazon-cloudwatch-agent --since "1 hour ago"
cat /opt/aws/amazon-cloudwatch-agent/logs/amazon-cloudwatch-agent.log
```

### Common Agent Issues

```text
SYMPTOM: No memory/disk metrics in CloudWatch. CPU is fine.

CAUSE 1: Agent not installed
  $ which /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent
  Install: sudo yum install amazon-cloudwatch-agent

CAUSE 2: Agent not running
  $ systemctl status amazon-cloudwatch-agent
  Start: sudo systemctl start amazon-cloudwatch-agent

CAUSE 3: Wrong IAM role
  Instance profile must include CloudWatchAgentServerPolicy
  or equivalent permissions:
    - cloudwatch:PutMetricData
    - cloudwatch:GetMetricData
    - cloudwatch:ListMetrics
    - logs:CreateLogGroup
    - logs:CreateLogStream
    - logs:PutLogEvents

CAUSE 4: Config file missing or invalid
  $ cat /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json
  Validate: sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
    -a fetch-config -m ec2 -c file:/opt/aws/.../amazon-cloudwatch-agent.json -s

CAUSE 5: Missing namespace in CloudWatch query
  EC2 level metrics: namespace = "AWS/EC2"
  Agent metrics:     namespace = "CWAgent" (or custom)
```

---

## EBS Volume Performance

### gp2 vs gp3 vs io1/io2

| Type | Baseline IOPS | Max IOPS | Baseline Throughput | Burst | Price |
|------|--------------|----------|---------------------|-------|-------|
| gp2 | 3 per GB (min 100) | 16,000 | 250 MB/s | Yes (credits) | $0.10/GB |
| gp3 | 3,000 | 16,000 | 125 MB/s | No (sustained) | $0.08/GB |
| io1 | Provisioned | 64,000 | 1,000 MB/s | No | $0.125/GB + $0.065/IOPS |
| io2 Block Express | Provisioned | 256,000 | 4,000 MB/s | No | Higher |
| st1 (HDD) | N/A | 500 | 500 MB/s | Yes | $0.045/GB |
| sc1 (Cold HDD) | N/A | 250 | 250 MB/s | Yes | $0.015/GB |

### gp2 Burst Bucket Mechanics

```text
gp2 IOPS formula: 3 IOPS per GB
  - 33 GB volume: 100 IOPS baseline (minimum)
  - 100 GB volume: 300 IOPS baseline
  - 1000 GB volume: 3,000 IOPS baseline
  - 5334 GB volume: 16,000 IOPS baseline (capped)

Burst bucket:
  - Starts full: 5.4 million I/O credits
  - Each credit = 1 I/O operation
  - Earns credits at baseline IOPS rate when idle
  - Spends credits when IOPS > baseline
  - When bucket empties: IOPS throttled to baseline

CloudWatch metric: BurstBalance (%)
  - 100% = full bucket, can burst at max
  - 0% = empty bucket, throttled to baseline
  - Alert when BurstBalance < 10% for >30 minutes
```

### Scenario: "Database Migration Exhausts EBS Burst Bucket"

```text
SYMPTOM: "After migrating the reporting DB to a new gp2 100GB volume,
         queries that took 200ms now take 1,000ms. CPU and memory
         are normal. It's intermittent — sometimes fast, sometimes slow."

INVESTIGATION:
  Volume: 100 GB gp2 → 300 IOPS baseline
  Workload: ETL job does 800 IOPS for 4 hours every night

  During ETL (bursting):  800 IOPS → spending 500 credits/sec
  Bucket: 5.4M credits → depletes in ~3 hours (5.4M / 500 = 10,800 sec)

  After 3 hours:
    BurstBalance = 0%
    IOPS throttled to 300 (from 800 needed)
    → Queue depth grows at EBS level
    → Every I/O takes 2.7x longer (800/300 = 2.67x)

  CloudWatch confirms:
    VolumeQueueLength spikes from 0 to 50+ during ETL
    BurstBalance drops to 0 at T+3 hours

FIX:
  1. Migrate to gp3: 3,000 IOPS baseline (no bursting needed)
     Cost: same or less than gp2
  2. Or increase gp2 size to 1600+ GB for sustained 4,800+ IOPS
  3. Or migrate to io1 with provisioned IOPS
```

---

## T-Series Burstable Instances

T-series instances (T2, T3, T3a, T4g) have a **CPU baseline** and **credit system** similar to EBS burst buckets.

### CPU Credit Mechanics

```text
T3.micro: 2 vCPUs, baseline = 10% CPU per vCPU
  - Earns 6 CPU credits per hour (each credit = 1 vCPU at 100% for 1 minute)
  - If CPU usage is at 10%: earns credits at the same rate it spends them
  - If CPU usage is at 5%: earns credits faster than spending (saving)
  - If CPU usage is at 50%: spends credits faster than earning (depleting)

T2 (Standard mode): When credits run out, CPU throttled to baseline
T3/T4g (Unlimited mode): When credits run out, keeps running but YOU PAY
  for surplus credits at $0.05/vCPU-hour

CloudWatch metrics:
  CPUCreditUsage     — credits spent per 5 min
  CPUCreditBalance   — credits remaining
  CPUSurplusCreditBalance — surplus spent (only when unlimited, not T2)
```

### Scenario: "Web Server 100% CPU at Peak Hours"

```text
SYMPTOM: "Our T3.micro web server hits 100% CPU every day at 9 AM.
         Response times go from 50ms to 2,000ms. After an hour,
         performance returns to normal even though traffic is
         still high."

INVESTIGATION:
  CPUCreditBalance at 8 AM:  100 credits (enough for 100 minutes at 100%)
  CPUCreditBalance at 9 AM:  drops rapidly
  CPUCreditBalance at 9:30 AM:  0 credits
  CPUUtilization at 9:30 AM:  drops from 100% to 10% ← throttled!

  Why does performance "recover"?
  → CPU throttled to 10% baseline → fewer requests processed/sec
  → Incoming request rate also drops (clients time out and leave)
  → Fits within baseline → appears "recovered"
  → Actually: just processing way fewer requests

  Check CPUSurplusCreditBalance:
  If T3 unlimited mode is ON:
    CPUSurplusCreditBalance grows → you're paying for surplus
    CPU stays at 100%, performance stays bad
    (throttling avoided, but you're spending money AND getting poor perf)

FIX:
  1. Enable T3 Unlimited mode (stop throttle, pay surplus, but this
     doesn't fix the performance problem — just hides it)
  2. Upgrade to a non-burstable instance: M6i.large (sustained compute)
  3. Add instances behind a load balancer (horizontal scaling)
  4. Investigate WHY CPU is peaking — is there a cron job at 9 AM?
     A batch process? Optimize that.
```

---

## ENI Limits and IP Exhaustion

### ENI Limits by Instance Type

```text
Each instance type has a max number of ENIs and max IPs per ENI:

  t3.micro:    2 ENIs, 2 IPs per ENI = 4 total IPs
  m5.large:    3 ENIs, 10 IPs per ENI = 30 total IPs
  m5.xlarge:   4 ENIs, 15 IPs per ENI = 60 total IPs
  m5.24xlarge: 15 ENIs, 50 IPs per ENI = 750 total IPs
```

### IP Exhaustion in Subnets

```text
AWS reserves 5 IPs per subnet:
  - 10.0.0.0:  Network address
  - 10.0.0.1:  VPC router
  - 10.0.0.2:  DNS server (Route 53 Resolver)
  - 10.0.0.3:  Reserved for future use
  - 10.0.0.255: Broadcast (last IP)

Available = (2^(32-prefix_length)) - 5

Example: 10.0.0.0/24 (256 IPs) → 251 available
Example: 10.0.0.0/28 (16 IPs) → 11 available

Check available IPs:
aws ec2 describe-subnets --subnet-ids subnet-xxx \
  --query "Subnets[0].AvailableIpAddressCount"
```

```bash
# Check ENIs attached to an instance
aws ec2 describe-network-interfaces \
  --filters Name=attachment.instance-id,Values=i-xxx

# Check available IPs in a subnet
aws ec2 describe-subnets \
  --filters Name=vpc-id,Values=vpc-xxx \
  --query "Subnets[*].{ID:SubnetId,CIDR:CidrBlock,Available:AvailableIpAddressCount}"
```

---

## Placement Groups

| Type | Description | Use Case | Limitation |
|------|------------|----------|------------|
| **Cluster** | Instances packed closely (same rack/switch) | Low-latency HPC, tightly coupled workloads | Single point of failure (rack failure kills all) |
| **Spread** | Each instance on distinct hardware | Critical apps, small number of instances | Max 7 running instances per AZ |
| **Partition** | Groups of instances spread across partitions | Large distributed workloads (HDFS, Cassandra) | Max 7 partitions per AZ |

```bash
# Check which placement group an instance belongs to
aws ec2 describe-instances --instance-ids i-xxx \
  --query "Reservations[0].Instances[0].Placement.GroupName"
```

### Scenario: "Spread Placement Group Blocks Deployment"

```text
SYMPTOM: "Auto Scaling group can't launch a new instance.
         Error: 'Spread placement groups do not support more
         than 7 running instances per Availability Zone.'"

INVESTIGATION:
  → ASG is configured with a spread placement group
  → Already 7 instances running in us-east-1a
  → AWS enforces the limit: no more spread instances in this AZ

FIX:
  1. If you have <7 instances: remove the spread placement group
  2. If you need >7 instances: switch to partition placement group
  3. Or: add instances in different AZs (spread limit is per AZ)
```

---

## AWS CLI EC2 Diagnostic Script

```bash
#!/bin/bash
# ec2-diag.sh — comprehensive EC2 instance diagnostic
# Usage: ./ec2-diag.sh i-xxxxxxxxxxxxx

set -euo pipefail

INSTANCE_ID="${1:?Usage: $0 <instance-id>}"
REGION="${2:-$(aws configure get region)}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "=== EC2 Diagnostic: ${INSTANCE_ID} ==="
echo ""

# Get basic info
INSTANCE_INFO=$(aws ec2 describe-instances \
  --instance-ids "$INSTANCE_ID" \
  --region "$REGION" 2>&1) || {
    echo -e "${RED}✗ Instance not found or access denied${NC}"
    exit 1
}

# 1. State
STATE=$(echo "$INSTANCE_INFO" | jq -r '.Reservations[0].Instances[0].State.Name')
echo -n "State: "
case "$STATE" in
  running) echo -e "${GREEN}${STATE}${NC}" ;;
  stopped) echo -e "${YELLOW}${STATE}${NC}" ;;
  *)       echo -e "${RED}${STATE}${NC}" ;;
esac

# 2. Status Checks
STATUS=$(aws ec2 describe-instance-status \
  --instance-ids "$INSTANCE_ID" \
  --region "$REGION" 2>/dev/null || echo "{}")

SYS=$(echo "$STATUS" | jq -r '.InstanceStatuses[0].SystemStatus.Status // "unknown"')
INST=$(echo "$STATUS" | jq -r '.InstanceStatuses[0].InstanceStatus.Status // "unknown"')
echo -n "System Status:  "
[ "$SYS" = "ok" ] && echo -e "${GREEN}${SYS}${NC}" || echo -e "${RED}${SYS}${NC}"
echo -n "Instance Status: "
[ "$INST" = "ok" ] && echo -e "${GREEN}${INST}${NC}" || echo -e "${RED}${INST}${NC}"

# 3. Networking
SG_IDS=$(echo "$INSTANCE_INFO" | jq -r '.Reservations[0].Instances[0].SecurityGroups[].GroupId')
SUBNET_ID=$(echo "$INSTANCE_INFO" | jq -r '.Reservations[0].Instances[0].SubnetId')
VPC_ID=$(echo "$INSTANCE_INFO" | jq -r '.Reservations[0].Instances[0].VpcId')
PUBLIC_IP=$(echo "$INSTANCE_INFO" | jq -r '.Reservations[0].Instances[0].PublicIpAddress // "none"')
PRIVATE_IP=$(echo "$INSTANCE_INFO" | jq -r '.Reservations[0].Instances[0].PrivateIpAddress // "none"')

echo "Public IP:     ${PUBLIC_IP}"
echo "Private IP:    ${PRIVATE_IP}"
echo "VPC:           ${VPC_ID}"
echo "Subnet:        ${SUBNET_ID}"
echo "Security Groups: ${SG_IDS}"

# 4. Subnet available IPs
AVAIL_IPS=$(aws ec2 describe-subnets \
  --subnet-ids "$SUBNET_ID" --region "$REGION" \
  --query "Subnets[0].AvailableIpAddressCount" --output text 2>/dev/null || echo "?")
echo "Available IPs in subnet: ${AVAIL_IPS}"

# 5. SSH rule check
echo ""
echo "--- SSH (Port 22) Reachability ---"
for SG in $SG_IDS; do
    SSH_RULES=$(aws ec2 describe-security-groups \
      --group-ids "$SG" --region "$REGION" \
      --query "SecurityGroups[0].IpPermissions[?FromPort==\`22\`]" \
      --output json 2>/dev/null || echo "[]")
    if [ "$SSH_RULES" != "[]" ]; then
        echo -e "  ${GREEN}✓${NC} SG $SG has port 22 open"
        echo "$SSH_RULES" | jq -r '.[].IpRanges[].CidrIp' | while read -r cidr; do
            echo "    From: $cidr"
        done
    else
        echo -e "  ${RED}✗${NC} SG $SG does NOT have port 22 open"
    fi
done

# 6. CloudWatch CPU (last 15 min)
echo ""
echo "--- CPU Utilization (last 15 min) ---"
CPU_DATA=$(aws cloudwatch get-metric-statistics \
  --namespace AWS/EC2 \
  --metric-name CPUUtilization \
  --dimensions Name=InstanceId,Value="$INSTANCE_ID" \
  --start-time "$(date -u -v-15M +%Y-%m-%dT%H:%M:%SZ)" \
  --end-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --period 300 --statistics Average \
  --region "$REGION" 2>/dev/null || echo "{}")

echo "$CPU_DATA" | jq -r '.Datapoints[] | "  \(.Timestamp): \(.Average)%"'

echo ""
echo "=== Diagnostic Complete ==="
```

---

## References

- [AWS EC2 Instance Types](https://aws.amazon.com/ec2/instance-types/)
- [AWS EC2 Status Checks](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/monitoring-system-instance-status-check.html)
- [IMDS Documentation](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-instance-metadata.html)
- [CloudWatch Agent Setup](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Install-CloudWatch-Agent.html)
- [EBS Volume Types](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ebs-volume-types.html)
- [T-Series Instance Credits](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/burstable-credits-baseline-concepts.html)
- [Placement Groups](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/placement-groups.html)
