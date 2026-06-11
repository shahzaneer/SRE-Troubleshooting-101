# systemd Troubleshooting
> **Category:** Linux | systemd | Services
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#linux` `#systemd` `#services` `#oncall`

---

## Table of Contents
1. [Service Status Commands](#1-service-status-commands)
2. [Unit File Anatomy](#2-unit-file-anatomy)
3. [Service Won't Start — Full Diagnostic Flow](#3-service-wont-start--full-diagnostic-flow)
4. [Socket Activation](#4-socket-activation)
5. [Cgroup Limits](#5-cgroup-limits)
6. [Timer Units vs Cron](#6-timer-units-vs-cron)
7. [Real Scenarios](#7-real-scenarios)

---

## 1. Service Status Commands

```bash
# ─── Basic Status ───
systemctl status nginx
# ● nginx.service - A high performance web server
#      Loaded: loaded (/lib/systemd/system/nginx.service; enabled; preset: enabled)
#      Active: active (running) since Thu 2026-06-11 14:32:15 UTC; 2h 15min ago
#        Docs: man:nginx(8)
#    Main PID: 12345 (nginx)
#       Tasks: 9 (limit: 38142)
#      Memory: 45.2M
#         CPU: 1min 23.456s
#      CGroup: /system.slice/nginx.service
#              ├─12345 nginx: master process /usr/sbin/nginx
#              ├─12346 nginx: worker process
#              ├─12347 nginx: worker process
#              ├─12348 nginx: worker process
#              └─12349 nginx: worker process
#
# Loaded line decoded:
#   /lib/systemd/system/nginx.service = unit file path
#   enabled = starts at boot
#   preset: enabled = distro default is enabled
#
# Active line decoded:
#   active (running) = running normally
#   active (exited) = oneshot service finished successfully
#   inactive (dead) = stopped or never started
#   activating (start) = starting up
#   deactivating (stop) = shutting down
#   failed = exited with error or timed out

# Boolean checks for scripting
systemctl is-active nginx    # "active" or "inactive" — exit code 0/3
systemctl is-enabled nginx   # "enabled"/"disabled"/"static" — exit code 0/1
systemctl is-failed nginx    # "active" or "failed" — exit code 0/1

# Which units have failed?
systemctl list-units --state=failed
#   UNIT            LOAD   ACTIVE SUB    DESCRIPTION
# ● myapp.service   loaded failed failed My Application

# Reset the failed state (so systemd doesn't think it's still failing)
systemctl reset-failed myapp

# All active units
systemctl list-units --state=active

# Dependencies: what does this unit depend on?
systemctl list-dependencies nginx
# nginx.service
# ● ├─system.slice
# ● ├─-.mount
# ● └─sysinit.target
# ...
# In reverse: what depends ON this unit?
systemctl list-dependencies --reverse nginx

# Show the actual unit file
systemctl cat nginx

# Reload systemd after editing unit files
systemctl daemon-reload

# Mask a service (prevent it from being started, even as a dependency)
systemctl mask bad-service
# Unmask:
systemctl unmask bad-service
```

---

## 2. Unit File Anatomy

### Complete Unit File Reference

```ini
[Unit]
Description=My Application
Documentation=https://wiki.internal/myapp
# Documentation links show in systemctl status/docs command.

After=network-online.target postgresql.service
# Ordering only: after these targets/services have STARTED.
# Does NOT imply dependency (use Requires/Wants for dependency).

Wants=network-online.target
# Soft dependency: if it's available, start it too. If it fails, my app still starts.

Requires=postgresql.service
# Hard dependency: if postgres fails to start, my app fails too.

BindsTo=postgresql.service
# Like Requires, but if postgres stops, my app also stops.

Before=apache2.service
# Start BEFORE apache2.

Conflicts=apache2.service
# Cannot coexist with apache2. If my app starts, apache2 stops, and vice versa.

ConditionPathExists=/etc/myapp/config.yaml
# Start ONLY if this file exists. If condition fails, skip quietly.
ConditionDirectoryNotEmpty=/var/lib/myapp/data
ConditionHost=web-07
ConditionKernelVersion=>=5.10
# AssertPathExists=  — same as Condition, but FAILS LOUDLY if not met.

[Service]
Type=simple
# simple:  process started by ExecStart is the main process (default)
# forking: process call fork() and exits; parent PID is the main process
# oneshot: like simple, but systemd waits for process to exit before continuing
#          (use RemainsAfterExit=yes for services that config system state and exit)
# notify:  service sends sd_notify("READY=1") when started
# dbus:    service acquires a D-Bus name
# idle:    like simple, but delayed until all jobs dispatched

ExecStart=/usr/local/bin/myapp --config /etc/myapp/config.yaml
# The command to start. Must be an absolute path.
# For scripts: ExecStart=/bin/bash /usr/local/bin/start-myapp.sh

ExecStartPre=/usr/local/bin/myapp-migrate-db
# Run before ExecStart. Multiple allowed (run in order).

ExecStartPost=/usr/local/bin/myapp-health-check
# Run after the main process starts.

ExecStop=/usr/local/bin/myapp-shutdown
# Command to stop the service. If not set, systemd sends SIGTERM then SIGKILL.

ExecReload=/usr/local/bin/myapp-reload
# Command for systemctl reload myapp (e.g., SIGHUP or config reload)

Restart=on-failure
# no:             never restart (default)
# on-success:     restart only if exit code is 0
# on-failure:     restart if exit code is non-zero or process is killed
# on-abnormal:    restart if killed by signal (not clean exit), timeout, or watchdog
# on-watchdog:    restart only if watchdog timeout triggers
# on-abort:       restart only if killed by uncaught signal without core dump
# always:         ALWAYS restart (including clean exit) — care, can restart-loop

RestartSec=5s
# Wait 5 seconds before restarting

TimeoutStartSec=30s
# Wait maximum 30s for ExecStart to complete. If exceeded, killed (SIGTERM then SIGKILL).

TimeoutStopSec=30s
# Wait maximum 30s for ExecStop to complete. If exceeded, killed.
# After SIGKILL: if still alive, process goes to "failed" state.
# IMPORTANT: set this longer than your graceful shutdown takes!

TimeoutAbortSec=30s
# If the service is killed, timeout for aborting (same as TimeoutStopSec if not set).

User=myapp
Group=myapp
# Run service as this user/group. NEVER run as root if avoidable.

WorkingDirectory=/var/lib/myapp
# cd to this directory before executing.

RootDirectory=/srv/myapp-chroot
# chroot to this directory (rarely used, complex setup).

Environment="APP_ENV=production"
Environment="CONFIG_PATH=/etc/myapp"
EnvironmentFile=-/etc/default/myapp
# The '-' means: don't fail if file doesn't exist.

UMask=027

Nice=-5                             # CPU priority (-20 highest, 19 lowest)
OOMScoreAdjust=-500                 # OOM killer bias (-1000 never kill, 1000 always kill)

LimitNOFILE=65536                   # Max open file descriptors
LimitNPROC=32768                    # Max processes/threads
LimitMEMLOCK=infinity               # Max locked memory (for JVM, crypto)
LimitCORE=infinity                  # Max core dump size
LimitCPU=infinity                   # Max CPU time (safeguard against runaway)
LimitRSS=infinity                   # Max physical memory

MemoryMax=2G                        # cgroup v2: absolute memory limit
MemoryHigh=1.5G                     # cgroup v2: soft limit, throttles above this
CPUQuota=200%                       # Max CPU: 100% = 1 core, 200% = 2 cores
CPUWeight=100                       # CPU scheduler weight (100 = default)
IOWeight=100                        # I/O scheduler weight (100 = default)

TasksMax=512                        # Max number of tasks/threads in this cgroup

PrivateTmp=yes                      # Give service a private /tmp (isolated)
PrivateDevices=yes                  # Only expose /dev/null, /dev/zero, etc.
PrivateNetwork=no                   # Isolate network (for security-critical services)
ProtectSystem=full                  # Read-only /usr, /boot, /etc
ProtectHome=yes                     # /home, /root, /run/user appear empty
NoNewPrivileges=yes                 # Prevent privilege escalation (setuid, etc.)

ReadWritePaths=/var/lib/myapp /var/log/myapp
# When ProtectSystem=full, explicitly allow writes to these paths.
ReadOnlyPaths=/etc/myapp
# Make these paths read-only for the service.

StandardOutput=journal              # Log stdout to journald
StandardError=journal               # Log stderr to journald
SyslogIdentifier=myapp              # Tag in syslog/journal

WatchdogSec=30s
# Service must call sd_notify("WATCHDOG=1") at least every 30s.
# If it doesn't, systemd kills and restarts it.

KillMode=mixed
# control-group: kill ALL processes in cgroup (default)
# process:       kill only main process
# mixed:         kill main process with SIGTERM, then SIGKILL to children
# none:          don't kill anything

KillSignal=SIGTERM                   # Signal to send on stop (default: SIGTERM)
SendSIGHUP=no                       # Send SIGHUP after SIGTERM? (for shells)

[Install]
WantedBy=multi-user.target
# Install as a dependency of multi-user.target (standard for most services).
# systemctl enable myapp → creates symlink in /etc/systemd/system/multi-user.target.wants/
```

---

## 3. Service Won't Start — Full Diagnostic Flow

### The Systematic Approach

When a service fails to start, follow this flow **in order**. Don't jump to step 5 before step 1.

```bash
SERVICE=myapp

# ─── STEP 1: systemctl status — what went wrong? ───
systemctl status $SERVICE
# Look at the Active line and any error messages.
# ● myapp.service - My Application
#      Loaded: loaded (/etc/systemd/system/myapp.service; enabled)
#      Active: failed (Result: exit-code) since Thu 2026-06-11 14:32:15 UTC
#     Process: 28471 ExecStart=/usr/local/bin/myapp (code=exited, status=1/FAILURE)
#    Main PID: 28471 (code=exited, status=1/FAILURE)
#         CPU: 12ms
#
# Key info: "code=exited, status=1/FAILURE" → the ExecStart command returned exit code 1.
# "Result: timeout" → ExecStart exceeded TimeoutStartSec.
# "Result: signal" → process was killed by a signal (check "killed by SIGSEGV" etc.)

# ─── STEP 2: journalctl — what did the app log? ───
journalctl -u $SERVICE -xe --no-pager -n 100
# -x: show explanatory text for log entries
# -e: jump to end (latest entries)
# -n 100: last 100 lines
# 
# Look for:
# - Python traceback
# - Java exception / stack trace
# - "Permission denied" (file permissions)
# - "No such file or directory" (missing binary or config)
# - "Address already in use" (port already bound)
# - OutOfMemoryError
# - Database connection failures

# ─── STEP 3: Run the ExecStart command manually ───
# Extract the exact command from the unit file:
systemctl cat $SERVICE | grep ExecStart
# ExecStart=/usr/local/bin/myapp --config /etc/myapp/config.yaml

# Run it as root manually (with the service's environment):
# This tells you if the problem is the app itself or systemd's environment.
/usr/local/bin/myapp --config /etc/myapp/config.yaml

# If it fails with a clear error, you've found the issue.
# If it works manually but fails under systemd, the problem is environment-related.

# ─── STEP 4: Check file permissions ───
# Does the binary exist?
ls -la /usr/local/bin/myapp
# Is it executable?
file /usr/local/bin/myapp

# Can the service user access it?
sudo -u myapp /usr/local/bin/myapp --version

# Config file permissions:
ls -la /etc/myapp/config.yaml
# Is the config readable by the service user?

# Working directory exists?
ls -la /var/lib/myapp

# Log directory writable?
sudo -u myapp touch /var/log/myapp/test-write

# ─── STEP 5: Check environment variables ───
systemctl show $SERVICE --property=Environment
# Environment=APP_ENV=production CONFIG_PATH=/etc/myapp

# Run the service with the same environment:
systemctl show $SERVICE --property=Environment | \
  sed 's/Environment=//' | \
  xargs -I{} env {} /usr/local/bin/myapp

# ─── STEP 6: Check dynamic dependencies ───
# Is the required database running?
systemctl show $SERVICE --property=After
# Is Postgres actually running?
systemctl is-active postgresql

# Is the required network up?
systemctl is-active network-online.target

# ─── STEP 7: Check for condition failures ───
systemctl show $SERVICE --property=ConditionResult
# ConditionResult=no → a ConditionPathExists or similar check failed
systemctl show $SERVICE --property=ConditionTimestamp

# Which condition failed?
journalctl -u $SERVICE --no-pager | grep -i condition
# "Condition check resulted in ConditionPathExists=/etc/myapp/config.yaml being skipped."
# → The file doesn't exist! (Deleted? Never created? Wrong path?)

# ─── STEP 8: Check for resource limits ───
systemctl show $SERVICE --property=LimitNOFILE --property=LimitNPROC

# ─── STEP 9: strace the start attempt (last resort) ───
# Edit the unit temporarily:
# ExecStart=/usr/bin/strace -f -o /tmp/myapp-startup.strace /usr/local/bin/myapp
# systemctl daemon-reload && systemctl start myapp
# Then analyze /tmp/myapp-startup.strace

# ─── STEP 10: Increase timeout for slow-starting services ───
systemctl show $SERVICE --property=TimeoutStartSec
# TimeoutStartSec=90s
# If your app needs more time (database migrations, cache warmup):
# systemctl edit myapp  (creates override)
# [Service]
# TimeoutStartSec=300
# systemctl daemon-reload && systemctl start myapp
```

---

## 4. Socket Activation

Socket activation is a systemd feature where systemd creates the listening socket and passes it to the service. The service doesn't need to be running until a connection arrives — systemd starts it on-demand.

```bash
# List all socket-activated services
systemctl list-sockets
# LISTEN         UNIT                   ACTIVATES
# /run/dbus/system_bus_socket  dbus.socket            dbus.service
# 0.0.0.0:80                   nginx.socket           nginx.service
# [::]:22                      sshd.socket            sshd.service
# /run/docker.sock             docker.socket          docker.service

# Check a socket unit status
systemctl status nginx.socket

# To debug a socket-activated service:
# 1. Start the socket unit
systemctl start myapp.socket

# 2. Verify the socket is listening
ss -tlnp | grep 8080
# LISTEN  0  128  *:8080  *:*  users:(("systemd",pid=1,fd=45))

# 3. Trigger the service by connecting
curl http://localhost:8080/health

# 4. The service should now be running
systemctl status myapp.service

# 5. If the service doesn't start on connection:
# Check the socket unit references:
systemctl cat myapp.socket
```

### Socket Unit Example

```ini
# myapp.socket
[Unit]
Description=MyApp Socket

[Socket]
ListenStream=0.0.0.0:8080
# ListenStream: TCP socket
# ListenDatagram: UDP socket
# ListenSequentialPacket: Unix seqpacket socket

# Socket options:
NoDelay=yes
KeepAlive=yes
Backlog=128
# MaxConnections=256          # Max simultaneous connections

# Accept=yes means systemd calls accept() and passes each connection fd
# Accept=no means systemd passes the listening socket fd (service does accept())
Accept=no

# Socket service activation
Service=myapp.service

[Install]
WantedBy=sockets.target
```

```ini
# myapp.service (the service activated by the socket)
[Service]
ExecStart=/usr/local/bin/myapp --systemd-socket
# The service receives the socket as fd 3 (systemd passes LISTEN_FDS=1)
# Apps must support socket activation (use sd_listen_fds() or check LISTEN_PID/LISTEN_FDS env vars)

NonBlocking=yes
# Socket activated services should use non-blocking I/O
```

---

## 5. Cgroup Limits

systemd uses cgroups v2 (on modern kernels) to track and limit resources per service.

```bash
# ─── View cgroup resource usage ───

# Interactive top for cgroups
systemd-cgtop
# Control Group                           Tasks   %CPU   Memory  Input/s Output/s
# /                                         234    45.2    3.4G        -        -
# /system.slice                             156    42.1    2.8G        -        -
# /system.slice/nginx.service                 9     2.3   45.2M        -        -
# /system.slice/postgresql.service           45    15.7    1.2G        -        -
# /system.slice/myapp.service                12    20.3  980.5M        -        -
# /user.slice                                78     3.1  600.2M        -        -

# Show current memory/CPU limits per service
systemctl show myapp.service --property=MemoryMax --property=MemoryCurrent --property=MemoryHigh

# ─── Set cgroup limits ───

# Runtime (immediate, not persistent):
systemctl set-property myapp.service MemoryMax=2G
systemctl set-property myapp.service MemoryHigh=1.5G
systemctl set-property myapp.service CPUQuota=200%
systemctl set-property myapp.service CPUWeight=50
systemctl set-property myapp.service TasksMax=512

# Persistent (written to /etc/systemd/system.control/):
systemctl set-property --runtime myapp.service MemoryMax=2G  # only for this boot

# View all properties:
systemctl show myapp.service | grep -E "(Memory|CPU|Tasks|IO)"

# ─── Monitor cgroup events ───

# Set up cgroup notification for memory pressure:
cat /sys/fs/cgroup/system.slice/myapp.service/memory.events
# low 0
# high 0
# max 123    ← OOM killed 123 times in this cgroup
# oom 0
# oom_kill 123

# Check if the service is throttled:
cat /sys/fs/cgroup/system.slice/myapp.service/cpu.stat
# usage_usec 123456789
# user_usec 987654321
# system_usec 234567890
# nr_periods 1000
# nr_throttled 5     ← throttled 5 times (exceeded CPUQuota)
# throttled_usec 50000123

# ─── Cgroup Resource Controllers ───
cat /sys/fs/cgroup/system.slice/myapp.service/cgroup.controllers
# cpuset cpu io memory hugetlb pids rdma misc
# Shows which resource controllers are available.
# To add IO limits: io.max, io.weight
# To add CPU limits: cpu.max, cpu.weight
```

---

## 6. Timer Units vs Cron

### Timer Unit Example

systemd timers are more precise, better integrated, and easier to debug than cron. They can randomize execution time, run missed executions, and trigger on calendar events or monotonic timers.

```ini
# /etc/systemd/system/myapp-backup.timer
[Unit]
Description=Daily Backup Timer
Requires=myapp-backup.service

[Timer]
OnCalendar=daily
# OnCalendar=*-*-* 03:00:00        # Every day at 3 AM UTC
# OnCalendar=Mon..Fri 02:00:00     # Weekdays at 2 AM
# OnCalendar=*-*-1,15 01:00:00     # 1st and 15th of every month
# OnCalendar=Sat *-*-1..7 02:00:00 # First Saturday of each month

Persistent=yes
# If the system was powered off at the scheduled time, run immediately on next boot.

RandomizedDelaySec=300
# Randomly delay by up to 5 minutes (avoid thundering herd when many timers fire).

OnUnitActiveSec=24h
# Monotonic: run 24 hours after the service unit was last activated.
# Can be combined with OnCalendar.
# OnBootSec=5min  → run 5 minutes after boot

AccuracySec=1s
# Default: 1 minute. How precise the timer must be.
# Lower = more precise but higher power consumption.

[Install]
WantedBy=timers.target
```

```ini
# /etc/systemd/system/myapp-backup.service
[Unit]
Description=MyApp Backup

[Service]
Type=oneshot
ExecStart=/usr/local/bin/myapp-backup
User=backup
Group=backup
ProtectSystem=full
PrivateTmp=yes
```

### Timer Management Commands

```bash
# List all timers with next run time
systemctl list-timers --all
# NEXT                         LEFT          LAST                         PASSED       UNIT
# Thu 2026-06-12 03:00:00 UTC  12h left      Wed 2026-06-11 03:00:01 UTC  9h ago       myapp-backup.timer
# Thu 2026-06-12 00:00:00 UTC  9h left       Wed 2026-06-11 00:00:04 UTC  12h ago      logrotate.timer
# ...
# Shows: when will it run next, when did it last run, which unit.

# Enable and start a timer
systemctl enable --now myapp-backup.timer

# Check timer status
systemctl status myapp-backup.timer
# This shows the timer's next elapse time and matches.

# Force a timer to trigger NOW (test/debug)
systemctl start myapp-backup.service  # run the underlying service

# Check the last run result
systemctl status myapp-backup.service
# "active (exited)" with "SUCCESS" or "FAILURE"

# Debug a timer that's not firing:
# 1. Is the timer active?
systemctl is-active myapp-backup.timer

# 2. When is it scheduled?
systemctl show myapp-backup.timer --property=NextElapseUSecRealtime

# 3. Did it run and fail silently?
journalctl -u myapp-backup.service --since "1 day ago"

# 4. Override the timer schedule for testing:
systemctl edit myapp-backup.timer
# [Timer]
# OnCalendar=*-*-* *:*:00   # Every minute
# Then: systemctl daemon-reload && systemctl restart myapp-backup.timer
```

### Timer vs Cron Comparison

```
Feature                  systemd Timer        Cron
────────────────────────────────────────────────────────
Randomized delay        RandomizedDelaySec   sleep $((RANDOM % N))
Persistent (catch up)   Persistent=yes        anacron (add-on)
Execution logging       journalctl -u unit    /var/log/cron (text)
Environment isolation   systemd exec env      Cron's limited env (no PATH often)
Dependency awareness     Yes (can require services)  No
Error handling          OnFailure=            MAILTO= only
Resource limits          MemoryMax, CPUQuota   none
Security isolation      PrivateTmp, etc.      none
Test/debug              systemctl start svc   Run script manually
Time zones               UTC + TZ=            System time zone
```

---

## 7. Real Scenarios

### Scenario 1: PrivateTmp Breaks the App

> **After OS patch and reboot,** myapp fails to start. `systemctl status myapp` shows "exit-code" with "No such file or directory."
>
> ```bash
> journalctl -u myapp -xe
> # myapp[12345]: Fatal: Unable to open /tmp/myapp-lock: No such file or directory
> 
> systemctl cat myapp | grep PrivateTmp
> # PrivateTmp=yes
> ```
>
> Root cause: The security team added `PrivateTmp=yes` to all service units as part of a hardening push. The app binary was compiled 5 years ago and has `/tmp/myapp-lock` hardcoded. With PrivateTmp enabled, the app sees `/tmp/systemd-private-XXXXXXX-myapp.service-XXXXXX/tmp/` instead of `/tmp/`. The old `/tmp/myapp-lock` is invisible.
>
> **Fix:**
> ```ini
> [Service]
> PrivateTmp=no
> # Or: BindReadWritePaths=/tmp/myapp-lock  (not ideal, still shares /tmp)
> # Better: fix the app to use /var/run/myapp/ or XDG_RUNTIME_DIR
> ```

### Scenario 2: ExecStart with Shell Variable Expansion

> **Service won't start:** `systemctl status` shows "code=exited, status=203/EXEC"
>
> ```bash
> systemctl cat myapp | grep ExecStart
> # ExecStart=/usr/local/bin/myapp --port $PORT
> 
> journalctl -u myapp
> # myapp.service: Failed to execute /usr/local/bin/myapp: No such file or directory
> ```
>
> Root cause: systemd does NOT expand shell variables in ExecStart. `$PORT` is treated literally. The command `/usr/local/bin/myapp --port $PORT` fails because the kernel can't find a binary at that exact path (with the dollar sign).
>
> **Fix:**
> ```ini
> [Service]
> Environment="PORT=8080"
> ExecStart=/usr/local/bin/myapp --port ${PORT}
> # Use curly braces for environment variable expansion.
> # Or: ExecStart=/bin/sh -c '/usr/local/bin/myapp --port $PORT'  (discouraged)
> ```

### Scenario 3: Restart=always Creates a Crash Loop

> **Service restarts constantly** — every 5 seconds, the service starts, crashes, restarts.
>
> ```bash
> systemctl status myapp
> # Active: activating (auto-restart) since Thu 2026-06-11 14:32:15 UTC
> # Process: 28471 ExecStart (code=exited, status=1/FAILURE)
> # ...
> # Active: activating (auto-restart) since Thu 2026-06-11 14:32:20 UTC
> # Process: 28472 ExecStart (code=exited, status=1/FAILURE)
> 
> journalctl -u myapp -f
> # (every 5 seconds: started → failed → restart)
> ```
>
> Root cause: `Restart=always` combined with a fatal error on startup. The app can't connect to the database because of wrong credentials. It exits with code 1. systemd's `Restart=always` restarts it. It fails again. Infinite loop.
>
> **Fix:**
> ```bash
> # Stop the crash loop first:
> systemctl stop myapp
> 
> # Fix the root cause (database credentials)
> 
> # Change Restart policy:
> # Restart=on-failure  ← still restarts on non-zero exit
> # Add restart rate limiting:
> # Restart=on-failure
> # RestartSec=5s
> # StartLimitIntervalSec=60   ← track restarts in a 60-second window
> # StartLimitBurst=5           ← after 5 restarts in that window, stop trying
> 
> # systemd automatically stops restarting after StartLimitBurst is exceeded.
> # This prevents infinite CPU-wasting crash loops.
> ```

### Scenario 4: TimeoutStopSec Too Short

> **During deployment,** old processes are SIGKILL'd mid-transaction, causing data corruption.
>
> ```bash
> systemctl cat myapp | grep TimeoutStopSec
> # TimeoutStopSec=90s (default)
> ```
>
> The app takes 2 minutes to gracefully shut down (flush pending writes, close DB connections, finish in-flight requests). systemd sends SIGTERM, waits 90 seconds, then sends SIGKILL. Data being flushed is lost.
>
> **Fix:**
> ```ini
> [Service]
> TimeoutStopSec=300
> # Or: TimeoutStopSec=infinity  (wait forever — risky if the process ignores SIGTERM)
> 
> # Also set ExecStop to your graceful shutdown command:
> ExecStop=/usr/local/bin/myapp-shutdown-graceful
> ```
>
> **Monitor time to stop:**
> ```bash
> # After a service stop, check how long it took:
> systemctl show myapp --property=ExecMainExitTimestamp --property=ActiveEnterTimestamp
> # Subtract to find the total stop time.
> ```

### Scenario 5: Oneshot Service That Never Returns

> **`systemctl start db-migrate` hangs forever.**
>
> ```ini
> [Service]
> Type=oneshot
> ExecStart=/usr/local/bin/db-migrate
> # Missing: TimeoutStartSec=
> ```
>
> The database migration tool hangs waiting for a database lock. Because `Type=oneshot` means systemd waits for the process to exit, and `TimeoutStartSec` defaults to infinity for oneshot services, systemd waits forever. Other services that depend on `db-migrate` block indefinitely.
>
> **Fix:**
> ```ini
> [Service]
> Type=oneshot
> ExecStart=/usr/local/bin/db-migrate
> TimeoutStartSec=300
> # If migration takes >5 minutes, it's considered failed.
> ```

### Scenario 6: Debugging Dependencies That Won't Start

```bash
# What's blocking my service from starting?
systemctl list-jobs
# JOB UNIT                     TYPE  STATE
# 123 myapp.service            start waiting
#  45 postgresql.service       start running
#
# myapp is WAITING for postgresql. postgresql is still starting.
# If postgresql never finishes starting, myapp waits forever (or times out).

# Check if any service is stuck "activating":
systemctl list-units --state=activating
# UNIT            LOAD   ACTIVE     SUB
# postgresql.service loaded activating start
# → postgresql is stuck starting — this blocks everything that depends on it.

# Force systemd to consider a service started:
# (only use when a service IS actually running but the notify mechanism failed)
systemctl reset-failed postgresql
```

### Quick Reference: Exit Codes and What They Mean

```bash
# When a systemd service fails, check the exit status:
systemctl show myapp --property=ExecMainStatus --property=Result

# Common exit codes (from ExecMainStatus):
# 0   = success
# 1   = generic error
# 2   = misuse of shell builtins (syntax error)
# 126 = command invoked cannot execute (permission, not executable)
# 127 = command not found
# 128 = invalid exit argument
# 128+N = killed by signal N (e.g., 128+9=137=SIGKILL, 128+15=143=SIGTERM)
# 200 = systemd-specific: timed out
# 203 = EXEC failure (couldn't execute the binary — check path, permissions)
# 217 = Reload failure (ExecReload failed)

# Common Result values:
# "success" = service started and exited normally (oneshot)
# "exit-code" = service exited with non-zero exit code
# "signal" = killed by signal
# "timeout" = exceeded TimeoutStartSec or TimeoutStopSec
# "core-dump" = process crashed with core dump
# "watchdog" = watchdog timer expired
# "resources" = cgroup resource limit hit
```
