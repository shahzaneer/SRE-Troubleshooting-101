# Database Connection Exhaustion Runbook

> **Category:** On-Call | Database
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#runbook` `#database` `#postgres` `#oncall`

---

## 1. DETECT — Symptoms

Applications throw one of these error messages:

```
FATAL: sorry, too many clients already
FATAL: remaining connection slots are reserved for non-replication superuser connections
org.postgresql.util.PSQLException: FATAL: too many connections
ConnectionPoolTimeoutException: Timeout after 30000ms waiting for a connection
```

Also may manifest as:
- Applications hanging / timing out on startup
- 503 errors from services that can't connect to DB
- Existing connections working but new ones can't be established

**Confirm the alert:**

```bash
# Grafana / Datadog — DB connection graph shows max_connections reached
# AWS RDS — DatabaseConnections metric at ceiling

# Try connecting from application host:
psql "$DATABASE_URL" -c "SELECT 1;"
# If this also fails → DB is maxed out.
```

---

## 2. ASSESS — Database Connection Overview

### 2a. Connect as superuser (required for most diagnostics)

Superuser has reserved connection slots that regular users cannot use.

```bash
# Connect with superuser credentials:
psql -U postgres -h db-prod.internal -d app_prod
# or via RDS master user:
psql -U rdsadmin -h prod-db.xxxx.us-east-1.rds.amazonaws.com -d app_prod
```

### 2b. Connection State Summary

```sql
-- Quick overview — which states are connections in?
SELECT state, count(*) AS connections
FROM pg_stat_activity
WHERE datname = 'app_prod'
GROUP BY state
ORDER BY count(*) DESC;

-- Expected/healthy output:
--   state       | connections
--   active      | 15
--   idle        | 45
--   (idle rows) | 0
--
-- Problematic output:
--   state         | connections
--   idle          | 250          <-- too many idle, not closing
--   idle in transaction | 180   <-- VERY bad, holding locks
--   active        | 20
```

### 2c. Current vs Max

```sql
-- Total connections vs max:
SELECT
  count(*) AS current_connections,
  (SELECT setting::int FROM pg_settings WHERE name = 'max_connections') AS max_connections,
  round(count(*) * 100.0 / (SELECT setting::int FROM pg_settings WHERE name = 'max_connections'), 1) AS pct_used
FROM pg_stat_activity;
```

---

## 3. IMMEDIATE MITIGATION — Free Connections (Buy Time)

Execute these in order. Each frees connections without disrupting active queries.

### 3a. Kill Old Idle Connections

```sql
-- Connections idle for >5 minutes with no active query:
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE state = 'idle'
  AND query_start < now() - interval '5 minutes'
  AND pid != pg_backend_pid();

-- Verify how many were killed:
-- Re-run the state summary from 2b.
```

### 3b. Kill Idle-in-Transaction Connections (⚠️ More Aggressive)

Idle-in-transaction = BEGIN was issued but never COMMIT/ROLLBACK. These **hold locks** and are a common cause of cascading issues.

```sql
-- Idle in transaction for >1 minute:
SELECT
  pid,
  application_name,
  usename,
  now() - xact_start AS xact_duration,
  now() - state_change AS idle_since,
  LEFT(query, 100) AS last_query
FROM pg_stat_activity
WHERE state = 'idle in transaction'
  AND xact_start < now() - interval '1 minute'
ORDER BY xact_duration DESC;

-- Kill them:
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE state = 'idle in transaction'
  AND xact_start < now() - interval '1 minute'
  AND pid != pg_backend_pid();
```

### 3c. Kill Long-Running Queries (Last Resort Before DB Restart)

```sql
-- Queries running >5 minutes that might be stuck:
SELECT
  pid,
  now() - query_start AS runtime,
  state,
  wait_event_type,
  LEFT(query, 200) AS query_snippet
FROM pg_stat_activity
WHERE state = 'active'
  AND query_start < now() - interval '5 minutes'
  AND pid != pg_backend_pid()
ORDER BY runtime DESC;

-- Kill the longest-running one first:
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE state = 'active'
  AND query_start < now() - interval '5 minutes'
  AND pid != pg_backend_pid()
ORDER BY query_start ASC
LIMIT 5;  -- kill worst 5. Then re-assess.
```

---

## 4. FIND ROOT CAUSE

### 4a. Which Service Is Hoarding Connections?

```sql
-- Connection count by application:
SELECT
  application_name,
  count(*) AS connections,
  count(*) FILTER (WHERE state = 'active') AS active,
  count(*) FILTER (WHERE state = 'idle') AS idle,
  count(*) FILTER (WHERE state = 'idle in transaction') AS idle_in_txn
FROM pg_stat_activity
WHERE datname = 'app_prod'
GROUP BY application_name
ORDER BY connections DESC;
```

### 4b. Connection Leak Diagnosis

```sql
-- Connections open for >1 hour but idle — classic leak pattern:
SELECT
  pid,
  application_name,
  client_addr,
  backend_start,
  now() - state_change AS idle_for,
  LEFT(query, 100) AS last_query
FROM pg_stat_activity
WHERE state = 'idle'
  AND state_change < now() - interval '1 hour'
ORDER BY state_change ASC
LIMIT 20;
```

### 4c. Root Cause Categories

| Category | Symptom | Tool/Fix |
|----------|---------|----------|
| **Pool misconfiguration** | pool_size * instance_count > max_connections | Math: `kubectl get deployment app -o jsonpath='{.spec.replicas}'` × pool size (check config) vs `SELECT setting FROM pg_settings WHERE name='max_connections'` |
| **Connection leak** | idle connections growing, never released | Check app code: connections not closed in `finally` blocks, orphaned transactions |
| **Slow queries** | active connections growing, each query takes minutes | Find slow queries (runbook section 4d) |
| **Downstream timeout** | app queries suceeed but take too long, piling up | Check network latency, I/O wait on DB |
| **Scaling event** | sudden scale-up of app instances → connection surge | Need PgBouncer to multiplex |

### 4d. Find the Slow Queries

```sql
-- Current slowest queries:
SELECT pid, now() - query_start AS duration, wait_event_type, LEFT(query, 200)
FROM pg_stat_activity
WHERE state = 'active' AND pid != pg_backend_pid()
ORDER BY duration DESC
LIMIT 10;

-- Historical slow queries (if pg_stat_statements enabled):
SELECT
  calls,
  round(mean_exec_time::numeric, 1) AS avg_ms,
  round(max_exec_time::numeric, 1) AS max_ms,
  LEFT(query, 150) AS query_snippet
FROM pg_stat_statements
ORDER BY mean_exec_time DESC
LIMIT 10;
```

---

## 5. PERMANENT FIXES

### 5a. Implement PgBouncer (Recommended)

PgBouncer multiplexes many application connections into fewer actual DB connections.

```
# Key pgbouncer.ini settings:
[databases]
app_prod = host=prod-db.rds.amazonaws.com port=5432 dbname=app_prod

[pgbouncer]
pool_mode = transaction     # release back to pool after each transaction
default_pool_size = 25      # per user per database
max_client_conn = 1000      # app can connect to pgbouncer
max_db_connections = 50     # but only 50 actual DB connections
```

**Result:** 200 app instances × 20 pool connections = 4000 → becomes 50 DB connections via PgBouncer.

### 5b. Fix Application Connection Leak

```java
// DON'T:
Connection conn = dataSource.getConnection();
// ... use conn ...
// (no close — leaked)

// DO:
try (Connection conn = dataSource.getConnection()) {
    // ... use conn ...
} // auto-closed by try-with-resources
```

```python
# DON'T:
conn = get_connection()
cursor = conn.cursor()
# ... use cursor ...
# (no close — leaked)

# DO:
with get_connection() as conn:
    with conn.cursor() as cursor:
        # ... use cursor ...
# auto-closed by context manager
```

### 5c. Reduce Pool Size, Add Instances

If you have 50 instances each with pool_size=20, that's 1000 connections.

```
Fix: pool_size=5, instances=50 → 250 connections
Better: pool_size=20, instances=3, with PgBouncer → 60 connections
```

### 5d. Add Connection Timeout (Safety Valve)

```sql
-- Set at database level:
ALTER SYSTEM SET idle_in_transaction_session_timeout = '5min';
SELECT pg_reload_conf();

-- Set at user level:
ALTER ROLE app_user SET idle_in_transaction_session_timeout = '5min';
```

### 5e. Increase max_connections (Last Resort)

```sql
-- Each PG connection consumes ~10-15 MB of RAM.
-- DO NOT increase this without checking available memory.

-- Check current:
SELECT setting FROM pg_settings WHERE name = 'max_connections';

-- Check available memory:
SELECT pg_size_pretty(pg_database_size('app_prod'));

-- On RDS: modify parameter group, then:
ALTER SYSTEM SET max_connections = 500;
SELECT pg_reload_conf();
```

---

## 6. VERIFY

```sql
-- Re-check connection usage:
SELECT
  count(*) AS current,
  (SELECT setting::int FROM pg_settings WHERE name = 'max_connections') AS max,
  round(count(*) * 100.0 / NULLIF((SELECT setting::int FROM pg_settings WHERE name = 'max_connections'), 0), 1) AS pct
FROM pg_stat_activity;

-- Verify applications can connect:
-- (Run from app host):
psql "$DATABASE_URL" -c "SELECT 1;"
```

```bash
# From Kubernetes:
kubectl run db-test --rm -it --image=postgres:16 --restart=Never -n prod -- \
  psql "$DATABASE_URL" -c "SELECT 1;"
```

---

## 7. MONITORING GAPS

After the incident, ensure these alerts exist:

- [ ] **Alert:** DatabaseConnections > 80% of max_connections (5 min window)
- [ ] **Alert:** idle in transaction count > 20
- [ ] **Alert:** connection pool pending > 0 for >2 min (HikariCP metric)
- [ ] **Dashboard:** connections by application_name
- [ ] **Dashboard:** connections by state (active, idle, idle in transaction)

---

## ABORT CRITERIA

| Condition | Action | Timebox |
|-----------|--------|---------|
| Can't connect as superuser to terminate connections | Escalate to DBA team | 5 min |
| DB becomes unresponsive after terminating connections | Escalate to DBA team | Immediately |
| Connection exhaustion recurs within 30 min after mitigation | Root cause not fixed — escalate | 30 min |
| Considering DB restart | **DB restart causes complete outage.** Escalate to Incident Commander + DBA for approval. | Before restart |
| PgBouncer not available / not set up | Mitigate with pool size reduction + rolling restart of apps | — |
