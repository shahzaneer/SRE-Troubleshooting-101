# CPU Troubleshooting
> **Category:** Linux | CPU | Performance
> **Difficulty:** Basic to Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#linux` `#cpu` `#performance` `#oncall`

---

## Table of Contents
1. [High CPU Detection](#1-high-cpu-detection)
2. [Per-CPU Statistics (mpstat)](#2-per-cpu-statistics-mpstat)
3. [CPU Steal Time](#3-cpu-steal-time)
4. [IOWait vs Idle](#4-iowait-vs-idle)
5. [Load Average](#5-load-average)
6. [Runaway Process](#6-runaway-process)
7. [CPU Affinity and NUMA](#7-cpu-affinity-and-numa)
8. [perf Profiling](#8-perf-profiling)
9. [Python: CPU Monitoring Script](#9-python-cpu-monitoring-script)
10. [Java: CPU Profiling](#10-java-cpu-profiling)
11. [JS/Node: CPU Analysis](#11-jsnode-cpu-analysis)

---

## 1. High CPU Detection

### First Response Commands

```bash
# Interactive — sort by CPU usage (press '1' to show per-CPU)
top -o %CPU

# htop with colors (install if missing: apt-get install htop)
htop -t                      # tree view
htop --sort-key PERCENT_CPU  # sort by CPU

# Non-interactive — top 20 CPU-consuming processes
ps aux --sort=-%cpu | head -20

# Which processes are using >50% CPU right now?
ps aux --sort=-%cpu | awk '$3 > 50 {print $2, $3, $11}' | head -10

# Quick CPU summary per process with pidstat
pidstat 1 5                  # 5 samples, 1 second apart
pidstat -u -p ALL 1 3        # all processes, CPU only

# Which users are consuming CPU?
ps aux | awk '{user[$1]+=$3} END {for(u in user) print user[u], u}' | sort -rn | head -5
```

### Real Scenario: Midnight CPU Spike

> **2:37 AM Page:** CPU on `web-03` has been at 99% for 10 minutes.
>
> ```
> $ top -o %CPU
>   PID USER      PR  NI    VIRT    RES    SHR S  %CPU %MEM     TIME+ COMMAND
> 28471 www-data  20   0  892344 542108   8228 S  98.7  3.4  27:34.12 php-fpm
> ```
>
> It's a single PHP-FPM worker consuming an entire core. `strace -p 28471 -c -f` shows it's in an infinite `select()` loop. The developer pushed a cron job that polls Redis every microsecond in a busy loop instead of using blocking `BLPOP`. Rollback the deploy, CPU drops to 5%.

---

## 2. Per-CPU Statistics (mpstat)

### mpstat Column-by-Column Breakdown

```bash
# Per-CPU stats: 5 samples, 1 second apart
mpstat -P ALL 1 5

# Sample output:
# CPU  %usr  %sys  %iowait  %irq  %soft  %steal  %guest  %idle
# all  23.5  5.2   0.0      0.0   0.5    0.0     0.0     70.8
#   0  45.1  8.3   0.0      0.0   0.8    0.0     0.0     45.8
#   1  12.2  3.1   0.0      0.0   0.2    0.0     0.0     84.5
#   2  18.7  6.5   0.0      0.0   0.5    0.0     0.0     74.3
#   3  18.2  2.9   0.0      0.0   0.5    0.0     0.0     78.4
```

| Column  | Meaning | What to Look For |
|---------|---------|------------------|
| `%usr`  | CPU time spent in user-space processes (your app code) | `>70%` on one CPU = single-thread bottleneck |
| `%sys`  | CPU time spent in kernel-space (system calls, drivers, I/O handling) | `>20%` consistently = too many syscalls, strace the process |
| `%iowait` | Time CPU was idle but had an outstanding I/O request | `>10%` = disk bottleneck; but see [IOWait vs Idle](#4-iowait-vs-idle) |
| `%irq`  | Hardware interrupt handling (NIC, disk controller interrupts) | `>5%` = NIC interrupt storm or bad driver |
| `%soft` | Software interrupt handling (network packet processing, block layer) | `>10%` = high network throughput or ksoftirqd bound |
| `%steal` | Time hypervisor stole CPU from this VM (overcommitted host) | `>5%` = noisy neighbor; see [Steal Time](#3-cpu-steal-time) |
| `%guest` | Time running a guest VM (on the hypervisor, not inside the guest) | Only relevant on hypervisor hosts |
| `%idle`  | Truly idle (no outstanding I/O either) | Should be >20% to absorb spikes |

### Key Insight: %usr + %sys Distribution

```bash
# Uneven distribution = single-threaded app not using SMP
mpstat -P ALL 1 5 | awk '
  /^[0-9]/ {cpu=$2; usr=$3}
  /^[0-9]/ && cpu!~/all/ {print cpu, usr}
' | sort -k2 -rn

# If CPU 0 is at 90% and CPUs 1-7 are at 5%, the process is single-threaded.
# Fix: check if app can use workers/threads (Gunicorn workers, PM2 cluster mode, Java thread pool)
```

---

## 3. CPU Steal Time

### What Is Steal Time?

Steal time is **CPU time the hypervisor gave to another VM** while your VM wanted it. It occurs when the physical host is overcommitted — more vCPUs allocated across all VMs than physical cores exist. Your VM's vCPU was ready to run, but the hypervisor scheduled a different VM's vCPU instead. From your VM's perspective, that time was "stolen."

### Why VMs Have It

- **Overcommitment ratio > 1.0** — e.g., a 32-core physical host has 40 VMs each with 2 vCPUs = 80 vCPUs competing for 32 cores.
- **Noisy neighbor** — another VM on the same host runs CPU-heavy workloads.
- **NUMA misalignment** — VM's vCPUs spread across NUMA nodes, causing remote memory access latency.
- **CPU pinning not configured** — vCPUs migrate between physical cores, incurring scheduler overhead.

### Classic Scenario: Daily 3 PM Latency Spike

> **Every day at 3:00 PM,** the order-processing API's p99 latency jumps from 15ms to 450ms. Engineers blame a database query, a GC pause, network saturation — everything except the hypervisor. After weeks of debugging, someone runs `mpstat` during the spike:
>
> ```
> %steal = 15.2
> ```
>
> A different VM on the same physical host runs a daily batch job (report generation) at 3 PM that pins 6 vCPUs at 100%. The hypervisor has only 4 physical cores to allocate. Steal time on all other VMs spikes because their vCPUs wait in the scheduler queue while the batch VM runs.
>
> **Fix:** The cloud provider migrated the noisy VM to a dedicated host. Steal time dropped to 0%.

### Detect and Diagnose Steal Time

```bash
# Live view of steal time per CPU
mpstat -P ALL 1 | awk '$9 > 1 {print "STEAL:", $2, $9"%"}'

# Check steal time over 60 seconds
mpstat 1 60 | awk '/all/ && $9 > 0 {print "steal=" $9 "% at " strftime("%H:%M:%S")}'

# From /proc:
grep steal /proc/stat
# Output: cpu  90784814 1001 6745364 215924949 2313771 0 1556437 0 0 0
# The 8th field (1556437) is steal time in jiffies since boot

# Python one-liner to compute current steal %
python3 -c "
with open('/proc/stat') as f:
    cpu = f.readline().split()[1:]
    total = sum(int(x) for x in cpu)
    steal = int(cpu[7])
    print(f'Steal: {steal * 100 / total:.1f}%')
"

# top can show steal time too — press 't' until you see it
# Or launch with: top -d1 -o %CPU
# Look for "%st" in the CPU summary line

# When steal > 5%, gather evidence for cloud provider:
echo "=== timestamp: $(date) ===" >> /tmp/steal-log.txt
echo "=== mpstat ===" >> /tmp/steal-log.txt
mpstat 1 30 >> /tmp/steal-log.txt &
echo "=== top ===" >> /tmp/steal-log.txt
top -b -n1 >> /tmp/steal-log.txt
```

### Mitigation

1. **Rightsize** — reduce vCPUs if app doesn't need them; fewer vCPUs = less contention.
2. **Dedicated host / dedicated instance** — AWS has Dedicated Hosts and Dedicated Instances.
3. **CPU pinning** — openstack: `hw:cpu_policy=dedicated`; AWS: `.metal` instance types.
4. **Reduce noisy neighbor impact** — if on shared tenancy, request host migration from cloud provider.

---

## 4. IOWait vs Idle

### The Critical Distinction

**IOWait is NOT "CPU waiting for disk."** On a multi-CPU system, iowait is the percentage of time a CPU was idle AND at least one I/O operation was in flight somewhere in the system. This means:

- **High iowait on one core + other cores busy** = the system is busy but one core happens to be idle when I/O is outstanding. This is normal.
- **High iowait on ALL cores** = likely a real I/O bottleneck.
- **High iowait with only one I/O-bound process** = misleading. The process sleeps on I/O, I/O completes, the kernel schedules the process — but during the interval between I/O completion and scheduling, if no other process was queued, the CPU is "iowait idle."

### Classic Scenario: The 60% IOWait False Alarm

> An SRE sees `iowait 60%` in `top`. They panic, file a ticket to provision faster SSDs (3,000 → 20,000 IOPS), and wake the storage team. After the upgrade, iowait is still 60%.
>
> The workload is a mostly-idle Postgres replica. The process executes a query, goes to sleep waiting for sequential scan I/O, I/O completes instantly (SSD), but there's no other work to schedule on the CPU. Between each I/O completion and the process being woken up, the CPU is in "idle with outstanding I/O" state = iowait.
>
> **The real metric is `%iowait * number_of_cores`.** If one core shows 100% iowait but total CPU usage is 5%, the system is actually 95% idle. Nothing is wrong.

### Diagnostic Flow

```bash
# Step 1: What does iowait look like per-CPU?
mpstat -P ALL 1 5
# If only one or two CPUs have iowait and rest are idle → probably nothing
# If ALL CPUs show iowait AND total CPU usage > 50% → real I/O issue

# Step 2: Is any process actually doing heavy I/O?
iotop -o
# Only shows processes currently doing I/O (not sleeping)

# Step 3: Is disk actually slow?
iostat -xz 1
# Look at:
#   r_await — average read latency (should be <5ms for SSD, <1ms for NVMe)
#   w_await — average write latency
#   %util — if near 100% AND await is high → real bottleneck

# Step 4: The definitive test — what's the actual disk latency?
ioping -c 10 /var/lib/postgresql
# Measures real I/O response time in microseconds

# The Bottom Line:
# IOWait is a CPU metric, not an I/O metric.
# Use iostat/ioping/blktrace for I/O debugging — see disk-troubleshooting.md
```

### iowait Calculation for Real

```bash
# From /proc/stat, compute iowait as fraction of wall-clock time:
# iowait% = iowait_jiffies / total_jiffies * 100
# But remember: this is based on CPU scheduling samples,
# not actual I/O wait time.

# Quick script to demystify iowait:
cat > /tmp/iowait-vs-idle.sh <<'EOF'
#!/bin/bash
# Shows iowait but also the proportion of truly idle CPUs
cpus=$(nproc)
while true; do
  read cpu usr nice sys idle iowait irq soft steal guest gn s< <(grep 'cpu ' /proc/stat)
  total=$((usr + nice + sys + idle + iowait + irq + soft + steal))
  busy=$((usr + nice + sys))
  echo "CPUs=$cpus | busy=$((busy * 100 / total))% | iowait=$((iowait * 100 / total))% | idle=$((idle * 100 / total))% | steal=$((steal * 100 / total))%"
  sleep 1
done
EOF
bash /tmp/iowait-vs-idle.sh
```

---

## 5. Load Average

### What the Numbers Mean

```bash
# Current load average
cat /proc/loadavg
# 2.15 1.89 1.56 3/1045 29834
#  ^     ^    ^   ^  ^    ^
#  |     |    |   |  |    most-recent PID
#  |     |    |   |  total processes in the system
#  |     |    |   running processes (currently on CPU)
#  |     |   15-minute load average
#  |     5-minute load average
#  1-minute load average

# Human-readable with uptime
uptime
# 14:23:01 up 45 days,  2:14,  3 users,  load average: 2.15, 1.89, 1.56
```

### Interpretation

| Load vs CPUs | Meaning |
|-------------|---------|
| Load < CPUs | System is underutilized. Processes are not waiting. |
| Load = CPUs | Saturation point. Every CPU has exactly 1 process. Perfectly utilized. |
| Load = CPUs + 1-3 | Slightly saturated. Short wait queues. Latency may increase slightly. |
| Load > CPUs * 2 | Significantly saturated. Long queues forming. Latency will be affected. |
| Load > CPUs * 10 | Critical saturation. Processes spending more time waiting than running. |

**Key nuance:** Load average counts processes in state R (running/runnable) AND D (uninterruptible sleep — usually stuck in I/O). A process blocked on NFS or a stuck storage device adds to load without using any CPU.

### Classic Scenario: Load 32 on 8-Core Machine

> An engineer sees `load average: 32.14, 30.22, 25.11` on an 8-core machine. They immediately think "CPU is pegged" but `mpstat` shows 40% idle.
>
> Upon investigation: a batch job runs 30 worker threads that all do synchronous HTTP calls to a slow downstream API. Each thread spends most of its time sleeping waiting for HTTP responses (S state) — these sleeping threads do NOT count toward load average. But the threads become RUNNABLE simultaneously when responses arrive, and all 30 compete for 8 CPUs. The run queue depth is ~22 processes waiting for CPU.
>
> The actual CPU isn't pegged because each thread's on-CPU time is tiny — just enough to process the HTTP response and issue the next request. But the context-switch rate is astronomical.

### Diagnostic Commands

```bash
# Add this to your top workflow:
# While top is running, press '1' to see per-CPU breakdown
# Check "load average" in the top-right corner

# Run queue depth (processes in R state — actually waiting for CPU)
vmstat 1 5 | awk 'NR>2 {print "r:", $1, "b:", $2}'
# 'r' column is processes waiting for CPU (run queue)
# 'b' column is processes in uninterruptible sleep (usually I/O)

# Context switch rate
vmstat 1 5 | awk 'NR>2 {print "cs:", $12}'
# Very high (>100k/s) with high load + low CPU = many short-lived, I/O-bound threads

# Check how many processes are actually running vs blocked
ps -eo state | sort | uniq -c | sort -rn
# High 'R' count + high load = actual CPU contention
# High 'S' count + high load = mostly sleeping, load inflated by D-state processes
```

---

## 6. Runaway Process

### Identify the Offender

```bash
# pidstat: which process(es) are consuming CPU right now
pidstat 1
# Sample output:
# 03:45:12 PM   UID       PID    %usr %system  %guest   %wait    %CPU   CPU  Command
# 03:45:13 PM  1000     28471   98.0    2.0    0.0    0.0   100.0     2  java
# 03:45:13 PM  1000     28503    0.0    1.0    0.0    0.0     1.0     0  pidstat

# top in batch mode for records
top -b -n 1 -o %CPU | head -20

# Which PID specifically on CPU 3 at 100%?
ps -eo pid,psr,pcpu,comm --sort=-pcpu | head -10
# psr = which CPU core the process is on

# For Java processes: identify the thread
top -H -p PID -o %CPU
# Show individual threads within a process
```

### Safe Kill Procedure

```bash
# Step 1: Identify the process
PID=28471

# Step 2: Check what it is
ps -fp $PID
# UID   PID   PPID   C  STIME  TTY   TIME     CMD
# user 28471 12345  98  14:23  ?     2:34:12  /usr/bin/java -jar app.jar

# Step 3: SIGTERM (15) — ask nicely, allows cleanup (closing connections, flushing buffers, deleting temp files)
kill -15 $PID
# or equivalently: kill $PID

# Step 4: Wait and check
sleep 5
if kill -0 $PID 2>/dev/null; then
  echo "Process $PID still alive after SIGTERM"

  # Step 5: SIGKILL (9) — LAST RESORT, no cleanup, may corrupt data
  # NEVER use kill -9 as the default. Always try SIGTERM first.
  kill -9 $PID
  echo "Process $PID killed with SIGKILL"
else
  echo "Process $PID terminated gracefully"
fi

# What happens between SIGTERM and SIGKILL:
# - SIGTERM is caught by the app. It runs shutdown hooks: close DB pools, flush logs,
#   finish in-flight requests, persist state.
# - If the app is stuck (deadlocked, infinite loop, reading from a hung NFS mount),
#   SIGTERM is delivered but the signal handler never runs because the process
#   is in uninterruptible sleep (D state). Kill -9 won't help either — only remounting
#   the NFS volume or rebooting will fix a D-state stuck process.

# For a process in D state (stuck in kernel):
ps -eo pid,state,wchan,comm | awk '$2=="D"'
# wchan shows what kernel function the process is stuck in
# Common: 'nfs_wait_on_rpc' (NFS hang), 'blk_mq_submit_bio' (storage hang)
# These processes CANNOT be killed with any signal. Reboot is the only fix.
```

### Kill All Instances of a Process

```bash
# Kill all java processes (careful!)
pkill -15 -f "java.*app.jar"
# -15 = SIGTERM, -f = match full command line

# Never do killall -9 without a filter
# killall -9 java  # DON'T — kills ALL java processes including Jenkins, elasticsearch, etc.

# Use pidof to be precise
PID=$(pidof -x /usr/local/bin/myapp)
kill -15 $PID

# If the process has children:
# Send signal to the process group (PGID = PID of the leader)
kill -15 -- -$(ps -o pgid= -p $PID | tr -d ' ')
# The leading '-' means "process group"
```

---

## 7. CPU Affinity and NUMA

### CPU Affinity with taskset

```bash
# Check current affinity of a process
taskset -cp $PID
# pid 28471's current affinity list: 0-7
# (process can run on any of CPUs 0 through 7)

# Pin a process to specific CPUs
taskset -cp 0-3 $PID
# Now the process is restricted to CPUs 0,1,2,3

# Launch a new process with affinity
taskset -c 0,2,4,6 my_application
# Only run on even-numbered CPUs

# Why would you do this?
# 1. Cache locality: pin a latency-sensitive process to specific cores,
#    reducing cache misses from migrating between cores
# 2. Isolate noisy neighbors: give the latency-critical app dedicated cores
#    and run batch jobs on other cores
# 3. NUMA optimization: pin a process to CPUs on the same NUMA node
#    as its memory (see below)

# Check process's current CPU (psr field)
ps -eo pid,psr,comm | grep $PID
# Which physical core is the process on right now?
# If psr changes between query runs, the process is migrating (no affinity)
```

### NUMA Awareness

```bash
# View NUMA topology
numactl --hardware
# available: 2 nodes (0-1)
# node 0 cpus: 0 1 2 3 4 5 6 7
# node 0 size: 64357 MB
# node 0 free: 23141 MB
# node 1 cpus: 8 9 10 11 12 13 14 15
# node 1 size: 64473 MB
# node 1 free: 28921 MB
# node distances:
# node   0   1
#   0:  10  21   <-- accessing node 1's memory from node 0 costs 2.1x
#   1:  21  10

# NUMA statistics
numastat
# Per-node memory allocation stats

# Run a process pinned to a NUMA node
numactl --cpunodebind=0 --membind=0 myapp
# Both CPUs and memory on node 0 — optimal for cache locality

# Check current NUMA allocation for a running process
numastat -p $PID
# See which NUMA node the process's memory pages are on
# If a process is running on node 0 CPUs but has lots of memory on node 1:
# move it:  numactl --membind=0 --cpunodebind=0 -p $PID nop  (requires restart)

# PostgreSQL NUMA example:
# Without NUMA tuning, Postgres might allocate shared_buffers from node 0
# but the process doing the query runs on node 1 — so every buffer access
# goes over the slow inter-core interconnect.
#
# Fix:  numactl --interleave=all postgres -D /data
# interleave=all distributes memory across both nodes, so average access
# time is consistent regardless of which core runs the query.
```

### Isolating CPUs from the Kernel

```bash
# For Ultra-Low-Latency Workloads (trading, real-time processing):
# Bool CPU 4-7 from OS scheduler. Kernel won't schedule anything on them.

# Check current isolated CPUs
cat /sys/devices/system/cpu/isolated

# Set at boot time (GRUB):
# GRUB_CMDLINE_LINUX="isolcpus=4-7 nohz_full=4-7 rcu_nocbs=4-7"
# Then: update-grub && reboot

# Then pin your latency-sensitive process to those cores:
taskset -cp 4-7 $PID

# Warning: if the process doesn't use those CPUs, they sit idle.
# This is infrastructure overhead you're paying for.
```

---

## 8. perf Profiling

### perf top — Live Hotspot Analysis

```bash
# Live view: what functions are eating CPU right now
perf top

# For a specific process
perf top -p $PID

# In callgraph mode (shows who called what)
perf top -g

# Breakdown:
# Overhead  Shared Object          Symbol
#   32.4%   libc-2.31.so          [.] __strcmp_avx2
#   18.1%   python3.10            [.] _PyEval_EvalFrameDefault
#   10.2%   app.so                [.] process_request
#
# Here, 32.4% of CPU samples hit __strcmp_avx2 — the app is doing too many string comparisons

# Filter to only user-space (exclude kernel)
perf top -e cycles:u -p $PID
```

### perf record — Profile, Then Analyze

```bash
# Record CPU profile for 30 seconds on a specific PID
perf record -g -p $PID -- sleep 30

# Record for ALL CPUs (for system-wide analysis)
perf record -g -a -- sleep 30

# Record with callgraph depth
perf record -g --call-graph dwarf -p $PID -- sleep 30
# dwarf = use DWARF debug info for accurate callgraphs (needs -g compiled binary)
# fp = frame pointer (fast, less accurate, needs -fno-omit-frame-pointer)

# After recording, analyze
perf report

# TUI commands within perf report:
#   /       — search for a symbol
#   +       — expand callchain
#   Enter   — annotate (see asm with source lines)
#   a       — annotate current symbol

# Generate flamegraph data
perf record -g -p $PID -- sleep 30
perf script > out.perf
# Then use FlameGraph tools:
git clone https://github.com/brendangregg/FlameGraph
./FlameGraph/stackcollapse-perf.pl out.perf > out.folded
./FlameGraph/flamegraph.pl out.folded > cpu-flamegraph.svg

# Count events (for finding hot CPUs)
perf stat -p $PID -- sleep 10
# Sample output:
#  Performance counter stats for process id '28471':
#      12,345.67 msec cpu-clock          # 1.235 CPUs utilized
#         23,456      context-switches    # 1.900 K/sec
#          1,234      cpu-migrations      # 99.9 /sec
#             45      page-faults         # 3.6 /sec
#      7,890,123,456  cycles              # 0.639 GHz
#      5,678,901,234  instructions        # 0.72  insn per cycle
#        <lots of cache-miss stats>
```

### perf for Production (Low Overhead)

```bash
# perf has very low overhead (unlike strace).
# Safe for production use on critical systems (sampling, not instrumenting):

# Safe profiling on production
perf record -g -F 99 -p $PID -- sleep 30
# -F 99 = sample at 99 Hz (99 times per second per CPU) — low overhead
# -F 999 if you need higher resolution for sub-millisecond functions

# Check if perf_event_paranoid allows profiling:
cat /proc/sys/kernel/perf_event_paranoid
# -1: no restrictions (not recommended)
#  0: allow raw tracepoint access
#  1: allow CPU and tracepoint access (default on many distros)
#  2: allow user-space profiling only (safe for unprivileged users)
# If you get "permission denied", you may need to be root or adjust perf_event_paranoid
```

---

## 9. Python: CPU Monitoring Script

```python
#!/usr/bin/env python3
"""
cpu-monitor.py — per-process CPU usage monitor with alerting
Monitors processes > THRESHOLD% CPU for longer than DURATION seconds.
"""

import psutil
import time
import smtplib
import json
import os
from datetime import datetime
from email.message import EmailMessage
from collections import defaultdict

# --- Configuration ---
CPU_THRESHOLD = 80.0           # Percentage — alert if process exceeds this
DURATION_THRESHOLD = 60         # Seconds — alert if sustained beyond this
POLL_INTERVAL = 5               # Seconds between checks
ALERT_EMAIL = "sre-alerts@example.com"
SMTP_SERVER = "smtp.internal.example.com"
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")  # Optional Slack/PagerDuty webhook
# --------------------

class CPUMonitor:
    def __init__(self):
        self.violations: dict[int, dict] = {}  # pid -> {start_time, max_cpu, name}
        self.alerted: set[int] = set()

    def _send_alert(self, pid: int, name: str, cpu_pct: float, duration: float):
        msg = EmailMessage()
        msg["Subject"] = f"[CPU ALERT] {name} (PID {pid}) at {cpu_pct:.1f}% for {duration:.0f}s"
        msg["From"] = f"cpu-monitor@{os.uname().nodename}"
        msg["To"] = ALERT_EMAIL
        msg.set_content(f"""
High CPU Alert
==============
Host:      {os.uname().nodename}
Process:   {name}
PID:       {pid}
CPU Usage: {cpu_pct:.1f}%
Duration:  {duration:.0f} seconds
Timestamp: {datetime.now().isoformat()}

Top 5 CPU processes:
{self._top_cpu_processes()}
""")
        try:
            with smtplib.SMTP(SMTP_SERVER, 25, timeout=10) as smtp:
                smtp.send_message(msg)
        except Exception as e:
            print(f"Failed to send email: {e}")

        if WEBHOOK_URL:
            try:
                import urllib.request
                payload = json.dumps({
                    "text": f"[CPU ALERT] {os.uname().nodename}: {name} (PID {pid}) "
                            f"at {cpu_pct:.1f}% for {duration:.0f}s"
                }).encode()
                req = urllib.request.Request(WEBHOOK_URL, data=payload,
                    headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=5)
            except Exception as e:
                print(f"Failed to send webhook: {e}")

    def _top_cpu_processes(self) -> str:
        lines = []
        for proc in sorted(psutil.process_iter(['pid', 'name', 'cpu_percent']),
                           key=lambda p: p.info['cpu_percent'] or 0, reverse=True)[:5]:
            lines.append(f"  PID {proc.info['pid']:6d}  {proc.info['cpu_percent']:5.1f}%  {proc.info['name']}")
        return "\n".join(lines)

    def run(self):
        print(f"[{datetime.now().isoformat()}] CPU Monitor starting on {os.uname().nodename}")
        print(f"  Threshold: >{CPU_THRESHOLD}% for >{DURATION_THRESHOLD}s")
        print(f"  Poll interval: {POLL_INTERVAL}s")
        print()

        while True:
            try:
                current_pids = set()
                cpu_percentages = {}

                # Use cpu_percent with interval for accurate measurement
                for proc in psutil.process_iter(['pid', 'name', 'cpu_percent']):
                    try:
                        pid = proc.info['pid']
                        name = proc.info['name']
                        cpu = proc.info['cpu_percent']
                        current_pids.add(pid)

                        if cpu and cpu > CPU_THRESHOLD:
                            if pid not in self.violations:
                                self.violations[pid] = {
                                    'start': time.time(),
                                    'name': name,
                                    'max_cpu': cpu,
                                }
                            else:
                                self.violations[pid]['max_cpu'] = max(
                                    self.violations[pid]['max_cpu'], cpu
                                )

                            duration = time.time() - self.violations[pid]['start']
                            if duration > DURATION_THRESHOLD and pid not in self.alerted:
                                print(f"  [ALERT] PID {pid} ({name}): {cpu:.1f}% for {duration:.0f}s")
                                self._send_alert(pid, name, cpu, duration)
                                self.alerted.add(pid)

                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue

                # Clean up violations for processes that dropped below threshold
                for pid in list(self.violations.keys()):
                    if pid not in current_pids:
                        del self.violations[pid]
                        self.alerted.discard(pid)

                # Print status every 60s
                if int(time.time()) % 60 < POLL_INTERVAL:
                    cpu_pct = psutil.cpu_percent(interval=0)
                    load = os.getloadavg()
                    print(f"  [{datetime.now().strftime('%H:%M:%S')}] "
                          f"CPU={cpu_pct:.1f}% | load={load[0]:.2f}/{load[1]:.2f}/{load[2]:.2f} | "
                          f"violations={len(self.violations)}")

            except Exception as e:
                print(f"  [ERROR] {e}")

            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    CPUMonitor().run()
```

### Using psutil Without the Script

```python
# Quick in-Python-process CPU check (for embedding in your app):
import psutil
import os

pid = os.getpid()
proc = psutil.Process(pid)

# Current CPU % for this process (first call returns 0, subsequent calls accurate)
cpu_pct = proc.cpu_percent(interval=1)
print(f"My CPU usage: {cpu_pct:.1f}%")

# System-wide CPU
sys_cpu = psutil.cpu_percent(interval=1, percpu=True)
for i, pct in enumerate(sys_cpu):
    print(f"CPU {i}: {pct:.1f}%")

# Load average
load1, load5, load15 = psutil.getloadavg()
ncpus = psutil.cpu_count()
print(f"Load: {load1:.2f} (1m) / CPUs: {ncpus} = {load1/ncpus:.2f} ratio")

# Memory used by this process
mem = proc.memory_info()
print(f"RSS: {mem.rss / 1024 / 1024:.1f} MB")
```

---

## 10. Java: CPU Profiling

### Thread Dump Analysis (jstack)

```bash
# Get the Java PID
jps -l
# 28471 com.example.App
# 28503 jdk.jcmd/sun.tools.jps.Jps

JAVA_PID=28471

# Thread dump — capture current state of all threads
jstack $JAVA_PID > threaddump.txt

# jstack output format:
# "pool-1-thread-5" #28 daemon prio=5 os_prio=0 cpu=1234.56ms elapsed=3600.23s
#   java.lang.Thread.State: RUNNABLE
#     at com.example.service.OrderProcessor.processOrder(OrderProcessor.java:47)
#     at com.example.service.OrderProcessor$$Lambda$342/0x0000000800.run(Unknown Source)
#     ...
#   Locked ownable synchronizers:
#     - <0x00000007c1a2b3c0> (a java.util.concurrent.ThreadPoolExecutor$Worker)
#
# Note the "cpu=1234.56ms" — cumulative CPU time for this thread.
# Compare before/after to see which threads are burning CPU:

# Capture thread dumps every 5 seconds, 3 times
for i in 1 2 3; do
  jstack $JAVA_PID >> /tmp/threaddumps.txt
  echo "=== SNAPSHOT $i @ $(date) ===" >> /tmp/threaddumps.txt
  sleep 5
done

# Find the busiest threads by comparing cpu= values across snapshots
grep "cpu=" /tmp/threaddumps.txt | awk -F'[=ms]' '{print $2, $0}' | sort -rn | head -10

# Alternative: capture the top N threads by CPU first
top -H -b -n 1 -p $JAVA_PID | head -20
# Convert OS thread ID (decimal) to jstack's nid (hex):
# printf "%x\n" 28475
# Then grep threaddump for "nid=0x6f3b"
```

### async-profiler — The Gold Standard for Java CPU Profiling

```bash
# Download: https://github.com/async-profiler/async-profiler/releases
# No JVM restart needed, no perf_event_paranoid changes for most modes

# CPU profiling — 30 second profile, output as flamegraph
./profiler.sh -d 30 -f /tmp/cpu-flamegraph.html $JAVA_PID

# Allocations profiling — find memory allocation hot spots
./profiler.sh -e alloc -d 30 -f /tmp/alloc-flamegraph.html $JAVA_PID

# Wall-clock profiling — where is time actually spent (including blocking)
./profiler.sh -e wall -d 30 -f /tmp/wall-flamegraph.html $JAVA_PID

# Lock contention profiling
./profiler.sh -e lock -d 30 -f /tmp/lock-flamegraph.html $JAVA_PID

# JFR-compatible output (view in JDK Mission Control)
./profiler.sh -d 60 -o jfr -f /tmp/profile.jfr $JAVA_PID

# Specific options:
# -t          threads: profile threads separately
# -e cpu      event to profile (cpu, alloc, lock, wall, itimer)
# -i 1ms      sampling interval (1 millisecond)
# --cstack fp use frame pointers for C stack (kernel/libs)

# Filter to specific threads or method patterns
./profiler.sh -d 30 --include 'com.mycompany.*' --exclude 'java.*,javax.*' -f flamegraph.html $JAVA_PID
```

### JVM Built-in CPU Monitoring

```bash
# jstat — JVM statistics (gc, compilation, class loading)
jstat -gcutil $JAVA_PID 1000 10
# Sample every 1 second, 10 samples
#   S0     S1     E      O      M     CCS    YGC     YGCT    FGC    FGCT     CGC    CGCT     GCT
#   0.00  96.25  45.32  12.84  94.12  89.34   1234  56.789    12   2.345    5      1.234  60.368
#
# YGC = young gen collections (should be frequent but short)
# FGC = full GC collections (should be RARE — if increasing, memory leak or under-sized heap)
# GCT = total GC time in seconds

# jcmd — the swiss army knife
jcmd $JAVA_PID help                               # list all available commands
jcmd $JAVA_PID VM.flags                           # all JVM flags
jcmd $JAVA_PID Thread.print                       # thread dump (same as jstack)
jcmd $JAVA_PID GC.heap_dump /tmp/heap.hprof       # heap dump
jcmd $JAVA_PID VM.system_properties               # system properties
jcmd $JAVA_PID GC.class_histogram                 # class histogram (shallow)
jcmd $JAVA_PID VM.native_memory summary           # native memory tracking (needs -XX:NativeMemoryTracking=summary at startup)

# JVM startup with CPU monitoring flags
java \
  -XX:+PrintCompilation \
  -XX:+CITime \
  -XX:+UnlockDiagnosticVMOptions \
  -XX:+PrintInlining \
  -XX:+LogCompilation \
  -jar app.jar
# PrintCompilation: shows JIT compilation events (helps identify code that's
# being compiled/decompiled — "made not entrant" = bad for CPU)
```

### Java Code: In-Process CPU Monitoring

```java
import java.lang.management.ManagementFactory;
import java.lang.management.OperatingSystemMXBean;
import java.lang.management.ThreadInfo;
import java.lang.management.ThreadMXBean;
import com.sun.management.OperatingSystemMXBean;

public class CPUMonitor {

    public static void monitorCPU() {
        OperatingSystemMXBean osBean =
            (OperatingSystemMXBean) ManagementFactory.getOperatingSystemMXBean();
        ThreadMXBean threadBean = ManagementFactory.getThreadMXBean();
        threadBean.setThreadCpuTimeEnabled(true);

        int availableProcessors = Runtime.getRuntime().availableProcessors();
        double processCpuTime = osBean.getProcessCpuTime();  // nanoseconds

        // CPU load for the current JVM process (0.0 - availableProcessors)
        double processCpuLoad = osBean.getProcessCpuLoad();
        // System-wide CPU load (0.0 - 1.0 per core)
        double systemCpuLoad = osBean.getSystemCpuLoad();

        double processCpuPct = processCpuLoad * 100 / availableProcessors;
        double systemCpuPct = systemCpuLoad * 100;

        System.out.printf("JVM CPU: %.1f%%, System CPU: %.1f%%, Cores: %d%n",
            processCpuPct, systemCpuPct, availableProcessors);

        // Find threads consuming the most CPU
        long[] threadIds = threadBean.getAllThreadIds();
        ThreadInfo[] threadInfos = threadBean.getThreadInfo(threadIds);

        System.out.println("Top CPU-consuming threads:");
        for (ThreadInfo info : threadInfos) {
            if (info == null) continue;
            long cpuNs = threadBean.getThreadCpuTime(info.getThreadId());
            double cpuMs = cpuNs / 1_000_000.0;
            if (cpuMs > 1000) {  // only show threads with >1s of CPU time
                System.out.printf("  %-50s CPU time: %.1f ms%n",
                    info.getThreadName(), cpuMs);
            }
        }
    }

    public static void main(String[] args) throws InterruptedException {
        while (true) {
            monitorCPU();
            Thread.sleep(5000);
        }
    }
}
```

---

## 11. JS/Node: CPU Analysis

### Node.js Built-in Profiler

```bash
# Profile your Node.js app
node --prof app.js
# Generates: isolate-0xNNNNNNNNNNNN-v8.log

# After the run, process the profile
node --prof-process isolate-0xNNNNNNNNNNNN-v8.log > processed-profile.txt

# Read the output:
# [Summary]:
#    ticks  total  nonlib   name
#    1234    45.2%   48.3%  JavaScript
#     876    32.1%   34.2%  C++
#     345    12.6%   13.5%  GC
#
# [JavaScript]:
#    ticks  total  nonlib   name
#     234    8.6%    9.2%    LazyCompile: *processOrder /app/service.js:45
#     167    6.1%    6.5%    LazyCompile: *validateInput /app/middleware.js:12
#     134    4.9%    5.2%    LazyCompile: ~parseJSON native json.js
#
# The LazyCompile entries show WHERE in your JS the CPU time is spent.
# * means optimized (TurboFan), ~ means not yet optimized, (blank) means interpreted.

# Profile with a specific sampling interval (microseconds)
node --prof --prof_sampling_interval=100 app.js
# Default: 1000us. Lower = more detail, slightly more overhead.
```

### Clinic.js — Production-Grade Node.js Profiling

```bash
# Install
npm install -g clinic

# CPU profiling with flamegraph output
clinic doctor --on-port 'autocannon -d 30 localhost:3000' -- node app.js
# Opens a browser with flamegraph + CPU usage chart + GC analysis

# Event loop lag detection
clinic bubbleprof --on-port 'autocannon -d 30 localhost:3000' -- node app.js
# Shows async operation latency — great for finding slow I/O blocking the event loop

# Heap profiling
clinic heapprofiler --on-port 'autocannon -d 30 localhost:3000' -- node app.js

# What clinic doctor shows:
# - CPU usage over time (identify spikes)
# - Flamegraph of top functions
# - Event loop delay (when the event loop is blocked)
# - GC pauses and their duration
# - Active handle count
```

### Node.js Process-Level Monitoring

```javascript
// cpu-collector.js — embed in your app or run as a sidecar
const v8 = require('v8');
const os = require('os');
const fs = require('fs');

class CPUCollector {
    constructor(opts = {}) {
        this.intervalMs = opts.intervalMs || 5000;
        this.thresholdPct = opts.thresholdPct || 80;
        this.lastCPUTimes = process.cpuUsage();
        this.samples = [];
    }

    collect() {
        const cpuUsage = process.cpuUsage(this.lastCPUTimes);
        this.lastCPUTimes = process.cpuUsage();

        // CPU usage in microseconds, convert to percent of one core
        const elapsedUs = this.intervalMs * 1000;
        const cpuPct = ((cpuUsage.user + cpuUsage.system) / elapsedUs) * 100;

        const memUsage = process.memoryUsage();
        const heapStats = v8.getHeapStatistics();

        const sample = {
            timestamp: new Date().toISOString(),
            cpuPct: cpuPct.toFixed(2),
            cpuUser: cpuUsage.user,
            cpuSystem: cpuUsage.system,
            pid: process.pid,
            uptime: process.uptime(),
            memoryRSS: (memUsage.rss / 1024 / 1024).toFixed(1),
            heapUsed: (memUsage.heapUsed / 1024 / 1024).toFixed(1),
            heapTotal: (memUsage.heapTotal / 1024 / 1024).toFixed(1),
            eventLoopDelay: null,  // populated below
            activeHandles: process._getActiveHandles?.()?.length ?? 0,
            activeRequests: process._getActiveRequests?.()?.length ?? 0,
            loadAvg: os.loadavg(),
            freeMem: os.freemem(),
            totalMem: os.totalmem(),
        };

        this.samples.push(sample);
        if (this.samples.length > 7200) this.samples.shift(); // 10h at 5s interval

        if (Number(cpuPct) > this.thresholdPct) {
            this.alert(sample);
        }

        return sample;
    }

    alert(sample) {
        const alert = {
            type: 'HIGH_CPU',
            severity: 'warning',
            ...sample,
            stack: new Error().stack, // capture call stack snapshot
        };
        console.error('[CPU_ALERT]', JSON.stringify(alert));

        // Dump a quick CPU profile when threshold is breached
        this.writeCPUProfile();
    }

    writeCPUProfile() {
        const profilePath = `/tmp/node-cpu-profile-${process.pid}-${Date.now()}.cpuprofile`;
        // Note: this requires --inspect flag or inspector module
        try {
            const inspector = require('inspector');
            if (inspector.url()) {
                const session = new inspector.Session();
                session.connect();
                session.post('Profiler.enable');
                session.post('Profiler.start');
                setTimeout(() => {
                    session.post('Profiler.stop', (err, { profile }) => {
                        fs.writeFileSync(profilePath, JSON.stringify(profile));
                        console.error(`CPU profile written to ${profilePath}`);
                        session.disconnect();
                    });
                }, 5000);
            }
        } catch (e) {
            // inspector not available
        }
    }

    start() {
        this._timer = setInterval(() => this.collect(), this.intervalMs);
        console.log(`CPU Collector started — interval=${this.intervalMs}ms, threshold=${this.thresholdPct}%`);
    }

    stop() {
        clearInterval(this._timer);
    }

    // Summarize collected data
    summary() {
        if (this.samples.length === 0) return {};

        const cpus = this.samples.map(s => Number(s.cpuPct));
        const sorted = [...cpus].sort((a, b) => a - b);

        return {
            count: cpus.length,
            p50: sorted[Math.floor(sorted.length * 0.5)],
            p95: sorted[Math.floor(sorted.length * 0.95)],
            p99: sorted[Math.floor(sorted.length * 0.99)],
            max: sorted[sorted.length - 1],
            min: sorted[0],
            avg: (cpus.reduce((a, b) => a + b, 0) / cpus.length),
        };
    }
}

// Usage
const collector = new CPUCollector({ intervalMs: 5000, thresholdPct: 80 });
collector.start();

// On shutdown
process.on('SIGTERM', () => {
    console.log('CPU Summary:', JSON.stringify(collector.summary()));
    collector.stop();
    process.exit(0);
});

module.exports = CPUCollector;
```

### Blocked Event Loop Detection

```javascript
// Detect when the event loop is blocked (CPU-bound synchronous work)
function monitorEventLoopDelay(maxDelayMs = 100, callback) {
    let lastCheck = process.hrtime.bigint();

    setInterval(() => {
        const now = process.hrtime.bigint();
        const delta = Number(now - lastCheck) / 1_000_000; // ns to ms
        lastCheck = now;

        if (delta > maxDelayMs) {
            console.error(`[EVENT_LOOP_BLOCKED] expected ~1000ms interval, got ${delta.toFixed(1)}ms`);
            if (callback) callback(delta);
        }
    }, 1000);
}

monitorEventLoopDelay(200, (delay) => {
    // Log to monitoring, trigger alert
    console.error(`Event loop was blocked for ${delay}ms`);
});
```
