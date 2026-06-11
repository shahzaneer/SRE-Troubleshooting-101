# MySQL Troubleshooting

> **Category:** Databases | MySQL
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#mysql` `#database` `#oncall`

---

## Table of Contents

1. [SHOW PROCESSLIST](#show-processlist)
2. [InnoDB Status & Deadlocks](#innodb-status--deadlocks)
3. [Replication](#replication)
4. [Table Locking](#table-locking)
5. [Query Cache (Deprecated in 8.0)](#query-cache-deprecated-in-80)
6. [Binary Log & Point-in-Time Recovery](#binary-log--point-in-time-recovery)
7. [pt-query-digest](#pt-query-digest)
8. [Slow Query Log](#slow-query-log)

---

## SHOW PROCESSLIST

```sql
-- Current connections and queries
SELECT id, user, host, db, command, time, state,
       LEFT(info, 200) AS query_info
FROM information_schema.processlist
WHERE command != 'Sleep'
ORDER BY time DESC;

-- Summarize by user/host
SELECT user, host, db, command, count(*) AS connections,
       AVG(time) AS avg_time_sec
FROM information_schema.processlist
GROUP BY user, host, db, command
ORDER BY connections DESC;

-- Find idle connections
SELECT id, user, host, db, time AS idle_seconds,
       LEFT(info, 100) AS last_query
FROM information_schema.processlist
WHERE command = 'Sleep'
  AND time > 600  -- idle > 10 minutes
ORDER BY time DESC;

-- Kill a connection
KILL CONNECTION 1234;  -- graceful (waits for transaction)
KILL QUERY 1234;       -- kill current query only (connection stays)

-- Max connections check
SHOW VARIABLES LIKE 'max_connections';
SHOW STATUS LIKE 'Threads_connected';
-- Utilization: Threads_connected / max_connections * 100
```

### Scenario: "MySQL Connections at 100%, Can't Connect"

```text
Symptom: Application getting "Too many connections" (error 1040).
         Even mysql CLI from bastion host fails.

Check:
  SHOW VARIABLES LIKE 'max_connections';        → 200
  SHOW STATUS LIKE 'Threads_connected';          → 200

  SELECT user, host, command, count(*) AS count
  FROM information_schema.processlist
  GROUP BY user, host, command
  ORDER BY count DESC;

  → app_user@10.0.1.0, Sleep, 185 idle connections
  → app_user@10.0.1.0, Query, 15 active

  ROOT CAUSE: Application connection pool has wait_timeout disabled.
  Idle connections never close. Over time, 200 connections accumulate,
  all idle but consuming slots. New connections denied.

Fix:
  1. IMMEDIATE: Kill idle connections
     SELECT GROUP_CONCAT(id) FROM information_schema.processlist
     WHERE command = 'Sleep' AND time > 600 INTO @ids;
     -- Then: KILL each one, OR set wait_timeout low for new sessions

  2. Configure wait_timeout (RDS parameter group):
     wait_timeout = 300  (5 min — close idle after 5 min)
     interactive_timeout = 300

  3. Fix application connection pool:
     - Set max pool size to 20 (not unlimited)
     - Set connection maxLifetime to 300000 (5 min)
     - Set idleTimeout to 300000
```

---

## InnoDB Status & Deadlocks

```sql
-- Full InnoDB engine status
SHOW ENGINE INNODB STATUS\G
```

### Key Sections

```text
LATEST DETECTED DEADLOCK:
  Shows exactly which two transactions deadlocked, which locks they held,
  and which locks they were waiting for. MySQL chose one as the victim.

  Example output:
  ------------------------
  LATEST DETECTED DEADLOCK
  ------------------------
  2026-06-11 10:15:30 0x7f8b2c001700
  *** (1) TRANSACTION:
  TRANSACTION 123456, ACTIVE 0 sec
  UPDATE orders SET status = 'shipped' WHERE id = 100;
  *** (1) HOLDS THE LOCK(S): PRIMARY lock on orders table, row id=100
  *** (1) WAITING FOR: PRIMARY lock on orders table, row id=200

  *** (2) TRANSACTION:
  TRANSACTION 123457, ACTIVE 0 sec
  UPDATE inventory SET quantity = quantity - 1 WHERE product_id = 300;
  *** (2) HOLDS THE LOCK(S): PRIMARY lock on inventory, row id=300
  *** (2) WAITING FOR: PRIMARY lock on orders table, row id=100

  → Transaction 1 has order 100 locked, wants inventory 300.
  → Transaction 2 has inventory 300 locked, wants order 100.
  → Classic deadlock cycle. MySQL rolled back transaction (2).

TRANSACTIONS:
  Current active transactions. Look for:
    - ACTIVE (sec): how long the transaction has been running
    - "starting index read", "updating", "committing"
    - Lock wait status: "waiting for lock" → contention

BUFFER POOL AND MEMORY:
  Buffer pool hit rate: (1 - (reads / read_requests)) * 100
  If hit rate < 95%: buffer pool is too small for working set.
    innodb_buffer_pool_size = 70-80% of available RAM on a dedicated DB server.
```

### Deadlock Detection Queries

```sql
-- Performance Schema deadlock tracking (MySQL 5.7+)
SELECT * FROM performance_schema.data_locks;
SELECT * FROM performance_schema.data_lock_waits;

-- Blocking query tree (who's blocking whom)
SELECT
    r.trx_id AS waiting_trx,
    r.trx_mysql_thread_id AS waiting_thread,
    r.trx_query AS waiting_query,
    b.trx_id AS blocking_trx,
    b.trx_mysql_thread_id AS blocking_thread,
    b.trx_query AS blocking_query,
    TIMESTAMPDIFF(SECOND, r.trx_wait_started, NOW()) AS wait_seconds
FROM information_schema.innodb_lock_waits w
JOIN information_schema.innodb_trx b
  ON b.trx_id = w.blocking_trx_id
JOIN information_schema.innodb_trx r
  ON r.trx_id = w.requesting_trx_id;

-- Deadlock metric
SHOW GLOBAL STATUS LIKE 'Innodb_deadlocks';

-- Enable deadlock logging (print ALL deadlocks to error log)
-- Parameter: innodb_print_all_deadlocks = ON
```

### Scenario: "Deadlock Between Checkout and Inventory Update"

```text
Symptom: Checkout API randomly fails with "Deadlock found when trying
         to get lock; try restarting transaction" (error 1213).
         ~0.5% of checkout requests fail. Retries usually work.

Investigation:
  SHOW ENGINE INNODB STATUS\G → LATEST DETECTED DEADLOCK:

  T1 (checkout): INSERT INTO orders (...) VALUES (...);
                 UPDATE inventory SET quantity=quantity-1 WHERE product_id=X;
  T2 (checkout): INSERT INTO orders (...) VALUES (...);
                 UPDATE inventory SET quantity=quantity-1 WHERE product_id=Y;

  But the deadlock shows BOTH transactions updating inventory rows
  for the SAME product_id. How?

  ROOT CAUSE: The orders table has a FOREIGN KEY constraint on
  product_id referencing inventory. The INSERT into orders acquires
  a SHARED lock on the parent row in inventory. Two concurrent INSERTs
  acquire shared locks on the same inventory row. Then both try to UPDATE
  inventory → both try to upgrade shared to exclusive → deadlock.

Neither can proceed:
  T1: holds shared, wants exclusive for update
  T2: holds shared, wants exclusive for update
  Both wait for the other to release shared → deadlock.

Fix:
  1. Reorder operations: UPDATE inventory first (acquires exclusive lock
     directly), THEN INSERT into orders.
  2. Retry logic in application: catch deadlock (error 1213), retry with
     exponential backoff.
  3. Use SELECT ... FOR UPDATE on the inventory row before updating,
     ensuring exclusive lock acquisition at the start of transaction.
```

---

## Replication

```sql
-- Replica status (includes Seconds_Behind_Master)
SHOW SLAVE STATUS\G

-- Key fields to check:
-- Slave_IO_Running: Yes  → I/O thread is receiving binary logs from primary
-- Slave_SQL_Running: Yes → SQL thread is applying relay logs
-- Seconds_Behind_Master: 0 → up to date (or how many seconds behind)
-- Last_IO_Error / Last_SQL_Error: any replication errors
-- Retrieved_Gtid_Set / Executed_Gtid_Set: GTID progress

-- Binary log position on primary
SHOW MASTER STATUS;

-- List binary logs
SHOW BINARY LOGS;

-- Show events from a specific binlog
SHOW BINLOG EVENTS IN 'mysql-bin-changelog.000001' LIMIT 20;
```

### Scenario: "Replica Lag 2000s, Slave_SQL_Running: No"

```text
Symptom: Read replica stopped applying changes. Monitor shows
         Slave_SQL_Running: No, Seconds_Behind_Master: NULL.

Check:
  SHOW SLAVE STATUS\G

  Last_SQL_Error: Could not execute Write_rows event on table mydb.orders;
  Duplicate entry '50001' for key 'PRIMARY', Error_code: 1062

  ROOT CAUSE: A row with id=50001 already exists on the replica but
  doesn't exist on the primary. The replica had data written to it
  directly (bypassing replication). When the primary's binlog event
  tries to INSERT id=50001, the replica fails with duplicate key.

  This is data inconsistency:
  - Someone connected to the replica with read_only=OFF and wrote data.
  - OR: A non-deterministic query (NOW(), UUID()) generated different
    values on primary vs replica.
  - OR: Replication was temporarily broken, data diverged, then restarted.

Fix:
  1. NEVER write to replicas. Set read_only = ON:
     SET GLOBAL read_only = ON;
     -- Also set in parameter group (RDS: read_only = 1)

  2. Skip the problematic event (TEMPORARY — data will be inconsistent):
     SET GLOBAL SQL_SLAVE_SKIP_COUNTER = 1;
     START SLAVE;
     -- This skips ONE event. Data is now inconsistent between primary
     -- and replica. Fix in the next step.

  3. Full resync (PERMANENT — restores consistency):
     - Stop replication on replica
     - Take a new snapshot from primary
     - Restore snapshot to replica
     - Restart replication from the snapshot's binlog position
```

---

## Table Locking

### MyISAM vs InnoDB Locking

```text
MyISAM: TABLE-LEVEL LOCKS
  - SELECT acquires READ lock on entire table (concurrent SELECTs allowed)
  - INSERT/UPDATE/DELETE acquires WRITE lock (blocks ALL other access)
  - No transactions, no row-level locking
  - Scenario: MyISAM table causes full-table lock on SELECT while INSERT waits.
    → 20 SELECT queries/sec block any writes. Writes queue up.
    → Migration to InnoDB fixes concurrent read/write.

InnoDB: ROW-LEVEL LOCKS (default)
  - Multiple transactions can modify different rows simultaneously
  - Row locks + gap locks (prevents phantom reads in REPEATABLE READ)
  - Deadlock detection built in
  - Scenario: 100 concurrent UPDATEs on different rows → all succeed.
    Same on MyISAM → serialized (one at a time).
```

### Check Table Engine

```sql
-- Check which tables are still MyISAM
SELECT TABLE_SCHEMA, TABLE_NAME, ENGINE
FROM information_schema.TABLES
WHERE ENGINE = 'MyISAM'
  AND TABLE_SCHEMA NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys');

-- Convert MyISAM to InnoDB
ALTER TABLE old_table ENGINE = InnoDB;
-- This is an ONLINE operation in MySQL 5.6+ (table is still accessible)
-- Takes time proportional to table size.
```

---

## Query Cache (Deprecated in 8.0)

```text
Query Cache was removed in MySQL 8.0. If you're on 5.7:

  SHOW STATUS LIKE 'Qcache%';
  - Qcache_hits: total cache hits
  - Qcache_inserts: queries added to cache
  - Qcache_lowmem_prunes: evictions due to low memory
  - Qcache_free_memory: available cache memory

Why it was removed:
  1. Global mutex contention: the query cache has a SINGLE global mutex.
     On multi-core servers, every query (read or write) contends for it.
     This becomes a bottleneck at high concurrency.

  2. Invalidation: ANY write to a table invalidates ALL cached queries
     for that table, even if the write doesn't affect the query result.
     On write-heavy tables, cache hit rate approaches 0%.

  3. Low hit rate in typical OLTP workloads. Often < 5%.

Alternatives:
  - Redis/Memcached for application-level caching
  - InnoDB buffer pool (caches data pages, not query results)
  - Application-side caching with invalidation logic (TinyCD, Caffeine for Java)
```

---

## Binary Log & Point-in-Time Recovery

```sql
-- List all binary logs
SHOW BINARY LOGS;

-- Show events from a specific binary log
SHOW BINLOG EVENTS IN 'mysql-bin.000001' FROM 123456 LIMIT 10;

-- Check binary log format
SHOW VARIABLES LIKE 'binlog_format';
-- ROW (recommended): logs actual row changes (safe, larger)
-- STATEMENT: logs SQL statements (smaller, but non-deterministic)
-- MIXED: MySQL decides per statement

-- Point-in-time recovery using mysqlbinlog:
-- On backup server:
-- Restore base backup:
--   mysql < full_backup.sql
-- Replay binlog to specific point:
--   mysqlbinlog --start-datetime="2026-06-11 09:00:00" \
--               --stop-datetime="2026-06-11 10:15:00" \
--               mysql-bin.000001 mysql-bin.000002 | mysql
-- Or to a specific GTID:
--   mysqlbinlog --include-gtids='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee:1-50000' \
--               mysql-bin.000001 | mysql
```

---

## pt-query-digest

Percona Toolkit's `pt-query-digest` analyzes slow query logs and summarizes
the most expensive queries.

```bash
# Install
# macOS: brew install percona-toolkit
# Linux: apt-get install percona-toolkit / yum install percona-toolkit

# Analyze slow query log
pt-query-digest /var/log/mysql/slow-query.log | head -100

# Analyze a specific time range
pt-query-digest --since '2026-06-11 09:00:00' --until '2026-06-11 10:00:00' \
  /var/log/mysql/slow-query.log

# Output sections:
# 1. Profile: Top queries by total execution time, count, and row impact
# 2. Response time distribution: how time is spent (connection, waiting, execution)
# 3. Each query's full details: worst executions, tables scanned, EXPLAIN

# Filter by database
pt-query-digest --filter '$event->{db} eq "mydb"' slow.log

# Stream from running MySQL (processlist)
pt-query-digest --processlist h=localhost,u=root

# Get EXPLAIN plans for slow queries
pt-query-digest --explain h=localhost,u=root slow.log
```

### Scenario: "pt-query-digest Shows One Query at 85% of Total Time"

```text
pt-query-digest output:
  Profile
  Rank Query ID           Response time    Calls  R/Call V/M   Item
  ==== ================== ================ ====== ====== ===== ====
  1    0x59A3D8F6C2...    856.0000 85.6%   423   2.0241  0.04 SELECT orders

  Query 1: SELECT orders
    Total time: 856s (85.6% of total server time)
    Calls: 423
    Avg time: 2.02s per call
    Rows examined: avg 8,500,000 rows per call

  This query reads 8.5M rows per execution and it runs 423 times
  in the analysis window. It's almost certainly missing an index
  on the WHERE clause column.

  Action:
  1. Get the exact query:
     pt-query-digest slow.log --limit=1
  2. Run EXPLAIN on it:
     EXPLAIN SELECT * FROM orders WHERE merchant_id = 42 AND status = 'pending';
  3. Likely missing: CREATE INDEX idx_orders_merchant_status ON orders(merchant_id, status);
  4. After adding index: re-analyze slow log to verify improvement.
```

---

## Slow Query Log

```sql
-- Check if slow query log is enabled
SHOW VARIABLES LIKE 'slow_query_log';
SHOW VARIABLES LIKE 'long_query_time';   -- queries longer than this (seconds)

-- Enable dynamically (no restart)
SET GLOBAL slow_query_log = 'ON';
SET GLOBAL long_query_time = 1;           -- log queries > 1 second
SET GLOBAL log_queries_not_using_indexes = 'ON';

-- Check log file location
SHOW VARIABLES LIKE 'slow_query_log_file';

-- View slow queries in table (MySQL 5.6+)
SELECT * FROM mysql.slow_log
ORDER BY start_time DESC
LIMIT 20;

-- Download from RDS
# aws rds download-db-log-file-portion \
#   --db-instance-identifier mydb \
#   --log-file-name slowquery/mysql-slowquery.log \
#   --output text > slow_queries.log

-- Quick size check of slow query log
SELECT COUNT(*) FROM mysql.slow_log;
```

---

## References

- [MySQL Documentation — SHOW PROCESSLIST](https://dev.mysql.com/doc/refman/8.0/en/show-processlist.html)
- [InnoDB Deadlock Detection](https://dev.mysql.com/doc/refman/8.0/en/innodb-deadlock-detection.html)
- [MySQL Replication](https://dev.mysql.com/doc/refman/8.0/en/replication.html)
- [Percona Toolkit — pt-query-digest](https://docs.percona.com/percona-toolkit/pt-query-digest.html)
- [Binary Logging](https://dev.mysql.com/doc/refman/8.0/en/binary-log.html)
