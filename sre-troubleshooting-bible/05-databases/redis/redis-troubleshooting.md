# Redis Troubleshooting

> **Category:** Databases | Redis
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#redis` `#cache` `#oncall`

---

## Table of Contents

1. [redis-cli INFO](#redis-cli-info)
2. [Memory Eviction Policies](#memory-eviction-policies)
3. [SLOWLOG](#slowlog)
4. [MONITOR (Use with Caution)](#monitor-use-with-caution)
5. [Key Expiry & TTL](#key-expiry--ttl)
6. [Redis Cluster](#redis-cluster)
7. [OBJECT ENCODING](#object-encoding)
8. [Python Redis Client](#python-redis-client)
9. [Java Jedis/Lettuce Connection Pool](#java-jedislettuce-connection-pool)

---

## redis-cli INFO

```bash
# All stats
redis-cli INFO

# Specific sections
redis-cli INFO server      # version, uptime, OS
redis-cli INFO clients     # connected_clients, blocked_clients
redis-cli INFO memory      # used_memory, maxmemory, eviction
redis-cli INFO stats       # ops/sec, hits/misses, evictions, expired
redis-cli INFO replication # role, connected_slaves, master_repl_offset
redis-cli INFO keyspace    # per-database key count, avg TTL
redis-cli INFO commandstats # per-command stats
redis-cli INFO cpu         # CPU consumption
redis-cli INFO persistence # RDB and AOF status
```

### Key Memory Metrics

```text
used_memory_human:      2.5G   → actual memory used by Redis
used_memory_rss_human:  3.1G   → OS-reported memory (includes fragmentation)
maxmemory_human:        4.0G   → configured limit
mem_fragmentation_ratio:1.24  → used_memory_rss / used_memory
                                > 1.5: high fragmentation → MEMORY PURGE or restart
                                < 1.0: swap used → DANGER (performance collapse)

Warning thresholds:
  used_memory > 90% maxmemory → near eviction
  mem_fragmentation_ratio > 2.0 → severe fragmentation
  mem_fragmentation_ratio < 0.8 → swapping (host-level emergency)
```

### Key Stats Metrics

```text
instantaneous_ops_per_sec: 85000     → current request throughput
total_connections_received: 5000000  → total since start
total_commands_processed: 500000000
keyspace_hits: 48000000              → cache hit count
keyspace_misses: 2000000             → cache miss count
hit_rate = hits / (hits + misses) = 96% → good

evicted_keys: 150000                 → keys evicted by maxmemory policy
                                      If > 0 and growing → memory limit reached
expired_keys: 50000                  → keys expired naturally by TTL
rejected_connections: 0              → maxclients reached
```

---

## Memory Eviction Policies

```bash
# Check current policy
redis-cli CONFIG GET maxmemory-policy

# Set policy
redis-cli CONFIG SET maxmemory-policy allkeys-lru
```

### Policy Comparison

| Policy | Eviction Target | Use Case |
|--------|----------------|----------|
| `noeviction` | None — returns error on write | Cache that must never lose data (not a real cache) |
| `allkeys-lru` | Least Recently Used across ALL keys | General-purpose cache. Evicts least-accessed first. |
| `allkeys-lfu` | Least Frequently Used across ALL keys | Better for access-pattern-based eviction (4.0+) |
| `volatile-lru` | LRU among keys WITH expiry | Mixed cache — keep persistent keys, evict temporary. |
| `volatile-lfu` | LFU among keys WITH expiry | Same but frequency-based |
| `volatile-ttl` | Keys closest to expiry | Evict keys about to expire anyway |
| `volatile-random` | Random among keys with expiry | Simple eviction, lower CPU overhead |

### Scenario: "Session Keys Disappearing Prematurely"

```text
Symptom: Users randomly logged out. Session keys set with 24-hour TTL
         are gone after 2 hours. No errors in application logs.

Investigation:
  redis-cli CONFIG GET maxmemory-policy → allkeys-lru
  redis-cli CONFIG GET maxmemory → 4294967296 (4GB)
  redis-cli INFO memory → used_memory_human: 3.9G
  redis-cli INFO stats → evicted_keys: 2500000 (high and growing)

  redis-cli DBSIZE → 1,250,000 keys
  redis-cli --bigkeys → shows 900K cache keys + 350K session keys

  ROOT CAUSE: maxmemory-policy is allkeys-lru, with 4GB limit but
  10GB worth of data (cache + sessions). Redis evicts keys based on
  LRU across ALL keys — cache keys and session keys are in the same
  eviction pool. The LRU algorithm evicts session keys that haven't
  been accessed recently (user went idle) even though they have a TTL.

Fix:
  1. Separate session keys into a DIFFERENT Redis instance with
     noeviction policy (4GB dedicated, no cache).
  2. OR: Use volatile-lru for the shared instance:
     redis-cli CONFIG SET maxmemory-policy volatile-lru
     → Only keys with TTL (sessions) are evicted. Persistent cache
       keys without TTL are never evicted.
  3. Ensure all cache keys have TTLs and session keys are tagged.
  4. Monitor evicted_keys over time to confirm fix.
```

---

## SLOWLOG

```bash
# Get the 25 slowest commands
redis-cli SLOWLOG GET 25

# Output per entry:
# 1) (integer) 42              ← slowlog entry ID
# 2) (integer) 1687471200      ← Unix timestamp
# 3) (integer) 2456789         ← execution time in MICROSECONDS (2.46 seconds!)
# 4) 1) "KEYS"                 ← command
#    2) "*user:sessions:*"     ← arguments
# 5) "192.168.1.50:54321"      ← client IP:port

# Reset slowlog (clear history)
redis-cli SLOWLOG RESET

# Check/configure slowlog settings
redis-cli CONFIG GET slowlog-log-slower-than   # log queries > N microseconds (0 = all, -1 = none)
redis-cli CONFIG GET slowlog-max-len           # max entries to keep

# Set threshold: log commands slower than 10ms
redis-cli CONFIG SET slowlog-log-slower-than 10000
```

### Scenario: "API Latency Spikes Every 5 Minutes"

```text
Symptom: P95 latency for an API endpoint spikes from 2ms to 2000ms
         every 5 minutes like clockwork. The endpoint reads from Redis.

Investigation:
  redis-cli SLOWLOG GET 50

  Entry 38: KEYS "cache:products:*"
    Execution time: 2,100,000 microseconds (2.1 seconds)
    Client: 10.0.5.23:45678
    Timestamp: matches the 5-minute spike pattern

  redis-cli CLIENT LIST | grep 10.0.5.23
  → Connection belongs to a health check script running via cron
    every 5 minutes.

  ROOT CAUSE: The health check script runs `KEYS cache:products:*`
  to verify cache population. KEYS is O(N) where N = total keys in DB.
  With 500K keys, KEYS blocks Redis for 2+ seconds. During this time,
  ALL other operations are queued → latency spike for ALL clients.

Fix:
  1. Replace KEYS with SCAN (non-blocking, cursor-based):
     python:
       cursor = 0
       while True:
           cursor, keys = redis.scan(cursor, match="cache:products:*", count=100)
           for key in keys:
               process(key)
           if cursor == 0:
               break

  2. Better: use a Redis SET to track product cache keys.
     When adding a product to cache:
       SET cache:product:123 "{...}" EX 3600
       SADD products:cached "123"
     When health-checking:
       SCARD products:cached → instant O(1) response

  3. NEVER use KEYS in production code. SCAN, SETS, or key-tagging.
```

---

## MONITOR (Use with Caution)

```text
⚠ WARNING: MONITOR streams ALL commands to your client. On a busy
  Redis instance (10K+ ops/sec), this:
  - Saturates your network bandwidth
  - Adds ~50% CPU overhead to Redis
  - Can cause Redis to swap

  NEVER run MONITOR in production on a busy instance.
  Use SLOWLOG instead, or MONITOR only on dev/staging.

  If you MUST use it in production:
    redis-cli MONITOR | head -1000   ← limit output
    redis-cli MONITOR | grep "mykey" ← filter
```

```bash
# See all commands in real-time (SAFE to run briefly, not for >10s)
redis-cli MONITOR | grep "ERROR\|DEL\|FLUSH" | head -100

# Filter for a specific key pattern
redis-cli MONITOR | grep "user:session:" | head -50
```

---

## Key Expiry & TTL

```bash
# Check TTL of a key
redis-cli TTL mykey
# Returns:
#   -1 → key exists but has NO expiry (persists forever)
#   -2 → key does NOT exist
#   N  → key expires in N seconds

# Set TTL on existing key
redis-cli EXPIRE mykey 3600    # expire in 1 hour
redis-cli EXPIREAT mykey 1718112000  # expire at specific Unix timestamp

# Remove TTL (make persistent)
redis-cli PERSIST mykey

# Create key with TTL
redis-cli SETEX mykey 3600 "value"    # SET with EXpire
redis-cli SET mykey "value" EX 3600   # alternative syntax

# Stats on expirations
redis-cli INFO stats | grep expired_keys
```

### Scenario: "Cache Keys Not Expiring"

```text
Symptom: Cache keys from 2 weeks ago still exist. Application assumes
         1-hour TTL on all cache keys. Data is stale but users see old
         cached values.

Investigation:
  redis-cli TTL "cache:user:123:profile"
  → -1 (key exists, NO expiry!)

  Check application code:
    redis.set("cache:user:123:profile", json.dumps(data))
  → Uses SET without EX or EXPIRE. No TTL set.

  ROOT CAUSE: The codebase uses `SET key value` instead of
  `SETEX key 3600 value`. The caching library's set() method
  was called without the ttl parameter.

Fix:
  1. Update application code to always set TTL:
     redis.setex("cache:user:123:profile", 3600, json.dumps(data))

  2. OR configure Redis for volatile-lru/volatile-lfu, then:
     redis-cli EXPIRE "cache:user:123:profile" 3600  ← retroactively

  3. Add a cache wrapper that enforces TTL:
     def cache_set(key, value, ttl=3600):
         redis.setex(key, ttl, json.dumps(value))

  4. Clean up existing persistent keys:
     # Find and add TTL to all keys without expiry
     # (Use SCAN, not KEYS!)
```

---

## Redis Cluster

```bash
# Cluster info
redis-cli CLUSTER INFO                     # overall cluster state
redis-cli CLUSTER NODES                    # each node's ID, role, slots
redis-cli CLUSTER SLOTS                    # slot-to-node mapping
redis-cli --cluster check HOST:PORT        # comprehensive cluster health check

# Key-to-slot mapping
redis-cli CLUSTER KEYSLOT mykey            # which slot does this key hash to?
redis-cli CLUSTER COUNTKEYSINSLOT 1234     # how many keys in this slot?

# Slot migration
redis-cli --cluster reshard HOST:PORT      # interactive resharding
redis-cli --cluster rebalance HOST:PORT    # rebalance slots across nodes
```

### Scenario: "Redis Cluster Returns MOVED Errors"

```text
Symptom: Application sporadically gets "(error) MOVED 1234 10.0.2.50:6379"
         responses from Redis. Some requests work, some don't.

Debugging:
  redis-cli -h 10.0.1.50 -p 6379 CLUSTER NODES
  → Shows 3 master nodes across 10.0.1.50, 10.0.2.50, 10.0.3.50
  → Cluster is healthy (all nodes connected)

  Application code:
    import redis
    r = redis.Redis(host='10.0.1.50', port=6379)
    r.get('user:123:profile')

  ROOT CAUSE: The Redis client is NOT cluster-aware. Standard
  redis.Redis() connects to a single node. When the key hashes
  to a slot on another node, that node returns a MOVED error
  telling the client to redirect. A non-cluster client doesn't
  understand MOVED errors.

Fix:
  Python:
    from redis.cluster import RedisCluster
    r = RedisCluster(
        host='10.0.1.50',
        port=6379,
        max_connections=100
    )
    # The cluster client automatically follows MOVED redirects
    # and caches the slot-to-node mapping.

  Java (Jedis):
    import redis.clients.jedis.JedisCluster;
    JedisCluster cluster = new JedisCluster(
        new HostAndPort("10.0.1.50", 6379)
    );

  Java (Lettuce):
    import io.lettuce.core.cluster.RedisClusterClient;
    RedisClusterClient client = RedisClusterClient.create(
        "redis://10.0.1.50:6379"
    );

  Also check: is the client library version recent enough?
  Older libraries may not support cluster mode at all.
```

---

## OBJECT ENCODING

Redis optimizes memory by using different internal encodings for values
that look the same. Encoding changes can dramatically affect memory usage.

```bash
redis-cli OBJECT ENCODING mykey
```

### Encodings Explained

```text
String values:
  int     → value is an integer (8 bytes). ~4x smaller than raw.
  embstr  → short string (<44 bytes). Stored inline with RedisObject.
  raw     → long string (>44 bytes). Stored separately with pointer.

Hash values:
  ziplist     → compact sequential structure for small hashes
                (< 512 entries, each < 64 bytes). ~50% smaller.
  hashtable   → standard hash table for larger hashes.

List values:
  quicklist   → linked list of ziplists (default in 5.0+)
  ziplist     → small lists only

Set values:
  intset      → sets of integers only. ~10x smaller than hashtable.
  hashtable   → standard hash table (contains any type or large int set)

Sorted Set:
  ziplist     → small sorted sets (<128 entries, each <64 bytes)
  skiplist    → skip list + hash table (standard)
```

### Scenario: "Memory Usage Spiked After Schema Change"

```text
Symptom: Redis memory usage jumped from 2GB to 8GB after a code change
         that seems unrelated. No new keys added, same number of keys.

Debugging:
  redis-cli INFO memory → used_memory_human: 8.2G (was 2.0G before deploy)

  Before deploy: OBJECT ENCODING cache:product:count:12345 → "int"
  After deploy:  OBJECT ENCODING cache:product:count:12345 → "raw"

  ROOT CAUSE: The code changed from storing integers to storing strings:
    Before: redis.set("cache:product:count:" + id, 5)
            → Redis detected int → used 8-byte int encoding

    After:  redis.set("cache:product:count:" + id, str(count))
            → Redis detected string → used ~80-byte raw encoding
            → 10x memory increase per key → 500K keys × 10x = 8GB

  Other common encoding change triggers:
    - id=12345 → id="12345" (int → string)
    - Adding a non-integer member to a set (intset → hashtable)
    - Exceeding ziplist thresholds (ziplist → hashtable)

Fix:
  1. Store numbers as integers:
     redis.set(key, int_value)  # don't convert to string

  2. Encode JSON numbers without quotes:
     json.dumps({"count": 5})  # not {"count": "5"}

  3. Monitor encoding thresholds:
     redis-cli CONFIG GET hash-max-ziplist-entries   → 512
     redis-cli CONFIG GET hash-max-ziplist-value      → 64
     If you exceed these, encoding silently changes.
```

---

## Python Redis Client

```python
#!/usr/bin/env python3
"""
Production-grade Redis client with connection retry, circuit breaker,
and fallback to stale cache on connection failure.
"""

import logging
import time
import json
from contextlib import contextmanager
from typing import Optional, Any

import redis
from redis.exceptions import (
    ConnectionError, TimeoutError, RedisError,
    ResponseError, ClusterDownError
)
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class RedisCache:
    """Redis cache wrapper with circuit breaker and graceful degradation."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: str = None,
        max_connections: int = 20,
        socket_timeout: float = 2.0,
        socket_connect_timeout: float = 2.0,
        default_ttl: int = 3600,
        circuit_breaker_threshold: int = 5,
        circuit_breaker_reset: float = 30.0,
    ):
        self.default_ttl = default_ttl
        self._failure_count = 0
        self._circuit_breaker_threshold = circuit_breaker_threshold
        self._circuit_breaker_reset = circuit_breaker_reset
        self._last_failure_time = 0.0
        self._circuit_open = False

        self.pool = redis.ConnectionPool(
            host=host,
            port=port,
            db=db,
            password=password,
            max_connections=max_connections,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_connect_timeout,
            socket_keepalive=True,
            retry_on_timeout=True,
            health_check_interval=30,
        )
        self.client = redis.Redis(connection_pool=self.pool)
        self._test_connection()

    def _test_connection(self):
        try:
            self.client.ping()
            logger.info("Redis connection established")
        except RedisError as e:
            logger.warning(f"Redis not available at startup: {e}")

    def _is_circuit_open(self) -> bool:
        """Check if circuit breaker is tripped."""
        if not self._circuit_open:
            return False
        # Allow a probe after reset interval
        if time.time() - self._last_failure_time > self._circuit_breaker_reset:
            self._circuit_open = False
            self._failure_count = 0
            logger.info("Circuit breaker: HALF-OPEN (probing)")
            return False
        return True

    def _record_failure(self):
        self._failure_count += 1
        self._last_failure_time = time.time()
        if self._failure_count >= self._circuit_breaker_threshold:
            self._circuit_open = True
            logger.error(
                f"Circuit breaker: OPEN ({self._failure_count} consecutive failures)"
            )

    def _record_success(self):
        self._failure_count = 0
        if self._circuit_open:
            logger.info("Circuit breaker: CLOSED (recovered)")
        self._circuit_open = False

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=0.1, max=2),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
    )
    def get(self, key: str, fallback: Any = None) -> Optional[Any]:
        """Get value from cache with retry and fallback."""
        if self._is_circuit_open():
            logger.warning(f"Circuit open — returning fallback for key: {key}")
            return fallback

        try:
            value = self.client.get(key)
            if value is None:
                return fallback
            # Attempt JSON decode, fallback to raw bytes
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value.decode('utf-8')
        except (ConnectionError, TimeoutError) as e:
            self._record_failure()
            raise
        except RedisError as e:
            logger.error(f"Redis error on GET {key}: {e}")
            self._record_failure()
            return fallback
        else:
            self._record_success()

    def set(self, key: str, value: Any, ttl: int = None) -> bool:
        """Set value in cache with TTL."""
        if self._is_circuit_open():
            return False

        try:
            if isinstance(value, (dict, list, int, float, bool)):
                serialized = json.dumps(value)
            else:
                serialized = str(value)

            ttl = ttl or self.default_ttl
            self.client.setex(key, ttl, serialized)
            return True
        except RedisError as e:
            logger.error(f"Redis error on SET {key}: {e}")
            self._record_failure()
            return False
        else:
            self._record_success()

    def delete(self, key: str) -> bool:
        """Delete key from cache."""
        try:
            self.client.delete(key)
            return True
        except RedisError as e:
            logger.error(f"Redis error on DELETE {key}: {e}")
            return False

    @contextmanager
    def lock(self, key: str, timeout: float = 10.0):
        """Distributed lock context manager."""
        lock = self.client.lock(key, timeout=timeout)
        acquired = lock.acquire(blocking=True, blocking_timeout=timeout)
        if not acquired:
            raise TimeoutError(f"Could not acquire lock: {key}")
        try:
            yield lock
        finally:
            lock.release()

    def health_check(self) -> dict:
        """Check Redis connectivity and basic stats."""
        try:
            self.client.ping()
            info = self.client.info('memory')
            return {
                "status": "ok",
                "used_memory_human": info.get('used_memory_human', 'unknown'),
                "connected_clients": self.client.info('clients').get('connected_clients', -1),
                "circuit_open": self._circuit_open,
            }
        except RedisError as e:
            return {"status": "error", "error": str(e)}

    def close(self):
        self.pool.disconnect()


# Usage
if __name__ == '__main__':
    cache = RedisCache(host='localhost', default_ttl=3600)

    cache.set("user:1", {"name": "Alice", "email": "alice@example.com"})
    user = cache.get("user:1", fallback={"name": "Unknown"})
    print(f"User: {user}")

    print(f"Health: {cache.health_check()}")
    cache.close()
```

---

## Java Jedis/Lettuce Connection Pool

```java
import redis.clients.jedis.*;
import java.time.Duration;

public class RedisConnectionManager {

    /** Production-grade Jedis pool configuration. */
    public static JedisPool createJedisPool(String host, int port, String password) {
        JedisPoolConfig poolConfig = new JedisPoolConfig();

        // Pool sizing
        poolConfig.setMaxTotal(20);
        poolConfig.setMaxIdle(10);
        poolConfig.setMinIdle(5);

        // Connection validation
        poolConfig.setTestOnBorrow(true);
        poolConfig.setTestOnReturn(false);
        poolConfig.setTestWhileIdle(true);
        poolConfig.setMinEvictableIdleTimeMillis(60_000);  // 1 minute
        poolConfig.setTimeBetweenEvictionRunsMillis(30_000); // 30 seconds

        // Graceful degradation under load
        poolConfig.setBlockWhenExhausted(false);  // Don't block — fail fast
        poolConfig.setMaxWaitMillis(2000);        // 2s max wait for connection

        return new JedisPool(
            poolConfig,
            host, port,
            2000,    // connection timeout
            2000,    // socket timeout
            password,
            0,       // database
            null,     // client name
            true      // use SSL
        );
    }

    /** Retry wrapper for Jedis operations. */
    public static <T> T withRetry(ThrowingSupplier<T> operation, int maxRetries) {
        int attempt = 0;
        Exception lastException = null;

        while (attempt < maxRetries) {
            attempt++;
            try {
                return operation.get();
            } catch (JedisConnectionException | JedisExhaustedPoolException e) {
                lastException = e;
                System.err.printf("Jedis attempt %d/%d failed: %s%n",
                    attempt, maxRetries, e.getMessage());

                if (attempt < maxRetries) {
                    try {
                        Thread.sleep((long) Math.pow(2, attempt) * 100L);
                    } catch (InterruptedException ie) {
                        Thread.currentThread().interrupt();
                        throw new RuntimeException("Interrupted during retry", ie);
                    }
                }
            } catch (Exception e) {
                throw new RuntimeException("Non-retryable error", e);
            }
        }

        throw new RuntimeException(
            "Operation failed after " + maxRetries + " attempts", lastException
        );
    }

    @FunctionalInterface
    public interface ThrowingSupplier<T> {
        T get() throws Exception;
    }

    public static void main(String[] args) {
        JedisPool pool = createJedisPool("localhost", 6379, null);

        String value = withRetry(() -> {
            try (Jedis jedis = pool.getResource()) {
                return jedis.get("mykey");
            }
        }, 3);

        System.out.println("Value: " + value);
        pool.close();
    }
}
```

---

## References

- [Redis Documentation — INFO](https://redis.io/commands/info/)
- [Redis Eviction Policies](https://redis.io/docs/reference/eviction/)
- [Redis SLOWLOG](https://redis.io/commands/slowlog/)
- [Redis Cluster Specification](https://redis.io/docs/reference/cluster-spec/)
- [Redis Python Client](https://redis-py.readthedocs.io/)
- [Jedis Documentation](https://github.com/redis/jedis)
- [Lettuce Documentation](https://lettuce.io/core/release/reference/)
