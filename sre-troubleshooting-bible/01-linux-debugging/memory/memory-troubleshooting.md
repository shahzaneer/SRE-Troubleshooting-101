# Memory Troubleshooting
> **Category:** Linux | Memory | Performance
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#linux` `#memory` `#oom` `#oncall`

---

## Table of Contents
1. [OOM Killer](#1-oom-killer)
2. [Understanding free -h](#2-understanding-free--h)
3. [Memory Leak Detection](#3-memory-leak-detection)
4. [Swap](#4-swap)
5. [Page Cache](#5-page-cache)
6. [slabtop — Kernel Memory](#6-slabtop--kernel-memory)
7. [Huge Pages](#7-huge-pages)
8. [Python: Memory Leak Detection](#8-python-memory-leak-detection)
9. [Java: Memory Analysis](#9-java-memory-analysis)
10. [JS/Node: Memory Analysis](#10-jsnode-memory-analysis)

---

## 1. OOM Killer

### What Happens During an OOM

When the kernel cannot allocate memory (all physical RAM + swap is exhausted), the Out-Of-Memory (OOM) killer selects a process to kill based on a "badness score" and terminates it. The goal is to free enough memory to keep the system running. The process to kill is chosen based on:

- **OOM score** (`/proc/PID/oom_score`) — higher = more likely to be killed. Computed from memory usage, process age, cgroup limits, etc.
- **OOM score adjustment** (`/proc/PID/oom_score_adj`) — can be biased by the admin. Range: -1000 (never kill) to 1000 (always kill).

### Reading the OOM Killer Logs

```bash
# Find OOM events in kernel logs
dmesg | grep -i "out of memory\|oom\|killed process"

# With human-readable timestamps
dmesg -T | grep -i oom

# journalctl (if kernel logs go through journald)
journalctl -k | grep -i oom

# Sample OOM output:
# [1562341.234567] Out of memory: Killed process 28471 (redis-server) total-vm:4294967296kB,
#   anon-rss:3145728kB, file-rss:0kB, shmem-rss:0kB, UID:1000 pgtables:4096kB oom_score_adj:0

# Decoding the message:
# anon-rss: 3145728kB = 3GB of anonymous (heap, stack) memory
# file-rss: 0kB       = 0 memory from mmap'd files (code, shared libs)
# The process was using 3GB of "real" memory (heap/stack)
```

### Classic Scenario: Redis Killed at 2 AM

> **Every night at 2:00 AM,** Redis is found dead. `systemctl status redis` shows `"exited" with code 137` (128+9 = killed by SIGKILL). `dmesg -T | grep -i oom` shows:
>
> ```
> [Tue Jun 11 02:03:17 2026] Out of memory: Killed process 28471 (redis-server) ...
> ```
>
> Root cause: A batch job (`import-job.service`) loads 40 million records into a PostgreSQL database. The job uses a Python script with `psycopg2.extras.execute_values` and builds the entire 40M-row dataset in memory before inserting. The Python process's RSS balloons to ~14GB. Total RAM is 16GB with no swap configured. The OOM killer picks Redis because:
> 1. Redis is the largest process after the batch job (3.5GB)
> 2. They have equal `oom_score_adj` (0), and Redis has a slightly higher raw score
> 3. Kernel picks the process with the highest oom_score first
>
> **Fix:** Set `oom_score_adj=-1000` on Redis to protect it; chunk the batch job to stream data instead of loading it all into memory.

### OOM Score Deep Dive

```bash
# Check OOM score for a specific process
cat /proc/$PID/oom_score
# 756 — higher means more likely to be killed

cat /proc/$PID/oom_score_adj
# 0 — administrator override (default 0)

# Adjust OOM score adjustment (protect a process)
echo -1000 > /proc/$PID/oom_score_adj  # Never kill this process
echo 1000 > /proc/$PID/oom_score_adj   # Always kill this one first

# In a systemd unit file, equivalent:
# [Service]
# OOMScoreAdjust=-1000

# Check all processes by OOM score
for pid in $(ls /proc | grep -E '^[0-9]+$'); do
  if [ -f /proc/$pid/oom_score ] && [ -f /proc/$pid/comm ]; then
    score=$(cat /proc/$pid/oom_score 2>/dev/null)
    comm=$(cat /proc/$pid/comm 2>/dev/null)
    [ -n "$score" ] && echo "$score $pid $comm"
  fi
done | sort -rn | head -20

# Top 5 processes most likely to be killed by OOM
for pid in $(ls /proc | grep -E '^[0-9]+$'); do
  [ -f /proc/$pid/oom_score ] && [ -f /proc/$pid/comm ] && \
  echo "$(cat /proc/$pid/oom_score) $(cat /proc/$pid/comm)" 2>/dev/null
done | sort -rn | head -5
```

### Memory Cgroups — Preventing OOM at the Service Level

```bash
# cgroup v2 memory limit (prefer this over relying on global OOM)
# Set on a systemd service:
systemctl set-property myapp.service MemoryMax=4G
systemctl set-property myapp.service MemoryHigh=3.5G  # soft limit, throttle allocation
# When the service exceeds MemoryMax, cgroup OOM kills a process INSIDE the cgroup,
# not the global OOM killer. Much more predictable.

# Check cgroup memory limits
systemctl show myapp.service --property=MemoryMax
systemctl show myapp.service --property=MemoryCurrent

# View cgroup memory stats
cat /sys/fs/cgroup/system.slice/myapp.service/memory.current
cat /sys/fs/cgroup/system.slice/myapp.service/memory.max

# For Docker containers:
docker update --memory 4g --memory-swap 4g container_name
```

---

## 2. Understanding free -h

### Output Explained

```bash
free -h
#                total        used        free      shared  buff/cache   available
# Mem:            15Gi       3.2Gi       256Mi       1.1Gi        11Gi        10Gi
# Swap:          2.0Gi       512Mi       1.5Gi
```

| Column       | Meaning |
|-------------|---------|
| **total**   | Total physical RAM available to the OS. |
| **used**    | Memory used by applications (heap, stack, anon mmap). Does NOT include kernel buff/cache. |
| **free**    | Completely unused memory. This is truly wasted — the kernel isn't using it for anything. |
| **shared**  | Shared memory (tmpfs, /dev/shm, shared libs). Often mostly tmpfs. |
| **buff/cache** | Page cache + buffer cache. File-backed pages the kernel keeps in RAM because "why not?" — they speed up file access. **This is reclaimable.** |
| **available** | Estimate of how much memory is available for new processes WITHOUT swapping. `available = free + reclaimable (buff/cache)` minus kernel reservations. **This is the number you care about.** |

### Classic Scenario: "System Is Out of Memory!"

> An SRE runs `free -h` and sees:
> ```
> Mem: total=15Gi, used=14.5Gi, free=200Mi, buff/cache=8Gi, available=8.2Gi
> ```
> They panic: "Only 200MB free! We need more RAM!"
>
> **Reality:** 8.2GB is `available`. The kernel has 8GB of page cache (recently read files cached in RAM) that it will instantly reclaim if a process needs more memory. The actual memory pressure is minimal. The `free` column is misleading — always look at `available`.

### Deeper Memory Stats from /proc/meminfo

```bash
cat /proc/meminfo
# Key fields:
# MemTotal:       16384000 kB   — Total physical RAM
# MemFree:          256000 kB   — Completely unused
# MemAvailable:   10485760 kB   — Available for new allocations (THE number to watch)
# Buffers:           51200 kB   — Block device buffers (raw I/O cache)
# Cached:         8388608 kB   — Page cache (file-backed pages)
# SwapCached:       10240 kB   — Pages swapped out AND still in swap cache
# Active:         5242880 kB   — Recently used pages, NOT reclaimable easily
# Inactive:       4194304 kB   — Not recently used pages, good candidates for reclaim
# Active(anon):   3145728 kB   — Anonymous (heap) pages, recently used
# Inactive(anon): 1048576 kB   — Anonymous pages, less recently used
# Active(file):   2097152 kB   — File-backed pages, recently used
# Inactive(file): 3145728 kB   — File-backed pages, less recently used
# Unevictable:           0 kB   — Locked pages (mlock), cannot be reclaimed
# Mlocked:               0 kB
# SwapTotal:      2097152 kB
# SwapFree:       1572864 kB
# Dirty:            10240 kB   — Modified pages not yet written to disk
# Writeback:             0 kB   — Pages currently being written to disk
# AnonPages:      4194304 kB   — Anonymous pages in resident set
# Shmem:          1153433 kB   — tmpfs / shared memory
# KReclaimable:   2097152 kB   — Kernel memory that can be reclaimed (slab)

# One-liner to show the useful fields:
grep -E "^(MemTotal|MemFree|MemAvailable|Cached|SwapTotal|SwapFree|Dirty|AnonPages)" /proc/meminfo
```

---

## 3. Memory Leak Detection

### Step 1: Is It Actually a Leak?

```bash
# Watch RSS growth over time
while true; do
  ps -eo pid,rss,comm --sort=-rss | head -10
  echo "---"
  sleep 60
done

# Or for a specific PID
watch -n 10 "ps -o pid,rss,vsz,comm -p $PID"

# pmap — detailed memory map of a process
pmap -x $PID
# Address           Kbytes     RSS   Dirty Mode  Mapping
# 00007f8a2c000000   65536   32768   32768 rw---   [ anon ]       <-- heap growing?
# 00007f8a30000000  131072   65536   65536 rw---   [ anon ]
# 00007f8a38000000  262144  131072  131072 rw---   [ anon ]
# 00007f8a48000000  524288  262144  262144 rw---   [ anon ]
# ...
# If anonymous mapping RSS keeps growing monotonically, it's a heap leak.

# /proc/PID/smaps — like pmap but with detailed stats per mapping
cat /proc/$PID/smaps | grep -E "(^[0-9a-f]|Rss:|Pss:|Private_Dirty:|Anonymous:)"
# Pss (Proportional Set Size) = RSS divided by number of processes sharing the page
# More accurate than RSS for shared memory

# Rss:       102400 kB
# Pss:        98304 kB  (lower than Rss if shared)
# Private_Dirty: 81920 kB  (pages this process wrote to — not reclaimable)
# Anonymous:  90112 kB     (heap/stack — the place to look for leaks)

# vmstat — system-level swap activity
vmstat 1
# procs -----------memory---------- ---swap-- -----io----
#  r  b   swpd   free   buff  cache   si   so    bi    bo
#  2  0 524288 256000  51200 8388608   0    0    12   345
#                                   ^^   ^^
# si (swap in): pages read from swap into RAM per second
# so (swap out): pages written from RAM to swap per second
# If 'so' is consistently > 0, memory pressure is building.

# Track process RSS over time (save for analysis)
nohup sh -c '
  while true; do
    echo "$(date +%s) $(awk "/^VmRSS:/ {print \$2}" /proc/PID/status)";
    sleep 10;
  done
' > /tmp/rss-tracking.log &
```

### Classic Scenario: 50MB/Hour Leak

> **Alert:** Memory on `api-04` grows from 2GB to 7.5GB over 3 days, then the process is killed by OOM killer. The app restarts, and the cycle repeats.
>
> `pmap -x` shows the heap (`[ anon ]`) is the only thing growing. `jmap -histo:live $PID` shows:
> ```
>  num     #instances         #bytes  class name
>    1:       1234567      987654320  com.mysql.cj.jdbc.PreparedStatementImpl
> ```
>
> Root cause: Database connection pool leak. A developer wrote:
> ```java
> Connection conn = dataSource.getConnection();
> PreparedStatement ps = conn.prepareStatement(query);
> ResultSet rs = ps.executeQuery();
> // ... process rs ...
> // BUG: forgot to close conn, ps, rs
> ```
> Connections never returned to the pool, and pooled connections holding `PreparedStatement` references prevent GC. Each new request opens a new connection.
>
> **Fix:** Use try-with-resources:
> ```java
> try (Connection conn = dataSource.getConnection();
>      PreparedStatement ps = conn.prepareStatement(query);
>      ResultSet rs = ps.executeQuery()) {
>     // ... process ...
> }
> ```

### valgrind for Native Code Leaks

```bash
# Detect leaks in a C/C++ binary:
valgrind --leak-check=full --show-leak-kinds=all --track-origins=yes ./myapp
# Output:
# ==12345== 123,456 bytes in 100 blocks are definitely lost at malloc()
#    at 0x483B7F3: malloc (in /usr/lib/valgrind/vgpreload_memcheck-amd64-linux.so)
#    by 0x4052B4: allocate_buffer (buffer.c:45)
#    by 0x405567: process_request (handler.c:123)
#
# "definitely lost" = memory allocated by never freed (true leak)
# "indirectly lost" = memory pointed to by leaked pointers (also a leak)
# "possibly lost"   = interior pointer — maybe a leak, maybe not (custom allocators)
# "still reachable" = memory still pointed to at exit (not a leak, but worth reviewing)

# WARNING: valgrind slows the process by 10-50x. Only use in dev/staging.
```

---

## 4. Swap

### When Swap Is Fine

Swap is NOT inherently bad. The kernel uses swap intelligently:
- Swaps out **inactive** pages (process startup code, unused heap regions) that haven't been accessed in a long time.
- Frees RAM for the page cache (file-backed pages), which speeds up I/O.
- A system with 2GB of swapped-out stale pages and 6GB of AVAlLABLE memory is fine.

### When Swap Is Catastrophic: Thrashing

**Thrashing** = the active working set exceeds available physical RAM, so the kernel constantly swaps pages in and out. The system spends more time doing I/O than useful work.

```bash
# Detecting thrashing with vmstat
vmstat 1
# procs -----------memory---------- ---swap-- -----io---- -system-- ------cpu-----
#  r  b   swpd   free   buff  cache   si   so    bi    bo   in   cs us sy id wa st
#  8  3 2097152  10240   1024   2048 5000 4000 12000  8000 2000 5000 10 30  0 60  0
#                                    ^^^^ ^^^^
# si (swap in)  = 5000 pages/sec → actively pulling from swap
# so (swap out) = 4000 pages/sec → actively pushing to swap
# bi (block in) = 12000 blocks/sec → massive reads (probably swap-backed)
#
# Also note: r (run queue) = 8, b (blocked) = 3, wa (iowait) = 60%
# This system is thrashing. Applications will be unresponsive.
#
# Fix: ADD RAM. No tuning can fix thrashing — you need more physical memory.
# Interim: kill or cgroup-limit the memory-hogging process.

# Check which processes are swapped out
for pid in $(ls /proc | grep -E '^[0-9]+$'); do
  swp=$(awk '/VmSwap:/ {print $2}' /proc/$pid/status 2>/dev/null)
  [ -n "$swp" ] && [ "$swp" != "0" ] && \
    echo "PID $pid ($(cat /proc/$pid/comm)): ${swp} kB swapped"
done | sort -t: -k2 -rn | head -10

# What's actively being swapped?
sar -W 1 5  # reports swap activity per second
```

### swappiness Tuning

```bash
# Check current swappiness
cat /proc/sys/vm/swappiness
# Default: 60 (range 0-100)
# 0 = avoid swapping as much as possible (but still swap to prevent OOM)
# 1 = minimal swapping (preferred for databases: Redis, PostgreSQL, MongoDB)
# 60 = default — swap when ~40% of RAM is used
# 100 = aggressive swapping

# Set swappiness
echo 1 > /proc/sys/vm/swappiness
# or permanently in /etc/sysctl.d/99-swap.conf:
# vm.swappiness = 1

# For Redis / databases — the recommendation is 1, not 0.
# Reason: swappiness=0 was historically "never swap" but since kernel 3.5,
# it can still cause OOM instead of swap. swappiness=1 gives you the behavior
# you actually want: swap only when absolutely necessary.
```

---

## 5. Page Cache

### Why Page Cache Matters

The page cache stores file-backed pages (contents of files on disk) in RAM. When an application reads a file, the kernel caches it. Next read is from RAM (nanoseconds) instead of disk (microseconds to milliseconds). This is "free" memory — it's automatically reclaimed if applications need more RAM.

```bash
# Check page cache size
cat /proc/meminfo | grep -E "(Cached|Buffers|Dirty|Writeback)"
# Cached:   8388608 kB    — page cache (file contents)
# Buffers:     51200 kB    — raw block I/O cache (filesystem metadata)
# Dirty:       10240 kB    — modified pages not yet written to disk
# Writeback:       0 kB    — pages currently being written to disk

# What's in the page cache? (requires root)
# vmtouch (apt-get install vmtouch) or fincore (part of util-linux)
vmtouch /var/lib/postgresql/data
# Resident Pages: 524288/1048576  4G/8G  50%
# Shows how much of the PostgreSQL data directory is in the page cache.

# fincore — show which parts of a file are cached
fincore --pages=false --bytes /var/lib/postgresql/data/base/16384/12345
# Shows per-file cache residency

# Does a specific file fit in the page cache?
ls -lh /var/lib/postgresql/data/PG_16_202307071/16384/12345
# Then check if cache is larger than the file — if not, working set exceeds cache.

# Dropping caches (⚠️ TESTING ONLY — NEVER IN PRODUCTION)
# This frees all page cache, dentries, and inodes.
# Production consequences: massive I/O spike as everything re-reads from disk.
# May cause service degradation, timeouts, cascading failures.
echo 3 > /proc/sys/vm/drop_caches
# 1 = drop page cache only
# 2 = drop dentries and inodes
# 3 = drop all
```

### Classic Scenario: Memory-Cached Database

> **Phenomenon:** After a PostgreSQL restart, queries are slow (20-50ms) for the first hour, then speed up to <1ms. Memory usage climbs from 2GB to 10GB over the same period.
>
> **Explanation:** When PostgreSQL starts, the page cache is empty. Every query must read from disk. Over time, the kernel populates the page cache with frequently-accessed data files. After the working set (indexes, hot tables) is cached, queries hit RAM and are fast.
>
> **This is normal and desirable.** The "high memory usage" after warm-up is just the page cache doing its job. `free -h` would show high `buff/cache` and low `free`, but high `available`.

---

## 6. slabtop — Kernel Memory

### When Kernel Memory Is the Problem

```bash
# slabtop — like top but for kernel memory (slab allocator)
slabtop -s c  # sort by cache size

# Sample output:
#  Active / Total Objects (% used)    : 12345678 / 13000000 (95.0%)
#  Active / Total Slabs (% used)      : 289012 / 289012 (100.0%)
#  Active / Total Caches (% used)     : 123 / 150 (82.0%)
#  Active / Total Size (% used)       : 2048.0M / 2150.0M (95.3%)
#
#   OBJS ACTIVE  USE OBJ SIZE  SLABS OBJ/SLAB CACHE SIZE NAME
# 4500000 4500000 100%    0.50K  60000       75    2200MB dentry
# 1200000 1190000  99%    1.00K  40000       30    1200MB inode_cache
# 500000  500000 100%    0.25K  12500       40     500MB kmalloc-256
#
# If dentry and inode_cache are huge, something is creating millions of files or directories.
# Common cause: PHP session files in /tmp, temp file spam from a misconfigured cron.
#
# If kmalloc-* slabs are huge, a kernel module or driver is leaking memory.

# Identify dentry/inode spam:
# Which directories have the most entries?
for d in /*; do echo "$(find "$d" -maxdepth 1 -type f 2>/dev/null | wc -l) $d"; done | sort -rn

# Track slab growth
watch -n 5 "slabtop -o -s c | head -30"
# -o = one-time output (non-interactive), -s c = sort by cache size

# Detailed slab info
cat /proc/slabinfo  # raw slab allocator stats
# Filter for dentry:
grep dentry /proc/slabinfo | awk '{print "active_objs=" $2 ", total_objs=" $3 ", obj_size=" $4 ", pages=" $6 * 4 "k"}'
```

---

## 7. Huge Pages

### Transparent Huge Pages (THP) and Latency

THP automatically promotes 4KB pages to 2MB huge pages. This reduces TLB (Translation Lookaside Buffer) misses and can significantly improve performance for databases and JVM apps. However, THP **compaction** (when the kernel scans for contiguous 4KB pages to merge into a 2MB huge page) can cause latency spikes.

```bash
# Check THP status
cat /sys/kernel/mm/transparent_hugepage/enabled
# always [madvise] never — current setting is madvise (only when app requests it)

# Disable THP (recommended for Redis, MongoDB, Cassandra)
echo never > /sys/kernel/mm/transparent_hugepage/enabled
echo never > /sys/kernel/mm/transparent_hugepage/defrag

# Permanent disable via GRUB:
# GRUB_CMDLINE_LINUX="transparent_hugepage=never"
# Or via systemd service override:
# [Service]
# ExecStartPre=/bin/sh -c "echo never > /sys/kernel/mm/transparent_hugepage/enabled"

# Check huge page usage
cat /proc/meminfo | grep -i huge
# AnonHugePages:   4194304 kB  — THP pages currently in use
# HugePages_Total:    1024     — explicit (non-transparent) huge pages reserved
# HugePages_Free:       512
# Hugepagesize:       2048 kB  — size of each huge page

# THP defrag can cause high latency — check if compaction is running
cat /proc/vmstat | grep compact
# compact_migrate_scanned: 123456789  — high number = lots of compaction happening
# compact_free_scanned: 234567890
# compact_isolated: 34567890
# compact_stall: 1234  — stalls are the problem (pauses allocation while compacting)

# Disable defrag only (keep THP but don't compact):
echo madvise > /sys/kernel/mm/transparent_hugepage/defrag
# or: echo never > /sys/kernel/mm/transparent_hugepage/defrag

# Explicit huge pages: pre-allocate at boot
# GRUB_CMDLINE_LINUX="hugepages=1024"
# Then: mount -t hugetlbfs none /dev/hugepages
# Apps can use mmap with MAP_HUGETLB to get guaranteed huge pages (no compaction)
```

---

## 8. Python: Memory Leak Detection

### tracemalloc — Python's Built-in Memory Tracker

```python
#!/usr/bin/env python3
"""
memory-leak-detector.py — tracks Python memory allocations over time,
identifies what is growing, and reports the top sources.
"""

import tracemalloc
import time
import os
import gc
from collections import defaultdict

class MemoryLeakDetector:
    def __init__(self, interval=60, top_n=10):
        self.interval = interval
        self.top_n = top_n
        self.snapshots = []
        self.baseline = None

    def start(self):
        tracemalloc.start(25)  # 25 frames deep — enough for accurate tracebacks
        self.baseline = tracemalloc.take_snapshot()
        print(f"[{time.strftime('%H:%M:%S')}] Memory tracking started. Baseline RSS: "
              f"{self._get_rss_mb():.1f} MB")

    def take_sample(self):
        snapshot = tracemalloc.take_snapshot()
        self.snapshots.append(snapshot)
        current = snapshot.statistics('lineno')
        previous_stats = self.baseline.statistics('lineno') if self.baseline else None

        print(f"\n{'='*70}")
        print(f"[{time.strftime('%H:%M:%S')}] Memory Snapshot #{len(self.snapshots)}")
        print(f"RSS: {self._get_rss_mb():.1f} MB | Traced: {snapshot.traced_memory}")

        # Top allocations since baseline
        if previous_stats:
            top_diff = snapshot.compare_to(self.baseline, 'lineno')
            print(f"\nTop {self.top_n} allocations since baseline:")
            print(f"{'Size Diff':>12s}  {'Count Diff':>11s}  {'File:Line':>50s}")
            print(f"{'-'*12}  {'-'*11}  {'-'*50}")
            for stat in top_diff[:self.top_n]:
                size_diff = stat.size_diff
                count_diff = stat.count_diff
                if size_diff > 0:  # only show growing allocations
                    print(
                        f"{self._format_bytes(size_diff):>12s}  "
                        f"{count_diff:>+11d}  "
                        f"{stat.traceback.format()[-1]:>50s}"
                    )

        # Top allocations in current snapshot
        print(f"\nTop {self.top_n} current allocations:")
        print(f"{'Size':>12s}  {'Count':>10s}  {'File:Line':>50s}")
        print(f"{'-'*12}  {'-'*10}  {'-'*50}")
        for stat in current[:self.top_n]:
            print(
                f"{self._format_bytes(stat.size):>12s}  "
                f"{stat.count:>10d}  "
                f"{stat.traceback.format()[-1]:>50s}"
            )

        # Track by category
        by_category = defaultdict(int)
        for stat in current:
            frame = stat.traceback.format()[-1] if stat.traceback else "unknown"
            by_category[frame] += stat.size
        print(f"\nUnique allocation sites: {len(by_category)}")

    def detect_anomalies(self):
        """Compare last two snapshots: what grew by >5%?"""
        if len(self.snapshots) < 2:
            return
        prev = self.snapshots[-2]
        curr = self.snapshots[-1]
        prev_total = sum(s.size for s in prev.statistics('lineno'))
        curr_total = sum(s.size for s in curr.statistics('lineno'))
        change = curr_total - prev_total

        if change > 0:
            pct = (change / max(prev_total, 1)) * 100
            if pct > 5:
                print(f"\n[WARNING] Memory grew by {pct:.1f}% ({self._format_bytes(change)}) "
                      f"in last {self.interval}s")

    def run_forever(self):
        self.start()
        while True:
            time.sleep(self.interval)
            self.take_sample()
            self.detect_anomalies()

    def _get_rss_mb(self):
        with open(f'/proc/{os.getpid()}/status') as f:
            for line in f:
                if line.startswith('VmRSS:'):
                    return int(line.split()[1]) / 1024
        return 0

    @staticmethod
    def _format_bytes(b):
        for unit in ['B', 'KB', 'MB', 'GB']:
            if abs(b) < 1024:
                return f"{b:.1f}{unit}"
            b /= 1024
        return f"{b:.1f}TB"

    @staticmethod
    def _format_number(n):
        if abs(n) < 1000:
            return str(n)
        if abs(n) < 1_000_000:
            return f"{n/1000:.1f}K"
        return f"{n/1_000_000:.1f}M"


if __name__ == "__main__":
    detector = MemoryLeakDetector(interval=30, top_n=10)
    try:
        detector.run_forever()
    except KeyboardInterrupt:
        print("\nDetector stopped.")
```

### Quick tracemalloc Recipes

```python
import tracemalloc

# Minimal example: find top allocations in a code block
tracemalloc.start()
# ... your code here ...
snapshot = tracemalloc.take_snapshot()
top_stats = snapshot.statistics('lineno')
for stat in top_stats[:5]:
    print(stat)
    # Example output:
    # <Statistic traceback=<Traceback> size=50.2 MiB count=500000>
    # python3.10/threading.py:1013: size=50.2 MiB (+50.2 MiB), count=500000 (+500000)

# Find what changed between two points
tracemalloc.start()
snap1 = tracemalloc.take_snapshot()  # baseline
# ... run code that might leak ...
snap2 = tracemalloc.take_snapshot()
for diff in snap2.compare_to(snap1, 'lineno')[:5]:
    print(diff)
    # Positive size_diff = memory grew at this line

# Show full traceback for a leak:
top = snap2.compare_to(snap1, 'lineno')[0]
print("Leak at:")
for frame in top.traceback:
    print(f"  {frame}")
```

### gc Module — Finding Uncollected Objects

```python
import gc

# Force a full garbage collection
gc.collect()

# List all objects tracked by the GC that can't be collected
# (due to circular references with __del__ or external references)
uncollectable = gc.garbage
print(f"Uncollectable objects: {len(uncollectable)}")

# Check GC thresholds and stats
print(f"GC thresholds: {gc.get_threshold()}")
print(f"GC count: {gc.get_count()}")
# (700, 10, 10) = gen0 has 700 pending, gen1 has 10, gen2 has 10

# Track specific object type counts
import sys
import types

def count_objects_by_type():
    """Count all live objects by type — find what's proliferating"""
    objects_by_type = {}
    for obj in gc.get_objects():
        t = type(obj)
        objects_by_type[t] = objects_by_type.get(t, 0) + 1
    return dict(sorted(objects_by_type.items(), key=lambda x: -x[1])[:20])

# Run this periodically and compare counts to find leaks

# Debug reference cycles:
gc.set_debug(gc.DEBUG_SAVEALL)  # save unreachable objects in gc.garbage
gc.collect()
for obj in gc.garbage:
    print(type(obj), obj)
```

---

## 9. Java: Memory Analysis

### jmap — Heap Analysis

```bash
JAVA_PID=28471

# Live histogram: count objects by class (without full GC, so "dead" objects are included)
jmap -histo $JAVA_PID | head -30
# Live histogram: forces full GC first (STW! use cautiously on production)
jmap -histo:live $JAVA_PID | head -30

# Sample output:
#  num     #instances         #bytes  class name (module)
#    1:      12345678     9876543216  [B (java.base)                 # byte arrays — suspicious
#    2:       8765432      876543210  java.lang.String (java.base)
#    3:       2345678      234567890  java.util.HashMap$Node         # Too many HashMap entries?
#    4:       1234567      123456789  com.example.model.Order        # 1.2M Order objects — likely a leak
#
# Look for:
# - [B (byte arrays) — usually the largest, but if >50% of heap, something holds too many buffers
# - Your app's domain classes with millions of instances
# - HashMap$Node — usually means too many entries in some map
# - char[] — mostly from String objects
#
# Monitor the histogram over time:
for i in $(seq 1 10); do
  echo "=== Sample $i @ $(date) ===" >> /tmp/java-hist.log
  jmap -histo:live $JAVA_PID 2>/dev/null | head -30 >> /tmp/java-hist.log
  sleep 60
done

# Heap dump (use with caution on large heaps — STW pause!)
jmap -dump:live,format=b,file=/tmp/heap-$(date +%s).hprof $JAVA_PID
# Alternative: use jcmd for less intrusive dump
jcmd $JAVA_PID GC.heap_dump /tmp/heap.hprof

# MAT (Eclipse Memory Analyzer) — analyze the heap dump offline:
# - Histogram by retained size (total memory freed if object were collected)
# - Leak Suspects report: auto-identifies objects holding most memory
# - Dominator Tree: hierarchy of object ownership by retained size
# - Path to GC Roots: why an object can't be collected
```

### jstat — GC Statistics

```bash
# Continuous GC monitoring (every 1s, 10 samples)
jstat -gcutil $JAVA_PID 1000 10

#   S0     S1     E      O      M     CCS    YGC     YGCT    FGC    FGCT     CGC    CGCT     GCT
#   0.00  45.32  67.23  89.12  94.23  92.11   1234   56.789    12   2.345     5      1.234  60.368
#
# S0/S1 = Survivor space 0/1 usage % (young gen)
# E     = Eden space usage %
# O     = Old gen (tenured) usage %  — GROWING monotonically = potential leak
# M     = Metaspace (class metadata) usage %
# CCS   = Compressed Class Space usage %
# YGC   = Young GC count (should be frequent)
# YGCT  = Young GC total time (seconds)
# FGC   = Full GC count  — GROWING = problem (should be RARE)
# FGCT  = Full GC total time
# GCT   = Total GC time
#
# Red flags:
# - O (Old gen) grows to 100% and triggers FGC
# - FGC count increasing rapidly
# - FGCT is a significant % of process uptime (e.g., 10% of total time in GC)

# With timestamps
jstat -gcutil -t $JAVA_PID 1000 10
# First column is timestamp since JVM start

# GC Cause — see why each collection happens
jstat -gccause $JAVA_PID 1000 3
# LGCC (Last GC Cause): Allocation Failure (normal young gen)
# GCC (Current GC Cause): No GC
# If LGCC is "Ergonomics" or "G1 Humongous Allocation" — normal
# If LGCC is "Metadata GC Threshold" — Metaspace too small
# If LGCC is "G1 Evacuation Pause" — normal young gen
```

### Java Native Memory Tracking

```bash
# Requires JVM started with:
# -XX:NativeMemoryTracking=summary  or  -XX:NativeMemoryTracking=detail

# Get NMT summary (can be done anytime on a running JVM)
jcmd $JAVA_PID VM.native_memory summary

# Sample output:
# Total: reserved=8252653KB, committed=4572397KB
# -     Java Heap (reserved=4194304KB, committed=3145728KB)
# -          Thread (reserved=102400KB, committed=51200KB)
#               (thread #100)
# -                     Class (reserved=1048576KB, committed=524288KB)
#               (classes #23456)
# -                    GC (reserved=204800KB, committed=102400KB)
# -             JIT Code (reserved=262144KB, committed=131072KB)
#
# Look for:
# - Thread memory growing: thread leak (thread pool not bounded, threads never die)
# - Class memory growing: classloader leak (hot-deploying without proper GC)
# - GC overhead large: too many GC threads or huge remembered sets
```

---

## 10. JS/Node: Memory Analysis

```bash
# Run with increased heap size
node --max-old-space-size=4096 app.js
# max-old-space-size: sets V8 old gen limit (default ~1.4GB on 64-bit)
# max-semi-space-size: sets V8 new gen limit

# Run with inspector for remote heap analysis
node --inspect=0.0.0.0:9229 app.js
# Then open chrome://inspect in Chrome, connect, take heap snapshots

# Node with heap profiler log
node --heap-prof app.js  # generates *.heapprofile files
```

### process.memoryUsage() — In-Process Monitoring

```javascript
// memory-monitor.js
function formatBytes(bytes) {
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function logMemory(label = '') {
    const mem = process.memoryUsage();
    const usage = {
        rss:            formatBytes(mem.rss),             // Resident Set Size (total process memory)
        heapTotal:      formatBytes(mem.heapTotal),       // Total V8 heap allocated
        heapUsed:       formatBytes(mem.heapUsed),        // Actually used heap
        external:       formatBytes(mem.external),        // C++ objects bound to JS (Buffers, etc.)
        arrayBuffers:   formatBytes(mem.arrayBuffers),    // ArrayBuffer + SharedArrayBuffer
        label,
    };

    const heapUsagePct = ((mem.heapUsed / mem.heapTotal) * 100).toFixed(1);
    console.log(
        `[${new Date().toISOString()}] ${label} | ` +
        `RSS: ${usage.rss} | ` +
        `Heap: ${usage.heapUsed}/${usage.heapTotal} (${heapUsagePct}%) | ` +
        `External: ${usage.external}`
    );

    // Alert on high heap usage
    if (heapUsagePct > 85) {
        console.error(
            `[MEMORY_ALERT] Heap usage at ${heapUsagePct}%. ` +
            `Consider --max-old-space-size or investigate memory leak.`
        );
    }

    return mem;
}

// Log every 30 seconds
setInterval(() => logMemory('periodic'), 30000);

// Log on SIGUSR1
process.on('SIGUSR1', () => {
    logMemory('SIGUSR1 triggered');
    const v8 = require('v8');
    const spaceStats = v8.getHeapSpaceStatistics();
    console.log('Heap space breakdown:');
    spaceStats.forEach(s => {
        console.log(`  ${s.space_name}: ${(s.space_used_size / 1024 / 1024).toFixed(1)} MB / ` +
                    `${(s.space_size / 1024 / 1024).toFixed(1)} MB (${(s.space_used_size / s.space_size * 100).toFixed(1)}%)`);
    });
});

// Trigger: kill -SIGUSR1 PID
```

### V8 Heap Snapshot via Chrome DevTools

```bash
# Start app with inspector
node --inspect app.js

# Open Chrome, go to chrome://inspect
# Click "inspect" on your Node process
# Go to "Memory" tab → "Take heap snapshot"
# Take snapshot 1 (baseline) → trigger suspected leak operation → Take snapshot 2
# Compare: "Comparison" view shows objects added between snapshots
# Sort by "Delta" (% change) — look for your domain objects growing

# Programmatic heap snapshot
node --inspect app.js
# In another terminal, connect:
node -e "
const inspector = require('inspector');
const fs = require('fs');
const session = new inspector.Session();
session.connect();
session.post('HeapProfiler.takeHeapSnapshot', null, (err, r) => {
    fs.writeFileSync('/tmp/heap.heapsnapshot', JSON.stringify(r.profile));
    session.disconnect();
});
"
# This requires inspector to be enabled and listening.
```

### Detecting Memory Leaks in Node.js

```javascript
// Common Node.js memory leak patterns and how to detect them:

// PATTERN 1: Growing collections (maps, arrays, sets)
// DETECT: Track Map/Set/Array size over time
const memoryTracker = {
    counters: new Map(),
    register(key) {
        this.counters.set(key, (this.counters.get(key) || 0) + 1);
        if (this.counters.get(key) > 10000) {
            console.error(`[LEAK_WARN] ${key}: ${this.counters.get(key)} instances`);
        }
    },
};

// PATTERN 2: Event listener accumulation
// DETECT: Track event emitter listener count
function checkEventEmitters() {
    const EventEmitter = require('events');
    // Walk process event emitters and check max listeners
    // This is simplified — in practice, use diagnostics_channel or async_hooks
    const maxListeners = EventEmitter.defaultMaxListeners;
    console.log(`Default max listeners: ${maxListeners}`);
    // To check specific emitter: emitter.listenerCount('data')
}
// Fix: always pair .on() with .off() or use .once()

// PATTERN 3: Unclosed resources (connections, file handles, timers)
// DETECT: process._getActiveHandles() and process._getActiveRequests()
function reportActiveResources() {
    const handles = process._getActiveHandles?.() || [];
    const requests = process._getActiveRequests?.() || [];
    console.log(`Active handles: ${handles.length}, Active requests: ${requests.length}`);

    if (handles.length > 1000) {
        // Group by type
        const byType = {};
        handles.forEach(h => {
            const t = h.constructor?.name || 'unknown';
            byType[t] = (byType[t] || 0) + 1;
        });
        console.error('[RESOURCE_LEAK] Active handles:', JSON.stringify(byType));
    }
}
setInterval(reportActiveResources, 60000);

// PATTERN 4: Closure retention (closures holding references to large objects)
// DETECT: heap snapshot comparison (see above) — look for "closure" or "context" objects
// that retain large arrays or strings

// PATTERN 5: Non-collected promises (promise chains with no rejection handler,
// or long chains with retained state)
// DETECT: Check unhandledRejection count
let unhandledRejections = 0;
process.on('unhandledRejection', (reason, promise) => {
    unhandledRejections++;
    if (unhandledRejections % 100 === 0) {
        console.error(`[PROMISE_WARN] ${unhandledRejections} unhandled rejections`);
    }
});
```
