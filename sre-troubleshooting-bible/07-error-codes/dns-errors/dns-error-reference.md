# DNS Error Reference
> **Category:** Networking | DNS | Error Codes
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#dns` `#networking` `#oncall`

---

## Overview

DNS (Domain Name System) resolution failures are a leading cause of "the network is broken" incidents that are not actually network problems. A single DNS misconfiguration can take down an entire microservice mesh because service discovery depends on it. This reference covers all DNS RCODEs (Response Codes), their diagnostic commands, and real-world troubleshooting scenarios.

---

## Quick Reference: DNS RCODEs

| RCODE | Name | Meaning | Immediate Action |
|-------|------|---------|-----------------|
| 0 | NOERROR | Query succeeded | None — normal operation |
| 1 | FORMERR | Format error — nameserver couldn't parse query | Check EDNS0, query syntax |
| 2 | SERVFAIL | Server failure — upstream resolution broke | `dig +trace` to find breakage point |
| 3 | NXDOMAIN | Domain does not exist (authoritative) | Check typos, DNS zone config |
| 4 | NOTIMP | Query type not implemented | Check query type (ANY, CAA, etc.) |
| 5 | REFUSED | Query refused by nameserver policy | Check ACLs, recursion settings |

---

## DNS Resolution Path

When an application calls `getaddrinfo("api.example.com")`, the resolver follows this path:

```
Application
    │
    ▼
/etc/resolv.conf (or systemd-resolved)
    │
    ▼
DNS Resolver (8.8.8.8, 1.1.1.1, or corporate resolver)
    │
    ├── Cache hit? → Return cached answer
    │
    └── Cache miss? → Recursive resolution:
            │
            ▼
        Root nameservers (.)
            │ → Returns .com nameservers
            ▼
        TLD nameservers (.com)
            │ → Returns example.com nameservers
            ▼
        Authoritative nameservers (ns1.example.com)
            │ → Returns A record for api.example.com
            ▼
        Answer: api.example.com. 300 IN A 10.0.1.42
```

**The most common failure points:**
1. `/etc/resolv.conf` points to unreachable or wrong nameserver
2. Recursive resolver is down or misconfigured
3. Authoritative nameserver is unreachable
4. DNSSEC validation fails at any level
5. Firewall blocking port 53 (UDP and TCP)

---

## NXDOMAIN (RCODE 3)

### Meaning

The authoritative nameserver for the domain explicitly states that the domain does not exist. This is a definitive answer — the domain is not just temporarily unavailable, it does not exist in the zone.

### Diagnosis

```bash
# Basic dig — check status line
dig api.example.com
# Look for: ;; ->>HEADER<<- opcode: QUERY, status: NXDOMAIN

# Check if the problem is specific to a nameserver
dig api.example.com @8.8.8.8        # Google DNS
dig api.example.com @1.1.1.1        # Cloudflare DNS
dig api.example.com @your-resolver  # Your corporate resolver

# Get the SOA record to see who is authoritative
dig +short SOA example.com
# Example output: ns-123.awsdns-45.com. awsdns-hostmaster.amazon.com. 1 7200 900 1209600 86400

# Query the authoritative nameserver directly
dig api.example.com @ns-123.awsdns-45.com
# If this returns NOERROR but your resolver returns NXDOMAIN,
# the zone was recently created and cached negative answer hasn't expired
```

### Common Causes and Fixes

| Cause | How to detect | Fix |
|-------|---------------|-----|
| **Typo in hostname** | `dig` returns NXDOMAIN, correct name works | Fix the application config |
| **DNS record deleted** | Zone file / Route53 doesn't have the record | Recreate the DNS record |
| **Internal vs external DNS** | `dig domain @8.8.8.8` works, `dig domain @internal-ns` fails | Internal zone missing record; add it |
| **Zone transfer/delegation broken** | `dig SOA domain` returns wrong nameservers | Fix NS delegation records |
| **Cached NXDOMAIN (negative caching)** | `dig domain @auth-ns` works, `dig domain @resolver` fails | The SOA record's `minimum` TTL defines negative cache time. Wait or flush cache |
| **Case sensitivity** (rare) | `dig Example.com` vs `dig example.com` | DNS is case-insensitive but some appliances aren't |

### Real Scenario

> **"Internal service `api.inventory.prod.internal` resolves for on-call engineer's laptop but not from Kubernetes pods."**
>
> *Root cause:* The engineer's laptop uses the corporate VPN DNS resolver, which has a conditional forwarder for `prod.internal` pointing to the production DNS servers. The Kubernetes pods use CoreDNS in the cluster, which only has a forwarder for `cluster.local` and forwards everything else to Google DNS (8.8.8.8). Google DNS doesn't know about `prod.internal` → NXDOMAIN.
>
> *Diagnosis:*
> ```bash
> # From the engineer's laptop (works):
> dig api.inventory.prod.internal +short
> # 10.12.0.45
>
> # From a pod (fails):
> kubectl exec -it debug-pod -- dig api.inventory.prod.internal
> # status: NXDOMAIN
>
> # Check CoreDNS config:
> kubectl get configmap coredns -n kube-system -o yaml
> # No forwarder for prod.internal!
> ```
>
> *Fix:* Add a CoreDNS forwarder for `prod.internal`:
> ```
> prod.internal:53 {
>     forward . 10.0.0.53 10.0.0.54  # Production DNS servers
> }
> ```

---

## SERVFAIL (RCODE 2)

### Meaning

The nameserver encountered an error while processing the query. Unlike NXDOMAIN, SERVFAIL does NOT mean the domain doesn't exist — it means the nameserver **couldn't determine** whether it exists. This is a "something went wrong" response from the DNS infrastructure.

### Diagnosis

```bash
# Step 1: Identify which nameserver is returning SERVFAIL
dig api.example.com +short
# If no output and status is SERVFAIL, trace the resolution path

# Step 2: Trace resolution from root to authoritative
dig +trace api.example.com

# Expected output (successful trace):
# .            518400  IN  NS  a.root-servers.net.  ← Root nameservers
# com.         172800  IN  NS  a.gtld-servers.net.   ← .com nameservers
# example.com. 172800  IN  NS  ns-123.awsdns-45.com. ← Authoritative
# api.example.com. 60 IN A 10.0.1.42                 ← Answer

# Problem trace example — SERVFAIL at .com level:
# .            518400  IN  NS  a.root-servers.net.    ← OK
# com.         172800  IN  NS  a.gtld-servers.net.     ← OK
# ;; Received packet with invalid opcode from 192.5.6.30  ← SERVFAIL here
# ;; connection timed out; no servers could be reached

# Step 3: Check DNSSEC validation (common SERVFAIL cause)
dig +dnssec api.example.com
# If you see "ad" flag missing or "SERVFAIL" with +dnssec,
# the DNSSEC chain is broken

# Test with DNSSEC validation disabled to isolate the issue
dig +cd api.example.com  # +cd = Checking Disabled (bypass DNSSEC)
# If this works but the normal query fails, DNSSEC is the problem
```

### Common Causes of SERVFAIL

| Cause | Detection | Fix |
|-------|-----------|-----|
| **DNSSEC validation failure** | `dig +cd` works, `dig` fails | Fix DNSSEC chain: check RRSIG, DNSKEY, DS records. Expired signatures are common. |
| **Authoritative nameserver unreachable** | `dig +trace` stops at delegation point | Check the authoritative server: is port 53 open? Firewall? Server crashed? |
| **Lame delegation** | `dig +trace` shows NS that returns non-authoritative answer | Parent zone lists NS, but that NS doesn't serve the zone. Fix NS records. |
| **Resolver memory exhausted** | Large answer (ANY query, DNSSEC chain) | Use specific query types. Increase resolver memory. |
| **CNAME chain too long** | `dig +trace` shows many CNAME hops | Fix CNAME loop or chain. Use A/AAAA records instead of long chains. |
| **Malformed zone data** | Zone transfer shows corrupt records | Fix zone file syntax errors and reload the zone. |

### Real Scenario

> **"All DNS lookups for our domain return SERVFAIL after a DNSSEC key rotation."**
>
> *Root cause:* The team rotated the Zone Signing Key (ZSK) but didn't update the DS record at the parent zone (the registrar). The DNSSEC validation chain is: DNSKEY in child → DS in parent → DNSKEY in child verifies RRSIG over records. With a new ZSK, the DS record at the parent still points to the old key. Resolvers that validate DNSSEC see the RRSIG signed by a key not matching the DS record → SERVFAIL.
>
> *Diagnosis:*
> ```bash
> dig +dnssec api.example.com
> # status: SERVFAIL
>
> dig +cd api.example.com
> # status: NOERROR  ← DNSSEC problem confirmed
>
> # Check the DS record at parent
> dig DS example.com @a.gtld-servers.net
> # Compare with the DNSKEY in the zone
> dig DNSKEY example.com @ns-123.awsdns-45.com
> # Key IDs don't match → rotation incomplete
> ```
>
> *Fix:* Update the DS record at the registrar/parent zone with the new key's hash. Wait for DS propagation (up to TTL of the parent's NS records).

---

## REFUSED (RCODE 5)

### Meaning

The nameserver received the query but its policy forbids answering it. This is not "I don't know" (SERVFAIL) or "it doesn't exist" (NXDOMAIN); it's "I won't tell you."

### Diagnosis

```bash
# Basic check
dig api.example.com @ns1.example.com
# status: REFUSED

# Is the server configured to allow recursion?
dig api.example.com @ns1.example.com +norecurse
# If this works but without +norecurse it fails, recursion is disabled

# Check if it's an ACL issue — try from a different source IP
dig api.example.com @ns1.example.com         # From your current IP
# vs
dig api.example.com @ns1.example.com -b 10.0.1.1  # From a specific IP (if allowed)
```

### Common Causes

| Cause | Detection | Fix |
|-------|-----------|-----|
| **Recursion not allowed** | `dig +norecurse` works, `dig` REFUSED | Allow recursion for trusted clients, or use a different resolver |
| **ACL restriction** | Only certain IPs get REFUSED | Update `allow-query` or `allow-recursion` in named.conf |
| **Rate limiting** | Fraction of queries get REFUSED | `rate-limit` in BIND; increase or remove the limit |
| **Anycast routing** | Some geographic regions get REFUSED | Check anycast health checks and routing policies |

### Real Scenario

> **"New microservice can't resolve internal hostnames — gets REFUSED from corporate DNS."**
>
> *Root cause:* The corporate DNS servers use ACLs: `allow-recursion { 10.0.0.0/8; 172.16.0.0/12; };`. The new microservice is deployed in a new VPC with CIDR `192.168.0.0/16`, which is NOT in the ACL. Every DNS query from the new VPC is REFUSED.
>
> *Diagnosis:*
> ```bash
> dig api.internal.corp.local @10.0.0.53
> # status: REFUSED
>
> # Check the source IP
> curl ifconfig.me
> # 192.168.1.42  ← Not in the ACL range
> ```
>
> *Fix:* Add `192.168.0.0/16` to the corporate DNS `allow-recursion` ACL.

---

## TIMEOUT (No RCODE; Client-side)

### Meaning

The DNS client (stub resolver) never received a response from the nameserver within the configured timeout. This is not a DNS protocol error — the UDP packet was either lost, blocked, or the server never responded.

### Diagnosis

```bash
# Basic timeout detection
dig api.example.com +time=3 +tries=2
# If it says "connection timed out; no servers could be reached"
# then no response was received from any nameserver

# Test if nameserver is reachable at ALL
ping -c 3 8.8.8.8
# If ping works but DNS doesn't, port 53 is blocked

# Test port 53 specifically
nc -zvu 8.8.8.8 53
# -z = scan, -v = verbose, -u = UDP

# Try TCP (fallback for UDP blocking)
dig +tcp api.example.com
# DNS uses TCP for large responses or when UDP is blocked

# Check firewall rules
sudo iptables -L -n -v | grep :53
# Look for DROP or REJECT rules on port 53
```

### Common Causes

| Cause | Detection | Fix |
|-------|-----------|-----|
| **Firewall blocking port 53** | `nc -zvu <ns> 53` fails, ping works | Open UDP 53 (and TCP 53 for large responses) |
| **Nameserver overloaded** | Intermittent timeouts under load | Scale up DNS infrastructure; add caching resolvers |
| **Network path broken** | `traceroute <ns>` shows break | Fix routing |
| **UDP packet too large** | Small queries work, large ones timeout | Use TCP fallback; configure EDNS0 buffer size properly |
| **Nameserver configured for wrong interface** | Port 53 open on 127.0.0.1 but not public IP | Bind to 0.0.0.0 or correct interface |

### Real Scenario

> **"After a firewall rule change, all DNS lookups from the app tier time out."**
>
> *Root cause:* A security team implements an egress firewall rule that only allows outbound traffic to approved IPs. The DNS servers (8.8.8.8, 1.1.1.1) were added to the allowlist, but **only on TCP port 443**. UDP port 53 (DNS) was not in the egress rules. Standard DNS queries use UDP first and only fall back to TCP for truncated responses. The firewall drops UDP 53 packets silently → DNS timeouts.
>
> *Diagnosis:*
> ```bash
> dig api.example.com
> # ;; connection timed out; no servers could be reached
>
> ping 8.8.8.8
> # OK
>
> nc -zvu 8.8.8.8 53
> # No response (UDP dropped)
>
> dig +tcp api.example.com
> # status: NOERROR  ← TCP works!
> ```
>
> *Fix:* Add UDP 53 (and TCP 53) to the egress firewall allowlist for DNS servers.

---

## FORMERR (RCODE 1)

### Meaning

The nameserver could not parse the DNS query. The query was malformed.

### Diagnosis

```bash
# FORMERR is usually caused by EDNS0 issues
# Test with EDNS0 disabled
dig api.example.com +noedns
# If this works but without +noedns it fails, EDNS0 buffer size is the problem

# Test with different EDNS0 buffer sizes
dig api.example.com +bufsize=512    # Small (original DNS limit)
dig api.example.com +bufsize=1232   # EDNS0 default
dig api.example.com +bufsize=4096   # Large

# Check if a specific firewall is mangling EDNS0 packets
dig api.example.com +dnssec         # DNSSEC requires EDNS0
# If this fails specifically, a middlebox is likely the issue
```

### Common Causes

| Cause | Detection | Fix |
|-------|-----------|-----|
| **EDNS0 buffer size too large** | `dig +bufsize=512` works, default fails | Reduce `edns-udp-size` in BIND or adjust firewall |
| **Firewall mangles EDNS0** | FORMERR consistently from specific network paths | Update firewall firmware; bypass with `+tcp` |
| **DNS proxy (dnsmasq) misconfigured** | FORMERR from local resolver but not from upstream | Update dnsmasq config or version |
| **Corrupt DNS library** | All queries from specific client get FORMERR | Reinstall/update DNS client library |

### Real Scenario

> **"DNSSEC-enabled domains fail to resolve from the office network but work from home."**
>
> *Root cause:* The office firewall has an old firmware version that strips EDNS0 options from DNS packets. DNSSEC requires EDNS0 (the DO bit and larger buffer size). When EDNS0 is stripped, the authoritative nameserver receives a malformed query → FORMERR.
>
> *Diagnosis:*
> ```bash
> # From office:
> dig +dnssec cloudflare.com
> # status: FORMERR
>
> # From office, EDNS0 disabled:
> dig +noedns cloudflare.com
> # status: NOERROR (but no DNSSEC validation)
>
> # From home:
> dig +dnssec cloudflare.com
> # status: NOERROR, ad flag present
> ```
>
> *Fix:* Update firewall firmware to support EDNS0.

---

## NOTIMP (RCODE 4)

### Meaning

The nameserver does not support the requested query type.

### Diagnosis

```bash
# Check what query type is being requested
dig api.example.com ANY
# If ANY queries are not supported: NOTIMP

# Most common: CAA record queries to old DNS servers
dig CAA example.com

# Uncommon query types to old servers
dig SSHFP example.com
```

### Common Causes

| Cause | Fix |
|-------|-----|
| ANY query type blocked (common DDoS mitigation) | Query specific types (A, AAAA, MX) instead of ANY |
| Old BIND version not supporting modern record types | Upgrade BIND |
| CAA record query to server without CAA support | Server will return NOERROR with empty answer; NOTIMP means server is very old |

---

## `/etc/resolv.conf` Configuration

The `resolv.conf` file controls how the system's stub resolver behaves.

```bash
# Typical /etc/resolv.conf
nameserver 8.8.8.8          # Primary DNS server
nameserver 8.8.4.4          # Secondary DNS server (used if primary times out)
search corp.example.com example.com  # Search domains for short names
options timeout:2           # Timeout per nameserver (seconds)
options attempts:2          # Number of attempts per nameserver
options rotate              # Round-robin nameservers instead of always using first
options ndots:1             # Minimum dots before trying absolute name first
```

### Key Configuration Options

| Option | Default | Meaning | Tuning Advice |
|--------|---------|---------|---------------|
| `timeout:N` | 5s | Seconds to wait for a response from one nameserver | Reduce to 1-2s for faster failover in microservices |
| `attempts:N` | 2 | Retries per nameserver before trying next | Keep at 2; increasing adds latency |
| `rotate` | Off | Use nameservers in round-robin order | Enable for load distribution |
| `ndots:N` | 1 | Minimum dots in name before trying as absolute | Increase if you use many subdomains (e.g., `ndots:3` for `api.prod.us-east.internal`) |

### Common `resolv.conf` Issues

```bash
# PROBLEM: nameserver unreachable
nameserver 127.0.0.1
# But no local DNS resolver is running
# Fix: install and start dnsmasq, systemd-resolved, or point to external DNS

# PROBLEM: Too many search domains
search corp.example.com prod.example.com staging.example.com dev.example.com test.example.com
# Each query tries every domain combination → latency explosion
# Fix: Limit to 2-3 search domains max

# PROBLEM: IPv6 nameserver without IPv6 connectivity
nameserver 2001:4860:4860::8888
# If the host has no IPv6 route, DNS times out before falling back to IPv4
# Fix: Remove IPv6 nameservers if you don't have IPv6 connectivity
```

---

## Python DNS Troubleshooting Script

```python
#!/usr/bin/env python3
"""
DNS Troubleshooting Script
Diagnoses DNS resolution issues step by step using dnspython.
"""
import sys
import socket
import time
from datetime import datetime
from typing import List, Tuple, Optional

import dns.resolver
import dns.message
import dns.query
import dns.rdatatype
import dns.rcode
import dns.reversename


def check_nameserver_connectivity(ns: str, timeout: float = 3.0) -> Tuple[bool, str]:
    """Check if a nameserver is reachable on port 53."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.connect((ns, 53))
        sock.close()
        return True, "Reachable"
    except socket.timeout:
        return False, "Connection timed out"
    except socket.error as e:
        return False, f"Connection error: {e}"


def resolve_with_trace(domain: str, nameservers: List[str]) -> dict:
    """
    Resolve a domain step by step, like dig +trace.
    Returns detailed results at each level.
    """
    result = {
        "domain": domain,
        "timestamp": datetime.utcnow().isoformat(),
        "steps": [],
        "final_answer": None,
        "error": None,
    }

    # Step 1: Check local nameserver connectivity
    for ns in nameservers:
        reachable, msg = check_nameserver_connectivity(ns)
        result["steps"].append({
            "step": "nameserver_check",
            "nameserver": ns,
            "reachable": reachable,
            "message": msg,
        })
        if not reachable:
            result["error"] = f"Nameserver {ns} is unreachable"
            return result

    # Step 2: Try standard resolution
    try:
        resolver = dns.resolver.Resolver()
        resolver.nameservers = nameservers
        resolver.timeout = 3
        resolver.lifetime = 10

        # Try different query types
        for rdtype in [dns.rdatatype.A, dns.rdatatype.AAAA]:
            try:
                answers = resolver.resolve(domain, rdtype)
                result["final_answer"] = []
                for rdata in answers:
                    result["final_answer"].append(str(rdata))
                result["steps"].append({
                    "step": "resolution",
                    "type": dns.rdatatype.to_text(rdtype),
                    "status": "SUCCESS",
                    "answers": result["final_answer"],
                })
                break
            except dns.resolver.NoAnswer:
                result["steps"].append({
                    "step": "resolution",
                    "type": dns.rdatatype.to_text(rdtype),
                    "status": "NO_ANSWER",
                })
                continue
            except dns.resolver.NXDOMAIN:
                result["steps"].append({
                    "step": "resolution",
                    "type": dns.rdatatype.to_text(rdtype),
                    "status": "NXDOMAIN",
                })
                result["error"] = "NXDOMAIN — domain does not exist"
                break
            except dns.resolver.NoNameservers:
                result["steps"].append({
                    "step": "resolution",
                    "type": dns.rdatatype.to_text(rdtype),
                    "status": "SERVFAIL",
                })
                result["error"] = "SERVFAIL — no nameservers could answer"
                break

    except dns.exception.Timeout:
        result["error"] = "TIMEOUT — no response from any nameserver"
    except Exception as e:
        result["error"] = f"Unexpected error: {e}"

    return result


def test_dnssec(domain: str, nameservers: List[str]) -> dict:
    """Test DNSSEC validation for a domain."""
    result = {
        "domain": domain,
        "dnssec_enabled": False,
        "dnssec_valid": False,
        "details": [],
    }

    resolver = dns.resolver.Resolver()
    resolver.nameservers = nameservers

    try:
        # Request DNSSEC records
        answers = resolver.resolve(domain, dns.rdatatype.A, raise_on_no_answer=False)
        result["dnssec_enabled"] = bool(answers.response.flags & dns.flags.AD)

        # Try to fetch DNSKEY
        try:
            dnskey = resolver.resolve(domain, dns.rdatatype.DNSKEY)
            result["details"].append(f"DNSKEY records: {len(dnskey)} found")
        except Exception as e:
            result["details"].append(f"DNSKEY query failed: {e}")

        # Try to fetch RRSIG
        try:
            rrsig = resolver.resolve(domain, dns.rdatatype.RRSIG, raise_on_no_answer=False)
            result["details"].append(f"RRSIG records: {len(rrsig.rrset) if rrsig.rrset else 0} found")
        except Exception as e:
            result["details"].append(f"RRSIG query failed: {e}")

    except dns.resolver.NXDOMAIN:
        result["details"].append("NXDOMAIN — cannot verify DNSSEC for non-existent domain")
    except dns.exception.Timeout:
        result["details"].append("TIMEOUT — cannot verify DNSSEC")
    except Exception as e:
        result["details"].append(f"Error: {e}")

    return result


def check_reverse_dns(ip: str, nameservers: List[str]) -> dict:
    """Check reverse DNS (PTR) for an IP address."""
    result = {"ip": ip, "hostname": None, "error": None}

    try:
        addr = dns.reversename.from_address(ip)
        resolver = dns.resolver.Resolver()
        resolver.nameservers = nameservers
        answers = resolver.resolve(addr, dns.rdatatype.PTR)
        result["hostname"] = str(answers[0])
    except dns.resolver.NXDOMAIN:
        result["error"] = "No PTR record"
    except Exception as e:
        result["error"] = str(e)

    return result


def diagnose_domain(domain: str, nameservers: Optional[List[str]] = None) -> dict:
    """
    Full DNS diagnosis for a domain.
    Replicates the diagnostic workflow an SRE would perform.
    """
    if nameservers is None:
        # Read nameservers from /etc/resolv.conf
        resolver = dns.resolver.Resolver()
        nameservers = list(resolver.nameservers)

    diagnosis = {
        "domain": domain,
        "nameservers": nameservers,
        "timestamp": datetime.utcnow().isoformat(),
        "connectivity": {},
        "resolution": {},
        "dnssec": {},
        "soa": {},
    }

    # 1. Check nameserver connectivity
    for ns in nameservers:
        reachable, msg = check_nameserver_connectivity(ns)
        diagnosis["connectivity"][ns] = {"reachable": reachable, "message": msg}

    # 2. Standard resolution
    diagnosis["resolution"] = resolve_with_trace(domain, nameservers)

    # 3. DNSSEC check
    domain_parts = domain.split(".")
    zone = ".".join(domain_parts[-2:]) if len(domain_parts) >= 2 else domain
    diagnosis["dnssec"] = test_dnssec(zone, nameservers)

    # 4. SOA check
    try:
        resolver = dns.resolver.Resolver()
        resolver.nameservers = nameservers
        answers = resolver.resolve(zone, dns.rdatatype.SOA)
        for rdata in answers:
            diagnosis["soa"] = {
                "mname": str(rdata.mname),
                "rname": str(rdata.rname),
                "serial": rdata.serial,
                "refresh": rdata.refresh,
                "retry": rdata.retry,
                "expire": rdata.expire,
                "minimum": rdata.minimum,
            }
    except Exception as e:
        diagnosis["soa"]["error"] = str(e)

    return diagnosis


# ============================================================
# CLI Interface
# ============================================================
if __name__ == "__main__":
    import json

    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <domain> [nameserver ...]")
        print(f"Example: {sys.argv[0]} api.example.com 8.8.8.8 1.1.1.1")
        sys.exit(1)

    domain = sys.argv[1]
    nameservers = sys.argv[2:] if len(sys.argv) > 2 else None

    print(f"DNS Diagnosis for: {domain}")
    print(f"Nameservers: {nameservers if nameservers else '(from /etc/resolv.conf)'}")
    print("-" * 60)

    result = diagnose_domain(domain, nameservers)
    print(json.dumps(result, indent=2, default=str))
```

---

## Using `dig +trace` to Find Where Resolution Breaks

The `+trace` flag makes `dig` perform iterative resolution from the root, showing each step. This is the single most valuable DNS debugging command.

```bash
dig +trace api.example.com
```

### Reading the Output

```
# Step 1: Root nameservers
.                       518400  IN  NS  a.root-servers.net.
.                       518400  IN  NS  b.root-servers.net.
;; Received 228 bytes from 8.8.8.8#53(8.8.8.8) in 12 ms
# ↑ Root nameservers returned successfully

# Step 2: TLD nameservers
com.                    172800  IN  NS  a.gtld-servers.net.
com.                    172800  IN  NS  b.gtld-servers.net.
;; Received 1174 bytes from 198.41.0.4#53(a.root-servers.net) in 24 ms
# ↑ com. nameservers returned successfully

# Step 3: Authoritative nameservers for example.com
example.com.            172800  IN  NS  ns-123.awsdns-45.com.
example.com.            172800  IN  NS  ns-456.awsdns-78.net.
;; Received 237 bytes from 192.5.6.30#53(a.gtld-servers.net) in 68 ms
# ↑ Delegation returned successfully

# Step 4: The answer
api.example.com.        60      IN  A    10.0.1.42
;; Received 62 bytes from 205.251.192.123#53(ns-123.awsdns-45.com) in 15 ms
# ↑ Authoritative answer received
```

### Common Failure Signatures

```
# FAILURE AT ROOT: Can't reach any root servers
# Means: No internet connectivity, or UDP 53 blocked globally
# Fix: Check basic network connectivity

# FAILURE AT TLD: Root works, TLD unreachable
# Means: .com/.net/.org servers blocked or down
# Fix: Usually a firewall issue. Check if any TLD servers respond.

# FAILURE AT AUTHORITATIVE: Delegation works, auth server unreachable
# Means: The domain's own nameservers are down
# Fix: Check if ns-123.awsdns-45.com is reachable. Check Route53 / DNS provider.

# SERVFAIL AT AUTHORITATIVE: Delegation works, auth server responds with error
# Means: Zone file problem, DNSSEC issue, or server misconfiguration
# Fix: Check zone file syntax, DNSSEC chain, server logs.
```

---

## Monitoring Recommendations

### Prometheus Blackbox Exporter — DNS Probe

```yaml
modules:
  dns_tcp:
    prober: dns
    dns:
      transport_protocol: tcp
      query_name: "api.example.com"
      query_type: "A"
      validate_answer_rrs:
        fail_if_matches_regexp:
          - ".*10\\.0\\.0\\..*"  # Fail if it resolves to a blocked IP

  dns_udp:
    prober: dns
    dns:
      transport_protocol: udp
      query_name: "api.example.com"
      query_type: "A"
      validate_answer_rrs:
        fail_if_not_matches_regexp:
          - ".*"               # Must have any answer
```

### Alert Thresholds

| Metric | Warning | Critical | Window |
|--------|---------|----------|--------|
| DNS failure rate | > 1% of queries | > 5% of queries | 5 min |
| DNS resolution time | p99 > 500ms | p99 > 2s | 5 min |
| SERVFAIL rate | > 0.1% | > 1% | 1 min |
| NXDOMAIN on known domains | ANY | > 5/min | 1 min |
| Nameserver unreachable | ANY | ANY | Immediate |

### Checklist for DNS Incidents

1. `dig +trace <domain>` — find where the chain breaks
2. Check all nameservers individually (`dig @ns1`, `dig @ns2`)
3. Check for recent DNS changes (zone file edits, Route53 changes, Terraform apply)
4. Check DNSSEC (`dig +dnssec`, `dig +cd`)
5. Check `/etc/resolv.conf` for correct nameservers
6. Check firewall rules for UDP/TCP port 53
7. Check DNS server health (CPU, memory, query rate)
8. Flush local DNS cache (`sudo systemd-resolve --flush-caches` or `sudo killall -HUP mDNSResponder`)

---

*Return to [07 Error Codes Home](../README.md)*
