# 02 — Networking

> **Diagnosing connectivity, DNS, TLS, and load balancer issues that cause production outages.**
> If a service can't talk to another service, start here.

---

## Quick Navigation

| # | Document | What You'll Learn | Time to Read |
|---|----------|-------------------|--------------|
| 1 | [DNS Troubleshooting](dns/dns-troubleshooting.md) | dig deep dive, propagation, split-horizon, resolv.conf, DNSSEC, resolver scripts in Python/Java | 25 min |
| 2 | [TLS/SSL Troubleshooting](tls-ssl/tls-troubleshooting.md) | openssl s_client, cert expiry, SNI, mTLS, chain validation, cipher suites, keystore debugging | 25 min |
| 3 | [Load Balancer Troubleshooting](loadbalancer/lb-troubleshooting.md) | Health checks, 502/503/504, session stickiness, connection draining, HAProxy/Nginx upstream errors | 20 min |

---

## Why Networking Debugging Is Different

Networking failures are **distributed by nature** — unlike a CPU spike or OOM kill, the problem almost never lives on a single host. A typical "service is down" alert might be:

- A DNS resolver returning stale records because TTL hasn't expired
- A TLS handshake failing because an intermediate certificate expired last night
- A load balancer marking all targets unhealthy because the health check endpoint got slower after a DB migration
- A security group change that dropped port 5432 outbound from the app subnet
- A Kubernetes `ndots:5` in resolv.conf silently adding 500ms to every DNS lookup

The key skill is **isolating the layer**: is it DNS? Is it TLS? Is it routing? Is it the LB? This section gives you a systematic approach for each layer.

---

## The Networking Diagnostic Stack (Bottom to Top)

```text
Layer 1 — Physical/Data Link:   Cable unplugged? Interface up? (rare in cloud)
Layer 2 — ARP/Neighbor:         Wrong MAC? ARP table stale?
Layer 3 — IP/Routing:           IP reachable? Route table correct? traceroute shows where?
Layer 4 — TCP/UDP:              Port open? Firewall blocking? SYN getting SYN-ACK?
Layer 5 — TLS:                  Handshake succeeds? Cert valid? Protocol mismatch?
Layer 6 — DNS:                  Name resolves? To correct IP? From correct resolver?
Layer 7 — HTTP/App:             HTTP response? Correct status? Correct payload?
```

**Golden rule**: Always diagnose **bottom-up**. If Layer 3 is broken, nothing above it will work, and you'll waste time debugging Layer 7.

---

## First 30 Seconds: The Master Socket Test

When someone says "service X can't connect to service Y," run this immediately:

```bash
echo "=== DNS ===" && dig +short service.internal
echo "=== PING ===" && ping -c 3 -W 2 10.0.1.50
echo "=== TCP ===" && nc -vz -w 3 10.0.1.50 443
echo "=== TLS ===" && echo | openssl s_client -connect 10.0.1.50:443 -servername service.internal 2>&1 | head -20
echo "=== HTTP ===" && curl -sv --max-time 5 https://service.internal/healthz 2>&1
```

This tells you in 3 seconds whether the problem is DNS, routing, TLS, or the application itself.

---

## Key Networking Commands Cheat Sheet

| Command | Purpose | Quick Example |
|---------|---------|---------------|
| `dig` | DNS queries (the standard) | `dig +short example.com @8.8.8.8` |
| `nslookup` | Simple DNS lookup (deprecated) | `nslookup example.com 8.8.8.8` |
| `host` | Minimal DNS lookup | `host example.com` |
| `ping` | ICMP reachability | `ping -c 3 10.0.0.1` |
| `traceroute` / `mtr` | Path discovery | `mtr -r -c 10 10.0.0.1` |
| `nc` / `ncat` | TCP/UDP socket test | `nc -vz 10.0.0.1 443` |
| `ss` | Socket statistics (replaces netstat) | `ss -tlnp` |
| `tcpdump` | Packet capture | `tcpdump -i eth0 port 53 -w dns.pcap` |
| `openssl s_client` | TLS handshake debug | `openssl s_client -connect host:443 -servername host` |
| `curl` | HTTP client with verbose output | `curl -sv https://example.com` |
| `iptables` / `nft` | Firewall rules | `iptables -L -n -v` |

---

## Common Networking Gotchas

| Gotcha | Explanation |
|--------|-------------|
| `ndots:5` in resolv.conf | Before trying `/etc/hosts`, glibc appends each search domain and queries DNS. With 5 search domains and `ndots:1`, single-label names get 5 extra lookups. |
| DNS caching | Local resolver (systemd-resolved, dnsmasq) caches records. Clear with `systemd-resolve --flush-caches` or `sudo killall -HUP dnsmasq`. |
| Half-open TCP connections | Firewall drops packets silently (no RST). App waits for TCP timeout (up to 127s on Linux). Use `net.ipv4.tcp_syn_retries` to control. |
| MTU issues | Packets larger than path MTU without DF=0 get dropped. MSS clamping in TCP usually handles this, but UDP + large payloads = silent drops. |
| Ephemeral port exhaustion | 28,232 available ports on Linux (32768-60999). If you make >28k connections in TIME_WAIT window, new connections fail with EADDRNOTAVAIL. |

---

## References

- [IANA DNS Parameters](https://www.iana.org/assignments/dns-parameters/dns-parameters.xhtml)
- [dig man page](https://linux.die.net/man/1/dig)
- [openssl s_client man page](https://www.openssl.org/docs/manmaster/man1/openssl-s_client.html)
- [AWS Load Balancer Troubleshooting](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-troubleshooting.html)
- [HAProxy Log Format Reference](https://www.haproxy.com/documentation/haproxy-configuration-manual/latest/#8.2.3)
- [nginx upstream module docs](https://nginx.org/en/docs/http/ngx_http_upstream_module.html)
