# DNS Troubleshooting

> **Category:** Networking | DNS
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#dns` `#networking` `#oncall`

---

## Table of Contents

1. [The DNS Resolution Chain](#the-dns-resolution-chain)
2. [dig Deep Dive](#dig-deep-dive)
3. [dig vs nslookup vs host](#dig-vs-nslookup-vs-host)
4. [DNS Propagation and TTL](#dns-propagation-and-ttl)
5. [Split-Horizon DNS](#split-horizon-dns)
6. [/etc/resolv.conf and /etc/nsswitch.conf](#etcresolvconf-and-etcnsswitchconf)
7. [DNS over TCP vs UDP](#dns-over-tcp-vs-udp)
8. [DNSSEC Validation Failures](#dnssec-validation-failures)
9. [Scenario: App Can't Resolve Internal Service](#scenario-app-cant-resolve-internal-service)
10. [Python DNS Resolution with Fallback](#python-dns-resolution-with-fallback)
11. [Java DNS Debugging](#java-dns-debugging)
12. [Kubernetes DNS Specifics](#kubernetes-dns-specifics)

---

## The DNS Resolution Chain

Every DNS query goes through a well-defined path. Understanding each step is critical when debugging why a name doesn't resolve.

```
Application
    │
    ├─ 1. Check /etc/nsswitch.conf (hosts: files dns → files first)
    ├─ 2. Check /etc/hosts
    ├─ 3. Check local resolver cache (systemd-resolved, dnsmasq, nscd)
    ├─ 4. Query configured nameserver from /etc/resolv.conf
    │       │
    │       ├─ Forwarding resolver (e.g., Route 53 Resolver, CoreDNS, Unbound)
    │       │       │
    │       │       ├─ Cache hit → return immediately
    │       │       ├─ Cache miss → recursive resolution:
    │       │               │
    │       │               ├─ Root hints → root servers (a.root-servers.net)
    │       │               ├─ Root refers to TLD servers (.com, .io, .internal)
    │       │               ├─ TLD refers to authoritative nameservers (ns-123.awsdns-45.com)
    │       │               └─ Authoritative returns the answer
    │       │
    │       └─ Returns response to client
    │
    └─ App receives IP address
```

---

## dig Deep Dive

**dig** is the definitive DNS debugging tool. It bypasses nsswitch and hosts files and speaks DNS protocol directly.

### Anatomy of dig Output

```bash
dig example.com
```

```text
; <<>> DiG 9.18.18 <<>> example.com       ← dig version, query args
;; global options: +cmd
;; Got answer:
;; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 12345
;; flags: qr rd ra; QUERY: 1, ANSWER: 1, AUTHORITY: 0, ADDITIONAL: 1

;; OPT PSEUDOSECTION:                      ← EDNS0 extension
; EDNS: version: 0, flags:; udp: 1232

;; QUESTION SECTION:                       ← What was asked
;example.com.           IN      A

;; ANSWER SECTION:                         ← The actual answer
example.com.    3600    IN      A       93.184.216.34

;; AUTHORITY SECTION:                      ← NS records for the zone (if any)
example.com.    86400   IN      NS      a.iana-servers.net.

;; ADDITIONAL SECTION:                     ← Glue records (IPs of NS servers)
a.iana-servers.net. 86400 IN    A       199.43.135.53

;; Query time: 12 msec                    ← RTT to the nameserver
;; SERVER: 8.8.8.8#53(8.8.8.8) (UDP)    ← Which resolver answered
;; WHEN: Wed Jun 11 10:15:00 UTC 2026
;; MSG SIZE  rcvd: 76
```

**Key fields to understand:**

| Section | Field | Meaning |
|---------|-------|---------|
| HEADER | `status: NOERROR` | Query succeeded (NXDOMAIN = no such domain, SERVFAIL = server failure, REFUSED = server refused) |
| HEADER | `flags: qr rd ra` | qr=response, rd=recursion desired, ra=recursion available |
| HEADER | `flags: aa` | Authoritative Answer — the nameserver is authoritative for this zone (missing means cached/forwarded) |
| ANSWER | TTL `3600` | Time-to-live in seconds. Record can be cached for 1 hour. After this, must re-query. |
| ANSWER | `IN A` | Internet class, A record (IPv4). AAAA = IPv6, CNAME = alias, MX = mail, TXT = text. |

### Essential dig Invocations

```bash
# Basic lookup (use +short for minimal output)
dig +short example.com

# Query a specific resolver (bypass local resolver)
dig @8.8.8.8 example.com

# Query a specific resolver on a non-default port
dig @10.0.0.53 -p 5353 example.com

# Trace the full resolution chain from root
dig +trace example.com

# Show all record types
dig example.com ANY

# Query specific record types
dig example.com AAAA
dig example.com MX
dig example.com TXT
dig example.com CNAME
dig example.com NS
dig example.com SOA

# Reverse DNS lookup (PTR record)
dig -x 93.184.216.34

# Show only the answer section (no headers/comments)
dig example.com +noall +answer

# Show authoritative nameservers
dig example.com NS +short

# Show DNSSEC records
dig +dnssec example.com

# Force TCP (bypass UDP)
dig +tcp example.com

# Set custom EDNS0 buffer size
dig +bufsize=4096 example.com

# Check DNSSEC chain of trust
dig +dnssec +multi example.com
```

### Understanding dig +trace

```bash
dig +trace example.com
```

```text
# Step 1: Query root servers for .com nameservers
.                       3600  IN  NS  a.root-servers.net.
.                       3600  IN  NS  b.root-servers.net.
;; Received 811 bytes from 8.8.8.8#53 in 12 ms

# Step 2: Query .com TLD servers for example.com nameservers
example.com.            172800 IN NS  a.iana-servers.net.
example.com.            172800 IN NS  b.iana-servers.net.
;; Received 95 bytes from 192.5.6.30#53 in 45 ms

# Step 3: Query authoritative nameservers for example.com A record
example.com.            3600  IN  A   93.184.216.34
;; Received 60 bytes from 199.43.135.53#53 in 80 ms
```

**What to look for in +trace output:**
- Does the root step work? If not, no internet connectivity or root hints wrong.
- Does the TLD step work? If not, specific TLD might be blocked.
- Does the authoritative step work? If not, the authoritative NS might be down or unreachable.
- Are there delegation gaps? A missing glue record can cause resolution failure.

---

## dig vs nslookup vs host

| Feature | dig | nslookup | host |
|---------|-----|----------|------|
| **Status** | Standard, maintained | Deprecated (ISC says "use dig") | Simple, maintained |
| **Scripting** | Excellent (+short, +noall, etc.) | Poor (interactive mode) | Decent |
| **Output detail** | Full DNS response decoded | Minimal by default, verbose with `-debug` | Minimal |
| **DNSSEC support** | Full (+dnssec flag) | None | None |
| **Trace mode** | `+trace` built-in | Manual `set type=ns` then iterate | None |
| **Non-interactive** | Yes (default) | Yes with `-query=type` | Yes (default) |
| **Use case** | Always. This is the standard. | Quick sanity check, interactive exploration | Fastest single-record lookup |

```bash
# dig — the standard for SREs
dig example.com

# nslookup — deprecated but still widely installed
nslookup example.com
nslookup example.com 8.8.8.8

# host — minimal output, good for scripts
host example.com
host example.com 8.8.8.8
host -t MX example.com
```

**Verdict**: Use `dig` for all serious debugging. `host` is acceptable for quick checks. Avoid `nslookup` in scripts — it's unreliable and may have different output formats across systems.

---

## DNS Propagation and TTL

### The TTL Problem

When you change a DNS record, the old value may be cached for up to TTL seconds at every resolver between you and the authoritative server.

```text
Authoritative:     A record changed from 10.0.0.1 → 10.0.0.2, TTL=3600
     │
     ├─ ISP resolver (Comcast): cached 10.0.0.1, expires in 3400s
     ├─ Google 8.8.8.8:         cached 10.0.0.1, expires in 2800s
     ├─ Corporate resolver:      cached 10.0.0.1, expires in 1800s
     └─ Local systemd-resolved:  cached 10.0.0.1, expires in 1200s
```

**Every resolver in this chain independently caches the record.** Your change takes effect at each resolver only when ITS cache expires.

### Checking Current TTL

```bash
# Check TTL on the authoritative nameserver directly
dig example.com @ns1.example.com +noall +answer

# Check TTL from different public resolvers
dig example.com @8.8.8.8 +noall +answer
dig example.com @1.1.1.1 +noall +answer
```

The TTL shown is the **remaining** TTL on that specific resolver. Subtract from the original TTL to see how long ago it was cached.

### Real-World Scenario: Stale DNS After Change

```text
SYMPTOM: "We changed the A record 30 minutes ago but traffic is
          still going to the old server."

INVESTIGATION:
$ dig api.example.com +noall +answer
api.example.com.  3100 IN A 10.0.0.1    ← TTL 3100s remaining
                                          (original was 3600, cached 500s ago)

$ dig api.example.com @ns1.example.com +noall +answer
api.example.com.  3600 IN A 10.0.0.2    ← Authoritative has new IP

ROOT CAUSE: TTL was set to 3600 seconds. The authoritative server has
the new record, but every caching resolver between the client and the
authoritative server still has the old record with up to 3100s remaining.

RESOLUTION:
  1. Lower TTL to 60s BEFORE planned DNS changes (ideally 24h before)
  2. Wait for old TTL to expire everywhere
  3. Make the change
  4. Verify from multiple locations

SHORT-TERM (if urgent):
  - Flush caches you control (systemd-resolved, CoreDNS)
  - Point clients directly at authoritative NS by overriding resolv.conf
  - Use /etc/hosts as temporary override
```

### TTL Best Practices

| Record Type | Before Change | After Change (stable) |
|-------------|---------------|----------------------|
| A/AAAA (critical) | 60s | 300s |
| A/AAAA (non-critical) | 300s | 3600s |
| CNAME | 60s | 300s |
| MX | 300s | 3600s |
| NS | 86400s | 86400s (change very rarely) |
| SOA | 900s | 900s |

---

## Split-Horizon DNS

Split-horizon (also called split-view or split-brain DNS) returns **different answers** for the same query depending on **who is asking** (source IP / network).

### How It Works

```text
              ┌───────────────────────────┐
              │  Corporate DNS Server     │
              │                           │
Query from    │  Internal view:           │
10.0.0.0/8    │  api.company.com → 10.0.5.10 (private IP)    │
─────────────>│                           │
              │                           │
Query from    │  External view:           │
public IP     │  api.company.com → 52.10.5.10 (public IP)    │
─────────────>│                           │
              └───────────────────────────┘
```

### Real-World Scenario: "Works from Office, Broken from Home"

```text
SYMPTOM: Employee reports that internal API works from the office
         but returns "connection refused" from home VPN.

INVESTIGATION:
$ # At office:
$ dig api.internal.company.com +short
10.0.5.10                          ← Private IP, accessible on corporate network

$ # From home:
$ dig api.internal.company.com +short
52.10.5.10                         ← Public IP, but service only listens on private IP

$ # Or worse:
$ dig api.internal.company.com +short
(NXDOMAIN)                         ← Name doesn't exist in public DNS at all

ROOT CAUSE: Split-horizon DNS. The internal view returns a private IP
that's only reachable from the corporate network. The external view
doesn't have this record (or returns a public IP without a listener).
The VPN doesn't route DNS queries to the internal resolver.

RESOLUTION:
  1. Configure VPN to use the internal DNS resolver for company domains
  2. Or create a public DNS record pointing to the VPN-routable private IP
  3. Or use DNS search domains pushed by VPN client
```

### Detecting Split-Horizon

```bash
# Query from different source networks (or use different resolvers)
dig api.company.com @internal-dns.company.com +short
dig api.company.com @8.8.8.8 +short

# If answers differ, split-horizon is in play
```

---

## /etc/resolv.conf and /etc/nsswitch.conf

### /etc/resolv.conf

```bash
# Example resolv.conf on a corporate VM
nameserver 10.0.0.2
nameserver 10.0.1.2
search corp.company.com us-east-1.compute.internal
options timeout:2 attempts:3 ndots:5
```

| Directive | Meaning | Impact |
|-----------|---------|--------|
| `nameserver` | DNS server IP (max 3, tried in order) | Order matters — first responder gets all queries if it answers |
| `search` | Domain suffix list for short names | Appending these domains creates extra queries |
| `options timeout:N` | Seconds to wait for reply (default 5) | Lower for faster failover, but might cause false timeouts |
| `options attempts:N` | Retries per nameserver (default 2) | More retries = more resilient but slower |
| `options ndots:N` | Names with <N dots are tried with search domains first | **The Kubernetes DNS killer** — see below |

### The ndots Problem in Kubernetes

```text
MEAN TIME TO RESOLUTION WITH ndots:5:

Query: "statsd" (a single-label, 0 dots < ndots:5)
  → Try statsd.corp.company.com.          (NXDOMAIN)
  → Try statsd.us-east-1.compute.internal. (NXDOMAIN)
  → Try statsd.svc.cluster.local.          (SUCCESS)
  → 3 DNS queries, each with serial timeouts = 300-600ms total

Query: "statsd.namespace" (2 dots < ndots:5)
  → Try statsd.namespace.corp.company.com.           (NXDOMAIN)
  → Try statsd.namespace.us-east-1.compute.internal.  (NXDOMAIN)
  → Try statsd.namespace.svc.cluster.local.           (SUCCESS)
  → 3 DNS queries again

Query: "statsd.namespace.svc.cluster.local." (5 dots + trailing dot = FQDN)
  → Query directly, no search domain appended
  → 1 DNS query = ~10ms
```

**Impact**: With `ndots:5`, every single-label Kubernetes service name triggers 3 extra DNS queries. With 100 services, that's 300 extra queries per lookup cycle. This is why Kubernetes DNS can feel "slow."

**Fix**:
```bash
# Option 1: Change ndots to 1 (one dot triggers direct lookup)
options ndots:1

# Option 2: Always use FQDN with trailing dot in apps
# Instead of:   http://statsd:8125
# Use:          http://statsd.namespace.svc.cluster.local.:8125

# Option 3: Add svc.cluster.local as the FIRST search domain
search svc.cluster.local corp.company.com us-east-1.compute.internal
# This ensures k8s names resolve on the first attempt
```

### /etc/nsswitch.conf

Controls the order of name resolution sources:

```bash
# Default on most Linux systems
hosts:          files dns
networks:       files
```

| Entry | Meaning |
|-------|---------|
| `hosts: files dns` | Check /etc/hosts first, then DNS |
| `hosts: dns files` | DNS first, then /etc/hosts |
| `hosts: files mdns4_minimal [NOTFOUND=return] dns` | macOS-style: files, mDNS, then DNS |

**Debugging nsswitch**:
```bash
# Check what order your system uses
cat /etc/nsswitch.conf | grep hosts

# Check if /etc/hosts is overriding DNS
grep "my-service" /etc/hosts

# Use getent to see the full resolution path
getent hosts my-service.internal
getent ahosts my-service.internal     # Shows all resolved addresses
```

---

## DNS over TCP vs UDP

### Default Behavior

```text
Normal DNS query (UDP):
  Client: UDP packet ≤ 512 bytes → Server
  Server: UDP response ≤ 512 bytes → Client
  ✅ Fast, connectionless, low overhead

Oversized response (UDP truncation → TCP fallback):
  Client: UDP query → Server
  Server: UDP response with TC (Truncated) flag set
  Client: Falls back to TCP
  Client: TCP SYN → Server
  Server: TCP SYN-ACK → Client
  Client: TCP query → Server (over established TCP connection)
  Server: TCP response → Client (full response, up to 65535 bytes)
```

### EDNS0 Extension

EDNS0 (Extension Mechanisms for DNS, RFC 6891) allows UDP DNS responses up to 4096 bytes, eliminating most truncation scenarios:

```bash
# EDNS0 is enabled by default in modern resolvers
# Check if your resolver supports EDNS0
dig +short rs.dns-oarc.net TXT

# Force a specific buffer size
dig +bufsize=512 example.com      # Force legacy 512-byte limit
dig +bufsize=4096 example.com     # Force EDNS0 maximum
```

### Real-World Scenario: DNSSEC + Firewall = Broken DNS

```text
SYMPTOM: DNS resolution works for some domains but not others.
         No error messages — just hangs and timeouts.

INVESTIGATION:
$ dig example.com
;; connection timed out; no servers could be reached

$ dig +tcp example.com
;; connection timed out; no servers could be reached

$ dig +notcp example.com    (force UDP only)
;; Truncated, retrying in TCP mode
;; connection timed out; no servers could be reached

$ nc -vz 8.8.8.8 53
Connection to 8.8.8.8 53 port [udp/domain] succeeded!

$ nc -vz 8.8.8.8 53    (TCP)
nc: connect to 8.8.8.8 port 53 (tcp) failed: Operation timed out

ROOT CAUSE: DNSSEC-signed domains (like example.com) produce responses
>512 bytes. The resolver sets the TC (truncated) flag and the client
falls back to TCP. But outbound TCP port 53 is blocked by a firewall
rule that was added during a security hardening exercise.

The firewall only allowed UDP 53, assuming "DNS is UDP."
But DNS-over-TCP is RFC-mandatory and critical for DNSSEC,
zone transfers, and large responses.

RESOLUTION:
  - Add firewall rule allowing TCP 53 outbound to DNS resolvers
  - Or configure resolver to use EDNS0 with larger buffer to minimize
    truncation (but TCP fallback is still required for >4096 byte responses)
```

---

## DNSSEC Validation Failures

DNSSEC adds cryptographic signatures to DNS records to prevent spoofing.

### Checking DNSSEC Status

```bash
# dig with DNSSEC
dig +dnssec +multi example.com

# Look for the "ad" flag in the header
# ad = Authenticated Data — the resolver validated the signatures
```

```text
;; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 54321
;; flags: qr rd ra ad; QUERY: 1, ANSWER: 1
                  ^^
                  ad flag = DNSSEC validation successful
```

| Flag | Meaning |
|------|---------|
| `ad` | Authenticated Data — DNSSEC validation succeeded |
| `cd` | Checking Disabled — client asked resolver NOT to validate |
| `do` | DNSSEC OK — client supports DNSSEC (EDNS0 flag) |

### DNSSEC Failure Diagnosis

```bash
# If DNSSEC validation failed, you'll see SERVFAIL
dig +dnssec example.com
# status: SERVFAIL

# Trace the chain of trust
dig +dnssec +trace example.com

# Check the DS record in the parent zone
dig DS example.com

# Check RRSIG records (signatures)
dig +dnssec example.com A

# Verify the DNSKEY records
dig +dnssec example.com DNSKEY
```

**Common DNSSEC failure modes:**

1. **Expired RRSIG signatures** — zone not re-signed in time
2. **Missing DS record in parent** — chain of trust broken between TLD and domain
3. **Wrong system clock** — if client clock is off by more than the signature validity window, ALL DNSSEC validation fails
4. **Broken DNSSEC implementation** — some resolvers have bugs, try a different one

```bash
# DNSSEC signature clock sensitivity
# Check system clock — if off by > few hours, DNSSEC fails globally
timedatectl
date -u

# If clock is wrong, fix NTP first
sudo ntpdate -s time.nist.gov
```

---

## Scenario: App Can't Resolve Internal Service

### The Full 10-Step Diagnostic

```text
SITUATION: "Payment service can't resolve inventory.internal — getting
            UnknownHostException in Java app logs."

This is the canonical internal DNS breakdown. Follow these steps
in order. Do not skip steps — each one validates a different layer.
```

#### Step 1: Query Internal DNS Directly

```bash
dig inventory.internal @10.0.0.2

# What to check:
#   - Did it return an answer?
#   - status: NOERROR or NXDOMAIN?
#   - Is the IP in the expected range?
#   - Is there an AUTHORITY section with the correct NS records?
```

#### Step 2: Check Nameserver Configuration

```bash
cat /etc/resolv.conf

# Verify:
#   - Is the internal DNS IP listed as a nameserver?
#   - Is it BEFORE any external resolvers?
#   - Are the search domains correct?
#   - Is ndots reasonable? (ndots:1 is best for Kubernetes)
```

#### Step 3: Check Resolution Order

```bash
cat /etc/nsswitch.conf | grep hosts
# hosts: files dns    → check /etc/hosts first

cat /etc/hosts | grep inventory
# Any static override? Remove if stale.
```

#### Step 4: Find Where the Chain Breaks

```bash
dig inventory.internal +trace

# Where does it stop?
#   - Stops at root → TLD can't be reached
#   - Stops at TLD → internal domain not delegated or TLD unreachable
#   - Stops at NS → authoritative NS unreachable
#   - Returns empty ANSWER → record doesn't exist on auth NS
```

#### Step 5: Cross-Check with Alternative Tool

```bash
nslookup inventory.internal 10.0.0.2
host inventory.internal 10.0.0.2
```

#### Step 6: Network Reachability to DNS Server

```bash
ping -c 3 10.0.0.2
# If ICMP is blocked, don't worry — try TCP/UDP next

mtr -r -c 5 10.0.0.2
# Where does the packet stop? Routing issue?
```

#### Step 7: UDP Port Reachability

```bash
nc -vz -u 10.0.0.2 53
# UDP is connectionless — nc may report "succeeded" even if server is down
# Better test: send an actual DNS query
dig @10.0.0.2 google.com +time=2 +tries=1
```

#### Step 8: TCP Port Reachability

```bash
nc -vz 10.0.0.2 53
# TCP must work for DNSSEC, large responses, zone transfers
# If TCP fails but UDP succeeds: truncated responses will fail
```

#### Step 9: Check Firewall Rules / Security Groups

```text
AWS:
  - Security group on the DNS server (or Route 53 Resolver endpoint):
    inbound UDP 53, TCP 53 from app subnet
  - Network ACL on the subnet: allow ephemeral ports outbound (1024-65535)

Kubernetes:
  - NetworkPolicy allowing egress to kube-dns on port 53?
  - CoreDNS pods running? kubectl get pods -n kube-system | grep coredns

On-prem:
  - iptables: sudo iptables -L -n -v | grep 53
  - Firewall: sudo ufw status
```

#### Step 10: Check AWS Route 53 Private Hosted Zone (if applicable)

```bash
aws route53 list-hosted-zones --query "HostedZones[?Config.PrivateZone==\`true\`]"
# Is the zone associated with this VPC?

aws route53 list-resource-record-sets --hosted-zone-id Z1234567890 \
  --query "ResourceRecordSets[?Name=='inventory.internal.']"
# Does the record exist?

aws route53 get-hosted-zone --id Z1234567890 \
  --query "VPCs[?VPCId=='vpc-xxx']"
# Is the private hosted zone associated with the app's VPC?
```

---

## Python DNS Resolution with Fallback

```python
#!/usr/bin/env python3
"""
Production DNS resolver with fallback, retry, and full error handling.
Supports DNSSEC validation via dnspython.
"""

import dns.resolver
import dns.exception
import dns.rdatatype
import dns.message
import time
import logging
from typing import List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

DNS_SERVERS_PRIMARY = ['10.0.0.2', '10.0.1.2']     # Internal resolvers
DNS_SERVERS_FALLBACK = ['8.8.8.8', '1.1.1.1']       # External fallback
DEFAULT_TIMEOUT = 3.0         # seconds per attempt
DEFAULT_LIFETIME = 10.0       # total time for entire resolution
MAX_RETRIES = 3


def resolve_with_fallback(
    hostname: str,
    record_type: str = 'A',
    nameservers: Optional[List[str]] = None,
    timeout: float = DEFAULT_TIMEOUT,
    lifetime: float = DEFAULT_LIFETIME
) -> Tuple[bool, str, List[str]]:
    """
    Resolve a hostname with primary + fallback DNS servers.

    Returns:
        (success: bool, source: str, addresses: List[str])
        source indicates which resolver pool answered.
    """
    all_servers = (nameservers or []) + DNS_SERVERS_PRIMARY + DNS_SERVERS_FALLBACK

    resolver = dns.resolver.Resolver(configure=False)
    resolver.timeout = timeout
    resolver.lifetime = lifetime

    rdtype = dns.rdatatype.from_text(record_type)

    for attempt in range(MAX_RETRIES):
        for ns in all_servers:
            resolver.nameservers = [ns]
            resolver_source = f"nameserver={ns}, attempt={attempt + 1}"

            try:
                answers = resolver.resolve(hostname, rdtype, raise_on_no_answer=False)
                addresses = [str(rdata) for rdata in answers]

                if addresses:
                    logger.info(f"Resolved {hostname} via {ns}: {addresses}")
                    return (True, resolver_source, addresses)

                logger.warning(f"No answer for {hostname} from {ns} (empty response)")

            except dns.resolver.NXDOMAIN:
                # Name does not exist — no point retrying other servers
                logger.error(f"NXDOMAIN: {hostname} does not exist (queried {ns})")
                return (False, f"NXDOMAIN from {ns}", [])
            except dns.resolver.NoAnswer:
                logger.warning(f"NoAnswer: {hostname} has no {record_type} records (from {ns})")
                continue
            except dns.resolver.NoNameservers:
                logger.error(f"NoNameservers: Could not reach {ns} for {hostname}")
                continue
            except dns.resolver.Timeout:
                logger.error(f"Timeout: {ns} did not respond for {hostname} in {timeout}s")
                continue
            except dns.exception.DNSException as e:
                logger.error(f"DNS error querying {ns} for {hostname}: {type(e).__name__}: {e}")
                continue

        # All servers exhausted in this attempt; sleep before retry
        if attempt < MAX_RETRIES - 1:
            backoff = 2 ** attempt
            logger.warning(f"All nameservers exhausted. Retrying in {backoff}s...")
            time.sleep(backoff)

    return (False, "all_servers_exhausted", [])


def check_dnssec(hostname: str, nameserver: str = '8.8.8.8') -> bool:
    """
    Verify DNSSEC chain of trust for a domain.
    Returns True if the 'ad' (authenticated data) flag is set.
    """
    try:
        import dns.query
        import dns.flags

        query = dns.message.make_query(hostname, dns.rdatatype.A)
        query.want_dnssec(True)

        response = dns.query.udp(query, nameserver, timeout=3.0)

        if response.flags & dns.flags.AD:
            logger.info(f"DNSSEC validation SUCCESS for {hostname}")
            return True
        else:
            logger.warning(f"DNSSEC validation FAILED for {hostname} (no AD flag)")
            return False

    except Exception as e:
        logger.error(f"DNSSEC check failed: {e}")
        return False


# ── Usage Example ────────────────────────────────────────────────

if __name__ == '__main__':
    # Standard resolution
    success, source, addrs = resolve_with_fallback('inventory.internal')
    if success:
        print(f"✓ {addrs} (from {source})")
    else:
        print(f"✗ Resolution failed: {source}")

    # DNSSEC check
    is_secure = check_dnssec('cloudflare.com')
    print(f"DNSSEC valid: {is_secure}")

    # CNAME resolution
    success, _, addrs = resolve_with_fallback('api.example.com', record_type='CNAME')
    print(f"CNAME: {addrs}")
```

---

## Java DNS Debugging

### The Java DNS Stack

```text
Java Application
    │
    ├─ InetAddress.getByName("inventory.internal")
    ├─ java.net.InetAddress uses InetAddressImpl
    ├─ Native getaddrinfo() call (libc resolver)
    └─ /etc/resolv.conf, /etc/nsswitch.conf, /etc/hosts
```

Java delegates DNS resolution to the operating system by default. All the standard `/etc/resolv.conf` debugging applies.

### Java DNS Caching

Java maintains its **own** DNS cache, independent of the OS cache:

```bash
# JVM DNS cache defaults:
#   - Successful lookups: cached forever (until JVM restart)
#   - Failed lookups: cached for 10 seconds

# Override in java.security or via system properties
# $JAVA_HOME/conf/security/java.security:
#   networkaddress.cache.ttl=60         # seconds
#   networkaddress.cache.negative.ttl=10

# Or at runtime:
#   -Dsun.net.inetaddr.ttl=60
#   -Dsun.net.inetaddr.negative.ttl=10
```

**Real-world scenario**: Rolling DNS change from old to new IP. App keeps connecting to old IP because Java cached the first resolution indefinitely.

```bash
# Fix: Set TTL in your container/docker entrypoint:
java -Dsun.net.inetaddr.ttl=60 -jar app.jar

# Or for Kubernetes, set in JAVA_TOOL_OPTIONS:
env:
  - name: JAVA_TOOL_OPTIONS
    value: "-Dsun.net.inetaddr.ttl=30"
```

### Java Custom DNS Resolution with Timeouts

```java
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.UnknownHostException;
import java.time.Duration;
import java.util.concurrent.*;

public class DnsDebugger {

    private static final Duration DNS_TIMEOUT = Duration.ofSeconds(3);

    /**
     * Resolve a hostname with a timeout.
     * Java's default InetAddress.getByName() has no timeout and
     * blocks on the OS resolver indefinitely.
     */
    public static InetAddress resolveWithTimeout(String hostname)
            throws UnknownHostException, TimeoutException {

        ExecutorService executor = Executors.newSingleThreadExecutor();
        Future<InetAddress> future = executor.submit(() ->
            InetAddress.getByName(hostname)
        );

        try {
            return future.get(DNS_TIMEOUT.toMillis(), TimeUnit.MILLISECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new RuntimeException("DNS resolution interrupted", e);
        } catch (ExecutionException e) {
            Throwable cause = e.getCause();
            if (cause instanceof UnknownHostException) {
                throw (UnknownHostException) cause;
            }
            throw new RuntimeException("DNS resolution failed", cause);
        } catch (java.util.concurrent.TimeoutException e) {
            future.cancel(true);
            throw new TimeoutException(
                "DNS resolution for " + hostname + " timed out after " +
                DNS_TIMEOUT.toMillis() + "ms"
            );
        } finally {
            executor.shutdownNow();
        }
    }

    /**
     * Full diagnostic — resolves and prints all addresses,
     * plus whether the host is reachable.
     */
    public static void diagnoseDns(String hostname) {
        System.out.println("=== DNS Diagnostics: " + hostname + " ===");

        try {
            InetAddress addr = resolveWithTimeout(hostname);
            System.out.println("Hostname:     " + addr.getHostName());
            System.out.println("Canonical:    " + addr.getCanonicalHostName());
            System.out.println("Address:      " + addr.getHostAddress());

            // Get ALL addresses (round-robin DNS)
            InetAddress[] all = InetAddress.getAllByName(hostname);
            System.out.println("All addresses:");
            for (InetAddress a : all) {
                boolean reachable = a.isReachable(2000);
                System.out.printf("  %-20s  reachable=%b%n",
                    a.getHostAddress(), reachable);
            }

        } catch (UnknownHostException e) {
            System.err.println("✗ UnknownHostException: " + e.getMessage());
        } catch (TimeoutException e) {
            System.err.println("✗ Timeout: " + e.getMessage());
        }
    }

    public static void main(String[] args) {
        // Check DNS JVM cache settings
        String cacheTtl = java.security.Security.getProperty(
            "networkaddress.cache.ttl");
        String negativeTtl = java.security.Security.getProperty(
            "networkaddress.cache.negative.ttl");
        System.out.println("JVM DNS cache TTL: " + cacheTtl);
        System.out.println("JVM DNS negative TTL: " + negativeTtl);

        diagnoseDns("inventory.internal");
    }
}
```

---

## Kubernetes DNS Specifics

### CoreDNS ConfigMap Debugging

```bash
kubectl get configmap coredns -n kube-system -o yaml
```

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: coredns
  namespace: kube-system
data:
  Corefile: |
    .:53 {
        errors
        health {
            lameduck 5s
        }
        ready
        kubernetes cluster.local in-addr.arpa ip6.arpa {
            pods insecure
            fallthrough in-addr.arpa ip6.arpa
            ttl 30
        }
        prometheus :9153
        forward . /etc/resolv.conf {
            max_concurrent 1000
        }
        cache 30
        loop
        reload
        loadbalance
    }
```

**Key parameters to check when CoreDNS is slow:**

| Parameter | Default | If Slow, Try |
|-----------|---------|--------------|
| `cache` | 30s | Increase to 300s (reduce upstream queries) |
| `forward . /etc/resolv.conf` | Uses node's DNS | Point directly to VPC resolver (AWS: `.2` address) |
| `max_concurrent` | 1000 | Increase if many concurrent lookups |
| `ttl` | 30s | Lower for faster propagation of service changes |

### Common CoreDNS Errors

```text
# CoreDNS responding with SERVFAIL
$ kubectl logs -n kube-system deployment/coredns
[ERROR] plugin/errors: 2 inventory.default.svc.cluster.local. A: ...
  → CoreDNS can't forward to upstream DNS, or the upstream returned SERVFAIL

# Fix: Check node's /etc/resolv.conf — does it point to a reachable resolver?
$ kubectl get pods -n kube-system -l k8s-app=kube-dns -o wide
$ kubectl exec -n kube-system <coredns-pod> -- cat /etc/resolv.conf

# CoreDNS pod crashing (OOM)
$ kubectl top pods -n kube-system | grep coredns
  → Increase memory limit, or tune cache size to use less memory
  → Check for query loops (loop detection should catch this)
```

### ndots Tuning for Kubernetes

```yaml
# Pod spec with optimized DNS config
apiVersion: v1
kind: Pod
metadata:
  name: app
spec:
  dnsPolicy: "None"
  dnsConfig:
    nameservers:
      - 10.96.0.10         # kube-dns service IP
    searches:
      - default.svc.cluster.local
      - svc.cluster.local
      - cluster.local
    options:
      - name: ndots
        value: "2"          # <=2 dots triggers search domain lookup
      - name: timeout
        value: "1"
      - name: attempts
        value: "2"
```

---

## References

- [DNS & BIND by Cricket Liu & Paul Albitz (O'Reilly)](https://www.oreilly.com/library/view/dns-and-bind/0596100574/)
- [RFC 1034 — Domain Names — Concepts and Facilities](https://datatracker.ietf.org/doc/html/rfc1034)
- [RFC 1035 — Domain Names — Implementation and Specification](https://datatracker.ietf.org/doc/html/rfc1035)
- [RFC 6891 — Extension Mechanisms for DNS (EDNS0)](https://datatracker.ietf.org/doc/html/rfc6891)
- [dnspython Documentation](https://www.dnspython.org/)
- [Kubernetes DNS Debugging](https://kubernetes.io/docs/tasks/administer-cluster/dns-debugging-resolution/)
- [Java Networking Properties](https://docs.oracle.com/en/java/javase/17/docs/api/java.base/java/net/doc-files/net-properties.html)
