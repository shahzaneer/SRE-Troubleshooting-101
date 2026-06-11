# High Latency Runbook

> **Category:** On-Call | Incident Response
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#runbook` `#latency` `#incident` `#oncall`

---

## 1. DETECT

Alert fires when p95 latency exceeds threshold for 5 minutes.

**Service tier latency thresholds:**

| Tier | Example Services | p95 Threshold |
|------|-----------------|--------------|
| Tier 0 (core) | Auth, payments | < 100ms |
| Tier 1 (critical) | Orders, inventory, users | < 250ms |
| Tier 2 (non-critical) | Reports, exports, search | < 1000ms |

**Confirm the alert — manual latency check:**

```bash
# Detailed timing breakdown:
curl -w "\n\
 DNS:      %{time_namelookup}s\n\
 TCP:      %{time_connect}s\n\
 TLS:      %{time_appconnect}s\n\
 TTFB:     %{time_starttransfer}s\n\
 Total:    %{time_total}s\n\
 HTTP:     %{http_code}\n" \
  -o /dev/null -s https://api.example.com/v1/health

# If Total > 1s for a simple health check, something is severely wrong.

# Approximate p95 from 20 samples:
for i in $(seq 1 20); do
  curl -s -o /dev/null -w "%{time_total}\n" https://api.example.com/v1/ping
done | sort -n | awk 'NR==int(NR*0.95+0.5) {print $1}'
```

---

## 2. TRIAGE — Narrow the Scope

### 2a. All endpoints or one?

```bash
# Latency comparison across critical endpoints:
for ep in /health /v1/orders /v1/users /v1/auth/login /v1/search; do
  echo -n "$ep: "
  curl -s -o /dev/null -w "%{time_total}s  (%{http_code})" "https://api.example.com${ep}"
  echo
done
```

| Pattern | Meaning |
|---------|---------|
| All endpoints slow equally | Downstream dependency (DB, Redis, network) |
| One endpoint slow | Specific query / N+1 problem / inefficient handler |
| Intermittent slowness | GC pauses, cron job, cache stampede |

### 2b. Which percentile?

```bash
# From Prometheus / Grafana:
# p50 (median):    histogram_quantile(0.50, rate(http_request_duration_seconds_bucket[5m]))
# p95:             histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))
# p99:             histogram_quantile(0.99, rate(http_request_duration_seconds_bucket[5m]))
```

| Pattern | Likely Cause |
|---------|-------------|
| p50 normal, p95/p99 high | Long-tail stragglers: GC, DB locking, cold starts |
| p50 also high | Systemic: CPU saturation, DB overload, network |
| Only p99 spikes | Rare edge case: large payloads, specific users, cold cache |

### 2c. Region-specific?

```bash
for region in us-east-1 us-west-2 eu-west-1 ap-southeast-1; do
  echo -n "${region}: "
  curl -s -o /dev/null -w "%{time_total}s" "https://${region}.api.example.com/health"
  echo
done
# If one region is slow → check that region's DB replica, network routes
```

### 2d. Time correlation?

Check if the spike correlates with:
- **Cron jobs** — `kubectl get cronjobs -n prod`, check `/etc/cron.d/`
- **Traffic spike** — check request rate graph alongside latency graph
- **Recent deployment** — check CI/CD pipeline timeline
- **Daily batch** — does it happen at the same time every day?

---

## 3. DIAGNOSIS — Find the Bottleneck

### 3a. Database (Most Common Cause)

**PostgreSQL — slow queries:**

```sql
-- Active queries with runtime:
SELECT pid, now() - query_start AS runtime,
       state, wait_event_type, wait_event,
       LEFT(query, 200) AS query_snippet
FROM pg_stat_activity
WHERE state != 'idle'
  AND pid != pg_backend_pid()
ORDER BY runtime DESC
LIMIT 15;

-- Longest single query that could be blocking others:
SELECT pid, now() - query_start AS runtime, query
FROM pg_stat_activity
WHERE state = 'active' AND pid != pg_backend_pid()
ORDER BY runtime DESC LIMIT 1;

-- Run EXPLAIN on the slow query (copy from app logs or pg_stat_activity):
EXPLAIN (ANALYZE, BUFFERS, TIMING) SELECT ... ;
```

**MySQL:**

```sql
SHOW FULL PROCESSLIST;
-- Check for queries with Time > 5s

-- Enable slow query log temporarily:
SET GLOBAL slow_query_log = 'ON';
SET GLOBAL long_query_time = 2;
-- Check: SELECT * FROM mysql.slow_log ORDER BY start_time DESC LIMIT 10;
```

### 3b. External API Calls

```bash
# Curl time breakdown helps isolate the problem:
curl -w "
    DNS lookup:     %{time_namelookup}s   (DNS resolution)
    TCP connect:    %{time_connect}s      (network round trip)
    TLS handshake:  %{time_appconnect}s   (TLS negotiation)
    TTFB:           %{time_starttransfer}s (server processing)
    Transfer:       %{time_total}s - %{time_starttransfer}s
    TOTAL:          %{time_total}s
" -o /dev/null -s https://api.example.com/v1/orders

# Interpretation:
# high time_namelookup    → DNS is slow
# high time_connect       → network / firewall / LB issue
# high time_starttransfer → application / DB is slow (most common)
# high transfer difference → response body too large
```

### 3c. CPU Saturation

```bash
top -bn1 | head -10
mpstat 1 3                       # per-core CPU
pidstat 1 5                      # per-process CPU

# Kubernetes:
kubectl top pods -n prod --sort-by=cpu | head -15

# Check for CPU throttling (container hitting limits):
kubectl describe pod <POD> -n prod | grep -A3 "Limits"
```

### 3d. GC Pauses (JVM Languages)

```bash
# GC monitoring (jstat):
jstat -gcutil $(pgrep -f java) 1000 10
# Key columns: FGC (Full GC count), FGCT (Full GC time), GCT (total GC time)
# If FGCT is growing rapidly → GC pressure

# GC pause metrics from Actuator:
curl -s http://localhost:8080/actuator/metrics/jvm.gc.pause | jq

# GC log analysis:
grep -E "Full GC|concurrent mode failure|promotion failed|allocation failure" /var/log/app/gc.log | tail -20
```

### 3e. Thread / Connection Pool Exhaustion

```bash
# Tomcat / Netty thread pool:
curl -s http://localhost:8080/actuator/metrics/tomcat.threads.busy | jq '.measurements[0].value'
curl -s http://localhost:8080/actuator/metrics/tomcat.threads.config.max | jq '.measurements[0].value'
# busy == max → thread starvation. Check what's blocking threads (likely DB).

# HikariCP connection pool:
curl -s http://localhost:8080/actuator/metrics/hikaricp.connections.active | jq '.measurements[0].value'
curl -s http://localhost:8080/actuator/metrics/hikaricp.connections.pending | jq '.measurements[0].value'
# pending > 0 → pool exhausted. Root cause: slow queries holding connections.

# Node.js event loop lag:
curl -s http://localhost:9090/metrics \
  | grep "nodejs_eventloop_lag_seconds"
```

---

## 4. IMMEDIATE MITIGATION

### Step 1: Kill Slow Database Queries

```sql
-- Terminate all queries running >30 seconds:
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE state = 'active'
  AND query_start < now() - interval '30 seconds'
  AND pid != pg_backend_pid();
```

### Step 2: Rolling Restart

```bash
# Kubernetes rolling restart (one pod at a time, graceful):
kubectl rollout restart deployment/api-server -n prod
kubectl rollout status deployment/api-server -n prod

# Verify new pods ready:
kubectl get pods -l app=api-server -n prod
```

### Step 3: Scale Up

```bash
# Horizontal scaling — add pods:
CURRENT=$(kubectl get deployment api-server -n prod -o jsonpath='{.spec.replicas}')
kubectl scale deployment api-server -n prod --replicas=$((CURRENT + 5))

# Database — bump RDS instance size if DB is the bottleneck:
aws rds modify-db-instance \
  --db-instance-identifier mydb \
  --db-instance-class db.r6g.xlarge \
  --apply-immediately \
  --region us-east-1
```

### Step 4: Rollback if Recent Deploy

Proceed to [Deployment Rollback Runbook](deployment-rollback.md).

---

## 5. VERIFY

```bash
# Re-check latency on all endpoints:
for ep in /health /v1/orders /v1/users /v1/auth/login; do
  echo -n "$ep: "
  curl -s -o /dev/null -w "%{time_total}s (%{http_code})" "https://api.example.com${ep}"
  echo
done

# Watch p95 in real time (Grafana iframe or Prometheus query):
watch -n 5 "curl -s 'http://prometheus:9090/api/v1/query?query=histogram_quantile(0.95,rate(http_request_duration_seconds_bucket[1m]))' | jq '.data.result[0].value[1] | tonumber'"

# All regions healthy?
for region in us-east-1 us-west-2 eu-west-1; do
  curl -s -o /dev/null -w "$region: %{http_code} (%{time_total}s)\n" \
    "https://${region}.api.example.com/health"
done
```

---

## 6. PERMANENT FIX (Post-Incident Action Items)

| Root Cause | Permanent Fix |
|------------|--------------|
| Slow query | Add index, rewrite query, add caching layer |
| N+1 query pattern | Eager loading, batch fetching, DataLoader |
| Missing index | `CREATE INDEX CONCURRENTLY idx_name ON table(col)` |
| Connection pool exhausted | Increase pool or add PgBouncer |
| No caching | Add Redis/memcached for hot data |
| GC pauses | Tune GC flags, increase heap, fix leak |
| Large response body | Pagination, compression, field filtering |

---

## 7. Monitoring Improvements

- [ ] p50 / p95 / p99 breakout per endpoint in dashboard
- [ ] DB query duration tracked as a metric (not just logs)
- [ ] Thread pool saturation alert (busy/max > 0.8)
- [ ] Connection pool pending alert (>0 for >2 min)
- [ ] GC pause duration alert (>200ms sustained)

---

## ABORT CRITERIA

| Condition | Escalation | Timebox |
|-----------|-----------|---------|
| p99 latency >10x normal and not improving | Incident Commander | 15 min |
| Latency causing cascading timeouts in other services | Incident Commander | Immediately |
| Database unresponsive (can't even run `SELECT 1`) | DBA + Incident Commander | Immediately |
| Any mitigation increases latency further | Incident Commander | Immediately |
| Root cause unknown after 20 min of diagnosis | L2 / Team Lead | 20 min |
