# CPU Fundamentals — What Those Numbers Actually Mean

> **Category:** Linux | CPU | Hardware | Fundamentals
> **Difficulty:** Basic
> **Last Reviewed:** 2026-06
> **Tags:** `#cpu` `#hardware` `#fundamentals` `#cores` `#scheduling`

---

## Table of Contents

1. [What Is a CPU?](#what-is-a-cpu)
2. [Cores, Threads, and Sockets](#cores-threads-and-sockets)
3. [How Apps Use CPU](#how-apps-use-cpu)
4. [CPU-Bound vs I/O-Bound Workloads](#cpu-bound-vs-io-bound-workloads)
5. [Reading CPU Numbers: The Universal Percentage Problem](#reading-cpu-numbers-the-universal-percentage-problem)
6. [mpstat Column-by-Column Meaning](#mpstat-column-by-column-meaning)
7. [Core-Level vs Socket-Level vs Process-Level](#core-level-vs-socket-level-vs-process-level)
8. [CPU Time Accounting](#cpu-time-accounting)

---

## What Is a CPU?

The CPU (Central Processing Unit) is the physical chip that executes instructions. Every line of code you write ultimately becomes a stream of instructions the CPU fetches, decodes, and executes — billions of times per second.

At the hardware level, a modern CPU is a silicon die with billions of transistors, organized into functional blocks:

```text
┌─────────────────────────────────────────────┐
│                  CPU Socket                  │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────┐│
│  │ Core 0  │ │ Core 1  │ │ Core 2  │ │ ... ││
│  │  ┌────┐ │ │  ┌────┐ │ │  ┌────┐ │ │     ││
│  │  │ALU │ │ │  │ALU │ │ │  │ALU │ │ │     ││
│  │  │FPU │ │ │  │FPU │ │ │  │FPU │ │ │     ││
│  │  │L1$ │ │ │  │L1$ │ │ │  │L1$ │ │ │     ││
│  │  └────┘ │ │  └────┘ │ │  └────┘ │ │     ││
│  └─────────┘ └─────────┘ └─────────┘ └─────┘│
│  ┌──────────────────────────────────────┐    │
│  │              L3 Cache (shared)       │    │
│  └──────────────────────────────────────┘    │
│  ┌──────────────────────────────────────┐    │
│  │      Memory Controller / IMC         │────┼──→ RAM
│  └──────────────────────────────────────┘    │
│  ┌──────────────────────────────────────┐    │
│  │      PCIe / Interconnect             │────┼──→ Other sockets, devices
│  └──────────────────────────────────────┘    │
└─────────────────────────────────────────────┘
```

Each **core** is an independent execution unit that runs one instruction stream at a time. The CPU's job is dead simple: fetch the next instruction from memory, decode what it means, execute it, write the result back — repeat forever.

---

## Cores, Threads, and Sockets

### The Physical Hierarchy

```text
Server / Machine
├── Socket 0 (physical CPU chip)
│   ├── Core 0
│   │   ├── Thread 0 (logical CPU)   ← what Linux shows as "cpu0"
│   │   └── Thread 1 (logical CPU)   ← what Linux shows as "cpu4" (HT sibling)
│   ├── Core 1
│   │   ├── Thread 0                 ← "cpu1"
│   │   └── Thread 1                 ← "cpu5"
│   ├── Core 2 → Thread 0, Thread 1 (cpu2, cpu6)
│   └── Core 3 → Thread 0, Thread 1 (cpu3, cpu7)
└── Socket 1 (second physical CPU chip)
    ├── Core 4 → Thread 0, Thread 1 (cpu8, cpu12)
    ├── Core 5 → Thread 0, Thread 1 (cpu9, cpu13)
    ├── Core 6 → Thread 0, Thread 1 (cpu10, cpu14)
    └── Core 7 → Thread 0, Thread 1 (cpu11, cpu15)
```

### Key Distinctions

| Term | What It Is | What `top`/`htop` Shows |
|------|-----------|--------------------------|
| **Socket** | Physical chip plugged into the motherboard | Not directly shown. `lscpu \| grep Socket` tells you how many. |
| **Core** | Independent execution pipeline inside a socket | `htop` bars = 1 bar per core (pairs threads) |
| **Thread (vCPU)** | Logical CPU visible to the OS. 2 threads/core with HT. | Each bar in `htop` represents one logical CPU. `cpu0`, `cpu1`, ..., `cpuN` |
| **Hyper-Threading (SMT)** | Intel's name for 2 threads per core. AMD calls it SMT. | 4-core HT CPU = 8 logical CPUs (`cpu0`–`cpu7`) |

### View Your Hardware

```bash
# Full CPU topology
lscpu

# Key output lines:
# Architecture:        x86_64
# CPU(s):              16             ← total logical CPUs the OS sees
# Thread(s) per core:  2              ← Hyper-Threading enabled
# Core(s) per socket:  8              ← physical cores per chip
# Socket(s):           1              ← physical CPU chips
# NUMA node(s):        1

# The equation:
# CPUs = Threads/core × Cores/socket × Sockets
#   16 = 2 × 8 × 1

# Which logical CPUs share the same physical core?
cat /sys/devices/system/cpu/cpu*/topology/thread_siblings_list
# cpu0: 0,4    ← cpu0 and cpu4 are HT siblings on the same core
# cpu1: 1,5    ← cpu1 and cpu5 share core 1
# cpu4: 0,4    ← same physical core as cpu0
```

### Hyper-Threading: The 30% Boost

Hyper-Threading (HT) lets one physical core run two instruction streams simultaneously. It does NOT double your cores — a single core's execution units (ALU, FPU, etc.) are shared between the two threads. If both threads need the same execution unit at the same time, one waits.

```text
Real-world throughput gain from HT:
  CPU-heavy number crunching:   0–10%   (execution units are the bottleneck)
  Mixed workloads (web, DB):    15–30%  (one thread often stalled on memory)
  I/O-heavy workloads:          30–40%  (both threads stall on I/O, core is underused)
```

**Key insight for SREs**: When `top` shows 50% CPU on an HT-enabled system, that could mean:
- One thread at 100% and its HT sibling idle (1 core saturated)
- All threads at 25% (genuinely half-loaded)
- Something in between

---

## How Apps Use CPU

### What "Using CPU" Means for Your Application

Every operation your application performs costs CPU cycles:

| Operation | Approximate Cost | CPU-Intensive? |
|-----------|-----------------|----------------|
| **Arithmetic** (`a + b`, `x * y`) | 1–4 cycles | No |
| **Function call** | 5–20 cycles | No |
| **Memory access (L1 cache hit)** | 4–5 cycles | No |
| **Memory access (L3 cache hit)** | 40–50 cycles | Slightly |
| **Memory access (RAM)** | 100–300 cycles | Yes (stall) |
| **JSON parse (1KB)** | ~5,000 cycles | Moderate |
| **TLS handshake (RSA 2048)** | ~5,000,000 cycles | Yes |
| **Image resize (1080p)** | ~50,000,000 cycles | Yes |
| **ML inference (small model)** | ~1,000,000,000 cycles | Very much so |

### Where Your App's CPU Time Goes

```text
┌─────────────────────────────────────────────────┐
│               Where CPU Cycles Go               │
│                                                 │
│  Application logic:  ████████░░░░░░░  40%       │
│  JSON/Protobuf serde: ███░░░░░░░░░░░  15%       │
│  Memory mgmt / GC:   ██░░░░░░░░░░░░  10%       │
│  Kernel / syscalls:  ██░░░░░░░░░░░░  10%       │
│  Waiting on RAM:     █████░░░░░░░░░  25%  ←┐   │
│                                                 │ ← This is often the real issue
│  (Waiting on RAM isn't "using" CPU — it's      │
│   CPU cycles where the core is STALLED.         │
│   They show up as %usr or %sys, not %iowait.)   │
└─────────────────────────────────────────────────┘
```

### CPU-Intensive Application Types

| Application Type | Typical CPU Profile | Why |
|-----------------|-------------------|-----|
| **Video transcoding** | 90–100% on all cores | Pure computation: encoding, decoding, filters |
| **Machine learning training** | 90–100% on GPU + 20–50% on CPU | Matrix ops on GPU, data prep on CPU |
| **Image processing** | 70–90% on multiple cores | Pixel manipulation, resizing, compression |
| **Scientific computing** | 80–100% on all cores | Floating point math, simulations |
| **Crypto / blockchain** | 100% on all cores | Hashing, signature verification |
| **Web servers (high RPS)** | 30–70% spread across cores | Request parsing, routing, serialization |
| **Database query execution** | 20–80% on select cores | Index scans, sorting, aggregation |
| **Compression (gzip/zstd)** | 80–100% on 1–4 cores | Dictionary lookups, entropy coding |
| **Compilation (GCC/Clang)** | 80–100% on all cores | Parsing, optimization passes, code gen |

### CPU-Light Application Types

| Application Type | Typical CPU Profile | Why |
|-----------------|-------------------|-----|
| **Static file server** | 1–5% | Sends cached data from disk/memory |
| **Proxy / load balancer** | 5–15% | Copies bytes between sockets |
| **CRUD API (simple)** | 5–20% | Validates input, queries DB, returns JSON |
| **Message queue consumer** | 5–15% | Reads from queue, writes to DB, acks |
| **Cron job (hourly)** | Spike to 50%, then 0% | Runs batch work, then sleeps |
| **Monitoring agent** | 1–3% steady | Samples metrics, ships to collector |

---

## CPU-Bound vs I/O-Bound Workloads

### Visual Difference

```text
CPU-Bound:  Core is always executing instructions.
            Adding more cores helps. Faster CPU helps.

    Core ████████████████████████████████████████  (100% busy)
         ← no gaps →


I/O-Bound:  Core executes a bit, then waits for disk/network.
            Adding more cores helps LESS. Faster I/O helps MORE.

    Core ██░░░░░░░██░░░░░░████░░░░░░░░█████░░░░░░░  (30% busy)
         ← gaps are waiting for I/O →
         (These gaps show as %iowait if no other work)
```

### How to Know Which You Have

```bash
# CPU-Bound: high %usr, low %iowait, all cores active
mpstat 1 5
# CPU  %usr  %sys  %iowait  %steal  %idle
# all  85.2   8.1    0.0      0.0     6.7     ← CPU-bound

# I/O-Bound: moderate %usr, high %iowait, some idle
mpstat 1 5
# CPU  %usr  %sys  %iowait  %steal  %idle
# all  15.3   5.2   42.1      0.0    37.4     ← I/O-bound (42% iowait!)

# Mixed: one core CPU-heavy, others varying
mpstat -P ALL 1 1
# CPU 0:  %usr=95,  %iowait=0   ← single-threaded CPU bottleneck
# CPU 1:  %usr=15,  %iowait=0   ← helper thread
# CPU 2:  %usr=5,   %iowait=60  ← I/O worker
# CPU 3:  %usr=2,   %iowait=0   ← mostly idle
```

### The Scaling Tactic Changes Completely

| If Your Workload Is... | Scale By... | Why |
|------------------------|-------------|-----|
| **CPU-Bound, multi-threaded** | Adding cores / machines | More cores = more work done in parallel |
| **CPU-Bound, single-threaded** | Faster CPU (higher GHz) or refactoring | More cores don't help a single-threaded loop |
| **I/O-Bound, disk** | Faster disks (NVMe) or more IOPS | CPU is waiting; faster I/O reduces wait time |
| **I/O-Bound, network** | Reduce latency (CDN, connection pooling) | CPU waiting on remote responses |

---

## Reading CPU Numbers: The Universal Percentage Problem

### The 100% = 1 Core Rule

Every CPU monitoring tool reports usage as a percentage of **one logical CPU**:

```text
100%  = ONE logical CPU fully saturated for the measurement interval
200%  = TWO logical CPUs fully saturated
800%  = EIGHT logical CPUs fully saturated

SAME VALUE, DIFFERENT MACHINES:

"myapp is using 150% CPU"
  On a 2-core VM:  150% = 1.5 cores fully busy = 75% of total capacity. CONCERNING.
  On a 32-core VM: 150% = 1.5 cores fully busy = 4.7% of total capacity. IRRELEVANT.
```

This is why you MUST always interpret CPU percentages relative to the total cores available.

### What Each Tool Shows

```bash
# top: %CPU is per-process, relative to ONE logical CPU
top -o %CPU
# PID   %CPU
# 1234  98.7    ← process using ~1 full core
# 5678  195.2   ← process using ~2 full cores
# 9012  0.3     ← process using ~0.3% of one core

# The system-wide CPU line at the top shows aggregate:
# %Cpu(s): 23.5 us, 5.2 sy, 0.0 ni, 70.8 id, 0.5 wa
#          ↑ 23.5% of ALL cores combined in user space
```

```bash
# htop: bars are PER CORE
htop
# [|||||||||||||||||||       39.2%]  ← core 0
# [||||||||||||              24.8%]  ← core 1
# [||||                       8.1%]  ← core 2
# [|||                        5.3%]  ← core 3
# Each bar maxes at 100% (that ONE core fully used)
```

```bash
# mpstat: percentages are PER CPU, each independently 0–100%
mpstat -P ALL 1 1
# CPU  %usr  %sys  %iowait  %steal  %idle
# all  62.5  15.2    3.1     0.0    19.2    ← average across all CPUs
#   0  95.0   3.0    0.0     0.0     2.0    ← this core is nearly maxed
#   1  30.0   2.0    0.0     0.0    68.0    ← this core is lightly loaded
#   2  98.0   1.0    0.0     0.0     1.0    ← this core is nearly maxed
#   3  27.0  54.0    0.0     0.0    19.0    ← this core is doing heavy syscalls
```

### Process-Level vs System-Level

```text
Process CPU% (from ps/top):
  "java is using 400% CPU"
  → This SINGLE process is consuming 4 full logical CPUs
  → On an 8-core machine, that's 50% of total capacity
  → On a 64-core machine, that's 6.25% — probably fine

System CPU% (from mpstat/vmstat):
  "%usr = 85% across all CPUs"
  → ALL processes combined are using 85% of ALL cores
  → On a 4-core:  85% = ~3.4 cores busy, 0.6 idle ← WARNING
  → On a 64-core: 85% = ~54 cores busy, 10 idle  ← Busy but has headroom
```

### The Core Saturation Test

```bash
# Is ANY single core saturated? (Single-thread bottleneck)
mpstat -P ALL 1 5 | awk '$3+0 > 90 {print "CPU " $2 " saturated: " $3 "% usr"}'

# Are ALL cores saturated? (Need more total CPU)
mpstat 1 5 | awk '/^Average.*all/ && $3+0 > 80 {print "WARNING: system CPU " $3 "%"}'

# How many processes are fighting for CPU?
vmstat 1 5
# The 'r' column = run queue: processes ready to run but waiting for a free core
# r > number_of_cores = CPU contention (processes queuing for CPU time)
```

---

## mpstat Column-by-Column Meaning

This is the most important output for understanding WHERE your CPU time is going:

```bash
mpstat -P ALL 1 5
```

```text
CPU  %usr   %nice   %sys %iowait  %irq  %soft  %steal  %guest  %gnice  %idle
all  23.50   0.00   5.20    0.00  0.00   0.50    0.00    0.00    0.00  70.80
```

### Every Column, Mapped to Hardware Reality

| Column | What It Counts | Hardware Meaning |
|--------|---------------|------------------|
| **`%usr`** | Time CPU spent running application code in user mode | Your app's instructions executing on the core. **This is your code running.** High `%usr` = your app is compute-heavy. |
| **`%nice`** | Same as `%usr` but for processes with modified priority (`nice`/`renice`) | Usually zero. If non-zero, something is deliberately running at lower priority (batch jobs, backups). Included in `%usr` for most tools. |
| **`%sys`** | Time CPU spent in kernel mode executing system calls, drivers, interrupt handlers | The kernel is doing work ON BEHALF of your app. High `%sys` (>20) = too many syscalls, file I/O overhead, or network processing. **This is overhead your app generates.** |
| **`%iowait`** | Time CPU was idle AND at least one I/O was in-flight somewhere in the system | The core had nothing to do, but something ELSE in the system is waiting on disk. **This is NOT "CPU waiting for I/O."** It's idle time tagged with a flag. See [IOWait deep dive](cpu-troubleshooting.md#4-iowait-vs-idle). |
| **`%irq`** | Time CPU spent servicing hardware interrupts | Device hardware (NIC, disk controller, USB) signaled the CPU. >5% = interrupt storm (bad driver, faulty hardware). |
| **`%soft`** | Time CPU spent in software interrupt (softirq) handlers — mostly network RX/TX | The kernel processes network packets here. >10% = extremely high packet rate (e.g., 100 Gbps NIC). Look for `ksoftirqd` in process list. |
| **`%steal`** | Time the hypervisor gave THIS VM's vCPU allocation to ANOTHER VM | Your VM wanted to run but the physical host scheduled a different VM instead. **This is stolen time.** >5% = noisy neighbor or overcommitted host. |
| **`%guest`** | Time CPU spent running a guest OS (visible on hypervisor, not inside VM) | Only relevant if YOU are the hypervisor host. Inside a VM, this is always 0. |
| **`%idle`** | Time CPU was truly idle — nothing to execute, no I/O pending | The core is doing nothing. This is your headroom. Should be >15–20% to absorb traffic spikes. |

### The Arithmetic: It Always Sums to ~100%

```text
%usr + %nice + %sys + %iowait + %irq + %soft + %steal + %guest + %idle ≈ 100%

Each column answers: "During the sample interval, what was THIS logical CPU doing?"

The CPU can only do ONE thing at a time per logical core:
  Executing your code         → %usr
  Executing kernel code       → %sys
  Idle (no work)              → %idle
  Idle (but I/O pending)      → %iowait
  Stolen by hypervisor        → %steal
```

---

## Core-Level vs Socket-Level vs Process-Level

### Three Lenses for CPU Analysis

```text
        LENS                    TOOL          WHAT IT TELLS YOU
        ─────────────────────────────────────────────────────────
Level 1 │ Process-Level    │ top, ps, pidstat │ "Which process is burning CPU?"
        │                  │                   │
Level 2 │ Core-Level       │ mpstat -P ALL     │ "Is one core saturated while
        │                  │ htop (per-core)   │  others sit idle?" 
        │                  │                   │ (single-thread bottleneck)
        │                  │                   │
Level 3 │ Socket/Node-Level│ lstopo, numactl   │ "Is the app crossing NUMA
        │                  │ /proc/cpuinfo     │  boundaries and paying latency?"
```

### Why Core-Level Matters

A 16-core machine at 12.5% total CPU can be **saturated** if the workload is single-threaded:

```text
System average: 12.5%  ← Looks fine

But mpstat -P ALL reveals:
CPU 0:  100.0%  ← ONE core is completely maxed
CPU 1:    0.0%  ← idle
CPU 2:    0.0%  ← idle
CPU 3:    0.0%  ← idle
CPU 4–15: 0.0%  ← all idle

Total: 100/16 = 6.25% system CPU — but the app is BOTTLENECKED.
```

This is the single most common misdiagnosis in CPU troubleshooting: looking at the average and missing the saturated core.

### The `/proc/cpuinfo` Cheat Sheet

```bash
# How many physical cores?
grep "cpu cores" /proc/cpuinfo | uniq
# cpu cores: 8

# How many siblings (HT threads per core)?
grep "siblings" /proc/cpuinfo | uniq
# siblings: 16   ← 2 threads × 8 cores = 16 logical CPUs

# Physical IDs (sockets):
grep "physical id" /proc/cpuinfo | sort -u | wc -l
# 1   ← single-socket machine

# Core IDs (unique per socket):
grep "core id" /proc/cpuinfo | sort -u
# 0,1,2,3,4,5,6,7   ← 8 physical cores

# Processor IDs (logical CPUs the OS sees):
grep "processor" /proc/cpuinfo
# 0 through 15   ← 16 logical CPUs
```

---

## CPU Time Accounting

### How the Kernel Tracks CPU Time

```bash
# /proc/stat — the raw counter file for ALL CPU time
cat /proc/stat | head -1
# cpu  90784814 1001 6745364 215924949 2313771 0 1556437 0 0 0
#      ↑user    ↑nice ↑sys   ↑idle     ↑iowait ↑irq ↑softirq ↑steal ↑guest ↑guest_nice
#
# Units: jiffies (usually 100 ticks/second = 10ms per jiffy)
# These are CUMULATIVE counters since boot. They NEVER decrease.
```

```bash
# /proc/PID/stat — per-process CPU time
cat /proc/1234/stat | awk '{print "utime=" $14 ", stime=" $15}'
# utime=234567 stime=45678
# ↑ user time          ↑ system time
# Units: jiffies. These are also cumulative.

# How top/ps calculate process %CPU:
# Take two snapshots of PID/stat, 1 second apart.
# %CPU = (utime_delta + stime_delta) / (1 second in jiffies) × 100
#
# Example:
# T0: utime=234567, stime=45678  → total=280245
# T1: utime=235567, stime=45778  → total=281345
# Delta = 1100 jiffies
# At 100 jiffies/sec: 1100/100 * 100 = 1100%    ← process used 11 cores!
# Or more realistically at 100ms samples: ~110%  ← ~1.1 cores
```

### What "100% CPU" Actually Represents

```text
When top says myapp is using 100% CPU for 10 seconds:

    Actual CPU time consumed = 100% × 10s = 10 core-seconds
    On a 3.0 GHz CPU, that's approximately:
    10 seconds × 3,000,000,000 cycles/second = 30 BILLION clock cycles

    This can represent:
    - 30 billion simple integer additions, OR
    - 7.5 billion RAM accesses (4 cycles × 4 per byte), OR
    - 600,000 TLS handshakes, OR
    - 1 video frame rendered
```

---

## Quick-Reference: Navigating the Numbers

```bash
# How many logical CPUs does the system have?
nproc
# 16

# Which process uses the most CPU right now?
ps aux --sort=-%cpu | head -5

# Is any single core saturated? (Look for >90% on any row)
mpstat -P ALL 1 1

# Is the system overall running hot? (Look at the 'all' row)
mpstat 1 1 | tail -1 | awk '{print "usr=" $3 " sys=" $5 " iowait=" $6 " idle=" $NF}'

# How many processes are waiting for a free CPU? (r > nproc = contention)
vmstat 1 5 | awk 'NR>2 {print "r=" $1 " b=" $2}'

# What's the steal time? (Am I on an overcommitted VM?)
mpstat 1 1 | awk '/all/ {print "steal=" $9 "%"}'

# What is my CPU topology?
lscpu | grep -E "^CPU\(s\)|Thread|Core|Socket|NUMA"

# How many physical cores (not HT threads)?
lscpu | grep "^Core(s) per socket" | awk '{print $NF}'
```

---

## References

- [CPU Troubleshooting (next: deep-dive scenarios)](cpu-troubleshooting.md)
- [Linux CPU Scheduling (CFS)](https://www.kernel.org/doc/html/latest/scheduler/sched-design-CFS.html)
- [lscpu / CPU topology](https://man7.org/linux/man-pages/man1/lscpu.1.html)
- [mpstat — per-processor statistics](https://man7.org/linux/man-pages/man1/mpstat.1.html)
- [Brendan Gregg — CPU Utilization is Wrong](https://www.brendangregg.com/blog/2017-05-09/cpu-utilization-is-wrong.html)
