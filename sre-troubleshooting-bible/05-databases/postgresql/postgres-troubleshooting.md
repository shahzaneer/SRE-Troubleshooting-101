# PostgreSQL Troubleshooting

> **Category:** Databases | PostgreSQL
> **Difficulty:** Intermediate to Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#postgresql` `#database` `#oncall`

---

## Table of Contents

1. [pg_stat_activity — Long-Running Queries](#pgstatactivity--long-running-queries)
2. [Lock Contention](#lock-contention)
3. [Autovacuum & Dead Tuples](#autovacuum--dead-tuples)
4. [Connection Exhaustion](#connection-exhaustion)
5. [WAL Replication & Replica Lag](#wal-replication--replica-lag)
6. [EXPLAIN (ANALYZE, BUFFERS)](#explain-analyze-buffers)
7. [Table & Index Bloat](#table--index-bloat)
8. [Index Maintenance](#index-maintenance)
9. [Python psycopg2 Connection Pool](#python-psycopg2-connection-pool)
10. [Java HikariCP Monitoring](#java-hikaricp-monitoring)

---

## pg_stat_activity — Long-Running Queries

```sql
-- Find long-running queries
SELECT
    pid,
    usename,
    application_name,
    client_addr,
    state,
    now() - query_start AS duration,
    wait_event_type,
    wait_event,
    query
FROM pg_stat_activity
WHERE state != 'idle'
  AND pid != pg_backend_pid()
ORDER BY query_start;
```

### Scenario: "API Increasingly Slow — 45-Minute UPDATE"

```text
Symptom: API response times creeping from 200ms to 5s+. Dashboard
         shows PostgreSQL CPU at 100%, I/O wait at 60%.

Investigation:
  SELECT pid, now()-query_start AS duration, query, wait_event
  FROM pg_stat_activity WHERE state != 'idle' ORDER BY query_start;

  pid=8732, duration=45:23, wait_event=DataFileRead,
  query="UPDATE orders SET ... WHERE created_at > '2025-01-01'"

  → A single UPDATE has been running for 45 minutes.
  → It acquired an ExclusiveLock on the orders table.
  → All other queries touching orders are queued behind it.
  → The UPDATE is scanning the entire table (no index on created_at).

Fix:
  1. IMMEDIATE: SELECT pg_terminate_backend(8732);  ← kill the blocking query
  2. Create index: CREATE INDEX CONCURRENTLY idx_orders_created
     ON orders (created_at) WHERE status = 'pending';
  3. Batch the UPDATE: instead of one giant transaction, update in
     batches of 10,000 rows with COMMIT between batches.
```

### Identify Idle-in-Transaction Connections

```sql
-- Idle-in-transaction: holding locks while doing nothing
SELECT pid, usename, application_name, client_addr,
       state, now()-state_change AS idle_since,
       query
FROM pg_stat_activity
WHERE state = 'idle in transaction'
  AND now() - state_change > interval '5 minutes';

-- Auto-kill idle-in-transaction > 5 min (parameter group):
-- idle_in_transaction_session_timeout = 300000  (5 min in ms)
```

---

## Lock Contention

```sql
-- Find blocked queries (comprehensive view)
SELECT
    blocked.pid AS blocked_pid,
    blocked.usename AS blocked_user,
    blocked.query AS blocked_query,
    blocked.wait_event_type,
    blocked.wait_event,
    now() - blocked.query_start AS blocked_for,
    blocking.pid AS blocking_pid,
    blocking.usename AS blocking_user,
    blocking.query AS blocking_query,
    now() - blocking.query_start AS blocking_running_for,
    blocking.state AS blocking_state
FROM pg_stat_activity blocked
JOIN pg_locks blocked_locks ON blocked.pid = blocked_locks.pid
JOIN pg_locks blocking_locks ON
    blocked_locks.locktype = blocking_locks.locktype
    AND blocked_locks.database IS NOT DISTINCT FROM blocking_locks.database
    AND blocked_locks.relation IS NOT DISTINCT FROM blocking_locks.relation
    AND blocked_locks.page IS NOT DISTINCT FROM blocking_locks.page
    AND blocked_locks.tuple IS NOT DISTINCT FROM blocking_locks.tuple
    AND blocked_locks.virtualxid IS NOT DISTINCT FROM blocking_locks.virtualxid
    AND blocked_locks.transactionid IS NOT DISTINCT FROM blocking_locks.transactionid
    AND blocked_locks.classid IS NOT DISTINCT FROM blocking_locks.classid
    AND blocked_locks.objid IS NOT DISTINCT FROM blocking_locks.objid
    AND blocked_locks.objsubid IS NOT DISTINCT FROM blocking_locks.objsubid
    AND blocked_locks.pid != blocking_locks.pid
JOIN pg_stat_activity blocking ON blocking.pid = blocking_locks.pid
WHERE NOT blocked_locks.granted
  AND blocked.state != 'idle'
ORDER BY blocked.query_start;
```

### Lock Types

```text
AccessShareLock         — SELECT (least restrictive)
RowShareLock            — SELECT FOR UPDATE/SHARE
RowExclusiveLock        — INSERT, UPDATE, DELETE
ShareUpdateExclusiveLock — VACUUM (non-FULL), ANALYZE, CREATE INDEX CONCURRENTLY
ShareLock               — CREATE INDEX (non-concurrent)
ShareRowExclusiveLock   — CREATE TRIGGER, some ALTER TABLE
ExclusiveLock           — REFRESH MATERIALIZED VIEW CONCURRENTLY
AccessExclusiveLock     — ALTER TABLE, DROP TABLE, VACUUM FULL (most restrictive)

Key: AccessExclusiveLock blocks EVERYTHING including SELECT.
     Upgrade to PostgreSQL 12+ for non-blocking REINDEX CONCURRENTLY.
```

### Scenario: "Deploy Migration Hangs — ALTER TABLE Blocked"

```text
Symptom: CI pipeline runs a schema migration with ALTER TABLE ADD COLUMN.
         It hangs at "Waiting for lock" for 10 minutes, then times out.
         Retries fail. Entire deployment pipeline is blocked.

Debugging:
  -- Find what's holding an AccessExclusiveLock on our table
  SELECT l.pid, l.mode, l.granted, a.state, a.query,
         now() - a.state_change AS idle_since
  FROM pg_locks l
  JOIN pg_stat_activity a ON l.pid = a.pid
  WHERE l.relation = 'orders'::regclass
    AND l.mode = 'AccessExclusiveLock';

  Result: pid=7621, state='idle in transaction', idle_since=3:45:00
  Query: BEGIN; UPDATE orders SET status = 'shipped' WHERE id = 100;

  ROOT CAUSE: A developer ran a BEGIN; UPDATE ...; and WENT TO LUNCH
  without COMMIT. The row lock prevents ALTER TABLE from acquiring
  AccessExclusiveLock. ALTER TABLE waits behind ALL existing locks.

Fix:
  1. Identify: SELECT pid, usename, query, now()-state_change AS idle_time
     FROM pg_stat_activity WHERE state = 'idle in transaction';
  2. Kill: SELECT pg_terminate_backend(7621);
  3. Prevention: Set idle_in_transaction_session_timeout = 5min
  4. Use lock_timeout for DDL: SET lock_timeout = '30s'; ALTER TABLE...;
     If lock can't be acquired in 30s, fail fast instead of blocking.
```

---

## Autovacuum & Dead Tuples

In PostgreSQL, UPDATE and DELETE don't immediately reclaim space. They mark
rows as "dead tuples." Autovacuum cleans them up. If autovacuum can't keep up,
dead tuples accumulate → table bloat → query slowdown.

```sql
-- Check dead tuple accumulation
SELECT
    schemaname || '.' || relname AS table_name,
    n_live_tup,
    n_dead_tup,
    CASE WHEN n_live_tup > 0
         THEN ROUND(100.0 * n_dead_tup / n_live_tup, 1)
         ELSE 0 END AS dead_ratio,
    last_autovacuum,
    last_autoanalyze,
    autovacuum_count,
    autoanalyze_count,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||relname)) AS total_size
FROM pg_stat_user_tables
ORDER BY n_dead_tup DESC
LIMIT 20;

-- autovacuum settings
SHOW autovacuum;
SHOW autovacuum_max_workers;
SHOW autovacuum_naptime;
SHOW autovacuum_vacuum_threshold;
SHOW autovacuum_vacuum_scale_factor;

-- Current autovacuum activity
SELECT pid, query, now() - query_start AS duration,
       wait_event_type, wait_event
FROM pg_stat_activity
WHERE query LIKE 'autovacuum:%'
ORDER BY query_start;
```

### Scenario: "Query Performance Gradually Degrading Over Weeks"

```text
Symptom: SELECT queries on the `events` table went from 5ms to 50ms
         over 3 weeks. No code or schema changes. EXPLAIN shows same
         plan (Index Scan) but "actual time" keeps increasing.

Investigation:
  SELECT n_live_tup, n_dead_tup, last_autovacuum
  FROM pg_stat_user_tables WHERE relname = 'events';

  n_live_tup: 8,000,000
  n_dead_tup: 22,000,000   ← 2.75x MORE dead tuples than live!
  dead_ratio: 275%
  last_autovacuum: 2026-06-08 02:00:00  (3 days ago!)

  ROOT CAUSE: The events table receives 100K+ UPDATEs per minute but
  autovacuum can only clean ~50K dead tuples per run. The dead tuple
  backlog has been growing for weeks. The index now spans 22M dead
  rows → every Index Scan reads through dead tuples → slow.

Fix:
  1. Tune autovacuum for this table:
     ALTER TABLE events SET (
       autovacuum_vacuum_scale_factor = 0.01,    ← vacuum earlier (1% dead)
       autovacuum_vacuum_cost_limit = 2000,       ← allow more work per cycle
       autovacuum_analyze_scale_factor = 0.005   ← analyze more often
     );

  2. Manual VACUUM to clear the backlog:
     VACUUM (VERBOSE, ANALYZE) events;

  3. If still not keeping up: increase autovacuum_max_workers from 3 to 5.

  4. Consider VACUUM FULL to reclaim disk:
     VACUUM FULL events;  ← REWRITES ENTIRE TABLE. Blocks all access.
     Use ONLY during maintenance window or if disk is critically full.
```

---

## Connection Exhaustion

```sql
-- Current connections vs max
SELECT count(*) AS current_connections FROM pg_stat_activity;
SHOW max_connections;

-- Connections by source
SELECT usename, application_name, client_addr, state, count(*) AS connections
FROM pg_stat_activity
GROUP BY usename, application_name, client_addr, state
ORDER BY connections DESC;

-- Connection utilization percentage
SELECT
    count(*) AS used,
    current_setting('max_connections')::int AS max,
    ROUND(100.0 * count(*) / current_setting('max_connections')::int, 1) AS pct
FROM pg_stat_activity;
```

### Scenario: "Too Many Clients Already"

```text
Symptom: Application pods throwing "FATAL: sorry, too many clients already"
         (PostgreSQL error code 53300). Even the admin user can't connect.

PostgreSQL max_connections: 200 (db.r5.large RDS, default)

Architecture: 50 application pods × 20 connections per Hikari connection
pool = 1,000 potential connections. Only 200 available at the DB level.

ROOT CAUSE: Direct connection pooling from each pod to the database.
Every pod maintains its own pool. There's no central pooling layer.

Fix:
  PgBouncer (transaction pooling mode):
    50 pods × 5 connections → PgBouncer (250 client connections)
    PgBouncer → PostgreSQL (100 server connections, shared pool)

  PgBouncer config (/etc/pgbouncer/pgbouncer.ini):
    [databases]
    mydb = host=rds-endpoint port=5432 dbname=mydb

    [pgbouncer]
    listen_addr = 0.0.0.0
    listen_port = 6432
    auth_type = md5
    auth_file = /etc/pgbouncer/userlist.txt
    pool_mode = transaction
    max_client_conn = 500    ← client connections (app → pgbouncer)
    default_pool_size = 20   ← server connections (pgbouncer → postgres)

  The key: transaction mode returns the connection to the pool after
  each transaction. Long-lived app connections don't tie up DB connections.

  Alternative: AWS RDS Proxy (managed PgBouncer, no self-hosting needed)
    RDS Proxy automatically shares connections across app servers.
    Cost: $0.015/vCPU-hour.
```

---

## WAL Replication & Replica Lag

```sql
-- On primary: check replication status
SELECT
    application_name,
    client_addr,
    state,
    sync_state,
    pg_wal_lsn_diff(sent_lsn, pg_current_wal_lsn()) AS sent_lag_bytes,
    pg_wal_lsn_diff(write_lsn, sent_lsn) AS write_lag_bytes,
    pg_wal_lsn_diff(flush_lsn, write_lsn) AS flush_lag_bytes,
    pg_wal_lsn_diff(replay_lsn, flush_lsn) AS replay_lag_bytes,
    pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn) AS total_lag_bytes,
    reply_time
FROM pg_stat_replication;

-- On replica: check replay lag
SELECT
    pg_is_in_recovery() AS is_replica,
    now() - pg_last_xact_replay_timestamp() AS replay_lag,
    pg_last_wal_receive_lsn() AS last_wal_received,
    pg_last_wal_replay_lsn() AS last_wal_replayed;
```

### Scenario: "Read Replica Lagging 2GB Behind Primary"

```text
Symptom: Read queries on replica return stale data. Replica lag monitor
         shows 2GB behind primary, growing at 50MB/min.

Check:
  On primary:
    SELECT application_name, state,
           pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn) AS lag_bytes
    FROM pg_stat_replication;
    → lag_bytes = 2,147,483,648 (2GB)

  On replica:
    SELECT pg_last_wal_receive_lsn(), pg_last_wal_replay_lsn();
    → WAL is being RECEIVED at line rate but REPLAY is slow.

Root causes:
  1. Replica has insufficient I/O to keep up (e.g., gp2 with low IOPS baseline).
     Fix: Scale replica to same instance class as primary, or migrate to gp3.

  2. Replica is serving heavy read traffic, competing for CPU/IO with replay.
     Fix: Add more replicas to share the read load.

  3. Long-running query on primary is generating WAL faster than normal.
     Fix: Identify and optimize the query generating excess WAL.

  4. Network bottleneck between AZs (rare, check with AWS support).

  Monitoring:
    aws cloudwatch get-metric-statistics \
      --namespace AWS/RDS --metric-name ReplicaLag \
      --dimensions Name=DBInstanceIdentifier,Value=mydb-replica \
      --start-time 2026-06-11T00:00:00Z --end-time 2026-06-11T12:00:00Z \
      --period 300 --statistics Maximum
```

---

## EXPLAIN (ANALYZE, BUFFERS)

```sql
EXPLAIN (ANALYZE, BUFFERS, TIMING, FORMAT TEXT)
SELECT u.email, COUNT(o.id) AS order_count
FROM users u
JOIN orders o ON u.id = o.user_id
WHERE o.created_at > NOW() - INTERVAL '30 days'
  AND o.status = 'completed'
GROUP BY u.id, u.email
ORDER BY order_count DESC
LIMIT 100;
```

### Reading EXPLAIN Output

```text
Sample output and interpretation:

Nested Loop  (cost=0.42..12450.78 rows=500 width=64)
              (actual time=0.123..456.789 rows=100 loops=1)
  Buffers: shared hit=42 read=980
  → 42 pages from shared_buffers (cache), 980 from disk (cold)

  ->  Index Scan using idx_orders_created
      on orders (cost=0.42..850.00 rows=500 width=32)
      (actual time=0.050..100.230 rows=5000 loops=1)
      Index Cond: created_at > '2026-05-12'
                     ^^^^^^^^^^^^^^^^^^^^^^^^^ ← uses the index
      Buffers: shared hit=5 read=820

  ->  Index Scan using users_pkey
      on users (cost=0.00..23.12 rows=1 width=40)
      (actual time=0.050..0.052 rows=1 loops=5000)
                ^^^^^^^^ high loop count × per-loop cost = total cost
      Index Cond: id = o.user_id

Key metrics:
  cost: Planner's estimated cost (lower = better, used for plan comparison)
  actual time: Measured execution (first row..last row) in milliseconds
  rows: Estimated rows. If actual >> estimated, statistics are stale → ANALYZE.
  loops: How many times this node executed. High loops × moderate cost = EXPENSIVE.
  Buffers read: Pages read from disk. If high, consider shared_buffers tuning.
  Seq Scan: Full table scan. On large tables (>1000 rows) → missing index.
  Nested Loop: Join method. OK for small outer sets. For large: prefer Hash Join.
  Hash Join: Good for large datasets. High startup cost but fast per-row.

Red flags:
  - Seq Scan on 10M row table → add index
  - Nested Loop with loops=100000 → add index on join column
  - actual rows=100000 but estimated rows=10 → ANALYZE (update stats)
  - Buffers read=50000 → working set doesn't fit in shared_buffers
```

---

## Table & Index Bloat

```sql
-- Table bloat estimate
SELECT
    schemaname || '.' || tablename AS table_name,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS total_size,
    n_dead_tup,
    n_live_tup,
    CASE WHEN n_live_tup > 0
         THEN ROUND(100.0 * n_dead_tup / n_live_tup, 1)
         ELSE 0 END AS dead_pct,
    last_vacuum,
    last_autovacuum
FROM pg_stat_user_tables
ORDER BY n_dead_tup DESC
LIMIT 20;

-- Index bloat (requires pgstattuple extension)
CREATE EXTENSION IF NOT EXISTS pgstattuple;
SELECT * FROM pgstatindex('idx_orders_created');
-- Returns: version, tree_level, index_size, root_block_no, internal_pages,
--          leaf_pages, empty_pages, deleted_pages, avg_leaf_density,
--          leaf_fragmentation
-- High deleted_pages or low avg_leaf_density = bloat → REINDEX

-- Rebuild a bloated index (non-blocking in PG 12+)
REINDEX INDEX CONCURRENTLY idx_orders_created;

-- Or for all indexes on a table:
REINDEX TABLE CONCURRENTLY orders;
```

### VACUUM vs VACUUM FULL

```text
Regular VACUUM:
  - Marks dead tuples for reuse (space stays allocated to the table)
  - Does NOT return disk space to OS
  - Does NOT block reads/writes (only ShareUpdateExclusiveLock)
  - Runs automatically via autovacuum
  - Use: ALWAYS (let autovacuum handle it)

VACUUM FULL:
  - Rewrites the entire table (new copy, removes all dead tuples)
  - Returns disk space to OS
  - BLOCKS ALL ACCESS (AccessExclusiveLock) — table is unusable during operation
  - Takes longer: proportional to table size
  - Use: maintenance window, when disk is critically low, or bloat is extreme
  - BETTER ALTERNATIVE: pg_repack (external tool, non-blocking)
```

---

## Index Maintenance

```sql
-- Find unused indexes (idx_scan = 0 for months despite writes)
SELECT
    schemaname || '.' || indexrelname AS index_name,
    idx_scan AS scans,
    idx_tup_read AS tuples_read,
    idx_tup_fetch AS tuples_fetched,
    pg_size_pretty(pg_relation_size(indexrelid)) AS size,
    pg_get_indexdef(indexrelid) AS definition
FROM pg_stat_user_indexes
WHERE idx_scan = 0
  AND schemaname NOT IN ('pg_catalog', 'information_schema')
ORDER BY pg_relation_size(indexrelid) DESC;

-- Find duplicate indexes
SELECT
    indrelid::regclass AS table_name,
    array_agg(indexrelid::regclass) AS indexes
FROM pg_index
GROUP BY indrelid, indkey
HAVING count(*) > 1;

-- Find indexes with high write-to-read ratio
SELECT
    schemaname || '.' || tablename AS table_name,
    n_tup_ins + n_tup_upd + n_tup_del AS writes,
    idx_scan AS reads,
    CASE WHEN idx_scan > 0
         THEN ROUND(1.0 * (n_tup_ins + n_tup_upd + n_tup_del) / idx_scan, 1)
         ELSE -1 END AS writes_per_read
FROM pg_stat_user_tables
WHERE (n_tup_ins + n_tup_upd + n_tup_del) > 0
ORDER BY writes_per_read DESC
LIMIT 10;
```

---

## Python psycopg2 Connection Pool

```python
#!/usr/bin/env python3
"""
Production-grade PostgreSQL connection pool with health checks,
connection retry, and idle-in-transaction timeout.
"""

import os
import logging
import time
from contextlib import contextmanager
from typing import Optional

import psycopg2
import psycopg2.extras
import psycopg2.pool
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type, before_sleep_log
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class PostgresPool:
    """Thread-safe PostgreSQL connection pool with failover support."""

    def __init__(
        self,
        host: str = None,
        port: int = 5432,
        database: str = None,
        user: str = None,
        password: str = None,
        min_connections: int = 2,
        max_connections: int = 10,
        idle_timeout: int = 300,    # 5 min — kill idle-in-transaction
        connect_timeout: int = 5,
    ):
        self.host = host or os.environ['DB_HOST']
        self.port = port
        self.database = database or os.environ.get('DB_NAME', 'app')
        self.user = user or os.environ.get('DB_USER', 'app')
        self.password = password or os.environ['DB_PASSWORD']

        self.pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=min_connections,
            maxconn=max_connections,
            host=self.host,
            port=self.port,
            dbname=self.database,
            user=self.user,
            password=self.password,
            connect_timeout=connect_timeout,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=5,
            # Session configuration for every new connection
            options=(
                f"-c idle_in_transaction_session_timeout={idle_timeout * 1000} "
                f"-c statement_timeout=30000 "
                f"-c lock_timeout=10000"
            ),
        )
        logger.info(
            f"Connection pool created: {min_connections}-{max_connections} "
            f"connections to {self.host}:{self.port}/{self.database}"
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((
            psycopg2.OperationalError,
            psycopg2.InterfaceError,
        )),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    @contextmanager
    def get_connection(self, readonly: bool = False):
        """Get a connection from the pool. Context manager ensures return."""
        conn = None
        try:
            conn = self.pool.getconn()
            conn.set_session(readonly=readonly, autocommit=False)

            # Health check: verify connection is alive
            with conn.cursor() as cur:
                cur.execute("SELECT 1")

            yield conn

        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            # Connection lost during use — don't return to pool
            if conn and not conn.closed:
                self.pool.putconn(conn, close=True)
                conn = None
            logger.error(f"Connection error: {e}")
            raise

        except psycopg2.Error as e:
            if conn and not conn.closed:
                conn.rollback()
            logger.error(f"Query error: {e}")
            raise

        finally:
            if conn and not conn.closed:
                try:
                    conn.rollback()  # Clean up any open transaction
                except Exception:
                    pass
                self.pool.putconn(conn)

    def execute(self, query: str, params: tuple = None, readonly: bool = False):
        """Execute a query with automatic connection management."""
        with self.get_connection(readonly=readonly) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(query, params or ())
                if cur.description:
                    return cur.fetchall()
                else:
                    conn.commit()
                    return cur.rowcount

    def health_check(self) -> dict:
        """Check pool and database health."""
        result = {
            "pool": "unknown",
            "database": "unknown",
            "is_replica": None,
            "connections": None,
        }
        try:
            with self.get_connection(readonly=True) as conn:
                cur = conn.cursor()
                cur.execute("SELECT 1, pg_is_in_recovery()")
                row = cur.fetchone()
                result["database"] = "ok"
                result["is_replica"] = row[1]
            result["pool"] = "ok"
        except Exception as e:
            result["pool"] = f"error: {e}"
            result["database"] = f"error: {e}"
        return result

    def close(self):
        self.pool.closeall()
        logger.info("Connection pool closed")


# Usage
if __name__ == '__main__':
    pool = PostgresPool(max_connections=5)

    try:
        users = pool.execute("SELECT email FROM users WHERE active = true LIMIT 10", readonly=True)
        print(f"Users: {users}")

        rows = pool.execute(
            "INSERT INTO audit_log (action, created_at) VALUES (%s, NOW())",
            ('health_check',)
        )
        print(f"Inserted {rows} rows")

        print(f"Health: {pool.health_check()}")
    finally:
        pool.close()
```

---

## Java HikariCP Monitoring

```java
import com.zaxxer.hikari.HikariConfig;
import com.zaxxer.hikari.HikariDataSource;
import com.zaxxer.hikari.HikariPoolMXBean;

import javax.management.JMX;
import javax.management.MBeanServer;
import javax.management.ObjectName;
import java.lang.management.ManagementFactory;

public class HikariMonitor {

    /** Register and query HikariCP MBean for real-time pool metrics. */
    public static void monitor(HikariDataSource ds, String poolName) {
        HikariPoolMXBean poolBean = ds.getHikariPoolMXBean();

        if (poolBean == null) {
            System.err.println("Pool not initialized yet");
            return;
        }

        System.out.printf("--- %s Pool Metrics ---%n", poolName);
        System.out.printf("Active connections:    %d%n", poolBean.getActiveConnections());
        System.out.printf("Idle connections:      %d%n", poolBean.getIdleConnections());
        System.out.printf("Total connections:     %d%n", poolBean.getTotalConnections());
        System.out.printf("Threads waiting:       %d%n", poolBean.getThreadsAwaitingConnection());
        System.out.printf("Pending connections:   %d%n", poolBean.getActiveConnections() + poolBean.getThreadsAwaitingConnection());
        System.out.println();
    }

    /** Detect N+1 query pattern via connection acquisition rate. */
    public static void detectNPlusOne(HikariDataSource ds) {
        HikariPoolMXBean poolBean = ds.getHikariPoolMXBean();

        // If many threads are waiting AND active connections == max pool size:
        // connections returned slowly because each request holds them too long
        // (possible N+1: many rapid sequential queries per request)

        int active = poolBean.getActiveConnections();
        int waiting = poolBean.getThreadsAwaitingConnection();
        int max = ds.getMaximumPoolSize();

        if (active == max && waiting > 5) {
            System.err.printf(
                "WARNING: Pool exhausted! Active=%d, Waiting=%d, Max=%d. " +
                "Possible causes: N+1 queries, long transactions, or slow queries.%n",
                active, waiting, max
            );
        }
    }

    /** Sample pool config for PostgreSQL with failover awareness. */
    public static HikariDataSource createPool(String host, String db) {
        HikariConfig config = new HikariConfig();

        config.setJdbcUrl(
            "jdbc:postgresql://" + host + ":5432/" + db +
            "?socketTimeout=30" +
            "&connectTimeout=5" +
            "&tcpKeepAlive=true" +
            "&ApplicationName=myapp" +
            "&prepareThreshold=1" +       // Avoid PREPARE for dynamic queries
            "&defaultRowFetchSize=1000"    // Prevent loading entire result to memory
        );

        config.setUsername(System.getenv("DB_USER"));
        config.setPassword(System.getenv("DB_PASSWORD"));

        // Pool sizing: connections = ((core_count * 2) + effective_spindle_count)
        // For typical cloud DB: 10-20 connections per pool is a safe start
        config.setMinimumIdle(5);
        config.setMaximumPoolSize(20);
        config.setConnectionTimeout(3000);   // 3s — fail fast, don't queue
        config.setValidationTimeout(2000);
        config.setIdleTimeout(600_000);      // 10 min
        config.setMaxLifetime(1_800_000);    // 30 min — must be < wait_timeout on server
        config.setConnectionTestQuery("SELECT 1");
        config.setLeakDetectionThreshold(30_000); // 30s — warn on leaked connections

        // Don't fail on startup if DB is temporarily unreachable
        config.setInitializationFailTimeout(-1);

        return new HikariDataSource(config);
    }

    public static void main(String[] args) throws Exception {
        HikariDataSource ds = createPool(
            System.getenv("DB_HOST"),
            System.getenv("DB_NAME")
        );

        // Register JMX for monitoring
        Thread.sleep(1000); // Wait for pool init

        // Periodic monitoring
        for (int i = 0; i < 5; i++) {
            monitor(ds, "myapp-pool");
            detectNPlusOne(ds);
            Thread.sleep(5000);
        }

        ds.close();
    }
}
```

---

## References

- [PostgreSQL Documentation — Monitoring](https://www.postgresql.org/docs/current/monitoring.html)
- [PostgreSQL Wiki — Lock Monitoring](https://wiki.postgresql.org/wiki/Lock_Monitoring)
- [Autovacuum Tuning](https://www.postgresql.org/docs/current/runtime-config-autovacuum.html)
- [psycopg2 Documentation](https://www.psycopg.org/docs/)
- [HikariCP Configuration](https://github.com/brettwooldridge/HikariCP/wiki/About-Pool-Sizing)
- [PgBouncer Configuration](https://www.pgbouncer.org/config.html)
