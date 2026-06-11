# High Error Rate Runbook

> **Category:** On-Call | Incident Response
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#runbook` `#errors` `#incident` `#oncall`

---

## 1. DETECT

Alert fires when error rate exceeds 5% for 5 minutes across the service.

**Confirm in dashboard:**

| Dashboard | Query | What to Check |
|-----------|-------|---------------|
| Grafana | `sum(rate(http_requests_total{status=~"5.."}[5m])) / sum(rate(http_requests_total[5m])) * 100` | Error % by endpoint |
| Datadog APM | Service → Errors tab | Error rate graph, stack trace sample |
| CloudWatch | `HTTPCode_Target_5XX_Count / RequestCount` | ALB target group health |

**Quick sanity check — is this a real alert?**

```bash
# Rapid-fire health checks (30 requests, check distribution):
for i in $(seq 1 30); do
  curl -s -o /dev/null -w "%{http_code}\n" https://api.example.com/v1/health &
done | sort | uniq -c
# If majority are 5xx → real incident. If handful → flapping / transient.
```

---

## 2. ACKNOWLEDGE & COMMUNICATE

```markdown
**Slack #incidents message:**
@oncall ACK — High Error Rate for `{service}`.
Currently investigating scope. Next update in 10 min.
Incident link: [PagerDuty / Opsgenie URL]
```

- [ ] Acknowledge the page
- [ ] Start the incident timer
- [ ] Post to `#incidents`
- [ ] Open the [Error Rate Dashboard](https://grafana.internal.example.com/d/err-rate)

---

## 3. TRIAGE — Determine Scope

Answer these four questions before touching anything:

### Q1: All endpoints or one endpoint?

```bash
# From Loki / Splunk — error count by endpoint:
# Loki (LogQL):
sum by (path) (
  count_over_time({app="api-prod", status=~"5[0-9]{2}"}[10m])
)

# From Nginx access log:
tail -5000 /var/log/nginx/access.log \
  | awk '$9 ~ /^5/ {print $7}' \
  | sort | uniq -c | sort -rn | head -15
```

| Scope | Likely Cause | Action |
|-------|-------------|--------|
| Single endpoint | Bug in handler, bad upstream for that feature | Isolate, check code diff for that route |
| All endpoints | Infrastructure, DB, middleware, auth, network | Broad investigation (go to Section 5) |

### Q2: All users or specific users?

```bash
# Check for user-id correlation in errors:
grep " ERROR " /var/log/app/app.log \
  | grep -oP 'user[_-]?id[=:]\K[\w-]+' \
  | sort | uniq -c | sort -rn | head -10
# If one user dominates → bad request pattern, token issue, rate-limiting
```

### Q3: All regions or one region?

```bash
for region in us-east-1 us-west-2 eu-west-1 ap-southeast-1; do
  echo -n "$region: "
  curl -s -o /dev/null -w "%{http_code}" "https://${region}.api.example.com/health" &
done; wait
# If only ONE region red → check that region's DB replica, cache, network
```

### Q4: Did something change recently?

```bash
# Git — last 20 commits:
git log --oneline --since="2 hours ago" -20

# Helm releases:
helm history release-name -n prod --max 5

# Kubernetes recent events:
kubectl get events --sort-by='.lastTimestamp' -n prod | grep -E "Deploy|Rollout|Scale" | tail -20

# Terraform / infrastructure changes:
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=UpdateService \
  --max-results 10 2>/dev/null | jq '.Events[].EventTime'
```

**If a deploy occurred within 30 min of the alert** → 80% chance it's the cause. Jump to [Deployment Rollback](deployment-rollback.md) immediately.

---

## 4. ISOLATE — Quick Checks

### 4a. Core Infrastructure Health

```bash
# Database — can we connect and query?
psql "$DATABASE_URL" -c "SELECT 1;" 2>&1 | head -5

# Redis — is it alive?
redis-cli -h "$REDIS_HOST" -p 6379 PING
# Expected: PONG

# Elasticsearch:
curl -s "http://${ES_HOST}:9200/_cluster/health" | jq '.status'
# green / yellow / red

# Message broker (Kafka / RabbitMQ):
# Check consumer lag / queue depth via dashboard
```

### 4b. Error Classification — 4xx vs 5xx

```bash
# Nginx access log quick breakdown:
awk '{print $9}' /var/log/nginx/access.log \
  | grep -oP '^\d' \
  | sort | uniq -c | sort -rn
```

| Class | Meaning | Whose Fault? |
|-------|---------|-------------|
| **4xx** | Client error (bad request, unauthorized, not found) | Caller — usually not our problem |
| **5xx** | Server error (internal, bad gateway, service unavailable) | **Our stack** — INVESTIGATE |

### 4c. Third-Party Dependencies

```bash
# Quick check of known external dependencies:
for dep in "https://api.stripe.com" "https://api.sendgrid.com" "https://api.twilio.com"; do
  echo -n "$dep: "
  curl -s -o /dev/null -w "%{http_code}" "$dep"
  echo
done
```

---

## 5. FIND ROOT CAUSE — Deep Diagnosis

### 5a. Application Logs

```bash
# Systemd service:
journalctl -u app-prod -p err --since "10 min ago" --no-pager | tail -60

# Kubernetes pods:
kubectl logs -l app=api-server -n prod --tail=200 --since=10m \
  | grep -iE "error|exception|panic|fatal|timeout" | tail -40

# Specific pod:
kubectl logs <POD-NAME> -n prod --previous --tail=200   # if pod restarted

# Raw log file:
grep -E "ERROR|FATAL|Exception" /var/log/app/production.log | tail -80
```

### 5b. Database Diagnosis

```bash
# Connection overview:
psql "$DATABASE_URL" <<SQL
SELECT state, count(*) AS cnt
FROM pg_stat_activity
WHERE datname = 'app_production'
GROUP BY state;
SQL

# Longest running queries — the likely culprits:
psql "$DATABASE_URL" <<SQL
SELECT pid,
       now() - query_start AS runtime,
       state,
       wait_event_type,
       LEFT(query, 150) AS query_snippet
FROM pg_stat_activity
WHERE state != 'idle'
  AND pid != pg_backend_pid()
ORDER BY runtime DESC
LIMIT 15;
SQL

# Lock contention check:
psql "$DATABASE_URL" <<SQL
SELECT blocked.pid AS blocked_pid,
       blocking.pid AS blocking_pid,
       blocked.query AS blocked_query,
       blocking.query AS blocking_query,
       now() - blocked.query_start AS blocked_duration
FROM pg_locks blocked_locks
JOIN pg_stat_activity blocked ON blocked.pid = blocked_locks.pid
JOIN pg_locks blocking_locks
  ON blocking_locks.locktype = blocked_locks.locktype
  AND blocking_locks.relation = blocked_locks.relation
  AND blocking_locks.pid != blocked_locks.pid
JOIN pg_stat_activity blocking ON blocking.pid = blocking_locks.pid
WHERE NOT blocked_locks.granted
  AND blocked_locks.granted = false;
SQL
```

### 5c. System Resources

```bash
# Each of these commands takes ~1 second:
top -bn1 | head -15               # CPU overview
free -h                           # Memory
df -h                             # Disk
netstat -an | grep ESTABLISHED | wc -l   # Open connections
ss -s                             # Socket statistics

# Are we out of file descriptors?
lsof -p $(pgrep -f java) | wc -l
ulimit -n                         # soft limit
```

### 5d. Application Memory / GC

```bash
# JVM heap usage (Spring Boot Actuator):
curl -s http://localhost:8080/actuator/metrics/jvm.memory.used | jq '.measurements[0].value'

# GC pauses:
curl -s http://localhost:8080/actuator/metrics/jvm.gc.pause | jq

# Thread pool:
curl -s http://localhost:8080/actuator/metrics/tomcat.threads.busy | jq '.measurements[0].value'
curl -s http://localhost:8080/actuator/metrics/tomcat.threads.config.max | jq '.measurements[0].value'

# Connection pool (HikariCP):
curl -s http://localhost:8080/actuator/metrics/hikaricp.connections.active | jq '.measurements[0].value'
curl -s http://localhost:8080/actuator/metrics/hikaricp.connections.pending | jq '.measurements[0].value'
```

---

## 6. MITIGATE

### If Deployment Caused It → ROLLBACK

```bash
# Kubernetes:
kubectl rollout undo deployment/api-server -n prod
kubectl rollout status deployment/api-server -n prod

# Helm:
helm rollback api-server $(helm history api-server -n prod --max 2 -o json | jq '.[-2].revision') -n prod
```

### If Database Connection Exhausted

Go to [Database Connection Exhaustion](database-connection-exhaustion.md) runbook. Quick fix:

```sql
-- Kill idle connections older than 5 min:
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE state = 'idle'
  AND query_start < now() - interval '5 minutes';
```

### If Upstream Dependency Down

- Enable circuit breaker (toggle config or Hystrix dashboard)
- Switch to stale cache / fallback for that dependency
- Reroute traffic away from broken region

### If Resource Exhausted

```bash
# Scale up now:
kubectl scale deployment api-server -n prod \
  --replicas=$(($(kubectl get deployment api-server -n prod -o jsonpath='{.spec.replicas}') + 5))
```

---

## 7. VERIFY

```bash
# Watch error rate dropping:
watch -n 5 "curl -s https://api.example.com/v1/health -o /dev/null -w '%{http_code}'"

# Smoke test critical flows (login, search, purchase):
./smoke-tests.sh prod

# All regions green?
for region in us-east-1 us-west-2 eu-west-1; do
  curl -s -o /dev/null -w "$region: %{http_code}\n" "https://${region}.api.example.com/health"
done
```

---

## 8. MONITOR (Post-Mitigation)

- [ ] Watch error rate dashboard for **30 minutes** with no recurrence
- [ ] Temporarily lower alert threshold to 2% (catch regressions faster)
- [ ] Verify downstream services also recovering
- [ ] Check app logs for any errors that preceded the spike (for root cause post-mortem)
- [ ] Update `#incidents`: "Error rate returned to baseline. Monitoring. ETA to close: +30 min."

---

## ABORT CRITERIA

| Condition | Action | Timebox |
|-----------|--------|---------|
| Error rate still >5% after 30 min of mitigation | **Escalate to Incident Commander** | 30 min |
| Error rate exceeds 50% for >5 min | **Escalate to Incident Commander + Engineering Manager** | 5 min |
| Data loss or corruption suspected | **Escalate to Security team immediately** | 0 min |
| Mitigation attempt makes error rate worse | **Stop. Escalate to L2 engineer.** | Immediately |
| Root cause unknown after 15 min diagnosis | **Escalate to team lead / L2 engineer** | 15 min |
