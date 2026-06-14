# Advanced Debugging Tricks
> **Category:** 10x SRE | Debugging | Advanced
> **Difficulty:** Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#10x` `#debugging` `#advanced` `#bpftrace`

---

## The SRE's Low-Level Arsenal

When dashboards show symptoms but not causes, you reach for the tools that operate BELOW your application. These tools look at the kernel, the CPU, the network stack — places where your application can't lie.

> "Metrics tell you something is wrong. Logs tell you WHAT is wrong. Kernel-level tools tell you WHY it's wrong."

---

## gdb — Attaching to a Running Process

```
WARNING: gdb -p FREEZES the target process while attached.
         NEVER use on a production process that's actively serving traffic.
         ONLY use when the process is already deadlocked/hung/unresponsive.
```

### When to Use

The process is hung. Not responding to health checks. Not consuming CPU. Not writing logs. You need to see what every thread is doing RIGHT NOW.

```bash
# Attach to running process
gdb -p <PID>

# Get stack traces of ALL threads
(gdb) info threads
   Id   Target Id         Frame
* 1    Thread 0x7f...   _lll_lock_wait ()
  2    Thread 0x7f...   _pthread_cond_wait ()
  3    Thread 0x7f...   read () at ../sysdeps/unix/syscall-template.S:84
  4    Thread 0x7f...   __accept_nocancel ()

(gdb) thread apply all bt
# OR: thread apply all bt full  (includes local variables — more data, slower)

# Find the stuck thread:
# If ALL threads are in _lll_lock_wait() → classic deadlock
# If one thread is in accept() and others in read() → normal I/O wait
# If threads are in malloc() → memory exhaustion or fragmentation

# Detach without killing process
(gdb) detach
(gdb) quit
```

### Real Scenario: Deadlocked Python GIL

```
Symptom: Django app hanging. Not responding at all. CPU = 0% (all threads waiting).

gdb -p <PID>
(gdb) thread apply all bt

Thread 1: _lll_lock_wait () → PyThread_acquire_lock ()
Thread 2: _lll_lock_wait () → PyThread_acquire_lock ()
Thread 3: _lll_lock_wait () → PyThread_acquire_lock ()
Thread 4: _lll_lock_wait () → PyThread_acquire_lock ()
...all 40 threads identical...

Thread 7 also shows: _PyCFunction_FastCallDict → logging.Handler.handle

Diagnosis: A logging handler (Sentry/DataDog) is making a network call while holding the GIL.
          The network call hangs (timeout not set). All other threads queue for the GIL.
          Python 2.7 (no GIL release on I/O for C extensions that don't release it explicitly).

Fix: Python 3 migration (better GIL handling) + set socket timeout on logging handler.
     Or: async logging (separate process for log shipping).
```

---

## perf — CPU Sampling and FlameGraph Generation

The tool that produces flamegraphs. `perf record` samples CPU call stacks at a configurable frequency. Near-zero overhead (sampling, not tracing).

### The Standard Workflow

```bash
# Record CPU samples for 30 seconds at 99 Hz (not 100 — avoids lockstep with timers)
perf record -F 99 -g -p <PID> -- sleep 30

# -F 99: Sample at 99 Hz (99 times per second)
# -g: Capture call graphs (stack traces) — essential for flamegraphs
# -p <PID>: Attach to specific process
# -- sleep 30: Record for 30 seconds

# Convert to flamegraph (requires FlameGraph scripts)
# Clone: git clone https://github.com/brendangregg/FlameGraph
perf script | ./FlameGraph/stackcollapse-perf.pl | ./FlameGraph/flamegraph.pl > flamegraph.svg

# Open flamegraph.svg in browser.

# Alternative: all in one command
perf record -F 99 -g -a -- sleep 30 && \
  perf script | curl -s http://localhost:3000/flamegraph -d @- > flamegraph.svg
  # (Assuming you're running a flamegraph server, or use speedscope.app)
```

### Sampling All CPUs (System-Wide)

```bash
# Sample all CPUs — find WHO is consuming CPU on the whole machine
perf record -F 99 -g -a -- sleep 30
perf report  # Interactive TUI — navigate with arrows, expand with Enter
```

### Real Scenario: Finding Hidden CPU Consumption

```
Symptom: Application CPU shows 40% in top. But application's own metrics show 15%.
         Where is the missing 25% going?

perf record -F 99 -g -a -- sleep 30
perf report

Shows:
  40% — [kernel] — vfs_write → ext4_file_write_iter → ... → jbd2 (journal)
  25% — /usr/lib/x86_64-linux-gnu/libz.so.1.2.11 — deflate_slow
  15% — /app/order-service — main → process_request → query_db

Diagnosis:
  - 40% in kernel journaling (jbd2): The disk is EXT4 with data=ordered journaling.
    Every write triggers a journal commit. Logging is writing synchronously.
  - 25% in zlib (deflate_slow): Logging library compresses every log line with gzip
    before writing it to disk.

  Combined: Logging library gzip-compresses every line, then writes synchronously.
  Each write triggers a journal commit. CPU consumed: 65% just for logging.

Fix:
  1. Set logging level to INFO (was DEBUG — reducing log volume by 90%)
  2. Disable gzip compression on logs (they go to stdout, Docker logging driver handles I/O)
  3. If compression needed: use zstd (faster) or lz4 (much faster)

Result: CPU drops from 40% to 18%. Logging CPU drops from 65% to 2%.
```

---

## bpftrace — Swiss Army Knife of Kernel Observability

bpftrace lets you write small programs that run in-kernel. Zero overhead for events you don't trace. Safe for production use. The most powerful observability tool that most SREs haven't learned yet.

### Essential One-Liners

```bash
# --- File I/O ---

# What files are being opened? (By whom, what path?)
bpftrace -e 'tracepoint:syscalls:sys_enter_openat {
    printf("%s %s\n", comm, str(args->filename));
}'
# Output:
# order-service /etc/resolv.conf
# order-service /app/config/production.yaml
# order-service /var/lib/data/orders.db
# order-service /tmp/session_abc123
# ← Shows every file open attempt. Find config files, temp files, unexpected reads.

# --- Networking ---

# Which processes are making TCP connections?
bpftrace -e 'kprobe:tcp_connect {
    @connections[comm] = count();
}'
# Output:
# @connections[order-service]: 5230
# @connections[payment-worker]: 1200
# @connections[curl]: 15

# Count TCP connections by destination port
bpftrace -e 'kprobe:tcp_connect {
    @dest_ports[comm, ntop(2, sk->__sk_common.skc_dport)] = count();
}'
# 2 = AF_INET for ntop()

# --- Process Execution ---

# What commands are being executed? (Catch shell injection, unexpected forks)
bpftrace -e 'tracepoint:syscalls:sys_enter_execve {
    printf("%s -> %s\n", comm, str(args->filename));
}'
# Output:
# sh -> /usr/bin/curl       ← Who spawned curl from a shell?
# node -> /usr/bin/sh       ← Node.js spawning shell (alarm!)
# python -> /usr/bin/git    ← Python running git (config management?)

# --- Syscall Distribution ---

# What syscalls are being called the most?
bpftrace -e 'tracepoint:syscalls:sys_enter_* {
    @[probe] = count();
}'
# Output (after Ctrl+C):
# @[tracepoint:syscalls:sys_enter_futex]: 1240523
# @[tracepoint:syscalls:sys_enter_write]: 450123
# @[tracepoint:syscalls:sys_enter_read]: 328901
# ← futex() = lock contention. write() > read() = write-heavy workload.

# --- Disk I/O ---

# Write size distribution per process
bpftrace -e 'tracepoint:syscalls:sys_enter_write {
    @write_bytes[comm] = hist(args->count);
}'
# Output:
# @write_bytes[order-service]:
# [256, 512)   3240 |@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@      | (small writes — logging?)
# [1K, 2K)     1523 |@@@@@@@@@@@@@                        |
# [8K, 16K)      12 |                                      |
# [64K, 128K)     5 |                                      |
# ← Many small writes = inefficient disk I/O (should batch)

# --- Memory Allocation ---

# Track brk() and mmap() — memory allocation syscalls
bpftrace -e 'tracepoint:syscalls:sys_enter_brk,
             tracepoint:syscalls:sys_enter_mmap {
    @allocations[comm] = count();
}'
# Which process is allocating memory fastest?

# --- Process Lifecycle ---

# Track process creation and exit
bpftrace -e 'tracepoint:sched:sched_process_fork {
    printf("PARENT %s (pid=%d) spawned CHILD %d\n", comm, pid, args->child_pid);
}
tracepoint:sched:sched_process_exit {
    printf("EXIT %s (pid=%d)\n", comm, pid);
}'
# Find processes being spawned and dying rapidly (fork bombs, crash loops).
```

### Real Scenario: Finding a Hidden DNS Problem with bpftrace

```
Symptom: Order service p99 latency spikes randomly to 8 seconds.
         No errors in logs. No slow DB queries. CPU normal. Memory normal.
         Jaeger shows 5-8s gaps in traces — spans with no children but long duration.

Hypothesis: DNS resolution is slow. Maybe /etc/resolv.conf has a bad nameserver?

bpftrace -e 'kprobe:sendmsg {
    $sock = (struct socket *)arg0;
    $dport = $sock->sk->__sk_common.skc_dport;
    $dport = ($dport >> 8) | (($dport << 8) & 0xff00);  // ntohs
    if ($dport == 53) {  // DNS port
        @dns[comm, pid] = count();
        @dns_latency = hist(nsecs / 1000000);  // milliseconds
    }
}'

Output:
  @dns[order-service, 1423]: 42 (42 DNS calls in 30s)

But also:
  @dns_latency:
  [0, 1)      30 |@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@      | (good: <1ms)
  [1, 2)       8 |@@@@@@@                               |
  [5000, 10000) 4 |@@@@                                 | ← 4 requests took 5-10 SECONDS

Root cause: /etc/resolv.conf has 3 nameservers. First two are internal (fast).
            Third is an old decommissioned server (10.0.1.53).
            When first two timeout (rarely), resolver tries #3.
            TCP timeout for DNS = 5 seconds by default.
            4 requests × 5 seconds = 20 seconds of DNS waiting distributed across requests.

Fix: Remove the decommissioned nameserver from resolv.conf.
     Or: reduce DNS timeout via resolv.conf: `options timeout:1 attempts:2`
```

---

## tcpdump + tshark — Network Analysis Scripted

### Rolling Packet Capture (Minimal Overhead)

```bash
# Capture 60 seconds of rolling window — useful for catching intermittent issues
tcpdump -i eth0 -w capture.pcap -G 60 -W 1 -s 0 \
  'host 10.0.1.53 and port 53'
# -G 60: rotate every 60 seconds
# -W 1: keep only 1 file (overwrite — minimal disk)
# -s 0: capture full packets (not just headers)

# Capture specific TCP flags
tcpdump -i eth0 -nn 'tcp[tcpflags] & (tcp-syn|tcp-fin) != 0'  # SYN and FIN only
tcpdump -i eth0 -nn 'tcp[tcpflags] & tcp-rst != 0'            # RST only

# Capture by process (using cgroups — container-aware)
tcpdump -i eth0 -w /tmp/capture.pcap \
  'cgroup == "/kubepods/burstable/pod-abc-123/container-order-service"'
```

### tshark Analysis (No GUI Needed)

```bash
# I/O statistics — see traffic patterns per second
tshark -r capture.pcap -q -z io,stat,1
# Output:
# | Interval    | Frames  | Bytes     |
# | 0.0 <> 1.0  | 4523    | 2345678   |
# | 1.0 <> 2.0  | 12340   | 8765432   | ← Spike: something happened here

# TCP conversation statistics
tshark -r capture.pcap -q -z conv,tcp
# Output:
# Src IP          | Src Port | Dst IP          | Dst Port | Packets | Bytes
# 10.0.1.10      | 52341    | 10.0.1.53      | 53       | 48      | 3456
# ^ 48 DNS lookups from order-service to DNS server

# Analyze TCP retransmissions
tshark -r capture.pcap -q -z io,stat,1,'tcp.analysis.retransmission'
# Count retransmissions per second

# HTTP request analysis
tshark -r capture.pcap -Y "http.request" -T fields \
  -e frame.time -e ip.src -e http.host -e http.request.uri
```

### Real Scenario: Finding Intermittent Connection Failures

```
Symptom: 0.5% of requests to payment-service fail with "connection refused."
         No pattern by time of day, request size, or endpoint.
         The failures are NOT captured by application metrics (they never reach the app).

tcpdump -i eth0 -w /tmp/payment.pcap -G 300 -W 1 'host payment-service'

tshark -r /tmp/payment.pcap -Y "tcp.flags.reset==1" -T fields \
  -e frame.time -e ip.src -e tcp.srcport

Output:
  Jun 11, 2026 10:14:23.456789  10.0.1.10   52341
  Jun 11, 2026 10:14:23.789012  10.0.1.10   52342
  ... 200 RSTs from 10.0.1.10 (payment-service) in 5 minutes

  All RSTs from payment-service, not from order-service.
  The server is actively refusing connections.

  Check payment-service:
    ss -s
    TCP: 5000 (estab 5000, closed 0, ...)
    ^ Exactly 5000 established. That's the net.core.somaxconn limit.

  Root cause: payment-service's listen backlog is 5000 (default).
             When >5000 connections arrive, kernel sends RST.
             The application cannot even see these connections — no error log,
             no metric — kernel rejects them before accept().

  Fix: sysctl -w net.core.somaxconn=16384
       Also: increase application backlog: server.listen(5000) → server.listen(16000)
```

---

## /proc — The Process Debugging Interface

The `/proc` filesystem exposes the kernel's view of every process. No tools required — just `cat` and `echo`.

```bash
# What is this process blocked on? (KERNEL stack — not userspace)
cat /proc/<PID>/stack
# [<0>] _ep_poll+0x...     ← blocked in epoll_wait() — normal I/O loop
# [<0>] _futex_wait+0x...  ← blocked on a lock — contention
# [<0>] wait_for_completion+0x... ← waiting for a kernel event

# What syscall is this process currently executing?
cat /proc/<PID>/syscall
# 0 0x7 0x7ffe1234 0x1000 0x0 0x0 0x0 0x7ffe1234 0x7f...
# ^ syscall 0 = read | fd=7 | buf=0x7ffe1234 | count=0x1000

# I/O statistics for this process
cat /proc/<PID>/io
# rchar: 524288000     (bytes read via read syscalls — includes from cache)
# wchar: 1048576000    (bytes written via write syscalls)
# read_bytes: 1048576  (bytes actually read from storage)
# write_bytes: 524288000 (bytes actually written to storage)
# cancelled_write_bytes: 104857600 (writes cancelled — application wrote, but
#                                    file was deleted or truncated before sync)

# Memory map — what's in this process's address space?
cat /proc/<PID>/maps
# Shows every mapped region: code, heap, stack, mmap'd files, shared libraries
# 7f1234000000-7f1238000000 rw-p 00000000 00:00 0          [heap]
# 7f123c000000-7f123c021000 r-xp 00000000 08:01 123456     /usr/lib/libc.so.6

# File descriptors in use
ls -la /proc/<PID>/fd/
# Shows every open FD and what it points to
# 3 -> socket:[54321]       (socket)
# 4 -> /var/log/app.log     (file)
# 5 -> pipe:[12345]         (pipe)
# 6 -> anon_inode:[eventfd] (eventfd)

# Details for a specific FD
cat /proc/<PID>/fdinfo/4
# pos: 5242880              (current file position — 5MB into file)
# flags: 0100002            (O_RDWR|O_LARGEFILE)

# Environment variables (how was this process started?)
cat /proc/<PID>/environ | tr '\0' '\n'

# Command line
cat /proc/<PID>/cmdline | tr '\0' ' '
```

### Real Scenario: Finding a File Descriptor Leak

```
Symptom: order-service crashes every 6 hours with:
        "OSError: [Errno 24] Too many open files"

ulimit -n shows 1024 (default).

Check current FDs:
  ls /proc/<PID>/fd/ | wc -l
  → 1004 (close to limit — minutes from crashing)

What are they?
  ls -la /proc/<PID>/fd/ | awk '{print $NF}' | sort | uniq -c | sort -rn
  →
    852 socket:[*]        ← 852 open sockets!
     89 /var/log/app.log
     45 /tmp/session_*
     18 pipe:[*]

  852 sockets is NOT normal.

  ls -la /proc/<PID>/fd/ | grep socket | head -20
  All pointing to the same IP: 10.0.1.20:6379 (Redis)
  But the connection state from /proc/net/tcp:
    cat /proc/net/tcp | awk '{print $4}' | sort | uniq -c | sort -rn
    → 800 connections in CLOSE_WAIT state! ← NOT being closed.

Root cause: Redis client library not closing connections properly.
           Each request opens a new connection. On response, connection goes
           to CLOSE_WAIT but app never calls close().
           Library version 3.2.0 — bug fixed in 3.3.1.

Fix: Upgrade Redis client library. Apply ulimit increase from 1024 to 65536 as immediate mitigation.
```

---

## auditd — Syscall Auditing

Track who does what to which files. Essential for security incidents AND for finding unexpected modifications.

```bash
# Watch a specific file for modifications
auditctl -w /etc/nginx/nginx.conf -p wa -k nginx_config
# -w: watch path
# -p wa: watch writes (w) and attribute changes (a)
# -k: key for searching audit logs

# Watch a directory recursively
auditctl -w /etc/kubernetes/ -p wa -k k8s_config

# Watch execution of a specific binary
auditctl -a always,exit -F arch=b64 -S execve -F path=/usr/bin/curl -k curl_exec

# Search audit logs
ausearch -k nginx_config
# Shows: who modified /etc/nginx/nginx.conf, when, via what process
# Output:
# type=SYSCALL msg=audit(06/11/2026 10:14:23.456:12345)
#   arch=x86_64 syscall=openat success=yes exit=4
#   auid=1000 uid=0 gid=0 euid=0 suid=0
#   comm="puppet" exe="/opt/puppetlabs/bin/puppet"
#   ← Puppet modified nginx config. Normal for infrastructure-as-code.

# List all current audit rules
auditctl -l

# Delete a rule
auditctl -W /etc/nginx/nginx.conf -p wa -k nginx_config
```

### Real Scenario: Config File Keeps Reverting

```
Symptom: Nginx config at /etc/nginx/nginx.conf keeps reverting to an old version.
         Engineer fixes it manually → 5 minutes later, it reverts again.
         "Someone or something is overwriting my changes."

auditctl -w /etc/nginx/nginx.conf -p wa -k nginx_revert

ausearch -k nginx_revert -ts recent

Output shows:
  comm="monitoring-agent" exe="/opt/datadog/bin/agent"
  ← Datadog agent is overwriting nginx.conf every 5 minutes as part of its
    "configuration drift remediation" feature.

Root cause: Datadog's config remediation is restoring a config baseline from
            2 months ago. The nginx config template in Datadog was not updated
            when the manual change was made.

Fix: Update the config template in Datadog, OR disable the remediation feature.
```

---

## inotifywait — File Change Monitoring

Watch for real-time file system events. Simpler than auditd for quick investigations.

```bash
# Watch a directory for any changes
inotifywait -m -r /etc/nginx/
# -m: monitor continuously (don't exit after first event)
# -r: recursive
# Output:
# /etc/nginx/ MODIFY nginx.conf
# /etc/nginx/sites-enabled/ DELETE default
# /etc/nginx/sites-enabled/ CREATE mysite

# Watch for specific events
inotifywait -m -r -e modify,create,delete /etc/nginx/

# Watch with timestamp
inotifywait -m -r --timefmt '%Y-%m-%d %H:%M:%S' --format '%T %w%f %e' /etc/nginx/
```

---

## strace — The Nuclear Option

```
WARNING: strace adds SIGNIFICANT overhead (up to 50-90% slowdown).
         NEVER run on a production process that's actively serving traffic.
         Use for: crashed processes, hung processes, or isolated investigation.
```

### When It's Worth It

```bash
# What files is this process trying to open? (Hung process — acceptable overhead)
strace -p <PID> -e trace=open,openat -f 2>&1 | grep -v ENOENT

# What network connections is it making?
strace -p <PID> -e trace=connect,sendto,recvfrom -f

# Why is it slow? (Count syscalls and their duration)
strace -p <PID> -c -f
# Shows summary: which syscalls took most time
# % time     seconds  usecs/call     calls    errors syscall
#  45.23    2.345678       23456       100         0 futex     ← lock contention
#  32.10    1.654321       12345       134         0 write     ← high write volume
#  12.45    0.654321        6543       100         0 poll      ← I/O multiplexing
```

### Real Scenario: Permission Denied Mystery

```
Symptom: order-service starts but immediately exits with no error in application logs.
         Application's stdout shows nothing after "Starting..."

strace -f -o /tmp/trace.log /app/order-service

From /tmp/trace.log:
  12345 openat(AT_FDCWD, "/app/config/production.yaml", O_RDONLY) = -1 EACCES
  12345 write(2, "Starting...\n", 11)       = 11     ← printed
  12345 exit_group(1)                       = ?

  The file /app/config/production.yaml exists but has permissions 0600, owned by root.
  The application runs as user "app" (uid 1000) → EACCES (Permission denied).

  The application has no error handling for config file read failure.
  It calls exit(1) silently.

Fix: chown app:app /app/config/production.yaml
     Also: add error handling to log "Cannot read config file: <path>" before exit().
```

---

## Tool Selection Matrix

```
Problem Type        | Tool              | Production Safe? | Overhead
--------------------|-------------------|------------------|---------
Process hung        | gdb -p            | Yes (process already stuck) | Freezes process
CPU hot             | perf record       | Yes              | <1%
CPU hot (Python)    | py-spy            | Yes              | 0%
What syscalls?      | bpftrace          | Yes              | 0%
Network packets     | tcpdump           | Yes              | <5%
File changes        | inotifywait       | Yes              | 0%
File changes (audit)| auditd            | Yes              | <1%
Process blocked     | cat /proc/PID/stack| Yes              | 0%
All syscalls        | strace            | NO (unless hung) | 50-90% slowdown
```

---

*See also: [10x Mindset](10x-mindset.md) | [Chaos Engineering](chaos-engineering.md) | [Capacity Planning](capacity-planning.md)*
