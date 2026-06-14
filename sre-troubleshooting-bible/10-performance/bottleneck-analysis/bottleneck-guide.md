# Bottleneck Analysis
> **Category:** Performance | Bottlenecks
> **Difficulty:** Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#performance` `#bottleneck` `#queuing-theory` `#oncall`

---

## Amdahl's Law — The Hard Limit of Parallelization

> The speedup gained from parallelizing a system is limited by the fraction that remains sequential.

```
Speedup = 1 / (S + (1-S)/N)

Where: S = fraction of code that is sequential (cannot be parallelized)
       N = number of parallel workers / CPUs

If 20% of code is sequential (S=0.2), maximum speedup with infinite CPUs:
  Speedup = 1 / (0.2 + 0.8/∞) = 1 / 0.2 = 5x
```

### Implications for Scaling

```
Sequential %  | Max Speedup (∞ CPUs) | Effective CPU Limit
--------------|----------------------|---------------------
5%            | 20x                   | 32 CPUs
10%           | 10x                   | 16 CPUs
20%           | 5x                    | 8 CPUs
50%           | 2x                    | 2 CPUs
80%           | 1.25x                 | Don't bother parallelizing
```

### Real Scenario: The 100-Instance Mirage

```
Symptom: Team migrates from 1 instance to 100 instances.
Expected: 100x throughput increase.
Actual: 4x throughput increase. Each instance idle at 4% CPU.

Investigation with tracing:
  100 instances all spend 95% of request time waiting for
  `synchronized refreshCache()` — a single-threaded operation
  that locks on a shared Redis key.

Amdahl's Law analysis:
  25% of code is sequential (cache refresh is synchronized).
  Max speedup = 1 / 0.25 = 4x.

  Adding 100 instances was expected to give 100x.
  Reality: 4x. The other 96 instances are adding ZERO value.

Fix:
  1. Remove synchronized keyword. Use optimistic locking.
  2. Replace global cache refresh with per-instance lazy refresh.
  3. Or: use a distributed cache (Redis) instead of in-memory synchronized.

Result: 12x actual speedup (better but still limited by DB).
```

---

## Queueing Theory — Little's Law

The most useful formula in capacity planning:

```
L = λ × W

L = Average number of in-flight requests (queue length)
λ = Average arrival rate (requests per second)
W = Average time each request spends in the system (latency)
```

### Applied to SRE

```
Example: λ = 500 req/s, W = 0.1s (100ms)
L = 500 × 0.1 = 50 requests in-flight at any moment

If your thread pool has 60 threads → OK (50 < 60, room for spikes)
If your thread pool has 40 threads → PROBLEM (50 > 40, queue is growing)
```

**Little's Law doesn't care about distribution.** It works regardless of arrival patterns, latency distributions, or queue discipline. This makes it the most reliable capacity check you can do.

### Using Little's Law to Predict Failures

```
Given:
  max_concurrent_connections = 200 (Tomcat thread pool)
  current_latency_p50 = 50ms = 0.05s

Question: At what request rate will the thread pool saturate?

Rearrange: λ = L / W
λ_max = 200 / 0.05 = 4,000 requests/second

But wait — latency increases with load (utilization cliff).
If p50 becomes 100ms at 3000 RPS: λ_max = 200 / 0.10 = 2,000 RPS
If p50 becomes 500ms at 4000 RPS: λ_max = 200 / 0.50 = 400 RPS ← COLLAPSE

This is the "utilization cliff" — with fixed capacity, as latency increases,
your maximum throughput actually DECREASES. A system under load degrades to
lower throughput than it had at medium load.
```

---

## The Utilization Cliff

The relationship between utilization and latency is NOT linear. It's exponential.

```
Utilization | Queueing Delay Multiplier | Latency (p50=10ms baseline)
------------|---------------------------|----------------------------
0-50%       | ~1x                       | 10ms
50-70%      | ~1-2x                     | 10-20ms
70-80%      | ~2-3x                     | 20-30ms
80-85%      | ~3-5x                     | 30-50ms
85-90%      | ~5-10x                    | 50-100ms
90-95%      | ~10-20x                   | 100-200ms
95-98%      | ~20-50x                   | 200-500ms ← EXPONENTIAL ZONE
98-99%      | ~50-100x                  | 500-1000ms ← SYSTEM COLLAPSING
>99%        | ~infinity                 | ∞ (requests queue forever)
```

### Real Scenario: The 85% Trap

```
Background: Service running at 85% CPU at peak (12 PM - 2 PM).
p50 = 75ms, p95 = 200ms. Within SLO (p95 < 250ms). No alerts.

Event: Marketing sends promotional email blast. Traffic +15%.
Expected: CPU 85% → ~98%. Latency should be linear with CPU.
         So p95 should go from 200ms → ~230ms.

Reality: CPU 85% → 98% (as expected).
         BUT p95 went from 200ms → 4,200ms (NOT expected).
         15% more traffic → 21x latency increase!

Why: At 85% CPU, the system is already at the cliff edge.
     The queueing delay at 85% utilization is ~5x baseline.
     At 98% utilization, queueing delay is ~50x baseline.
     Moving from 85% to 98% = 10x increase in queueing delay.

     Original p95 = 200ms = 50ms compute + 150ms queueing
     New p95 = 4000ms = 58ms compute + 3942ms queueing (150 × 26x)

Lesson: Keep utilization below 60% for headroom. The cliff starts at 60-70%,
        not at 95%. At 80% you have already lost significant headroom.
```

---

## Systematic Bottleneck Hunting — The Elimination Method

Test resources in order of likelihood (for web applications):

### 1. External Dependencies (Most Likely)

Web apps spend 80%+ of request time waiting on DB, cache, or external APIs.

```bash
# Check external call latency
curl -w "@timing.txt" -o /dev/null -s https://api.example.com/orders
# timing.txt:
#   time_namelookup:  %{time_namelookup}\n
#   time_connect:     %{time_connect}\n
#   time_starttransfer: %{time_starttransfer}\n
#   time_total:       %{time_total}\n
# If time_total - time_starttransfer > 500ms → server processing is slow

# Trace in Jaeger: sort spans by duration. External call span > 100ms?
# That's likely your bottleneck.
```

### 2. Disk I/O

```bash
# Check disk latency
iostat -x 1 5
# Output:
# Device  r/s   w/s   rkB/s   wkB/s  await  svctm  %util
# sda     50   200    400    5000   45.00   2.50   98.0  ← 45ms await at 98% util!

# await = average time each I/O request takes (queueing + service time)
# await > 10ms → disk is a bottleneck
# %util near 100% → disk is saturated

# Who is doing I/O?
iotop -o  # Shows processes by I/O usage
pidstat -d 1  # I/O stats per process
```

### 3. CPU

```bash
# Check CPU utilization
top -bn1 | head -20
mpstat 1 5     # Per-CPU stats including iowait, steal
# Watch for:
#   %iowait > 10%  → Actually disk-bound, not CPU-bound (CPU waiting for disk)
#   %steal > 5%    → Hypervisor stealing CPU (noisy neighbor on VM)

# If CPU > 80%: profile to find hot functions
perf top -p <PID>   # Live view of hot functions (Linux)
py-spy top -p <PID>  # Python-specific live view
```

### 4. Memory

```bash
# Check memory pressure
free -h
#              total   used    free   shared  buff/cache  available
# Mem:         16G     15.5G   200M   500M    300M         200M   ← CRITICAL
# (available < 2GB → memory pressure)

vmstat 1 10
# si (swap in) and so (swap out) columns: if non-zero → swapping = DEATH
# System will spend >50% of CPU on swap operations.

# Check for OOM kills
dmesg -T | grep -i "out of memory"
journalctl -u your-service --since "10 min ago" | grep -i "killed"
```

### 5. Network

```bash
# Check network throughput
iftop -i eth0   # Bandwidth per connection
nload eth0       # Simple bandwidth monitor

# Check TCP connection state
ss -s
# Output:
# Total: 5000
# TCP:   4800 (estab 4500, closed 50, orphaned 20, timewait 230)
# If orphaned or timewait is large → connection leak or short-lived connections

# Check for TCP retransmissions (indicates packet loss)
ss -ti | grep retrans
# retrans:0/5  ← 5 retransmissions on this connection = packet loss
netstat -s | grep retransmit
# 127490 segments retransmitted ← If growing rapidly: network problem
```

---

## Thread Pool Exhaustion

The #1 cause of application-level bottlenecks in Java services.

### Tomcat (Spring Boot Embedded)

```yaml
# application.yml
server:
  tomcat:
    threads:
      max: 200          # Max worker threads
      min-spare: 10     # Min idle threads kept alive
    accept-count: 100   # Queue size — requests waiting for a thread
    connection-timeout: 20000  # 20s
```

Exhaustion symptoms:
- `java.util.concurrent.RejectedExecutionException` in logs
- 503 Service Unavailable from upstream LB
- p50 latency low, p99 latency HUGE (requests sit in accept queue)

### Scenario: Tomcat Thread Pool Exhaustion

```
Environment: Tomcat max threads = 200. Accept queue = 100.
Traffic: 500 concurrent requests.

What happens:
  200 requests → active processing (thread pool)
  100 requests → accept queue (waiting for a thread)
  200 requests → REJECTED (returned as 503, or LB retries)

Request in processing: 500ms average
Request in accept queue: position 100 × 500ms / 200 threads = 250ms wait
Request rejected: instant 503 (or LB retry → double load)

Effective throughput: 200 / 0.5 = 400 RPS (not 500 concurrent)
Actual max concurrent: 200 threads + 100 queue = 300 before rejection

Monitoring queries:
  # Active threads (should be < max)
  tomcat_threads_busy_threads{name="http-nio-8080"}

  # Thread pool size
  tomcat_threads_config_max_threads{name="http-nio-8080"}

  # If busy/max > 0.8 → thread pool nearing exhaustion
  tomcat_threads_busy_threads / tomcat_threads_config_max_threads > 0.8

Fix options:
  1. Increase threads: server.tomcat.threads.max=400 (if CPU/memory allow)
  2. Async processing: @Async, CompletableFuture, WebFlux
  3. Reduce processing time: optimize DB queries, add caching
  4. Horizontal scaling: add more instances
```

### Netty (WebFlux, gRPC)

```yaml
spring:
  webflux:
    netty:
      max-threads: 200
      max-connections: 10000  # Netty handles more connections than threads
```

Netty is event-loop based — one thread handles many connections. Thread exhaustion is rare but event loop blocking is common. A single blocking call in the event loop stalls ALL connections on that thread.

---

## Connection Pool Saturation

### HikariCP (Java DB Connection Pool)

```yaml
# application.yml
spring:
  datasource:
    hikari:
      maximum-pool-size: 20
      minimum-idle: 5
      connection-timeout: 30000  # 30s to wait for a connection
      idle-timeout: 600000       # 10 min before idle connection removed
      max-lifetime: 1800000      # 30 min max life
```

Monitoring:
```
# Prometheus metrics from Micrometer
hikaricp_connections_active           # Currently in-use connections
hikaricp_connections_idle             # Idle connections in pool
hikaricp_connections_pending          # Threads waiting for a connection ← THIS IS THE KEY
hikaricp_connections_timeout_total    # Total connection timeouts (connection abandoned)
hikaricp_connections_max              # Configured maximum pool size
```

### Scenario: Connection Pool Math

```
Config: HikariCP maximumPoolSize = 20
Load: 100 concurrent requests, each doing 1 DB query averaging 500ms

At any moment:
  Active connections needed = 100 req × 500ms query / 1000ms = 50 connections needed
  But pool has only 20.
  20 requests → processing (DB query running)
  80 requests → waiting for a connection (pending)

Wait time for a connection:
  pool cycle time = 500ms / 20 connections = 25ms per slot
  position 80 in queue × 25ms = 2,000ms (2 seconds) wait just for a CONNECTION
  Plus 500ms for the actual query
  Total = 2,500ms per request

  BUT: 80 requests need to complete before new ones start.
  If each takes 500ms, 80 requests = 40,000ms.
  With 20 parallel: 40,000ms / 20 = 2,000ms.

  Connection timeout = 30,000ms → no timeouts yet, but p99 latency = 2s.

What to check:
  1. Query time: can it be reduced? (add index, optimize query, denormalize)
  2. Pool size: can it be increased? (DB max_connections allows it?)
  3. Connection usage: are connections being held longer than needed?
     (Check for connections held during non-DB work — Java streams, external API calls)
```

---

## Quick Diagnostic Commands Cheat Sheet

```bash
# What's the system bottleneck RIGHT NOW?
# Run these in order. The first one that shows saturation IS the current bottleneck.

# CPU
top -bn1 -o %CPU | head -20          # Top CPU processes
mpstat 1 5                            # Per-CPU utilization + iowait

# Memory
free -h                               # Memory overview
vmstat 1 5                            # si/so columns (swap in/out)

# Disk I/O
iostat -x 1 5                         # await and %util
iotop -o                              # Per-process I/O (requires root)

# Network
ss -s                                 # Socket summary
ss -ti | grep -c retrans              # Count connections with retransmissions
nload eth0                            # Real-time bandwidth

# File descriptors
lsof -p <PID> | wc -l                 # Open file descriptors for process
# Check against ulimit -n (max open FDs)

# Process-specific
cat /proc/<PID>/status | grep -i threads  # Thread count
cat /proc/<PID>/limits                    # All limits for this process
cat /proc/<PID>/io                        # Process I/O statistics
```

---

## The Universal Bottleneck Taxonomy

Bottlenecks don't come randomly — they follow patterns. Here's the taxonomy:

| Type | Symptom | Diagnostic | Fix |
|------|---------|------------|-----|
| **CPU-bound** | High CPU utilization, latency scales with CPU | perf top, flamegraph | Optimize hot path, parallelize, use faster serialization |
| **Memory-bound** | Swapping, OOM kills, GC thrashing | vmstat, jstat, heap dumps | Fix leak, reduce allocations, tune GC |
| **I/O-bound** | High iowait, high await (>10ms) | iostat, iotop | SSD upgrade, batch writes, async I/O |
| **Network-bound** | High retransmissions, saturated bandwidth | ss -ti, iftop | Reduce payload, compress, upgrade network |
| **Lock contention** | Threads blocked on locks, low CPU but high response | async-profiler -e lock | Reduce lock scope, use lock-free data structures, shard |
| **Connection pool** | Pending connections > 0, latency spikes | HikariCP metrics, Tomcat threads | Increase pool, reduce query time, add circuit breaker |
| **External dependency** | Downstream service slow, errors | Tracing, curl timing | Add timeout + retry + circuit breaker + fallback |

---

*See also: [Application Profiling](../profiling/application-profiling.md) | [Load Testing Guide](../load-testing/load-testing-guide.md) | [Caching Strategies](../caching/caching-strategies.md)*
