# SSL Certificate Expired Runbook

> **Category:** On-Call | Security | Emergency
> **Difficulty:** Basic
> **Last Reviewed:** 2026-06
> **Tags:** `#runbook` `#ssl` `#certificate` `#emergency`

---

## 1. DETECT

Alert fires from Prometheus blackbox exporter (`probe_ssl_early_cert_expiry`) or a monitor reports:

| Source | Alert Metric |
|--------|-------------|
| Prometheus Blackbox | `probe_ssl_early_cert_expiry < 86400 * 14` (14 days) |
| Datadog SSL Check | `ssl.certificate.expiration` |
| AWS Certificate Manager | Certificate status `EXPIRED` or `PENDING_VALIDATION` |
| Users | "Your connection is not private" / `NET::ERR_CERT_DATE_INVALID` |

**Confirm the expiry:**

```bash
# Check certificate details directly:
echo | openssl s_client -servername example.com -connect example.com:443 2>/dev/null | openssl x509 -noout -dates

# Output:
# notBefore=Jun 10 00:00:00 2025 GMT
# notAfter=Jun 10 12:00:00 2026 GMT    ← if this date is in the past → EXPIRED

# Check all SANs on the cert:
echo | openssl s_client -servername example.com -connect example.com:443 2>/dev/null | openssl x509 -noout -text | grep -A1 "Subject Alternative Name"

# Check cert chain:
echo | openssl s_client -servername example.com -connect example.com:443 2>/dev/null | openssl x509 -noout -issuer -subject
```

---

## 2. ASSESS THE BLAST RADIUS

```bash
# Which hostnames are affected?
# Check each service / subdomain:
for domain in api.example.com admin.example.com app.example.com www.example.com; do
  expiry=$(echo | openssl s_client -servername "$domain" -connect "${domain}:443" 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
  echo "$domain : $expiry"
done

# Internal services too:
for service in internal-api.example.local grafana.internal.example.com; do
  expiry=$(echo | openssl s_client -connect "${service}:443" 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
  echo "$service : $expiry"
done
```

---

## 3. FIX — By Provider

### 3a. Let's Encrypt (certbot)

**This is usually the fastest fix (~2 minutes):**

```bash
# Check if certbot is installed:
which certbot

# List currently managed certificates:
certbot certificates

# Force renewal of all certificates:
certbot renew --force-renewal

# If you need to renew only one domain:
certbot renew --cert-name example.com --force-renewal

# Reload web server to pick up new cert:
systemctl reload nginx
# or:
systemctl reload apache2

# Verify new cert:
echo | openssl s_client -servername example.com -connect example.com:443 2>/dev/null | openssl x509 -noout -dates
```

**Troubleshooting certbot renewal failures:**

```bash
# Check certbot logs:
tail -50 /var/log/letsencrypt/letsencrypt.log

# Common issues:
# - Port 80 not open: need HTTP challenge. Open port temporarily.
#   ufw allow 80/tcp
#   iptables -A INPUT -p tcp --dport 80 -j ACCEPT
# - DNS validation failing: check DNS records
# - Rate limit hit: Let's Encrypt has rate limits (5 certs/domain/week)

# If HTTP challenge port is blocked, use DNS challenge:
certbot renew --force-renewal --preferred-challenges dns-01
# Requires DNS plugin (certbot-dns-route53, certbot-dns-cloudflare, etc.)
```

### 3b. AWS ACM (Certificate Manager)

```bash
# List certificates and their status:
aws acm list-certificates \
  --region us-east-1 \
  --query "CertificateSummaryList[*].[DomainName,CertificateArn,Status,NotAfter]" \
  --output table

# Check specific cert details:
aws acm describe-certificate \
  --certificate-arn "arn:aws:acm:us-east-1:123456789:certificate/xxxx-xxxx-xxxx" \
  --region us-east-1

# If expired: request a new certificate
aws acm request-certificate \
  --domain-name "*.example.com" \
  --subject-alternative-names "example.com" \
  --validation-method DNS \
  --region us-east-1

# After requesting, get validation CNAME records:
aws acm describe-certificate \
  --certificate-arn "<NEW_CERT_ARN>" \
  --region us-east-1 \
  --query "Certificate.DomainValidationOptions[0].ResourceRecord"

# Add the returned CNAME record in Route 53:
aws route53 change-resource-record-sets \
  --hosted-zone-id "ZXXXXXXXXXXXXX" \
  --change-batch '{
    "Changes": [{
      "Action": "UPSERT",
      "ResourceRecordSet": {
        "Name": "_xxxxxxxxx.example.com",
        "Type": "CNAME",
        "TTL": 60,
        "ResourceRecords": [{"Value": "_yyyyyyyy.acm-validations.aws."}]
      }
    }]
  }'

# Wait for validation (usually 1-5 minutes):
aws acm wait certificate-validated \
  --certificate-arn "<NEW_CERT_ARN>" \
  --region us-east-1

# Attach new cert to ALB / CloudFront:
# For ALB:
aws elbv2 add-listener-certificates \
  --listener-arn "arn:aws:elasticloadbalancing:..." \
  --certificates CertificateArn="<NEW_CERT_ARN>" \
  --region us-east-1

# For CloudFront — update distribution to use new cert:
# (This must be done in us-east-1 for CloudFront)
aws cloudfront get-distribution-config --id EXXXXXXXXXXXXX > dist-config.json
# Edit the JSON: update ACMCertificateArn and CallerReference
aws cloudfront update-distribution \
  --id EXXXXXXXXXXXXX \
  --distribution-config file://dist-config-edited.json
```

### 3c. Manual Cert (Purchased / Enterprise CA)

```bash
# 1. Locate the current certificate files:
# Common locations:
ls -la /etc/ssl/certs/
ls -la /etc/nginx/ssl/
ls -la /etc/pki/tls/

# Find cert files referenced in web server config:
grep -r "ssl_certificate\|SSLCertificateFile" /etc/nginx/ /etc/apache2/ /etc/httpd/

# 2. Get new certificate and key from provider / CA:
# - Download .crt (certificate) and .key (private key) files
# - Or generate new CSR and get it signed

# 3. Copy new files to server:
scp new-cert.crt prod-server:/etc/nginx/ssl/example.com.crt
scp new-cert.key prod-server:/etc/nginx/ssl/example.com.key

# 4. Set correct permissions:
chmod 600 /etc/nginx/ssl/*.key
chmod 644 /etc/nginx/ssl/*.crt
chown root:root /etc/nginx/ssl/*

# 5. Test nginx config and reload:
nginx -t && systemctl reload nginx

# Or Apache:
apachectl configtest && systemctl reload apache2

# 6. If running behind a load balancer (ALB/NLB), update the
#    listener certificate there too (see ACM section above).
```

### 3d. Kubernetes Cert-Manager

```bash
# Check certificate resources:
kubectl get certificates -A

# Describe the failing certificate:
kubectl describe certificate example-com-tls -n prod

# Check challenges:
kubectl get challenges -A

# If stuck in Pending:
# - Check DNS records are created
# - Check cert-manager logs:
kubectl logs -l app=cert-manager -n cert-manager --tail=100

# Force renewal:
kubectl annotate certificate example-com-tls -n prod \
  cert-manager.io/issuer-name="" --overwrite
kubectl annotate certificate example-com-tls -n prod \
  cert-manager.io/issuer-name=letsencrypt-prod

# cert-manager will detect the change and re-issue.
```

---

## 4. VERIFY

```bash
# 1. Browser: visit the site, click the padlock, verify Not After date.
#    Or use cURL:
curl -svI https://example.com 2>&1 | grep -E "expire|subject:|issuer:|SSL certificate"

# 2. Programmatic validation:
echo | openssl s_client -servername example.com -connect example.com:443 2>/dev/null | openssl x509 -noout -dates
# Expected: notAfter date in the future (>30 days ideally)

# 3. Check expiry in days (monitoring script):
EXPIRY=$(echo | openssl s_client -servername example.com -connect example.com:443 2>/dev/null | openssl x509 -noout -enddate | cut -d= -f2)
EXPIRY_EPOCH=$(date -d "$EXPIRY" +%s)
NOW_EPOCH=$(date +%s)
DAYS_LEFT=$(( (EXPIRY_EPOCH - NOW_EPOCH) / 86400 ))
echo "$DAYS_LEFT days until expiry"

# 4. SSL Labs comprehensive test:
# https://www.ssllabs.com/ssltest/analyze.html?d=example.com

# 5. All services verified:
for domain in api.example.com admin.example.com www.example.com; do
  echo -n "$domain: "
  echo | openssl s_client -servername "$domain" -connect "${domain}:443" 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2
done
```

---

## 5. PERMANENT PREVENTION

| Method | How | Coverage |
|--------|-----|----------|
| **certbot auto-renewal** | certbot creates systemd timer by default. Verify: `systemctl status certbot.timer` | Let's Encrypt |
| **ACM auto-renewal** | AWS ACM certs auto-renew automatically when DNS-validated | AWS services |
| **cert-manager** | `renewBefore: 720h` (renew 30 days before expiry) | Kubernetes |
| **Prometheus alert** | `probe_ssl_early_cert_expiry < 86400 * 30` (30 days) | All domains |
| **Calendar reminder** | Add manual cert expiry to team calendar 30 days before | Manual certs |

### Verify auto-renewal is working:

```bash
# certbot systemd timer:
systemctl list-timers | grep certbot
systemctl status certbot.timer
# Should show "active" and last run recently.

# certbot renewal config:
cat /etc/letsencrypt/renewal/example.com.conf
# Should contain the correct authenticator and challenge method.

# cert-manager certificate spec:
kubectl get certificate -n prod -o yaml | grep -A5 "renewBefore"
```

---

## ABORT CRITERIA

| Condition | Action | Timebox |
|-----------|--------|---------|
| DNS validation record not propagating (certbot or ACM stuck) | Escalate to **Networking / DNS team** | 15 min |
| ACM cert validation stuck >30 minutes | Open AWS Support ticket. Escalate to Infra team. | 30 min |
| Rate limit hit (Let's Encrypt: 5 certs/domain/week) | Escalate to Security team for alternative CA. | Immediately |
| New cert breaks TLS handshake (ciphers, protocol mismatch) | Roll back to old cert if still valid for a few hours. Escalate to Security. | Before old cert expires |
| Private key lost / compromised | **Escalate to Security immediately.** Must revoke old cert and reissue. | Immediately |
| Multiple services affected simultaneously | Escalate to Incident Commander. | Immediately |
