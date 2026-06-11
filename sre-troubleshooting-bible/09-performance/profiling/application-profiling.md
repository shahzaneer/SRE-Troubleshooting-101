# Application Profiling
> **Category:** Performance | Profiling
> **Difficulty:** Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#performance` `#profiling` `#flamegraphs` `#oncall`

---

## Python Profiling

### cProfile — Deterministic Profiling (Highest Precision)

cProfile instruments every function call. Use it when you need exact function-level timing. Don't use it in production — the overhead is significant (10-50% slowdown).

```bash
# Profile your application
python -m cProfile -o output.prof app.py

# Visualize with snakeviz (pip install snakeviz)
snakeviz output.prof
# Opens browser with interactive sunburst + icicle visualization
# Click on wide segments to drill into hot functions
```

```python
# In-code profiling of specific code paths
import cProfile
import pstats
import io

def profile_block():
    profiler = cProfile.Profile()
    profiler.enable()

    # --- Your code here ---
    result = expensive_computation()
    # ---------------------

    profiler.disable()
    stream = io.StringIO()
    stats = pstats.Stats(profiler, stream=stream).sort_stats(pstats.SortKey.CUMULATIVE)
    stats.print_stats(20)  # Top 20 by cumulative time
    print(stream.getvalue())
    return result
```

### py-spy — Sampling Profiler (Zero Overhead, Production-Safe)

py-spy reads the Python call stack via process memory without modifying the process. **You can attach it to a running production process with zero performance impact.**

```bash
# Install
pip install py-spy

# Live top view (like htop but for Python functions)
py-spy top -p <PID>

# Record sampling profile for 60s, output as flamegraph
py-spy record -o flamegraph.svg -p <PID> --duration 60

# Profile only during a specific time window
py-spy record -o capture.svg -p <PID> --duration 30

# Profile subprocesses too
py-spy record -o profile.svg -p <PID> --subprocesses

# Dump current call stack (like sending SIGQUIT) — instantaneous snapshot
py-spy dump -p <PID>
```

### Real Scenario: Production CPU at 100%

```
Alert: order-service CPU 100% across all 8 instances.
Actions:
  1. kubectl exec into any pod
  2. ps aux | grep python → PID 142
  3. py-spy top -p 142

Output shows:
  Total Samples 500
  Samples:
    60.0% — json.dumps (json/__init__.py:234)
    15.0% — json.loads (json/__init__.py:296)
    10.0% — OrderSerializer.to_json (serializers.py:89)
    5.0%  — logging.info (logging/__init__.py:1234)

Analysis: 60% of CPU is spent in json.dumps(). Logging library is emitting
JSON for every request at DEBUG level (should be INFO). Each request has a
50MB response body that json.dumps() serializes synchronously.

Fix:
  1. Install orjson: pip install orjson
  2. Replace: json.dumps(data) → orjson.dumps(data).decode()
      orjson is 2-10x faster than stdlib json for large payloads
  3. Set logging level to INFO (was DEBUG)
  4. Add streaming response for large payloads

Result: CPU drops from 100% to 22%. p99 latency drops from 4.5s to 400ms.
```

### memory_profiler — Line-by-Line Memory Usage

```bash
pip install memory_profiler matplotlib

# Profile a script
mprof run app.py
mprof plot  # Opens graph of memory over time
```

```python
from memory_profiler import profile

@profile  # Decorator — shows memory usage per line
def build_large_data_structure(num_items):
    items = []                    # 0.1 MiB
    for i in range(num_items):
        items.append({            # +2.3 MiB after 1M iterations
            'id': i,              # WHY: dict overhead is ~240 bytes/dict
            'name': f'item_{i}',  #       + string allocation
            'data': 'x' * 100,    #       + 100 bytes of data
        })
    return items                  # Peak: 240 MiB

# Output:
# Line #    Mem usage    Increment  Line Contents
# 4         12.3 MiB      0.0 MiB      items = []
# 5         18.1 MiB      5.8 MiB      for i in range(num_items):
# 6         150.2 MiB    132.1 MiB      items.append({...})

# Fix: Use __slots__, tuples, or numpy arrays to reduce memory.
```

### tracemalloc — Built-in Memory Tracing (Python 3.4+)

```python
import tracemalloc

tracemalloc.start()

# --- Run your code ---
run_my_service()
# -------------------

# Take snapshot
snapshot = tracemalloc.take_snapshot()

# Top 10 memory-consuming lines
top_stats = snapshot.statistics('lineno')
print("[ Top 10 Memory Allocations ]")
for stat in top_stats[:10]:
    print(f"{stat}")
# Output:
# /app/services/report_service.py:142: size=512 MiB, count=1048576, average=512 B
# /app/lib/cache.py:78: size=256 MiB, count=524288, average=512 B

# Compare two snapshots (find what grew between them)
snapshot2 = tracemalloc.take_snapshot()
top_diff = snapshot2.compare_to(snapshot, 'lineno')
for stat in top_diff[:5]:
    print(f"{stat}")
# Shows exactly which lines allocated memory between the two points.
```

---

## Java Profiling

### async-profiler — The Gold Standard (Production-Safe)

async-profiler uses `perf_events` and `AsyncGetCallTrace` to sample CPU and allocations with near-zero overhead. **Same safety profile as py-spy — attach to running JVM anytime.**

```bash
# Download
curl -LO https://github.com/async-profiler/async-profiler/releases/download/v3.0/async-profiler-3.0-linux-x64.tar.gz
tar xzf async-profiler-3.0-linux-x64.tar.gz

# CPU profiling
./profiler.sh -d 30 -f /tmp/flamegraph.html <PID>
# -d 30: 30 second duration
# -f: output flamegraph HTML

# Allocation profiling (what's allocating the most memory?)
./profiler.sh -e alloc -d 30 -f /tmp/alloc.html <PID>

# Lock profiling (what locks are contended?)
./profiler.sh -e lock -d 30 -f /tmp/locks.html <PID>

# Wall-clock profiling (I/O, network, sleep — not just CPU)
./profiler.sh -e wall -d 30 -f /tmp/wall.html <PID>

# Attach to containerized JVM
./profiler.sh -d 30 -f flamegraph.html $(pgrep -f "java.*order-service")
```

### Real Scenario: Application Freeze Every 10 Minutes

```
Symptom: Order service hangs for 2 seconds exactly every 10 minutes.
Users see spikes in p99 latency. No errors in logs.

Investigation:
  1. JFR recording: jcmd <PID> JFR.start name=freeze-investigation duration=600s
  2. Reproduced in 10 min window.

JFR Analysis (JDK Mission Control):
  - Event: GC Full GC
  - Duration: 2,130ms (!)
  - Cause: CMS old generation collection on 32GB heap
  - Old gen occupancy: 95% before collection

Root cause: Heap is 32GB with CMS GC. Young gen promotes too fast.
CMS old gen collection is stop-the-world on a heap this large.
Java 8 CMS cannot compact — fragmentation accumulates until single-threaded full GC.

Fix: Migrate from CMS to G1GC (default since Java 9).
  -XX:+UseG1GC -XX:MaxGCPauseMillis=200
  Also: reduce heap to 12GB. 32GB was overprovisioned.
  Oversized heap = longer GC pauses. Find the right size, not the biggest size.

Result: Max GC pause dropped from 2,130ms to 45ms. Freezes eliminated.
```

### Java Flight Recorder (JFR) — Comprehensive System-Level Recording

```bash
# Start JFR at JVM startup
java -XX:StartFlightRecording=filename=recording.jfr,dumponexit=true \
     -XX:FlightRecorderOptions=stackdepth=256 \
     -jar order-service.jar

# Start JFR on running JVM (attach)
jcmd <PID> JFR.start name=myrecording filename=/tmp/recording.jfr duration=600s

# Dump current recording
jcmd <PID> JFR.dump name=myrecording filename=/tmp/recording.jfr

# Stop recording
jcmd <PID> JFR.stop name=myrecording

# Command-line analysis (no GUI needed)
jfr print /tmp/recording.jfr --events jdk.GarbageCollection
jfr print /tmp/recording.jfr --events jdk.ThreadDump
jfr print /tmp/recording.jfr --events jdk.SocketRead,jdk.SocketWrite

# Summarize event counts
jfr summary /tmp/recording.jfr
```

### Heap Dump Analysis — Finding Memory Leaks

```bash
# Take heap dump (pauses JVM for duration — seconds on large heaps)
jmap -dump:live,format=b,file=/tmp/heap.bin <PID>

# Better: set JVM to dump on OOM automatically
java -XX:+HeapDumpOnOutOfMemoryError \
     -XX:HeapDumpPath=/dumps/ \
     -jar order-service.jar
```

Load in Eclipse Memory Analyzer (MAT):
1. **Leak Suspects Report** — automatic analysis. Click "Leak Suspects" → shows retained heap per suspect.
2. **Histogram** — list all objects grouped by class. Sort by "Retained Heap" descending.
3. **Dominator Tree** — which objects keep the most memory alive.
4. **Paths to GC Roots** — why is this specific object not garbage collected?

```
Example MAT analysis:
  Class: com.example.cache.MetricsCache   |  Objects: 1  | Retained: 2.3GB
  Dominator Tree → MetricsCache → ConcurrentHashMap → 1.4M entries
  Path to GC Roots: MetricsCache → static field in MetricsCollector

  Root cause: Static MetricsCache never evicts entries.
  Each entry holds a 60-second window of raw metrics data (never expires).
  After 7 days of uptime: 1.4M entries × ~1.6KB average = 2.3GB leak.

  Fix: Add TTL-based eviction. Expire entries after 10 minutes.
```

---

## JavaScript / Node.js Profiling

### clinic.js — The Node.js Diagnostic Suite

```bash
# Install
npm install -g clinic

# CPU profiling (flamegraph)
clinic flame -- node app.js
# Load test your app, then Ctrl+C
# Opens flamegraph in browser automatically

# Event loop latency (doctor)
clinic doctor -- node app.js
# Load test, Ctrl+C
# Shows event loop delay, GC pauses, async operation breakdown
# Red = bad (event loop was blocked), green = good

# Async operation latency (bubbleprof)
clinic bubbleprof -- node app.js
# Load test, Ctrl+C
# Shows waterfall of async operations and their latencies
```

### Real Scenario: Node.js Event Loop Blocked

```
Symptom: API p50 = 10ms, p99 = 4800ms. NOT gradual — binary distribution.
Either 10ms (normal) or 4800ms (blocked for 4+ seconds).

clinic doctor output:
  Event Loop Delay: p50 = 2ms, p99 = 4720ms
  Top blocking operation: crypto.pbkdf2Sync()

Root cause: A middleware is calling crypto.pbkdf2Sync() synchronously
on every login request. pbkdf2 is deliberately slow (password hashing).
Synchronous crypto blocks the ENTIRE event loop — no other requests
can be processed while it runs.

Fix: Replace crypto.pbkdf2Sync() with crypto.pbkdf2() async version.
  Or: move password hashing to a worker thread.

Result: Event loop delay p99 drops from 4720ms to 8ms.
```

### Chrome DevTools (Node.js Inspector)

```bash
# Start with inspector
node --inspect app.js

# Opens: chrome://inspect → Click "inspect"
```

Tabs available:
- **Memory**: Take heap snapshots. Compare snapshots to find leaks. Allocation timeline shows allocation rate.
- **Profiler**: Record JavaScript CPU profile. Shows hot functions as a flamegraph.
- **Sources**: Set breakpoints, step through code, inspect variables.

### Built-in V8 Profiler

```bash
# Generate V8 CPU profile
node --prof app.js
# Produces: isolate-0x*.log

# Process the log into human-readable format
node --prof-process isolate-0x*.log > processed.txt

# Output shows:
# [Summary]:
#    ticks  total  nonlib   name
#    3456   58.2%   61.3%   JavaScript
#    1200   20.2%   21.3%   C++
#     ...     ...     ...   ...
#
# [JavaScript]:
#    ticks  total  nonlib   name
#    1203   20.3%   21.4%   LazyCompile: *serializeOrder /app/serializers.js:45
#     567    9.6%   10.1%   LazyCompile: *validateInput /app/middleware.js:23
```

---

## How to Read Flame Graphs

Flame graphs were invented by Brendan Gregg. They are the universal language of CPU profiling.

### Anatomy

```
       ┌── abc ──┬── def ──┬─ ghi ──────────────┐
  main ─┤         │         ├─ jkl ──────────────┤
       │         └─ mno ────┤         ┌─── pqr ──┤
       └─ stu ──────────────┴─ vwx ───┤         ├─ ...
                                       └─ yza ──┘
```

Rules:
1. **X-axis is NOT time**. It's the stack profile sorted alphabetically. The left-to-right order means nothing.
2. **Y-axis is stack depth**. Bottom = entry point (main). Each level up = one function call deeper.
3. **Width is proportional to CPU time** spent in that function (including its children).
4. **Flat top** = function is directly consuming CPU (leaf function — doing work, not calling others).
5. **Wide base** = parent function that calls many things (includes time of all children).

### What to Look For

```
Pattern                     | Meaning                           | Action
----------------------------|-----------------------------------|------------------------
Wide, flat plateaus         | Hot function doing computation   | Optimize this function
Narrow, tall towers         | Deep recursion (not necessarily  | Check recursion depth
                            | slow — just deep call stack)     |
Many small, separate towers | Many different code paths        | Hard to optimize — profile
                            |                                   | by endpoint, not whole app
Missing stacks              | JIT-compiled code, or I/O wait   | Try wall-clock profiling
                            | (not consuming CPU at that time) |
```

### Real Scenario: Flamegraph Shows 40% in Regex

```
Flamegraph screenshot:
  ████████████████████ url_router.match   40.2% of all CPU samples
  ██████████          compile_regex       38.7% (called by url_router.match)
  ██████              regex_match          1.5% (actual matching)

Interpretation: 38.7% of ALL CPU time is spent COMPILING regexes, not matching them.
The URL router is re-compiling regex patterns on every request (Python's re.match
caches ~100 patterns, but you have 500 routes).

Fix: Pre-compile regexes at startup:
  BAD:  re.match(r'^/api/v\d+/users/(\d+)$', path)  # Compiles every call
  GOOD: ROUTE_RE = re.compile(r'^/api/v\d+/users/(\d+)$')
        ROUTE_RE.match(path)  # No compilation overhead

Better: Use a URL routing library that compiles patterns once (e.g., find-my-way
for Node.js, or use a trie-based router instead of regex for simple routes).

Result: url_router drops from 40.2% to 2.1% of CPU samples.
p99 latency drops 38%. No code changes to endpoints — just routing infrastructure.
```

---

## Quick Reference: Which Profiler When

| Language | What to Profile | Tool | Command |
|----------|----------------|------|---------|
| Python | CPU (live) | py-spy | `py-spy top -p PID` |
| Python | CPU (flamegraph) | py-spy | `py-spy record -o fg.svg -p PID` |
| Python | Memory (line-by-line) | memory_profiler | `@profile` decorator |
| Python | Memory (snapshot diff) | tracemalloc | `tracemalloc.take_snapshot()` |
| Python | Function timing | cProfile | `python -m cProfile -o out.prof app.py` |
| Java | CPU (live attach) | async-profiler | `./profiler.sh -d 30 -f fg.html PID` |
| Java | Allocations | async-profiler | `./profiler.sh -e alloc -d 30 -f fg.html PID` |
| Java | Locks | async-profiler | `./profiler.sh -e lock -d 30 -f fg.html PID` |
| Java | Comprehensive | JFR | `jcmd PID JFR.start` |
| Java | Memory leak | jmap + MAT | `jmap -dump:live,file=heap.bin PID` |
| Node.js | CPU | clinic flame | `clinic flame -- node app.js` |
| Node.js | Event loop | clinic doctor | `clinic doctor -- node app.js` |
| Node.js | Async latency | clinic bubbleprof | `clinic bubbleprof -- node app.js` |
| Node.js | Memory | Chrome DevTools | `node --inspect app.js` |

---

*See also: [Load Testing Guide](../load-testing/load-testing-guide.md) | [Bottleneck Analysis](../bottleneck-analysis/bottleneck-guide.md) | [Caching Strategies](../caching/caching-strategies.md)*
