# Secrets Leaked Response
> **Category:** Security | Secrets Management
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#security` `#secrets` `#emergency`

---

## The Golden Rule

> **REVOKE FIRST. Fix commit second.** A secret pushed to a public GitHub repo is compromised the instant the push completes. Git history is immutable and public. Bots scrape GitHub commits continuously — your secret is already in someone's database within 60 seconds. Revoke the credential, then deal with the git history.

---

## Detection Methods

### GitHub Advanced Security (Built-in)

GitHub scans for 200+ secret patterns automatically on public repos. Patterns include AWS keys, GCP service accounts, Azure keys, private keys, JWTs, connection strings, and more.

```bash
# Check secret scanning alerts via GitHub API
gh api /repos/OWNER/REPO/secret-scanning/alerts --jq '.[] | {secret: .secret_type, state: .state, created: .created_at}'

# Enable push protection (blocks commits containing secrets)
# In repo Settings → Code Security → Secret Scanning → Push Protection
```

### Open-Source Scanners

```bash
# git-secrets — prevents committing secrets (pre-commit hook)
brew install git-secrets
git secrets --install
git secrets --register-aws  # Add AWS key patterns
git secrets --scan-history  # Scan entire git history

# truffleHog — finds high-entropy strings (likely secrets) in git history
pip install trufflehog3
trufflehog3 --repo-path /path/to/repo --format json --output trufflehog_report.json

# detect-secrets — Yelp's tool, pre-commit hook
pip install detect-secrets
detect-secrets scan --all-files > .secrets.baseline
detect-secrets audit .secrets.baseline  # Interactive review of findings

# gitleaks — fast, CI-friendly
brew install gitleaks
gitleaks detect --source . --report-format json --report-path gitleaks.json
```

### CI/CD Pipeline Integration

```yaml
# .github/workflows/secret-scan.yml
name: Secret Scan
on: [push, pull_request]
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
        with:
          fetch-depth: 0  # Full history for scanning

      - name: Run gitleaks
        uses: gitleaks/gitleaks-action@v2
        env:
          GITLEAKS_LICENSE: ${{ secrets.GITLEAKS_LICENSE }}

      - name: Run git-secrets
        run: |
          git secrets --install
          git secrets --register-aws
          git secrets --scan-history
```

---

## Rotation Playbook — Priority Order

### CRITICAL FIRST (Revoke within 5 minutes)

These credentials cause immediate catastrophic damage if compromised:

| Credential Type | Revocation Mechanism | Verification |
|----------------|---------------------|--------------|
| AWS Access Keys / Secret Keys | `aws iam update-access-key --status Inactive` | `aws iam list-access-keys --user USER` |
| Production DB master passwords | `ALTER USER ... PASSWORD 'new';` then `aws secretsmanager rotate-secret` | Attempt connection with old password |
| Stripe/Payment API keys | Stripe Dashboard → Developers → API Keys → Roll key | Check webhook deliveries |
| Private TLS/SSL keys | Issue new cert, revoke old via ACM/CA | `openssl verify -CAfile chain.pem cert.pem` |
| GCP Service Account keys | `gcloud iam service-accounts keys delete KEY_ID` | `gcloud iam service-accounts keys list` |
| Azure Storage Account keys | `az storage account keys renew --key primary` | Check access with old key |

### HIGH (Within 1 hour)

| Credential Type | Risk if Leaked | Revocation |
|----------------|---------------|------------|
| Staging DB credentials | Access to PII-like test data | Same as production rotation |
| CI/CD deploy keys | Attacker can push malicious code | Delete from GitHub/GitLab deploy keys; check all repos |
| Docker registry credentials | Attacker can push poisoned images | Rotate in registry, update all CI/CD pipelines |
| Slack/Teams webhooks | Attacker can exfiltrate data to external channel | Delete webhook URL, generate new |
| Datadog/NewRelic API keys | Read metrics, dashboards, logs (info disclosure) | Rotate in provider console |

### MEDIUM (Within 24 hours)

| Credential Type | Rotation |
|----------------|----------|
| Feature flag service tokens (LaunchDarkly, Split) | Rotate in provider dashboard |
| Logging service auth tokens (LogDNA, Papertrail) | Regenerate in provider |
| Monitoring API keys (status page, uptime checker) | Rotate, update health check configs |
| Sentry DSN / auth tokens | Rotate in Sentry settings |

### LOW (Next sprint)

| Credential Type | Rotation |
|----------------|----------|
| Dev environment credentials | Rotate, update local `.env` files |
| Code coverage service tokens (Codecov, Coveralls) | Regenerate |
| Documentation site API keys | Rotate |

---

## AWS Secret Rotation Procedures

### Access Key Rotation (Complete Playbook)

```bash
# Step 1: Create a new access key
aws iam create-access-key --user-name app-service-user
# Output: { "AccessKey": { "AccessKeyId": "AKIA_NEW...", "SecretAccessKey": "wJalrX_NEW..." } }
# SAVE THE SECRET KEY NOW — it won't be shown again

# Step 2: Update application with new credentials
#   - Update AWS Secrets Manager secret value
#   - Update K8s secret
#   - Update ECS task definition (new revision)
#   - Or update Vault dynamic secrets config
kubectl create secret generic aws-creds \
  --from-literal=AWS_ACCESS_KEY_ID=AKIA_NEW \
  --from-literal=AWS_SECRET_ACCESS_KEY=wJalrX_NEW \
  -n production --dry-run=client -o yaml | kubectl apply -f -
kubectl rollout restart deployment/app -n production

# Step 3: Verify new key works
aws sts get-caller-identity --profile new-key

# Step 4: Deactivate old key (keep for rollback window)
aws iam update-access-key \
  --access-key-id AKIA_OLD \
  --status Inactive \
  --user-name app-service-user

# Step 5: After 24h verification, delete old key
aws iam delete-access-key \
  --access-key-id AKIA_OLD \
  --user-name app-service-user
```

### AWS Secrets Manager Auto-Rotation

```bash
# Enable automatic rotation (30-day cycle, Lambda function handles the actual rotation)
aws secretsmanager rotate-secret \
  --secret-id prod/database/master \
  --rotation-lambda-arn arn:aws:lambda:us-east-1:123456789:function:rotate-rds-secret \
  --rotation-rules AutomaticallyAfterDays=30

# Force immediate rotation
aws secretsmanager rotate-secret --secret-id prod/database/master --rotate-immediately

# Check rotation status
aws secretsmanager describe-secret --secret-id prod/database/master
# Look for: "RotationEnabled": true, "LastRotatedDate"

# List all secrets not rotated in 90+ days (stale secrets)
aws secretsmanager list-secrets --query 'SecretList[?LastRotatedDate<`2026-03-13`].{Name:Name,LastRotated:LastRotatedDate}' --output table
```

### RDS Master Password Emergency Change

```bash
# Change the password directly
aws rds modify-db-instance \
  --db-instance-identifier prod-db-1 \
  --master-user-password "NEW_64_CHAR_RANDOM_PASSWORD" \
  --apply-immediately

# NOTE: This causes a brief outage (~30s) as RDS restarts with the new password.
# Applications using IAM database auth or Secrets Manager will auto-refresh.

# After RDS change, update Secrets Manager secret value
aws secretsmanager put-secret-value \
  --secret-id prod/database/master \
  --secret-string '{"username":"admin","password":"NEW_64_CHAR_RANDOM_PASSWORD","host":"prod-db-1.xxx.us-east-1.rds.amazonaws.com","port":5432}'

# Trigger immediate rotation so apps get the new value
aws secretsmanager rotate-secret --secret-id prod/database/master --rotate-immediately
```

### EC2 Key Pair Compromise

EC2 key pairs cannot be rotated on existing instances. You must replace instances.

```bash
# Create new key pair
aws ec2 create-key-pair --key-name prod-key-pair-v2 --query 'KeyMaterial' --output text > prod-key-pair-v2.pem
chmod 400 prod-key-pair-v2.pem

# For Auto Scaling Groups: update launch template
aws ec2 create-launch-template-version \
  --launch-template-name prod-lt \
  --source-version 1 \
  --launch-template-data '{"KeyName":"prod-key-pair-v2"}'

# Initiate instance refresh (rolling replacement of all instances)
aws autoscaling start-instance-refresh \
  --auto-scaling-group-name prod-asg \
  --preferences '{"MinHealthyPercentage": 80, "InstanceWarmup": 300}'
```

---

## Vault / HashiCorp Secret Revocation

```bash
# Revoke all database credentials issued for a specific role
# This invalidates ALL active leases for those credentials
vault lease revoke -prefix database/creds/api-role

# Revoke a specific lease
vault lease revoke database/creds/api-role/abcd1234-5678-90ef-ghij

# Revoke all tokens from a specific auth method (e.g., if Okta/K8s auth is compromised)
vault token revoke -mode path auth/kubernetes

# Revoke a specific token
vault token revoke s.YOUR_TOKEN_HERE

# Audit: see who accessed what (requires audit device enabled)
vault audit list
# Check audit logs:
sudo cat /var/log/vault/audit.log | jq 'select(.request.path | startswith("database/creds")) | {time: .time, client: .request.remote_address, path: .request.path}'

# Force rotation of a static role (RDS master password)
vault write -force database/rotate-root/postgres-prod

# List all current leases
vault list sys/leases/lookup/database/creds/api-role
```

---

## Scenario: AWS Access Key Found in Public S3 Bucket

**Initial Detection:**
- AWS Trusted Advisor alert: "S3 bucket 'app-logs-archive' has public read access"
- Security team reviews bucket contents → finds `config/deploy.sh` containing hardcoded `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`

**Investigation Timeline:**

```bash
# T+1min: Verify bucket is public
aws s3api get-bucket-acl --bucket app-logs-archive
# Output shows: <Grant><Grantee ...>AllUsers</Grantee><Permission>READ</Permission></Grant>

# T+2min: Check what the key was used for (IAM access advisor)
aws iam generate-service-last-accessed-details \
  --arn arn:aws:iam::123456789:user/deploy-script-user

aws iam get-service-last-accessed-details \
  --job-id <job-id> \
  --query 'ServicesLastAccessed[?TotalAuthenticatedEntities>0]'

# Output reveals: ec2.amazonaws.com, s3.amazonaws.com — in ALL regions

# T+3min: Check for unauthorized EC2 instances (crypto mining signature)
for region in $(aws ec2 describe-regions --query 'Regions[*].RegionName' --output text); do
  instances=$(aws ec2 describe-instances --region $region \
    --filters "Name=instance-state-name,Values=running" \
    --query 'Reservations[*].Instances[*].InstanceId' --output text)
  if [ -n "$instances" ]; then
    echo "REGION $region: $instances"
  fi
done
# Found: 50 c5n.18xlarge instances in ap-northeast-1 (Japan) — NEVER used by our org
# Found: 30 p3.16xlarge instances in eu-west-2 (London) — GPU instances for crypto mining
```

**Response:**

```bash
# 1. IMMEDIATELY deactivate and delete the leaked access key
aws iam update-access-key --access-key-id AKIA_LEAKED_KEY --status Inactive --user-name deploy-script-user
aws iam delete-access-key --access-key-id AKIA_LEAKED_KEY --user-name deploy-script-user

# 2. Block public access to the S3 bucket
aws s3api put-public-access-block --bucket app-logs-archive \
  --public-access-block-configuration \
  '{"BlockPublicAcls":true,"IgnorePublicAcls":true,"BlockPublicPolicy":true,"RestrictPublicBuckets":true}'

# 3. Terminate all unauthorized instances
for region in ap-northeast-1 eu-west-2; do
  aws ec2 describe-instances --region $region \
    --filters "Name=instance-state-name,Values=running" \
    --query 'Reservations[*].Instances[*].InstanceId' --output text | \
    xargs -r aws ec2 terminate-instances --region $region --instance-ids
done

# 4. Apply SCP to deny EC2 in regions we don't use
aws organizations create-policy \
  --name "deny-ec2-non-production-regions" \
  --type SERVICE_CONTROL_POLICY \
  --description "Prevent EC2 in non-authorized regions" \
  --content '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Deny",
      "Action": "ec2:*",
      "Resource": "*",
      "Condition": {
        "StringNotEquals": {
          "aws:RequestedRegion": ["us-east-1", "us-west-2", "eu-west-1"]
        }
      }
    }]
  }'

# 5. Enable S3 Block Public Access at ACCOUNT level
aws s3control put-public-access-block \
  --account-id 123456789 \
  --public-access-block-configuration \
  '{"BlockPublicAcls":true,"IgnorePublicAcls":true,"BlockPublicPolicy":true,"RestrictPublicBuckets":true}'
```

**Financial Impact:** $145,000 in unauthorized EC2 charges (AWS credited back after incident report).

---

## Python: GitHub Secret Scanner

```python
#!/usr/bin/env python3
"""
github_secret_scanner.py — Scans a local git repository for secrets patterns.
Detects AWS keys, private keys, connection strings, tokens, and other sensitive patterns.
Usage: python github_secret_scanner.py --repo-path /path/to/repo
"""

import re
import os
import sys
import json
import subprocess
from argparse import ArgumentParser
from typing import List, Dict, Tuple

PATTERNS: List[Tuple[str, str, re.Pattern]] = [
    # (name, description, regex)
    ("AWS Access Key ID", "AWS AKIA-prefixed access key",
     re.compile(r'(?:A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}')),
    ("AWS Secret Key", "AWS secret access key (high entropy heuristic)",
     re.compile(r'(?i)aws.{0,20}(?:secret).{0,20}([\'"]?)([a-zA-Z0-9\/+=]{40})(\1)')),
    ("RSA Private Key", "-----BEGIN RSA PRIVATE KEY----- block",
     re.compile(r'-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----')),
    ("GitHub Token", "GitHub personal access token (ghp_ prefix)",
     re.compile(r'ghp_[a-zA-Z0-9]{36}')),
    ("Generic API Key", "Hex string labeled as key/token/secret",
     re.compile(r'(?i)(?:api[_-]?key|token|secret|password).{0,10}[\'":=]\s*[\'"]?([a-zA-Z0-9_\-\.]{20,60})[\'"]?')),
    ("PostgreSQL URL", "Database connection string with credentials",
     re.compile(r'postgres(?:ql)?://[^:@]+:[^@]+@[^/\s]+\/[^\s"\'<]+')),
    ("MongoDB URL", "MongoDB connection string with credentials",
     re.compile(r'mongodb(?:\+srv)?://[^:@]+:[^@]+@[^/\s]+\/[^\s"\'<]+')),
    ("Redis URL", "Redis connection string with password",
     re.compile(r'redis://[^:@]*:[^@]+@[^\s"\'<]+')),
    ("JWT Token", "JSON Web Token (three base64url segments)",
     re.compile(r'eyJ[a-zA-Z0-9_\-]{20,}\.eyJ[a-zA-Z0-9_\-]{20,}\.[a-zA-Z0-9_\-]{20,}')),
    ("Slack Webhook", "Slack incoming webhook URL",
     re.compile(r'https://hooks\.slack\.com/services/T[a-zA-Z0-9_]+/B[a-zA-Z0-9_]+/[a-zA-Z0-9_]+')),
    ("Stripe Live Key", "Stripe live secret key",
     re.compile(r'sk_live_[a-zA-Z0-9]{24,}')),
    ("Google API Key", "Google API key (AIza prefix)",
     re.compile(r'AIza[0-9A-Za-z\-_]{35}')),
]


def get_git_tracked_files(repo_path: str) -> List[str]:
    """Get list of all files tracked by git (not .gitignore'd)."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            capture_output=True, text=True, cwd=repo_path, timeout=30
        )
        return [f for f in result.stdout.splitlines() if f]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def should_skip_file(filepath: str) -> bool:
    """Skip binary files and vendored dependencies."""
    skip_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.ico', '.woff', '.woff2',
                       '.ttf', '.eot', '.mp3', '.mp4', '.zip', '.tar', '.gz', '.pdf',
                       '.exe', '.dll', '.so', '.dylib', '.pyc', '.class', '.jar',
                       '.whl', '.egg', '.lock'}
    skip_dirs = {'node_modules', 'vendor', '.git', '__pycache__', '.terraform',
                 'bower_components', '.venv', 'venv', 'dist', 'build'}
    ext = os.path.splitext(filepath)[1].lower()
    if ext in skip_extensions:
        return True
    parts = filepath.replace(os.sep, '/').split('/')
    return any(d in skip_dirs for d in parts)


def scan_file(filepath: str, full_path: str) -> List[Dict]:
    """Scan a single file for secret patterns."""
    findings = []
    try:
        with open(full_path, 'r', errors='ignore') as f:
            lines = f.readlines()
    except (IOError, PermissionError):
        return findings

    for line_no, line in enumerate(lines, 1):
        for name, desc, pattern in PATTERNS:
            for match in pattern.finditer(line):
                findings.append({
                    "file": filepath,
                    "line": line_no,
                    "pattern": name,
                    "description": desc,
                    "match": mask_secret(match.group(0)),
                })
    return findings


def mask_secret(value: str) -> str:
    """Show first 4 + last 4 chars, mask the middle."""
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "*" * (len(value) - 8) + value[-4:]


def scan_repo(repo_path: str) -> List[Dict]:
    """Scan entire repository for secrets."""
    all_findings = []
    all_files = get_git_tracked_files(repo_path)

    print(f"Scanning {len(all_files)} tracked files in {repo_path}...", file=sys.stderr)

    for filepath in all_files:
        if should_skip_file(filepath):
            continue
        full_path = os.path.join(repo_path, filepath)
        if not os.path.isfile(full_path) or os.path.getsize(full_path) > 10_000_000:
            continue
        findings = scan_file(filepath, full_path)
        all_findings.extend(findings)

    return all_findings


def main():
    parser = ArgumentParser(description="Scan a git repo for leaked secrets patterns")
    parser.add_argument("--repo-path", default=".", help="Path to git repository")
    parser.add_argument("--output", default="-", help="Output JSON file (- for stdout)")
    parser.add_argument("--severity", choices=["all", "critical-only"], default="all",
                        help="Filter by severity")
    args = parser.parse_args()

    findings = scan_repo(os.path.abspath(args.repo_path))

    output = {
        "scan_timestamp": subprocess.run(["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"],
                                         capture_output=True, text=True).stdout.strip(),
        "repo_path": args.repo_path,
        "findings_count": len(findings),
        "findings": sorted(findings, key=lambda x: (x["file"], x["line"])),
    }

    json_output = json.dumps(output, indent=2)

    if args.output == "-":
        print(json_output)
    else:
        with open(args.output, 'w') as f:
            f.write(json_output)
        print(f"Report written to {args.output}", file=sys.stderr)

    if findings:
        print(f"\n{len(findings)} potential secrets found!", file=sys.stderr)
        for f in findings:
            print(f"  {f['pattern']}: {f['file']}:{f['line']} → {f['match']}", file=sys.stderr)
        sys.exit(1)
    else:
        print("No secrets detected.", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
```

---

## Bash: git-secrets History Scan Wrapper

```bash
#!/bin/bash
# git-secrets-scan-all.sh — Wrapper to scan all branches and all commits
# Usage: ./git-secrets-scan-all.sh [repo-path]
set -euo pipefail

REPO_PATH="${1:-.}"

echo "🔍 Scanning all branches for secrets in: $REPO_PATH"
echo "===================================================="

# Ensure git-secrets is installed
if ! command -v git-secrets &>/dev/null; then
    echo "❌ git-secrets not installed. Install: brew install git-secrets"
    exit 1
fi

# Register AWS patterns (always a good start)
git secrets --register-aws

# Register custom patterns for your org
git secrets --add 'ghp_[a-zA-Z0-9]{36}'                    # GitHub PAT
git secrets --add '-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----'  # Private keys
git secrets --add 'postgres(ql)?://[^:@]+:[^@]+@'          # DB connection strings
git secrets --add 'redis://[^:@]*:[^@]+@'                  # Redis with auth
git secrets --add 'sk_live_[a-zA-Z0-9]{24,}'               # Stripe live keys
git secrets --add '(?i)(api[_-]?key|token|secret|password).{0,10}[\'":=]\s*[\'"]?[a-zA-Z0-9_\-]{20,}'  # Generic secrets

# Get all branches (local + remote tracking)
echo -e "\n📋 Branches to scan:"
git branch -a | sed 's/^..//'

# Scan each branch
git branch -a | sed 's/^..//' | while read -r branch; do
    # Trim leading remotes/ prefix for display
    display_name="${branch#remotes/origin/}"
    echo -e "\n🔍 Scanning branch: $display_name..."

    # For remote branches, check them out temporarily
    if [[ "$branch" == remotes/* ]]; then
        git checkout --detach "$branch" 2>/dev/null || { echo "  ⚠️ Could not checkout $branch"; continue; }
    fi

    # Scan the entire history of this branch
    if git secrets --scan-history 2>&1 | grep -q 'ERROR'; then
        echo "  🚨 SECRETS FOUND in $display_name!"
    else
        echo "  ✅ Clean: $display_name"
    fi
done

# Return to original branch
git checkout - 2>/dev/null || true

echo -e "\n✅ Scan complete."
```

---

## Prevention Checklist

- [ ] `git-secrets` pre-commit hook installed globally on all developer machines
- [ ] CI pipeline runs `gitleaks` or `truffleHog` on every PR
- [ ] GitHub Push Protection enabled on all repos
- [ ] All production credentials stored in Vault or AWS Secrets Manager
- [ ] IAM roles for EC2/ECS/Lambda instead of access keys (where possible)
- [ ] Access key auto-rotation every 90 days (IAM policy enforcement)
- [ ] S3 Block Public Access enabled at account level
- [ ] CloudTrail logging enabled in all regions with log file validation
- [ ] Alert on `PutBucketPolicy`, `ModifySnapshotAttribute`, `CreateAccessKey` CloudTrail events
- [ ] No `.env` files in git (use `.env.example` with placeholder values)
- [ ] `.gitignore` includes `.env`, `*.pem`, `*.key`, `credentials.json`
