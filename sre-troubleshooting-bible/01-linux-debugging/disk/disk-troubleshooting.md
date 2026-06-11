# Disk Troubleshooting
> **Category:** Linux | Disk | Storage
> **Difficulty:** Basic to Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#linux` `#disk` `#storage` `#oncall`

---

## Table of Contents
1. [Disk Full Emergency](#1-disk-full-emergency)
2. [Finding Large Files](#2-finding-large-files)
3. [Deleted-but-Open Files](#3-deleted-but-open-files)
4. [Inode Exhaustion](#4-inode-exhaustion)
5. [I/O Bottlenecks (iostat)](#5-io-bottlenecks-iostat)
6. [Disk Latency](#6-disk-latency)
7. [LVM Operations](#7-lvm-operations)
8. [fio Benchmarks](#8-fio-benchmarks)
9. [Python: Disk Monitoring Script](#9-python-disk-monitoring-script)
10. [Java: NIO Disk Operations](#10-java-nio-disk-operations)

---

## 1. Disk Full Emergency

### The 3 AM Page

> **Alert:** "Filesystem / on host web-07 is at 100% capacity."
>
> **Immediate triage:**
> ```bash
> $ df -h
> Filesystem      Size  Used Avail Use% Mounted on
> /dev/sda1        50G   50G     0 100% /
> ```
>
> **Find the culprits fast:**
> ```bash
> # What's eating the space at the top level?
> du -sh /* 2>/dev/null | sort -rh | head -10
> # 34G     /var
> # 8.2G    /usr
> # 4.1G    /home
> # 1.2G    /opt
> # 765M    /lib
> # ...
>
> # Drill into /var
> du -sh /var/* 2>/dev/null | sort -rh | head -10
> # 32G     /var/log
> # 1.2G    /var/lib
> # 234M    /var/cache
>
> # Drill into /var/log
> du -sh /var/log/* 2>/dev/null | sort -rh | head -10
> # 31G     /var/log/nginx
> # 512M    /var/log/syslog
> # 123M    /var/log/journal
>
> # Bingo. nginx logs.
> ls -lhS /var/log/nginx/ | head -10
> # -rw-r--r-- 1 www-data www-data 47G Jun 11 02:59 access.log
> ```
>
> **Root cause:** `logrotate` stopped working 3 weeks ago because someone changed the ownership of `/var/log/nginx/` from `root:adm` to `www-data:www-data` and the logrotate cron job runs as root but uses `su www-data www-data` in its config. The `create` directive in logrotate failed silently because `www-data` couldn't create files with the correct ownership. Logs were never rotated, and the 47GB `access.log` filled the root partition.
>
> **Immediate fix:** `> /var/log/nginx/access.log` (truncate in place — no service restart needed, just release inode data). Or safer with a backup: `mv /var/log/nginx/access.log /mnt/backup/ && kill -USR1 $(cat /var/run/nginx.pid)` (move then signal nginx to reopen log files).
>
> **Long-term fix:** Fix permissions on `/var/log/nginx/`, fix the logrotate config, verify with `logrotate -d /etc/logrotate.d/nginx`.

### Emergency Disk Freeing Commands

```bash
# ncdu — interactive disk usage explorer (install: apt-get install ncdu)
ncdu /
# ↑ arrow keys to navigate, d to delete, Enter to drill down
# Shows disk usage as a tree with visual size bars

# Find and show the top space-consuming directories
du -ah / 2>/dev/null | sort -rh | head -30

# What changed recently (last 1 day)?
find / -type f -mtime -1 -size +100M -exec ls -lh {} \; 2>/dev/null

# Safe cleanup commands (verify before running):
# Truncate a log file in-place (releases space, keeps inode, no service restart)
: > /var/log/app/access.log  # colon is bash no-op; redirect truncates
# OR:
truncate -s 0 /var/log/app/access.log

# Clear systemd journal logs older than 2 days
journalctl --vacuum-time=2d
# Or limit by size:
journalctl --vacuum-size=500M

# Clear apt cache
apt-get clean && apt-get autoremove --purge

# Clear old kernel packages (keep only current + 2 previous)
dpkg -l 'linux-*' | grep '^ii' | awk '{print $2}' | \
  grep -v "$(uname -r | sed 's/\(.*\)-\([^0-9]\+\)/\1/')" | \
  xargs apt-get purge -y

# Clear Docker garbage
docker system prune -a -f --volumes

# Find deleted large files still held open (space not freed until process stops)
lsof +L1 | grep deleted | sort -k7 -rn | head -10
# If a process holds a deleted file open, restart or kill the process to free space.
```

---

## 2. Finding Large Files

```bash
# All files > 1GB
find / -type f -size +1G -exec ls -lh {} \; 2>/dev/null

# All files > 100MB, sorted by size, only in specific mount point
find /var -type f -size +100M -exec ls -lhS {} + 2>/dev/null

# Top 50 largest files on the entire system
find / -type f -printf '%s %p\n' 2>/dev/null | sort -rn | head -50 | \
  numfmt --to=iec --field=1 --suffix=B

# Large files by age — old things that can probably be archived/deleted
find /var/log -type f -size +100M -mtime +30 -exec ls -lh {} \;

# Files opened by a process that are > 1GB (fast, no filesystem scan)
lsof -p PID | awk '$7 > 1073741824 {print $7, $9}' | sort -rn

# Total size of a directory (excluding subdirectory mounts)
du -sh --one-file-system /

# Breakdown by extension (what kind of files are eating space?)
find /var -type f | awk -F. 'NF>1 {print $NF}' | sort | uniq -c | sort -rn | head -10
# 234567  log
#   2345  gz
#    123  tmp
```

### Classic Scenario: Docker Overlay Growing Unbounded

```bash
# Docker images and container layers can silently fill disks
docker system df
# TYPE            TOTAL     ACTIVE    SIZE      RECLAIMABLE
# Images          45        3         12.34GB   10.21GB (82%)
# Containers      120       5         2.45GB    1.23GB (50%)
# Local Volumes   8         2         45.6GB    0B (0%)
# Build Cache     0         0         0B        0B

# Find which Docker volumes are huge
docker volume ls -q | while read vol; do
  size=$(docker run --rm -v "$vol":/vol alpine du -sh /vol 2>/dev/null | awk '{print $1}')
  echo "$size $vol"
done | sort -rh
```

---

## 3. Deleted-but-Open Files

### The Invisible Disk Space Problem

When a process opens a file and the file is deleted (`rm` or `unlink`), the file's directory entry is removed but the inode and data blocks remain allocated until the last file descriptor pointing to them is closed. Until then, `df` shows the space as used but `du` doesn't count the file because it no longer has a name.

```bash
# Find all deleted files still held open by processes
lsof +L1 | head -20
# COMMAND   PID   USER   FD   TYPE  DEVICE  SIZE/OFF  NLINK NODE NAME
# java     12345  app    12w  REG   253,0   83886080  0     56789 /var/log/app.log (deleted)
# nginx    12346  www    5w   REG   253,0   1073741824 0    56790 /var/log/nginx/access.log (deleted)
#
# Key: NLINK=0 means the file has been deleted (no directory entries)
# The SIZE/OFF column shows how much disk space is still consumed.
#
# The space IS counted in df (inode still allocated) but NOT in du (no name to find it by).

# Sum up all deleted-but-open file sizes
lsof +L1 | awk 'NR>1 {sum+=$7} END {printf "Total unreclaimed space: %.1f GB\n", sum / 1024 / 1024 / 1024}'

# Check a specific process
lsof -p $PID | grep deleted

# Recoverable? If you need the data:
# 1. Find the file descriptor: ls -la /proc/PID/fd/N where N is the FD number
# 2. Copy it: cp /proc/PID/fd/12 /tmp/recovered-file
# 3. Restart the process when safe (closes FD, releases space)
```

### Classic Scenario: The 40% Mismatch

> **df shows `/var` at 100% (50G used). `du -sh /var` shows 30G. 20G is missing.**
>
> ```bash
> $ df -h /var
> Filesystem      Size  Used Avail Use%
> /dev/sda3        50G   50G     0 100% /var
>
> $ du -sh /var
> 30G     /var
>
> $ lsof +L1 | awk 'NR>1{sum+=$7}END{print sum/1024/1024/1024" GB"}'
> 20.1 GB
> ```
>
> Root cause: An admin deleted `/var/log/nginx/access.log` while nginx was running. Nginx still has the file descriptor open. 20GB of space is allocated to that now-invisible file. Nobody can find it with `ls` or `find`, but `df` still shows it.
>
> **Fix:** `kill -USR1 $(cat /var/run/nginx.pid)` — nginx reopens log files, closing its old FDs. The 20GB is instantly freed.

---

## 4. Inode Exhaustion

### What Are Inodes?

Every file, directory, symlink, socket, and FIFO consumes one inode. An inode stores metadata (permissions, timestamps, ownership, data block pointers). A filesystem has a fixed number of inodes set at mkfs time. You can have free disk space but zero free inodes — meaning you can't create any new files.

```bash
# Check inode usage
df -i
# Filesystem      Inodes    IUsed   IFree IUse% Mounted on
# /dev/sda1       3276800  3276800       0  100% /
# Whoa — 0 free inodes on /!

# Find directories with the most inodes (most files)
for d in /*; do
  count=$(find "$d" -type f -printf '.' 2>/dev/null | wc -c)
  echo "$count $d"
done | sort -rn | head -10
# 2891203 /var
#  123456 /usr
#   34567 /home

# Drill deeper — find which subdirectory has the most files
find /var -type d -exec sh -c 'echo "$(find "$1" -type f -printf . | wc -c) $1"' _ {} \; 2>/dev/null | sort -rn | head -10
# 2845301 /var/lib/php/sessions
#    4567 /var/spool
#     345 /var/log

# How many files total on the filesystem?
find / -type f 2>/dev/null | wc -l

# Average file size (total space / number of files)
# If this is < 1KB, you have an inode problem, not a space problem.
total_kb=$(df --output=size / | tail -1)
total_files=$(df --output=itotal / | tail -1 | sed 's/Inodes//')
avg_kb=$((total_kb / total_files))
echo "Average file size: ${avg_kb}KB"
# If average is tiny (e.g., 4KB), you have too many small files for your inode count.
# Fix: increase inode count at mkfs time: mkfs.ext4 -N 10000000 /dev/sda1
```

### Classic Scenario: PHP Session File Bomb

> **"I can't create any files! But `df -h` says I have 40% free!"**
>
> ```
> $ df -h /
> Filesystem      Size  Used Avail Use%
> /dev/sda1        50G   30G   20G  60%  — 20GB free!
>
> $ df -i /
> Filesystem      Inodes  IUsed  IFree IUse%
> /dev/sda1        500000 500000      0 100%  — 0 inodes free!
>
> $ touch /tmp/test
> touch: cannot touch '/tmp/test': No space left on device  — misleading error
>
> $ find /var/lib/php/sessions -type f | wc -l
> 485000
> ```
>
> Root cause: PHP's default session handler stores each session as a single file in `/var/lib/php/sessions/`. GC is configured to clean up sessions older than 1440 seconds (24 min) with a 1% probability per request. On a busy site, sessions accumulate faster than PHP's probabilistic GC can clean them. 485,000 tiny session files consume 0 inodes but only ~500MB of space. The filesystem's 500,000 inode limit is hit long before the 50GB space limit.
>
> **Fix:**
> ```bash
> # Immediate: delete sessions older than 24 hours
> find /var/lib/php/sessions -type f -mtime +1 -delete
>
> # Long-term:
> # 1. Switch to Redis/memcached for session storage
> # 2. Set session.gc_divisor = 100 (always run GC) and session.gc_probability = 1
> # 3. Use a cron-based cleaner instead of probabilistic GC
> # 4. Increase inodes: backup, mkfs with -N, restore
> # 5. Set up monitoring: alert on IUse% > 80%
> ```

---

## 5. I/O Bottlenecks (iostat)

### iostat Deep Dive

```bash
# Install: apt-get install sysstat

# Extended stats, 1-second intervals, continuous
iostat -xz 1

# Sample output for a single device:
# Device   r/s   w/s   rkB/s   wkB/s  rrqm/s  wrqm/s  %rrqm  %wrqm  r_await  w_await  aqu-sz  rareq-sz  wareq-sz  svctm  %util
# sda      5.0  250.0   20.0  8000.0     0.0    50.0   0.00  16.67    2.00     25.00    6.30     4.00     32.00   3.92  100.00
#                                                                                                        ↑↑↑↑↑↑↑↑↑
#                                                                                                      PAY ATTENTION HERE
```

| Column | Meaning | Healthy | Problem | What to Do |
|--------|---------|---------|---------|-------------|
| `r/s`, `w/s` | Reads/Writes per second | Depends on workload | Not inherently bad — work with await | -
| `rkB/s`, `wkB/s` | KB read/written per second | Depends on workload | Saturating bandwidth? | Check device throughput limit |
| `rrqm/s`, `wrqm/s` | Merged read/write requests per second | High is good — merging avoids seeks | - | If zero, no merging possible (random I/O) |
| `r_await` | Avg read service time (ms) + queue time | < 3ms (SSD), < 10ms (HDD) | > 10ms | Storage is slow. Check disk, controller, or network (for SAN/NAS). |
| `w_await` | Avg write service time (ms) + queue time | < 3ms (SSD) | > 10ms | Write bottleneck. Check write cache, disk firmware. |
| **aqu-sz** | **Average queue size** | < 1 per device | > (number of spindles + 1) | Queue building up — device can't keep up. |
| `rareq-sz` | Average read request size (KB) | > 16KB | < 16KB | Small reads = random I/O, probably index lookups |
| `wareq-sz` | Average write request size (KB) | > 16KB | < 16KB | Small writes — try batching |
| `svctm` | ~~Avg service time~~ **UNRELIABLE on Linux** | Ignore | Ignore | Kernel can't measure this accurately |
| **%util** | **Percent of time the device had at least one request in-flight** | < 70% | > 90% | Device is saturated. But on SSDs with parallelism, %util can be 100% while actual throughput is fine. Better: watch aqu-sz and await. |

### iostat: The Aqu-Sz Story

```
%util = 100%, aqu-sz = 1.0 → device is doing exactly one I/O at a time. Sequential workload.
%util = 100%, aqu-sz = 0.5 → device has idle gaps between requests. Not saturated.
%util = 100%, aqu-sz = 15  → queue is 15 deep. Device is severely overloaded. Latency will hurt.
%util = 50%,  aqu-sz = 15  → bursty I/O. Half the time the queue is empty, half the time it's overloaded.
                              Check for periodic heavy writes (checkpoints, log flushes).
```

### iotop — Which Process Is Doing I/O?

```bash
# Real-time I/O view, only processes actively doing I/O
iotop -o

# Non-interactive (for scripting)
iotop -bo -n 10

# Sample output:
# TID  PRIO  USER     DISK READ  DISK WRITE  SWAPIN  IO>    COMMAND
# 12345 be/4 postgres   0.00 B/s   45.67 M/s  0.00%  99.99% postgres: wal writer
# 12378 be/4 postgres   12.34 M/s   0.00 B/s  0.00%  34.56% postgres: autovacuum worker
#
# IO> column = % of time the thread was in I/O wait.
# WAL writer is saturating write throughput (99.99% IO) — check disk write capacity.

# For a specific process
iotop -p $PID

# Only show accumulated I/O per process (iotop alternative: pidstat)
pidstat -d 1 5  # I/O stats: kB_rd/s, kB_wr/s
```

### blktrace — Block-Level Tracing

```bash
# For when iostat isn't enough: trace every block I/O operation
blktrace -d /dev/sda -o - | blkparse -i -

# Focus on latency distribution
blktrace -d /dev/sda -o - | blkparse -i - | grep -E "D\s+|C\s+" | \
  awk '{if ($1 ~ /D/) start[$6]=$2; else if ($1 ~ /C/) {dur=$2-start[$6]; if (dur>0) print dur}}' | \
  sort -n | awk '{all[NR]=$1} END{print "p50:", all[int(NR*0.5)], "p99:", all[int(NR*0.99)], "max:", all[NR]}'

# btrace — simpler wrapper
btrace /dev/sda | head -50
```

---

## 6. Disk Latency

### ioping — Measure Real Disk Response Time

```bash
# Install: apt-get install ioping

# Measure how fast the disk responds to a single small I/O
ioping -c 10 /var
# 4 KiB <<< /var (ext4 /dev/sda2): request=1 time=152.3 us
# 4 KiB <<< /var (ext4 /dev/sda2): request=2 time=145.1 us
# 4 KiB <<< /var (ext4 /dev/sda2): request=3 time=138.7 us
# --- /var (ext4 /dev/sda2) ioping statistics ---
# 10 requests completed, min/avg/max/mdev = 138.7 us / 147.2 us / 165.4 us / 7.3 us
#
# 138-165 microseconds for 4KB random read — this is a fast NVMe SSD.
# Typical numbers:
#   NVMe SSD:    50-150 us
#   SATA SSD:    150-500 us
#   HDD 7200rpm:  5-15 ms  (seek time included)
#   EBS gp3:     1-3 ms   (network-attached)
#   EBS gp2:     1-10 ms  (burst credits affect this)

# Sequential read: larger block, measures throughput more than seek time
ioping -R -c 10 -s 1M /var
# 1 MiB >>> /var: request=1 time=312.5 us
# Throughput = 1 MiB / 312.5 us = ~3.2 GB/s

# Latency distribution (more samples)
ioping -c 100 -i 0 /var | grep "time=" | awk -F'[=. ]' '{print $8}' | sort -n | \
  awk 'BEGIN{print "min\tp50\tp95\tp99\tmax"} \
       {arr[NR]=$1} END{print arr[1]"\t"arr[int(NR*0.5)]"\t"arr[int(NR*0.95)]"\t"arr[int(NR*0.99)]"\t"arr[NR]}'

# AWS EBS baseline comparison:
# gp3: 3000 IOPS baseline, 125 MB/s baseline — regardless of volume size
# gp2: 3 IOPS per GB — 1TB volume = 3000 IOPS baseline (burst to 3000 for small volumes)
# io1/io2: provisioned IOPS — you pay per IOPS
# st1: throughput-optimized HDD — low IOPS, high throughput, minimum 500GB

# Check EBS burst balance (for gp2):
aws cloudwatch get-metric-statistics --namespace AWS/EBS \
  --metric-name BurstBalance --dimensions Name=VolumeId,Value=vol-12345 \
  --statistics Average --start-time $(date -u -d '-1 hour' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) --period 300
```

---

## 7. LVM Operations

### LVM Emergency Extend (Live, No Downtime)

```bash
# Situation: /data is 95% full, it's an LVM logical volume, there's free space in the VG.

# 1. Check current state
pvs   # Physical volumes — are there free extents?
vgs   # Volume group free space
lvs   # Logical volume size

# 2. Verify free space in the VG
vgdisplay vg_data | grep Free

# 3. Extend the LV by 50GB
lvextend -L +50G /dev/vg_data/lv_data

# 4. Resize the filesystem (for ext4)
resize2fs /dev/vg_data/lv_data

# 5. For XFS (CANNOT be shrunk, only grown!)
xfs_growfs /data

# 6. Verify
df -h /data

# ALL DONE — no unmount, no downtime. LVM is magic.

# --- Emergency: No Free Space in VG ---
# Add a new disk to the VG first:
# 1. Attach disk (cloud console / hot-plug)
# 2. Scan for it
echo "- - -" > /sys/class/scsi_host/host0/scan
lsblk  # verify it appears (e.g., /dev/sdc)

# 3. Create PV on the new disk
pvcreate /dev/sdc

# 4. Add it to the VG
vgextend vg_data /dev/sdc

# 5. Now extend as above
lvextend -L +50G /dev/vg_data/lv_data
resize2fs /dev/vg_data/lv_data
```

### LVM Diagnostics

```bash
# Full LVM information dump
pvdisplay    # Physical volumes — disks, partitions, raw devices
vgdisplay    # Volume groups — pooling of PVs
lvdisplay    # Logical volumes — the usable block devices

# One-liner: all LVs with size and usage
lvs -o lv_name,lv_size,lv_attr,vg_name --units g

# Check for partial/damaged LVs (missing PV, etc.)
lvs -a -o +devices | grep -v "linear\|striped\|mirrored\|available"

# Snapshots (check if any snapshot is full — can cause I/O errors)
lvs -a -o lv_name,data_percent,metadata_percent,lv_attr
#  lv_snapshot_20260611 100.00  sv---  <-- 100% full snapshot = BAD
#  A full snapshot becomes invalid; all reads to it fail.
#  Remove it: lvremove /dev/vg0/lv_snapshot_20260611
```

---

## 8. fio Benchmarks

### fio — Test What Your Disk Can Actually Do

```bash
# Install: apt-get install fio

# 1. Random write test (simulates database WAL)
fio --name=randwrite \
    --ioengine=libaio \
    --iodepth=16 \
    --rw=randwrite \
    --bs=4k \
    --direct=1 \
    --size=1G \
    --numjobs=4 \
    --runtime=60 \
    --group_reporting
# Output:
#   IOPS=12500, BW=48.8MiB/s
# Compare against expected: gp3 3000 IOPS baseline — this is 4x the baseline (good).
# If you get IOPS=250, your volume is throttled or on HDD.

# 2. Random read test (simulates database queries)
fio --name=randread \
    --ioengine=libaio \
    --iodepth=32 \
    --rw=randread \
    --bs=4k \
    --direct=1 \
    --size=1G \
    --runtime=60 \
    --group_reporting

# 3. Sequential write (simulates log writing, backups)
fio --name=seqwrite \
    --ioengine=libaio \
    --rw=write \
    --bs=1M \
    --direct=1 \
    --size=4G \
    --iodepth=4 \
    --group_reporting

# 4. Mixed RW — database-like workload (70/30 read/write)
fio --name=mixed \
    --ioengine=libaio \
    --rw=randrw \
    --rwmixread=70 \
    --bs=8k \
    --direct=1 \
    --size=2G \
    --iodepth=16 \
    --runtime=60 \
    --group_reporting

# 5. Latency-focused test
fio --name=latency \
    --ioengine=libaio \
    --rw=randread \
    --bs=4k \
    --direct=1 \
    --iodepth=1 \
    --size=1G \
    --runtime=60 \
    --group_reporting
# With iodepth=1, you're measuring pure disk latency with no queuing.
# Output includes latency percentiles: clat (completion latency) in nanoseconds.
```

### Interpreting fio Results

```
Key metrics:
  IOPS=XXXX     — I/O operations per second (higher is better)
  BW=XXXXMiB/s  — Bandwidth (for sequential I/O)
  lat (usec)    — Completion latency distribution:
    min=100     — Best case (cached writes, DRAM cache)
    p50=250     — Median: 50% of I/Os completed within 250us
    p99=2000    — 99th percentile: 1% of I/Os took > 2ms
    max=15000   — Worst case: garbage collection, wear leveling, EBS jitter
  clat percentiles — Same as lat but considers only completion latency
                      (excludes submission/queue time)

Red flags:
  p99 latency > 100x p50 latency → tail latency problem
  IOPS much below provisioned → throttling (check burst balance on EBS)
  BW saturating at low value → volume throughput limit
  clat p50 high with low iodepth → inherently slow device (HDD, bad SAN)
```

---

## 9. Python: Disk Monitoring Script

```python
#!/usr/bin/env python3
"""
disk-monitor.py — monitors disk usage, inode usage, and I/O stats.
Alerts when usage > 85% or inode usage > 80%.
"""

import os
import shutil
import smtplib
import json
import time
from datetime import datetime
from email.message import EmailMessage

# --- Configuration ---
DISK_THRESHOLD_PCT = 85     # Alert when disk usage > 85%
INODE_THRESHOLD_PCT = 80    # Alert when inode usage > 80%
IO_AWAIT_THRESHOLD_MS = 10  # Alert when disk await > 10ms
ALERT_EMAIL = "sre-alerts@example.com"
SMTP_SERVER = "smtp.internal.example.com"
POLL_INTERVAL = 60          # Seconds
# --------------------

def send_alert(subject, body):
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = f"disk-monitor@{os.uname().nodename}"
        msg["To"] = ALERT_EMAIL
        msg.set_content(body)
        with smtplib.SMTP(SMTP_SERVER, 25, timeout=10) as smtp:
            smtp.send_message(msg)
    except Exception as e:
        print(f"[ERROR] Failed to send alert: {e}")

def check_disk_usage():
    alerts = []
    for path in ["/", "/var", "/tmp", "/home"]:
        if not os.path.isdir(path):
            continue
        try:
            usage = shutil.disk_usage(path)
            pct = (usage.used / usage.total) * 100
            if pct > DISK_THRESHOLD_PCT:
                alerts.append(
                    f"  {path}: {pct:.1f}% used "
                    f"({usage.used // 1024 // 1024 // 1024}G / {usage.total // 1024 // 1024 // 1024}G)"
                )
        except Exception as e:
            print(f"[WARN] Could not check {path}: {e}")
    return alerts

def check_inode_usage():
    alerts = []
    for path in ["/", "/var", "/tmp"]:
        if not os.path.isdir(path):
            continue
        try:
            stat = os.statvfs(path)
            total_inodes = stat.f_files
            free_inodes = stat.f_ffree
            if total_inodes > 0:
                pct = ((total_inodes - free_inodes) / total_inodes) * 100
                if pct > INODE_THRESHOLD_PCT:
                    alerts.append(
                        f"  {path}: {pct:.1f}% inodes used "
                        f"({total_inodes - free_inodes:,} / {total_inodes:,})"
                    )
        except Exception as e:
            print(f"[WARN] Could not check inodes for {path}: {e}")
    return alerts

def check_io_latency():
    """Parse /proc/diskstats for I/O await."""
    alerts = []
    try:
        with open("/proc/diskstats") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 14:
                    continue
                device = parts[2]
                if not device.startswith(("sd", "nvme", "vd", "xvd")):
                    continue
                # Field 10 (index 9) = time spent doing I/O (ms)
                # Field 7 (index 6) = reads completed
                # Field 11 (index 10) = writes completed
                # This is approximate — for accuracy use psutil.disk_io_counters or iostat
                _io_ticks = int(parts[12])
                _io_ops = int(parts[7]) + int(parts[11])

                # Simplified check: just flag based on path existence
                # For real await accounting, use psutil or call iostat
        # Better approach: use psutil
        import psutil
        disk_io = psutil.disk_io_counters(perdisk=True)
        # psutil doesn't give await directly; need to compute over interval
        # For simplicity, we'll flag based on disk_usage > 95% as proxy for high I/O
    except Exception as e:
        print(f"[WARN] Could not check I/O latency: {e}")
    return alerts

def get_largest_dirs(base_paths, top_n=10):
    """Find the largest directories for a given set of paths."""
    results = []
    for base in base_paths:
        if not os.path.isdir(base):
            continue
        try:
            for entry in os.listdir(base):
                full_path = os.path.join(base, entry)
                if os.path.isdir(full_path):
                    total = 0
                    for dirpath, _dirnames, filenames in os.walk(full_path, onerror=lambda e: None):
                        for f in filenames:
                            try:
                                total += os.path.getsize(os.path.join(dirpath, f))
                            except OSError:
                                pass
                    results.append((total, full_path))
        except PermissionError:
            pass
    results.sort(reverse=True)
    return results[:top_n]

def main():
    print(f"[{datetime.now().isoformat()}] Disk Monitor starting on {os.uname().nodename}")
    print(f"  Disk threshold: >{DISK_THRESHOLD_PCT}%")
    print(f"  Inode threshold: >{INODE_THRESHOLD_PCT}%")
    print()

    alerted_disks = set()
    alerted_inodes = set()

    while True:
        now = datetime.now().isoformat()

        disk_alerts = check_disk_usage()
        inode_alerts = check_inode_usage()

        new_disk = set(disk_alerts) - alerted_disks
        new_inode = set(inode_alerts) - alerted_inodes

        if new_disk:
            largest = get_largest_dirs(["/", "/var", "/tmp"])
            largest_str = "\n".join(f"  {size // 1024 // 1024} MB  {path}"
                                    for size, path in largest)
            body = f"""Disk Usage Alert
Host: {os.uname().nodename}
Time: {now}

Alerts:
{chr(10).join(disk_alerts)}

Largest directories:
{largest_str}
"""
            send_alert(f"[DISK ALERT] {os.uname().nodename} — disk usage > {DISK_THRESHOLD_PCT}%", body)
            alerted_disks.update(new_disk)

        if new_inode:
            body = f"""Inode Usage Alert
Host: {os.uname().nodename}
Time: {now}

Alerts:
{chr(10).join(inode_alerts)}

Check with: df -i
Find directories with many files: find /var -type f | wc -l
"""
            send_alert(f"[INODE ALERT] {os.uname().nodename} — inode usage > {INODE_THRESHOLD_PCT}%", body)
            alerted_inodes.update(new_inode)

        # Reset alerted if condition clears
        alerted_disks &= set(disk_alerts)
        alerted_inodes &= set(inode_alerts)

        # Periodic status log
        if int(time.time()) % 300 < POLL_INTERVAL:
            usage = shutil.disk_usage("/")
            pct = usage.used / usage.total * 100
            stat = os.statvfs("/")
            inode_pct = (stat.f_files - stat.f_ffree) / stat.f_files * 100 if stat.f_files else 0
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] "
                  f"/ = {pct:.1f}% ({usage.used // (1024**3)}G/{usage.total // (1024**3)}G) | "
                  f"inodes = {inode_pct:.1f}% | "
                  f"warnings: disk={len(disk_alerts)}, inode={len(inode_alerts)}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
```

---

## 10. Java: NIO Disk Operations

### Detecting Disk Errors in Java

```java
import java.io.IOException;
import java.nio.file.*;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class DiskHealthChecker {

    public static boolean checkDiskWriteable(String path) {
        Path testFile = Paths.get(path, ".disk_health_check_" + System.currentTimeMillis());
        try {
            Files.writeString(testFile, "health check", StandardOpenOption.CREATE_NEW,
                StandardOpenOption.WRITE, StandardOpenOption.SYNC);
            Files.delete(testFile);
            return true;
        } catch (NoSpaceLeftOnDeviceException e) {
            System.err.println("[DISK_ALERT] No space left on device: " + path);
            return false;
        } catch (FileSystemException e) {
            System.err.println("[DISK_ALERT] File system error on " + path + ": " + e.getMessage());
            return false;
        } catch (IOException e) {
            System.err.println("[DISK_ALERT] I/O error on " + path + ": " + e.getMessage());
            return false;
        }
    }

    public static long getFreeDiskSpace(String path) {
        try {
            FileStore store = Files.getFileStore(Paths.get(path));
            long usable = store.getUsableSpace();
            long total = store.getTotalSpace();
            double pctUsed = ((double)(total - usable) / total) * 100;

            System.out.printf("Path: %s | Total: %d GB | Usable: %d GB | Used: %.1f%% | Type: %s | Read-only: %s%n",
                path,
                total / (1024 * 1024 * 1024),
                usable / (1024 * 1024 * 1024),
                pctUsed,
                store.type(),
                store.isReadOnly()
            );

            if (pctUsed > 85) {
                System.err.printf("[WARNING] Disk usage > 85%% on %s: %.1f%%%n", path, pctUsed);
            }
            return usable;
        } catch (IOException e) {
            System.err.println("Failed to check disk space: " + e.getMessage());
            return -1;
        }
    }

    public static class PeriodicFileWrite {
        private final Path directory;
        private final ExecutorService executor;

        public PeriodicFileWrite(String dir) {
            this.directory = Paths.get(dir);
            this.executor = Executors.newSingleThreadExecutor(r -> {
                Thread t = new Thread(r, "disk-health-writer");
                t.setDaemon(true);
                return t;
            });
        }

        public void start(long intervalMs) {
            executor.submit(() -> {
                while (!Thread.currentThread().isInterrupted()) {
                    try {
                        long start = System.nanoTime();
                        checkDiskWriteable(directory.toString());
                        long elapsed = System.nanoTime() - start;
                        double ms = elapsed / 1_000_000.0;
                        if (ms > 100) {
                            System.err.printf("[DISK_LATENCY] Write to %s took %.1f ms (>100ms threshold)%n",
                                directory, ms);
                        }
                        Thread.sleep(intervalMs);
                    } catch (InterruptedException e) {
                        Thread.currentThread().interrupt();
                        break;
                    } catch (Exception e) {
                        System.err.println("[DISK_ERROR] Periodic check failed: " + e.getMessage());
                    }
                }
            });
        }

        public void stop() {
            executor.shutdownNow();
        }
    }

    public static void main(String[] args) {
        // One-time check
        getFreeDiskSpace("/");
        getFreeDiskSpace("/var");
        checkDiskWriteable("/tmp");

        // Periodic background health checks
        PeriodicFileWrite writer = new PeriodicFileWrite("/tmp");
        writer.start(30_000);  // every 30 seconds

        Runtime.getRuntime().addShutdownHook(new Thread(writer::stop));
    }
}
```

### Java: Safe File I/O with Error Handling

```java
import java.io.*;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.file.*;

public class SafeFileIO {

    /**
     * Write data with sync, catching disk-full and I/O errors properly.
     */
    public static void safeWriteWithSync(Path file, byte[] data) throws IOException {
        try (FileChannel channel = FileChannel.open(file,
                StandardOpenOption.CREATE,
                StandardOpenOption.WRITE,
                StandardOpenOption.SYNC)) {  // SYNC = fsync after every write
            ByteBuffer buf = ByteBuffer.wrap(data);
            while (buf.hasRemaining()) {
                channel.write(buf);
            }
        } catch (NoSpaceLeftOnDeviceException e) {
            // Disk full — alert and handle gracefully
            System.err.println("[CRITICAL] Disk full: " + file);
            throw e;
        } catch (FileSystemException e) {
            // Filesystem-level error (read-only FS, quota exceeded, etc.)
            System.err.println("[CRITICAL] Filesystem error: " + e.getMessage());
            throw e;
        }
    }

    /**
     * Atomic write — write to temp file, then rename.
     * Ensures readers never see a partial file.
     */
    public static void atomicWrite(Path target, byte[] data) throws IOException {
        Path tempFile = target.resolveSibling("." + target.getFileName() + ".tmp");
        try {
            Files.write(tempFile, data, StandardOpenOption.CREATE, StandardOpenOption.SYNC);
            Files.move(tempFile, target, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
        } catch (IOException e) {
            try { Files.deleteIfExists(tempFile); } catch (IOException ignored) { }
            throw e;
        }
    }

    /**
     * Drain (truncate) a file without closing it — for log rotation scenarios.
     * Uses FileChannel.truncate which works even if process has file open.
     */
    public static void truncateFileInPlace(Path file) throws IOException {
        try (FileChannel channel = FileChannel.open(file, StandardOpenOption.WRITE)) {
            channel.truncate(0);
        }
    }
}
```
