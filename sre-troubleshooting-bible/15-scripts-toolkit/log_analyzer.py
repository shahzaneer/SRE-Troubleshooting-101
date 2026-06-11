#!/usr/bin/env python3
"""
log_analyzer.py — Parses nginx/combined access logs or JSON log files.
Extracts key metrics and detects anomalies (spike detection via std dev).

Supports: Nginx combined format, Apache common format, JSON-lines logs.
Dependencies: None (stdlib only)

Usage:
    python log_analyzer.py --file /var/log/nginx/access.log
    python log_analyzer.py --file /var/log/nginx/access.log --format nginx --anomaly-threshold 3.0
    python log_analyzer.py --file app.json --format json --timestamp-field timestamp
    python log_analyzer.py --file /var/log/nginx/access.log --output report.json

Exit codes:
    0 — Analysis complete, no anomalies
    1 — Anomalies detected
    2 — Usage error / file not found
"""

import json
import re
import sys
import gzip
from argparse import ArgumentParser
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple


# Regex for Nginx combined log format:
# $remote_addr - $remote_user [$time_local] "$request" $status $body_bytes_sent "$http_referer" "$http_user_agent"
NGINX_COMBINED_RE = re.compile(
    r'(?P<ip>\S+)\s+'                          # remote_addr
    r'\S+\s+'                                    # - (ident)
    r'(?P<remote_user>\S+)\s+'                   # remote_user
    r'\[(?P<time>[^\]]+)\]\s+'                   # [time_local]
    r'"(?P<request>[^"]*)"\s+'                   # "request"
    r'(?P<status>\d{3})\s+'                      # status
    r'(?P<body_bytes>\d+|-)\s+'                  # body_bytes_sent
    r'"(?P<referer>[^"]*)"\s+'                   # "http_referer"
    r'"(?P<user_agent>[^"]*)"'                   # "http_user_agent"
)

# Simpler: Apache common format (no referer/ua)
APACHE_COMMON_RE = re.compile(
    r'(?P<ip>\S+)\s+'
    r'\S+\s+'
    r'(?P<remote_user>\S+)\s+'
    r'\[(?P<time>[^\]]+)\]\s+'
    r'"(?P<request>[^"]*)"\s+'
    r'(?P<status>\d{3})\s+'
    r'(?P<body_bytes>\d+|-)'
)

# Nginx time format: 10/Jun/2026:14:35:10 +0000
NGINX_TIME_FMT = "%d/%b/%Y:%H:%M:%S %z"

# JSON log fields that are commonly used
DEFAULT_JSON_IP_FIELDS = ("remote_addr", "client_ip", "ip", "src_ip", "source_ip")
DEFAULT_JSON_STATUS_FIELDS = ("status", "status_code", "http_status", "response_code")
DEFAULT_JSON_ENDPOINT_FIELDS = ("path", "uri", "endpoint", "request_path", "url")
DEFAULT_JSON_TIMESTAMP_FIELDS = ("timestamp", "time", "@timestamp", "_timestamp", "ts")


class LogEntry:
    """Parsed log entry from any supported format."""
    __slots__ = ("ip", "timestamp", "method", "endpoint", "status", "body_bytes", "referer", "user_agent", "raw")

    def __init__(self, ip="", timestamp=None, method="", endpoint="",
                 status=0, body_bytes=0, referer="", user_agent="", raw=""):
        self.ip = ip
        self.timestamp = timestamp
        self.method = method
        self.endpoint = endpoint
        self.status = status
        self.body_bytes = body_bytes
        self.referer = referer
        self.user_agent = user_agent
        self.raw = raw


def parse_nginx_line(line: str) -> Optional[LogEntry]:
    """Parse a single nginx combined format log line."""
    match = NGINX_COMBINED_RE.match(line)
    if not match:
        match = APACHE_COMMON_RE.match(line)
    if not match:
        return None

    d = match.groupdict()
    try:
        timestamp = datetime.strptime(d["time"], NGINX_TIME_FMT)
    except (ValueError, KeyError):
        timestamp = None

    request = d.get("request", "").split()
    method = request[0] if len(request) > 0 else ""
    endpoint = request[1] if len(request) > 1 else ""

    return LogEntry(
        ip=d.get("ip", ""),
        timestamp=timestamp,
        method=method,
        endpoint=endpoint,
        status=int(d.get("status", 0)),
        body_bytes=int(d.get("body_bytes", 0)) if d.get("body_bytes", "-") != "-" else 0,
        referer=d.get("referer", ""),
        user_agent=d.get("user_agent", ""),
        raw=line.strip(),
    )


def find_json_field(obj: Dict, candidate_fields: Tuple[str, ...]) -> str:
    """Find the first matching field name in a JSON object from a list of candidates."""
    for field in candidate_fields:
        if field in obj:
            return field
    return candidate_fields[0]  # Return first candidate as fallback


def parse_json_line(line: str, timestamp_field: str) -> Optional[LogEntry]:
    """Parse a single JSON log line. Tries to auto-detect fields."""
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None

    # Auto-detect fields
    ip_field = find_json_field(obj, DEFAULT_JSON_IP_FIELDS)
    status_field = find_json_field(obj, DEFAULT_JSON_STATUS_FIELDS)
    endpoint_field = find_json_field(obj, DEFAULT_JSON_ENDPOINT_FIELDS)
    ts_field = timestamp_field or find_json_field(obj, DEFAULT_JSON_TIMESTAMP_FIELDS)

    # Parse timestamp
    timestamp = None
    ts_raw = obj.get(ts_field)
    if ts_raw:
        for fmt in (NGINX_TIME_FMT, "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z",
                     "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%s"):
            try:
                if fmt == "%s":
                    timestamp = datetime.fromtimestamp(float(ts_raw))
                else:
                    timestamp = datetime.strptime(str(ts_raw)[:26], fmt)
                break
            except (ValueError, TypeError, OSError):
                continue

    return LogEntry(
        ip=str(obj.get(ip_field, "")),
        timestamp=timestamp,
        method=obj.get("method", obj.get("http_method", "")),
        endpoint=str(obj.get(endpoint_field, "")),
        status=int(obj.get(status_field, 0)),
        body_bytes=int(obj.get("body_bytes", obj.get("response_size", 0))),
        raw=line.strip(),
    )


class LogAnalyzer:
    """Analyzes parsed log entries for metrics and anomalies."""

    def __init__(self, anomaly_threshold: float = 3.0):
        """
        anomaly_threshold: Number of standard deviations above mean to flag as anomaly.
        3.0 = flag if error rate > mean + 3*stddev (99.7% confidence interval).
        """
        self.anomaly_threshold = anomaly_threshold

    def top_ips(self, entries: List[LogEntry], n: int = 20) -> List[Tuple[str, int]]:
        """Most active IPs by request count."""
        counter = Counter(e.ip for e in entries if e.ip)
        return counter.most_common(n)

    def top_endpoints(self, entries: List[LogEntry], n: int = 20) -> List[Tuple[str, int]]:
        """Most requested endpoints."""
        counter = Counter(e.endpoint for e in entries if e.endpoint)
        return counter.most_common(n)

    def status_distribution(self, entries: List[LogEntry]) -> Dict[str, int]:
        """Count of 2xx, 3xx, 4xx, 5xx responses."""
        dist = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0, "other": 0}
        for e in entries:
            if 200 <= e.status < 300:
                dist["2xx"] += 1
            elif 300 <= e.status < 400:
                dist["3xx"] += 1
            elif 400 <= e.status < 500:
                dist["4xx"] += 1
            elif 500 <= e.status < 600:
                dist["5xx"] += 1
            else:
                dist["other"] += 1
        return dist

    def error_rate_by_minute(self, entries: List[LogEntry]) -> Dict[str, Tuple[int, int, float]]:
        """
        Returns dict of minute → (total, errors, error_rate).
        Used for anomaly detection.
        """
        by_minute = defaultdict(lambda: [0, 0])  # [total, errors]

        for e in entries:
            if e.timestamp is None:
                continue
            minute_key = e.timestamp.strftime("%Y-%m-%dT%H:%M")
            by_minute[minute_key][0] += 1  # total
            if e.status >= 400:
                by_minute[minute_key][1] += 1  # errors

        return {
            k: (v[0], v[1], v[1] / v[0] if v[0] > 0 else 0.0)
            for k, v in sorted(by_minute.items())
        }

    def detect_anomalies(self, entries: List[LogEntry]) -> List[Dict]:
        """
        Detect minutes where error rate exceeds mean + N * stddev.
        Returns a list of anomaly descriptions.
        """
        rates = self.error_rate_by_minute(entries)
        if len(rates) < 5:
            return []  # Not enough data

        error_rates = [r[2] for r in rates.values() if r[0] >= 10]  # Only minutes with >=10 requests
        if not error_rates:
            return []

        mean = sum(error_rates) / len(error_rates)
        if mean == 0 and all(r == 0 for r in error_rates):
            return []  # No errors at all

        # Handle case where mean is 0 but there are some errors
        variance = sum((r - mean) ** 2 for r in error_rates) / len(error_rates)
        stddev = variance ** 0.5

        threshold = mean + (self.anomaly_threshold * stddev) if stddev > 0 else mean + 0.05

        anomalies = []
        for minute, (total, errors, rate) in rates.items():
            if total >= 10 and rate > threshold and rate > 0.05:
                anomalies.append({
                    "minute": minute,
                    "total_requests": total,
                    "error_count": errors,
                    "error_rate": round(rate, 4),
                    "mean_error_rate": round(mean, 4),
                    "stddev": round(stddev, 4),
                    "threshold": round(threshold, 4),
                    "severity": "HIGH" if rate > threshold * 1.5 else "MEDIUM",
                })

        return anomalies

    def top_user_agents(self, entries: List[LogEntry], n: int = 10) -> List[Tuple[str, int]]:
        """Most common user agents."""
        counter = Counter(e.user_agent for e in entries if e.user_agent and e.user_agent != "-")
        return counter.most_common(n)

    def request_rate_summary(self, entries: List[LogEntry]) -> Dict:
        """Overall request rate statistics (req/s, req/min)."""
        if not entries:
            return {"total_requests": 0, "rps": 0, "rpm": 0}

        total = len(entries)
        timestamps = [e.timestamp for e in entries if e.timestamp is not None]
        if len(timestamps) < 2:
            return {"total_requests": total, "rps": 0, "rpm": 0, "note": "Insufficient timestamps"}

        time_range = (max(timestamps) - min(timestamps)).total_seconds()
        if time_range <= 0:
            return {"total_requests": total, "rps": 0, "rpm": 0, "note": "Zero time range"}

        return {
            "total_requests": total,
            "time_range_seconds": round(time_range, 1),
            "rps": round(total / time_range, 2),
            "rpm": round(total / time_range * 60, 2),
        }


def read_lines(filepath: str) -> List[str]:
    """Read lines from a file, supporting gzip."""
    opener = gzip.open if filepath.endswith(".gz") else open
    with opener(filepath, "rt", errors="replace") as f:
        return f.readlines()


def generate_report(entries: List[LogEntry], analyzer: LogAnalyzer, filepath: str) -> Dict:
    """Generate full analysis report as a dictionary."""
    status_dist = analyzer.status_distribution(entries)
    total = len(entries)
    error_count = status_dist["4xx"] + status_dist["5xx"]
    overall_error_rate = (error_count / total * 100) if total > 0 else 0

    return {
        "file": filepath,
        "analysis_timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "overview": {
            "total_lines": total,
            "parsable_lines": total,  # We skip unparsable lines; could track separately
            "error_count": error_count,
            "error_rate_pct": round(overall_error_rate, 2),
            **analyzer.request_rate_summary(entries),
        },
        "status_distribution": status_dist,
        "top_ips": [{"ip": ip, "count": count} for ip, count in analyzer.top_ips(entries)],
        "top_endpoints": [{"endpoint": ep, "count": count} for ep, count in analyzer.top_endpoints(entries)],
        "top_user_agents": [{"ua": ua, "count": count} for ua, count in analyzer.top_user_agents(entries)][:10],
        "error_rate_by_minute": {
            k: {"total": v[0], "errors": v[1], "error_rate": round(v[2], 4)}
            for k, v in analyzer.error_rate_by_minute(entries).items()
        },
        "anomalies": analyzer.detect_anomalies(entries),
    }


def print_report(report: Dict):
    """Pretty-print analysis report to stdout."""
    ov = report["overview"]
    sd = report["status_distribution"]

    print("=" * 70)
    print("LOG ANALYSIS REPORT")
    print(f"File: {report['file']}")
    print(f"Total requests: {ov['total_lines']:,}")
    print(f"Error rate: {ov['error_rate_pct']:.2f}% ({ov['error_count']:,} errors)")
    print(f"Request rate: {ov.get('rps', 'N/A')} req/s | {ov.get('rpm', 'N/A')} req/min")
    print("=" * 70)

    print(f"\nStatus Distribution:")
    for cat in ("2xx", "3xx", "4xx", "5xx"):
        count = sd[cat]
        pct = (count / ov['total_lines'] * 100) if ov['total_lines'] > 0 else 0
        bar = "█" * int(pct / 2)
        print(f"  {cat}: {count:>8,} ({pct:5.1f}%) {bar}")

    print(f"\nTop 10 IPs:")
    for item in report["top_ips"][:10]:
        print(f"  {item['ip']:<20} {item['count']:>8,} requests")

    print(f"\nTop 10 Endpoints:")
    for item in report["top_endpoints"][:10]:
        print(f"  {item['endpoint']:<50} {item['count']:>8,}")

    anomalies = report["anomalies"]
    if anomalies:
        print(f"\n{'⚠' * 20}")
        print(f"ANOMALIES DETECTED: {len(anomalies)}")
        print(f"{'⚠' * 20}")
        for a in anomalies[:10]:
            print(f"  [{a['severity']}] {a['minute']}: "
                  f"{a['error_rate']*100:.1f}% error rate "
                  f"(mean: {a['mean_error_rate']*100:.1f}%, threshold: {a['threshold']*100:.1f}%)")
    else:
        print(f"\nNo anomalies detected.")


def main():
    parser = ArgumentParser(
        description="Log analyzer for nginx/combined access logs and JSON log files"
    )
    parser.add_argument("--file", required=True, help="Path to log file (.gz supported)")
    parser.add_argument("--format", choices=("nginx", "json"), default="nginx",
                        help="Log format (default: nginx)")
    parser.add_argument("--timestamp-field", help="JSON field containing timestamp (auto-detect if omitted)")
    parser.add_argument("--anomaly-threshold", type=float, default=3.0,
                        help="Std dev multiplier for anomaly detection (default: 3.0)")
    parser.add_argument("--output", help="Save JSON report to file")
    args = parser.parse_args()

    # Read log file
    try:
        lines = read_lines(args.file)
    except FileNotFoundError:
        print(f"ERROR: File not found: {args.file}", file=sys.stderr)
        sys.exit(2)
    except IOError as e:
        print(f"ERROR: Cannot read file: {e}", file=sys.stderr)
        sys.exit(2)

    print(f"Read {len(lines):,} lines from {args.file}", file=sys.stderr)

    # Parse lines
    entries = []
    unparsable = 0
    for line in lines:
        if args.format == "nginx":
            entry = parse_nginx_line(line)
        else:
            entry = parse_json_line(line, args.timestamp_field or "")

        if entry:
            entries.append(entry)
        else:
            unparsable += 1

    print(f"Parsed {len(entries):,} entries ({unparsable:,} unparsable)",
          file=sys.stderr)

    if not entries:
        print("ERROR: No parsable log entries found", file=sys.stderr)
        sys.exit(2)

    # Analyze
    analyzer = LogAnalyzer(anomaly_threshold=args.anomaly_threshold)
    report = generate_report(entries, analyzer, args.file)
    print_report(report)

    # Save JSON report if requested
    if args.output:
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\nJSON report saved to {args.output}", file=sys.stderr)

    # Exit code
    if report["anomalies"]:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
