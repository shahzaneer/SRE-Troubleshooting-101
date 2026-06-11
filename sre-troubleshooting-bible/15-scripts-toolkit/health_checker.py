#!/usr/bin/env python3
"""
health_checker.py — Multi-endpoint HTTP health checker with retries and JSON summary.

Dependencies: pip install requests

Usage:
    python health_checker.py --endpoints https://api1.example.com/health,https://api2.example.com/health
    python health_checker.py --endpoints https://api.example.com/health --timeout 5 --retries 5
    python health_checker.py --config health_endpoints.json

Config file format (JSON):
{
    "endpoints": [
        "https://api1.example.com/health",
        "https://api2.example.com/health"
    ],
    "timeout": 5,
    "retries": 3
}

Exit codes:
    0 — All endpoints healthy
    1 — One or more endpoints down
    2 — Usage/configuration error
"""

import json
import os
import sys
import time
import signal
from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class HealthCheckResult:
    """Result of a single endpoint health check."""

    def __init__(self, url: str):
        self.url = url
        self.status: str = "UNKNOWN"
        self.status_code: Optional[int] = None
        self.response_time_ms: float = 0.0
        self.error: Optional[str] = None
        self.attempts: int = 0

    @property
    def is_up(self) -> bool:
        return self.status == "UP"

    def to_dict(self) -> Dict:
        d = {
            "url": self.url,
            "status": self.status,
            "status_code": self.status_code,
            "response_time_ms": round(self.response_time_ms, 2),
            "attempts": self.attempts,
        }
        if self.error:
            d["error"] = str(self.error)[:200]
        return d


class HealthChecker:
    """Checks health of multiple HTTP(S) endpoints concurrently with retries."""

    def __init__(self, timeout: int = 5, retries: int = 3, max_workers: int = 10):
        self.timeout = timeout
        self.max_workers = max_workers

        # Setup session with retry strategy (exponential backoff)
        self.session = requests.Session()
        retry_strategy = Retry(
            total=retries,
            backoff_factor=0.5,  # 0.5s, 1s, 2s, 4s...
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "HEAD"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        # Set a user-agent that identifies this health checker
        self.session.headers.update({
            "User-Agent": "SRE-HealthChecker/1.0",
            "Accept": "application/json, text/plain, */*",
        })

    def check_single(self, url: str) -> HealthCheckResult:
        """Check a single endpoint with retries and exponential backoff."""
        result = HealthCheckResult(url)

        # Manual retry loop for more granular control and timing
        for attempt in range(1, 4):  # Up to 3 attempts (1 original + 2 retries)
            result.attempts = attempt
            try:
                start = time.monotonic()
                response = self.session.get(
                    url,
                    timeout=(3.05, self.timeout),  # (connect_timeout, read_timeout)
                    allow_redirects=True,
                )
                elapsed = (time.monotonic() - start) * 1000  # Convert to ms
                result.response_time_ms = elapsed
                result.status_code = response.status_code

                if 200 <= response.status_code < 400:
                    result.status = "UP"
                    return result
                else:
                    result.status = "DOWN"
                    result.error = f"HTTP {response.status_code}"

            except requests.exceptions.ConnectionError as e:
                result.error = f"Connection refused: {e.args[0].args[0] if e.args and e.args[0].args else str(e)}"
            except requests.exceptions.Timeout:
                result.error = f"Timeout after {self.timeout}s"
            except requests.exceptions.TooManyRedirects:
                result.error = "Too many redirects"
            except requests.exceptions.SSLError as e:
                result.error = f"SSL Error: {e}"
            except Exception as e:
                result.error = f"Unexpected: {type(e).__name__}: {e}"

            # Exponential backoff before retry
            if attempt < 3:
                wait = (2 ** (attempt - 1)) * 0.5
                time.sleep(wait)

        result.status = "DOWN"
        return result

    def check_all(self, endpoints: List[str]) -> List[HealthCheckResult]:
        """Check all endpoints concurrently."""
        results = []
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(endpoints))) as executor:
            future_to_url = {
                executor.submit(self.check_single, url): url
                for url in endpoints
            }
            for future in as_completed(future_to_url):
                url = future_to_url[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    result = HealthCheckResult(url)
                    result.status = "ERROR"
                    result.error = f"Thread error: {e}"
                    results.append(result)
        return results


def build_summary(results: List[HealthCheckResult]) -> Dict:
    """Build a JSON-serializable summary of all health check results."""
    up_count = sum(1 for r in results if r.is_up)
    down_count = sum(1 for r in results if not r.is_up)

    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "summary": {
            "total": len(results),
            "up": up_count,
            "down": down_count,
            "healthy": up_count == len(results),
        },
        "endpoints": [r.to_dict() for r in sorted(results, key=lambda r: r.url)],
    }


def load_endpoints_from_config(config_path: str) -> Tuple[List[str], int, int]:
    """Parse a JSON config file for endpoints and settings."""
    with open(config_path) as f:
        config = json.load(f)

    if "endpoints" not in config:
        raise ValueError("Config file must contain an 'endpoints' key")

    return (
        config["endpoints"],
        config.get("timeout", 5),
        config.get("retries", 3),
    )


def main():
    parser = ArgumentParser(
        description="Multi-endpoint HTTP health checker with retries and JSON output"
    )
    parser.add_argument(
        "--endpoints",
        help="Comma-separated list of health check URLs",
    )
    parser.add_argument(
        "--config",
        help="JSON config file with endpoints array",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=5,
        help="Request timeout in seconds (default: 5)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Number of retry attempts (default: 3)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=10,
        help="Max concurrent health checks (default: 10)",
    )
    parser.add_argument(
        "--output",
        default="-",
        help="Output JSON file path (- for stdout, default: -)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-endpoint stderr output",
    )

    args = parser.parse_args()

    # Load endpoints
    endpoints = []
    timeout = args.timeout
    retries = args.retries

    if args.config:
        try:
            endpoints, timeout, retries = load_endpoints_from_config(args.config)
        except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
            print(f"ERROR: Failed to load config: {e}", file=sys.stderr)
            sys.exit(2)
    elif args.endpoints:
        endpoints = [url.strip() for url in args.endpoints.split(",") if url.strip()]
    elif os.environ.get("HEALTH_CHECK_ENDPOINTS"):
        endpoints = [
            url.strip()
            for url in os.environ["HEALTH_CHECK_ENDPOINTS"].split(",")
            if url.strip()
        ]
    else:
        print("ERROR: Specify --endpoints, --config, or HEALTH_CHECK_ENDPOINTS env var",
              file=sys.stderr)
        sys.exit(2)

    if not endpoints:
        print("ERROR: No endpoints specified", file=sys.stderr)
        sys.exit(2)

    # Run health checks
    checker = HealthChecker(timeout=timeout, retries=retries, max_workers=args.max_workers)
    results = checker.check_all(endpoints)
    summary = build_summary(results)

    # Output per-endpoint status to stderr (unless quiet)
    if not args.quiet:
        for r in sorted(results, key=lambda r: r.url):
            icon = "✓" if r.is_up else "✗"
            timing = f"{r.response_time_ms:.0f}ms" if r.response_time_ms > 0 else "N/A"
            error_info = f" — {r.error}" if r.error else ""
            print(f"  {icon} {r.url} [{r.status_code}] {timing} (attempt {r.attempts}){error_info}",
                  file=sys.stderr)

    # Output full JSON summary
    json_output = json.dumps(summary, indent=2)
    if args.output == "-":
        print(json_output)
    else:
        with open(args.output, "w") as f:
            f.write(json_output)
        print(f"\nReport written to {args.output}", file=sys.stderr)

    # Exit with appropriate code
    if summary["summary"]["healthy"]:
        print(f"\nAll {summary['summary']['total']} endpoints UP",
              file=sys.stderr)
        sys.exit(0)
    else:
        print(f"\n{summary['summary']['down']}/{summary['summary']['total']} endpoints DOWN",
              file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
