#!/usr/bin/env python3
"""
db_connection_pool_monitor.py — PostgreSQL connection pool utilization monitor.
Connects to PostgreSQL, queries pg_stat_activity, and reports connection
utilization as a percentage of max_connections.

Dependencies: pip install psycopg2-binary

Usage:
    python db_connection_pool_monitor.py --host localhost --port 5432 --db mydb --user monitor
    python db_connection_pool_monitor.py --host localhost --db mydb --user monitor --password secret
    python db_connection_pool_monitor.py --host localhost --db mydb --user monitor --output json
    python db_connection_pool_monitor.py --host localhost --db mydb --user monitor --alert-threshold 80

Environment variables (alternative to CLI args):
    PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD

Exit codes:
    0 — Connection pool healthy (below threshold)
    1 — Connection pool above alert threshold
    2 — Usage error or connection failure
"""

import json
import os
import sys
from argparse import ArgumentParser
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple


def get_connection_params(args) -> Dict[str, str]:
    """Resolve connection parameters from CLI args or environment variables."""
    params = {
        "host": args.host or os.environ.get("PGHOST", "localhost"),
        "port": str(args.port) or os.environ.get("PGPORT", "5432"),
        "dbname": args.db or os.environ.get("PGDATABASE", "postgres"),
        "user": args.user or os.environ.get("PGUSER", "postgres"),
        "password": args.password or os.environ.get("PGPASSWORD", ""),
        "connect_timeout": str(args.connect_timeout),
    }
    return params


def query_connection_stats(params: Dict[str, str]) -> Dict:
    """Execute monitoring queries against PostgreSQL and return raw results."""

    # Attempt to import psycopg2
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary",
              file=sys.stderr)
        sys.exit(2)

    conn = None
    try:
        conn = psycopg2.connect(**{
            k: v for k, v in params.items() if v  # Skip empty strings
        })
        conn.set_session(autocommit=True)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        stats: Dict = {}

        # 1. Get max_connections
        cur.execute("SHOW max_connections;")
        stats["max_connections"] = int(cur.fetchone()["max_connections"])

        # 2. Get current connection count
        cur.execute("SELECT count(*) AS total FROM pg_stat_activity;")
        stats["current_connections"] = int(cur.fetchone()["total"])

        # 3. Get connections by state
        cur.execute("""
            SELECT state, count(*) AS count
            FROM pg_stat_activity
            GROUP BY state
            ORDER BY count DESC;
        """)
        stats["by_state"] = {row["state"] or "NULL": row["count"] for row in cur.fetchall()}

        # 4. Get connections by application name
        cur.execute("""
            SELECT application_name, count(*) AS count
            FROM pg_stat_activity
            WHERE application_name != ''
            GROUP BY application_name
            ORDER BY count DESC
            LIMIT 20;
        """)
        stats["by_application"] = {
            row["application_name"]: row["count"]
            for row in cur.fetchall()
        }

        # 5. Get connections by database
        cur.execute("""
            SELECT datname, count(*) AS count
            FROM pg_stat_activity
            WHERE datname IS NOT NULL
            GROUP BY datname
            ORDER BY count DESC;
        """)
        stats["by_database"] = {
            row["datname"]: row["count"]
            for row in cur.fetchall()
        }

        # 6. Get connections by wait event (what are they waiting on?)
        cur.execute("""
            SELECT wait_event_type, wait_event, count(*) AS count
            FROM pg_stat_activity
            WHERE wait_event IS NOT NULL
            GROUP BY wait_event_type, wait_event
            ORDER BY count DESC
            LIMIT 15;
        """)
        stats["wait_events"] = []
        for row in cur.fetchall():
            stats["wait_events"].append({
                "type": row["wait_event_type"],
                "event": row["wait_event"],
                "count": row["count"],
            })

        # 7. Long-running queries (> 30 seconds)
        cur.execute("""
            SELECT pid, state, now() - query_start AS duration,
                   left(query, 200) AS query_preview
            FROM pg_stat_activity
            WHERE state != 'idle'
              AND now() - query_start > interval '30 seconds'
              AND query NOT LIKE '%pg_stat_activity%'
            ORDER BY duration DESC
            LIMIT 10;
        """)
        stats["long_running_queries"] = []
        for row in cur.fetchall():
            stats["long_running_queries"].append({
                "pid": row["pid"],
                "state": row["state"],
                "duration_seconds": round(row["duration"].total_seconds(), 1),
                "query_preview": row["query_preview"],
            })

        # 8. Idle in transaction connections (potential leaks)
        cur.execute("""
            SELECT count(*) AS count
            FROM pg_stat_activity
            WHERE state = 'idle in transaction'
              AND now() - state_change > interval '5 minutes';
        """)
        stats["idle_in_transaction_long"] = int(cur.fetchone()["count"])

        # 9. Superuser connections
        cur.execute("""
            SELECT count(*) AS count
            FROM pg_stat_activity
            WHERE usesysid = 10;
        """)
        stats["superuser_connections"] = int(cur.fetchone()["count"])

        cur.close()
        return stats

    except psycopg2.OperationalError as e:
        print(f"ERROR: Cannot connect to PostgreSQL: {e}", file=sys.stderr)
        print(f"  Host: {params['host']}:{params['port']}", file=sys.stderr)
        print(f"  Database: {params['dbname']}", file=sys.stderr)
        print(f"  User: {params['user']}", file=sys.stderr)
        sys.exit(2)
    finally:
        if conn:
            conn.close()


def build_report(stats: Dict, alert_threshold: float, params: Dict) -> Dict:
    """Process raw stats into a rich report."""
    max_conn = stats["max_connections"]
    current = stats["current_connections"]
    utilization = (current / max_conn * 100) if max_conn > 0 else 0
    available = max_conn - current

    active = stats["by_state"].get("active", 0)
    idle = stats["by_state"].get("idle", 0)
    idle_in_transaction = stats["by_state"].get("idle in transaction", 0)

    # Determine status
    if utilization >= alert_threshold:
        status = "CRITICAL"
        color = "\033[31m"
    elif utilization >= alert_threshold * 0.75:
        status = "WARNING"
        color = "\033[33m"
    else:
        status = "HEALTHY"
        color = "\033[32m"

    return {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "server": f"{params['host']}:{params['port']}/{params['dbname']}",
        "status": status,
        "pool": {
            "max_connections": max_conn,
            "current_connections": current,
            "available": available,
            "utilization_pct": round(utilization, 1),
            "alert_threshold_pct": alert_threshold,
            "active": active,
            "idle": idle,
            "idle_in_transaction": idle_in_transaction,
            "superuser_connections": stats["superuser_connections"],
            "idle_in_transaction_long_minutes": stats["idle_in_transaction_long"],
        },
        "by_state": stats["by_state"],
        "by_application": stats["by_application"],
        "by_database": stats["by_database"],
        "wait_events": stats["wait_events"],
        "long_running_queries": stats["long_running_queries"],
        "recommendations": _generate_recommendations(stats, utilization, alert_threshold),
    }


def _generate_recommendations(stats: Dict, utilization: float, threshold: float) -> List[str]:
    """Generate actionable recommendations based on the data."""
    recs = []

    if utilization >= threshold:
        recs.append(
            f"CRITICAL: Connection pool at {utilization:.1f}% — increase max_connections "
            f"or reduce application pool sizes immediately"
        )

    idle_txn = stats.get("idle_in_transaction_long", 0)
    if idle_txn > 0:
        recs.append(
            f"WARNING: {idle_txn} connection(s) idle in transaction for >5 min — "
            f"check for unclosed transactions in application code"
        )

    if stats.get("long_running_queries"):
        count = len(stats["long_running_queries"])
        longest = stats["long_running_queries"][0]
        recs.append(
            f"WARNING: {count} long-running query(s) — longest: {longest['duration_seconds']:.0f}s "
            f"(PID {longest['pid']}: {longest['query_preview'][:80]})"
        )

    by_app = stats.get("by_application", {})
    if len(by_app) > 10:
        recs.append(
            f"INFO: {len(by_app)} distinct application names — consider standardizing "
            f"application_name for better monitoring"
        )

    if not recs:
        recs.append("Connection pool is healthy. No action needed.")

    return recs


def print_report(report: Dict):
    """Pretty-print the pool report to stdout."""
    pool = report["pool"]
    status = report["status"]
    color = {"CRITICAL": "\033[31m", "WARNING": "\033[33m", "HEALTHY": "\033[32m"}.get(status, "")
    reset = "\033[0m"

    print("=" * 60)
    print(f"{color}PostgreSQL Connection Pool Monitor{reset}")
    print(f"Server: {report['server']}")
    print(f"Time:   {report['timestamp']}")
    print("=" * 60)

    # Utilization bar
    util = pool["utilization_pct"]
    bar_len = 40
    filled = int(bar_len * util / 100)
    bar = "█" * filled + "░" * (bar_len - filled)
    print(f"\n  [{color}{bar}{reset}] {color}{util:.1f}%{reset}")
    print(f"  {pool['current_connections']}/{pool['max_connections']} connections "
          f"({pool['available']} available)")
    print(f"\n  Status:  {color}{status}{reset}")
    print(f"  Active:  {pool['active']}")
    print(f"  Idle:    {pool['idle']}")
    print(f"  Idle in Transaction: {pool['idle_in_transaction']} "
          f"({pool['idle_in_transaction_long_minutes']} >5 min)")

    # Wait events
    if report["wait_events"]:
        print(f"\n  Top Wait Events:")
        for we in report["wait_events"][:5]:
            print(f"    {we['type']}/{we['event']}: {we['count']}")

    # Long-running queries
    lrq = report["long_running_queries"]
    if lrq:
        print(f"\n  ⚠  Long-Running Queries:")
        for q in lrq[:5]:
            print(f"    PID {q['pid']}: {q['duration_seconds']:.0f}s — {q['query_preview'][:100]}")

    # By application
    if report["by_application"]:
        print(f"\n  Connections by Application:")
        for app_name, count in list(report["by_application"].items())[:10]:
            print(f"    {app_name:<40} {count}")

    # Recommendations
    print(f"\n  Recommendations:")
    for rec in report["recommendations"]:
        print(f"    • {rec}")

    print(f"\n{'=' * 60}")


def main():
    parser = ArgumentParser(
        description="PostgreSQL connection pool utilization monitor"
    )
    parser.add_argument("--host", help="PostgreSQL host (env: PGHOST)")
    parser.add_argument("--port", type=int, default=5432, help="PostgreSQL port (env: PGPORT, default: 5432)")
    parser.add_argument("--db", help="Database name (env: PGDATABASE)")
    parser.add_argument("--user", help="Database user (env: PGUSER)")
    parser.add_argument("--password", help="Database password (env: PGPASSWORD)")
    parser.add_argument("--connect-timeout", type=int, default=10,
                        help="Connection timeout in seconds (default: 10)")
    parser.add_argument("--alert-threshold", type=float, default=80.0,
                        help="Alert if utilization exceeds this %% (default: 80)")
    parser.add_argument("--output", choices=("text", "json"), default="text",
                        help="Output format (default: text)")
    args = parser.parse_args()

    params = get_connection_params(args)
    stats = query_connection_stats(params)
    report = build_report(stats, args.alert_threshold, params)

    if args.output == "json":
        print(json.dumps(report, indent=2))
    else:
        print_report(report)

    # Exit code based on status
    if report["status"] == "CRITICAL":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
