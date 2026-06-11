# 05 — Databases

> **Diagnosing database failures: connection exhaustion, query performance, replication lag, deadlocks, and caching.**
> Databases are the source of truth. When they're broken, everything is broken.

---

## Quick Navigation

| # | Document | What You'll Learn | Time to Read |
|---|----------|-------------------|--------------|
| 1 | [PostgreSQL Troubleshooting](postgresql/postgres-troubleshooting.md) | Lock contention, autovacuum, WAL replication, EXPLAIN ANALYZE, bloat, PgBouncer | 25 min |
| 2 | [MySQL Troubleshooting](mysql/mysql-troubleshooting.md) | InnoDB status, deadlocks, replication, slow queries, pt-query-digest | 20 min |
| 3 | [Redis Troubleshooting](redis/redis-troubleshooting.md) | Memory eviction, SLOWLOG, cluster, key expiry, connection pooling | 15 min |

---

## Database First 30 Seconds

```bash
# Is the database accepting connections?
# PostgreSQL
psql -h DBHOST -U app -d mydb -c "SELECT 1"

# MySQL
mysql -h DBHOST -u app -p -e "SELECT 1"

# Redis
redis-cli -h REDISHOST PING

# How many connections?
# PostgreSQL: SELECT count(*) FROM pg_stat_activity;
# MySQL: SHOW STATUS LIKE 'Threads_connected';

# Any long-running queries?
# PostgreSQL: SELECT pid, now()-query_start AS duration, query FROM pg_stat_activity WHERE state != 'idle' ORDER BY duration DESC;
# MySQL: SELECT * FROM information_schema.processlist WHERE command != 'Sleep' ORDER BY time DESC;

# Replication lag?
# PostgreSQL: SELECT now()-pg_last_xact_replay_timestamp() AS lag;
# MySQL: SHOW SLAVE STATUS\G
# Redis: redis-cli INFO replication
```

---

## Common Database Gotchas

| Gotcha | Explanation |
|--------|-------------|
| **Connection exhaustion** | max_connections reached. App pool size × pods > DB max connections. Fix: PgBouncer/RDS Proxy. |
| **Idle in transaction** | Application opened a transaction, ran a query, and never committed. Holds locks. Auto-kill idle transactions > 5 min. |
| **Autovacuum can't keep up** | Dead tuples accumulate faster than autovacuum can clean. Table bloats, queries slow down. Monitor n_dead_tup. |
| **Missing index after migration** | Migration script DROP CASCADE removes dependent indexes. Always verify indexes after schema changes. |
| **Replication lag from long transactions** | A 4-hour UPDATE holds WAL segments. Replica can't replay until it commits. |
| **Redis OOM from no maxmemory-policy** | Default policy is `noeviction`. When maxmemory is hit, writes fail. Always set a policy. |
| **KEYS command in production** | `KEYS *` blocks Redis for the duration. Use `SCAN` instead. |
| **SELECT * in production on large table** | Pulls all columns, all rows, saturates I/O. Use indexed queries with column lists and LIMIT. |

---

## References

- [PostgreSQL Documentation](https://www.postgresql.org/docs/current/)
- [MySQL Documentation](https://dev.mysql.com/doc/)
- [Redis Documentation](https://redis.io/docs/latest/)
- [RDS Troubleshooting Guide](../03-aws/rds/rds-troubleshooting.md)
