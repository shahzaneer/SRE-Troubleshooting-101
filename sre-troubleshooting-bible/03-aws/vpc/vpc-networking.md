# VPC Networking Troubleshooting

> **Category:** AWS | VPC | Networking
> **Difficulty:** Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#aws` `#vpc` `#networking` `#oncall`

---

## Table of Contents

1. [VPC Flow Logs](#vpc-flow-logs)
2. [Security Group vs NACL](#security-group-vs-nacl)
3. [VPC Peering](#vpc-peering)
4. [Transit Gateway](#transit-gateway)
5. [NAT Gateway](#nat-gateway)
6. [VPC Endpoints](#vpc-endpoints)
7. [Network Diagnostic Script](#network-diagnostic-script)

---

## VPC Flow Logs

VPC Flow Logs capture IP traffic going to and from network interfaces. They are the single most powerful tool for network debugging.

### Enabling Flow Logs

```bash
aws logs create-log-group --log-group-name /vpc/flow-logs

aws ec2 create-flow-logs \
  --resource-type VPC \
  --resource-ids vpc-12345678 \
  --traffic-type ALL \
  --log-group-name /vpc/flow-logs \
  --deliver-logs-permission-arn arn:aws:iam::123456789012:role/FlowLogsRole \
  --log-format '${version} ${account-id} ${interface-id} ${srcaddr} ${dstaddr} ${srcport} ${dstport} ${protocol} ${packets} ${bytes} ${start} ${end} ${action} ${log-status}'
```

### Reading Flow Log Entries

```text
Format: version account-id interface-id srcaddr dstaddr srcport dstport
        protocol packets bytes start end action log-status

Example:
  2 123456789012 eni-0abc1234 10.0.1.100 10.0.2.50 54321 443 6 5 1200 1687471200 1687471202 ACCEPT OK

Key fields:
  action:
    ACCEPT → Allowed by NACL and Security Group. TCP handshake may still fail at OS level.
    REJECT → Explicitly denied by NACL or SG. Packet NEVER reached the OS.

  log-status:
    OK → Normal. NODATA → No traffic during capture. SKIPDATA → Some entries skipped.
```

### Scenario: "Instance A Can't Reach Instance B in Same VPC"

```text
Symptom: 10.0.1.100 can't connect to 10.0.2.50:8080. Same VPC. ping works.

Flow Logs show REJECT for all packets from 10.0.1.100 → 10.0.2.50:8080.

Check NACLs on BOTH subnets (NACLs are STATELESS):
  aws ec2 describe-network-acls \
    --filters Name=association.subnet-id,Values=subnet-private-b

  Inbound rules: #100 ALLOW 10.0.1.0/24 TCP 443 ← Only port 443!
  Port 8080 is NOT allowed inbound → REJECT at subnet boundary.

Check Security Groups on destination instance:
  SG allows 10.0.0.0/16 TCP 8080 ✓

ROOT CAUSE: NACL on subnet-private-b allows only port 443. Port 8080 blocked
before it reaches the instance's security group. Fix: add inbound NACL rule
for TCP 8080 from source CIDR.
```

---

## Security Group vs NACL

Understanding the stateless/stateful difference is critical for diagnosing
connectivity issues.

### Key Differences

| Aspect | Security Group | NACL |
|--------|---------------|------|
| Scope | ENI (instance-level) | Subnet (subnet boundary) |
| State | STATEFUL (return auto-allowed) | STATELESS (both directions needed) |
| Rules | ALLOW only (implicit deny) | ALLOW + DENY (explicit deny possible) |
| Evaluation | All rules evaluated | Lowest rule number first, then DENY |
| Return Traffic | Auto-allowed if inbound allowed | Must explicitly allow ephemeral ports |

### Ephemeral Ports

```text
TCP connection flow:
  1. Client picks ephemeral port (1024-65535) as srcport
  2. Client SYN → 10.0.2.50:443
  3. Server SYN-ACK → client_ip:ephemeral_port
  4. Client ACK

With NACLs (stateless): If you allow INBOUND TCP 443 but DON'T allow OUTBOUND
TCP 1024-65535, the server's SYN-ACK is DROPPED by its OWN outbound NACL rules.
```

### Scenario: "SSH Works from Office But Not from Home"

```text
Broken: SSH to bastion works from office IP (203.0.113.10) but fails from
home IP (198.51.100.50). Same key, same user.

SG check: TCP 22 from 0.0.0.0/0 ALLOW ✓ (open to everyone)

NACL check on bastion's subnet:
  Inbound: Rule 100: TCP 22 from 203.0.113.0/24 ALLOW  ← OFFICE ONLY!
           Rule *:   ALL TRAFFIC DENY

ROOT CAUSE: NACL allows SSH only from office IP range. SG allows everyone,
but NACL evaluates FIRST and denies home IP at subnet boundary.

Fix: Update NACL or use VPN.
```

---

## VPC Peering

VPC Peering connects two VPCs using AWS's network backbone. Non-overlapping
CIDRs required. NO TRANSITIVE ROUTING (A→B→C: A cannot reach C through B).

### Creating and Verifying

```bash
# Create peering
aws ec2 create-vpc-peering-connection \
  --vpc-id vpc-A --peer-vpc-id vpc-B \
  --peer-owner-id <ACCOUNT_B_ID> --peer-region us-east-1

# Accept in peer account
aws ec2 accept-vpc-peering-connection --vpc-peering-connection-id pcx-xxx

# Add routes on BOTH sides
aws ec2 create-route --route-table-id rtb-A \
  --destination-cidr-block 10.2.0.0/16 --vpc-peering-connection-id pcx-xxx
aws ec2 create-route --route-table-id rtb-B \
  --destination-cidr-block 10.1.0.0/16 --vpc-peering-connection-id pcx-xxx
```

### Scenario: "Can't Reach VPC-C Through VPC-B"

```text
VPC-A peered to VPC-B ✓, VPC-B peered to VPC-C ✓. VPC-A → VPC-C ✗.

ROOT CAUSE: VPC peering is NOT transitive. AWS does not forward traffic
between peering connections. VPC-A must peer directly to VPC-C.

Fix: Direct peering or Transit Gateway (hub-and-spoke model). TGW scales
linearly (N VPCs = N attachments vs. N(N-1)/2 peerings).
```

---

## Transit Gateway

TGW is a hub-and-spoke model for connecting VPCs, VPNs, and Direct Connects.

### Key Commands

```bash
# List TGWs
aws ec2 describe-transit-gateways

# List VPC attachments
aws ec2 describe-transit-gateway-vpc-attachments \
  --transit-gateway-id tgw-xxx

# View routes in TGW route table
aws ec2 search-transit-gateway-routes \
  --transit-gateway-route-table-id tgw-rtb-xxx \
  --filters Name=type,Values=static,propagated

# Enable route propagation (auto-add VPC routes)
aws ec2 enable-transit-gateway-route-table-propagation \
  --transit-gateway-route-table-id tgw-rtb-xxx \
  --transit-gateway-attachment-id tgw-attach-vpc-B
```

### Scenario: "New VPC Attached to TGW But Can't Reach Other VPCs"

```text
VPC-D attached, attachment is "available" in console. But can't reach others.

Check TGW route table: NO ROUTE for VPC-D's CIDR → return traffic blocked.
Check VPC-D subnet route tables: NO routes to other VPC CIDRs via TGW.

ROOT CAUSE: Both sides missing routes. TGW doesn't auto-propagate routes
to VPC route tables. Must manually add or enable propagation.

Fix:
  1. Add routes in VPC-D's subnet RT: 10.x.0.0/16 → tgw-xxx
  2. Enable propagation on TGW route table for VPC-D attachment
```

---

## NAT Gateway

Allows private subnet instances to reach internet (outbound only).

### Architecture Check

```text
Private instance → NAT GW requires:
  1. Private subnet RT: 0.0.0.0/0 → nat-xxxxxxxxx
  2. NAT GW in PUBLIC subnet (has IGW route 0.0.0.0/0 → igw-xxx)
  3. NAT GW has Elastic IP

Check everything:
  aws ec2 describe-nat-gateways --nat-gateway-ids nat-xxx \
    --query "NatGateways[0].{State:State,SubnetId:SubnetId}"
  # Verify NAT subnet has IGW route
  aws ec2 describe-route-tables \
    --filters Name=association.subnet-id,Values=<nat-subnet> \
    --query "RouteTables[0].Routes[?DestinationCidrBlock=='0.0.0.0/0'].GatewayId"
  # Should return igw-xxxxxxxxx
```

### SNAT Port Exhaustion

```text
Each NAT Gateway: 55,000 simultaneous connections to a SINGLE destination (IP:port).

SNAT exhaustion occurs with high-churn connections to same external endpoint.
Example: Lambda making 500 short-lived HTTPS connections/sec to same API →
500 × 120s TIME_WAIT = 60,000 ports → exceeds 55K → intermittent timeouts.

CloudWatch metrics:
  - ErrorPortAllocation: failed SNAT port allocations
  - PacketsDropCount: packets dropped due to exhaustion
  - ActiveConnectionCount: current active connections

Fix: Multiple NAT GWs (different AZs), HTTP connection pooling,
  VPC Endpoints for AWS services, or move to public subnet.
```

---

## VPC Endpoints

### Interface vs Gateway Endpoint

| Feature | Interface Endpoint | Gateway Endpoint |
|---------|-------------------|------------------|
| Implementation | ENI in your subnet | Route table entry |
| Services | Most AWS services | S3, DynamoDB ONLY |
| Cost | $0.01/hr per ENI + data | Free |
| Routing | DNS-based (auto for SDKs) | Route-based (route table update required) |
| Security Group | Yes | No (endpoint policy applies) |

### Scenario: "S3 Access From Private Subnet Works But Is Slow"

```text
Symptom: S3 GETs from private subnet take 200-400ms. Public subnet: 30ms.

Check route: 0.0.0.0/0 → NAT Gateway. S3 traffic goes through internet:
  Instance → NAT GW → IGW → Internet → S3 → Internet → IGW → NAT GW → Instance

Fix: Create S3 Gateway Endpoint:
  aws ec2 create-vpc-endpoint \
    --vpc-id vpc-xxx --vpc-endpoint-type Gateway \
    --service-name com.amazonaws.us-east-1.s3 \
    --route-table-ids rtb-private

After: Instance → VPC Router → S3 Gateway Endpoint → S3 (AWS backbone)
  Latency drops to 10-30ms. No NAT GW bottleneck. No bandwidth charges.
```

### DNS for Endpoints

```bash
aws ec2 modify-vpc-attribute --vpc-id vpc-xxx --enable-dns-support
aws ec2 modify-vpc-attribute --vpc-id vpc-xxx --enable-dns-hostnames

# Without these, private DNS for endpoints won't resolve.
# SDK tries to resolve sqs.us-east-1.amazonaws.com to public IPs.
# From private subnet without NAT, connection fails.
```

---

## Network Diagnostic Script

```bash
#!/bin/bash
# vpc-net-diag.sh — End-to-end VPC network diagnostic
# Usage: ./vpc-net-diag.sh <source-instance-id> <destination-ip> <port>
set -euo pipefail

SOURCE_INSTANCE="${1:?Usage: $0 <source-instance-id> <destination-ip> <port>}"
DEST_IP="${2:?}"
DEST_PORT="${3:-443}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

echo "=== VPC Network Diagnostic: ${SOURCE_INSTANCE} -> ${DEST_IP}:${DEST_PORT} ==="

# 1. Source instance info
SOURCE_INFO=$(aws ec2 describe-instances --instance-ids "$SOURCE_INSTANCE" \
  --query "Reservations[0].Instances[0]")
SRC_SUBNET=$(echo "$SOURCE_INFO" | jq -r '.SubnetId')
SRC_VPC=$(echo "$SOURCE_INFO" | jq -r '.VpcId')
SRC_SGS=$(echo "$SOURCE_INFO" | jq -r '.SecurityGroups[].GroupId')
SRC_IP=$(echo "$SOURCE_INFO" | jq -r '.PrivateIpAddress')

echo "Source: ${SRC_IP} | Subnet: ${SRC_SUBNET} | VPC: ${SRC_VPC}"
echo "Security Groups: ${SRC_SGS}"
echo ""

# 2. Source NACL outbound rules
echo "--- Source Subnet NACL (Outbound) ---"
aws ec2 describe-network-acls \
  --filters Name=association.subnet-id,Values="$SRC_SUBNET" \
  --query "NetworkAcls[0].Entries[?Egress==\`true\` && RuleAction==\`allow\`].[RuleNumber,Protocol,PortRange.From,PortRange.To,CidrBlock]" \
  --output table 2>/dev/null || echo "  (no NACL or default NACL)"

# 3. Source SG outbound rules
echo ""
echo "--- Source Security Groups (Outbound) ---"
for sg in $SRC_SGS; do
    OUTBOUND=$(aws ec2 describe-security-groups --group-ids "$sg" \
      --query "SecurityGroups[0].IpPermissionsEgress")
    HAS_ALLOW=$(echo "$OUTBOUND" | jq -r '.[] | select(.IpProtocol=="-1" or (.FromPort <= '$DEST_PORT' and .ToPort >= '$DEST_PORT'))')
    if [ -n "$HAS_ALLOW" ]; then
        echo -e "  ${GREEN}OK${NC} SG $sg allows outbound port $DEST_PORT"
    else
        echo -e "  ${RED}FAIL${NC} SG $sg does NOT allow outbound port $DEST_PORT"
    fi
done

# 4. Source route table
echo ""
echo "--- Source Route Table ---"
aws ec2 describe-route-tables \
  --filters Name=association.subnet-id,Values="$SRC_SUBNET" \
  --query "RouteTables[0].Routes[].{Dest:DestinationCidrBlock,Target:GatewayId,NAT:NatGatewayId,Peering:VpcPeeringConnectionId,TGW:TransitGatewayId,Endpoint:VpcEndpointId}" \
  --output table 2>/dev/null

# 5. Destination check (if in same account)
echo ""
echo "--- Destination ---"
DEST_INFO=$(aws ec2 describe-network-interfaces \
  --filters Name=addresses.private-ip-address,Values="$DEST_IP" \
  --query "NetworkInterfaces[0]" 2>/dev/null || echo "null")

if [ "$(echo "$DEST_INFO" | jq -r '.VpcId // "null"')" != "null" ]; then
    DEST_VPC=$(echo "$DEST_INFO" | jq -r '.VpcId')
    DEST_SUBNET=$(echo "$DEST_INFO" | jq -r '.SubnetId')
    DEST_SGS=$(echo "$DEST_INFO" | jq -r '.Groups[].GroupId')

    echo "Found: VPC=$DEST_VPC Subnet=$DEST_SUBNET"

    # Destination NACL inbound
    echo ""
    echo "--- Destination Subnet NACL (Inbound) ---"
    aws ec2 describe-network-acls \
      --filters Name=association.subnet-id,Values="$DEST_SUBNET" \
      --query "NetworkAcls[0].Entries[?Egress==\`false\` && RuleAction==\`allow\`].[RuleNumber,Protocol,PortRange.From,PortRange.To,CidrBlock]" \
      --output table 2>/dev/null || echo "  (no NACL)"

    # Destination SG inbound
    echo ""
    echo "--- Destination Security Groups (Inbound) ---"
    for sg in $DEST_SGS; do
        INBOUND=$(aws ec2 describe-security-groups --group-ids "$sg" \
          --query "SecurityGroups[0].IpPermissions")
        HAS_ALLOW=$(echo "$INBOUND" | jq -r '.[] | select(.IpProtocol=="-1" or (.FromPort <= '$DEST_PORT' and .ToPort >= '$DEST_PORT'))')
        if [ -n "$HAS_ALLOW" ]; then
            echo -e "  ${GREEN}OK${NC} SG $sg allows inbound port $DEST_PORT"
        else
            echo -e "  ${RED}FAIL${NC} SG $sg does NOT allow inbound port $DEST_PORT"
        fi
    done
else
    echo "  Destination $DEST_IP not found (external / other account)"
fi

# 6. Flow logs
echo ""
echo "--- Flow Logs ---"
FLOW_LOGS=$(aws ec2 describe-flow-logs \
  --filter Name=resource-id,Values="$SRC_VPC" \
  --query "FlowLogs[0].LogGroupName" --output text 2>/dev/null || echo "")
if [ -n "$FLOW_LOGS" ]; then
    echo "Flow logs enabled: $FLOW_LOGS"
    echo "Query for REJECT: filter srcaddr='${SRC_IP}' and dstaddr='${DEST_IP}' and dstport=${DEST_PORT} and action='REJECT'"
else
    echo "  No flow logs configured on VPC $SRC_VPC"
fi

echo ""
echo "=== Diagnostic Complete ==="
echo "Quick checks:"
echo "  - REJECT from source NACL: add outbound rule"
echo "  - REJECT from dest NACL: add inbound rule"
echo "  - REJECT from dest SG: add inbound rule to SG"
echo "  - Missing route in source RT: add route for dest CIDR"
echo "  - NAT GW port exhaustion: check ErrorPortAllocation metric"
```

---

## References

- [VPC Flow Logs Documentation](https://docs.aws.amazon.com/vpc/latest/userguide/flow-logs.html)
- [Security Groups vs NACLs](https://docs.aws.amazon.com/vpc/latest/userguide/VPC_Security.html)
- [VPC Peering Guide](https://docs.aws.amazon.com/vpc/latest/peering/what-is-vpc-peering.html)
- [Transit Gateway Documentation](https://docs.aws.amazon.com/vpc/latest/tgw/what-is-transit-gateway.html)
- [NAT Gateway Troubleshooting](https://docs.aws.amazon.com/vpc/latest/userguide/vpc-nat-gateway.html#nat-gateway-troubleshooting)
- [VPC Endpoints](https://docs.aws.amazon.com/vpc/latest/privatelink/vpc-endpoints.html)
