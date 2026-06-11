# IAM Troubleshooting

> **Category:** AWS | IAM | Security
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#aws` `#iam` `#security` `#oncall`

---

## Table of Contents

1. ["Access Denied" Forensics](#access-denied-forensics)
2. [Policy Evaluation Logic](#policy-evaluation-logic)
3. [AssumeRole Chain Debugging](#assumerole-chain-debugging)
4. [Service-Linked Roles](#service-linked-roles)
5. [Instance Profile & EC2 Metadata](#instance-profile--ec2-metadata)
6. [Cross-Account Role Access](#cross-account-role-access)
7. [Troubleshooting Tools & Scripts](#troubleshooting-tools--scripts)

---

## "Access Denied" Forensics

When you hit `AccessDenied`, don't guess. Follow this systematic forensic flow.

### Step 1: Who Am I?

```bash
aws sts get-caller-identity
# Returns:
# {
#     "UserId": "AROA5NEXAMPLE:app-session",
#     "Account": "123456789012",
#     "Arn": "arn:aws:sts::123456789012:assumed-role/AppRole/app-session"
# }
#   Account: which account am I operating in?
#   Arn:     which role/user is my current identity?
```

```text
Key insights from get-caller-identity:
  - If Arn ends with "assumed-role/ROLE_NAME/session": you assumed a role.
    This is common for Lambda, ECS tasks, EC2 instance profiles, and
    cross-account access.
  - If Arn has ":user/": you're using IAM user credentials.
  - The Account number tells you which account's resources you have access to.
    Cross-account issues often come from being in the wrong account.
  - If the output is "InvalidClientTokenId": your credentials are expired
    or invalid. Run `aws configure list` to check your profile.
```

### Step 2: Policy Simulator

```bash
# Test if a specific action is allowed for a principal
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::123456789012:role/MyRole \
  --action-names s3:GetObject \
  --resource-arns arn:aws:s3:::my-bucket/*

# Or simulate for current caller
aws iam simulate-principal-policy \
  --policy-source-arn $(aws sts get-caller-identity --query Arn --output text) \
  --action-names ec2:DescribeInstances s3:ListBucket dynamodb:GetItem
```

```text
Understanding Policy Simulator output:
{
    "EvaluationResults": [
        {
            "EvalActionName": "s3:GetObject",
            "EvalDecision": "explicitDeny",    ← explicitly DENIED
            "MatchedStatements": [              ← which statements matched?
                {
                    "SourcePolicyId": "DenyS3Access",
                    "SourcePolicyType": "IAM_POLICY"
                }
            ],
            "ResourceSpecificResults": [
                {
                    "EvalResourceName": "arn:aws:s3:::my-bucket/*",
                    "EvalResourceDecision": "explicitDeny"
                }
            ]
        }
    ]
}

Decisions you'll see:
  allowed         — Access granted (no deny found, at least one allow)
  explicitDeny    — An explicit "Deny" statement matched (WINS OVER ALLOW)
  implicitDeny    — No allow statement matched (default deny)
```

### Step 3: CloudTrail — Find the Denied Event

```bash
# Search for AccessDenied events in the last 2 hours
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=AccessDenied \
  --start-time "$(date -u -v-2H +%Y-%m-%dT%H:%M:%SZ)" \
  --end-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --max-results 20 \
  --query "Events[*].CloudTrailEvent" \
  --output text | jq -r '
    select(.errorCode != null) |
    "\(.eventTime) | \(.userIdentity.arn) | \(.eventName) | \(.errorCode): \(.errorMessage)"
  '

# Filter for a specific principal
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=Username,AttributeValue=MyRole \
  --start-time "$(date -u -v-6H +%Y-%m-%dT%H:%M:%SZ)" \
  --end-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --max-results 50
```

```text
CloudTrail AccessDenied event fields to examine:
{
  "userIdentity": {
    "type": "AssumedRole",
    "arn": "arn:aws:sts::123456789012:assumed-role/AppRole/i-0abc1234",
    "sessionContext": {
      "sessionIssuer": { ... }  ← who created this session?
    }
  },
  "errorCode": "AccessDenied",
  "errorMessage": "User: arn:aws:sts::123456789012:assumed-role/AppRole/... is not authorized to perform: s3:GetObject on resource: arn:aws:s3:::logs-bucket/..."
}

The resource ARN is the single most valuable piece — it tells you EXACTLY
which resource access was denied. Check if that resource has:
  - A resource-based policy (S3 bucket policy, KMS key policy, SQS queue policy)
  - A permission boundary on the principal
  - An SCP at the organization level
```

### Step 4: Check Explicit Denies (They ALWAYS Win)

```bash
# List all attached policies for a role
aws iam list-attached-role-policies --role-name MyRole

# Check each policy for DENY statements
aws iam get-policy-version \
  --policy-arn arn:aws:iam::123456789012:policy/DeveloperPolicy \
  --version-id v5 \
  --query "PolicyVersion.Document" \
  --output text | jq '.Statement[] | select(.Effect == "Deny")'

# List inline policies (attached directly to the role)
aws iam list-role-policies --role-name MyRole
aws iam get-role-policy --role-name MyRole --policy-name InlineRestrictions \
  --query "PolicyDocument.Statement[] | select(.Effect == \"Deny\")"
```

### Step 5: Check Permission Boundaries and SCPs

```bash
# Check if role has a permission boundary
aws iam get-role --role-name MyRole \
  --query "Role.PermissionsBoundary"

# Check what the boundary allows (boundary limits MAX permissions)
aws iam get-policy-version \
  --policy-arn arn:aws:iam::123456789012:policy/RoleBoundary \
  --version-id v1 \
  --query "PolicyVersion.Document"

# SCPs: If using AWS Organizations, SCPs can deny at the account/OU level
# Check Organizations SCPs for the account:
aws organizations describe-account --account-id 123456789012
aws organizations list-policies --filter SERVICE_CONTROL_POLICY
aws organizations list-policies-for-target \
  --target-id 123456789012 \
  --filter SERVICE_CONTROL_POLICY
```

---

## Policy Evaluation Logic

AWS evaluates policies in a strict hierarchy. Understanding this is essential for debugging any `AccessDenied`.

### Evaluation Order (First Match Wins in Deny)

```text
┌──────────────────────────────────────────────────────────┐
│                    POLICY EVALUATION FLOW                │
│                                                          │
│  1. Explicit DENY in identity-based policy?              │
│     YES → IMMEDIATELY DENY (skip all remaining checks)   │
│     NO  → Continue                                       │
│                                                          │
│  2. Explicit DENY in SCP (Organization level)?           │
│     YES → IMMEDIATELY DENY                               │
│     NO  → Continue                                       │
│                                                          │
│  3. Explicit DENY in permission boundary?                │
│     YES → IMMEDIATELY DENY                               │
│     NO  → Continue                                       │
│                                                          │
│  4. Explicit DENY in resource-based policy?              │
│     YES → IMMEDIATELY DENY                               │
│     NO  → Continue                                       │
│                                                          │
│  5. Explicit ALLOW in resource-based policy?             │
│     YES → ALLOW (no identity policy needed)              │
│     NO  → Continue                                       │
│                                                          │
│  6. Explicit ALLOW in identity-based policy?             │
│     YES → Check SCP allows? SCP ALLOW → ALLOW            │
│     NO  → Continue                                       │
│                                                          │
│  7. Check SCP allow?                                     │
│     NO → IMPLICIT DENY                                   │
│     YES → Check permission boundary allow?               │
│            NO → IMPLICIT DENY                            │
│            YES → ALLOW                                   │
│                                                          │
│  FINAL: If no explicit allow anywhere → IMPLICIT DENY    │
└──────────────────────────────────────────────────────────┘
```

### Scenario: "Admin Can't Access S3 Bucket"

```text
SYMPTOM: "I'm an AWS admin (AdministratorAccess) but I can't access
         our company's production S3 bucket. AccessDenied on every
         operation."

INVESTIGATION:
  Account: 222222222222 (dev account)
  Principal: arn:aws:iam::222222222222:user/admin
  Policy: AdministratorAccess (Allow *:*)

  Step 1: Policy Simulator shows "implicitDeny"
  Step 2: CloudTrail shows AccessDenied for s3:GetBucketLocation

  ROOT CAUSE: An SCP at the Organization level:
  {
    "Effect": "Deny",
    "Action": "s3:*",
    "Resource": "*",
    "Condition": {
      "StringNotEquals": {
        "aws:RequestedRegion": "us-east-1"
      }
    }
  }
  → The SCP denies S3 access in ALL regions EXCEPT us-east-1.
  → The admin is trying to access the bucket using the global endpoint
    (S3 global defaults to us-east-1, but the bucket is in us-west-2
    via region-specific endpoint).

  SCPs cannot be overridden by any identity or resource policy.
  Even AdministratorAccess is powerless against an SCP DENY.

  Fix: Either update the SCP, or use the allowed region.

LESSON: In organizations, SCPs are the ultimate gatekeeper.
        Even account-level admins can't override them.
```

---

## AssumeRole Chain Debugging

### How AssumeRole Works

```text
Chain: IAM User → Role A → Role B → Role C

Each hop requires:
  1. The CALLER must have sts:AssumeRole on the TARGET role
  2. The TARGET role's TRUST POLICY must allow the CALLER to assume it

Example: IAM user "alice" wants to assume "ReadOnlyRole" in account A.

Caller's policy (attached to alice):
  {
    "Effect": "Allow",
    "Action": "sts:AssumeRole",
    "Resource": "arn:aws:iam::ACCOUNT_A:role/ReadOnlyRole"
  }

Target role's TRUST policy (on ReadOnlyRole):
  {
    "Effect": "Allow",
    "Principal": {"AWS": "arn:aws:iam::ALICE_ACCOUNT:user/alice"},
    "Action": "sts:AssumeRole"
  }

BOTH must match for the assumption to succeed.
```

### Debugging AssumeRole Failures

```bash
# 1. Check the caller has permission
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::111111111111:user/alice \
  --action-names sts:AssumeRole \
  --resource-arns arn:aws:iam::222222222222:role/ReadOnlyRole

# 2. Check the target role's trust policy
aws iam get-role --role-name ReadOnlyRole \
  --query "Role.AssumeRolePolicyDocument" --output text | jq .

# Look for:
#   - Principal: correct account ID? Correct role/user ARN?
#   - Action: includes sts:AssumeRole?
#   - Condition: any restrictive conditions?

# 3. Try the assumption with verbose debug
aws sts assume-role \
  --role-arn arn:aws:iam::222222222222:role/ReadOnlyRole \
  --role-session-name debug-session \
  --debug 2>&1 | grep -i "error\|denied\|not authorized"
```

### Scenario: "Cross-Account Role Assumption Fails"

```text
SYMPTOM: "A developer in Account A needs to read logs from Account B.
         They've been using the cross-account role for 12 months.
         Today it stopped working."

INVESTIGATION:
  1. aws sts assume-role --role-arn ROLE_B
     Error: "User: arn:aws:iam::A:user/dev is not authorized to perform:
            sts:AssumeRole on resource: arn:aws:iam::B:role/LogReader"

  2. Check trust policy on ROLE_B:
     aws iam get-role --role-name LogReader --query \
       "Role.AssumeRolePolicyDocument.Statement[]"
     → Principal: {"AWS": "arn:aws:iam::A:user/dev"}
     → Action: sts:AssumeRole
     → Condition: {"StringEquals": {"sts:ExternalId": "secret-external-id-2023"}}

  ROOT CAUSE: The trust policy has an `sts:ExternalId` condition.
  The original developer who set up the role knew the external ID.
  A new team member joined and updated the CI pipeline, forgetting to
  pass the external ID in the AssumeRole call.

  Fix: Include the external ID in the API call:
    aws sts assume-role \
      --role-arn arn:aws:iam::B:role/LogReader \
      --external-id "secret-external-id-2023" \
      --role-session-name ci-session

LESSONS:
  1. ExternalId is a security best practice (prevents confused deputy)
  2. Document external IDs in a secure location (secrets manager, not wiki)
  3. Test cross-account access after any CI pipeline change
```

### Max Session Duration

```bash
# Check the maximum session duration for a role
aws iam get-role --role-name MyRole \
  --query "Role.MaxSessionDuration"

# Default: 3600 seconds (1 hour)
# Max: 43200 seconds (12 hours) for roles assumed by users
# Max: 3600 for roles assumed by roles (chained)

# If you try to set --duration-seconds higher than MaxSessionDuration:
# Error: "The requested DurationSeconds exceeds the MaxSessionDuration set for this role."
```

---

## Service-Linked Roles

Service-linked roles are predefined by AWS services. They can't be deleted manually
until the associated service resource is deleted.

```bash
# List all service-linked roles
aws iam list-roles --query "Roles[?Path=='/aws-service-role/'].[RoleName,Arn]"

# Check which service owns this role
aws iam get-role --role-name AWSServiceRoleForECS \
  --query "Role.Description"
```

### Scenario: "Can't Delete IAM Role"

```text
SYMPTOM: "I'm cleaning up unused IAM roles. When I try to delete
         aws-service-role/rds.amazonaws.com/AWSServiceRoleForRDS,
         I get: 'Cannot delete service-linked role. Please delete
         the associated resources first.'"

INVESTIGATION:
  → This role was auto-created when someone launched an RDS instance.
  → Service-linked roles persist until ALL resources using them are deleted.
  → Even if you deleted the RDS instance 2 weeks ago, if there's a
    manual snapshot still referencing it, the role stays.

  Check for remaining resources:
  aws rds describe-db-instances          # any RDS instances?
  aws rds describe-db-snapshots           # any manual snapshots?
  aws rds describe-db-cluster-snapshots   # any cluster snapshots?
  aws rds describe-db-instance-automated-backups  # any backups?

  Fix:
  1. Delete all resources of that service in the account/region.
  2. THEN the service-linked role can be deleted.
  3. OR: just leave it — service-linked roles cost nothing.
```

---

## Instance Profile & EC2 Metadata

### Checking Instance Profile

```bash
# From within the EC2 instance — who am I?
TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 300")

# Check if an instance profile is attached
ROLE_NAME=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/iam/security-credentials/)

if [ -z "$ROLE_NAME" ]; then
  echo "No IAM role attached to this instance!"
else
  echo "Role: $ROLE_NAME"
  # Get the actual credentials
  curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
    http://169.254.169.254/latest/meta-data/iam/security-credentials/$ROLE_NAME
fi

# From AWS CLI — check what role is attached to an instance
aws ec2 describe-iam-instance-profile-associations \
  --filters Name=instance-id,Values=i-0abc1234def5678

# Get the role details
aws iam get-instance-profile \
  --instance-profile-name MyEC2Profile \
  --query "InstanceProfile.Roles[*].RoleName"
```

### Scenario: "EC2 App Can't Access S3"

```text
SYMPTOM: "Our Rails app on EC2 gets AccessDenied when calling S3.
         The same app works fine on my laptop with user credentials.
         The EC2 instance is running, SSH works fine."

INVESTIGATION:
  1. SSH into the instance
  2. Check IMDS for role:
     curl http://169.254.169.254/latest/meta-data/iam/security-credentials/
     → Returns nothing. No role attached.

  3. Check from AWS CLI:
     aws ec2 describe-instances --instance-ids i-0abc1234 \
       --query "Reservations[0].Instances[0].IamInstanceProfile"
     → Returns null. Instance profile was NEVER attached.

  4. Check the launch template / auto scaling group:
     → Launch template had no IamInstanceProfile specified.
     → Someone launched the instance manually via the console and
       forgot to select the IAM role.

  Fix:
  1. For an existing running instance: you CANNOT attach an instance
     profile after launch. Must create a new instance with it.
     Option: create an AMI from this instance, then launch with role.
     Option: use SSM to deliver temporary credentials.
     Option: stop the instance, use modify-instance-attribute, restart.

  2. For future instances: update the launch template.

  Proper launch template snippet:
  aws ec2 run-instances \
    --image-id ami-0abcdef1234567890 \
    --instance-type t3.medium \
    --iam-instance-profile Name=EC2S3AccessProfile \
    ...
```

---

## Cross-Account Role Access

### Setting Up Cross-Account Access

```text
Account A (Source) → Wants to access → Account B (Target)

Step 1: In Account B, create a role with a trust policy:
  {
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Allow",
        "Principal": {
          "AWS": "arn:aws:iam::ACCOUNT_A_ID:root"
        },
        "Action": "sts:AssumeRole",
        "Condition": {
          "StringEquals": {
            "sts:ExternalId": "secret-external-id-abc123"
          }
        }
      }
    ]
  }
  → "root" means ANY principal in Account A can assume, provided
    they also have sts:AssumeRole in their identity policy.

Step 2: In Account A, the principal needs an identity policy:
  {
    "Effect": "Allow",
    "Action": "sts:AssumeRole",
    "Resource": "arn:aws:iam::ACCOUNT_B_ID:role/CrossAccountRole"
  }

Step 3: Assume the role from Account A:
  aws sts assume-role \
    --role-arn arn:aws:iam::ACCOUNT_B_ID:role/CrossAccountRole \
    --external-id "secret-external-id-abc123" \
    --role-session-name cross-account-debug
```

### Common Cross-Account Gotchas

```text
1. Wrong Account ID in Trust Policy
   "Principal": {"AWS": "arn:aws:iam::111111111111:root"}
   → The source account is 222222222222. Access denied.

2. Forgot ExternalId Condition
   → Trust policy has ExternalId condition. AssumeRole call doesn't pass it.
   → Access denied, even though the account ID is correct.

3. Principal is a Specific Role, Not Root
   "Principal": {"AWS": "arn:aws:iam::A:role/SpecificRole"}
   → Only SpecificRole can assume, not other roles in Account A.
   → If another role tries → denied.

4. Chained Assumptions (Role → Role → S3)
   → The assume chain: User → RoleA → RoleB → Access S3
   → BUT: RoleB's trust policy allows Principal: RoleA
   → AND: RoleA needs sts:AssumeRole for RoleB in its identity policy
   → AND: The CLI only auto-chains profiles if ~/.aws/config is set up:
       [profile roleb]
       role_arn = arn:aws:iam::B:role/RoleB
       source_profile = rolea  # RoleA must be configured above

5. SCP Blocking Cross-Account Access
   → SCP at Org level: "Deny sts:AssumeRole on resource:* unless
     account is in production OU"
   → Dev accounts can't assume ANY cross-account role, period.
```

---

## Troubleshooting Tools & Scripts

### IAM Diagnostic Script

```bash
#!/bin/bash
# iam-diag.sh — Diagnose IAM access denied issues
# Usage: ./iam-diag.sh <role-name> <action> <resource-arn>

set -euo pipefail

ROLE_NAME="${1:?Usage: $0 <role-name> <action> <resource-arn>}"
ACTION="${2:-s3:ListBucket}"
RESOURCE="${3:-*}"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"

echo "=== IAM Diagnostic for: ${ROLE_ARN} ==="
echo "Action:   ${ACTION}"
echo "Resource: ${RESOURCE}"
echo ""

# 1. Role exists?
if aws iam get-role --role-name "$ROLE_NAME" &>/dev/null; then
    echo "✓ Role exists: ${ROLE_NAME}"
else
    echo "✗ Role NOT FOUND: ${ROLE_NAME}"
    exit 1
fi

# 2. List attached policies
echo ""
echo "--- Attached Policies ---"
aws iam list-attached-role-policies --role-name "$ROLE_NAME" \
  --query "AttachedPolicies[*].PolicyName" --output table 2>/dev/null || echo "  (none)"

# 3. Check permission boundary
BOUNDARY=$(aws iam get-role --role-name "$ROLE_NAME" \
  --query "Role.PermissionsBoundary.PermissionsBoundaryArn" --output text 2>/dev/null || echo "none")
echo ""
echo "Permission Boundary: ${BOUNDARY}"

# 4. Simulate the action
echo ""
echo "--- Policy Simulation ---"
aws iam simulate-principal-policy \
  --policy-source-arn "$ROLE_ARN" \
  --action-names "$ACTION" \
  --resource-arns "$RESOURCE" \
  --query "EvaluationResults[*].{Action:EvalActionName,Decision:EvalDecision,Matched:MatchedStatements[*].{Policy:SourcePolicyId,Type:SourcePolicyType}}" \
  --output json 2>/dev/null | jq .

# 5. Check trust policy
echo ""
echo "--- Trust Policy ---"
aws iam get-role --role-name "$ROLE_NAME" \
  --query "Role.AssumeRolePolicyDocument.Statement[*]" --output json 2>/dev/null | jq .

# 6. List SCPs (if Organizations is set up)
echo ""
echo "--- SCP Check ---"
if aws organizations describe-organization &>/dev/null 2>&1; then
    echo "Org policies for account ${ACCOUNT_ID}:"
    aws organizations list-policies-for-target \
      --target-id "$ACCOUNT_ID" \
      --filter SERVICE_CONTROL_POLICY \
      --query "Policies[*].{Name:PolicyName,Description:Description}" \
      --output table 2>/dev/null || echo "  (no SCPs)"
else
    echo "  (not in an organization or no org access)"
fi

echo ""
echo "=== Diagnostic Complete ==="
```

### Quick One-Liners

```bash
# Who am I and what account?
aws sts get-caller-identity

# What permissions does this role actually have?
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::123456789:role/MyRole \
  --action-names s3:GetObject s3:PutObject s3:ListBucket

# What's denying my access?
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=AccessDenied \
  --start-time 2026-06-11T00:00:00Z \
  --end-time 2026-06-12T00:00:00Z \
  --max-results 10 | jq '.Events[].CloudTrailEvent'

# List all roles with their trust policies
aws iam list-roles --query "Roles[].[RoleName,AssumeRolePolicyDocument.Statement[].Principal]" --output table

# Check for unused roles (last used 90+ days ago)
aws iam generate-service-last-accessed-details --arn arn:aws:iam::123456789:role/MyRole
aws iam get-service-last-accessed-details --job-id <job-id>
```

---

## References

- [IAM Policy Evaluation Logic](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_evaluation-logic.html)
- [IAM Policy Simulator](https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies_testing-policies.html)
- [IAM Troubleshooting Guide](https://docs.aws.amazon.com/IAM/latest/UserGuide/troubleshoot.html)
- [Cross-Account Access Roles](https://docs.aws.amazon.com/IAM/latest/UserGuide/tutorial_cross-account-with-roles.html)
- [Service Control Policies (SCPs)](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_policies_scps.html)
- [CloudTrail Event Reference](https://docs.aws.amazon.com/awscloudtrail/latest/userguide/cloudtrail-event-reference.html)
