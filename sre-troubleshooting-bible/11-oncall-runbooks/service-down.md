# Service Down (503) Runbook

> **Category:** On-Call | Incident Response | Critical
> **Difficulty:** Basic to Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#runbook` `#503` `#outage` `#oncall`

---

## 1. DETECT

Alert fires when health check fails or availability drops to 0%. This is a **P0 incident**.

**Confirm immediately:**

```bash
# External perspective — are we down from the internet?
curl -s -o /dev/null -w "%{http_code}" https://api.example.com/health

# If that hangs or returns 5xx → confirmed outage.

# Check multiple regions:
for region in us-east-1 us-west-2 eu-west-1; do
  echo -n "$region: "
  curl -s -o /dev/null -w "%{http_code}\n" "https://${region}.api.example.com/health"
done
```

---

## 2. ACKNOWLEDGE & DECLARE P0

```
🚨 SLACK #incidents:
@here 🚨 P0 OUTAGE — {service} is returning 503 / unavailable.
All regions affected: [yes / us-east-1 only / ...].
Investigating now. ETA to update: 5 min.
```

- [ ] Acknowledge page immediately (< 5 min)
- [ ] Declare P0 in incident channel
- [ ] Start incident timer
- [ ] Notify on-call Incident Commander (if not already on the incident)
- [ ] Update public status page: "Investigating degraded service"

---

## 3. IMMEDIATE CHECKS (First 60 Seconds)

Run these commands in parallel on the affected host(s):

```bash
# 1. Is the process alive?
systemctl status app || supervisorctl status app
ps aux | grep -E "java|node|python|ruby" | grep -v grep

# 2. Is it listening on the expected port?
ss -tlnp | grep -E ":8080|:3000|:443"
# If nothing on the port → process crashed or never started.

# 3. What does the health endpoint return locally?
curl -sv http://localhost:8080/health 2>&1 | head -20
# If local responds but external doesn't → LB / network / firewall issue.

# 4. Has anything been recently deployed?
# Check CI/CD dashboard or run:
git log --oneline --since="1 hour ago" -10
helm list -n prod --date --reverse | tail -5

# 5. Any infrastructure changes?
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=ModifySecurityGroupRule \
  --max-results 5 2>/dev/null
```

---

## 4. QUICK WINS (Try These First — Fastest to Slowest)

### 4a. Restart the Service (30 seconds)

```bash
# Systemd:
systemctl restart app

# Kubernetes (if pod is crash-looping):
kubectl delete pod <CRASHING-POD> -n prod

# Supervisor:
supervisorctl restart app

# Docker:
docker restart $(docker ps -q --filter "name=app")
```

**Check:** After restart, does the health check respond?

```bash
sleep 10 && curl -s -o /dev/null -w "%{http_code}" https://api.example.com/health
```

### 4b. Rollback Last Deployment (60 seconds)

If a deployment happened in the last 30 minutes:

```bash
# Kubernetes:
kubectl rollout undo deployment/app -n prod
kubectl rollout status deployment/app -n prod

# Helm:
helm rollback app-prod $(helm history app-prod -n prod --max 2 -o json | jq '.[-2].revision') -n prod

# ECS:
aws ecs update-service --cluster prod --service app \
  --task-definition app-previous-version \
  --force-new-deployment --region us-east-1
```

### 4c. Scale Up (if pods are OOMKilled / CrashLoopBackOff)

```bash
kubectl scale deployment app -n prod --replicas=5
kubectl get pods -l app=app -n prod -w
```

### 4d. Restart Upstream Dependencies

```bash
# If DB is the issue:
# AWS RDS:
aws rds reboot-db-instance --db-instance-identifier prod-db --region us-east-1

# Redis:
redis-cli -h prod-redis.example.com -p 6379 SHUTDOWN NOSAVE
# (ElastiCache: use AWS console to reboot)

# If using managed services: reboot via console first, CLI as fallback.
```

---

## 5. DEEP DIAGNOSIS

If quick wins didn't work, dig deeper:

### 5a. System Resources

```bash
# Is the machine overloaded?
top -bn1 | head -10
free -h
df -h
iostat -x 1 3

# Out of memory / OOM killer?
dmesg | grep -i "Out of memory" | tail -10
grep -i "oom" /var/log/syslog | tail -20

# File descriptor limit?
lsof -p $(pgrep -f java) | wc -l
cat /proc/$(pgrep -f java)/limits | grep "open files"
```

### 5b. Application Logs

```bash
# Last errors before outage:
journalctl -u app --since "10 min ago" --no-pager | grep -iE "error|fatal|panic|shutdown" | tail -30

# Crash log (if systemd service exited):
journalctl -u app --since "10 min ago" --no-pager | tail -50

# Kubernetes:
kubectl logs <POD> -n prod --previous --tail=100
kubectl describe pod <POD> -n prod | grep -A10 "Events:"
```

### 5c. Network Connectivity

```bash
# Can the app reach the database?
nc -zv db-prod.internal 5432
# or:
psql -h db-prod.internal -U app -d app_prod -c "SELECT 1;" 2>&1 | head -5

# Can the app reach Redis?
nc -zv redis-prod.internal 6379

# Can it resolve DNS?
nslookup db-prod.internal
# If DNS fails → CoreDNS / Route 53 / /etc/resolv.conf issue

# Check network interfaces:
ip addr show
# Is the primary interface UP? Is the IP assigned?
```

### 5d. Load Balancer Health Checks

```bash
# AWS ALB — check target group health:
aws elbv2 describe-target-health \
  --target-group-arn arn:aws:elasticloadbalancing:us-east-1:123456789:targetgroup/app/xxx \
  --region us-east-1

# Look for unhealthy targets. Common causes:
# - Health check path changed (404 → unhealthy)
# - Health check port wrong
# - App too slow to respond (timeout)
# - Security group rule removed (can't reach targets)
```

### 5e. Disk Full (Common Silent Killer)

```bash
df -h | grep -E "Use%|100%|98%|99%"
# If any filesystem at 100% → Disk Full Emergency Runbook
# Even if the app process is running, it can't write logs = 503
```

---

## 6. KUBERNETES-SPECIFIC DIAGNOSIS

```bash
# 1. Pod status overview:
kubectl get pods -n prod -o wide

# 2. Describe a failing pod — the Events section tells you everything:
kubectl describe pod <FAILING-POD> -n prod

# 3. Common pod failure patterns:
# CrashLoopBackOff  → app crashes on startup. Check logs with --previous.
# ImagePullBackOff   → can't pull image. Check registry, image tag, credentials.
# OOMKilled          → memory limit hit. Increase limit or fix leak.
# Pending            → no node can schedule. Check resources, node taints.
# Evicted            → node ran out of disk/memory. Check node health.

# 4. Are endpoints routing to pods?
kubectl get endpoints app-service -n prod
# If no endpoints listed → Service selector doesn't match any running pods.

# 5. Ingress / Service configuration:
kubectl describe ingress app-ingress -n prod
kubectl describe service app-service -n prod

# 6. Recent cluster events:
kubectl get events -n prod --sort-by='.lastTimestamp' | tail -30
```

---

## 7. MITIGATION HIERARCHY

Execute in this order — stop when service recovers:

| Priority | Action | When to Use | Downtime |
|----------|--------|------------|----------|
| 1 | Rollback deployment | Deploy in last 30 min | ~30 sec |
| 2 | Feature flag off broken code | Code path controlled by flag | 0 sec |
| 3 | Restart service | Process crashed or stuck | 5-30 sec |
| 4 | Scale up replicas | Resource saturation | 30-60 sec |
| 5 | Restart upstream (DB/Redis) | DB locked up | 1-3 min |
| 6 | Failover to DR region | Region-level outage | 5-10 min |
| 7 | Restore from backup | Data corruption | 15-60 min |

---

## 8. IF NOTHING WORKS — FAILOVER TO DR

```bash
# Route 53 — update DNS to point to DR region:
aws route53 change-resource-record-sets \
  --hosted-zone-id ZXXXXXXXXXXXX \
  --change-batch '{
    "Changes": [{
      "Action": "UPSERT",
      "ResourceRecordSet": {
        "Name": "api.example.com",
        "Type": "A",
        "AliasTarget": {
          "HostedZoneId": "Z1H1FL5HABSF5",
          "DNSName": "dr-load-balancer.elb.amazonaws.com",
          "EvaluateTargetHealth": true
        }
      }
    }]
  }' --region us-east-1

# DNS propagation takes 60-300 seconds depending on TTL.
# Reduce TTL ahead of time for critical records.
```

---

## 9. VERIFY RECOVERY

```bash
# External health check:
curl -s -o /dev/null -w "%{http_code}\n" https://api.example.com/health
# Expected: 200

# Smoke test critical user flows:
./smoke-tests.sh prod

# All regions:
for region in us-east-1 us-west-2 eu-west-1; do
  echo "$region: $(curl -s -o /dev/null -w '%{http_code}' https://${region}.api.example.com/health)"
done

# Real user traffic — check request rate is recovering:
# (Grafana / Datadog dashboard)
```

---

## 10. POST-RECOVERY

```markdown
**Slack #incidents update:**
✅ RECOVERED — {service} is back online.
Outage duration: X minutes.
Root cause: [preliminary].
Post-mortem scheduled: [date].
Monitoring for 30 min before closing incident.
```

- [ ] Watch dashboards for 30 minutes
- [ ] File post-mortem ticket
- [ ] Tag the bad deployment / config change
- [ ] Update this runbook if process gaps found
- [ ] Notify customer support team that service is restored

---

## ABORT CRITERIA

| Condition | Action | Timebox |
|-----------|--------|---------|
| **P0 outage >15 minutes** | **Declare major incident. Page engineering management.** | 15 min |
| Unable to identify cause in 10 minutes | Escalate to L2 / Team Lead | 10 min |
| Rollback fails or makes things worse | Stop rollback. Escalate to deployment/infra team. | Immediately |
| Data loss or corruption detected | Escalate to **Security team + Engineering Director** | Immediately |
| DR failover considered | Must get **Incident Commander approval** before executing | Before DR |
| Single on-call overwhelmed | Request backup via PagerDuty escalation policy | Any time |
