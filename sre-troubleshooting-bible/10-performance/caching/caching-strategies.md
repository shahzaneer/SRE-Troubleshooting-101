# Caching Strategies
> **Category:** Performance | Caching
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#performance` `#caching` `#redis` `#oncall`

---

## The Two Hard Problems

```
There are only two hard things in Computer Science:
  1. Cache invalidation
  2. Naming things
  3. Off-by-one errors
```

This joke exists because cache invalidation is genuinely the hardest problem in distributed systems. When do you know cached data is stale? Who invalidates it? What if the invalidation message is lost?

---

## Cache Patterns

### Cache-Aside (Lazy Loading — Most Common)

The application is responsible for managing the cache. The cache is NOT the source of truth — it's a best-effort copy.

```
App reads key "user:12345":
  1. Check Redis: GET user:12345
     → HIT: return cached data (50ms)
     → MISS: query DB (500ms)
  2. Populate cache: SET user:12345 <data> EX 300 (5 min TTL)
  3. Return data
```

**Pros**: Cache contains only what's actually accessed. Cache failure = slower, not broken.
**Cons**: First request is always slow (cold start). Stale data if TTL too long.

### Write-Through

Every write goes to cache AND DB simultaneously.

```
App writes "user:12345":
  1. Write to cache: SET user:12345 <new_data>
  2. Write to DB: UPDATE users SET ... WHERE id=12345
  3. Return success

App reads "user:12345":
  1. Read from cache: GET user:12345 → always fresh
```

**Pros**: Cache is always consistent with DB. Read latency always low.
**Cons**: Slower writes (2 synchronous writes). Cache holds data that might never be read.

### Write-Behind (Write-Back)

Write to cache first. DB write happens asynchronously later.

```
App writes "user:12345":
  1. Write to cache: SET user:12345 <new_data>
  2. Return success immediately (fastest writes)
  3. Background: async worker flushes to DB

Risk: Cache crashes before flush → data loss.
Mitigation: Redis persistence (AOF) + replication.
```

### Read-Through

Cache sits between app and DB. App never talks to DB directly — always through cache layer.

```
App reads "user:12345":
  1. Cache library: GET user:12345
     → HIT: return data
     → MISS: cache library queries DB, populates cache, returns data

App never writes SQL queries. CachePlugin handles all DB interaction.
```

---

## Cache Stampede (Thundering Herd)

The single most common cache-related production incident.

### What Happens

```
Timeline:
  10:14:00 — Homepage data (key: homepage:data) cached. TTL = 5 min. Expires at 10:19:00.
  10:19:00 — Cache key expires. Cache now EMPTY for this key.
  10:19:00.001 — Request 1: MISS → queries DB (takes 2s)
  10:19:00.010 — Request 2: MISS → queries DB (same data, takes 2s)
  10:19:00.015 — Request 3: MISS → queries DB
  ...
  10:19:01.000 — Request 350: MISS → queries DB

  350 concurrent requests ALL querying the DB for the same data.
  DB CPU spikes from 20% to 100%.
  DB connection pool saturated (20 connections / 350 requests = 17.5x over capacity).
  All 350 requests timeout.
  Service starts returning 500 errors.

  10:19:02.000 — Request 1 completes, populates cache.
  BUT: DB is already saturated → requests 2-350 continue timing out.
  It takes 30 seconds for DB to drain the backlog.

Root cause: A popular cache key expired, and EVERY request tried to recompute
           simultaneously instead of coordinating.
```

### Solutions

**Solution 1: Probabilistic Early Recompute (Recommended)**

```python
import time
import random

def get_with_stampede_protection(cache_key, db_fetch_fn, ttl_seconds=300):
    """Fetch from cache, with probabilistic early recompute to prevent stampede."""
    value, expiry = cache.get_with_expiry(cache_key)

    now = time.time()
    if value is not None:
        remaining_ttl = expiry - now
        # As TTL approaches 0, probability of early recompute increases
        # At 50% TTL remaining: 0% chance
        # At 0% TTL remaining: 100% chance (normal behavior — key expired)
        if remaining_ttl > 0:
            early_recompute_chance = max(0, 1 - (remaining_ttl / ttl_seconds))
            if random.random() < early_recompute_chance * 0.3:  # Max 30% chance
                # Recompute in background, return stale data immediately
                thread_pool.submit(_recompute_and_cache, cache_key, db_fetch_fn, ttl_seconds)
            return value

    # Actual miss (key expired or never existed)
    data = db_fetch_fn()
    cache.set(cache_key, data, ttl=ttl_seconds)
    return data

def _recompute_and_cache(cache_key, db_fetch_fn, ttl_seconds):
    data = db_fetch_fn()
    cache.set(cache_key, data, ttl=ttl_seconds)
```

**Solution 2: Mutex Lock (First Request Recomputes, Others Wait)**

```python
import threading
import hashlib

_locks = {}
_locks_lock = threading.Lock()

def get_with_lock(cache_key, db_fetch_fn, ttl_seconds=300):
    value = cache.get(cache_key)
    if value is not None:
        return value

    # Use a per-key lock to serialize recomputation
    with _locks_lock:
        if cache_key not in _locks:
            _locks[cache_key] = threading.Lock()

    lock = _locks[cache_key]
    with lock:  # Only one request enters here per key
        # Double-check: another request might have populated while we waited
        value = cache.get(cache_key)
        if value is not None:
            return value

        data = db_fetch_fn()
        cache.set(cache_key, data, ttl=ttl_seconds)
        return data
```

**Solution 3: Distributed Lock (Redis SETNX)**

```python
def get_with_distributed_lock(cache_key, db_fetch_fn, ttl_seconds=300):
    value = redis.get(cache_key)
    if value is not None:
        return value

    lock_key = f"lock:{cache_key}"
    # Try to acquire lock with 10s TTL (prevents deadlock if process dies)
    if redis.set(lock_key, "1", nx=True, ex=10):
        try:
            # Double-check cache
            value = redis.get(cache_key)
            if value is not None:
                return value

            data = db_fetch_fn()
            redis.setex(cache_key, ttl_seconds, data)
            return data
        finally:
            redis.delete(lock_key)
    else:
        # Another process is recomputing. Poll until cache is populated.
        for _ in range(50):  # Wait up to 5 seconds
            time.sleep(0.1)
            value = redis.get(cache_key)
            if value is not None:
                return value
        # Timeout — fall through to DB as last resort
        data = db_fetch_fn()
        redis.setex(cache_key, ttl_seconds, data)
        return data
```

---

## Cache Invalidation Strategies

### TTL-Based (Simplest — "Eventually Consistent")

```
SET user:12345 <data> EX 300  # Expires in 5 minutes, period.

Trade-off: shorter TTL = fresher data but more DB queries.
           longer TTL = better performance but stale data risk.
```

**TTL Selection Framework**:
```
Data Type           | TTL Recommendation
--------------------|---------------------
User profile        | 5-15 minutes
Product catalog     | 15-60 minutes
Session data        | 30 minutes
Configuration       | 1-5 minutes (or event-driven)
Static content      | 1-24 hours
Leaderboard         | 30-60 seconds
```

### Event-Driven Invalidation (Most Accurate)

```python
# When user updates their profile:
def update_user(user_id, new_data):
    db.update_user(user_id, new_data)
    # Invalidate cache
    redis.delete(f"user:{user_id}")
    # Optional: publish invalidation event so OTHER services also invalidate
    redis.publish("cache-invalidation", f"user:{user_id}")

# Other services subscribe:
def on_invalidation_message(message):
    key = message['data'].decode()
    redis.delete(key)
```

### Cache-Key Versioning

```python
# Globally versioned keys — invalidate ALL versions of a key pattern at once
VERSION = redis.get("cache:schema_version") or 1

def get_cached(key):
    versioned_key = f"v{VERSION}:{key}"
    value = redis.get(versioned_key)
    if value is not None:
        return value
    data = db.fetch(key)
    redis.setex(versioned_key, 300, data)
    return data

# Deploy a schema change that invalidates all caches:
redis.incr("cache:schema_version")  # Now all reads go to v2:key (empty = miss)
```

---

## Negative Caching — Cache the Absence of Data

Why this matters: A DDoS attacker requests `user:999999` (nonexistent). Every request hits the DB. With negative caching, "NOT_FOUND" is cached.

```python
NOT_FOUND = object()  # Sentinel for "doesn't exist"

def get_user(user_id):
    cache_key = f"user:{user_id}"
    cached = redis.get(cache_key)

    if cached is not None:
        if cached == b"__NOT_FOUND__":
            return None  # Cached negative — no DB query
        return json.loads(cached)

    user = db.query("SELECT * FROM users WHERE id = ?", user_id)

    if user is None:
        # Cache the absence with SHORT ttl (30s)
        redis.setex(cache_key, 30, "__NOT_FOUND__")
        return None

    redis.setex(cache_key, 300, json.dumps(user))
    return user
```

---

## CDN Caching — HTTP Headers

CDNs (CloudFront, Cloudflare, Fastly) obey standard HTTP cache headers.

### Cache-Control Directives

```
# Static assets — cache aggressively
Cache-Control: public, max-age=31536000, immutable
# "This file never changes. If the URL changes, it's a different file."
# Works with content-hashed filenames: main.a1b2c3d4.js

# API responses — short cache, revalidate after
Cache-Control: public, s-maxage=60, max-age=0
# CDN caches for 60s (s-maxage). Browsers always revalidate (max-age=0).

# User-specific data — never cache
Cache-Control: private, no-cache
# "This response is unique to this user. Do not cache anywhere."

# Stale-while-revalidate — serve stale, refresh in background
Cache-Control: max-age=3600, stale-while-revalidate=600
# "Cache for 1 hour. If expired, serve stale for up to 10 min while revalidating."
```

### CDN Purging

```bash
# CloudFront invalidation
aws cloudfront create-invalidation \
  --distribution-id E1234567890ABC \
  --paths "/images/*" "/api/v2/*"

# Cloudflare purge
curl -X POST "https://api.cloudflare.com/client/v4/zones/<ZONE_ID>/purge_cache" \
  -H "Authorization: Bearer <API_TOKEN>" \
  -H "Content-Type: application/json" \
  --data '{"files":["https://example.com/api/v2/config"]}'

# Fastly purge
curl -X PURGE https://api.example.com/specific-resource
# Or purge by surrogate key (tag)
curl -X POST -H "Fastly-Key: <KEY>" \
  -H "Surrogate-Key: api-v2 users" \
  https://api.fastly.com/service/<SERVICE_ID>/purge
```

---

## Multi-Level Caching

Production applications often use LOCAL + DISTRIBUTED caching.

```
Request for user:12345:
  1. L1: In-memory cache (Caffeine / node-cache) — <1ms
     HIT → return (0.001ms)
     MISS → check L2
  2. L2: Distributed cache (Redis / Memcached) — <2ms
     HIT → populate L1, return (2ms)
     MISS → query DB
  3. L3: Database — 50-500ms
     → populate L2, then L1, return

Result: 95% hit in L1 (ultra-fast). 4% hit in L2 (fast).
        1% hit in L3 (slow) — unavoidable for new/uncommon data.
        Weighted average: 0.95×0.001 + 0.04×2 + 0.01×200 = 2.08ms (vs 200ms with no cache)
```

---

## Language-Specific Implementations

### Python: Caching Decorator with Stampede Prevention

```python
import redis
import json
import hashlib
import random
import time
import functools
import threading
from typing import Any, Callable, Optional

redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)

class CacheConfig:
    def __init__(self, ttl: int = 300, negative_ttl: int = 30,
                 stampede_protection: bool = True, prefix: str = "app"):
        self.ttl = ttl
        self.negative_ttl = negative_ttl
        self.stampede_protection = stampede_protection
        self.prefix = prefix

def cached(config: CacheConfig):
    """Decorator that adds cache-aside behavior with stampede protection."""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Generate cache key from function name + arguments
            key_parts = [config.prefix, func.__name__]
            key_parts.extend(str(a) for a in args)
            key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
            cache_key = hashlib.sha256(":".join(key_parts).encode()).hexdigest()[:32]

            # Try cache
            cached_data = redis_client.get(cache_key)

            if cached_data is not None:
                # Check for negative cache
                if cached_data == "__NOT_FOUND__":
                    return None

                # Stampede protection: probabilistic early recompute
                if config.stampede_protection:
                    ttl = redis_client.ttl(cache_key)
                    if ttl > 0 and ttl < config.ttl * 0.3:  # Last 30% of TTL
                        if random.random() < 0.1:  # 10% chance
                            thread = threading.Thread(
                                target=_refresh_cache,
                                args=(func, cache_key, config, args, kwargs),
                                daemon=True
                            )
                            thread.start()

                return json.loads(cached_data)

            # Cache miss — fetch from source
            result = func(*args, **kwargs)

            if result is None:
                redis_client.setex(cache_key, config.negative_ttl, "__NOT_FOUND__")
            else:
                redis_client.setex(cache_key, config.ttl, json.dumps(result))

            return result

        return wrapper
    return decorator

def _refresh_cache(func, cache_key, config, args, kwargs):
    """Background refresh to prevent stampede."""
    try:
        result = func(*args, **kwargs)
        if result is not None:
            redis_client.setex(cache_key, config.ttl, json.dumps(result))
    except Exception as e:
        # Log but don't crash the background thread
        import logging
        logging.getLogger(__name__).warning(f"Background cache refresh failed: {e}")

# --- Usage ---

user_cache_config = CacheConfig(ttl=300, prefix="users")

@cached(user_cache_config)
def get_user(user_id: int) -> Optional[dict]:
    """Fetch user from DB. Cached automatically."""
    # Simulate slow DB query
    time.sleep(0.2)
    if user_id > 1000000:
        return None  # Will be negatively cached
    return {"id": user_id, "name": f"User_{user_id}", "email": f"user{user_id}@example.com"}
```

### Java: Spring Cache with Caffeine + Redis (Multi-Level)

```java
// build.gradle dependencies:
// - org.springframework.boot:spring-boot-starter-cache
// - com.github.ben-manes.caffeine:caffeine:3.1.0
// - org.springframework.boot:spring-boot-starter-data-redis

import org.springframework.cache.CacheManager;
import org.springframework.cache.annotation.Cacheable;
import org.springframework.cache.annotation.CacheEvict;
import org.springframework.cache.annotation.CachePut;
import org.springframework.cache.caffeine.CaffeineCacheManager;
import org.springframework.cache.support.CompositeCacheManager;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.redis.cache.RedisCacheConfiguration;
import org.springframework.data.redis.cache.RedisCacheManager;
import org.springframework.data.redis.connection.RedisConnectionFactory;

import java.time.Duration;
import java.util.Arrays;
import java.util.concurrent.TimeUnit;

// --- Cache Configuration ---
@Configuration
public class MultiLevelCacheConfig {

    @Bean
    public CacheManager cacheManager(RedisConnectionFactory redisConnectionFactory) {
        // L1: Caffeine (local, ultra-fast, bounded size)
        CaffeineCacheManager l1Cache = new CaffeineCacheManager();
        l1Cache.setCaffeine(
            com.github.benmanes.caffeine.cache.Caffeine.newBuilder()
                .maximumSize(10_000)           // Max 10K entries
                .expireAfterWrite(5, TimeUnit.MINUTES)  // 5 min TTL
                .recordStats()                 // Expose cache stats for monitoring
        );

        // L2: Redis (distributed, all instances share)
        RedisCacheManager l2Cache = RedisCacheManager.builder(redisConnectionFactory)
            .cacheDefaults(
                RedisCacheConfiguration.defaultCacheConfig()
                    .entryTtl(Duration.ofMinutes(15))     // 15 min TTL
                    .disableCachingNullValues()            // Don't cache nulls
            )
            .build();

        // Composite: check L1 first, then L2, then DB
        CompositeCacheManager composite = new CompositeCacheManager(l1Cache, l2Cache);
        composite.setFallbackToNoOpCache(false);
        return composite;
    }
}

// --- Service Usage ---
@Service
public class UserService {

    // Cache read: check cache first, query DB on miss
    @Cacheable(value = "users", key = "#userId",
               unless = "#result == null")  // Don't cache null results
    public User getUser(Long userId) {
        // Simulate slow DB query
        simulateDelay(200);
        return userRepository.findById(userId).orElse(null);
    }

    // Cache evict: remove from cache on update
    @CacheEvict(value = "users", key = "#user.id")
    public void updateUser(User user) {
        userRepository.save(user);
    }

    // Cache put: update cache explicitly
    @CachePut(value = "users", key = "#result.id")
    public User createUser(User user) {
        return userRepository.save(user);
    }

    private void simulateDelay(long ms) {
        try { Thread.sleep(ms); }
        catch (InterruptedException e) { Thread.currentThread().interrupt(); }
    }
}
```

### JavaScript: node-cache (L1) + ioredis (L2)

```javascript
// npm install node-cache ioredis

const NodeCache = require('node-cache');
const Redis = require('ioredis');

const L1_CACHE = new NodeCache({
    stdTTL: 300,           // 5 minutes
    checkperiod: 60,       // Check for expired keys every 60s
    maxKeys: 10000,        // Max entries
    useClones: false,      // Faster: return reference, don't clone
});

const redis = new Redis({
    host: 'localhost',
    port: 6379,
    enableOfflineQueue: false,  // Fail fast if Redis is down
    maxRetriesPerRequest: 1,
});

class MultiLevelCache {
    constructor(l1 = L1_CACHE, l2 = redis) {
        this.l1 = l1;
        this.l2 = l2;
    }

    async get(key, fetchFn, ttl = 300) {
        // Check L1 (in-memory)
        const l1Value = this.l1.get(key);
        if (l1Value !== undefined) {
            return l1Value === '__NOT_FOUND__' ? null : l1Value;
        }

        // Check L2 (Redis)
        try {
            const l2Value = await this.l2.get(key);
            if (l2Value !== null) {
                const parsed = JSON.parse(l2Value);
                this.l1.set(key, parsed, ttl);  // Populate L1
                return parsed === '__NOT_FOUND__' ? null : parsed;
            }
        } catch (err) {
            // Redis is down — continue to DB (degraded mode)
            console.warn(`Redis unavailable: ${err.message}`);
        }

        // Both caches missed — fetch from DB
        const data = await fetchFn();

        // Populate both caches
        if (data === null || data === undefined) {
            // Negative cache
            this.l1.set(key, '__NOT_FOUND__', 30);
            try {
                await this.l2.setex(key, 30, JSON.stringify('__NOT_FOUND__'));
            } catch (_) { /* Redis down — skip L2 */ }
        } else {
            this.l1.set(key, data, ttl);
            try {
                await this.l2.setex(key, ttl, JSON.stringify(data));
            } catch (_) { /* Redis down — skip L2 */ }
        }

        return data;
    }

    async del(key) {
        this.l1.del(key);
        try {
            await this.l2.del(key);
        } catch (_) { /* Redis down — skip L2 */ }
    }

    async flushPattern(pattern) {
        // Flush all keys matching pattern from L2 (Redis)
        // (L1 only flushes explicit keys)
        try {
            const keys = await this.l2.keys(pattern);
            if (keys.length > 0) {
                await this.l2.del(...keys);
            }
        } catch (_) { /* Redis down */ }
    }
}

// --- Usage ---
const cache = new MultiLevelCache();

async function getUser(userId) {
    return cache.get(
        `user:${userId}`,
        async () => {
            // This only runs on cache miss
            console.log(`Cache miss for user:${userId}, querying DB`);
            const user = await db.query('SELECT * FROM users WHERE id = ?', [userId]);
            return user || null;  // null triggers negative caching
        },
        300  // 5 minute TTL
    );
}

// Update user → invalidate both caches
async function updateUser(userId, data) {
    await db.updateUser(userId, data);
    await cache.del(`user:${userId}`);
}
```

---

## Common Pitfalls

1. **Stale data from long TTLs**: User updates profile, but cache still shows old data for 15 minutes. Impressions: "The app is broken." Fix: event-driven invalidation or shorter TTLs.
2. **Stampede from popular key expiry**: Redis key for homepage expires. 1000 concurrent requests hit DB. Fix: probabilistic early recompute, or mutex lock.
3. **Cache pollution**: Caching every single DB query regardless of access frequency. Low-use data evicts high-use data from LRU. Fix: only cache data accessed >10 times/second, or use TTL + max-size.
4. **No circuit breaker on cache**: Redis goes down, app falls through to DB on every request. DB gets 100x traffic and also dies. Fix: circuit breaker. If Redis down for >5s, serve errors (or stale local cache) instead of hammering DB.
5. **Caching PII without encryption**: User's email/phone cached in plaintext in Redis. Redis compromise = data breach. Fix: encrypt sensitive fields before caching, or don't cache PII at all.

---

*See also: [Application Profiling](../profiling/application-profiling.md) | [Load Testing Guide](../load-testing/load-testing-guide.md) | [Bottleneck Analysis](../bottleneck-analysis/bottleneck-guide.md)*
