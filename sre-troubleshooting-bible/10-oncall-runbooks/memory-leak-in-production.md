# Memory Leak in Production Runbook

> **Category:** On-Call | Memory | Performance
> **Difficulty:** Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#runbook` `#memory` `#leak` `#oncall`

---

## 1. DETECT

Alert fires when memory usage monotonically increases over hours/days without corresponding traffic increase.

**Typical alert thresholds:**

| Environment | Alert at | Critical at |
|-------------|---------|-------------|
| Container (1GB limit) | 80% (800MB) | 90% (900MB) |
| Container (4GB limit) | 75% (3GB) | 85% (3.4GB) |
| VM (16GB) | 80% | 90% |

**Symptoms beyond monitoring:**
- Steady rise in RSS without traffic increase
- OOMKilled containers in Kubernetes
- Application becomes progressively slower (GC pressure)
- `dmesg` shows OOM killer events

---

## 2. CONFIRM — Is It Really a Leak?

### 2a. Current State

```bash
# Top memory consumers:
ps aux --sort=-%mem | head -10

# Focus on the app process:
ps aux | grep -E "java|node|python" | grep -v grep

# Process-level detail:
cat /proc/$(pgrep -f java)/status | grep -E "VmRSS|VmSize|VmPeak|Threads"
# VmPeak >> VmRSS and VmRSS growing = leak
# Steady VmSize but growing VmRSS = heap within JVM expanding
# VmSize growing unbounded = native memory leak (dangerous)
```

### 2b. Rate of Growth

```bash
# Watch memory every 10 seconds for 1 minute:
for i in $(seq 1 6); do
  ps -p $(pgrep -f java) -o rss,vsz --no-headers
  sleep 10
done

# Calculate growth rate (if available, use monitoring data):
# Prometheus: rate(container_memory_working_set_bytes{pod="app"}[1h])
```

### 2c. Distinguish Leak vs Normal Usage

| Observation | Leak? | Normal? |
|-------------|-------|---------|
| Memory grows with traffic, releases after | No | Normal cache / request processing |
| Memory grows during traffic, doesn't release | **Yes** | Objects retained unnecessarily |
| Memory grows even at idle (no traffic) | **Yes** | Background process or memory leak |
| Sawtooth pattern (grows, drops sharply) | No | Normal GC cycle |
| Steady upward slope (never drops) | **Yes** | Leak in progress |

---

## 3. IMMEDIATE MITIGATION — Stabilize the System

### 3a. Rolling Restart (Standard Approach)

```bash
# Kubernetes rolling restart — one pod at a time:
kubectl rollout restart deployment/app -n prod
kubectl rollout status deployment/app -n prod

# Verify new pods healthy, old pods terminated:
kubectl get pods -l app=app -n prod

# Traditional deploy / systemd (rolling across instances):
# Instance 1:
systemctl restart app
sleep 30  # let it warm up
# Instance 2:
ssh instance-2 "sudo systemctl restart app"
sleep 30
# ... repeat for all instances
# NEVER restart all instances simultaneously — you lose all capacity.
```

### 3b. If OOMKilled — Increase Memory Limit Temporarily

```bash
# Kubernetes — bump memory limit (buy time to investigate):
kubectl set resources deployment/app -n prod \
  --limits=memory=4Gi \
  --requests=memory=2Gi

# Verify new pods get the updated limits:
kubectl describe pod <POD> -n prod | grep -A2 "Limits"
```

### 3c. Language-Specific Emergency Flags

```bash
# Java — cap heap to prevent system OOM:
# Add these JVM args to deployment config:
# -XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath=/dumps/
# -XX:MaxRAMPercentage=75.0  (already present? check)

# Kubernetes:
kubectl set env deployment/app -n prod \
  JAVA_TOOL_OPTIONS="-XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath=/dumps/"

# Node.js — cap the heap:
# NODE_OPTIONS="--max-old-space-size=2048"
kubectl set env deployment/app -n prod \
  NODE_OPTIONS="--max-old-space-size=2048"
```

---

## 4. CAPTURE EVIDENCE (Before Restart — If Time Allows)

> **Important:** If OOM is imminent (<5 min), skip to restart. Losing evidence is acceptable vs an outage.

### 4a. Java — Heap Dump

```bash
# Option 1: jmap (pauses JVM for duration of dump):
PID=$(pgrep -f java)
jmap -dump:live,format=b,file=/tmp/heap-$(date +%s).hprof $PID
# NOTE: "live" flag triggers a Full GC before dump. For large heaps, this can take minutes.

# Option 2: jcmd (less disruptive):
jcmd $PID GC.heap_dump /tmp/heap-$(date +%s).hprof

# Option 3: If JVM has HeapDumpOnOutOfMemoryError set:
# Copy the auto-generated dump from /dumps/ or /tmp/
```

### 4b. Java — Thread Dump (Also Useful)

```bash
# Thread dump — may reveal what's holding memory:
jstack $PID > /tmp/threads-$(date +%s).txt
jcmd $PID Thread.print > /tmp/threads-$(date +%s).txt

# Take 3 dumps 5 seconds apart (to see which threads are stuck):
for i in 1 2 3; do jstack $PID > /tmp/threads-${i}.txt; sleep 5; done
```

### 4c. Node.js — Heap Snapshot

```bash
# Option 1: heapdump npm package (if installed):
kill -USR2 $(pgrep -f "node.*app")
# Generates: heapdump-<timestamp>.heapsnapshot

# Option 2: Chrome DevTools inspector:
# ssh -L 9229:localhost:9229 instance-1
# Then open chrome://inspect in Chrome
# Take heap snapshot from the Memory tab

# Option 3: process._getActiveHandles() if heapdump not available:
node -e "
const net = require('net');
const sock = net.createConnection('/var/run/app.sock');
sock.write(JSON.stringify({cmd:'heapdump'}));
"
```

### 4d. Python — Memory Snapshot

```python
# tracemalloc (Python 3.4+):
import tracemalloc
tracemalloc.start()

# ... let it run ...

snapshot = tracemalloc.take_snapshot()
top_stats = snapshot.statistics('lineno')
for stat in top_stats[:10]:
    print(stat)

# Or use objgraph:
import objgraph
objgraph.show_most_common_types(limit=20)
```

### 4e. Process Memory Map (All Languages)

```bash
# Save /proc/PID/smaps — detailed memory regions:
cat /proc/$PID/smaps > /tmp/smaps-$(date +%s).txt

# pmap — summary of memory mappings:
pmap -x $PID > /tmp/pmap-$(date +%s).txt
```

### 4f. Copy to Safe Location

```bash
# Copy dump off the instance before restarting:
aws s3 cp /tmp/heap-*.hprof s3://dumps-bucket/heap-dumps/
# Or:
scp /tmp/heap-*.hprof bastion-host:/analysis/
```

---

## 5. POST-MITIGATION ANALYSIS

### 5a. Java — Eclipse MAT / VisualVM

```bash
# Open heap.hprof in Eclipse Memory Analyzer Tool (MAT):
# 1. File → Open Heap Dump
# 2. Click "Leak Suspects" report
# 3. Look at "Biggest Objects" by retained heap

# Command-line alternative (if MAT not available):
# jhat (deprecated but still in JDK):
jhat -J-Xmx4g /path/to/heap.hprof
# Then open http://localhost:7000

# Key things to check in MAT:
# - "Problem Suspect 1" / "Problem Suspect 2" — suspected leak holders
# - "Accumulated Objects" in dominator tree
# - HashMap$Node / ConcurrentHashMap$Node with huge counts
# - Byte[] / char[] with massive retained size
```

### 5b. Java — Common Leak Patterns

| Root Cause | Signature in Heap Dump | Fix |
|------------|----------------------|------|
| ThreadLocal not cleaned in thread pool | ThreadLocalMap holding large objects | Call `remove()` after use, use try-finally |
| Static Map / List growing unbounded | HashMap$Node with millions of entries | Add eviction policy (LRU cache) or cleanup |
| Unclosed streams/connections | Leaked sockets, file descriptors | Use try-with-resources |
| Event listeners never deregistered | Listener lists in GUI/web framework objects | Always deregister on destroy/close |
| ClassLoader leak | Multiple instances of same class loaded | Fix hot-redeploy, check framework |
| SoftReference / WeakReference memory not reclaimed | SoftReference entries in ReferenceQueue | Tune -XX:SoftRefLRUPolicyMSPerMB |

### 5c. Node.js — Analyze Heap Snapshot

```
1. Open Chrome DevTools → Memory tab
2. Load .heapsnapshot file
3. Sort by "Retained Size" (descending)
4. Look for:
   - Objects at (string) with large Shallow Size → string concatenation leak
   - Array or Object with growing Shallow/Retained Size
   - (closure) objects holding unexpected references
   - Timer / Interval that was never cleared
```

### 5d. Python — Analyze tracemalloc

```python
# Compare two snapshots to find growth:
snapshot1 = ...  # earlier
snapshot2 = ...  # later

top_diff = snapshot2.compare_to(snapshot1, 'lineno')
for stat in top_diff[:10]:
    print(stat)
    print(stat.traceback.format())
```

---

## 6. COMMON ROOT CAUSES (Per Language)

| Language | Common Cause | Detection |
|----------|-------------|-----------|
| **Java** | ThreadLocal values in thread pool | Heap dump → ThreadLocalMap objects |
| **Java** | Static HashMap growing unbounded | Heap dump → HashMap$Node dominate retained size |
| **Java** | JDBC connections/resultsets not closed | Profiler / leak detector |
| **Java** | Log4j appender accumulating events | Heap dump → log events in memory |
| **Node.js** | Closures retaining scope accidentally | Memory tab → (closure) with unexpected references |
| **Node.js** | Event listeners on global objects | `process._getActiveHandles().length` growing |
| **Node.js** | Buffer / stream backpressure not handled | Large Buffer objects, streams not piped |
| **Python** | Global list/dict accumulating | tracemalloc → __main__ module allocations |
| **Python** | Reference cycles (if no GC) | `gc.garbage` list, circular references |
| **Go** | Goroutine leak | pprof goroutine profile → count growing |
| **Go** | Sliced arrays retaining underlying memory | pprof heap profile → large allocations |

---

## 7. PERMANENT FIX

- Fix the code based on analysis
- Add memory limit to containers (prevent OOM killer)
- Add memory monitoring with trend-based alerting (not just threshold)
- Consider periodic scheduled restarts as a short-term safety valve
- Add heap dump on OOM kill `-XX:+HeapDumpOnOutOfMemoryError`
- Add integration test for memory (run load, check heap doesn't grow)

---

## 8. VERIFY

```bash
# Check memory after fix:
kubectl top pods -n prod | grep app

# Watch for 30 minutes — no upward trend:
watch -n 60 "kubectl top pods -n prod | grep app"

# Check no OOM events in last hour:
kubectl get events -n prod | grep -i OOM

# Prometheus query (memory growth rate):
# deriv(container_memory_working_set_bytes{pod=~"app.*"}[1h])
# Should be close to 0 for a fixed leak.
```

---

## ABORT CRITERIA

| Condition | Action | Timebox |
|-----------|--------|---------|
| Memory growing >100 MB/hour and OOM risk within 30 min | **Rolling restart immediately.** Do not wait for heap dump. | Act immediately |
| OOMKill has already occurred and pods are restart-looping | Increase memory limit, then restart | 5 min |
| Heap dump causes service degradation during capture | Abort dump, do rolling restart instead | Immediately |
| Can't identify root cause after 2 hours of analysis | Escalate to performance engineering team | 2 hours |
| Memory leak causing customer-impacting errors | Prioritize restart + limit increase over root cause analysis | Immediately |
