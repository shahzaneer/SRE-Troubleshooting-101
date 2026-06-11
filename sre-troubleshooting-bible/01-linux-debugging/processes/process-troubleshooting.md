# Process Troubleshooting
> **Category:** Linux | Processes | Debugging
> **Difficulty:** Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#linux` `#processes` `#debugging` `#oncall`

---

## Table of Contents
1. [Zombie Processes](#1-zombie-processes)
2. [strace Mastery](#2-strace-mastery)
3. [lsof Mastery](#3-lsof-mastery)
4. [fuser](#4-fuser)
5. [Process Limits](#5-process-limits)
6. [Signal Handling](#6-signal-handling)
7. [/proc/PID Deep Dive](#7-procpid-deep-dive)

---

## 1. Zombie Processes

### What Is a Zombie?

A zombie process is a process that has **finished executing** (exited) but still has an entry in the process table because its **parent has not called `wait()` or `waitpid()`** to collect its exit status. The kernel cannot fully reap the child until the parent acknowledges the termination.

A zombie:
- Has freed all memory, file descriptors, and resources.
- Exists only as a process table entry (PID, exit code, resource usage stats).
- Is in state `Z` (defunct/zombie).
- Does NOT consume CPU or memory (beyond the tiny process table entry).

### When to Worry

```bash
# Count zombies
ps aux | awk '$8=="Z"' | wc -l
# Or more robust:
ps -eo pid,stat,comm | awk '$2 ~ /^Z/ {print $1, $3}' | wc -l

# List all zombies with their parent PID
ps -eo pid,ppid,stat,comm | awk '$3 ~ /Z/ {print "zombie PID:", $1, "parent PID:", $2, "cmd:", $4}'

# Check /proc loadavg — the 4th field is threads in zombie state
cat /proc/loadavg
# 0.32 0.27 0.21 4/456 12345
#               ^ 4 = 3 running + 1 zombie? Actually this is threads in R state.
# To count zombies from /proc:
grep -c "^State:.*Z" /proc/[0-9]*/status 2>/dev/null
```

| Zombie Count | Severity | Action |
|-------------|----------|--------|
| 1-10 | Normal | Some programs spawn short-lived children and are slow to reap. Harmless. |
| 10-100 | Investigate | Bug in parent process — it's not calling wait(). Check the parent. |
| 100-1000 | Serious | Parent is broken. Number of zombies usually stays stable if the parent has a fixed number of missing wait() calls. |
| 1000+ | Critical | Parent is spawning children in a loop without reaping. Will eventually exhaust PID space if combined with high PID velocity. |

### Classic Scenario: 10,000 Zombies

> **Alert:** A cron job that processes files spawns a child for each file. The parent creates children with `fork()` + `exec()` but the parent loop doesn't call `waitpid()`. Unable to create new processes: "Resource temporarily unavailable" (fork fails — PID space or process limit exhausted). `ps aux` shows 32,767 zombies — the `pid_max` default on the system. Even though zombies don't consume memory or CPU, **they consume PIDs**. Once all PIDs are exhausted, no new process can start — not even `kill` or `ps`.
>
> **Fix:** Kill the parent process. `init` (PID 1) or `systemd` inherits all zombie children and reaps them instantly.
> ```bash
> kill -15 1234   # SIGTERM to the irresponsible parent
> # If parent is stubborn:
> kill -9 1234
> # Verify zombies are gone:
> ps aux | awk '$8=="Z"' | wc -l
> # Should drop to 0 within seconds
> ```
>
> **Long-term fix:** Fix the parent code to reap children or use `SA_NOCLDWAIT` + `SIGCHLD` with `SIG_IGN` so kernel auto-reaps.

### Prevention: Python Example

```python
import os
import signal
import time

# Pattern 1: Auto-reap children by ignoring SIGCHLD
signal.signal(signal.SIGCHLD, signal.SIG_IGN)

# Now any child process is automatically reaped by the kernel.
# No wait() needed. No zombies produced.

# Pattern 2: Explicit reaping with poll
import subprocess

processes = []
for _ in range(10):
    processes.append(subprocess.Popen(['sleep', '2']))

while processes:
    for p in list(processes):
        ret = p.poll()
        if ret is not None:
            print(f"Child PID {p.pid} exited with {ret}")
            processes.remove(p)
    time.sleep(0.5)

# Pattern 3: Process pool (handles wait() internally)
from concurrent.futures import ProcessPoolExecutor

with ProcessPoolExecutor(max_workers=4) as executor:
    futures = [executor.submit(some_work, arg) for arg in args]
    for f in futures:
        f.result()  # blocks and reaps
```

---

## 2. strace Mastery

strace traces system calls (the interface between user-space and the kernel) made by a process. It's the most powerful process debugging tool — and the most dangerous when misapplied.

### Basic Usage

```bash
# Trace a running process (output goes to stderr)
strace -p $PID

# Trace a process from start
strace ./myapp

# Trace with timestamps on each call
strace -t -p $PID
# 14:32:15 read(3, "...", 4096) = 4096

# Trace with wall-clock timing (duration of each syscall)
strace -T -p $PID
# read(3, "...", 4096) = 4096 <0.000145>   <-- took 145 microseconds

# Count syscalls and show summary (Ctrl-C to stop)
strace -c -p $PID
# % time     seconds  usecs/call     calls    errors syscall
# ------ ----------- ----------- --------- --------- ----------------
#  45.23    1.234567        1234      1000           poll
#  23.45    0.567890        5678       100           read
#  12.34    0.345678        3456       100           write
#   5.67    0.123456        1234       100           futex
#   3.33    0.098765        9876        10           mmap
#   ...
# ------ ----------- ----------- --------- --------- ----------------
# 100.00    2.345678                  1500       100 total
#
# This tells you where the process spends its time in the kernel.

# Trace specific syscalls
strace -e trace=read,write,open,close -p $PID
strace -e trace=network -p $PID       # socket, connect, bind, send, recv, etc.
strace -e trace=file -p $PID          # open, read, write, close, stat, etc.
strace -e trace=process -p $PID       # fork, exec, exit, wait, etc.
strace -e trace=signal -p $PID        # signal-related syscalls

# Trace with string output (show full strings, not just pointers)
strace -s 4096 -p $PID                # -s = max string size to print

# Trace child processes too
strace -f -p $PID                     # follow forks

# Write output to file (cleaner for analysis)
strace -o /tmp/strace.out -f -p $PID
```

### WARNING: strace Overhead

```
strace uses ptrace() to intercept every syscall. This means:
  - Every syscall triggers a context switch from traced process → strace → kernel
  - Typical slowdown: 10x to 100x for I/O-bound workloads
  - Worst case: 1000x for syscall-heavy workloads (e.g., busy-wait polling)
  
NEVER run strace on a production process under heavy load without:
  1. Understanding the performance impact
  2. Having a rollback plan
  3. Informing your team
  4. Setting a time limit: timeout 30 strace -p $PID
```

### Real Debugging Scenarios

```bash
# Scenario 1: "Why is my process stuck?"
# strace shows a syscall that never returns:
strace -p $PID 2>&1 | head -5
# read(5,     <-- hanging on read from fd 5
# Check what fd 5 is:
ls -la /proc/$PID/fd/5
# lrwx------ 1 app app 64 Jun 11 14:32 5 -> /mnt/nfs/data/file.dat
# NFS mount is hung — process stuck in D state forever. Fix NFS or unmount.

# Scenario 2: "Why is the process using 100% CPU?"
strace -p $PID -c -f 2>&1 &
sleep 10
kill %1
# % time     seconds  usecs/call     calls    errors syscall
#  95.67    9.876543          98    100000           futex
# Process is spinning in a futex (fast userspace mutex) loop.
# Thread is busy-waiting on a lock that will never be released → livelock.

# Scenario 3: "Where is the app reading config from?"
strace -e trace=open,openat,stat,newfstatat -f -p $PID 2>&1 | grep -v ENOENT
# Shows every file the process opens and stats — find missing config files,
# wrong paths, permission errors.

# Scenario 4: "Why are database queries slow?"
strace -e trace=sendto,recvfrom,write,read -T -p $PID 2>&1 | \
  grep -E "<[0-9]+\.[0-9]{3}>$" | awk '$NF ~ /^</' | sort -rn -t'<' -k2
# Find the slowest network I/O syscalls with timing.
```

### strace + Filtering Example

```bash
# Find only the slow syscalls (>100ms)
strace -T -p $PID 2>&1 | \
  awk '/<[0-9]+\.[0-9]{6}>/ {
    match($0, /<([0-9]+\.[0-9]+)>/, arr);
    if (arr[1] > 0.1) print arr[1], $0
  }'

# Find futex contention (threads waiting >1 second on locks)
strace -e trace=futex -T -p $PID 2>&1 | \
  awk '/<[0-9]+\.[0-9]+>/ {match($0,/<([0-9.]+)>/,a); if(a[1]>1) print}'

# Trace only write() calls to specific fd
FD=5
strace -e trace=write -e write=$FD -p $PID
```

---

## 3. lsof Mastery

`lsof` (list open files) shows everything that's open on a Linux system. "Everything is a file" includes regular files, directories, pipes, sockets, devices, and more.

### Essential lsof Patterns

```bash
# All open files for a specific process
lsof -p $PID
# Columns: COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME
# FD examples:
#   0u  = read/write on fd 0
#   3r  = read-only on fd 3
#   4w  = write-only on fd 4
#   cwd = current working directory
#   rtd = root directory
#   txt = program text (the binary)
#   mem = memory-mapped file
#   DEL = deleted file (marked "deleted" in NAME column)

# What process is using port 80?
lsof -i :80
# COMMAND  PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME
# nginx   1234 root    6u  IPv4  12345      0t0  TCP *:http (LISTEN)
# nginx   1235 www     6u  IPv4  12345      0t0  TCP *:http (LISTEN)
# nginx   1236 www     6u  IPv4  12345      0t0  TCP *:http (LISTEN)

# What processes are using a specific file?
lsof /var/log/nginx/access.log
# COMMAND  PID USER   FD   TYPE DEVICE SIZE/OFF    NODE NAME
# nginx   1234 root    5w  REG  253,0 47123456789  5678 /var/log/nginx/access.log

# All open network connections (TCP + UDP)
lsof -i
# IPv4 and IPv6:
lsof -i   # all protocols, all ports

# TCP connections only
lsof -i tcp

# All TCP connections in TIME_WAIT state (useful for debugging socket exhaustion)
ss -tan state time-wait
lsof -i tcp | grep TIME_WAIT   # lsof approach

# Established connections to a specific host
lsof -i @10.0.1.5
# All connections involving that IP (source or destination)

# Deleted files still held open (space not freed)
lsof +L1
# Shows open files with link count <1 (deleted but open)

# All open files by a specific user
lsof -u www-data

# All open files EXCEPT a user (negation)
lsof -u ^root

# All open files in a directory (recursively)
lsof +D /var/log

# All open regular files (no sockets, pipes, etc.)
lsof -a -p $PID -d '^mem,^cwd,^rtd,^txt,^DEL'

# Count open files per process — find file descriptor leaks
lsof 2>/dev/null | awk '{print $2}' | sort | uniq -c | sort -rn | head -20 | \
  while read count pid; do
    comm=$(cat /proc/$pid/comm 2>/dev/null)
    echo "$count $pid $comm"
  done

# Count open files per process (using /proc, faster)
for pid in $(ls /proc | grep -E '^[0-9]+$'); do
  count=$(ls /proc/$pid/fd 2>/dev/null | wc -l)
  if [ "$count" -gt 0 ]; then
    comm=$(cat /proc/$pid/comm 2>/dev/null)
    echo "$count $pid $comm"
  fi
done | sort -rn | head -20
```

### lsof Output Decoded

```
COMMAND     PID   TID TASKCMD            USER   FD      TYPE             DEVICE  SIZE/OFF       NODE NAME
java      12345                          app   12u     sock                 0,7       0t0  987654321 protocol: TCPv6
java      12345  23456 GC Thread#0       app   12u     sock                 0,7       0t0  987654321 protocol: TCPv6
```

- **TID** — thread ID within a multi-threaded process. `Fd 12u` is shared across threads in a Java process.
- **TYPE `sock`** — socket (network or UNIX domain).
- **DEVICE `0,7`** — major,minor for non-filesystem objects (0 = no backing device).
- **SIZE/OFF `0t0`** — offset or size; `0t0` for sockets.
- **NODE** — inode number or unique socket identifier.

---

## 4. fuser

`fuser` identifies processes using a file, socket, or filesystem. It's like `lsof` in reverse: "given a resource, who's using it?"

```bash
# What process is bound to TCP port 80?
fuser -v 80/tcp
#                      USER        PID ACCESS COMMAND
# 80/tcp:              root       1234 F.... nginx
#                      www-data   1235 F.... nginx
# ACCESS codes:
#   c = current directory
#   e = executable being run
#   f = open file (default; f is omitted in default output)
#   r = root directory
#   m = mmap'd file or shared lib
#   . = placeholder (shown when not f)

# What processes are using a specific file?
fuser -v /var/log/app.log
# /var/log/app.log:    app     28471 F.... java

# What processes have files open under /var/log?
fuser -v /var/log
# Only shows processes with this exact directory as current directory or mounted.

# Kill all processes using a specific port
fuser -k 8080/tcp
# Sends SIGKILL to every process bound to port 8080. Use -SIGNAL to specify:
fuser -k -TERM 8080/tcp  # SIGTERM (graceful)

# What processes are preventing filesystem unmount?
fuser -v -m /mnt/data
# Shows all processes accessing any file on that filesystem (open files, cwd, mapped, etc.)

# Who is accessing a specific filesystem? (same as above, with PID only)
fuser -m /mnt/data
# Output: 1234m 1235c 3456e  # PIDs + access codes
```

---

## 5. Process Limits

### Viewing Limits

```bash
# Current shell limits
ulimit -a
# core file size          (blocks, -c) 0
# data seg size           (kbytes, -d) unlimited
# scheduling priority             (-e) 0
# file size               (blocks, -f) unlimited
# pending signals                 (-i) 63619
# max locked memory       (kbytes, -l) 8192   ← can lock 8MB (important for JVM)
# max memory size         (kbytes, -m) unlimited
# open files                      (-n) 1024   ← TOO LOW for any server process!
# pipe size            (512 bytes, -p) 8
# POSIX message queues     (bytes, -q) 819200
# real-time priority              (-r) 0
# stack size              (kbytes, -s) 8192
# cpu time               (seconds, -t) unlimited
# max user processes              (-u) 63619
# virtual memory          (kbytes, -v) unlimited
# file locks                      (-x) unlimited

# Limits for a running process (from /proc)
cat /proc/$PID/limits
# Limit                     Soft Limit           Hard Limit           Units
# Max open files            1024                 1048576              files
# Max locked memory         65536                65536                bytes
# Max address space         unlimited            unlimited            bytes
# Max processes             63619                63619                processes
```

### Classic Scenario: "Too Many Open Files"

> **App crashes with:** `java.io.IOException: Too many open files` or `EMFILE` (errno 24).
> **Service handles 5000 concurrent HTTP connections.**
>
> ```bash
> $ ulimit -n
> 1024
> $ cat /proc/$(pidof java)/limits | grep "open files"
> Max open files            1024                 1048576              files
> ```
>
> The default file descriptor limit is 1024. Each incoming HTTP connection is a socket (= file descriptor). The app also opens files for logging, DB connections, config files, and shared libraries. At ~1000 concurrent connections, it runs out of FDs and starts failing.
>
> **Fix:**
> ```bash
> # For systemd services (PREFERRED):
> # Edit /etc/systemd/system/myapp.service.d/limits.conf:
> # [Service]
> # LimitNOFILE=65536
> systemctl daemon-reload
> systemctl restart myapp
>
> # Verify:
> systemctl show myapp --property=LimitNOFILE
> # LimitNOFILE=65536
>
> # For user processes (login shell):
> # /etc/security/limits.conf:
> # appuser   soft   nofile   65536
> # appuser   hard   nofile   1048576
>
> # For Docker:
> docker run --ulimit nofile=65536:65536 myapp
> ```

### systemd Limit Configuration

```ini
# /etc/systemd/system/myapp.service or override:
[Service]
# File descriptor limit
LimitNOFILE=65536

# Number of processes/threads allowed
LimitNPROC=32768

# Maximum locked memory (for JVM large pages, crypto libs)
LimitMEMLOCK=infinity

# Core dump size
LimitCORE=infinity

# Maximum number of pending signals
LimitSIGPENDING=1024

# CPU time limit (in seconds, for runaway process protection)
LimitCPU=infinity

# Address space limit
LimitAS=infinity

# Nice level
Nice=-5

# Verify all limits:
systemctl show myapp --property=LimitNOFILE --property=LimitNPROC --property=LimitMEMLOCK
```

---

## 6. Signal Handling

### The Key Signals Every SRE Must Know

| Signal | Number | Default Action | What It Does | Use Case |
|--------|--------|---------------|--------------|----------|
| **SIGTERM** | 15 | Terminate | **Graceful shutdown.** Process can catch, run cleanup, flush buffers, close connections. | Your FIRST kill attempt. Always. |
| **SIGKILL** | 9 | Terminate (cannot be caught) | **Immediate kill.** Kernel destroys process. No cleanup. Data loss risk. | Last resort. Only when SIGTERM failed. |
| **SIGINT** | 2 | Terminate | **Interrupt from keyboard (Ctrl+C).** Like SIGTERM, can be caught. | User-initiated stop of foreground process. |
| **SIGQUIT** | 3 | Core dump + Terminate | **Quit with core dump.** Process can catch. Core dump created. | Debug frozen process; get a core for offline analysis. |
| **SIGHUP** | 1 | Terminate | **Hangup.** Traditional: terminal closed. Modern: "reload config." | Many daemons (nginx, haproxy) reload config on SIGHUP. |
| **SIGSTOP** | 19 | Stop (cannot be caught) | **Suspend process.** Kernel freezes it. Cannot be caught or ignored. | Pause a process temporarily. Resume with SIGCONT. |
| **SIGCONT** | 18 | Continue | **Resume stopped process.** | Resume after SIGSTOP. |
| **SIGUSR1** | 10 | Terminate | **User-defined signal 1.** Programs can define custom behavior. | Nginx: reopen log files. Node: start debugger. |
| **SIGUSR2** | 12 | Terminate | **User-defined signal 2.** Custom behavior. | Apache: graceful restart. |
| **SIGABRT** | 6 | Core dump + Terminate | **Abort.** Generated by abort(). Often from failed assertion. | Program crashed itself deliberately. |
| **SIGSEGV** | 11 | Core dump + Terminate | **Segmentation fault.** Invalid memory access. | Null pointer, buffer overflow. Check core dump. |
| **SIGBUS** | 7 | Core dump + Terminate | **Bus error.** Misaligned access or hardware memory error. | Bad RAM, mmap issue, faulty hardware. |

### Signal Masking and Handling

```bash
# What signals are blocked (masked) in a process?
cat /proc/$PID/status | grep -E "Sig(Cgt|Ign|Blk)"
# SigQ:   0/63619       # Queued signals: current/max
# SigPnd: 0000000000000000  # Pending signals for the process (bitmap as hex)
# ShdPnd: 0000000000000000  # Pending signals for the thread group
# SigBlk: 0000000000000000  # Blocked signals
# SigIgn: 0000000000001000  # Ignored signals
# SigCgt: 0000000180004003  # Caught (handled) signals

# Decode signal masks (example: SigCgt: 0000000180004003)
# Bit positions correspond to signal numbers.
# Bit 0 = SIGHUP, bit 1 = SIGINT, bit 2 = SIGQUIT, ...
python3 -c "
mask = 0x0000000180004003
signals = ['HUP','INT','QUIT','ILL','TRAP','ABRT','BUS','FPE','KILL',
           'USR1','SEGV','USR2','PIPE','ALRM','TERM','STKFLT','CHLD',
           'CONT','STOP','TSTP','TTIN','TTOU','URG','XCPU','XFSZ',
           'VTALRM','PROF','WINCH','IO','PWR','SYS']
for i in range(1,32):
    if mask & (1 << (i-1)):
        print(f'SIG{signals[i-1]} ({i})')
"
# Output:
# SIGINT (2)
# SIGQUIT (3)
# SIGTERM (15)  <-- app catches these
```

### Signal Handling in Code

```python
import signal
import time
import sys

# Graceful shutdown handler
running = True

def handle_sigterm(signum, frame):
    global running
    print(f"\nReceived SIGTERM ({signum}). Starting graceful shutdown...")
    running = False

def handle_sigusr1(signum, frame):
    print("SIGUSR1 received — reloading config / printing stats / etc.")

def handle_sigquit(signum, frame):
    print("SIGQUIT received — dumping state for debugging")
    # Print current stack, open connections, etc.
    import traceback
    traceback.print_stack(frame)

signal.signal(signal.SIGTERM, handle_sigterm)
signal.signal(signal.SIGINT, handle_sigterm)
signal.signal(signal.SIGUSR1, handle_sigusr1)
signal.signal(signal.SIGQUIT, handle_sigquit)
signal.signal(signal.SIGHUP, signal.SIG_IGN)  # ignore SIGHUP (e.g., for nohup-like behavior)

print(f"Process {os.getpid()} started. Send SIGTERM to stop gracefully.")

while running:
    time.sleep(0.5)  # real app would do work here

print("Cleaning up: closing connections, flushing buffers...")
time.sleep(2)  # simulate cleanup
print("Shutdown complete.")
sys.exit(0)
```

```java
// Java signal handling (via sun.misc.Signal — note: internal API)
import sun.misc.Signal;
import sun.misc.SignalHandler;
import java.util.concurrent.atomic.AtomicBoolean;

public class GracefulShutdown {
    private static final AtomicBoolean running = new AtomicBoolean(true);

    public static void main(String[] args) {
        // Register SIGTERM handler
        Signal.handle(new Signal("TERM"), sig -> {
            System.out.println("Received SIGTERM. Starting graceful shutdown...");
            running.set(false);
        });

        // Register SIGINT handler (Ctrl+C)
        Signal.handle(new Signal("INT"), sig -> {
            System.out.println("Received SIGINT. Shutting down...");
            running.set(false);
        });

        // JVM shutdown hook (runs on normal exit or SIGTERM, NOT on SIGKILL)
        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            System.out.println("JVM shutting down — cleaning up...");
            // Close connection pools, flush logs, etc.
        }));

        System.out.println("Application started. PID: " + ProcessHandle.current().pid());

        while (running.get()) {
            try { Thread.sleep(500); } catch (InterruptedException e) { break; }
        }

        System.out.println("Shutdown complete.");
    }
}
```

```javascript
// Node.js signal handling
process.on('SIGTERM', () => {
    console.log('SIGTERM received. Closing server gracefully...');
    server.close(() => {
        console.log('HTTP connections closed.');
        db.disconnect().then(() => {
            console.log('DB connection closed.');
            process.exit(0);
        });
    });

    // Force exit after 10s if graceful shutdown stalls
    setTimeout(() => {
        console.error('Graceful shutdown timed out — forcing exit');
        process.exit(1);
    }, 10000);
});

process.on('SIGINT', () => {
    console.log('SIGINT (Ctrl+C) received. Shutting down...');
    process.exit(0);
});

process.on('SIGUSR1', () => {
    console.log('SIGUSR1 — triggering heap dump for debugging');
    const inspector = require('inspector');
    // ... trigger heap snapshot or print stats
});
```

---

## 7. /proc/PID Deep Dive

### Key Files in /proc/PID

```bash
PID=12345

# ─── Process Identity ───

cat /proc/$PID/comm         # Command name (truncated to 15 chars)
cat /proc/$PID/cmdline | tr '\0' ' '  # Full command line (null-separated)
cat /proc/$PID/environ | tr '\0' '\n'  # Environment variables (null-separated)
cat /proc/$PID/status       # Human-readable process status

# From /proc/PID/status:
cat /proc/$PID/status | grep -E "^(Name|State|Tgid|Pid|PPid|Uid|VmRSS|VmSize|Threads|voluntary_ctxt_switches|nonvoluntary_ctxt_switches)"
# Name:  java
# State: S (sleeping)   ← R (running), S (sleeping), D (uninterruptible), Z (zombie), T (stopped)
# Tgid:  12345           ← Thread Group ID (= PID for main thread)
# Pid:   12345
# PPid:  1               ← Parent PID (1 = init/systemd — original parent died)
# TracerPid: 0           ← 0 = not being traced; non-zero = being ptraced
# Uid:   1000 1000 1000 1000  (real, effective, saved, filesystem)
# Gid:   1000 1000 1000 1000
# VmSize: 8388608 kB     ← Virtual memory size (includes mmap'd files, libs)
# VmRSS:  3145728 kB     ← Resident Set Size (physical memory used)
# Threads: 48
# voluntary_ctxt_switches:   123456789  ← process voluntarily yielded CPU
# nonvoluntary_ctxt_switches: 23456789  ← preempted (used its time slice)

# ─── File Descriptors ───

ls -la /proc/$PID/fd/      # Open file descriptors
# lrwx------ 1 app app 64 Jun 11 14:32 0 -> /dev/pts/0
# lrwx------ 1 app app 64 Jun 11 14:32 1 -> /dev/pts/0
# lrwx------ 1 app app 64 Jun 11 14:32 2 -> /dev/pts/0
# lrwx------ 1 app app 64 Jun 11 14:32 3 -> socket:[98765]
# lrwx------ 1 app app 64 Jun 11 14:32 4 -> socket:[98766]
# lr-x------ 1 app app 64 Jun 11 14:32 5 -> /var/log/app/access.log
# lrwx------ 1 app app 64 Jun 11 14:32 12 -> /var/log/app/app.log (deleted)

# Read data from a socket fd (if it's readable):
# cat /proc/$PID/fd/3    # might hang if socket is empty

# On Linux, /proc/PID/fd/N is a magic symlink.
# For deleted files: you can recover the data!
cp /proc/$PID/fd/12 /tmp/recovered-app.log
# Then restart the process to release the space.

# ─── Memory Mappings ───

cat /proc/$PID/maps | head -20
# address                   perms offset   dev   inode   pathname
# 5566778899000-55667788a000 r--p 00000000 08:01 123456  /usr/bin/java
# 55667788a000-55667788b000 r-xp 00001000 08:01 123456  /usr/bin/java     (code)
# 7f1234000000-7f1234021000 rw-p 00000000 00:00 0       [heap]            ← heap
# 7f1238000000-7f123c000000 rw-p 00000000 00:00 0       [anon:thread_stack]
# 7f1240000000-7f1250000000 ---p 00000000 00:00 0
# 7f1260000000-7f1270000000 rw-p 00000000 00:00 0

# /proc/PID/smaps — detailed memory info per mapping (HUGE file, read only when needed)
cat /proc/$PID/smaps | head -100
# Shows Rss, Pss, Private_Clean, Private_Dirty, Shared_Clean, Shared_Dirty
# for EACH memory mapping — find what's eating RSS.

# ─── I/O Statistics ───

cat /proc/$PID/io
# rchar: 123456789    ← bytes read (via read syscalls)
# wchar: 987654321    ← bytes written
# syscr: 12345        ← read syscalls count
# syscw: 67890        ← write syscalls count
# read_bytes: 9876543210  ← bytes read from storage layer
# write_bytes: 12345678901 ← bytes written to storage layer
# cancelled_write_bytes: 123456  ← writes that were cancelled (e.g., tmp file deleted)
#
# read_bytes/write_bytes count actual block I/O (not page cache hits).
# rchar/wchar count ALL read/write syscalls (even those satisfied from cache).
# If rchar >> read_bytes, cache hit rate is high (good).

# ─── Scheduler Info ───

cat /proc/$PID/sched
# Shows scheduling policy, priority, and runtime stats:
# se.exec_start :    1234567890.123456
# se.vruntime   :       -12345.678901
# se.sum_exec_runtime :     1234.567890
# nr_switches   :      12345678
# nr_voluntary_switches :  12000000
# nr_involuntary_switches :   345678

# ─── Stack Trace (kernel) ───

cat /proc/$PID/stack
# [<0>] do_select+0x1a2/0x2b0
# [<0>] core_sys_select+0x1f0/0x310
# [<0>] __x64_sys_pselect6+0x18f/0x230
# [<0>] do_syscall_64+0x5c/0x80
# [<0>] entry_SYSCALL_64_after_hwframe+0x64/0xce
# Shows what the process is doing in kernel space RIGHT NOW.
# "do_select" = process is in select() / poll() loop — normal idle state for event loop.
# "do_exit" = process is dying.
# "do_wait" = process is waiting for children.
# "pipe_read/pipe_write" = blocked on a pipe.
# "ext4_file_read" = blocked on filesystem I/O.
# "rpc_wait_for_completion_task" = stuck on NFS.

# ─── Syscall (what syscall is the process in?) ───

cat /proc/$PID/syscall
# 0 0x7 0x7fffd8123456 0x1000 0xffffffff 0x0 0x7fffd8123400 0x7fffd8123456
# First number = syscall number. 0 = read, 1 = write, 3 = close, ...
# Check /usr/include/asm/unistd_64.h for the mapping.
# A process stuck in D state will show a syscall that never completes.

# ─── Cgroup ───

cat /proc/$PID/cgroup
# 0::/system.slice/myapp.service
# Shows which cgroup hierarchy the process belongs to.
# systemd puts each service in its own cgroup.

# ─── Mount Namespace ───

ls -la /proc/$PID/ns/
# lrwxrwxrwx 1 app app 0 Jun 11 14:32 mnt -> mnt:[4026531840]
# lrwxrwxrwx 1 app app 0 Jun 11 14:32 net -> net:[4026531992]
# lrwxrwxrwx 1 app app 0 Jun 11 14:32 pid -> pid:[4026531836]
# Each namespace has a unique inode number — if two processes have different
# numbers, they're in different namespaces (different containers, different views).
```

### Quick Diagnostic Scripts Using /proc

```bash
#!/bin/bash
# proc-health.sh — comprehensive health snapshot for a PID
PID=$1
[ -z "$PID" ] && { echo "Usage: $0 PID"; exit 1; }
[ ! -d "/proc/$PID" ] && { echo "PID $PID not found"; exit 1; }

echo "=== Process: $(cat /proc/$PID/comm) (PID $PID) ==="
echo "State:     $(awk '/^State:/ {print $2}' /proc/$PID/status)"
echo "Threads:   $(awk '/^Threads:/ {print $2}' /proc/$PID/status)"
echo "PPID:      $(awk '/^PPid:/ {print $2}' /proc/$PID/status)"
echo "Uptime:    $(ps -o etime= -p $PID | xargs)"
echo "CPU time:  $(ps -o time= -p $PID | xargs)"
echo ""
echo "Memory:    RSS=$(awk '/^VmRSS:/ {printf "%.0f MB", $2/1024}' /proc/$PID/status)"
echo "           VSZ=$(awk '/^VmSize:/ {printf "%.0f MB", $2/1024}' /proc/$PID/status)"
echo "           Data=$(awk '/^VmData:/ {printf "%.0f MB", $2/1024}' /proc/$PID/status)"
echo "           Stk=$(awk '/^VmStk:/ {printf "%.0f MB", $2/1024}' /proc/$PID/status)"
echo ""
echo "FDs open:  $(ls /proc/$PID/fd 2>/dev/null | wc -l)"
echo "FD limit:  $(grep 'Max open files' /proc/$PID/limits | awk '{print $4}')"
echo ""
echo "Vol ctx sw: $(awk '/^voluntary_ctxt_switches/ {print $2}' /proc/$PID/status | numfmt --to=iec)"
echo "Invol ctx:  $(awk '/^nonvoluntary_ctxt_switches/ {print $2}' /proc/$PID/status | numfmt --to=iec)"
echo ""
echo "I/O read:   $(awk '/^read_bytes/ {printf "%.1f GB", $2/1024/1024/1024}' /proc/$PID/io 2>/dev/null || echo 'N/A')"
echo "I/O write:  $(awk '/^write_bytes/ {printf "%.1f GB", $2/1024/1024/1024}' /proc/$PID/io 2>/dev/null || echo 'N/A')"
echo ""
echo "Kernel stack:"
cat /proc/$PID/stack 2>/dev/null | head -5
echo ""
echo "Top 5 open file types:"
ls -la /proc/$PID/fd 2>/dev/null | awk -F' -> ' '{print $2}' | awk -F'[][]' '{print $2}' | sort | uniq -c | sort -rn | head -5
```
