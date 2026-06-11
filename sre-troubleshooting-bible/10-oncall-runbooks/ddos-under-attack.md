# DDoS Under Attack Runbook

> **Category:** On-Call | Security | Emergency
> **Difficulty:** Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#runbook` `#ddos` `#security` `#emergency`

---

## 1. DETECT

Alert fires when:

| Metric | Threshold |
|--------|-----------|
| Request count | >10x normal baseline |
| Error rate (503/502) | >20% |
| Network throughput | >90% of capacity |
| Connections (SYN/ESTABLISHED) | >10x normal |

**Confirm it's DDoS — not a marketing event:**

```bash
# Check traffic volume vs baseline:
# Grafana / Datadog — request rate over last 24h. Is this a spike or organic?

# Check if traffic pattern matches a known event:
# - Product launch / marketing campaign?
# - Viral post on social media?
# - Holiday / Black Friday / etc?
# Check with marketing team BEFORE assuming DDoS.
```

---

## 2. CONFIRM — DDoS or Legitimate Traffic?

### 2a. Traffic Source Analysis

```bash
# Top IPs hitting port 80/443:
netstat -an 2>/dev/null | grep -E ":80|:443" | awk '{print $5}' | cut -d: -f1 | sort | uniq -c | sort -rn | head -30

# Or with ss (faster):
ss -tn "sport = :443 or sport = :80" | awk '{print $5}' | cut -d: -f1 | sort | uniq -c | sort -rn | head -30

# VPC Flow Logs export (AWS):
# Query in Athena / CloudWatch Logs Insights:
# fields srcAddr, count(*) as req_count
# | filter dstPort in [80, 443]
# | stats count(*) by srcAddr
# | sort req_count desc
# | limit 20
```

### 2b. User-Agent Analysis

```bash
# Nginx / Apache access log — user agent patterns:
awk -F'"' '{print $6}' /var/log/nginx/access.log \
  | sort | uniq -c | sort -rn | head -20

# Common DDoS signatures in User-Agent:
# - Empty or very short UA strings
# - Generic curl/wget/nmap
# - Specific DDoS tool signatures (research based on current threat landscape)
```

### 2c. Request Path Analysis

```bash
# What URLs are being hit?
awk '{print $7}' /var/log/nginx/access.log \
  | sort | uniq -c | sort -rn | head -20

# Is it hitting:
# - A specific endpoint? (targeted attack on expensive operation)
# - Random URLs? (probe / scan)
# - Only the home page? (simple flood)
```

### 2d. Geo Distribution

```bash
# If using CloudFront / Cloudflare CDN — check geographic origin in dashboard.
# AWS WAF — check location-based metrics.
# Manual check of top IPs:
for ip in <TOP_IPS>; do
  echo -n "$ip: "
  curl -s "https://ipapi.co/${ip}/country/" 2>/dev/null || echo "unknown"
done
```

---

## 3. Layer Identification — L3/4 vs L7

| Layer | Characteristics | Mitigation Strategy |
|-------|----------------|-------------------|
| **L3/4 (Network/Transport)** | SYN floods, UDP floods, ICMP floods. Saturates network pipe. | AWS Shield, NTP, scrubbing |
| **L7 (Application/HTTP)** | HTTP floods, API abuse, slow POST. Low network but high CPU. | WAF, rate limiting |

```bash
# L3/4 detection — check packet rates / SYN counts:
netstat -an | grep SYN_RECV | wc -l
# If huge number (>1000) → SYN flood.

ss -s
# "synrecv" field shows half-open connections.

# L7 detection — request rate is high but bandwidth is not:
# Grafana: request count vs network bytes
# High requests, low bytes = L7 (HTTP flood / slowloris)
# High requests, high bytes = L3/4 or large-payload L7
```

---

## 4. IMMEDIATE RESPONSE

### 4a. Enable DDoS Protection Services

**AWS Shield Advanced (if subscribed):**
```
- Shield Advanced auto-detects and mitigates at the edge.
- If auto-mitigation isn't engaged: open AWS Shield Support case.
- AWS Shield Response Team (SRT): 24/7 for Advanced subscribers.
```

**Cloudflare (if using):**
```
- Dashboard → Firewall → DDoS → Enable "Under Attack" Mode
- This serves a JavaScript challenge to all visitors.
- Real browsers pass automatically. Bots get blocked.
```

### 4b. AWS WAF — Rate-Based Rules

```bash
# Create WAF rate-based rule via AWS Console:
# AWS WAF → Web ACL → Rules → Add rules → Add my own rules and rule groups
# Rule type: Rate-based rule
# Rate limit: 2000 requests per 5 minutes
# Action: Block
# This blocks individual IPs that exceed the threshold.

# Or via CLI:
aws wafv2 create-rule-group \
  --name "DDoS-Rate-Limit" \
  --scope REGIONAL \
  --capacity 10 \
  --visibility-config SampledRequestsEnabled=true,CloudWatchMetricsEnabled=true,MetricName=DDoSRateLimit \
  --region us-east-1
```

### 4c. Block Top Attacker IPs

```bash
# Nginx — add to server block (manual):
# /etc/nginx/conf.d/block-attackers.conf
# location / {
#     deny 1.2.3.4;
#     deny 5.6.7.8;
#     # ... more IPs
# }

# AWS Security Group — block source IPs:
# This is limited to ~60 rules per SG. Use WAF for larger lists.
aws ec2 revoke-security-group-ingress \
  --group-id sg-xxxxxxxx \
  --protocol tcp --port 443 \
  --cidr 1.2.3.4/32

# CloudFront — Geo restriction (if attack from specific countries):
# Console: CloudFront → Distribution → Restrictions → Geographic Restrictions
# Create blocklist for countries identified in 2d.
```

### 4d. Rate Limiting at Application Level (Nginx)

```bash
# Add to nginx.conf (http block):
# limit_req_zone $binary_remote_addr zone=ddos:10m rate=10r/s;

# Add to server/location block:
# limit_req zone=ddos burst=20 nodelay;

# Apply and reload:
nginx -t && systemctl reload nginx

# Rate limiting with iptables (Linux firewall — last resort):
iptables -I INPUT -p tcp --dport 443 -m recent --update --seconds 1 --hitcount 20 -j DROP
iptables -I INPUT -p tcp --dport 443 -m recent --set
# Drops connections from IPs making >20 requests/sec.
# WARNING: Affects legitimate users behind NAT (office networks, mobile carriers).
```

---

## 5. TRAFFIC ANALYSIS DURING ATTACK

### 5a. Real-Time Nginx Monitoring

```bash
# ngxtop — real-time nginx log analysis:
ngxtop -f /var/log/nginx/access.log

# If ngxtop not installed:
pip install ngxtop
ngxtop --no-follow /var/log/nginx/access.log

# Or use goaccess:
goaccess /var/log/nginx/access.log --log-format=COMBINED --real-time-html -o /var/www/html/report.html
```

### 5b. CloudWatch / Prometheus Metrics

```bash
# AWS ALB metrics (per-minute):
# - RequestCount
# - HTTPCode_ELB_5XX_Count
# - TargetResponseTime
# - ActiveConnectionCount
# - NewConnectionCount

# Prometheus — request rate by endpoint:
curl -s "http://prometheus:9090/api/v1/query?query=rate(http_requests_total[1m])" | jq
```

### 5c. Connection Tracking

```bash
# Count connections per IP in real time:
watch -n 1 "ss -tun state established | awk '{print \$5}' | cut -d: -f1 | sort | uniq -c | sort -rn | head -10"

# Check conntrack table (may overflow under DDoS):
wc -l /proc/net/nf_conntrack
cat /proc/sys/net/netfilter/nf_conntrack_max
```

---

## 6. COORDINATION & COMMUNICATION

### 6a. Internal Notification

```
🚨 SLACK #security-incidents:
@here 🚨 P0 DDoS — {service} under active L7 attack.
Traffic volume: {X}x normal.
Action: WAF rate limiting applied. CloudFlare "Under Attack" mode enabled.
Shield SRT case: [case ID]
Status page: updating to "Investigating degraded performance".
```

- [ ] Notify Security team (mandatory for any DDoS event)
- [ ] Notify Incident Commander
- [ ] Open AWS Shield support case (if Shield Advanced)
- [ ] Notify Legal if this may be ransom/extortion DDoS

### 6b. External Communication — Status Page

```
Title: Investigating Degraded Performance
Body: We are currently experiencing a DDoS attack that may affect
       service availability. Our security team is actively mitigating.
       We will update in 30 minutes.
```

- **Do not** reveal specifics (attack size, source, methods). This aids the attacker.
- **Do not** promise "fixed in X minutes." DDoS is unpredictable.

---

## 7. ADVANCED MITIGATION

### 7a. Shed Non-Critical Traffic

```bash
# If legitimate traffic can't get through, drop non-essential endpoints:
# API Gateway / ALB — rule to return 503 for non-critical paths.
# Example: block /api/v1/search, /api/v1/recommendations
# Keep: /api/v1/auth, /api/v1/orders, /api/v1/payments

# Nginx configuration:
location /api/v1/search {
    return 503;
}
location /api/v1/recommendations {
    return 503;
}
nginx -t && systemctl reload nginx
```

### 7b. Challenge Legitimate Traffic

```bash
# Nginx — present challenges only to suspicious IPs:
# /etc/nginx/conf.d/challenge.conf
geo $challenge {
    default 0;
    10.0.0.0/8 0;     # internal traffic — no challenge
}
map $challenge $limit_key {
    0 "";
    1 $binary_remote_addr;
}

# Cloudflare — "I'm Under Attack" mode serves JS challenge
# — real browsers auto-pass, bots don't.
```

### 7c. Contact ISP / Cloud Provider

- **AWS:** Shield Support via Support Center → "DDoS attack" severity: Critical
- **Cloudflare:** Enterprise support, dedicated SRE for critical attacks
- **CDN provider:** Can add upstream filtering

---

## 8. POST-ATTACK

### 8a. Validate Recovery

```bash
# Traffic back to normal?
# Grafana / Datadog — request rate, error rate, bandwidth.

# All services healthy?
kubectl get pods -n prod -o wide
curl -s -o /dev/null -w "%{http_code}" https://api.example.com/health

# Any residual blocks?
# Review WAF IP blocklist — remove blocks that are no longer needed.
# Remove any manual iptables rules.
```

### 8b. Collect Evidence

- [ ] Save access logs for the attack window (S3, separate bucket)
- [ ] Save WAF logs
- [ ] Save VPC Flow Logs
- [ ] Capture CloudWatch metrics for the attack period
- [ ] File security incident report
- [ ] File post-mortem

### 8c. Hardening (Post-Incident)

| Measure | Description |
|---------|-------------|
| **WAF rate-based rules** | Permanent rules at 2x normal traffic level |
| **Shield Advanced** | Subscribe if attack was significant |
| **CDN always-on** | Don't bypass CDN for any traffic |
| **Geo-blocking** | Block countries with no legitimate traffic |
| **Capacity planning** | Scale infrastructure to absorb small attacks |
| **Incident response plan** | Update with lessons learned |

---

## ABORT CRITERIA

| Condition | Action | Timebox |
|-----------|--------|---------|
| Attack >100 Gbps (L3/4) | AWS Shield Standard can't mitigate. Request Shield Advanced SRT. | Immediately |
| Service unavailable to legitimate users for >15 min | Escalate to Incident Commander + VP Engineering | 15 min |
| Mitigation (WAF, iptables) blocks legitimate users | Revert mitigation. Escalate to Security team for alternative. | Immediately |
| Ransom/extortion note received | **Do NOT pay.** Escalate to Security + Legal + FBI. | Immediately |
| Single on-call overwhelmed | Request backup via PagerDuty escalation | Any time |
