# Debugging Methodology

> **Category:** Foundations | Debugging
> **Difficulty:** Basic to Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#debugging` `#methodology` `#use` `#red`

---

## Table of Contents

1. [The Scientific Method of Debugging](#the-scientific-method-of-debugging)
2. [USE Method — Resources](#use-method--resources)
3. [RED Method — Services](#red-method--services)
4. [The Half-Split Method](#the-half-split-method)
5. [Front-to-Back vs. Back-to-Front](#front-to-back-vs-back-to-front)
6. [Common Debugging Commands Reference](#common-debugging-commands-reference)
7. [Scenario Walkthroughs](#scenario-walkthroughs)
8. [Anti-Patterns](#anti-patterns)

---

## The Scientific Method of Debugging

### The Process

```text
╔═══════════════════════════════════════════════════════════╗
║ 1. OBSERVE     What are the symptoms?                    ║
║      ↓                                                    ║
║ 2. HYPOTHESIZE  "I think the DB connection pool is full"  ║
║      ↓                                                    ║
║ 3. PREDICT      "If true, SHOW PROCESSLIST will show >100"║
║      ↓                                                    ║
║ 4. TEST         Run the query                             ║
║      ↓                                                    ║
║ 5. EVALUATE     Confirmed? → Fix. Rejected? → New hypo.   ║
╚═══════════════════════════════════════════════════════════╝
```

### Why This Matters

When you're paged at 3 AM, your brain wants to jump to conclusions. *Don't.* Guessing leads to:
- Restarting the wrong service (no change, 5 minutes wasted)
- Rolling back a deploy that wasn't the cause (no change, 3 minutes wasted + confusion)
- Changing a config that makes things WORSE (30 extra minutes of outage)

The scientific method forces you to **let evidence guide you**, not intuition.

### Applied Example: Payment API Slowdown

```text
STEP 1: OBSERVE
  What: payment-api p99 latency = 28s (normal: 200ms)
  When: Started at 14:23 UTC
  Scope: All regions, all endpoints
  Recent changes: No deploys in last 2 hours

STEP 2: HYPOTHESIS A
  H: "The database is slow"
  P: "pg_stat_activity will show long-running queries, and
      pg_stat_statements will show queries with high mean_time"
  T: Connect to DB, check pg_stat_activity
     SELECT pid, query, state, age(now(), query_start) AS duration
     FROM pg_stat_activity
     WHERE state = 'active'
     ORDER BY query_start;
  E: Found 3 queries running for > 20s. HYPOTHESIS CONFIRMED.

STEP 3: HYPOTHESIS B (refining)
  H: "The slow queries are caused by a missing index"
  P: "EXPLAIN ANALYZE will show a sequential scan on a large table"
  T: EXPLAIN ANALYZE <the slow query>;
  E: Sequential Scan on transactions (rows=8,500,000). Confirmed.

STEP 4: HYPOTHESIS C (refining further)
  H: "The index was accidentally dropped during last week's migration"
  P: "pg_indexes won't have idx_transactions_merchant_date"
  T: SELECT indexname FROM pg_indexes WHERE tablename = 'transactions';
  E: idx_transactions_merchant_date does not exist.
     Confirmed — index was dropped.

STEP 5: FIX
  - Mitigate: kill the long-running queries to relieve pressure (5 min)
  - Fix: recreate the index (3 min)
  - Verify: EXPLAIN ANALYZE now shows Index Scan
  - Monitor: p99 latency back to 200ms

TIME SAVED: 8 minutes of systematic debugging vs potentially hours
           of "maybe it's the cache? restart the cache? no?
           maybe it's the network? ping test? no?"
```

---

## USE Method — Resources

Developed by Brendan Gregg. For analyzing the performance of **physical resources**: CPU, memory, disks, network interfaces.

### The Three Questions

| Question | Meaning | Linux Tool |
|----------|---------|------------|
| **Utilization** | How busy is the resource? (percent used) | `top`, `vmstat`, `iostat` |
| **Saturation** | How much backlog is there? (queue depth) | `uptime` (load), `iostat` (avgqu-sz) |
| **Errors** | How many failures? | `perf`, `dmesg`, `netstat` |

### CPU — USE Method

```bash
# UTILIZATION: What % of CPU is in use?
mpstat 1 5
# Output: %usr %sys %iowait %idle
# If %idle < 10% → CPU is highly utilized

# SATURATION: Is work backing up?
uptime
# Output: load average: 15.2, 12.1, 9.8
# On an 8-core system, load 8.0 = 100% saturation
# Load 15.2 on 8 cores → 7.2 threads waiting → SATURATED

# ERRORS: Are there CPU-level errors?
perf stat -e cpu/event=0x9c/ -a sleep 5  # Check for MCE (Machine Check Exceptions)
dmesg | grep -i "hardware error"
```

**Scenario — CPU Saturation**:
```text
OBSERVATION: api-server nodes have load average 24 on 8-core machines
HYPOTHESIS:  CPU is saturated
TEST:        mpstat shows %idle = 0%, %iowait = 2%, %usr = 94%, %sys = 4%
             → CPU is fully utilized by user-space processes
HYPOTHESIS:  A specific process is eating CPU
TEST:        top -o %CPU → java process at 780% CPU (multi-threaded)
HYPOTHESIS:  GC thrashing (frequent full GC pauses causing CPU spikes)
TEST:        jstat -gc <pid> 1s → FGC (Full GC count) increasing every 3s
CONFIRMED:   JVM heap is too small, constant full GC
FIX:         Increase heap size, restart, or scale horizontally
```

### Memory — USE Method

```bash
# UTILIZATION: How much memory is allocated?
free -h
# Output: Mem: 15G used, 1.2G free (15/16 = 94% utilized)

# SATURATION: Is swapping happening? (swapping = memory pressure)
vmstat 1 5
# Output: si (swap in) and so (swap out) columns
# Non-zero so → system is swapping → MEMORY SATURATED

# ERRORS: OOM killer activity?
dmesg | grep -i "out of memory"
dmesg | grep -i "killed process"
journalctl -k | grep -i oom
```

**Scenario — Memory Exhaustion (OOM)**:
```text
OBSERVATION: payment-service pods restarting every 10 min. CrashLoopBackOff.
HYPOTHESIS:  OOM kill
TEST:        kubectl describe pod payment-service-xxx | grep OOMKilled
             → "Last State: Terminated, Reason: OOMKilled, Exit Code: 137"
CONFIRMED:   Container killed by OOM killer
HYPOTHESIS:  Memory leak in application
TEST:        kubectl top pod (before crash): memory climbing 200MB → 800MB → 1.6GB → OOM
             memory limit is 2GB. Steady climb suggests leak.
FIX:         Short term: increase memory limit to 4GB (buy time)
             Long term: profile heap dump for leak. Found: unbounded cache
                        of user sessions never evicted.
```

### Disk — USE Method

```bash
# UTILIZATION: How much disk space is used?
df -h
# Output: /dev/sda1  100G  94G  1.2G  99% /
# → 99% utilized. CRITICAL.

# SATURATION: How long is the I/O queue?
iostat -x 1 5
# Output: avgqu-sz (average queue size) and await (average wait time)
# await > 20ms on SSD → saturated
# await > 50ms on HDD → saturated
# avgqu-sz > 1 per disk → saturated

# ERRORS: I/O errors, bad sectors
dmesg | grep -i "i/o error"
smartctl -a /dev/sda | grep -i error
```

**Scenario — Disk Full**:
```text
OBSERVATION: api server returning 500 errors. Logs show "No space left on device"
HYPOTHESIS:  Disk is full
TEST:        df -h → /var/log is 98% full (100G volume)
HYPOTHESIS:  Something is writing excessively to logs
TEST:        du -sh /var/log/* | sort -rh | head -5
             → /var/log/app/error.log = 85G
HYPOTHESIS:  Error log is growing rapidly (app is throwing exceptions)
TEST:        tail -f /var/log/app/error.log → 1000 lines/second of
             NullPointerException in payment processing
MITIGATE:    Rotate logs: logrotate -f /etc/logrotate.d/app
FIX:         Fix the NullPointerException. Add log sampling to avoid
             1000 identical log lines per second.
PREVENT:     Add disk usage alert (df > 80% → warning, > 90% → critical)
```

### Network — USE Method

```bash
# UTILIZATION: How much bandwidth is being used?
iftop -i eth0
nload eth0
# If near interface speed (1 Gbps, 10 Gbps) → saturated

# SATURATION: Are there drops? Retransmits? Backlogged queues?
netstat -s | grep -i retrans
# → 4521 segments retransmitted  ← TCP retransmits = network congestion

ss -s
# → shows closed vs established connections
# High number of TIME_WAIT sockets → connection churn

# ERRORS: Interface errors, drops
ip -s link show eth0
# → RX errors: 132, TX errors: 45, RX dropped: 2012
# Non-zero → network card or driver issue
```

**Scenario — Network Saturation**:
```text
OBSERVATION: API p99 latency = 3s. DB query time = 15ms (fast).
             Large gap between total request time and DB time.
HYPOTHESIS:  Network congestion between app and DB
TEST:        ping -c 100 <db-host> → avg = 2.8s, stddev = 1.2s
             (expected: < 1ms in same VPC)
             → MASSIVE latency and jitter
TEST:        iperf3 between app-node and db-node → bandwidth = 5 Mbps
             (expected: 1 Gbps in same VPC)
HYPOTHESIS:  Network saturation or bad network configuration
TEST:        Cloud provider console → app and DB are in DIFFERENT
             availability zones with a misconfigured routing table.
             Traffic is going AZ-A → public internet → AZ-B instead of
             AZ-A → internal VPC peering → AZ-B.
FIX:         Correct the VPC route table. Latency drops to < 1ms.

EVIDENCE TRAIL:
  - RED method flagged high duration but low DB time
  - Half-split eliminated DB, pointed to network
  - Ping confirmed network latency
  - Cloud provider logs confirmed misrouting
```

---

## RED Method — Services

Developed by Tom Wilkie. For monitoring and debugging **services** (not resources).

### The Three Metrics

| Metric | Meaning | PromQL Example |
|--------|---------|---------------|
| **Rate** | Requests per second | `rate(http_requests_total[5m])` |
| **Errors** | Failed requests per second | `rate(http_requests_total{status=~"5.."}[5m])` |
| **Duration** | Latency distribution | `histogram_quantile(0.99, rate(http_request_duration_seconds_bucket[5m]))` |

### Interpreting RED Metrics Together

```text
Rate      Errors    Duration   | Diagnosis
---------- --------- ----------|-----------------------------------------
Normal    Normal    Normal     | Healthy. Go back to bed.
High      Normal    Normal     | Load increase. Check if legitimate or DDoS.
High      High      High       | Overloaded. Service can't handle current load.
Normal    High      Normal     | Bug. Specific requests failing (e.g., certain input).
Normal    High      High       | Partial failure. Some code path is slow AND failing.
Low       Normal    Normal     | Downstream is down. Users can't reach service.
Low       High      High       | Catastrophic failure. Almost no requests arrive,
          (100%)               | and those that do fail.
High      Normal    High       | Saturation. Service is handling load but slowly.
                              | Check USE metrics for bottleneck.
```

### RED Method — Dashboards and Alerts

```promql
# SLI: Fraction of requests that are successful AND fast (< 500ms)
- record: sli:api:availability
  expr: |
    (
      sum(rate(http_requests_total{status!~"5.."}[5m])) by (service)
      /
      sum(rate(http_requests_total[5m])) by (service)
    )
    *
    (
      sum(rate(http_request_duration_seconds_bucket{le="0.5"}[5m])) by (service)
      /
      sum(rate(http_request_duration_seconds_count[5m])) by (service)
    )

# Alert: Error rate above 1%
- alert: HighErrorRate
  expr: |
    sum(rate(http_requests_total{status=~"5.."}[5m])) by (service)
    /
    sum(rate(http_requests_total[5m])) by (service)
    > 0.01
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "{{ $labels.service }} error rate = {{ $value | humanizePercentage }}"

# Alert: p99 latency above 2s
- alert: HighLatency
  expr: |
    histogram_quantile(0.99,
      rate(http_request_duration_seconds_bucket[5m])
    ) > 2
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: "{{ $labels.service }} p99 latency = {{ $value }}s"
```

### RED Method Applied — Step by Step

```text
SCENARIO: Dashboard shows:
  Rate:     1200 req/s (normal: 1000)
  Errors:   180 req/s (15% — normal: < 1%)
  Duration: p99 = 8.2s (normal: p99 = 300ms)

DIAGNOSIS:
  All three metrics are elevated → service is overloaded.

NEXT STEPS:
  □ Check CPU/memory (USE method) → CPU at 97%, memory OK
  □ Check downstream services → DB latency is normal (5ms)
  □ Check if traffic spike is legitimate → CDN logs show normal user traffic
  □ Check recent deploys → No deploys in 24h

HYPOTHESIS:
  The service is simply under-provisioned for the increased load.

MITIGATE:
  kubectl scale deployment/api --replicas=10
  → Rate per pod drops. p99 latency drops to 400ms. Errors drop to < 1%.

RESOLVE:
  Configure HPA to scale based on CPU > 70%:
    kubectl autoscale deployment/api --min=3 --max=20 --cpu-percent=70
```

---

## The Half-Split Method

### Concept

Binary search through your technology stack. At each step, eliminate half the possible causes.

```text
CLIENT
  │
  ├── CDN ────────────────────  Is it the CDN?  (Check CDN status page)
  │
  ├── LOAD BALANCER ─────────  Is it the LB?    (Check LB metrics)
  │
  ├── API GATEWAY ───────────  Is it the gateway? (Check gateway health)
  │
  ├── APPLICATION ───────────  Is it the app?   (Check app health endpoint)
  │   │
  │   ├── CACHE ─────────────  Is it the cache? (Check Redis/Memcached stats)
  │   │
  │   └── DATABASE ──────────  Is it the DB?    (Check slow query log)
  │
  └── UPSTREAM API ──────────  Is it the upstream? (Check upstream status)
```

### Half-Split Walkthrough

```text
SYMPTOM: /api/orders endpoint returns 500 errors.

HALF-SPLIT STEP 1: Client-side or server-side?
  Test: curl from the server itself (bypass CDN + LB + Gateway)
    curl -H "Host: api.example.com" http://localhost:8080/api/orders
  Result: Returns 500. → Problem is server-side (eliminates CDN, LB, Gateway)
  Time saved: 2 minutes (vs checking CDN, DNS, LB individually)

HALF-SPLIT STEP 2: App code or dependency (DB/Cache/Upstream)?
  Test: Hit a simple endpoint that doesn't touch DB
    curl http://localhost:8080/health
  Result: Returns 200. → App server itself is healthy.
  Test: Hit an endpoint that touches cache but not DB
    curl http://localhost:8080/api/products (cached)
  Result: Returns 200. → Cache is fine.

  → By elimination, the problem is in the DB or DB-dependent code path.

HALF-SPLIT STEP 3: Is it the DB connection? Or the query?
  Test: Run a trivial DB query from the app server
    psql -h db-host -c "SELECT 1;"
  Result: Returns 1 in 2ms. → DB connection is fine.

  → By elimination, the problem is in the specific query for /api/orders.

HALF-SPLIT STEP 4: Is the query slow? Or returning bad data?
  Test: Explain analyze the query
    EXPLAIN ANALYZE SELECT * FROM orders WHERE user_id = 123;
  Result: Seq Scan on orders (rows=18,000,000). duration=28,000ms.
  → Query is doing a full table scan. Missing index.

ROOT CAUSE: Index on orders(user_id) was dropped during a migration.

TOTAL DEBUGGING STEPS: 4 (half-split)
VERSUS CHECKING EVERYTHING: CDN, DNS, LB, gateway, app health, app code,
                             cache, DB connection, DB query, upstream API
                             = 10+ steps (no method, just guessing)
```

---

## Front-to-Back vs. Back-to-Front

### When to Use Each

| Method | Start At | Best When |
|--------|----------|-----------|
| **Front-to-Back** | Client/Load Balancer | Problem is user-facing. You want to find WHERE the failure begins. |
| **Back-to-Front** | Database/Resource | Problem is performance-related. You suspect a resource bottleneck. |

### Front-to-Back Example

```text
SYMPTOM: Users report "Checkout page won't load."

FRONT-TO-BACK TRACE:

STEP 1 — Browser / Client
  curl -w "\n%{time_total}\n" https://shop.example.com/checkout
  → time_total: 30.001s (timed out)
  → Problem exists at the outermost layer

STEP 2 — CDN
  curl -H "Host: shop.example.com" https://cdn-origin.example.com/checkout
  → time_total: 30.001s
  → CDN is not the issue. Bypass it.

STEP 3 — Load Balancer
  curl -H "Host: shop.example.com" http://lb-internal.example.com/checkout
  → time_total: 30.001s
  → Load balancer is not the issue.

STEP 4 — Application
  curl http://app-node-01.internal:8080/checkout
  → time_total: 30.001s
  → Application is the bottleneck. Now investigate inside the app.

STEP 5 — App Logs
  tail -f /var/log/app/checkout.log
  → "DB query timeout after 30000ms: SELECT * FROM inventory WHERE sku = ?"
  → It's the DB query.

STEP 6 — Database
  SELECT * FROM inventory WHERE sku = 'ABC-123';
  → Hangs for 30s then errors.
  EXPLAIN ANALYZE → Seq Scan on inventory (rows=200M). No index on sku.

RESULT: Missing index on inventory.sku.
TIME:   6 steps, ~4 minutes to find root cause.
```

### Back-to-Front Example

```text
SYMPTOM: All services running slowly. No clear culprit from error logs.

BACK-TO-FRONT TRACE:

STEP 1 — Database (bottom of the stack)
  SELECT * FROM pg_stat_activity WHERE state != 'idle';
  → 247 active connections. max_connections = 250.
  → Connection pool nearly exhausted.

  SELECT count(*) FROM pg_stat_activity;
  → 249 connections, many "idle in transaction" for > 10 min.

STEP 2 — Why are connections held open?
  Application connection pool settings:
    spring.datasource.hikari.maximumPoolSize=30
    → 8 app instances × 30 connections = 240 possible connections.
    → At peak, all connections are in use.
    → DB max_connections = 250. Static allocation is 240/250 = 96%.

STEP 3 — Why are connections "idle in transaction"?
  App logs show: "BEGIN" but corresponding "COMMIT" is delayed.
  Code review: A developer wrapped an HTTP call to a slow upstream
  service INSIDE a database transaction.

  @Transactional  // ← THIS IS THE BUG
  public void createOrder(Order order) {
      orderRepository.save(order);
      emailService.sendConfirmation(order);  // HTTP call to email service
                                              // takes 5-30 seconds!
      paymentService.charge(order);           // Another HTTP call
  }

  The transaction stays open for the duration of both HTTP calls,
  holding a DB connection unusable for 30+ seconds.

FIX:
  1. Move email/payment calls OUTSIDE the @Transactional block
  2. Increase max_connections to 500 (short-term)
  3. Add connection pool monitoring

TIME: 3 steps from DB upward.
```

---

## Common Debugging Commands Reference

### Linux

```bash
# === CPU ===
top -o %CPU                    # Process-level CPU usage
mpstat 1 5                     # Per-core CPU stats
pidstat 1                      # Per-process CPU with I/O
perf top                       # Live CPU sampling (which functions?)

# === MEMORY ===
free -h                        # Memory overview
vmstat 1 5                     # Memory + swap activity
smem -tk                       # USS/PSS/RSS by process (shared mem aware)
pmap -x <pid>                  # Memory map of a process

# === DISK ===
df -h                          # Filesystem usage
du -sh /path/* | sort -rh | head -10  # Largest directories
iostat -x 1 5                  # Disk I/O stats (util%, await, queue)
lsof +L1                       # Find deleted-but-still-open files (disk space leak)
lsblk                          # Block devices

# === NETWORK ===
ss -tunp                       # All TCP/UDP sockets with processes
ss -s                          # Socket summary
iftop -i eth0                  # Bandwidth by connection
nethogs eth0                   # Bandwidth by process
tcpdump -i eth0 port 443 -w capture.pcap  # Packet capture
mtr <host>                     # Combines ping + traceroute (identify hop loss)

# === PROCESSES ===
ps aux --sort=-%mem | head -20 # Top processes by memory
pstree -p <pid>                # Process tree
strace -p <pid> -c             # Syscall summary (what is the process doing?)
strace -p <pid> -e trace=network  # Only network syscalls
lsof -p <pid>                  # All files/sockets opened by process

# === SYSTEM ===
dmesg -T | tail -50            # Recent kernel messages with timestamps
journalctl -xe -n 100          # Recent systemd journal entries
uptime                         # Load averages
sar -A                         # Historical system activity (if sysstat installed)
```

### Kubernetes

```bash
# === PODS ===
kubectl get pods -n <ns>                    # Pod status
kubectl describe pod <pod> -n <ns>          # Events, conditions, container statuses
kubectl logs <pod> -n <ns> --tail=100 -f    # Recent logs, follow
kubectl logs <pod> -n <ns> --previous       # Logs from crashed container
kubectl top pod -n <ns>                     # CPU/Memory usage per pod

# === DEPLOYMENTS ===
kubectl rollout history deployment/<name> -n <ns>  # Deploy history
kubectl rollout undo deployment/<name> -n <ns>     # Rollback
kubectl rollout status deployment/<name> -n <ns>   # Status of rollout

# === DEBUG ===
kubectl exec -it <pod> -n <ns> -- /bin/sh   # Shell into pod
kubectl run debug --rm -it --image=busybox -- sh  # Ephemeral debug pod
kubectl port-forward <pod> 8080:8080 -n <ns>       # Access pod locally
kubectl cp <pod>:/path/file ./local-file -n <ns>   # Copy from pod

# === NODES ===
kubectl describe node <node>                # Node conditions, events, allocatable
kubectl top node                            # CPU/Memory per node
kubectl get events -n <ns> --sort-by='.lastTimestamp'  # Recent events

# === RESOURCES ===
kubectl api-resources                       # All available resource types
kubectl get hpa -n <ns>                     # Autoscaler status
kubectl get pdb -n <ns>                     # Pod disruption budgets
```

### Databases (PostgreSQL)

```sql
-- Current activity
SELECT pid, usename, application_name, state, query,
       age(now(), query_start) AS duration
FROM pg_stat_activity
WHERE state != 'idle'
ORDER BY query_start;

-- Long-running queries (> 5 min)
SELECT pid, age(now(), query_start) AS duration, query
FROM pg_stat_activity
WHERE state = 'active'
  AND age(now(), query_start) > interval '5 minutes'
ORDER BY query_start;

-- Locks held
SELECT l.pid, l.locktype, l.mode, l.granted,
       a.query, age(now(), a.query_start) AS duration
FROM pg_locks l
JOIN pg_stat_activity a ON l.pid = a.pid
WHERE NOT l.granted
ORDER BY a.query_start;

-- Blocking queries (who is blocking whom)
SELECT blocked.pid AS blocked_pid,
       blocked.query AS blocked_query,
       blocking.pid AS blocking_pid,
       blocking.query AS blocking_query,
       age(now(), blocked.query_start) AS blocked_for
FROM pg_stat_activity blocked
JOIN pg_locks blocked_locks ON blocked.pid = blocked_locks.pid
JOIN pg_locks blocking_locks ON blocked_locks.lock_type = blocking_locks.lock_type
  AND blocked_locks.relation = blocking_locks.relation
  AND blocked_locks.pid != blocking_locks.pid
JOIN pg_stat_activity blocking ON blocking_locks.pid = blocking.pid
WHERE NOT blocked_locks.granted
  AND blocking_locks.granted;

-- Kill a query
SELECT pg_terminate_backend(<pid>);
SELECT pg_cancel_backend(<pid>);  -- gentler: cancels but doesn't kill connection

-- Connection count
SELECT count(*) AS total,
       count(*) FILTER (WHERE state = 'active') AS active,
       count(*) FILTER (WHERE state = 'idle') AS idle,
       count(*) FILTER (WHERE state = 'idle in transaction') AS idle_in_txn
FROM pg_stat_activity;

-- Current settings
SELECT name, setting FROM pg_settings
WHERE name IN ('max_connections', 'shared_buffers', 'effective_cache_size',
               'work_mem', 'maintenance_work_mem');

-- Replication lag
SELECT client_addr, state,
       pg_wal_lsn_diff(sent_lsn, write_lsn) AS write_lag,
       pg_wal_lsn_diff(sent_lsn, flush_lsn) AS flush_lag,
       pg_wal_lsn_diff(sent_lsn, replay_lsn) AS replay_lag
FROM pg_stat_replication;

-- Table sizes (find large tables)
SELECT schemaname, tablename,
       pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC
LIMIT 20;

-- Index usage (find unused indexes)
SELECT schemaname, tablename, indexname, idx_scan, idx_tup_read, idx_tup_fetch
FROM pg_stat_user_indexes
ORDER BY idx_scan ASC
LIMIT 20;

-- Slow query analysis (requires pg_stat_statements extension)
SELECT queryid,
       calls,
       mean_exec_time::numeric(10,2) AS avg_ms,
       max_exec_time::numeric(10,2) AS max_ms,
       rows,
       LEFT(query, 200) AS query_preview
FROM pg_stat_statements
ORDER BY mean_exec_time DESC
LIMIT 20;
```

### Redis

```bash
# === CONNECTION ===
redis-cli -h <host> -p <port> ping          # Basic connectivity

# === METRICS ===
redis-cli info stats                         # General statistics
redis-cli info memory                        # Memory usage
redis-cli info replication                   # Replication status (slave/replica)
redis-cli info clients                       # Connected clients
redis-cli info commandstats                  # Per-command stats

# === MONITORING ===
redis-cli --latency                          # Continuous latency sampling
redis-cli --bigkeys                          # Find large keys
redis-cli --scan --pattern '*' | head        # Scan all keys (cursor-based, safe)

# === DEBUG ===
redis-cli client list                        # All connected clients + info
redis-cli slowlog get 25                     # Recent slow queries
redis-cli monitor                            # Stream all commands (CAREFUL: production impact!)
redis-cli --stat                             # Live stats (hits/misses, ops/sec)
```

---

## Scenario Walkthroughs

### Scenario 1: Payment API Slow — Comprehensive Debug

```text
===== SYMPTOMS =====
  Alert: "payment-api p99 latency > 5s"
  Dashboards: latency climbing 200ms → 12s over 10 minutes
  Error rate: 0% (requests succeed, just slowly)
  Throughput: dropping (clients timing out)

===== STEP 1: OBSERVE (RED Method) =====
  Rate:     850 req/s (normal: 1000 — slightly down, clients timing out)
  Errors:   0% (requests eventually complete)
  Duration: p50 = 6s, p95 = 11s, p99 = 12s (normal: p50=50ms, p99=200ms)

  → All requests are slow. Not an error. Service is saturated.

===== STEP 2: HALF-SPLIT =====
  Is it the application code, or a dependency?

  Check app-level timing:
  # Add temporary log to capture internal timing
  # Shows: total_request=11.2s, auth=2ms, validation=5ms, db_query=11.1s
  → DB query is taking 11.1s. It's the database.

===== STEP 3: HYPOTHESIZE (DB) =====
  H: "The database is under resource pressure"
  P: "USE metrics will show saturation"

  # CPU
  ssh db-primary
  mpstat 1 5
  → %idle = 62%  ← CPU is fine

  # Memory
  free -h
  → used=54G/64G, cached=28G  ← Memory is fine

  # Disk
  iostat -x 1 5
  → %util = 98%, avgqu-sz = 45, await = 320ms
  → DISK IS COMPLETELY SATURATED

===== STEP 4: HYPOTHESIZE (Disk) =====
  H: "Something is hammering the disk"
  P: "iotop or pidstat will show which process"

  pidstat -d 1 5
  → postgres: disk write = 85 MB/s (this is a single EBS volume with
     100 MB/s throughput limit)

  H: "What is writing so much?"
  P: "pg_stat_activity will show write-heavy queries"

  SELECT pid, query, state, age(now(), query_start) AS duration
  FROM pg_stat_activity
  WHERE state = 'active'
  ORDER BY query_start;

  → 8 concurrent queries: INSERT INTO audit_log (user_id, action, payload)
    SELECT user_id, action, payload FROM raw_events WHERE processed = false

  → An ETL job is processing a backlog of 120M raw events and writing
    to the audit_log table, which also triggers 3 indexes to be updated.

  Disk throughput: 100 MB/s (EBS gp2 volume limit for this size)
  ETL write rate: 85 MB/s + normal traffic: 20 MB/s = 105 MB/s
  → Disk saturated. Everything that needs disk I/O (all writes, index
    updates, WAL flushes) is queued.

===== STEP 5: MITIGATE =====
  # Option A: Kill the ETL job (business decision — data processing delayed)
  SELECT pg_terminate_backend(38491);  -- ETL process PID

  Option B: Throttle the ETL job
  # Insert a SLEEP between batches in the ETL script

  Option C: Reduce impact of normal writes
  # Temporarily set synchronous_commit = off (data loss risk, fast)

  Decision: Kill ETL job. It can be resumed during off-peak hours.

  → Disk util drops to 25%. Latency drops to 200ms. SERVICE RESTORED.

===== STEP 6: RESOLVE =====
  1. ETL job: Add throttling (max 30 MB/s write rate). Schedule for 2AM.
  2. Disk: Upgrade EBS volume from gp2 to gp3 (3000 IOPS, 125 MB/s baseline
     → 1000 MB/s throughput at larger sizes, same cost).
  3. Monitoring: Add alert on disk utilization > 80%.

===== TIMELINE =====
  T+0:    Alert fires
  T+3:    RED method confirms all requests slow
  T+6:    Half-split identifies DB
  T+9:    USE method identifies disk saturation
  T+12:   Query analysis identifies ETL as culprit
  T+14:   ETL job killed, service restored
  T+20:   Root cause documented, fix planned

  Total debugging time: 14 minutes
```

### Scenario 2: "It Works on My Machine" — Environment Mismatch

```text
===== SYMPTOMS =====
  Deploy v2.4.0 to production. Immediately: "503 Service Unavailable"
  Deploy v2.4.0 to staging: works perfectly.
  Deploy v2.3.0 back to production: works perfectly.
  Problem is specific to v2.4.0 IN PRODUCTION.

===== HALF-SPLIT: Environment or Code? =====
  v2.3.0 + prod env = works   }
  v2.4.0 + staging env = works } → Problem is v2.4.0 + specific
  v2.4.0 + prod env = fails    }   production environment difference

  What's different between staging and production?

  CHECKLIST:
  □ App version:         prod=v2.4.0, staging=v2.4.0  ✓ Same
  □ Config:               diff config/prod.yml config/staging.yml
                           → No significant differences
  □ DB schema:            Both on migration V.104           ✓ Same
  □ OS/Kernel:            Both Debian 12                    ✓ Same
  □ ENV VARS:             diff <(kubectl get secret prod -o yaml)
                                <(kubectl get secret staging -o yaml)
                           → PROD has DATABASE_URL=postgres://...
                              STAGING has DATABASE_URL=postgresql://...
                               ↑ EXTRA "ql" in staging URL

  v2.4.0 introduced a new DB connection library that strictly validates
  the connection URL scheme. "postgres://" is technically non-standard
  (should be "postgresql://"). The old library accepted both. The new
  library rejects "postgres://".

  → Connection pool fails to initialize → 503 on every request.

===== FIX =====
  kubectl edit secret prod-db-credentials
  # Change DATABASE_URL from postgres:// to postgresql://
  kubectl rollout restart deployment/api
  → Service restores in 30 seconds.

===== LESSON =====
  The "it works on staging" trap is almost always:
  1. Environment variable difference
  2. Data difference (staging has 100K rows, prod has 100M)
  3. Network difference (staging can reach a service, prod can't)
  4. Resource difference (staging has more CPU per request because it
     serves fewer requests)
```

### Scenario 3: Cache Stampede (Thundering Herd)

```text
===== SYMPTOMS =====
  Every Monday at 9:00 AM: site slows to a crawl for 5-10 minutes.
  Self-recovers every time.

  Dashboards during event:
  - Redis CPU: 100%
  - App CPU: 90%
  - DB CPU: 65%
  - Error rate: 2% (mostly timeouts)

===== HYPOTHESIS =====
  Cache stampede: Many cache keys expire simultaneously at 9 AM Monday
  (common TTL: "expire at start of business week"). All requests miss
  cache and hit the origin (DB), overloading both DB and the cache itself
  (as it tries to serve old data while being overwhelmed with writes).

===== TEST =====
  Before next Monday:
  # Check Redis key expiry patterns
  redis-cli --scan --pattern '*' | while read key; do
    ttl=$(redis-cli ttl "$key")
    echo "$key $ttl"
  done | sort -k2 -n | head -20

  → 500,000 keys all expire between 08:55 and 09:05 AM Monday
  → Typical TTL pattern: set at Friday 5 PM with TTL 259200 (3 days = Monday 5 PM)
    BUT someone ran a cache-warm script at 9AM Monday that wrote
    keys with inconsistent TTLs.

===== MITIGATE (immediate) =====
  # Add staggered TTL jitter
  # Instead of: EXPIRE key 86400
  # Use:        EXPIRE key 86400 + random(0, 3600)
  # This spreads expiration over 1 hour instead of 1 minute.

===== RESOLVE (permanent) =====
  1. Implement probabilistic early recomputation (PER):
     if ttl < 1 hour and random() < 0.01: recompute cache value

  2. Implement request coalescing:
     if cache miss:
       acquire lock on key
       if lock acquired:
         recompute from DB
         set cache
         release lock
       else:  # another thread is recomputing
         wait for lock release
         read from cache

  Redis-based request coalescing:
  ```python
  import redis
  import time

  def get_with_coalescing(key, db_query_fn, ttl=3600):
      r = redis.Redis()
      value = r.get(key)
      if value is not None:
          return value

      lock_key = f"lock:{key}"
      # Try to acquire the recompute lock
      if r.set(lock_key, "1", nx=True, ex=10):
          try:
              value = db_query_fn()
              r.setex(key, ttl + random.randint(0, 3600), value)
              return value
          finally:
              r.delete(lock_key)
      else:
          # Another process is recomputing. Wait and retry.
          for _ in range(20):
              time.sleep(0.05)
              value = r.get(key)
              if value is not None:
                  return value
          # Fallback: recompute anyway after timeout
          value = db_query_fn()
          r.setex(key, ttl, value)
          return value
  ```

===== LESSON =====
  Cache expiry storms are predictable and preventable:
  1. Never let many keys expire at the same exact time
  2. Use staggered TTLs with random jitter
  3. Use request coalescing (only ONE request recomputes the value)
  4. Monitor Redis key expiry rate (if it suddenly spikes, you'll know why)
```

---

## Anti-Patterns

### 1. "Let Me Just Restart Everything"

```text
Engineer sees error. Engineer restarts service. Error goes away.
Engineer feels good. Engineer does not investigate root cause.
Error returns 3 days later at 3 AM with 10x severity.

FIX: Restarting is valid MITIGATION ("stop the bleeding").
     But ALWAYS file a ticket to investigate root cause.
     A restart without investigation = technical debt.
```

### 2. "I've Seen This Before" (Pattern Matching Fallacy)

```text
Engineer sees "502 Bad Gateway" on api-gateway.
"Ah yes, this happened last month. The auth service was down."
Spends 30 minutes debugging auth service. Auth service is fine.

This time, the issue is expired TLS cert on the upstream.
Same symptom, different cause.

FIX: Your first hypothesis can be "looks like last month's incident."
     But you MUST verify. Don't jump to fix without confirming.
     "Last time it was auth. Let's check auth first." is correct.
     "It's definitely auth, I'm restarting it." is wrong.
```

### 3. "Changing Too Many Things at Once"

```text
Engineer: "I'll restart the service, rotate the logs, update the config,
          and bump the connection pool. One of those should fix it."

Two things happen:
  1. The problem is fixed (yay!) but you don't know WHICH change fixed it.
  2. You introduced a NEW problem with one of the changes, and now you're
     debugging two problems at once.

FIX: Change ONE thing. Observe. Did it help? Good. Still broken? Undo
     that change, try the next thing. One variable at a time.
```

### 4. "Deleting Evidence"

```text
Engineer fixes the problem by deleting /var/log to free up disk space.
Then deletes the old container, scales down old pods, clears the
deployment history.

Now there's no way to determine:
  - Which log file was too large
  - What was writing to it
  - Why log rotation didn't work

FIX: Take screenshots. Save `df -h` output. Save `du -sh` output.
     Save relevant log excerpts. BEFORE cleaning up.
     The evidence you need for the post-mortem is most accessible
     DURING the incident. Capture it.
```

### 5. "Assuming Monitoring is Correct"

```text
Grafana shows: "Error Rate = 0%"
But users are reporting errors.

Engineer: "The dashboard says everything is fine. The users must be wrong."

Reality: The Prometheus scrape target for the production cluster was
changed to the staging cluster during a config update 3 days ago.
The dashboard has been showing staging data this whole time.

FIX: Trust but verify. If users report errors and dashboards show green,
     verify the data pipeline: Is the scrape target correct? Is the
     metric being recorded? Is the alert expression valid?

     Test: curl the application's /metrics endpoint directly. Do the
     metrics look right? Compare with what the dashboard shows.
```

### 6. "The Single Diagnostic Test"

```text
Engineer: "The database must be the problem because the query is slow."

Runs ONE test:
  SELECT * FROM pg_stat_activity;
  → Shows 200 connections. Normal for this time of day.

Concludes: "Database is fine. Must be the network."

Reality: They needed to ALSO check:
  - Slow query log (would have shown the problem query)
  - Connection pool wait time (would have shown pool exhausted)
  - Disk I/O (would have shown saturation)
  - Lock contention (would have shown a blocking query)

FIX: Triangulate. Confirm a hypothesis with at least TWO independent
     observations. Don't trust a single diagnostic test.
```
