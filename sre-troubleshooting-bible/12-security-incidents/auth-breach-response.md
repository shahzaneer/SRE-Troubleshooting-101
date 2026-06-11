# Auth Breach Response
> **Category:** Security | Incident Response
> **Difficulty:** Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#security` `#auth` `#breach` `#emergency`

---

## Detecting Credential Stuffing

Credential stuffing is the #1 auth attack vector. Attackers use credentials leaked from other breaches, betting on password reuse.

### Signs of Credential Stuffing

| Signal | What to Look For |
|--------|-----------------|
| Spike in 401 responses | 10x normal failed login rate in < 5 minutes |
| Many usernames, few IPs | Single IP tries 500+ distinct usernames |
| Low success rate | < 0.1% of attempts succeed (real users: 85-95%) |
| Unusual device/browser fingerprint | Python `requests` User-Agent, missing JS challenge cookie |
| Geographic mismatch | Login from Romania for a user who logged in from Chicago 10 min ago |
| Burst pattern | 50 attempts in 1 second, 0 for 10 seconds, repeat (automation) |

### Detection Commands

```bash
# Top IPs returning 401 on nginx — credential stuffing indicator
awk '$9==401 {print $1}' /var/log/nginx/access.log | sort | uniq -c | sort -rn | head -20

# Check if a specific IP hit many different usernames (credential stuffing signature)
grep "192.168.1.100" /var/log/nginx/access.log | awk '{print $NF}' | sort -u | wc -l
# If output > 20 unique usernames from one IP → high probability of credential stuffing

# Real-time tail watching for auth failures (staging/env dependant — adapt endpoint)
tail -f /var/log/auth.log | grep --line-buffered "Failed password"

# Parse structured auth logs (JSON assumed) — find spike windows
cat /var/log/auth.json | jq 'select(.status==401) | {ts: .timestamp, ip: .client_ip, user: .username}' \
  | jq -s 'group_by(.ip) | map({ip: .[0].ip, count: length}) | sort_by(-.count) | .[0:20]'
```

### Scenario: Real Credential Stuffing Attack

**Timeline:**
- 03:14 UTC — Normal login rate: 15/min
- 03:17 UTC — Login rate jumps to 800/min from 3 IPs (all AS4134, China Telecom)
- 03:17:30 UTC — 500 unique usernames tried from IP 103.235.46.191
- 03:18 UTC — 2 successful logins detected (users reused passwords from LinkedIn 2021 breach)

**Diagnosis:**
```bash
# Step 1: Confirm attack pattern
awk '$9==401' /var/log/nginx/access.log | awk '{print $1}' | sort | uniq -c | sort -rn | head -10
# 103.235.46.191 — 487
# 103.235.46.192 — 234
# 103.235.46.193 — 79

# Step 2: Check for successful logins from those IPs (the 2 breached accounts)
awk '$9==200 && /login/' /var/log/nginx/access.log | grep -E "103\.235\.46\.(191|192|193)"
# Found 2 POST /api/login with 200 — accounts: jdoe@corp.com, asmith@corp.com

# Step 3: Check what those users accessed after login
grep -E "jdoe@corp.com|asmith@corp.com" /var/log/app/audit.log
# jdoe@corp.com accessed /api/admin/users/export at 03:19 UTC (data exfiltration)
```

---

## Immediate Response Playbook

### T-0: Stop the Bleeding (< 2 minutes)

```bash
# 1. Block attacker IPs at WAF/security group level
# AWS WAF: Update IP set
aws wafv2 update-ip-set --name ATTACKER_IPS --scope REGIONAL --addresses \
  103.235.46.191/32 103.235.46.192/32 103.235.46.193/32 \
  --id <ip-set-id> --lock-token <lock-token>

# Or block via security group (faster, but less scalable):
aws ec2 revoke-security-group-ingress --group-id sg-xxx \
  --ip-permissions "IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{CidrIp='103.235.46.191/32'}]"

# 2. Force logout ALL users (invalidate all sessions/tokens)
# PostgreSQL sessions table
psql -h $DB_HOST -U admin -d production -c "UPDATE sessions SET valid = false, invalidated_at = NOW();"

# Redis session store — flush all
redis-cli -h $REDIS_HOST -p 6379 FLUSHALL

# JWT — if you use a signing key rotation mechanism, rotate the key immediately
# This invalidates all existing JWTs globally
curl -X POST https://vault.internal/v1/transit/keys/jwt-signing-key/rotate \
  -H "X-Vault-Token: $VAULT_TOKEN"

# 3. Enable CAPTCHA on login (via feature flag if available)
# If using Cloudflare:
curl -X PATCH "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/settings/security_level" \
  -H "Authorization: Bearer $CF_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"value":"under_attack"}'
```

### T-5: Contain and Notify

```bash
# Disable compromised user accounts
aws iam update-login-profile --user-name jdoe --no-password-reset-required
# Or for application users (DB):
psql -c "UPDATE users SET locked = true, locked_reason = 'security_breach_20260611' WHERE email IN ('jdoe@corp.com', 'asmith@corp.com');"

# Increase rate limiting — Nginx example:
# Add to nginx.conf and reload:
# limit_req_zone $binary_remote_addr zone=login:10m rate=1r/s;
# location /api/login { limit_req zone=login burst=3 nodelay; }

# Notify security team via PagerDuty
pd incident:create --service "Security" --description "Credential stuffing - 2 accounts breached" \
  --severity critical --from "oncall-bot"
```

---

## CloudTrail Forensics

### Finding the Breach Timeline

```bash
# Look for ConsoleLogin events in the incident window
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=ConsoleLogin \
  --start-time 2026-06-10T00:00:00Z \
  --end-time 2026-06-11T00:00:00Z \
  --query 'Events[*].CloudTrailEvent' \
  --output text | jq -r '[.userIdentity.userName, .eventTime, .sourceIPAddress, .responseElements.ConsoleLogin] | @csv'

# Look for IAM credential creation (attacker creating backdoor access)
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=CreateAccessKey \
  --start-time 2026-06-01T00:00:00Z \
  --end-time 2026-06-11T00:00:00Z

# Audit all IAM changes in past 7 days
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventSource,AttributeValue=iam.amazonaws.com \
  --start-time 2026-06-04T00:00:00Z \
  --end-time 2026-06-11T00:00:00Z \
  --query 'Events[*].{Event:EventName,Time:EventTime,User:Username}' \
  --output table
```

### Suspicious CloudTrail Events to Hunt

| Event Name | Why It's Suspicious |
|-----------|-------------------|
| `CreateAccessKey` | Attacker creating permanent backdoor keys for an IAM user |
| `CreateLoginProfile` | Attacker enabling console access for a user that shouldn't have it |
| `AttachUserPolicy` / `AttachRolePolicy` | Attacker escalating privileges |
| `AuthorizeSecurityGroupIngress` (0.0.0.0/0) | Attacker opening firewall to the world |
| `PutBucketPolicy` | Attacker making an S3 bucket public for exfiltration |
| `ModifySnapshotAttribute` | Attacker making RDS/EBS snapshots public (data exfil) |
| `CreateUser` | New IAM user created without authorization |
| `AssumeRole` from unknown account ID | Cross-account access from attacker's AWS account |
| `StartInstances` in unusual region | Attacker spinning up resources for crypto mining |

### Query CloudTrail Like a Pro

```bash
# Export CloudTrail to S3, query with Athena:
# Find all API calls from a specific IP
SELECT eventTime, eventName, awsRegion, sourceIPAddress, userIdentity.arn
FROM cloudtrail_logs
WHERE sourceIPAddress = '103.235.46.191'
  AND eventTime BETWEEN timestamp '2026-06-10 00:00:00' AND timestamp '2026-06-11 00:00:00'
ORDER BY eventTime DESC;

# Find all IAM permission changes in last 24h
SELECT eventTime, eventName, requestParameters
FROM cloudtrail_logs
WHERE eventSource = 'iam.amazonaws.com'
  AND eventName IN ('CreateAccessKey', 'CreateUser', 'AttachUserPolicy', 'AttachRolePolicy',
                    'PutUserPolicy', 'PutRolePolicy', 'CreateLoginProfile', 'UpdateAssumeRolePolicy')
  AND eventTime > now() - interval '24' hour
ORDER BY eventTime DESC;
```

---

## IAM Credential Review

### Audit All Users and Keys

```bash
# Generate credential report (CSV of all users + their credential status)
aws iam generate-credential-report

# Download and parse the report
aws iam get-credential-report --query 'Content' --output text | base64 -d > /tmp/credential-report.csv

# Key columns in the CSV:
# user, arn, user_creation_time, password_enabled, password_last_used,
# password_last_changed, access_key_1_active, access_key_1_last_used_date,
# access_key_2_active, access_key_2_last_used_date, cert_1_active, mfa_active

# Find users with access keys not rotated in 90+ days
cat /tmp/credential-report.csv | awk -F',' '$10 != "N/A" && $10 < "2026-03-13" {print $1, $10}'

# Find users with MFA disabled
cat /tmp/credential-report.csv | awk -F',' '$16 == "false" {print $1 " — NO MFA — " $3}'

# List all access keys for a suspicious user
aws iam list-access-keys --user-name jdoe
# Check when each key was last used
aws iam get-access-key-last-used --access-key-id AKIAXXXXXXXXXXXXXXXXX
```

### Revoke Compromised Credentials

```bash
# Deactivate (don't delete yet — preserve evidence) the compromised key
aws iam update-access-key \
  --access-key-id AKIAXXXXXXXXXXXXXXXXX \
  --status Inactive \
  --user-name compromised-user

# Delete the key after forensics are complete
aws iam delete-access-key \
  --access-key-id AKIAXXXXXXXXXXXXXXXXX \
  --user-name compromised-user

# Force password reset for compromised users
aws iam update-login-profile --user-name compromised-user --password-reset-required

# Delete login profile to prevent console access
aws iam delete-login-profile --user-name compromised-user
```

### Rotate All Secrets After a Breach

```bash
# AWS Secrets Manager — force immediate rotation
aws secretsmanager rotate-secret --secret-id prod/database/credentials --rotate-immediately

# HashiCorp Vault — rotate database credentials
vault write -force database/rotate-root/postgres-prod

# Revoke all Vault tokens for compromised auth method
vault token revoke -mode path auth/okta

# K8s — delete and recreate secrets (triggers pods to restart with new values)
kubectl delete secret db-credentials -n production
kubectl create secret generic db-credentials \
  --from-literal=username=new_user \
  --from-literal=password="$(openssl rand -base64 32)" \
  -n production
kubectl rollout restart deployment/api -n production
```

---

## Scenario: Production DB Credentials Leaked on GitHub

**Timeline:**
1. **T+0min** — GitHub Advanced Security sends alert: AWS RDS credentials found in public repo `engineering/internal-scripts`
2. **T+2min** — SRE on-call verifies: it's a production RDS master user password, committed 45 minutes ago, repo has 18 stars
3. **T+3min** — Credentials rotated in AWS Secrets Manager, DB password changed immediately
4. **T+5min** — CloudTrail audit: unknown IP `45.33.32.156` connected to RDS instance `prod-db-1` at T+30min after commit. Queries audited via RDS enhanced monitoring
5. **T+10min** — Attacker used DB access to enumerate S3 bucket names via app config table, accessed `prod-uploads` bucket. Downloaded 2.3GB of user data
6. **T+15min** — `prod-uploads` bucket policy tightened, access keys rotated, affected S3 objects inventoried
7. **T+30min** — GDPR assessment: 45,000 EU users' PII potentially accessed. Legal notified. 72-hour notification clock started
8. **T+1hour** — All production credentials rotated (DB, Redis, third-party APIs, CI/CD deploy keys)
9. **T+2hour** — GitHub repo made private, commit history rewritten with `git filter-branch`, force-pushed
10. **T+24hour** — `git-secrets` pre-commit hook deployed org-wide. Vault adopted for all new services. Secret scanning alert integrated into PagerDuty

### Long-Term Fixes Implemented

```bash
# git-secrets pre-commit hook (installed in all repos)
brew install git-secrets  # macOS
git secrets --install ~/.git-templates/git-secrets
git config --global init.templateDir ~/.git-templates/git-secrets
git secrets --register-aws --global

# Pre-commit hook in CI (GitHub Actions)
# .github/workflows/secrets-scan.yml
# - uses: actions/checkout@v3
# - run: git secrets --scan-history

# Vault for secrets: apps fetch secrets at runtime, never stored in code
# vault-agent injects secrets into pods via sidecar:
# vault.hashicorp.com/agent-inject-template-db-creds: |
#   {{- with secret "database/creds/api-role" }}
#   export DB_USER="{{ .Data.username }}"
#   export DB_PASS="{{ .Data.password }}"
#   {{- end }}
```

---

## Python: CloudTrail Event Analyzer

```python
#!/usr/bin/env python3
"""
cloudtrail_auditor.py — Extracts suspicious API calls from CloudTrail logs (past 24h).
Parses CloudTrail JSON exports from S3 and flags potentially malicious activity.
Usage: python cloudtrail_auditor.py --bucket my-cloudtrail-bucket --prefix AWSLogs/123456789/CloudTrail/us-east-1/2026/06/11/
"""

import boto3
import gzip
import json
import sys
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from argparse import ArgumentParser

SUSPICIOUS_EVENTS = {
    "CreateAccessKey": "New access key created — possible backdoor",
    "CreateLoginProfile": "Console login profile created — possible persistence",
    "AttachUserPolicy": "Policy attached to user — possible privilege escalation",
    "AttachRolePolicy": "Policy attached to role — possible privilege escalation",
    "AuthorizeSecurityGroupIngress": "Security group rule added — possible network opening",
    "PutBucketPolicy": "S3 bucket policy modified — possible data exfiltration setup",
    "ModifySnapshotAttribute": "Snapshot shared publicly — possible data exfiltration",
    "CreateUser": "New IAM user created — unauthorized action",
    "DeleteTrail": "CloudTrail disabled — covering tracks",
    "StopLogging": "CloudTrail logging stopped — covering tracks",
    "UpdateAccessKey": "Access key activated — possible reactivation of revoked key",
    "PutRolePolicy": "Inline policy added to role — privilege escalation",
}


def load_cloudtrail_from_s3(bucket: str, prefix: str) -> list[dict]:
    """Download and parse gzipped CloudTrail log files from S3."""
    s3 = boto3.client("s3")
    paginator = s3.get_paginator("list_objects_v2")
    events = []

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if not obj["Key"].endswith(".json.gz"):
                continue
            resp = s3.get_object(Bucket=bucket, Key=obj["Key"])
            with gzip.GzipFile(fileobj=resp["Body"]) as f:
                data = json.loads(f.read())
                events.extend(data.get("Records", []))

    return events


def flag_suspicious(events: list[dict], hours: int = 24) -> list[dict]:
    """Flag events matching suspicious patterns within the time window."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    flagged = []

    for evt in events:
        event_time = datetime.fromisoformat(evt["eventTime"].replace("Z", "+00:00"))
        if event_time < cutoff:
            continue

        event_name = evt.get("eventName", "")
        if event_name in SUSPICIOUS_EVENTS:
            flagged.append({
                "event_time": evt["eventTime"],
                "event_name": event_name,
                "user": evt.get("userIdentity", {}).get("arn", "Unknown"),
                "source_ip": evt.get("sourceIPAddress", "Unknown"),
                "region": evt.get("awsRegion", "Unknown"),
                "reason": SUSPICIOUS_EVENTS[event_name],
                "raw": json.dumps(evt.get("requestParameters", {}), default=str),
            })

    return flagged


def generate_report(flagged: list[dict]) -> str:
    """Generate a human-readable report of flagged events."""
    if not flagged:
        return "No suspicious CloudTrail events found in the specified window."

    lines = ["=" * 80, "SUSPICIOUS CLOUDTRAIL ACTIVITY REPORT",
             f"Generated: {datetime.now(timezone.utc).isoformat()}",
             f"Total flagged events: {len(flagged)}", "=" * 80, ""]

    events_by_type = defaultdict(list)
    for evt in flagged:
        events_by_type[evt["event_name"]].append(evt)

    for event_name, evts in sorted(events_by_type.items()):
        lines.append(f"\n## {event_name} ({len(evts)} occurrences)")
        lines.append(f"  Risk: {SUSPICIOUS_EVENTS.get(event_name, 'Unknown')}")
        for i, evt in enumerate(evts[:5]):
            lines.append(f"  [{i+1}] {evt['event_time']} — User: {evt['user']}")
            lines.append(f"      Source IP: {evt['source_ip']} | Region: {evt['region']}")
            if evt["raw"] and evt["raw"] != "{}":
                lines.append(f"      Params: {evt['raw'][:200]}")
        if len(evts) > 5:
            lines.append(f"  ... and {len(evts) - 5} more")

    lines.append(f"\n{'=' * 80}")
    lines.append("IMMEDIATE ACTIONS:")
    lines.append("1. Verify each event was authorized")
    lines.append("2. If unauthorized: revoke credentials, investigate scope, notify security")
    lines.append(f"{'=' * 80}")
    return "\n".join(lines)


def main():
    parser = ArgumentParser(description="CloudTrail suspicious event auditor")
    parser.add_argument("--bucket", required=True, help="S3 bucket with CloudTrail logs")
    parser.add_argument("--prefix", default="", help="S3 prefix for log files")
    parser.add_argument("--hours", type=int, default=24, help="Hours to look back")
    parser.add_argument("--output", default="-", help="Output file path (- for stdout)")
    args = parser.parse_args()

    print(f"Scanning CloudTrail logs from s3://{args.bucket}/{args.prefix} "
          f"(last {args.hours}h)...", file=sys.stderr)

    events = load_cloudtrail_from_s3(args.bucket, args.prefix)
    print(f"Loaded {len(events)} events", file=sys.stderr)

    flagged = flag_suspicious(events, args.hours)
    report = generate_report(flagged)

    if args.output == "-":
        print(report)
    else:
        with open(args.output, "w") as f:
            f.write(report)
        print(f"Report written to {args.output}", file=sys.stderr)

    sys.exit(1 if flagged else 0)


if __name__ == "__main__":
    main()
```

---

## Java: Spring Security Auth Failure Listener

```java
package com.example.security;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.ApplicationListener;
import org.springframework.security.authentication.event.AuthenticationFailureBadCredentialsEvent;
import org.springframework.security.authentication.event.AuthenticationSuccessEvent;
import org.springframework.stereotype.Component;

import java.time.Instant;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Detects credential stuffing attacks by tracking failed authentication rates per IP.
 * Triggers an alert when an IP exceeds the configured failure threshold within a time window.
 * Intended to be paired with a rate limiter or WAF integration for automatic blocking.
 */
@Component
public class AuthBreachDetector implements
        ApplicationListener<AuthenticationFailureBadCredentialsEvent> {

    private static final Logger log = LoggerFactory.getLogger(AuthBreachDetector.class);

    // IP → (window_start_epoch_second, failure_count)
    private final Map<String, WindowCounter> failureTracker = new ConcurrentHashMap<>();

    private static final int FAILURE_THRESHOLD = 20;
    private static final int WINDOW_SECONDS = 60;

    @Override
    public void onApplicationEvent(AuthenticationFailureBadCredentialsEvent event) {
        String ip = extractClientIp(event);
        if (ip == null) return;

        String username = event.getAuthentication().getName();
        long now = Instant.now().getEpochSecond();

        WindowCounter counter = failureTracker.compute(ip, (key, existing) -> {
            if (existing == null || now - existing.windowStart > WINDOW_SECONDS) {
                return new WindowCounter(now, 1);
            }
            existing.count.incrementAndGet();
            return existing;
        });

        log.warn("Auth failure — IP: {}, user: {}, failures_in_window: {}/{}",
                ip, maskUsername(username), counter.count.get(), FAILURE_THRESHOLD);

        if (counter.count.get() >= FAILURE_THRESHOLD) {
            alertCredentialStuffing(ip, counter.count.get(), username);
        }
    }

    private void alertCredentialStuffing(String ip, int failureCount, String lastUsername) {
        log.error("CREDENTIAL STUFFING DETECTED — IP: {}, failures: {}, window: {}s, last_user: {}",
                ip, failureCount, WINDOW_SECONDS, maskUsername(lastUsername));

        // TODO: Integrate with PagerDuty, block IP via WAF API, or add to rate limiter
        // wafClient.blockIp(ip, "credential_stuffing", WINDOW_SECONDS * 4);
        // pagerDuty.triggerIncident("Credential stuffing from " + ip);
    }

    private String extractClientIp(AuthenticationFailureBadCredentialsEvent event) {
        // In production, extract from X-Forwarded-For or servlet request
        var request = event.getAuthentication().getDetails();
        if (request instanceof org.springframework.security.web.authentication
                .WebAuthenticationDetails webDetails) {
            return webDetails.getRemoteAddress();
        }
        return null;
    }

    private String maskUsername(String username) {
        if (username == null || username.length() <= 3) return "***";
        return username.charAt(0) + "***" + username.substring(username.length() - 2);
    }

    private static class WindowCounter {
        final long windowStart;
        final AtomicInteger count;

        WindowCounter(long windowStart, int initialCount) {
            this.windowStart = windowStart;
            this.count = new AtomicInteger(initialCount);
        }
    }
}
```

---

## Java: Auth Success Event Listener (Pairing)

```java
package com.example.security;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.ApplicationListener;
import org.springframework.security.authentication.event.AuthenticationSuccessEvent;
import org.springframework.stereotype.Component;

import java.time.Instant;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Tracks successful logins per IP for anomaly detection.
 * A sudden spike in successful logins from a new IP can indicate a successful breach.
 */
@Component
public class LoginAnomalyDetector implements
        ApplicationListener<AuthenticationSuccessEvent> {

    private static final Logger log = LoggerFactory.getLogger(LoginAnomalyDetector.class);

    private final Map<String, IpLoginProfile> ipProfiles = new ConcurrentHashMap<>();

    @Override
    public void onApplicationEvent(AuthenticationSuccessEvent event) {
        String username = event.getAuthentication().getName();
        String ip = extractClientIp(event);
        if (ip == null) return;

        IpLoginProfile profile = ipProfiles.computeIfAbsent(ip, IpLoginProfile::new);
        profile.recordLogin(username);

        if (profile.isAnomalous()) {
            log.error("ANOMALOUS LOGIN PATTERN — IP: {}, users_logged_in: {}, window: {}s",
                    ip, profile.uniqueUsers.size(), profile.WINDOW_SECONDS);
        }
    }

    private String extractClientIp(AuthenticationSuccessEvent event) {
        var details = event.getAuthentication().getDetails();
        if (details instanceof org.springframework.security.web.authentication
                .WebAuthenticationDetails webDetails) {
            return webDetails.getRemoteAddress();
        }
        return null;
    }

    private static class IpLoginProfile {
        static final int WINDOW_SECONDS = 300; // 5 minutes
        static final int MAX_UNIQUE_USERS = 3;

        final String ip;
        final Map<String, Instant> uniqueUsers = new ConcurrentHashMap<>();
        volatile long windowStart = Instant.now().getEpochSecond();

        IpLoginProfile(String ip) { this.ip = ip; }

        void recordLogin(String username) {
            long now = Instant.now().getEpochSecond();
            if (now - windowStart > WINDOW_SECONDS) {
                uniqueUsers.clear();
                windowStart = now;
            }
            uniqueUsers.put(username, Instant.now());
        }

        boolean isAnomalous() {
            return uniqueUsers.size() > MAX_UNIQUE_USERS;
        }
    }
}
```

---

## Prevention Checklist

- [ ] Rate limiting on login endpoint (1 req/s per IP, burst 3)
- [ ] CAPTCHA after 3 failed attempts
- [ ] MFA enforced for all human users
- [ ] `git-secrets` pre-commit hooks on all repos
- [ ] CloudTrail enabled in all regions, log validation on
- [ ] Alert on `AuthorizeSecurityGroupIngress` with 0.0.0.0/0 CIDR
- [ ] Access keys rotated every 90 days (auto-enforced via IAM policy)
- [ ] Vault or AWS Secrets Manager for all production credentials
- [ ] Session tokens have reasonable TTL (24h max for web, 1h for API)
- [ ] `SetSecurityTokenServicePreferences` — global endpoint token v4 enforced
