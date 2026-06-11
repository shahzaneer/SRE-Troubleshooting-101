#!/usr/bin/env python3
"""
cert_expiry_checker.py — SSL/TLS certificate expiry checker for multiple domains.
Connects to each domain on port 443, extracts the certificate, checks expiry date,
and generates a CSV report with days remaining.

Dependencies: None (stdlib only)

Usage:
    python cert_expiry_checker.py --domains example.com,google.com,github.com
    python cert_expiry_checker.py --domains example.com --output report.csv
    python cert_expiry_checker.py --file domains.txt
    python cert_expiry_checker.py --domains example.com --port 8443

Exit codes:
    0 — All certificates valid (> 60 days remaining)
    1 — One or more certificates expiring within 30 days (CRITICAL)
    2 — Usage error
"""

import csv
import socket
import ssl
import sys
import os
from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Tuple


@dataclass
class CertInfo:
    """Information about a single TLS certificate."""
    domain: str
    port: int = 443
    issuer: str = ""
    subject: str = ""
    serial_number: str = ""
    not_before: datetime = field(default_factory=lambda: datetime(1970, 1, 1, tzinfo=timezone.utc))
    not_after: datetime = field(default_factory=lambda: datetime(1970, 1, 1, tzinfo=timezone.utc))
    days_remaining: int = 0
    is_expired: bool = False
    status: str = "UNKNOWN"  # OK, WARNING, CRITICAL, EXPIRED, ERROR
    error: Optional[str] = None
    sans: List[str] = field(default_factory=list)

    @property
    def status_color(self) -> str:
        """ANSI color code for terminal output."""
        return {
            "OK": "\033[32m",        # Green
            "WARNING": "\033[33m",   # Yellow
            "CRITICAL": "\033[31m",  # Red
            "EXPIRED": "\033[31m",   # Red
            "ERROR": "\033[35m",     # Magenta
        }.get(self.status, "\033[0m")

    @property
    def to_csv_row(self) -> List[str]:
        return [
            self.domain,
            str(self.port),
            self.not_after.strftime("%Y-%m-%d %H:%M:%S UTC"),
            str(self.days_remaining),
            self.status,
            self.issuer,
            self.subject,
            ", ".join(self.sans[:5]) + ("..." if len(self.sans) > 5 else ""),
            self.error or "",
        ]


# CSV header
CSV_HEADER = [
    "Domain", "Port", "Expiry Date (UTC)", "Days Remaining",
    "Status", "Issuer", "Subject", "Subject Alternative Names", "Error"
]


def check_certificate(domain: str, port: int = 443, timeout: float = 10.0) -> CertInfo:
    """
    Connect to a domain on the specified port, perform TLS handshake,
    and extract certificate information.
    """
    info = CertInfo(domain=domain, port=port)

    try:
        # Create TCP connection
        sock = socket.create_connection((domain, port), timeout=timeout)
    except socket.gaierror:
        info.status = "ERROR"
        info.error = f"DNS resolution failed: {domain}"
        return info
    except socket.timeout:
        info.status = "ERROR"
        info.error = f"Connection timed out after {timeout}s"
        return info
    except ConnectionRefusedError:
        info.status = "ERROR"
        info.error = f"Connection refused on port {port}"
        return info
    except OSError as e:
        info.status = "ERROR"
        info.error = f"OS error: {e}"
        return info

    try:
        # Create SSL context (system default CA bundle, strict verification)
        ctx = ssl.create_default_context()
        # Set SNI (Server Name Indication) — required for virtual hosting
        with ctx.wrap_socket(sock, server_hostname=domain) as ssock:
            cert_der = ssock.getpeercert(binary_form=True)
            cert_dict = ssock.getpeercert()
    except ssl.SSLCertVerificationError as e:
        info.status = "ERROR"
        info.error = f"SSL verification failed: {e}"
        sock.close()
        return info
    except ssl.SSLError as e:
        info.status = "ERROR"
        info.error = f"SSL/TLS error: {e}"
        sock.close()
        return info
    except Exception as e:
        info.status = "ERROR"
        info.error = f"Connection error: {type(e).__name__}: {e}"
        sock.close()
        return info
    finally:
        try:
            sock.close()
        except Exception:
            pass

    # Parse certificate fields
    if cert_dict:
        info.issuer = _format_dn(cert_dict.get("issuer", []))
        info.subject = _format_dn(cert_dict.get("subject", []))
        info.serial_number = cert_dict.get("serialNumber", "")

        # Extract SANs
        san_ext = cert_dict.get("subjectAltName", [])
        info.sans = [name for _, name in san_ext]

    # Parse expiry from cert DER (cryptography library not required)
    # ssl.getpeercert() returns notAfter as a string like 'Jun 10 14:30:00 2026 GMT'
    not_after_str = cert_dict.get("notAfter", "") if cert_dict else ""
    try:
        info.not_after = datetime.strptime(not_after_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        # Fallback: try parsing with dateutil-like format
        try:
            info.not_after = datetime.strptime(not_after_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            info.status = "ERROR"
            info.error = f"Could not parse expiry date: {not_after_str}"
            return info

    # Calculate days remaining
    now = datetime.now(timezone.utc)
    delta = info.not_after - now
    info.days_remaining = delta.days
    info.is_expired = delta.total_seconds() < 0

    # Determine status
    if info.is_expired:
        info.status = "EXPIRED"
    elif info.days_remaining < 30:
        info.status = "CRITICAL"
    elif info.days_remaining < 60:
        info.status = "WARNING"
    else:
        info.status = "OK"

    return info


def _format_dn(dn: List[Tuple[str, str]]) -> str:
    """Format a Distinguished Name tuple list into a readable string."""
    if not dn:
        return ""
    # Reverse order for readability (root → leaf in DN)
    parts = []
    for key, value in reversed(dn):
        parts.append(f"{key}={value}")
    return ", ".join(parts)


def load_domains_from_file(filepath: str) -> List[str]:
    """Read domains from a file (one per line, ignores comments and blanks)."""
    with open(filepath) as f:
        return [
            line.strip()
            for line in f
            if line.strip() and not line.strip().startswith("#")
        ]


def print_results(results: List[CertInfo]):
    """Pretty-print results with color coding."""
    RED = "\033[31m"
    YELLOW = "\033[33m"
    GREEN = "\033[32m"
    RESET = "\033[0m"

    print("\n" + "=" * 90)
    print(f"{'Domain':<35} {'Port':<6} {'Days Left':>10} {'Status':<12} {'Expiry Date'}")
    print("=" * 90)

    for r in sorted(results, key=lambda x: x.days_remaining):
        if r.status == "OK":
            color = GREEN
        elif r.status == "WARNING":
            color = YELLOW
        else:
            color = RED

        days = f"{r.days_remaining}d"
        if r.is_expired:
            days = f"-{abs(r.days_remaining)}d (EXPIRED!)"

        print(f"{r.domain:<35} {r.port:<6} {color}{days:>10}{RESET} "
              f"{color}{r.status:<12}{RESET} {r.not_after.strftime('%Y-%m-%d')}")

    print("=" * 90)
    print(f"\nSummary: {sum(1 for r in results if r.status == 'OK')} OK, "
          f"{sum(1 for r in results if r.status == 'WARNING')} WARNING, "
          f"{sum(1 for r in results if r.status == 'CRITICAL')} CRITICAL, "
          f"{sum(1 for r in results if r.status == 'EXPIRED')} EXPIRED, "
          f"{sum(1 for r in results if r.status == 'ERROR')} ERROR")


def write_csv(results: List[CertInfo], filepath: str):
    """Write results to a CSV file."""
    with open(filepath, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        for r in sorted(results, key=lambda x: x.days_remaining):
            writer.writerow(r.to_csv_row)
    print(f"\nCSV report written to {filepath}")


def main():
    parser = ArgumentParser(
        description="SSL/TLS certificate expiry checker for multiple domains"
    )
    parser.add_argument(
        "--domains",
        help="Comma-separated list of domains to check",
    )
    parser.add_argument(
        "--file",
        help="File containing domains (one per line)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=443,
        help="Port to connect to (default: 443)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Connection timeout in seconds (default: 10)",
    )
    parser.add_argument(
        "--output",
        help="Save CSV report to file",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=20,
        help="Max concurrent checks (default: 20)",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable color output",
    )

    args = parser.parse_args()

    # Load domains
    domains: List[str] = []
    if args.domains:
        domains = [d.strip() for d in args.domains.split(",") if d.strip()]
    elif args.file:
        try:
            domains = load_domains_from_file(args.file)
        except FileNotFoundError:
            print(f"ERROR: File not found: {args.file}", file=sys.stderr)
            sys.exit(2)
    elif os.environ.get("CERT_CHECK_DOMAINS"):
        domains = [d.strip() for d in os.environ["CERT_CHECK_DOMAINS"].split(",") if d.strip()]
    else:
        print("ERROR: Specify --domains, --file, or CERT_CHECK_DOMAINS env var",
              file=sys.stderr)
        sys.exit(2)

    if not domains:
        print("ERROR: No domains specified", file=sys.stderr)
        sys.exit(2)

    # Remove duplicates
    unique_domains = list(dict.fromkeys(domains))

    print(f"Checking {len(unique_domains)} domain(s) on port {args.port}...", file=sys.stderr)

    # Check certificates concurrently
    results: List[CertInfo] = []
    with ThreadPoolExecutor(max_workers=min(args.max_workers, len(unique_domains))) as executor:
        future_to_domain = {
            executor.submit(check_certificate, domain, args.port, args.timeout): domain
            for domain in unique_domains
        }
        for future in as_completed(future_to_domain):
            domain = future_to_domain[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                err = CertInfo(domain=domain, port=args.port)
                err.status = "ERROR"
                err.error = f"Thread error: {e}"
                results.append(err)

    # Print results
    print_results(results)

    # Write CSV if requested
    if args.output:
        write_csv(results, args.output)

    # Detailed error output for any failures
    errors = [r for r in results if r.status == "ERROR"]
    if errors:
        print(f"\nErrors encountered ({len(errors)}):")
        for r in errors:
            print(f"  {r.domain}: {r.error}")

    # Determine exit code
    critical_or_expired = sum(
        1 for r in results if r.status in ("CRITICAL", "EXPIRED")
    )
    if critical_or_expired > 0:
        print(f"\n{critical_or_expired} certificate(s) require immediate attention!")
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
