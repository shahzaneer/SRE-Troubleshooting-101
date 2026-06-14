# 15 — Scripts Toolkit

> **Section Owner:** SRE Platform On-Call
> **Last Reviewed:** 2026-06-11

A curated collection of battle-tested utility scripts for day-to-day SRE operations. Every script here has been used in production incidents. They are designed to be run quickly, give clear output, and exit with appropriate status codes for CI/CD or monitoring integration.

---

## Scripts

| Script | Language | Purpose | Usage |
|--------|----------|---------|-------|
| `health_checker.py` | Python 3.9+ | Multi-endpoint HTTP health checker with retries and JSON output | `python health_checker.py --endpoints https://api1/health,https://api2/health` |
| `log_analyzer.py` | Python 3.9+ | Nginx/JSON log parser with anomaly detection | `python log_analyzer.py --file /var/log/nginx/access.log` |
| `cert_expiry_checker.py` | Python 3.9+ | SSL/TLS certificate expiry checker with CSV export | `python cert_expiry_checker.py --domains example.com,google.com` |
| `db_connection_pool_monitor.py` | Python 3.9+ | PostgreSQL connection pool utilization monitor | `python db_connection_pool_monitor.py --host localhost --db mydb` |
| `disk_emergency.sh` | Bash 4+ | Disk space emergency — find and clean large files | `./disk_emergency.sh 90` |
| `tcp_connection_audit.sh` | Bash 4+ | TCP connection state audit and leak detection | `./tcp_connection_audit.sh` |
| `k8s_pod_debug.sh` | Bash 4+ | Interactive Kubernetes pod debugger | `./k8s_pod_debug.sh <pod-name> [namespace]` |

---

## Script Conventions

Every script in this toolkit follows these conventions:
- **Exit codes:** 0 = healthy/normal, 1 = problem detected, 2 = usage error
- **Output:** Human-readable to stderr, machine-parseable (JSON/CSV) to stdout where applicable
- **Dependencies:** Listed in a comment at the top of each script
- **No destructive actions by default:** Scripts that can clean/delete require explicit flags
- **Timeout aware:** Network operations have configurable timeouts (no hanging forever)

---

## Installation

```bash
# Make bash scripts executable
chmod +x 16-scripts-toolkit/*.sh

# Python dependencies (install as needed)
pip install requests psycopg2-binary  # health_checker, db_connection_pool_monitor
```
