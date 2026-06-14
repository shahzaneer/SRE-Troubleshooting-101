# 12 — Security Incidents

> **Section Owner:** SRE Security On-Call
> **Last Reviewed:** 2026-06-11

Security incidents require speed, precision, and a calm chain of command. This section covers the most common security emergencies SREs face: auth breaches, leaked secrets, and dependency vulnerabilities.

---

## Files in This Section

| File | Description | Difficulty |
|------|-------------|------------|
| [auth-breach-response.md](auth-breach-response.md) | Credential stuffing, session hijack, IAM forensics, CloudTrail analysis | Advanced |
| [secrets-leaked.md](secrets-leaked.md) | GitHub leaks, AWS key rotation, Vault revocation, rotation playbooks | Intermediate |
| [dependency-vulnerability.md](dependency-vulnerability.md) | CVE triage, scanner tools, patch deployment, SBOM generation | Intermediate |

---

## Golden Rules for Security Incidents

1. **Stop the bleeding first.** Revoke credentials, block IPs, isolate compromised resources. Root cause comes SECOND.
2. **Never fix the commit before revoking the secret.** A secret in git history is forever in public repos. Revoke the secret within 60 seconds. THEN rewrite history.
3. **Assume breach on critical CVEs.** If a service had a remotely exploitable RCE vulnerability during a window where it was internet-facing, rotate all its credentials after patching.
4. **Preserve evidence.** Before wiping a compromised instance, take a snapshot. You'll need it for the post-mortem and potentially for legal.
5. **Communicate early.** Security incidents have regulatory timelines (GDPR: 72 hours). Inform your security officer immediately.

---

## Incident Classification

| Severity | Definition | Example | Response Time |
|----------|-----------|---------|---------------|
| **P0** | Active breach, data exfiltration, production credentials leaked | AWS root key on GitHub | < 5 min |
| **P1** | Critical CVE exploitable on internet-facing service, suspicious activity detected | Log4Shell on public API | < 1 hour |
| **P2** | Staging credentials leaked, non-critical CVE, failed security audit | Dev API key in private repo | < 24 hours |
| **P3** | Policy violation, expiring certificate, routine rotation | TLS cert expires in 7 days | Next business day |

---

## Quick-Reference Commands

```bash
# Find top IPs hitting your auth endpoint with 401
awk '$9==401 {print $1}' /var/log/nginx/access.log | sort | uniq -c | sort -rn | head -20

# Scan a git repo for secrets
git secrets --scan-history

# List all IAM users and their access key status
aws iam generate-credential-report && aws iam get-credential-report --query 'Content' --output text | base64 -d

# Check for publicly accessible S3 buckets
aws s3api list-buckets --query 'Buckets[*].Name' --output text | \
  xargs -I {} aws s3api get-public-access-block --bucket {} 2>/dev/null || echo "MANUAL CHECK: {}"

# Find all security groups with 0.0.0.0/0 ingress
aws ec2 describe-security-groups \
  --filters Name=ip-permission.cidr,Values=0.0.0.0/0 \
  --query 'SecurityGroups[*].{GroupName:GroupName,GroupId:GroupId}' --output table
```

---

## Resources

- [AWS Security Incident Response Guide](https://docs.aws.amazon.com/whitepapers/latest/aws-security-incident-response-guide/welcome.html)
- [Google SRE Book — Security Chapter](https://sre.google/workbook/security/)
- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [GitHub Secret Scanning](https://docs.github.com/en/code-security/secret-scanning)
