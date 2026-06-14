# Disk Full Emergency Runbook

> **Category:** On-Call | Linux | Emergency
> **Difficulty:** Basic
> **Last Reviewed:** 2026-06
> **Tags:** `#runbook` `#disk` `#emergency` `#oncall`

---

## 1. DETECT

Alert fires when disk usage exceeds 85% (first warning) or 95% (critical). Applications may start throwing:

```
No space left on device
java.io.IOException: No space left on device
Error writing to log file: No space left on device
```

**Symptoms:**
- Health checks failing
- Services restarting and unable to write logs
- Database writes failing
- Containers being evicted (Kubernetes)

---

## 2. ASSESS (30 Seconds)

```bash
# 2a. Which filesystem is full?
df -h
# Look for Use% at 100%, 99%, 98%.

# 2b. Are we out of inodes (many small files)?
df -i
# If IUse% is 100% but Use% is low → inode exhaustion. See section 5.

# 2c. Which top-level directories are eating space?
du -sh /* 2>/dev/null | sort -rh | head -15
# This will identify the biggest consumer quickly.

# 2d. Filesystem-level detail:
# Dive into the biggest dir from 2c:
du -sh /var/* 2>/dev/null | sort -rh | head -10
du -sh /opt/* 2>/dev/null | sort -rh | head -10
```

---

## 3. BUY TIME — SAFE TO REMOVE

These can be removed without fear of breaking the application.

### 3a. Clean /tmp

```bash
# Delete files older than 1 day:
find /tmp -type f -mtime +1 -delete 2>/dev/null

# Delete files older than 2 hours (more aggressive):
find /tmp -type f -mmin +120 -delete 2>/dev/null

# Check what's left:
du -sh /tmp
```

### 3b. Shrink Systemd Journal

```bash
# Keep only last 500 MB of journals:
journalctl --vacuum-size=500M

# Or keep only last 2 days:
journalctl --vacuum-time=2d

# Check journal disk usage before/after:
journalctl --disk-usage
```

### 3c. Docker Cleanup (⚠️ Stops Containers Using Volumes)

```bash
# Check Docker disk usage:
docker system df

# Prune unused images, containers, networks, build cache:
docker system prune -af
# -a: all unused images (not just dangling)
# -f: force (no prompt)
# This does NOT remove named volumes by default.

# If volumes are big:
docker system prune -af --volumes
# WARNING: --volumes deletes ALL unused volumes including data volumes.
# Only use this if you know no important data is in unused volumes.
```

### 3d. Remove Old Kernels

```bash
# Debian / Ubuntu:
apt autoremove --purge -y

# RHEL / Amazon Linux / CentOS:
dnf autoremove -y
# or older systems:
yum autoremove -y

# Check old kernels before removing:
dpkg -l | grep linux-image     # Debian
rpm -q kernel                  # RHEL
```

### 3e. Core Dumps

```bash
# Check core dump size:
du -sh /var/crash 2>/dev/null
du -sh /var/lib/systemd/coredump 2>/dev/null

# Delete core dumps older than 7 days:
find /var/crash -type f -mtime +7 -delete 2>/dev/null

# Disable core dumps to prevent future buildup:
ulimit -c 0
echo "* soft core 0" >> /etc/security/limits.conf
```

### 3f. Package Caches

```bash
# Debian / Ubuntu:
apt clean
apt autoclean

# RHEL / Amazon Linux:
dnf clean all

# npm cache:
npm cache clean --force

# pip cache:
pip cache purge

# Maven cache:
rm -rf ~/.m2/repository/*
```

---

## 4. BUY TIME — RISKY TO REMOVE (Verify First)

### 4a. Large Log Files

```bash
# Find all log files >1 GB:
find /var/log -type f -size +1G -exec ls -lh {} \; 2>/dev/null

# Find top 20 largest files in /var/log:
du -ah /var/log 2>/dev/null | sort -rh | head -20

# Compress (safer than deleting):
gzip /var/log/app/application.log.old
# This replaces the file with application.log.old.gz (~10x smaller).

# For the CURRENT log file — DO NOT DELETE (app is writing to it).
# Instead, truncate it (⚠️ loses logs):
truncate -s 0 /var/log/app/application.log
# Application continues writing from byte 0. No restart needed.
# Copy to S3 first if you need to preserve:
aws s3 cp /var/log/app/application.log s3://logs-backup/hostname/application-$(date +%s).log && truncate -s 0 /var/log/app/application.log
```

### 4b. Deleted-but-Open Files (Hidden Disk Usage)

When a process deletes a file but keeps it open (log rotation without sending SIGHUP), the space is not freed until the process closes the handle.

```bash
# Find files deleted but still held open:
lsof +L1 2>/dev/null | grep -E "deleted|\(deleted\)" | head -20

# Show total space consumed by deleted-open files:
lsof +L1 2>/dev/null | awk '{sum+=$7} END {print sum/1024/1024 " MB in deleted-but-open files"}'

# Fix: restart the process holding the deleted file.
systemctl restart app
# After restart, re-check with df -h to confirm space freed.
```

### 4c. Kubernetes Node Disk Pressure

```bash
# Check which node is under disk pressure:
kubectl get nodes | grep DiskPressure

# Evict pods from that node:
kubectl drain <node> --ignore-daemonsets --delete-emptydir-data

# On the node itself, check what's using space:
du -sh /var/lib/docker/*
du -sh /var/lib/kubelet/*

# Common culprits on K8s nodes:
# - /var/lib/docker/overlay2 (container image layers)
# - /var/log/containers (pod logs)
# - /var/lib/kubelet/emptyDir (ephemeral volumes never cleaned)
```

---

## 5. INODE EXHAUSTION (Special Case)

When `df -i` shows IUse% at 100% but `df -h` shows plenty of space — you have too many small files.

```bash
# Confirm inode exhaustion:
df -i

# Find which directory has the most files:
for dir in /*; do
  echo "$(find "$dir" -type f 2>/dev/null | wc -l) $dir"
done | sort -rn | head -10

# Drill into the worst offender:
find /var/spool -type f | wc -l

# Common inode hogs:
# - /tmp (session files, PHP sessions)
# - /var/spool/postfix/maildrop (mail queue)
# - /var/spool/clientmqueue (sendmail)
# - npm/node_modules (symlinks or duplicated deps)

# Clean session files:
find /tmp -name "sess_*" -mtime +1 -delete 2>/dev/null

# Clean mail queue:
postsuper -d ALL   # Postfix — delete all queued mail
```

---

## 6. ROOT CAUSE INVESTIGATION

```bash
# Interactive disk explorer (if ncdu is installed):
ncdu /          # Navigate with arrow keys, 'd' to delete, '?' for help

# If ncdu not installed, install it:
apt install -y ncdu    # Debian
dnf install -y ncdu    # RHEL

# Check log rotation configuration:
cat /etc/logrotate.d/app
logrotate -d /etc/logrotate.d/app   # dry-run: shows what would happen

# Common root causes:
# - Log rotation not running: systemctl status logrotate.timer
# - Debug logging left on in production: check app config
# - Cron job output not redirected: check /var/spool/mail/root
# - Core dumps from crashing process: coredumpctl list
# - Backup left behind: find /tmp -name "*.sql" -o -name "*.dump"
```

---

## 7. VERIFY RECOVERY

```bash
# Re-check disk:
df -h
df -i

# Verify applications can write:
# Touch a file in the app's data directory:
touch /var/log/app/write-test && rm /var/log/app/write-test
# If no error → good.

# Are services healthy?
systemctl status app
curl -s -o /dev/null -w "%{http_code}" https://api.example.com/health
```

---

## 8. PERMANENT FIX

| Issue | Fix |
|-------|-----|
| Logs not rotating | Configure `logrotate` with daily rotation, 7-day retention, compress |
| No disk monitoring | Add alerts at 75%, 85%, 95% usage |
| Log level too verbose | Set `INFO` or `WARN` for production |
| No log retention policy | Implement automated S3 archival + local deletion |
| Docker images piling up | Add `docker system prune` cron job |
| Deleted files not freed | Fix log rotation to send SIGHUP to app after rotation |
| Core dumps filling disk | Disable core dumps or limit to small size |
| Inode exhaustion | Fix spam/abuse of file creation, clean old sessions |

### Example logrotate config:

```
# /etc/logrotate.d/app
/var/log/app/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    maxsize 100M
}
```

### Example disk monitoring alert (Prometheus):

```yaml
# Alert rule:
- alert: DiskAlmostFull
  expr: predict_linear(node_filesystem_avail_bytes{mountpoint="/"}[6h], 24*3600) < 0
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: "Disk predicted to fill within 24 hours"
```

---

## ABORT CRITERIA

| Condition | Action | Timebox |
|-----------|--------|---------|
| <5% free and disk filling rapidly (>1%/min) | **Stop investigating — free space immediately.** Traffic > logs. | Immediately |
| Cannot free enough space to stay above 90% | Escalate to Infra team — may need volume expansion | 15 min |
| Database is on the same volume and running out | **Critical.** Escalate to DBA. Do NOT truncate DB files. | Immediately |
| EBS volume expansion fails | Escalate to AWS support + Infra team | 10 min |
| Can't identify what's using space | Escalate to L2 engineer for analysis | 15 min |
