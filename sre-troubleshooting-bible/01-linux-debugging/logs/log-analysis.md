# Log Analysis
> **Category:** Linux | Logs | Debugging
> **Difficulty:** Basic to Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#linux` `#logs` `#oncall`

---

## Table of Contents
1. [journalctl Mastery](#1-journalctl-mastery)
2. [grep Power Patterns](#2-grep-power-patterns)
3. [awk for Logs](#3-awk-for-logs)
4. [sed for Log Transform](#4-sed-for-log-transform)
5. [Finding Patterns in Large Files](#5-finding-patterns-in-large-files)
6. [logrotate Troubleshooting](#6-logrotate-troubleshooting)
7. [Real Scenario: Rate Limiter Analysis](#7-real-scenario-rate-limiter-analysis)
8. [Python: Log Parser Script](#8-python-log-parser-script)

---

## 1. journalctl Mastery

`journalctl` is the query interface for systemd's journal. It stores logs in a binary format with structured metadata — much more powerful and efficient than plain text log files for system-level debugging.

### Essential Queries

```bash
# ─── Service-Focused Queries ───

# All logs for a specific service unit
journalctl -u nginx

# Errors only, last 10 minutes
journalctl -u nginx -p err --since "10 minutes ago" --no-pager

# Follow (tail -f equivalent) for a service
journalctl -u nginx -f

# Logs since last boot, for a service
journalctl -u nginx -b

# Multiple services combined
journalctl -u nginx -u php-fpm --since "1 hour ago"

# ─── Time-Based Queries ───

# Absolute time range
journalctl --since "2026-06-11 08:00:00" --until "2026-06-11 09:00:00"

# Relative time
journalctl --since "30 minutes ago"
journalctl --since "1 hour ago" --until "10 minutes ago"
journalctl --since yesterday
journalctl --since "2026-06-10" --until "2026-06-11"

# ─── Boot-Based Queries ───

# List all boots
journalctl --list-boots
# -3  abc123...def456  Wed 2026-06-04 03:15:22 UTC—Fri 2026-06-06 12:34:56 UTC
# -2  def789...ghi012  Fri 2026-06-06 12:35:00 UTC—Mon 2026-06-09 08:22:11 UTC
# -1  ghi345...jkl678  Mon 2026-06-09 08:22:30 UTC—Thu 2026-06-11 02:15:44 UTC
#  0  jkl901...mno234  Thu 2026-06-11 02:16:00 UTC—Thu 2026-06-11 14:32:15 UTC

# Logs from a specific boot
journalctl -b -1           # previous boot
journalctl -b -2           # two boots ago
journalctl -b 0            # current boot (default)

# ─── Filtering ───

# Priority levels (0=emerg, 1=alert, 2=crit, 3=err, 4=warning, 5=notice, 6=info, 7=debug)
journalctl -p err          # error and above
journalctl -p warning      # warning and above
journalctl -p 0..3         # emerg through err

# Kernel messages only
journalctl -k

# Specific user
journalctl _UID=1000

# Specific executable
journalctl /usr/bin/nginx

# Specific syslog facility
journalctl SYSLOG_FACILITY=3  # facility 3 = daemon

# ─── Output Formatting ───

# JSON output (for programmatic consumption)
journalctl -u nginx -o json | head -50

# JSON pretty-printed
journalctl -u nginx -o json-pretty | head -100

# Short format with precise timestamps
journalctl -u nginx -o short-iso
# 2026-06-11T14:32:15.123456+0000 web-07 nginx[1234]: GET /api/health 200 0.005

# Export to file
journalctl -u nginx --since "2026-06-11" -o json > /tmp/nginx-logs-20260611.json

# Show only the message field (no metadata)
journalctl -u nginx -o cat

# ─── Maintenance ───

# Check journal disk usage
journalctl --disk-usage
# Archived and active journals take up 1.5G on disk.

# Vacuum: keep only recent logs
journalctl --vacuum-size=500M     # keep at most 500MB
journalctl --vacuum-time=7d       # keep 7 days
journalctl --vacuum-files=10      # keep only 10 archived journal files

# Rotate journals now
journalctl --rotate
```

---

## 2. grep Power Patterns

### Essential grep Flags

```bash
# -E: Extended regex (+, |, (), etc.)
# -i: Case-insensitive
# -v: Invert match
# -c: Count matches
# -n: Show line numbers
# -w: Match whole words only
# -A N: Show N lines AFTER match
# -B N: Show N lines BEFORE match
# -C N: Show N lines around match (both before and after)
# -r: Recursive directory search
# -h: Suppress filename prefix
# -l: Only show filenames with matches
# --color=auto: Highlight matches
# -P: Perl-compatible regex (lookaheads, lookbehinds)
```

### Production Patterns

```bash
LOG="/var/log/nginx/access.log"

# Find all errors and criticals
grep -E "(ERROR|CRITICAL|FATAL)" app.log

# Example: Show matching lines with context
grep -B 5 -A 10 "Exception" app.log
# Shows 5 lines before and 10 lines after each Exception
# Critical for reading stack traces in context

# Count 5xx errors
grep -c '" 5[0-9][0-9] ' "$LOG"

# Count distinct error types and sort by frequency
grep -oE '" 5[0-9][0-9] ' "$LOG" | sort | uniq -c | sort -rn
#   1234 " 500 "
#    567 " 502 "
#     89 " 503 "
#     12 " 504 "

# Filter out noise: exclude health check requests
grep -v "healthcheck" "$LOG" | grep -E '" 5[0-9][0-9] '

# Find requests from a specific IP across ALL log files
grep -r "10.0.1.5" /var/log/nginx/

# Match a date range (Jun 11 between 14:00 and 15:00)
grep "11/Jun/2026:1[45]:" access.log

# Find log lines that do NOT contain expected patterns (anomaly detection)
grep -v -E "(200|301|304|401|403|404)" access.log
# ^ Shows everything EXCEPT these expected status codes — good for spotting attacks or crashes

# Find all lines containing SQL injection attempts
grep -Pi "(union.*select|drop\s+table|1=1|--\s)" access.log

# Find lines where a field matches multiple patterns (AND match)
grep -E "POST|PUT" access.log | grep " 500 "
# POST or PUT that returned 500

# Count requests per minute (rough):
grep -oE "\[[0-9]{2}/\w+/[0-9]{4}:[0-9]{2}:[0-9]{2}" access.log | \
  sort | uniq -c | sort -rn | head -20
```

### Multi-File grep with Filenames

```bash
# Search across rotated logs
grep -E "OutOfMemoryError" /var/log/app/app.log*

# With filenames suppressed for piping to other tools
grep -h "ERROR" /var/log/app/*.log | sort | uniq -c

# Search gzipped logs without decompressing
zgrep "ERROR" /var/log/app/app.log*.gz
# zgrep = grep for gzipped files (also works: zcat file.gz | grep pattern)

# Search both regular and gzip logs
find /var/log/app -name "app.log*" -exec zgrep -H "CRITICAL" {} \;

# Count total errors across all rotated logs
zgrep -h -c "ERROR" /var/log/app/app.log* | paste -sd '+' | bc
```

---

## 3. awk for Logs

### awk Quick Reference

```
awk '{print $1}'           — print first field (default delimiter: whitespace)
awk -F',' '{print $3}'     — comma delimiter, print 3rd field
awk '$3 > 100'             — filter: only rows where field 3 > 100
awk 'NR > 1'              — skip first line (header)
awk 'NF == 10'             — only rows with exactly 10 fields
awk '/pattern/ {action}'   — apply action only to lines matching pattern
awk '{arr[$1]++} END {for (i in arr) print arr[i], i}'  — count and sort
```

### Production awk Patterns

```bash
LOG="/var/log/nginx/access.log"

# Top 10 IPs by request count
awk '{print $1}' "$LOG" | sort | uniq -c | sort -rn | head -10
# 12345 10.0.1.5
#  9876 10.0.2.100
#  5432 10.0.3.200

# Top 10 IPs with awk only (more efficient, no sort/uniq):
awk '{count[$1]++} END {for (ip in count) print count[ip], ip}' "$LOG" | \
  sort -rn | head -10

# 5xx errors with IPs and URLs
awk '$9 ~ /5[0-9][0-9]/ {print $1, $9, $7}' "$LOG" | head -20
# 10.0.1.5 500 /api/orders
# 10.0.2.100 502 /api/health

# Average response time ($NF = last field, often the request_time in custom log format)
awk '{sum+=$NF; count++} END {printf "Average: %.3f ms\n", sum/count}' "$LOG"

# Percentile calculation for response times:
awk '{times[NR]=$NF} END {
  asort(times);
  print "p50:", times[int(NR*0.5)], "p95:", times[int(NR*0.95)],
        "p99:", times[int(NR*0.99)], "max:", times[NR]
}' "$LOG"

# Total bytes transferred
awk '{sum+=$10} END {printf "Total: %.2f GB\n", sum/1024/1024/1024}' "$LOG"

# Requests per hour
awk '{
  split($4, dt, ":");
  hour=substr(dt[1], 2) ":" substr(dt[2], 1, 2);
  count[hour]++
} END {
  for (h in count) print h, count[h]
}' "$LOG" | sort

# Top 10 slowest requests (assuming request_time is last column)
awk '{print $NF, $7}' "$LOG" | sort -rn | head -10

# Status code distribution
awk '{codes[$9]++} END {for (c in codes) printf "%s: %d (%.1f%%)\n", c, codes[c], codes[c]*100/NR}' "$LOG"

# Find 5xx spike: count 5xx per minute
awk '$9 ~ /5[0-9][0-9]/ {
  split($4, dt, ":");
  minute=substr(dt[1], 2) ":" substr(dt[2], 1, 4);
  err[minute]++
} END {
  for (m in err) print m, err[m]
}' "$LOG" | sort

# Parse JSON logs: extract a specific field
grep '"status":500' app.json.log | awk -F'"message":' '{print $2}' | awk -F'"' '{print $2}'

# Apache combined format: bytes by HTTP method
awk '{method[$6]+=$10} END {for (m in method) printf "%s: %.1f GB\n", m, method[m]/1024/1024/1024}' "$LOG"
```

### Combined awk + grep Pattern (Rate Limiter Analysis)

```bash
# Find all requests that returned 429 (Too Many Requests)
awk '$9==429 {print $1}' /var/log/nginx/access.log | \
  sort | uniq -c | sort -rn | head -20
#   2345 10.0.1.5          <-- this poor tenant is rate-limited hard
#    345 10.0.2.100
#     12 10.0.3.200

# Now check what those 10.0.1.5 requests look like:
grep "10.0.1.5" /var/log/nginx/access.log | awk '{print $7}' | sort | uniq -c | sort -rn
#   2000 /api/unlimited-fetch     <-- aha, they're hammering this expensive endpoint
#    300 /api/health
#     50 /api/orders

# Cross-reference with response codes:
grep "10.0.1.5" /var/log/nginx/access.log | \
  awk '{print $9}' | sort | uniq -c | sort -rn
#   2000 429
#     50 200
#      5 500
```

---

## 4. sed for Log Transform

### Production sed Recipes

```bash
# Replace IP addresses with a placeholder (anonymization)
sed 's/[0-9]\+\.[0-9]\+\.[0-9]\+\.[0-9]\+/REDACTED/g' access.log

# Replace specific IP with a label
sed 's/10\.0\.1\.5/SERVICE_ACCOUNT/g' access.log

# Remove lines matching a pattern (e.g., health checks)
sed '/healthcheck/d' access.log

# Extract only lines between two timestamps
sed -n '/14:30:00/,/14:45:00/p' access.log

# Add line numbers
sed = access.log | sed 'N;s/\n/\t/'

# Delete blank lines
sed '/^$/d' app.log

# Remove ANSI color codes from logs (common with app frameworks)
sed 's/\x1b\[[0-9;]*m//g' app.log

# Convert Apache combined format to CSV
sed -E 's/^([^ ]+) ([^ ]+) ([^ ]+) \[([^\]]+)\] "([^ ]+) ([^ ]+) ([^"]+)" ([0-9]+) ([0-9]+)/\1,\2,\3,\4,\5,\6,\7,\8,\9/' access.log

# Truncate long lines for readability (keep first 200 chars)
sed 's/\(.\{200\}\).*/\1.../' app.log

# Extract JSON object from log line
sed -n 's/.*\({.*}\).*/\1/p' app.log | python3 -m json.tool
```

---

## 5. Finding Patterns in Large Files

### When grep Is Too Slow

```bash
# For files > 10GB, grep can be slow. Alternatives:

# ripgrep (rg) — much faster than grep for large files
# Install: apt-get install ripgrep
rg -c "ERROR" /var/log/app/app.log   # count matches
rg "OutOfMemoryError" /var/log/app/  # recursive search
rg --context 5 "panic" app.log       # context lines
rg -t log "ERROR"                    # search only .log files
rg -g "*.log.gz" "ERROR"             # search gzipped logs (via rg --pre)

# For counting in huge files without reading everything:
wc -l access.log                     # total lines
split -l 1000000 access.log chunk_   # split into 1M line chunks
for chunk in chunk_*; do rg "ERROR" "$chunk" >> results.txt; done

# If you just need the total count:
grep -c "pattern" huge.log           # fast — grep stops after counting
# NOT: grep "pattern" huge.log | wc -l  # slow — pipes ALL matches
```

### Sampling Large Logs

```bash
# Take a statistical sample (every Nth line)
awk 'NR % 100 == 0' huge.log > sample.log

# Random sampling: 1% of lines
awk 'rand() < 0.01' huge.log > random_sample.log

# Head + tail: first and last 1000 lines
(head -1000; tail -1000) < huge.log

# Find the most active minute (for time-series analysis)
awk '{
  split($4, dt, ":");
  minute=substr(dt[1], 2) " " substr(dt[2], 1, 2) ":" substr(dt[3], 1, 2);
  count[minute]++
} END {
  for (m in count) print count[m], m
}' huge_access.log | sort -rn | head -10
```

---

## 6. logrotate Troubleshooting

### How logrotate Works

logrotate reads `/etc/logrotate.conf` and all files in `/etc/logrotate.d/`. It uses a state file (`/var/lib/logrotate/status`) to track when each log was last rotated. It runs daily via cron (`/etc/cron.daily/logrotate`) or systemd timer (`logrotate.timer`).

### Classic Scenario: Logs Stopped Rotating

> **Alert:** Disk at 95% because `/var/log/app/app.log` is 47GB and hasn't rotated in 3 weeks.
>
> Diagnostic flow:
>
> ```bash
> # 1. Check logrotate config
> cat /etc/logrotate.d/app
> # /var/log/app/*.log {
> #     daily
> #     rotate 7
> #     compress
> #     delaycompress
> #     missingok
> #     notifempty
> #     postrotate
> #         /usr/local/bin/app-reload-log.sh
> #     endscript
> # }
>
> # 2. Dry-run: see what logrotate WOULD do
> logrotate -d /etc/logrotate.d/app
> # Shows: "considering log /var/log/app/app.log"
> # "log needs rotating"
> # "running postrotate script"
> # "error: error running postrotate script for /var/log/app/app.log: /usr/local/bin/app-reload-log.sh exited with 1"
>
> # 3. Run the postrotate script manually
> /usr/local/bin/app-reload-log.sh
> # Error: kill: (1234): No such process
> # The script sends SIGHUP to a PID file, but the PID is stale.
> # The real PID is different (restart happened).
>
> # 4. Check logrotate status file
> cat /var/lib/logrotate/status | grep /var/log/app
> # "/var/log/app/app.log" 2026-5-21-3:0:0  <-- last rotation was May 21!
> # It's now June 11 — 3 weeks of failed rotations.
>
> # 5. Fix the postrotate script
> # BAD: kill -HUP $(cat /var/run/app.pid)
> # GOOD: systemctl reload app  OR  kill -HUP $(pidof app)
> # OR: use copytruncate instead of postrotate if the app can't be signalled
> ```
>
> **Fix:**
> ```bash
> # Update the postrotate to use systemd:
> # postrotate
> #     systemctl reload app || true
> # endscript
>
> # Test:
> logrotate -f /etc/logrotate.d/app  # force rotation
> # Check that rotation happened:
> ls -la /var/log/app/
> # app.log  app.log.1  app.log.2.gz  ...
> ```

### logrotate Debugging Commands

```bash
# Dry run (debug mode) — shows what it would do but doesn't do it
logrotate -d /etc/logrotate.d/app

# Verbose force run — actually executes
logrotate -vf /etc/logrotate.d/app

# Check logrotate status file for all managed logs
cat /var/lib/logrotate/status

# Check if logrotate cron/timer is actually running
systemctl status logrotate.timer
systemctl list-timers --all | grep logrotate

# View logrotate's own logs
journalctl -u logrotate.service -n 50
grep logrotate /var/log/syslog

# Common logrotate pitfalls:
# 1. "create" directive fails: wrong owner/group/permissions
#    Fix: create 0640 www-data www-data
#
# 2. "postrotate" script fails: stale PID, wrong path, permission denied
#    Fix: use "|| true" to prevent failure from blocking rotation:
#    postrotate
#        /usr/bin/systemctl reload app || true
#    endscript
#
# 3. "size" vs "daily": if both set, rotation happens when EITHER condition is met
#    size 100M → rotates when file reaches 100MB, even if it's only been 1 hour
#
# 4. "delaycompress": doesn't compress the most recent rotated file (app.log.1)
#    Useful if the app keeps writing to app.log.1 during rotation
#
# 5. "copytruncate": copies the log then truncates original
#    Use when the app can't be signalled to reopen files (e.g., third-party binary)
#    Downside: can lose log lines written between copy and truncate
#
# 6. "missingok": don't error if the log file doesn't exist
#    "nomissingok": DO error if log file doesn't exist (safer for critical apps)
```

---

## 7. Real Scenario: Rate Limiter Analysis

### Problem Statement

> **Page:** "Users reporting 429 errors on the API. Is it a specific client abusing the system, or is our rate limiter misconfigured?"

### Investigation

```bash
# Step 1: How many 429s in the last hour?
awk '$9==429' /var/log/nginx/access.log | wc -l
# 15234 — that's a lot.

# Step 2: Which IPs are getting 429d?
awk '$9==429 {print $1}' /var/log/nginx/access.log | sort | uniq -c | sort -rn | head -20
#  12000 10.0.1.5
#   2000 10.0.2.100
#    500 10.0.3.200
#     ...
# One IP accounts for 78% of rate-limited requests.

# Step 3: What endpoints is 10.0.1.5 hitting?
grep "10.0.1.5" /var/log/nginx/access.log | \
  awk '{print $7}' | sort | uniq -c | sort -rn | head -10
#  15000 /api/v1/search
#    300 /api/v1/health
#     50 /api/v1/users/me

# Step 4: Is 10.0.1.5 being rate-limited fairly?
# What's its actual success rate?
grep "10.0.1.5" /var/log/nginx/access.log | \
  awk '{codes[$9]++} END {for (c in codes) printf "%s: %d\n", c, codes[c]}'
# 429: 12000
# 200: 100
# Shows 99.2% of its requests are rejected — it IS the abuser.

# Step 5: Identify the traffic pattern
grep "10.0.1.5" /var/log/nginx/access.log | awk '{print $4}' | head -20
# [11/Jun/2026:14:30:00 +0000]
# [11/Jun/2026:14:30:00 +0000]
# [11/Jun/2026:14:30:00 +0000]
# (many requests in the same second — scripted abuse)

# Step 6: Find the user agent
grep "10.0.1.5" /var/log/nginx/access.log | \
  awk -F'"' '{print $6}' | sort | uniq -c | sort -rn
# 12100 python-requests/2.28.1
# It's an automated script, not a browser.

# Step 7: Identify the organization
whois 10.0.1.5 2>/dev/null || echo "Private IP — internal service"

# Step 8: Check if the rate limit is truly being enforced
# What's the rate per second?
grep "10.0.1.5" /var/log/nginx/access.log | \
  awk -F'[ :]' '{time=$4":"$5":"$6; count[time]++} END {for (t in count) print t, count[t]}' | \
  sort -k2 -rn | head -10
# 14:30:00 345   <-- 345 requests in one second
# Rate limit is 10/sec → working correctly but aggressive abuse continues

# Resolution:
# 1. Block the IP temporarily: iptables -A INPUT -s 10.0.1.5 -j DROP
# 2. Contact the team owning 10.0.1.5 — they deployed a broken retry loop
# 3. Add monitoring: alert when 429 rate > 100/min globally
```

### One-Liner Summary

```bash
# The full analysis as a piped one-liner:
awk '$9==429 {print $1}' /var/log/nginx/access.log | \
  sort | uniq -c | sort -rn | head -5 | \
  while read count ip; do
    echo "IP $ip: $count 429s"
    grep "$ip" /var/log/nginx/access.log | \
      awk '{print $7}' | sort | uniq -c | sort -rn | head -3
    echo "---"
  done
```

---

## 8. Python: Log Parser Script

```python
#!/usr/bin/env python3
"""
log-parser.py — real-time log analysis with error detection and anomaly alerting.
Parses common log formats (Apache/NGINX combined, JSON, custom regex).
"""

import re
import sys
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Optional

class LogParser:
    # Apache/NGINX combined log format regex
    COMBINED_RE = re.compile(
        r'^(?P<ip>\S+) \S+ \S+ \[(?P<time>[^\]]+)\] '
        r'"(?P<method>\S+) (?P<url>\S+) \S+" '
        r'(?P<status>\d+) (?P<size>\d+) '
        r'"(?P<referer>[^"]*)" "(?P<ua>[^"]*)"'
        r'(?: (?P<rt>\S+))?'  # optional request_time
    )

    def __init__(self, filepath: str, alert_threshold_error_rate: float = 0.05,
                 window_seconds: int = 60):
        self.filepath = filepath
        self.alert_threshold = alert_threshold_error_rate
        self.window_seconds = window_seconds
        self.status_codes = defaultdict(int)
        self.error_codes = defaultdict(int)
        self.client_ips = defaultdict(int)
        self.error_urls = defaultdict(int)
        self.time_series = deque()  # (timestamp, is_error)
        self.alerted = False

    def parse_line(self, line: str) -> Optional[dict]:
        m = self.COMBINED_RE.match(line)
        if not m:
            return None
        d = m.groupdict()
        status = int(d['status'])
        is_error = 500 <= status < 600
        return {
            'ip': d['ip'],
            'time': d['time'],
            'method': d['method'],
            'url': d['url'],
            'status': status,
            'size': int(d['size']) if d['size'].isdigit() else 0,
            'ua': d['ua'],
            'rt': float(d['rt']) if d.get('rt') and d['rt'] != '-' else None,
            'is_error': is_error,
        }

    def update_metrics(self, entry: dict):
        self.status_codes[entry['status']] += 1
        self.client_ips[entry['ip']] += 1
        if entry['is_error']:
            self.error_codes[entry['status']] += 1
            self.error_urls[entry['url']] += 1
            self.time_series.append((time.time(), True))
        else:
            self.time_series.append((time.time(), False))

        cutoff = time.time() - self.window_seconds
        while self.time_series and self.time_series[0][0] < cutoff:
            self.time_series.popleft()

    def check_anomalies(self):
        if len(self.time_series) == 0:
            return
        total = len(self.time_series)
        errors = sum(1 for _, is_err in self.time_series if is_err)
        error_rate = errors / total if total > 0 else 0

        if error_rate > self.alert_threshold and not self.alerted:
            self.alerted = True
            self._fire_alert(error_rate, errors, total)
        elif error_rate <= self.alert_threshold:
            self.alerted = False

    def _fire_alert(self, error_rate: float, errors: int, total: int):
        timestamp = datetime.now().isoformat()
        report = f"""
[ALERT] High Error Rate Detected
================================
Timestamp:  {timestamp}
Window:     {self.window_seconds}s
Request Rate: {total / self.window_seconds:.1f}/s
Error Rate: {error_rate:.1%} ({errors}/{total})

Top Error Codes (window):
{self._format_counter(self.error_codes, 5)}

Top Error URLs (window):
{self._format_counter(self.error_urls, 5)}

Top Client IPs (window):
{self._format_counter(self.client_ips, 5)}
"""
        print(report, file=sys.stderr)

    def _format_counter(self, counter, top_n=5):
        return '\n'.join(f"  {code:>5s}: {count:>7d}" for code, count in
                         sorted(counter.items(), key=lambda x: -x[1])[:top_n])

    def run_tail(self):
        """Follow a file like tail -f."""
        print(f"[{datetime.now().isoformat()}] Log parser started: {self.filepath}")
        print(f"  Alert threshold: {self.alert_threshold:.1%} error rate")
        print(f"  Window: {self.window_seconds}s")
        print()

        processed = 0
        errors = 0
        start = time.time()

        with open(self.filepath, 'r') as f:
            f.seek(0, 2)  # seek to end for tail -f behavior

            while True:
                line = f.readline()
                if not line:
                    # Print summary every 10 seconds of idle
                    if time.time() - start > 10 and processed > 0:
                        elapsed = time.time() - start
                        rate = processed / elapsed
                        print(f"  [{datetime.now().strftime('%H:%M:%S')}] "
                              f"processed={processed} errors={errors} "
                              f"rate={rate:.1f}/s")
                    time.sleep(0.1)
                    continue

                entry = self.parse_line(line)
                if not entry:
                    continue

                processed += 1
                if entry['is_error']:
                    errors += 1

                self.update_metrics(entry)
                self.check_anomalies()

    def run_summary(self):
        """One-shot: parse entire file and print summary."""
        print(f"[{datetime.now().isoformat()}] Parsing: {self.filepath}")

        total_lines = 0
        parsed = 0
        errors = 0
        response_times = []
        methods = defaultdict(int)

        with open(self.filepath, 'r') as f:
            for line in f:
                total_lines += 1
                entry = self.parse_line(line)
                if not entry:
                    continue
                parsed += 1
                if entry['is_error']:
                    errors += 1
                self.update_metrics(entry)
                methods[entry['method']] += 1
                if entry['rt'] is not None:
                    response_times.append(entry['rt'])

        print(f"\n{'='*60}")
        print("PARSING SUMMARY")
        print(f"{'='*60}")
        print(f"Total lines:     {total_lines:,}")
        print(f"Parsed entries:  {parsed:,} ({parsed/max(total_lines,1)*100:.1f}%)")
        print(f"Error rate:      {errors/max(parsed,1)*100:.1f}% ({errors}/{parsed})")
        print()

        print("Status Code Distribution:")
        print(f"  {'Code':>6s}  {'Count':>8s}  {'Percent':>8s}")
        for code in sorted(self.status_codes.keys()):
            count = self.status_codes[code]
            pct = count / parsed * 100
            print(f"  {code:>6d}  {count:>8d}  {pct:>7.1f}%")
        print()

        print("HTTP Methods:")
        for method, count in sorted(methods.items(), key=lambda x: -x[1]):
            print(f"  {method:>8s}: {count:>7d}")
        print()

        print("Top 10 Error URLs:")
        for url, count in sorted(self.error_urls.items(), key=lambda x: -x[1])[:10]:
            print(f"  {count:>5d}  {url}")
        print()

        print("Top 10 Client IPs:")
        for ip, count in sorted(self.client_ips.items(), key=lambda x: -x[1])[:10]:
            print(f"  {count:>5d}  {ip}")
        print()

        if response_times:
            rt_sorted = sorted(response_times)
            n = len(rt_sorted)
            print("Response Time Percentiles:")
            print(f"  p50:  {rt_sorted[int(n*0.50)]:.3f}s")
            print(f"  p90:  {rt_sorted[int(n*0.90)]:.3f}s")
            print(f"  p95:  {rt_sorted[int(n*0.95)]:.3f}s")
            print(f"  p99:  {rt_sorted[int(n*0.99)]:.3f}s")
            print(f"  max:  {rt_sorted[-1]:.3f}s")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Log parser with anomaly detection")
    parser.add_argument("logfile", help="Path to log file")
    parser.add_argument("--tail", "-f", action="store_true", help="Follow file (tail -f mode)")
    parser.add_argument("--threshold", "-t", type=float, default=0.05,
                        help="Alert threshold error rate (default: 0.05 = 5%%)")
    parser.add_argument("--window", "-w", type=int, default=60,
                        help="Alert window in seconds (default: 60)")

    args = parser.parse_args()
    lp = LogParser(args.logfile, args.threshold, args.window)

    try:
        if args.tail:
            lp.run_tail()
        else:
            lp.run_summary()
    except KeyboardInterrupt:
        print("\nParser stopped.")
        sys.exit(0)
```

### Quick Python: Regex-Based Log Extraction

```python
# Quick snippet for extracting error patterns from log files
import re
from collections import Counter

def analyze_errors(logfile):
    error_pattern = re.compile(r'(?P<level>ERROR|CRITICAL|FATAL)\s+'
                               r'(?P<timestamp>\S+\s+\S+)\s+'
                               r'(?P<message>.*)')
    errors = Counter()
    messages = []

    with open(logfile) as f:
        for line in f:
            m = error_pattern.search(line)
            if m:
                msg = m.group('message')
                # Classify by first meaningful word
                category = msg.split()[0] if msg.split() else 'unknown'
                errors[category] += 1
                messages.append((m.group('timestamp'), msg))

    print("Error Categories:")
    for cat, count in errors.most_common(10):
        print(f"  {count:>5d}  {cat}")

    # Check if error rate is anomalous (by time bucket)
    time_buckets = Counter()
    for ts, _msg in messages:
        bucket = ts[:15]  # truncate to minute: "2026-06-11 14:32"
        time_buckets[bucket] += 1

    avg = sum(time_buckets.values()) / max(len(time_buckets), 1)
    for bucket, count in time_buckets.items():
        if count > avg * 3:  # anomaly: 3x the average
            print(f"\n[ANOMALY] Error spike at {bucket}: {count} errors "
                  f"(avg={avg:.1f})")

analyze_errors('/var/log/app/app.log')
```
