# RDS Troubleshooting

> **Category:** AWS | RDS | Database
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#aws` `#rds` `#database` `#oncall`

---

## Table of Contents

1. [Connection Limit Exhaustion](#connection-limit-exhaustion)
2. [Slow Queries and Performance Insights](#slow-queries-and-performance-insights)
3. [Failover Behavior](#failover-behavior)
4. [Read Replica Lag](#read-replica-lag)
5. [IOPS Throttling](#iops-throttling)
6. [Deadlocks](#deadlocks)
7. [Parameter Groups](#parameter-groups)
8. [Backup, RTO, and RPO](#backup-rto-and-rpo)
9. [Python Database Connection with Failover](#python-database-connection-with-failover)
10. [Java HikariCP with Failover Configuration](#java-hikaricp-with-failover-configuration)

---

## Connection Limit Exhaustion

RDS instances have a `max_connections` limit based on instance size (memory). Exceeding it means new connections are rejected — even for the admin user.

### max_connections by Instance Type

| DB Engine | Max Connections Formula | Example (db.r5.large, 16GB) |
|-----------|------------------------|------------------------------|
| MySQL | `{DBInstanceClassMemory / 12582880}` | ~1350 |
| PostgreSQL | `LEAST({DBInstanceClassMemory / 9531392}, 5000)` | ~1700 |
| Aurora MySQL | `{DBInstanceClassMemory / 17000000}` (approx) | ~1000 |
| Aurora PostgreSQL | Similar to PostgreSQL | ~1700 |
| SQL Server | Varies by edition | Check AWS docs |

```sql
-- MySQL: Check current connections
SHOW STATUS LIKE 'Threads_connected';
SHOW VARIABLES LIKE 'max_connections';
-- Calculate utilization: Threads_connected / max_connections * 100

-- MySQL: See who's connected
SHOW PROCESSLIST;
SELECT user, host, db, count(*) AS connections
FROM information_schema.PROCESSLIST
GROUP BY user, host, db
ORDER BY connections DESC;

-- PostgreSQL: Check current connections
SELECT count(*) AS current_connections FROM pg_stat_activity;
SHOW max_connections;
-- Calculate utilization

-- PostgreSQL: See who's connected
SELECT usename, application_name, client_addr, state, count(*) AS connections
FROM pg_stat_activity
GROUP BY usename, application_name, client_addr, state
ORDER BY connections DESC;
```

### Scenario: "Lambda Flood Exhausts Connections"

```text
SYMPTOM: "After deploying a new API, RDS connections spiked to 500
         and all new connections get 'too many connections' error.
         We have 1000 Lambda functions behind API Gateway."

INVESTIGATION:
  RDS instance: db.r5.large (16GB)
  max_connections: ~1350 (MySQL)
  Peak connections: 1350 → connections rejected

  Root cause: Lambda functions are stateless.
  Each invocation opens a NEW database connection.
  There's no connection pooling across Lambda invocations.
  (Lambda containers ARE reused, but each cold start = new connection)

  With 1000 concurrent Lambda executions:
  = 1000 database connections opened simultaneously
  + existing 350 from other services
  = 1350 = max_connections → exhaustion

FIX OPTIONS:
  1. RDS Proxy (AWS managed connection pooling):
     Lambda → RDS Proxy → RDS
     RDS Proxy pools connections and shares them across Lambda invocations.
     1000 Lambda calls → RDS Proxy → 100 pooled connections → RDS.
     Cost: $0.015 per vCPU-hour (proxy capacity)
     Latency: ~2-5ms added per query (connection pooling overhead)

  2. Scale up RDS instance (more memory = more max_connections)
     db.r5.xlarge (32GB) → ~2700 max_connections (MySQL)
     But this is treating the symptom, not the cause.

  3. Implement application-level queuing:
     API Gateway → SQS → Lambda (with concurrency limit)
     Limits concurrent DB operations without losing requests

  4. For serverless: AWS Aurora Serverless v2
     Scales connections and capacity automatically.
```

### Monitoring Connection Utilization

```bash
# CloudWatch metric: DatabaseConnections
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name DatabaseConnections \
  --dimensions Name=DBInstanceIdentifier,Value=mydb \
  --start-time 2026-06-10T00:00:00Z \
  --end-time 2026-06-11T00:00:00Z \
  --period 3600 --statistics Maximum

# Alert: if DatabaseConnections > 80% of max_connections for 5 minutes
```

---

## Slow Queries and Performance Insights

### AWS Performance Insights

Performance Insights shows which queries consume the most load, broken down by waits.

```bash
# Enable Performance Insights (if not enabled)
aws rds modify-db-instance \
  --db-instance-identifier mydb \
  --enable-performance-insights \
  --performance-insights-retention-period 7

# View top SQL by load (last 1 hour)
aws pi get-resource-metrics \
  --service-type RDS \
  --identifier mydb \
  --start-time $(date -u -v-1H +%s) \
  --end-time $(date -u +%s) \
  --period-seconds 300 \
  --metric-queries '[{"Metric": "db.load.avg", "GroupBy": {"Group": "db.sql_tokenized", "Limit": 10}}]'

# View top waits
aws pi get-resource-metrics \
  --service-type RDS \
  --identifier mydb \
  --start-time $(date -u -v-1H +%s) \
  --end-time $(date -u +%s) \
  --period-seconds 300 \
  --metric-queries '[{"Metric": "db.load.avg", "GroupBy": {"Group": "db.wait_event", "Limit": 10}}]'
```

### Slow Query Logs

#### MySQL

```sql
-- Enable slow query log (dynamic parameter, no reboot)
SET GLOBAL slow_query_log = 'ON';
SET GLOBAL long_query_time = 2;  -- log queries > 2 seconds
SET GLOBAL log_queries_not_using_indexes = 'ON';

-- View slow queries
SELECT * FROM mysql.slow_log ORDER BY start_time DESC LIMIT 20;

-- Or via RDS console / CLI:
-- Download slow query log file
aws rds download-db-log-file-portion \
  --db-instance-identifier mydb \
  --log-file-name slowquery/mysql-slowquery.log \
  --output text > slowqueries.log
```

#### PostgreSQL

```sql
-- Set minimum duration for logging (in milliseconds)
-- In parameter group: log_min_duration_statement = 1000 (1 second)

-- View current setting
SHOW log_min_duration_statement;

-- Find long-running queries right now
SELECT pid, usename, application_name, state,
       age(now(), query_start) AS duration,
       query
FROM pg_stat_activity
WHERE state = 'active'
  AND query NOT LIKE '%pg_stat_activity%'
ORDER BY query_start;

-- Kill a specific query
SELECT pg_cancel_backend(pid);   -- graceful cancel
SELECT pg_terminate_backend(pid); -- force kill
```

### Using EXPLAIN ANALYZE

```sql
-- PostgreSQL: Show actual execution plan with timings
EXPLAIN (ANALYZE, BUFFERS, TIMING, FORMAT TEXT)
SELECT u.name, COUNT(o.id) AS order_count
FROM users u
JOIN orders o ON u.id = o.user_id
WHERE o.created_at > NOW() - INTERVAL '30 days'
GROUP BY u.id, u.name
ORDER BY order_count DESC
LIMIT 100;
```

```text
Output interpretation:
  Seq Scan on orders → BAD (no index used, reads every row)
  Index Scan using idx_orders_created → GOOD (using index)
  actual time=0.123..456.789 → first row at 0.123ms, last at 456ms
  rows=5000 → planner estimated 5000 rows
  loops=1 → executed once
  Buffers: shared hit=42 read=980 → 42 pages from cache, 980 from disk

Key indicators:
  - "Seq Scan" on large tables (>1000 rows) = missing index
  - Large discrepancy between "rows" (estimated) and "actual ... rows"
    = outdated table statistics → run ANALYZE
  - "shared read" >> "shared hit" = not enough buffer cache
  - "Foreign Scan" = scanning a foreign table (Federated query)
```

### Scenario: "Index Dropped During Migration"

```text
SYMPTOM: "After last week's schema migration, the dashboard query
         went from 200ms to 45 seconds. Nothing else changed."

INVESTIGATION:
  EXPLAIN ANALYZE SELECT ... (the dashboard query)

  Before migration plan:
    Index Scan using idx_orders_merchant_date on orders
    (cost=0.42..8.44 rows=50)
    actual time=0.050..0.180

  After migration plan:
    Seq Scan on orders
    (cost=0.00..125000.00 rows=8450000)
    actual time=1200..45000
    Filter: merchant_id = 42

ROOT CAUSE: The migration script had a DROP TABLE ... CASCADE
  that cascaded to the index. The CREATE TABLE ... AS SELECT
  recreated the table but not the index. The developer didn't
  notice because EXPLAIN (without ANALYZE) was used for testing,
  which showed a lower cost estimate.

LESSONS:
  1. Always use EXPLAIN ANALYZE (not just EXPLAIN) to verify
  2. Check pg_indexes after migration:
     SELECT indexname FROM pg_indexes WHERE tablename = 'orders';
  3. Set up index monitoring: alert if a critical index is missing
```

---

## Failover Behavior

### Multi-AZ Failover Mechanics

```text
Normal operation:
  ┌─────────────────┐    Sync Replication     ┌─────────────────┐
  │  Primary (AZ-a)  │ ─────────────────────> │  Standby (AZ-b)  │
  │  10.0.1.100      │    (disk-level, EBS/   │  10.0.2.100      │
  │  Accepts R/W     │     storage layer)      │  Not accessible   │
  └─────────────────┘                         └─────────────────┘
         │
         │ DNS CNAME: mydb.xxx.us-east-1.rds.amazonaws.com → 10.0.1.100
         │

Failover (triggered by: AZ outage, instance failure, manual failover):
  Time 0s:     Primary failure detected
  Time 10s:    DNS CNAME update begins
  Time 30s:    Standby promoted to primary
  Time 60-120s: DNS propagation completes (old records expire)
  Time 120s:   Failover complete. New primary accepts connections.
```

### What Happens During Failover

```text
Total downtime: 60-120 seconds

Phase 1 (0-30s): Detection + Promotion
  - Standby's storage catches up to last committed transaction
  - Standby promoted to primary
  - Old primary's CNAME record invalidated

Phase 2 (30-120s): DNS Propagation
  - CNAME updated: mydb.xxx.rds.amazonaws.com → new primary IP
  - DNS TTL on the CNAME is 5 seconds
  - BUT: Application DNS cache may not respect TTL
  - AND: Connection pools hold connections to old IP until timeout

Phase 3 (60-120s): Client reconnection
  - Application receives "connection refused" or "connection reset"
  - Connection pool retries → resolves new CNAME → connects
  - Normal operation resumes
```

### Scenario: "Failover During Black Friday"

```text
SYMPTOM: "RDS Multi-AZ failover happened during peak Black Friday
         traffic. It took 90 seconds. The error rate hit 100%
         for that entire period. We lost an estimated $50K in orders."

POST-MORTEM FINDINGS:
  App connection pool: HikariCP, connectionTimeout=30000 (30 seconds)
  DNS cache: JVM default (cache forever for successful lookups)

  Timeline:
    T+0s:     DB fails. HikariCP holds 20 connections to old primary IP.
    T+5s:     RDS CNAME updated to new primary IP.
    T+30s:    HikariCP's 20 connections ALL timeout (connectionTimeout=30s).
              20 new connections created → but Java DNS cache still has old IP.
              New connections ALSO fail (still pointing to dead primary).
    T+60s:    Finally some node's DNS cache expires → resolves new IP → connects.
    T+90s:    All nodes reconnected. Error rate drops.

FIXES:
  1. Reduce connectionTimeout from 30s to 5s:
     spring.datasource.hikari.connection-timeout=5000
  2. Set JVM DNS cache TTL (prevents cached stale IPs):
     java -Dsun.net.inetaddr.ttl=5 -jar app.jar
  3. Enable HikariCP's exception override:
     @Override
     public boolean isConnectionDead(SQLException e) {
         // Treat connection reset/refused as dead → evict immediately
         String sqlState = e.getSQLState();
         return sqlState != null && sqlState.startsWith("08");
     }
  4. Use RDS Proxy (handles failover at the proxy layer — transparent to app)
  5. Implement circuit breaker + retry with exponential backoff
```

### Testing Failover (GameDay)

```bash
# Initiate manual failover
aws rds reboot-db-instance \
  --db-instance-identifier mydb \
  --force-failover

# Monitor the failover in real-time
while true; do
  aws rds describe-db-instances --db-instance-identifier mydb \
    --query "DBInstances[0].{Status:DBInstanceStatus,Zone:AvailabilityZone,Secondary:SecondaryAvailabilityZone}"
  sleep 5
done

# Check the CNAME resolution
while true; do
  dig +short mydb.xxx.rds.amazonaws.com
  sleep 2
done

# Test from application side:
# Start a continuous query loop and watch for errors
while true; do
  psql -h mydb.xxx.rds.amazonaws.com -U app -d mydb \
    -c "SELECT now(), pg_is_in_recovery();" 2>&1
  sleep 0.5
done
```

---

## Read Replica Lag

Read replicas use asynchronous replication. The replica is always slightly behind the primary.

### Monitoring Replica Lag

```bash
# CloudWatch metric: ReplicaLag (seconds)
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name ReplicaLag \
  --dimensions Name=DBInstanceIdentifier,Value=mydb-replica \
  --start-time 2026-06-11T09:00:00Z \
  --end-time 2026-06-11T10:00:00Z \
  --period 300 --statistics Maximum
```

```sql
-- MySQL: Check replication lag on replica
SHOW SLAVE STATUS\G
-- Look for: Seconds_Behind_Master

-- PostgreSQL (physical replication): Check lag on replica
SELECT now() - pg_last_xact_replay_timestamp() AS replication_lag;

-- Or on primary for each replica:
SELECT application_name, client_addr, state,
       pg_wal_lsn_diff(sent_lsn, write_lsn) AS write_lag_bytes,
       pg_wal_lsn_diff(write_lsn, flush_lsn) AS flush_lag_bytes,
       pg_wal_lsn_diff(flush_lsn, replay_lsn) AS replay_lag_bytes
FROM pg_stat_replication;
```

### Causes of Replica Lag

```text
1. Heavy write load on primary:
   If primary writes 100MB/s of WAL but replica can only apply 50MB/s
   → lag grows continuously. Fix: scale up replica to match primary.

2. Long-running transaction on primary:
   WAL is held until the transaction commits (or rolls back).
   A 4-hour UPDATE blocks WAL from being sent to replica for 4 hours.
   Fix: identify and break up long transactions.

3. Insufficient replica resources:
   Replica is on a smaller instance class than primary.
   Fix: use same or larger instance class for replica.

4. Single-threaded replication (MySQL):
   MySQL replica applies changes in a single thread.
   High-concurrency writes on primary serialize on replica.
   Fix: enable multi-threaded replication (MySQL 5.7+, Aurora).

5. Replica also serving heavy read traffic:
   Reads compete with replication apply for CPU/IO.
   Fix: add more replicas, split read traffic.
```

---

## IOPS Throttling

### Understanding EBS IOPS on RDS

```text
RDS uses EBS volumes under the hood. The same burst bucket rules apply.

gp2:
  Storage size × 3 = IOPS (min 100, max 16,000)
  5.4M I/O credit bucket

gp3 (recommended for RDS):
  3,000 IOPS baseline regardless of size
  12,000 IOPS for 400GB
  16,000 IOPS max
  Configurable: can provision up to 16,000 IOPS independently of size

io1:
  Provisioned IOPS — you specify exact IOPS
  max_IOPS:storage_GB ratio = 50:1
  Example: 100GB io1 can have up to 5,000 provisioned IOPS
```

### CloudWatch EBS Metrics for RDS

```bash
# BurstBalance (for gp2 volumes)
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name BurstBalance \
  --dimensions Name=DBInstanceIdentifier,Value=mydb \
  --start-time 2026-06-11T09:00:00Z \
  --end-time 2026-06-11T10:00:00Z \
  --period 300 --statistics Average

# VolumeQueueDepth (pending I/O operations)
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name VolumeQueueDepth \
  --dimensions Name=DBInstanceIdentifier,Value=mydb \
  --start-time 2026-06-11T09:00:00Z \
  --end-time 2026-06-11T10:00:00Z \
  --period 300 --statistics Average

# ReadIOPS / WriteIOPS
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name ReadIOPS \
  --dimensions Name=DBInstanceIdentifier,Value=mydb \
  --start-time 2026-06-11T09:00:00Z \
  --end-time 2026-06-11T10:00:00Z \
  --period 300 --statistics Sum
```

### Scenario: "ETL Job Kills Database Performance"

```text
SYMPTOM: "Every night at 2 AM, our ETL job runs for reporting.
         During the 4-hour window, the website becomes unbearably
         slow — 5-10 second page loads. After 4 AM, everything
         returns to normal."

INVESTIGATION:
  RDS: db.r5.large, gp2 500GB → 1500 IOPS baseline
  ETL job: Bulk INSERT of 50M rows over 4 hours
  IOPS needed: ~2500 sustained write IOPS

  BurstBalance at 10 PM: 100%
  BurstBalance at 2 AM (ETL start): Drops steadily
  BurstBalance at 3:30 AM: 0% → throttled to 1500 IOPS
  VolumeQueueDepth at 3:30 AM: Spikes from 0 to 80+
  Web queries at 3:30 AM: Reads also throttled (same volume shares IOPS)
  WriteLatency at 3:30 AM: 50-200ms (normal: 1-2ms)

  The ETL consumes ALL available IOPS. The web app's reads
  queue up behind the writes. Every page load waits for I/O.

FIX:
  1. Migrate to gp3: 3,000 IOPS baseline (covers the 2,500 need)
     Or provision 10,000 IOPS on gp3: no burst needed ever.

  2. Create a read replica for the web app:
     Web reads → replica (separate IOPS budget)
     ETL writes → primary

  3. Offload the ETL to a separate RDS instance:
     DMS (Database Migration Service) to replicate to a reporting DB
     ETL runs on the reporting DB, not the production primary

  4. Optimize the ETL:
     - Use batched INSERT with transactions (not row-by-row)
     - Disable indexes during bulk load, rebuild after
     - Use COPY (PostgreSQL) or LOAD DATA INFILE (MySQL) instead of INSERT
```

---

## Deadlocks

A deadlock occurs when two transactions hold locks that the other needs, and neither can proceed.

### MySQL Deadlock Detection

```sql
-- Check latest deadlock
SHOW ENGINE INNODB STATUS\G

-- Look for the LATEST DETECTED DEADLOCK section:
------------------------
LATEST DETECTED DEADLOCK
------------------------
2026-06-11 10:15:30 0x7f8b2c001700
*** (1) TRANSACTION:
TRANSACTION 123456, ACTIVE 0 sec starting index read
mysql tables in use 1, locked 1
LOCK WAIT 3 lock struct(s), heap size 1136, 2 row lock(s)
MySQL thread id 42, OS thread handle 140234..., query id 9876
UPDATE orders SET status = 'shipped' WHERE id = 100;

*** (1) HOLDS THE LOCK(S):
RECORD LOCKS space id 55 page no 3 n bits 72 index PRIMARY
Record lock, heap no 4 PHYSICAL RECORD: n_fields 4; compact format; info bits 0

*** (1) WAITING FOR THIS LOCK TO BE GRANTED:
RECORD LOCKS space id 55 page no 4 n bits 72 index PRIMARY
Record lock, heap no 7 PHYSICAL RECORD: n_fields 4; compact format; info bits 0

*** (2) TRANSACTION:
TRANSACTION 123457, ACTIVE 0 sec starting index read
mysql tables in use 1, locked 1
3 lock struct(s), heap size 1136, 2 row lock(s)
MySQL thread id 43, OS thread handle 140234..., query id 9877
UPDATE orders SET status = 'cancelled' WHERE id = 200;

*** (2) HOLDS THE LOCK(S):
RECORD LOCKS space id 55 page no 4 n bits 72 index PRIMARY
Record lock, heap no 7 PHYSICAL RECORD: ...  ← Holding what (1) wants

*** (2) WAITING FOR THIS LOCK TO BE GRANTED:
RECORD LOCKS space id 55 page no 3 n bits 72 index PRIMARY
Record lock, heap no 4 PHYSICAL RECORD: ...  ← Waiting for what (1) holds

*** WE ROLL BACK TRANSACTION (2)  ← MySQL chose (2) as the victim
```

```sql
-- Enable deadlock logging in parameter group:
-- innodb_print_all_deadlocks = 1

-- Deadlock count metric
SHOW GLOBAL STATUS LIKE 'Innodb_deadlocks';

-- Lock wait timeout (default 50s, should be tuned)
SHOW VARIABLES LIKE 'innodb_lock_wait_timeout';
```

### PostgreSQL Deadlock Detection

```sql
-- Check deadlock count
SELECT datname, deadlocks FROM pg_stat_database
WHERE datname = 'mydb';

-- Enable deadlock logging (parameter group):
-- log_lock_waits = 1
-- deadlock_timeout = 1s (default, time before deadlock check runs)

-- See current locks
SELECT l.pid, l.locktype, l.mode, l.granted,
       a.usename, a.query, a.query_start,
       age(now(), a.query_start) AS age
FROM pg_locks l
JOIN pg_stat_activity a ON l.pid = a.pid
WHERE NOT l.granted
ORDER BY a.query_start;

-- See blocked queries (waiting for locks)
SELECT blocked_locks.pid AS blocked_pid,
       blocked_activity.usename AS blocked_user,
       blocking_locks.pid AS blocking_pid,
       blocking_activity.usename AS blocking_user,
       blocked_activity.query AS blocked_query,
       blocking_activity.query AS blocking_query
FROM pg_locks blocked_locks
JOIN pg_stat_activity blocked_activity
  ON blocked_activity.pid = blocked_locks.pid
JOIN pg_locks blocking_locks
  ON blocking_locks.locktype = blocked_locks.locktype
  AND blocking_locks.database IS NOT DISTINCT FROM blocked_locks.database
  AND blocking_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
  AND blocking_locks.page IS NOT DISTINCT FROM blocked_locks.page
  AND blocking_locks.tuple IS NOT DISTINCT FROM blocked_locks.tuple
  AND blocking_locks.virtualxid IS NOT DISTINCT FROM blocked_locks.virtualxid
  AND blocking_locks.transactionid IS NOT DISTINCT FROM blocked_locks.transactionid
  AND blocking_locks.classid IS NOT DISTINCT FROM blocked_locks.classid
  AND blocking_locks.objid IS NOT DISTINCT FROM blocked_locks.objid
  AND blocking_locks.objsubid IS NOT DISTINCT FROM blocked_locks.objsubid
  AND blocking_locks.pid != blocked_locks.pid
JOIN pg_stat_activity blocking_activity
  ON blocking_activity.pid = blocking_locks.pid
WHERE NOT blocked_locks.granted;
```

### Preventing Deadlocks

```text
1. Always acquire locks in the same order:
   - If Transaction A locks table X then table Y,
   - Transaction B must also lock X then Y (never Y then X)

2. Keep transactions short:
   - Commit frequently
   - No user interaction inside a transaction
   - No external API calls inside a transaction

3. Use appropriate isolation levels:
   - READ COMMITTED (default, fewer deadlocks)
   - REPEATABLE READ (PostgreSQL default, more deadlocks possible)
   - SERIALIZABLE (most deadlocks, use only when needed)

4. Add covering indexes:
   - UPDATE WHERE non-indexed column → table scan → row locks on ALL rows
   - UPDATE WHERE indexed column → locks only matching rows
```

---

## Parameter Groups

RDS parameter groups control database engine configuration. Some parameters are static (require reboot), others are dynamic (apply immediately).

### Checking Parameter Status

```bash
# View parameter group attached to an instance
aws rds describe-db-instances --db-instance-identifier mydb \
  --query "DBInstances[0].DBParameterGroups"

# Check if a parameter change is pending reboot
aws rds describe-db-parameters \
  --db-parameter-group-name my-pg \
  --query "Parameters[?ParameterName=='max_connections'].{Name:ParameterName,Value:ParameterValue,ApplyMethod:ApplyMethod,ApplyType:ApplyType}"

# ApplyType:
#   "static"  → requires reboot
#   "dynamic" → applies immediately (if ApplyMethod = immediate)
```

### Scenario: "Changed max_connections, Still Getting Errors"

```text
SYMPTOM: "I changed max_connections from 500 to 1000 in the parameter
         group 15 minutes ago. But new connections are still failing
         at 500. The parameter group shows the new value."

INVESTIGATION:
$ aws rds describe-db-parameters \
  --db-parameter-group-name my-pg \
  --query "Parameters[?ParameterName=='max_connections']"

{
  "ParameterName": "max_connections",
  "ParameterValue": "1000",
  "ApplyMethod": "pending-reboot",   ← CHANGED BUT NOT APPLIED
  "ApplyType": "static",             ← REQUIRES REBOOT
  "IsModifiable": true
}

$ mysql -h mydb.xxx.rds.amazonaws.com -u admin -p -e "SHOW VARIABLES LIKE 'max_connections';"
max_connections   500              ← STILL THE OLD VALUE!

ROOT CAUSE: max_connections is a STATIC parameter.
Changing it in the parameter group only stages the change.
It does NOT take effect until the DB instance is rebooted.
No error, no warning — the value in the parameter group says 1000,
but the running instance still uses 500.

FIX: Reboot the instance.
aws rds reboot-db-instance --db-instance-identifier mydb

BEST PRACTICE:
  - Check ApplyType before changing: if "static", schedule a
    maintenance window or plan for a brief outage
  - After changing, VERIFY the running value, not just the parameter group
  - Use CloudWatch alarm: DatabaseConnections > 80% of max_connections
```

### Common Parameter Tuning

```sql
-- PostgreSQL Performance Parameters
-- (static = needs reboot, dynamic = immediate)

-- Memory (static)
shared_buffers = {25% of instance RAM}           -- e.g., 4GB on 16GB instance
-- default: {25% of RAM, but RDS overrides}

-- Work memory for sorts/hashes (dynamic)
work_mem = 4MB                                   -- per-operation, increase for analytical

-- Maintenance operations (dynamic)
maintenance_work_mem = 256MB                     -- for VACUUM, CREATE INDEX

-- WAL settings (static)
wal_buffers = 16MB                               -- WAL write buffer

-- Query planning (dynamic)
effective_cache_size = {75% of instance RAM}     -- e.g., 12GB on 16GB

-- Autovacuum settings (dynamic)
autovacuum = on                                  -- MUST be on for RDS
autovacuum_max_workers = 3                       -- increase for high-write workloads
autovacuum_naptime = 15s                         -- check every 15s

-- Connections (static)
max_connections = {formula from AWS docs}
```

---

## Backup, RTO, and RPO

### Automated Backups

```text
RDS automated backups:
  - Daily snapshot during backup window (you define or AWS picks)
  - Transaction log backups every 5 minutes
  - Retention: 1-35 days (default 7)
  - Stored in S3 (you don't see the bucket)

RPO (Recovery Point Objective): Up to 5 minutes
  - Can restore to any point in time within retention period
  - Minimum granularity: 5 minutes (transaction log backup interval)

RTO (Recovery Time Objective): Variable
  - Depends on: database size, instance class, provisioned IOPS
  - Rough estimate: ~1-2 hours per 500GB
  - Restoring to a point in time takes longer (must replay transaction logs)
  - Restoring from latest snapshot is faster

Manual snapshots:
  - Not automatic, not deleted after retention
  - Shareable across accounts and regions
  - No point-in-time recovery (just snapshot restore)
```

### Restore Time Testing

```bash
# Initiate point-in-time restore (creates NEW RDS instance)
aws rds restore-db-instance-to-point-in-time \
  --source-db-instance-identifier mydb \
  --target-db-instance-identifier mydb-restored \
  --restore-time "2026-06-11T10:00:00Z" \
  --db-instance-class db.r5.large

# Monitor restore progress
aws rds describe-db-instances --db-instance-identifier mydb-restored \
  --query "DBInstances[0].{Status:DBInstanceStatus,Percent:PercentProgress}"
```

---

## Python Database Connection with Failover

```python
#!/usr/bin/env python3
"""
Production-grade PostgreSQL connection with failover handling.
Uses psycopg2 + tenacity for exponential backoff retry.
"""

import os
import time
import logging
import psycopg2
import psycopg2.extras
import psycopg2.pool
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class RDSConnectionManager:
    """
    Connection manager with failover awareness.
    Handles: connection loss during failover, connection pooling,
    exponential backoff retry, and read/write splitting.
    """

    def __init__(
        self,
        writer_endpoint: str,
        reader_endpoint: str = None,
        port: int = 5432,
        user: str = None,
        password: str = None,
        database: str = 'mydb',
        min_connections: int = 2,
        max_connections: int = 10,
    ):
        self.writer_endpoint = writer_endpoint
        self.reader_endpoint = reader_endpoint or writer_endpoint
        self.port = port
        self.user = user or os.environ.get('DB_USER', 'app')
        self.password = password or os.environ.get('DB_PASSWORD', '')
        self.database = database

        # Writer pool (rw operations)
        self.write_pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=min_connections,
            maxconn=max_connections,
            host=self.writer_endpoint,
            port=self.port,
            user=self.user,
            password=self.password,
            dbname=self.database,
            connect_timeout=5,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=5,
        )

        # Reader pool (read-only queries)
        if reader_endpoint:
            self.read_pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=min_connections,
                maxconn=max_connections,
                host=self.reader_endpoint,
                port=self.port,
                user=self.user,
                password=self.password,
                dbname=self.database,
                connect_timeout=5,
                keepalives=1,
                keepalives_idle=30,
                keepalives_interval=10,
                keepalives_count=5,
            )
        else:
            self.read_pool = self.write_pool

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        retry=retry_if_exception_type((
            psycopg2.OperationalError,
            psycopg2.InterfaceError,
        )),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def execute_with_retry(self, query: str, params: tuple = None,
                           read_only: bool = False):
        """
        Execute a query with automatic retry on failover-related errors.

        Failover errors include:
        - OperationalError: connection lost during failover
        - InterfaceError: connection closed unexpectedly
        """
        pool = self.read_pool if read_only else self.write_pool
        conn = None

        try:
            conn = pool.getconn()
            conn.set_session(readonly=read_only, autocommit=False)

            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(query, params or ())
                if cur.description:  # SELECT query
                    result = cur.fetchall()
                else:
                    result = cur.rowcount
                conn.commit()
                return result

        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            logger.error(f"Query failed: {e}")
            raise

        finally:
            if conn and pool:
                pool.putconn(conn)

    def check_failover_status(self):
        """Check if connected to primary or replica."""
        result = self.execute_with_retry(
            "SELECT pg_is_in_recovery() AS is_replica",
            read_only=True
        )
        is_replica = result[0]['is_replica']
        logger.info(f"Connected to: {'REPLICA' if is_replica else 'PRIMARY'}")
        return is_replica

    def close(self):
        """Close all connection pools."""
        self.write_pool.closeall()
        if self.read_pool is not self.write_pool:
            self.read_pool.closeall()


# ── Usage Example ────────────────────────────────────────────────

if __name__ == '__main__':
    db = RDSConnectionManager(
        writer_endpoint='mydb.cluster-xxx.us-east-1.rds.amazonaws.com',
        reader_endpoint='mydb.cluster-ro-xxx.us-east-1.rds.amazonaws.com',
    )

    try:
        # Check replication state
        db.check_failover_status()

        # Read from replica
        users = db.execute_with_retry(
            "SELECT id, email FROM users WHERE active = true LIMIT 10",
            read_only=True
        )
        logger.info(f"Found {len(users)} users from replica")

        # Write to primary
        db.execute_with_retry(
            "INSERT INTO audit_log (action, timestamp) VALUES (%s, NOW())",
            ('query_executed',)
        )
        logger.info("Write succeeded")

    finally:
        db.close()
```

---

## Java HikariCP with Failover Configuration

```yaml
# application.yml — Spring Boot + HikariCP with Aurora/RDS failover
spring:
  datasource:
    # Aurora PostgreSQL cluster endpoints
    url: jdbc:postgresql://mydb.cluster-xxx.us-east-1.rds.amazonaws.com:5432/mydb
    username: ${DB_USER:app}
    password: ${DB_PASSWORD}

    hikari:
      # Pool sizing
      minimum-idle: 5
      maximum-pool-size: 20

      # Timeouts — CRITICAL for failover
      connection-timeout: 3000       # 3s to get connection from pool
      validation-timeout: 2000       # 2s for connection validation query
      idle-timeout: 600000           # 10 min idle before eviction
      max-lifetime: 1800000          # 30 min max connection age
      leak-detection-threshold: 60000 # Warn if connection held >60s

      # Connection testing
      connection-test-query: "SELECT 1"

      # Failover-specific settings
      initialization-fail-timeout: -1  # Don't fail on startup if DB is down
      keepalive-time: 30000           # 30s keepalive — detects dead connections

      # For Aurora PostgreSQL with read/write splitting:
      # Use read-only pool data source for readers
```

```java
import com.zaxxer.hikari.HikariConfig;
import com.zaxxer.hikari.HikariDataSource;
import com.zaxxer.hikari.pool.HikariPool;
import org.postgresql.Driver;
import org.postgresql.util.PSQLException;
import org.postgresql.util.PSQLState;

import javax.sql.DataSource;
import java.sql.Connection;
import java.sql.SQLException;
import java.util.concurrent.atomic.AtomicInteger;

public class RDSConnectionPool {

    /**
     * Create a HikariCP pool with failover awareness for RDS/Aurora.
     *
     * Key failover behaviors:
     *  - Short connection timeout (fail fast, don't queue)
     *  - Connection validation before use
     *  - Max lifetime < DNS TTL (to pick up CNAME changes)
     *  - Custom exception override to detect dead connections
     */
    public static DataSource createFailoverDataSource(String host, String db) {
        HikariConfig config = new HikariConfig();

        config.setJdbcUrl(String.format(
            "jdbc:postgresql://%s:5432/%s?" +
            "socketTimeout=30&" +                    // 30s socket timeout
            "connectTimeout=5&" +                    // 5s TCP connect timeout
            "tcpKeepAlive=true&" +
            "ApplicationName=myapp",
            host, db
        ));

        config.setUsername(System.getenv("DB_USER"));
        config.setPassword(System.getenv("DB_PASSWORD"));

        // Pool settings
        config.setMinimumIdle(5);
        config.setMaximumPoolSize(20);
        config.setConnectionTimeout(5000);    // Don't queue during failover
        config.setValidationTimeout(2000);
        config.setIdleTimeout(600_000);
        config.setMaxLifetime(300_000);       // 5 min — shorter than DNS TTL!
        config.setKeepaliveTime(30_000);      // Check dead connections every 30s

        // Connection testing
        config.setConnectionTestQuery("SELECT 1");

        // Don't fail startup if DB is temporarily unavailable
        config.setInitializationFailTimeout(-1);

        // Leak detection
        config.setLeakDetectionThreshold(60_000);

        // Data source properties for PostgreSQL failover awareness
        config.addDataSourceProperty("reWriteBatchedInserts", "true");
        config.addDataSourceProperty("prepareThreshold", "5");

        return new HikariDataSource(config);
    }

    /**
     * Run a query with failover retry logic.
     */
    public static void executeWithRetry(HikariDataSource ds, String query) {
        int maxRetries = 3;
        SQLException lastException = null;

        for (int attempt = 0; attempt < maxRetries; attempt++) {
            try (Connection conn = ds.getConnection()) {
                conn.createStatement().execute(query);
                return; // Success
            } catch (SQLException e) {
                lastException = e;

                // Only retry on connection/network errors
                if (!isRetryable(e)) {
                    throw new RuntimeException("Non-retryable SQL error", e);
                }

                System.err.printf(
                    "Attempt %d failed (retryable): %s — retrying...%n",
                    attempt + 1, e.getMessage()
                );

                // Evict all connections from pool — they may all be stale
                HikariPool pool = (HikariPool) ds.getHikariPoolMXBean();
                pool.softEvictConnections();

                // Brief pause before retry
                try {
                    Thread.sleep(2000L * (attempt + 1));
                } catch (InterruptedException ie) {
                    Thread.currentThread().interrupt();
                    throw new RuntimeException("Interrupted during retry", ie);
                }
            }
        }

        throw new RuntimeException(
            "Query failed after " + maxRetries + " attempts", lastException
        );
    }

    /**
     * Determine if a SQLException is retryable (likely transient).
     */
    private static boolean isRetryable(SQLException e) {
        if (e instanceof PSQLException) {
            PSQLException pe = (PSQLException) e;
            String sqlState = pe.getSQLState();

            // PostgreSQL error codes for connection failures:
            // 08000 = connection exception
            // 08003 = connection does not exist
            // 08006 = connection failure
            // 08P01 = protocol violation
            // 57P01 = admin shutdown
            // 57P02 = crash shutdown
            // 57P03 = cannot connect now
            return sqlState != null && (
                sqlState.startsWith("08") ||
                sqlState.startsWith("57P")
            );
        }
        // For non-PostgreSQL SQLExceptions, check the cause
        String msg = e.getMessage().toLowerCase();
        return msg.contains("connection") ||
               msg.contains("timeout") ||
               msg.contains("closed") ||
               msg.contains("reset");
    }

    public static void main(String[] args) throws Exception {
        DataSource ds = createFailoverDataSource(
            "mydb.cluster-xxx.us-east-1.rds.amazonaws.com",
            "mydb"
        );

        try {
            executeWithRetry((HikariDataSource) ds, "SELECT 1");
            System.out.println("Connection OK");
        } catch (Exception e) {
            System.err.println("Connection failed: " + e.getMessage());
        } finally {
            ((HikariDataSource) ds).close();
        }
    }
}
```

---

## References

- [AWS RDS User Guide — Troubleshooting](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/CHAP_Troubleshooting.html)
- [AWS RDS Proxy Documentation](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/rds-proxy.html)
- [Performance Insights Documentation](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_PerfInsights.html)
- [MySQL max_connections Formula](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/CHAP_Limits.html)
- [PostgreSQL on RDS — Best Practices](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/CHAP_BestPractices.html)
- [HikariCP Configuration](https://github.com/brettwooldridge/HikariCP)
- [psycopg2 Documentation](https://www.psycopg.org/docs/)
- [tenacity (Python retry library)](https://tenacity.readthedocs.io/)
