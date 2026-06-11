# S3 Troubleshooting

> **Category:** AWS | S3 | Storage
> **Difficulty:** Basic to Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#aws` `#s3` `#storage` `#oncall`

---

## Table of Contents

1. [403 Forbidden — Policy Evaluation Anatomy](#403-forbidden--policy-evaluation-anatomy)
2. [S3 Request Throttling (503 Slow Down)](#s3-request-throttling-503-slow-down)
3. [Consistency Model](#consistency-model)
4. [Cross-Account Access](#cross-account-access)
5. [Presigned URL Expiry](#presigned-url-expiry)
6. [Lifecycle Policies Not Running](#lifecycle-policies-not-running)
7. [Python boto3 Client with Retry Logic](#python-boto3-client-with-retry-logic)
8. [VPC Endpoint Troubleshooting](#vpc-endpoint-troubleshooting)

---

## 403 Forbidden — Policy Evaluation Anatomy

A 403 on S3 is rarely a simple "permission denied." It's the result of a multi-step policy evaluation that considers at least 5 different policy sources.

### The S3 Authorization Decision Flow

```text
REQUEST: GET /my-bucket/data.csv
         by IAM role "arn:aws:iam::123456789012:role/app-role"

DECISION FLOW (evaluated in THIS ORDER):

Step 1: S3 Block Public Access (Account/Bucket Level)
  ├─ BlockPublicAcls: true?
  ├─ IgnorePublicAcls: true?
  ├─ BlockPublicPolicy: true?
  └─ RestrictPublicBuckets: true?
  → If ANY blocks and request is anonymous/public → DENY

Step 2: IAM User/Role Policy
  └─ Does the principal's IAM policy ALLOW s3:GetObject on this bucket?
     If ALLOW → continue. If no mention → continue (implicit deny only applies at end).

Step 3: Bucket Policy
  └─ Does the bucket policy have a matching rule?
     If explicit DENY → STOP, return 403.
     If ALLOW → continue.
     If no match → continue.

Step 4: Bucket ACL (legacy, rarely used)
  └─ Does the bucket ACL grant access?

Step 5: Object ACL (legacy, rarely used)
  └─ Does the object ACL grant access?

FINAL: If NO explicit ALLOW found in Steps 2-5 → implicit DENY → 403.
        If ANY explicit DENY found in Steps 1-3 → DENY (even if later steps ALLOW).
        EXPLICIT DENY ALWAYS WINS OVER ALLOW.
```

### Diagnosing 403 Errors

```bash
# Step 1: Who is making the request?
aws sts get-caller-identity
# → Note the ARN and Account ID

# Step 2: Test if the request works with aws CLI (uses same IAM)
aws s3 ls s3://my-bucket/
aws s3 cp s3://my-bucket/data.csv /tmp/

# Step 3: Check the bucket policy
aws s3api get-bucket-policy --bucket my-bucket
# Parse and look for Deny statements affecting your principal

# Step 4: Check Block Public Access settings
aws s3api get-public-access-block --bucket my-bucket

# Step 5: Test using IAM Policy Simulator
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::123456789012:role/app-role \
  --action-names s3:GetObject \
  --resource-arns arn:aws:s3:::my-bucket/*

# Step 6: If using VPC Endpoint — check the endpoint policy
aws ec2 describe-vpc-endpoints \
  --vpc-endpoint-ids vpce-xxx \
  --query "VpcEndpoints[0].PolicyDocument"
```

### Scenario: "VPC Endpoint Policy Blocks Access"

```text
SYMPTOM: "Our ECS tasks can't read from S3. The IAM role has
         's3:*' on 'Resource: *'. CloudTrail shows AccessDenied.
         The same role works from an EC2 instance in a different VPC."

INVESTIGATION:
  $ aws s3 cp s3://my-bucket/data.csv /tmp/ (from ECS task)
  AccessDenied

  $ aws sts get-caller-identity (from ECS task)
  arn:aws:sts::123456789012:assumed-role/app-role/ecs-task-xxx
  ✓ Correct role

  $ # Check IAM simulation (from anywhere)
  $ aws iam simulate-principal-policy \
    --policy-source-arn arn:aws:iam::123456789012:role/app-role \
    --action-names s3:GetObject \
    --resource-arns arn:aws:s3:::my-bucket/*
  → "EvalDecision": "allowed"
  ✓ IAM says allowed!

  $ # Check VPC endpoint policy
  $ aws ec2 describe-vpc-endpoints \
    --vpc-endpoint-ids vpce-xxx \
    --query "VpcEndpoints[0].PolicyDocument"

  {
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Deny",
        "Principal": "*",
        "Action": "s3:*",
        "Resource": "*",
        "Condition": {
          "StringNotEquals": {
            "s3:prefix": "logs/"     ← Only allows objects with prefix 'logs/'
          }
        }
      }
    ]
  }

ROOT CAUSE: The VPC endpoint policy has an explicit DENY for anything
not matching the 'logs/' prefix. Even though the IAM role allows s3:*,
the VPC endpoint policy denies it. The request traverses through the VPC
endpoint, which adds an additional policy layer.

The policy evaluation order is:
  IAM role (ALLOW) → VPC Endpoint policy (DENY) → DENY WINS.

FIX: Modify the VPC endpoint policy to allow the required bucket/prefix,
or remove the endpoint policy to delegate entirely to IAM.
```

### Common 403 Patterns

```bash
# Pattern 1: Wrong region
aws s3 ls s3://my-bucket
# An error occurred (AccessDenied) when calling the ListObjectsV2 operation
# Bucket might be in us-east-2, but your CLI/STS token is configured for us-west-2.
# S3 doesn't redirect — it just returns 403.

aws s3api get-bucket-location --bucket my-bucket
# → us-east-2 → configure your client for us-east-2

# Pattern 2: SSE-KMS permissions missing
# If objects are encrypted with KMS, you need BOTH:
#   - s3:GetObject on the bucket/object
#   - kms:Decrypt on the KMS key
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::123456789012:role/app-role \
  --action-names kms:Decrypt

# Pattern 3: Bucket owner != object owner
# If Account-A uploads to Account-B's bucket without
# --acl bucket-owner-full-control, Account-B gets 403
# even though it owns the bucket.
# Fix: require bucket-owner-full-control via bucket policy:
{
  "Sid": "RequireBucketOwnerFullControl",
  "Effect": "Deny",
  "Principal": "*",
  "Action": "s3:PutObject",
  "Resource": "arn:aws:s3:::my-bucket/*",
  "Condition": {
    "StringNotEquals": {
      "s3:x-amz-acl": "bucket-owner-full-control"
    }
  }
}
```

---

## S3 Request Throttling (503 Slow Down)

### Understanding S3 Rate Limits

```text
LEGACY (pre-2018):
  - 3,500 PUT/POST/DELETE per second per prefix
  - 5,500 GET/HEAD per second per prefix
  - Prefix = everything before the last "/"
    s3://bucket/folder1/subfolder2/file.csv
    prefix = folder1/subfolder2/

CURRENT (auto-scaling):
  - S3 automatically scales to handle request rates
  - Initial burst capacity: 3,500 PUT or 5,500 GET per second per prefix
  - After initial burst: S3 scales up if traffic continues
  - Scaling takes minutes, not seconds
  - If traffic spikes above burst capacity FAST: 503 Slow Down

MITIGATION: Distribute keys across many prefixes to spread the burst.
```

### Prefix Distribution Strategy

```text
BAD (single prefix bottleneck):
  s3://logs/2026-06-11-10-00-00-app1.log
  s3://logs/2026-06-11-10-00-01-app1.log
  s3://logs/2026-06-11-10-00-02-app1.log
  ...
  Prefix: logs/ → ALL objects share one prefix → ONE burst limit

GOOD (reverse date for better distribution):
  s3://logs/00-00-10-2026-06-11/app1.log
  s3://logs/00-00-11-2026-06-11/app1.log
  s3://logs/00-00-12-2026-06-11/app1.log
  ...
  Prefixes: logs/00-00-10-2026-06-11/, logs/00-00-12-2026-06-11/, ...
  = 3,600 unique prefixes per hour → 3,600 × 3,500 = 12.6M PUT/sec capacity

BEST (hex hash prefix):
  s3://logs/a1/b2/c3/timestamp-data.log
  s3://logs/d4/e5/f6/timestamp-data.log
  ...
  Prefixes: logs/a1, logs/d4, logs/ff, ...
  = 256^3 = 16.8M possible prefixes with 3-char hex → virtually unlimited
```

### Scenario: "Log Aggregation Bottleneck"

```text
SYMPTOM: "Our centralized logging pipeline writes 50,000 log objects
         per second to S3. Every hour we see a spike of 503 Slow Down
         errors. The errors last about 5 minutes, then go away."

INVESTIGATION:
  Object key pattern: logs/YYYY/MM/DD/HH/app-instance-uuid.log
  Example: logs/2026/06/11/10/i-0abcd1234.log

  50,000 writes/sec → single prefix `logs/2026/06/11/10/`
  → Burst capacity: 3,500/sec
  → S3 scales up over ~5 minutes to handle 50,000/sec
  → During those 5 minutes: 503 Slow Down errors

ROOT CAUSE: All objects share the same date-hour prefix.
At the start of each hour, S3's auto-scaling resets back to
baseline burst capacity. The traffic floods in, exceeds the
burst threshold, and triggers 503s until scaling completes.

FIX:
  1. Invert the date: YYYY/MM/DD/HH → MM/DD/HH/YYYY
     = 12 unique first-level prefixes per year (months)
     Actually doesn't help much — still collapses by hour.

  2. Add a random/hash prefix to distribute:
     logs/a1/2026/06/11/10/i-0abcd1234.log
     logs/b4/2026/06/11/10/i-0abcd1234.log
     = 16^2 = 256 prefixes × 3,500 = 896,000 burst capacity

  3. Prepend a device-ID hash:
     {MD5(app-instance-id)[0:3]}/logs/YYYY/MM/DD/HH/
     or better:
     {MD5(app-instance-id)[0:4]}/YYYY/MM/DD/HH/
     = 16^4 = 65,536 prefixes × 3,500 = 229M burst capacity
```

### Checking for 503 Slow Down

```bash
# CloudWatch S3 metrics
aws cloudwatch get-metric-statistics \
  --namespace AWS/S3 \
  --metric-name 5xxErrors \
  --dimensions Name=BucketName,Value=my-bucket \
  --start-time 2026-06-11T09:00:00Z \
  --end-time 2026-06-11T10:00:00Z \
  --period 60 --statistics Sum

# In application logs:
# Look for: "SlowDown", "Please reduce your request rate"
# Or: "503 Service Unavailable" from S3 endpoint

# In CloudTrail:
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=GetObject \
  --query "Events[?contains(CloudTrailEvent, 'SlowDown')]" \
  --max-results 50
```

---

## Consistency Model

### The December 2020 Change

```text
PRE-DECEMBER 2020:
  - PUT of a new object:       read-after-write consistency
  - PUT of an existing object: eventual consistency (might get old version)
  - DELETE:                     eventual consistency
  - LIST:                       eventual consistency

  Real-world headache:
    App deletes user-profile-pic.jpg, then immediately uploads a new one.
    GET might return:
    - The old image (delete hasn't propagated yet)
    - 404 (new object hasn't propagated yet)
    - The new image (both propagated correctly)
    Result: race condition hell.

POST-DECEMBER 2020:
  - ALL S3 operations: strong read-after-write consistency
  - GET after PUT: always returns the new version
  - GET after DELETE: always returns 404
  - LIST: always reflects the current state
  - No additional cost, no performance impact
  - Automatically enabled for ALL S3 buckets globally
```

### What This Means for Your Code

```python
# PRE-2020: This pattern was necessary
import boto3, time

def guarded_put(s3, bucket, key, data):
    s3.put_object(Bucket=bucket, Key=key, Body=data)
    # Wait for consistency (no longer needed!)
    time.sleep(2)
    # Verify
    s3.get_object(Bucket=bucket, Key=key)

# POST-2020: This is overkill — no wait needed
def simple_put(s3, bucket, key, data):
    s3.put_object(Bucket=bucket, Key=key, Body=data)
    return s3.get_object(Bucket=bucket, Key=key)

# If you see sleep/retry patterns for "S3 consistency" in code reviews,
# flag it — it's technical debt. The retry itself doesn't hurt,
# but it adds unnecessary 2-3 second latency to every write.
```

### Scenario: "Legacy Consistency Retry Logic Causes Slowness"

```text
SYMPTOM: "Our upload pipeline takes 3 seconds per file even for
         tiny 1KB JSON documents. Profiling shows 2 seconds of
         sleep() calls."

INVESTIGATION:
  Codebase has:
    s3_client.put_object(...)
    time.sleep(2)       # ← "Wait for S3 consistency"
    s3_client.get_object(...)  # ← "Verify it was written"

  This was added in 2019 to work around eventual consistency.
  Since December 2020, the sleep is unnecessary.
  With strong read-after-write, the GET always returns the correct data.

ROOT CAUSE: Unnecessary sleep() from pre-consistency era.

FIX: Remove the sleep(). The upload pipeline goes from 3s to <200ms.
  Across 1 million objects/day → saves 2,000,000 seconds = ~23 days
  of cumulative time per day.
```

---

## Cross-Account Access

Cross-account S3 access requires BOTH the bucket policy (resource side) AND the IAM policy (principal side) to grant access.

### The Two-Sided Permission Model

```text
ACCOUNT-A (owns the bucket):          ACCOUNT-B (user/role needing access):
  ┌─────────────────────────┐          ┌────────────────────────────┐
  │ Bucket Policy:          │          │ IAM Policy:                │
  │                         │          │                            │
  │ Allow Principal:        │          │ Allow Action: s3:GetObject │
  │   arn:aws:iam::B:role/X │  ────>   │ Resource:                 │
  │ Action: s3:GetObject    │  <────   │   arn:aws:s3:::bucket-A/* │
  │                         │          │                            │
  └─────────────────────────┘          └────────────────────────────┘
  BOTH policies must grant access.    The IAM user/role must ALSO
  The bucket "invites" the principal.  have permission to access S3.
```

### Setting Up Cross-Account Access

```json
// Bucket Policy (in Account-A, the bucket owner):
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::111111111111:role/data-consumer"
      },
      "Action": [
        "s3:GetObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::my-data-bucket",
        "arn:aws:s3:::my-data-bucket/*"
      ]
    }
  ]
}
```

```json
// IAM Policy (in Account-B, the consumer):
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::my-data-bucket",
        "arn:aws:s3:::my-data-bucket/*"
      ]
    }
  ]
}
```

```bash
# Test cross-account access (from Account-B):
aws s3 ls s3://my-data-bucket/ --profile account-b
aws s3 cp s3://my-data-bucket/data.csv /tmp/ --profile account-b
```

### Scenario: "Cross-Account Access Suddenly Fails"

```text
SYMPTOM: "Our analytics account (Account-B) has been reading from
         the production S3 bucket (Account-A) for 6 months. Today,
         all cross-account reads return AccessDenied."

INVESTIGATION:
  $ aws s3 ls s3://prod-data-bucket/ --profile analytics
  AccessDenied

  $ # Check bucket policy (from Account-A)
  $ aws s3api get-bucket-policy --bucket prod-data-bucket --profile prod
  {
    "Statement": [
      {
        "Principal": {"AWS": "arn:aws:iam::222222222222:role/old-analytics-role"},
        ...
      }
    ]
  }
  ↑ Still references "old-analytics-role" — but Analytics team migrated to
    a new role "data-platform-role" last week.

  CloudTrail confirms:
  "userIdentity": {"arn": "arn:aws:iam::222222222222:role/data-platform-role"}
  "errorCode": "AccessDenied"

ROOT CAUSE: The bucket policy wasn't updated when the Analytics team
changed their IAM role name. The bucket only trusts old-analytics-role,
not data-platform-role.

FIX:
  1. Update bucket policy with the new role ARN
  2. Better: use a condition on the source account instead of role name:
     "Condition": {
       "StringEquals": {
         "aws:PrincipalAccount": "222222222222"
       }
     }
     This way, ANY role in Account-B has access (managed by Account-B's IAM).
```

---

## Presigned URL Expiry

Presigned URLs grant temporary access to S3 objects without AWS credentials. They embed the signature, expiry time, and authorized action.

### How Presigned URLs Work

```text
GENERATION (server-side, uses IAM credentials):
  presigned_url = s3.generate_presigned_url(
    ClientMethod='get_object',
    Params={'Bucket': 'my-bucket', 'Key': 'report.pdf'},
    ExpiresIn=3600                         # 1 hour
  )
  → https://my-bucket.s3.amazonaws.com/report.pdf?
    X-Amz-Algorithm=AWS4-HMAC-SHA256&
    X-Amz-Credential=AKIA.../20260611/us-east-1/s3/aws4_request&
    X-Amz-Date=20260611T100000Z&
    X-Amz-Expires=3600&                    ← Duration from generation time
    X-Amz-SignedHeaders=host&
    X-Amz-Signature=abc123...

LIMITS:
  - Default max: 3,600 seconds (1 hour) with permanent credentials
  - Max: 604,800 seconds (7 days) with IAM role or STS credentials
  - Expiry is absolute: generated at T, valid until T+ExpiresIn
  - Cannot be extended after generation (must generate a new one)
```

### Scenario: "Mobile Uploads Fail After 1 Hour"

```text
SYMPTOM: "Users report that uploading files from our mobile app fails
         if they take too long. Photos upload fine, but videos over
         ~50MB consistently fail. Error: 'The request signature we
         calculated does not match the signature you provided.'"

INVESTIGATION:
  Presigned URL expiry: 3600 seconds (1 hour)
  Video file upload: 50MB+ over 4G/LTE connection

  Flow:
  T+0s:     App requests presigned URL. Server generates it (expires at T+3600s).
  T+5s:     App returns presigned URL to client.
  T+10s:    Client starts upload (user went to another screen briefly).
  T+1800s:  Upload at 45% (slow cell connection in rural area).
  T+3590s:  Upload at 98% — only 10 seconds left on URL!
  T+3600s:  URL expires. Signature is now invalid.
            Client finishes upload at T+3610.
            S3: "Signature expired" → 403 Forbidden.

ROOT CAUSE: The presigned URL expires 3600 seconds after generation,
not after upload begins. The upload itself takes >3600 seconds on
slow connections, so the URL expires mid-upload.

FIX:
  1. Use multipart upload with presigned URLs:
     - Generate URL for InitiateMultipartUpload (long expiry: 7 days with STS)
     - Generate separate presigned URLs for each part (short expiry OK)
     - Generate URL for CompleteMultipartUpload
     - Parts are 5MB-5GB each; upload part-by-part with short URLs

  2. Client-side: use a pre-signed POST policy instead of PUT:
     More flexible expiration, can add conditions (content-type, size).

  3. Increase ExpiresIn to 7 days (604800 seconds):
     Only works with STS credentials, not long-lived IAM user access keys.
```

### Generating Different Presigned URL Types

```python
import boto3
from botocore.config import Config

s3 = boto3.client('s3', config=Config(signature_version='s3v4'))

# GET — for downloads (default expiry max: 7 days with STS)
get_url = s3.generate_presigned_url(
    'get_object',
    Params={'Bucket': 'my-bucket', 'Key': 'report.pdf'},
    ExpiresIn=3600  # 1 hour
)

# PUT — for direct uploads
put_url = s3.generate_presigned_url(
    'put_object',
    Params={'Bucket': 'my-bucket', 'Key': 'uploads/file.pdf',
            'ContentType': 'application/pdf'},
    ExpiresIn=3600
)

# Multipart upload initiation
initiate_url = s3.generate_presigned_url(
    'create_multipart_upload',
    Params={'Bucket': 'my-bucket', 'Key': 'large-file.zip',
            'ContentType': 'application/zip'},
    ExpiresIn=604800  # 7 days
)

# Upload part
part_url = s3.generate_presigned_url(
    'upload_part',
    Params={
        'Bucket': 'my-bucket',
        'Key': 'large-file.zip',
        'UploadId': upload_id,
        'PartNumber': 1
    },
    ExpiresIn=3600
)
```

---

## Lifecycle Policies Not Running

### Lifecycle Policy Behavior

```text
Key facts:
  - Policies are evaluated ONCE PER DAY (not continuously)
  - Minimum object age before transition: 1 day (for most rules)
  - Glacier transition: minimum 1 day
  - Objects smaller than 128KB: NEVER transitioned to IA/Glacier
    (S3 minimum billable object size for IA is 128KB)
  - Lifecycle actions are FREE (transitions incur cost, but the rule is free)
  - Takes 24-48 hours for policies to take effect after creation
```

### Diagnostic Checklist

```bash
# 1. Is the rule enabled?
aws s3api get-bucket-lifecycle-configuration --bucket my-bucket
# Look for: "Status": "Enabled" (not "Disabled")

# 2. Is the prefix/filter correct?
# Rule has "Prefix": "logs/" — but objects are in "app-logs/"
# Rule filter "Prefix": "" — applies to ALL objects

# 3. Are objects old enough?
# Rule: "Days": 30 — objects must be 30+ days old
# Check object age:
aws s3api head-object --bucket my-bucket --key path/to/file.txt
# "LastModified": "2026-05-01T00:00:00Z"
# Today is 2026-06-11 = 41 days → should transition
# BUT: if created May 31, only 11 days → won't transition

# 4. Are the objects too small for IA/Glacier?
aws s3api head-object --bucket my-bucket --key small-file.txt
# "ContentLength": 1024   ← 1KB, smaller than 128KB
# → IA/Glacier transition silently SKIPPED by S3

# 5. Check if transitions are actually happening:
aws s3api list-objects-v2 --bucket my-bucket --prefix logs/ \
  --query "Contents[?StorageClass!='STANDARD'].{Key:Key,Class:StorageClass}"
# If empty → no objects have been transitioned yet
```

### Scenario: "Lifecycle Policy Created But Not Applied"

```text
SYMPTOM: "We created a lifecycle policy 2 days ago to transition
         objects older than 30 days to Glacier. Our storage costs
         haven't changed. Objects are still in STANDARD class."

INVESTIGATION:
  Policy:
    "Filter": {"Prefix": "backups/"},
    "Transitions": [{"Days": 30, "StorageClass": "GLACIER"}]
    "Status": "Enabled"

  Objects:
    backups/db/dump-2026-05-01.sql.gz  → 40 days old → SHOULD transition
    backups/db/dump-2026-05-01.sql.gz  → ContentLength: 4KB → 4KB < 128KB!

  S3 silently skips IA/Glacier transitions for objects < 128KB.
  No error, no log entry, no notification — the object simply stays
  in STANDARD.

ROOT CAUSE: Objects smaller than the minimum billable size (128KB for
IA/Glacier). S3's lifecycle engine ignores them for transitions.

FIX:
  1. Know your object sizes. If most backups are <128KB, lifecycle to
     Glacier won't help. Consider Deep Archive (same minimum) or just
     delete them if they're that small and old.
  2. Aggregating small objects before storing (tar/zip, parquet, avro)
     makes transitions effective.
  3. Monitor: Count standard vs transitioned objects by age:
     aws s3api list-objects-v2 --bucket my-bucket --prefix backups/ \
       --query "length(Contents[?StorageClass=='STANDARD'])"
```

---

## Python boto3 Client with Retry Logic

```python
#!/usr/bin/env python3
"""
Production-grade S3 client with retry logic, exponential backoff,
and proper error handling for SlowDown, 403, and transient errors.
"""

import boto3
import botocore
import botocore.exceptions
import logging
import time
import random
from functools import wraps
from typing import Optional, Callable, Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Retryable S3 error codes
RETRYABLE_CODES = {
    'SlowDown',           # Rate limiting
    'InternalError',      # Transient S3 internal error
    'ServiceUnavailable', # S3 temporarily overloaded
    'RequestTimeout',     # Request timed out
    'OperationAborted',   # Conflict with another operation
}


def s3_retry(max_retries: int = 5, base_delay: float = 0.5):
    """
    Decorator for S3 operations with exponential backoff + jitter.

    Retryable errors:
    - SlowDown (503): rate limiting, back off aggressively
    - InternalError / ServiceUnavailable: transient, retry
    - ClientError with retryable error codes
    - ConnectionError / EndpointConnectionError

    NOT retryable:
    - AccessDenied (403): permissions issue, retrying won't help
    - NoSuchBucket / NoSuchKey: resource doesn't exist
    - InvalidAccessKeyId: credential configuration issue
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)

                except botocore.exceptions.ClientError as e:
                    error_code = e.response.get('Error', {}).get('Code', '')
                    http_status = e.response.get('ResponseMetadata',
                                                  {}).get('HTTPStatusCode', 0)

                    # Non-retryable errors — fail immediately
                    if error_code in ('AccessDenied', 'InvalidAccessKeyId',
                                      'SignatureDoesNotMatch',
                                      'NoSuchBucket', 'NoSuchKey',
                                      'NotFound', 'ExpiredToken'):
                        logger.error(
                            f"Non-retryable S3 error: {error_code} — "
                            f"Status: {http_status} — {e}"
                        )
                        raise

                    # Rate limiting — use longer backoff
                    if error_code == 'SlowDown' or http_status == 503:
                        backoff = base_delay * (2 ** attempt) + random.uniform(0, 1)
                        logger.warning(
                            f"S3 SlowDown (attempt {attempt + 1}/{max_retries + 1})"
                            f" — backing off {backoff:.1f}s"
                        )
                    elif error_code in RETRYABLE_CODES:
                        backoff = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
                        logger.warning(
                            f"S3 transient error '{error_code}' "
                            f"(attempt {attempt + 1}/{max_retries + 1})"
                            f" — retrying in {backoff:.1f}s"
                        )
                    else:
                        # Unknown error — log and retry cautiously
                        backoff = base_delay * (2 ** attempt)
                        logger.warning(
                            f"S3 unknown error '{error_code}' "
                            f"(attempt {attempt + 1}) — retrying in {backoff:.1f}s"
                        )

                    last_exception = e

                    if attempt < max_retries:
                        time.sleep(backoff)
                    else:
                        raise

                except (botocore.exceptions.ConnectionError,
                        botocore.exceptions.EndpointConnectionError) as e:
                    backoff = base_delay * (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(
                        f"S3 connection error (attempt {attempt + 1})"
                        f" — retrying in {backoff:.1f}s: {e}"
                    )
                    last_exception = e

                    if attempt < max_retries:
                        time.sleep(backoff)
                    else:
                        raise

            raise last_exception  # Should not reach here

        return wrapper
    return decorator


class S3Client:
    """
    Wrapper around boto3 S3 client with built-in retries,
    metrics collection, and exponential backoff.
    """

    def __init__(
        self,
        region: str = 'us-east-1',
        max_attempts: int = 5,
        connect_timeout: int = 5,
        read_timeout: int = 30,
    ):
        # Botocore-level retry configuration (complements our own retry)
        botocore_config = botocore.config.Config(
            retries={
                'max_attempts': max_attempts,
                'mode': 'adaptive',  # adaptive = dynamic throttling aware
            },
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            tcp_keepalive=True,
        )

        self.client = boto3.client('s3', region_name=region,
                                   config=botocore_config)
        self.stats = {'gets': 0, 'puts': 0, 'errors': 0, 'retries': 0}

    @s3_retry(max_retries=5, base_delay=0.5)
    def get_object(self, bucket: str, key: str) -> bytes:
        """Get an object with automatic retry."""
        start = time.time()
        try:
            response = self.client.get_object(Bucket=bucket, Key=key)
            data = response['Body'].read()
            elapsed = time.time() - start
            self.stats['gets'] += 1
            logger.debug(f"GET s3://{bucket}/{key} — {len(data)} bytes in {elapsed:.3f}s")
            return data
        except Exception:
            self.stats['errors'] += 1
            raise

    @s3_retry(max_retries=5, base_delay=0.5)
    def put_object(self, bucket: str, key: str, data: bytes,
                   content_type: str = None,
                   metadata: dict = None) -> dict:
        """Put an object with automatic retry."""
        start = time.time()
        kwargs = {'Bucket': bucket, 'Key': key, 'Body': data}
        if content_type:
            kwargs['ContentType'] = content_type
        if metadata:
            kwargs['Metadata'] = metadata

        try:
            response = self.client.put_object(**kwargs)
            elapsed = time.time() - start
            self.stats['puts'] += 1
            logger.debug(f"PUT s3://{bucket}/{key} — {len(data)} bytes in {elapsed:.3f}s")
            return response
        except Exception:
            self.stats['errors'] += 1
            raise

    def upload_with_multipart(self, bucket: str, key: str,
                               file_path: str, part_size_mb: int = 8):
        """
        Multipart upload for large files with automatic retry on each part.
        Handles SlowDown by reducing concurrency when throttled.
        """
        import os
        from concurrent.futures import ThreadPoolExecutor, as_completed

        file_size = os.path.getsize(file_path)
        part_size = part_size_mb * 1024 * 1024
        total_parts = (file_size + part_size - 1) // part_size

        # Initiate multipart upload
        mpu = self.client.create_multipart_upload(
            Bucket=bucket, Key=key,
            ContentType='application/octet-stream'
        )
        upload_id = mpu['UploadId']
        parts = []

        try:
            def upload_part(part_number: int, data: bytes) -> dict:
                @s3_retry(max_retries=5, base_delay=1.0)
                def _upload():
                    return self.client.upload_part(
                        Bucket=bucket, Key=key,
                        UploadId=upload_id,
                        PartNumber=part_number,
                        Body=data,
                    )
                response = _upload()
                return {
                    'PartNumber': part_number,
                    'ETag': response['ETag']
                }

            with open(file_path, 'rb') as f:
                with ThreadPoolExecutor(max_workers=4) as executor:
                    futures = {}
                    for part_num in range(1, total_parts + 1):
                        data = f.read(part_size)
                        futures[
                            executor.submit(upload_part, part_num, data)
                        ] = part_num

                    for future in as_completed(futures):
                        result = future.result()
                        parts.append(result)
                        logger.debug(
                            f"Part {result['PartNumber']}/{total_parts} uploaded"
                        )

            # Sort and complete
            parts.sort(key=lambda p: p['PartNumber'])
            self.client.complete_multipart_upload(
                Bucket=bucket, Key=key,
                UploadId=upload_id,
                MultipartUpload={'Parts': parts}
            )
            logger.info(f"Multipart upload complete: s3://{bucket}/{key}")

        except Exception:
            # Abort on any failure to avoid incomplete multipart uploads
            self.client.abort_multipart_upload(
                Bucket=bucket, Key=key, UploadId=upload_id
            )
            logger.error(f"Multipart upload aborted: s3://{bucket}/{key}")
            raise


# ── Usage Example ────────────────────────────────────────────────

if __name__ == '__main__':
    s3 = S3Client(region='us-east-1')

    try:
        # Get an object (with retry)
        data = s3.get_object('my-bucket', 'config/settings.json')
        print(f"Read {len(data)} bytes")

        # Put an object (with retry)
        s3.put_object('my-bucket', 'results/output.json', b'{"status":"ok"}')

        # Large file multipart upload (with part-level retry)
        s3.upload_with_multipart('my-bucket', 'data/large-dataset.parquet',
                                 '/local/path/to/large-dataset.parquet')

        # Print stats
        print(f"Stats: {s3.stats}")

    except botocore.exceptions.ClientError as e:
        print(f"S3 Error: {e}")
```

---

## VPC Endpoint Troubleshooting

### Gateway vs Interface Endpoints

```text
GATEWAY ENDPOINT (for S3 and DynamoDB):
  - FREE
  - Added to route table (not a network interface)
  - Uses S3's public IP range (not private IPs)
  - Route table entry: pl-xxx (prefix list) → vpce-xxx
  - Does NOT work across VPC peering / Transit Gateway
  - Does NOT work from on-premises via VPN/Direct Connect

INTERFACE ENDPOINT (for all other AWS services):
  - $0.01/hour per AZ + $0.01/GB processed
  - Creates an ENI with private IP in your subnet
  - Private DNS: automatically overrides service's public DNS
    Example: s3.us-east-1.amazonaws.com → private IP of endpoint ENI
  - Works across VPC peering, Transit Gateway, VPN, Direct Connect
  - Requires security group rules allowing inbound HTTPS (443)
```

### Diagnosing VPC Endpoint Issues

```bash
# 1. Does the endpoint exist?
aws ec2 describe-vpc-endpoints \
  --filters Name=vpc-id,Values=vpc-xxx \
  --query "VpcEndpoints[*].{ID:VpcEndpointId,Service:ServiceName,State:State}"

# 2. Is the route table entry correct? (gateway endpoints)
aws ec2 describe-route-tables \
  --route-table-ids rtb-xxx \
  --query "RouteTables[0].Routes[?VpcEndpointId]"

# 3. Is the security group allowing traffic? (interface endpoints)
aws ec2 describe-security-groups \
  --group-ids sg-xxx \
  --query "SecurityGroups[0].IpPermissions"

# 4. Is Private DNS enabled?
aws ec2 describe-vpc-endpoints --vpc-endpoint-ids vpce-xxx \
  --query "VpcEndpoints[0].PrivateDnsEnabled"

# 5. Test DNS resolution from within VPC (should resolve to private IP):
dig s3.us-east-1.amazonaws.com
# If using gateway endpoint: resolves to public S3 IPs (expected)
# If using interface endpoint with PrivateDNS: resolves to private ENI IPs
```

### Scenario: "S3 Access Works from EC2 But Not from On-Prem"

```text
SYMPTOM: "We have a Gateway VPC Endpoint for S3 in our VPC.
         EC2 instances can access S3 without internet. But our
         on-premises servers connected via Direct Connect
         cannot access S3 through the VPC endpoint."

INVESTIGATION:
  VPC: 10.0.0.0/16
  Gateway Endpoint for S3: attached to route tables in private subnets
  Direct Connect: connected, on-prem routes to VPC work for EC2

  On-prem server: 192.168.1.100 → Direct Connect → VPC → tries to reach S3

  Route table for private subnets:
    10.0.0.0/16 → local
    0.0.0.0/0   → NAT Gateway
    pl-xxx → vpce-xxx  (S3 gateway endpoint)

  On-prem traffic: source IP = 192.168.1.100
  Gateway endpoint: only works for traffic ORIGINATING from within the VPC.
  Traffic from 192.168.1.100 goes through Direct Connect, enters the VPC,
  but does NOT originate from a VPC ENI.

ROOT CAUSE: Gateway VPC endpoints are NOT accessible from outside the VPC
(via VPN, Direct Connect, peering, Transit Gateway). Only resources
WITHIN the VPC with an ENI can use gateway endpoints.

FIX: Use an Interface VPC Endpoint for S3 instead.
  - Creates ENIs in the VPC with private IPs
  - Accessible from on-prem via Direct Connect/VPN
  - Enable Private DNS so on-prem DNS resolves S3 to the private IP
  - Cost: ~$0.01/hr per AZ + data processing
  - Also works across VPC peering and Transit Gateway
```

---

## References

- [AWS S3 Troubleshooting Guide](https://docs.aws.amazon.com/AmazonS3/latest/userguide/troubleshooting.html)
- [S3 Error Responses](https://docs.aws.amazon.com/AmazonS3/latest/API/ErrorResponses.html)
- [S3 Request Rate and Performance](https://docs.aws.amazon.com/AmazonS3/latest/userguide/optimizing-performance.html)
- [S3 Consistency Model](https://docs.aws.amazon.com/AmazonS3/latest/userguide/Welcome.html#ConsistencyModel)
- [S3 Presigned URLs](https://docs.aws.amazon.com/AmazonS3/latest/userguide/PresignedUrlUploadObject.html)
- [S3 Lifecycle Policies](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lifecycle-mgmt.html)
- [VPC Endpoints for S3](https://docs.aws.amazon.com/vpc/latest/privatelink/vpc-endpoints-s3.html)
- [boto3 S3 Documentation](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html)
