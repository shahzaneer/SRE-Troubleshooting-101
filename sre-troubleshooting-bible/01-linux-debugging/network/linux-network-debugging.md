# Linux Network Debugging
> **Category:** Linux | Networking | Debugging
> **Difficulty:** Intermediate to Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#linux` `#networking` `#tcp` `#oncall`

---

## Table of Contents
1. [ss vs netstat](#1-ss-vs-netstat)
2. [TCP State Machine](#2-tcp-state-machine)
3. [tcpdump Recipes](#3-tcpdump-recipes)
4. [curl Diagnostics](#4-curl-diagnostics)
5. [Network Namespaces](#5-network-namespaces)
6. [MTU Issues](#6-mtu-issues)
7. [conntrack](#7-conntrack)
8. [iptables](#8-iptables)
9. [ethtool](#9-ethtool)
10. [Python: TCP Connection Health Checker](#10-python-tcp-connection-health-checker)
11. [JS: Node net Module Debugging](#11-js-node-net-module-debugging)

---

## 1. ss vs netstat

`ss` (socket statistics) is the modern replacement for `netstat`. It's faster (reads directly from kernel via netlink), supports more socket states, and has better filtering. **Only use `ss` — netstat is deprecated.**

```bash
# Listening TCP sockets with process info
ss -tlnp
# -t = TCP, -l = listening, -n = numeric ports (no DNS), -p = show process

# Listening TCP + UDP
ss -tulnp

# All TCP connections (established + listening + all states)
ss -tan
# -a = all states

# Filter by state
ss -tan state established
ss -tan state time-wait
ss -tan state close-wait
ss -tan state syn-sent
ss -tan state syn-recv
ss -tan state fin-wait-1
ss -tan state fin-wait-2
ss -tan state last-ack
ss -tan state closing

# Filter by port (source or destination)
ss -tan sport eq :443                # source port 443
ss -tan dport eq :443                # destination port 443
ss -tan '( dport = :443 or sport = :443 )'  # either direction

# Filter by IP
ss -tan dst 10.0.1.5                 # connections TO 10.0.1.5
ss -tan src 10.0.1.5                 # connections FROM 10.0.1.5
ss -tan '( dst 10.0.1.5 or src 10.0.1.5 )'

# Summary statistics
ss -s
# Total: 1234
# TCP:   500 (estab 345, closed 100, orphaned 0, timewait 55)
# Transport Total     IP        IPv6
# RAW       1         0         1
# UDP       10        5         5
# TCP       500       300       200
# INET      511       305       206
# FRAG      0         0         0

# TCP memory usage
ss -tm
# Shows skmem (socket memory) details — recv-Q, send-Q buffers

# Show IPv4 only (no IPv6 noise)
ss -4 -tan

# Show only Unix domain sockets (local IPC)
ss -xlnp

# Count connections per state
ss -tan | awk 'NR>1 {print $1}' | sort | uniq -c | sort -rn
# 345 ESTAB
#  55 TIME-WAIT
#   2 LISTEN
#   1 SYN-SENT
```

### ss Output Decoded

```
State       Recv-Q   Send-Q   Local Address:Port   Peer Address:Port   Process
ESTAB       0        0        10.0.1.5:443         10.0.2.100:52341    users:(("nginx",pid=1234,fd=6))
CLOSE-WAIT  1        0        10.0.1.5:443         10.0.2.200:12345    users:(("java",pid=28471,fd=78))
```

| Field | Meaning |
|-------|---------|
| **Recv-Q** | Bytes in the socket receive buffer NOT yet read by the application. Non-zero = app is slow reading. |
| **Send-Q** | Bytes in the socket send buffer NOT yet acknowledged by the remote side. Non-zero = network or remote is slow. |
| **CLOSE-WAIT** | Remote side closed the connection (sent FIN) but the local application hasn't called close() yet. See [CLOSE_WAIT Leak](#2-tcp-state-machine). |

---

## 2. TCP State Machine

### TCP State Transition Diagram (Simplified)

```
        CLOSED
          |
          | active open: connect() sends SYN
          v
       SYN_SENT -----------> (receive SYN+ACK)
          |                        |
(receive SYN)                      v
          |                    ESTABLISHED
          v                        |
       SYN_RECV                    | close() sends FIN
          |                        v
(receive ACK)                  FIN_WAIT_1
          |                   /            \
          v                  /              \
      ESTABLISHED    (receive FIN)    (receive FIN+ACK)
          |              |                  |
          |              v                  v
          |          CLOSING            FIN_WAIT_2
          |              |                  |
          |         (receive ACK)    (receive FIN)
          |              |                  |
          |              v                  v
          |           TIME_WAIT <--------- TIME_WAIT
          |              |                  |
          |         (2MSL wait)        (2MSL wait)
          |              |                  |
          +--------------+------------------+
                         |
                         v
                      CLOSED

Passive close (remote initiates):
      ESTABLISHED --> CLOSE_WAIT --> LAST_ACK --> CLOSED
```

### TIME_WAIT — When It's a Problem

After a connection closes, the side that initiated the close stays in TIME_WAIT for 2 * MSL (Maximum Segment Lifetime, typically 60 seconds on Linux = `2 * 30s`). During this time, the port pair (source IP:port, dest IP:port) cannot be reused. This prevents delayed packets from a previous connection from being mistaken for data on a new connection.

```bash
# Count TIME_WAIT connections
ss -tan state time-wait | wc -l

# High TIME_WAIT (1000s) is normal for:
# - Load balancers / proxies that terminate many short-lived connections
# - Benchmarks that create ephemeral connections rapidly
# - Backend services behind a connection pool

# TIME_WAIT is a problem when:
# 1. Running out of ephemeral ports (ports 32768-60999 by default)
#    cat /proc/sys/net/ipv4/ip_local_port_range
#    32768   60999   <-- 28,231 ports available
#    If all of them are in TIME_WAIT, new outbound connections fail.
# 2. Too much kernel memory consumed by TIME_WAIT sockets

# Quick check: are we running out of ephemeral ports?
ss -tan state time-wait | wc -l
# If this approaches (60999 - 32768), you're in trouble.

# Fixes (in order of preference):
# 1. Increase ephemeral port range:
echo "1024 65535" > /proc/sys/net/ipv4/ip_local_port_range
# Persistent: /etc/sysctl.d/99-network.conf:
# net.ipv4.ip_local_port_range = 1024 65535

# 2. Enable TCP time-wait reuse (use with caution):
echo 1 > /proc/sys/net/ipv4/tcp_tw_reuse
# Allows reusing TIME_WAIT sockets for new connections to the SAME remote.

# 3. Reduce TIME_WAIT duration (not recommended for production):
# Cannot change 2MSL at runtime; requires kernel config: HZ=1000 and TCP_TIMEWAIT_LEN=1
# Better to fix the architecture:
# - Use connection pooling instead of creating new connections per request
# - Use keep-alive connections (HTTP/1.1, gRPC, WebSocket)
# - Use a proxy (nginx, haproxy) to handle outbound connections
```

### CLOSE_WAIT — The Silent Killer

CLOSE_WAIT is the state after the remote side has closed the connection (sent FIN) but the local application **has not called close() yet**. A socket in CLOSE_WAIT means the application is holding the connection open indefinitely.

```bash
# Find stuck CLOSE_WAIT connections
ss -tan state close-wait
# If these accumulate and never go away, the app has a bug.

# For each CLOSE_WAIT, check how old the socket is:
ss -tanop state close-wait
# The 'o' option shows timer info:
# timer:(keepalive,1min,0)  <- keepalive timer has been waiting 1 minute

# Classic scenario: Database connection pool CLOSE_WAIT leak
# Server A runs a connection pool to Database B. Database B sends a FIN
# (e.g., due to idle timeout on the DB side, connection_max_lifetime).
# The connection pool picks up a dead connection, sends a query, gets RST.
# But the pool doesn't close the socket — it just removes it from the pool
# without calling close(). The socket remains in CLOSE_WAIT forever.
#
# Fix: Connection pool must call close() on dead connections.
# In HikariCP: connectionTimeout, idleTimeout, maxLifetime must all be configured.
```

### Classic Scenario: 20,000 CLOSE_WAIT

> **Page:** "Server unresponsive — connection refused on port 8080."
>
> ```bash
> $ ss -s
> TCP: 20100 (estab 100, closed 20000, orphaned 0, timewait 0)
>
> $ ss -tan state close-wait | wc -l
> 20000
>
> $ ss -tanop state close-wait | head -5
> CLOSE-WAIT 0 0   10.0.1.5:8080   10.0.2.50:42987  timer:(keepalive,119min,0)
> CLOSE-WAIT 0 0   10.0.1.5:8080   10.0.2.51:12345  timer:(keepalive,118min,0)
> ...
> ```
>
> Root cause: A Java HTTP client code was:
> ```java
> try {
>     HttpResponse response = client.execute(request);
>     // process response...
>     // BUG: missing response.close() or EntityUtils.consume()
> } catch (IOException e) {
>     // BUG: didn't close connection on error either
> }
> ```
> The remote side sent FIN, but the Java code never consumed the response entity and never closed the connection. Each failed/abandoned request leaked one file descriptor and one socket in CLOSE_WAIT. Over 2 hours, 20,000 sockets accumulated. The process hit its `LimitNOFILE` and could no longer accept new connections.
>
> **Fix:** Use try-with-resources or `finally { response.close(); }`.

---

## 3. tcpdump Recipes

### Capture Commands

```bash
# Basic capture on a specific interface
tcpdump -i eth0 -nn port 443
# -i = interface, -nn = don't resolve hostnames or ports (faster, safer)

# Capture from ALL interfaces
tcpdump -i any host 10.0.1.5 and port 80

# Write to PCAP file for later analysis
tcpdump -i eth0 -nn -w /tmp/capture.pcap host 10.0.1.5 and port 443

# Read PCAP file and show packets
tcpdump -r /tmp/capture.pcap -nn

# Capture with packet limit (stop after N packets)
tcpdump -i eth0 -c 1000 -w capture.pcap

# Capture with rotating files (1GB each, keep 10)
tcpdump -i eth0 -C 1000 -W 10 -w capture.pcap

# Capture HTTP traffic (port 80 or 443 without TLS decryption)
tcpdump -i eth0 -nn -A 'port 80'         # -A = print ASCII (for HTTP)
tcpdump -i eth0 -nn -X 'port 80'         # -X = print hex + ASCII

# Filter by TCP flags
tcpdump -i eth0 -nn 'tcp[tcpflags] & tcp-syn != 0 and tcp[tcpflags] & tcp-ack == 0'
# Pure SYN (connection initiation attempts)

# Filter by source/dest network
tcpdump -i eth0 -nn src net 10.0.1.0/24
tcpdump -i eth0 -nn dst net 10.0.2.0/24

# Capture everything EXCEPT SSH (to avoid capturing your own session)
tcpdump -i eth0 -nn not port 22

# Capture DNS traffic
tcpdump -i eth0 -nn port 53

# Capture with human-readable timestamps
tcpdump -i eth0 -nn -tttt

# Show absolute TCP sequence numbers (for tracking retransmissions)
tcpdump -i eth0 -nn -S

# Verbose: show TTL, IP ID, TCP options
tcpdump -i eth0 -nn -v
```

### tcpdump Output Decoded

```
14:32:15.123456 IP 10.0.1.5.52341 > 10.0.2.100.443: Flags [S], seq 123456789, win 65535, options [mss 1460,sackOK,TS val 123456 ecr 0,nop,wscale 7], length 0
14:32:15.234567 IP 10.0.2.100.443 > 10.0.1.5.52341: Flags [S.], seq 987654321, ack 123456790, win 28960, options [mss 1460,sackOK,TS val 654321 ecr 123456,nop,wscale 7], length 0
14:32:15.345678 IP 10.0.1.5.52341 > 10.0.2.100.443: Flags [.], ack 1, win 514, options [nop,nop,TS val 123457 ecr 654321], length 0
```

```
Flag Legend:
[S]   = SYN     (connection start)
[S.]  = SYN-ACK (server acknowledges)
[.]   = ACK     (acknowledgement only, no data)
[P.]  = PUSH+ACK (data being pushed to application)
[F.]  = FIN+ACK (connection closing)
[R]   = RST     (connection reset)
[R.]  = RST+ACK (reset with acknowledgement)
[FP.] = FIN+PUSH+ACK
```

### Analyzing PCAP for Problems

```bash
# Check for TCP retransmissions (packet loss indicator)
tcpdump -r capture.pcap -nn 'tcp[tcpflags] & (tcp-syn|tcp-fin) == 0' | \
  awk '{if (NR>1 && $1 != prev) {if (count>1) print prev " -> " count " retransmissions"} prev=$1; count=0} {count++}'

# Simpler: use tshark (Wireshark CLI)
tshark -r capture.pcap -q -z io,stat,1,tcp.analysis.retransmission,tcp.analysis.fast_retransmission,tcp.analysis.spurious_retransmission

# Check TCP window sizes (too small = receiver bottleneck)
tcpdump -r capture.pcap -nn -v 2>/dev/null | grep -oP 'win \d+' | sort -t' ' -k2 -n | head -20

# Check round-trip time between SYN and SYN-ACK
# This is how long the TCP handshake takes:
tcpdump -r capture.pcap -nn 'tcp[tcpflags] & (tcp-syn) != 0' | \
  awk '{print $1, $3}' | head -50

# Find connection resets (RST packets)
tcpdump -r capture.pcap -nn 'tcp[tcpflags] & tcp-rst != 0'
# High RST count = connections being rejected or aborted
# Common causes: firewall blocking, application crash, port not listening
```

---

## 4. curl Diagnostics

### Verbose Mode — See the Full HTTP Transaction

```bash
# Full HTTP request/response details
curl -v https://api.example.com/health
# *   Trying 10.0.1.5:443...
# * Connected to api.example.com (10.0.1.5) port 443 (#0)
# * ALPN: offers h2,http/1.1
# * TLSv1.3 (OUT), TLS handshake, Client hello (1):
# * TLSv1.3 (IN), TLS handshake, Server hello (2):
# * TLSv1.3 (IN), TLS handshake, Encrypted Extensions (8):
# * TLSv1.3 (IN), TLS handshake, Certificate (11):
# * TLSv1.3 (IN), TLS handshake, CERT verify (15):
# * TLSv1.3 (IN), TLS handshake, Finished (20):
# * TLSv1.3 (OUT), TLS change cipher, Change cipher spec (1):
# * TLSv1.3 (OUT), TLS handshake, Finished (20):
# * SSL connection using TLSv1.3 / TLS_AES_256_GCM_SHA384
# * ALPN: server accepted h2
# * using HTTP/2
# > GET /health HTTP/2
# > Host: api.example.com
# > user-agent: curl/8.4.0
# > accept: */*
# >
# < HTTP/2 200
# < content-type: application/json
# < date: Thu, 11 Jun 2026 14:32:15 GMT
# <
# {"status":"ok"}
```

### Timing Diagnostic with Template File

```bash
# Create a timing template
cat > /tmp/curl-format.txt <<'EOF'
     time_namelookup:  %{time_namelookup}\n
        time_connect:  %{time_connect}\n
     time_appconnect:  %{time_appconnect}\n
    time_pretransfer:  %{time_pretransfer}\n
       time_redirect:  %{time_redirect}\n
  time_starttransfer:  %{time_starttransfer}\n
                     ----------\n
          time_total:  %{time_total}\n
EOF

# Use it to diagnose where time is spent
curl -w "@curl-format.txt" -o /dev/null -s https://api.example.com/
#      time_namelookup:  0.004s    <-- DNS resolution (0.004s = OK)
#         time_connect:  0.015s    <-- TCP handshake (0.011s for TCP connect itself)
#      time_appconnect:  0.085s    <-- TLS handshake (0.070s for TLS negotiation)
#     time_pretransfer:  0.085s    <-- Time from start until first byte can be sent
#        time_redirect:  0.000s
#   time_starttransfer:  0.250s    <-- Time to first byte (TTFB): 0.165s server processing
#                      ----------
#           time_total:  0.251s    <-- Total time

# Interpretation guide:
# time_namelookup > 100ms:  Slow DNS — check DNS server, add caching
# time_connect > 50ms:      Slow TCP connect — network latency or SYN flood protection
# time_appconnect > 500ms:  Slow TLS — cipher negotiation, OCSP stapling, CRL checking
# time_starttransfer - time_pretransfer > 1000ms:  Slow app — server is slow to respond
# time_total - time_starttransfer > 500ms:  Slow download — large response body

# Automated health check that alerts on slow endpoints:
curl -w "%{http_code} %{time_total} %{time_starttransfer}\n" \
  -o /dev/null -s --max-time 5 https://api.example.com/health
# 200 0.251 0.250
```

---

## 5. Network Namespaces

Linux network namespaces provide isolated network stacks — each has its own interfaces, routing tables, iptables rules, and sockets. Containers (Docker, Kubernetes) use network namespaces for isolation.

```bash
# List all network namespaces
ip netns list

# Create a network namespace
ip netns add ns-test

# Run a command inside a namespace
ip netns exec ns-test ip addr
ip netns exec ns-test ping 8.8.8.8  # won't work — no routes or interfaces yet

# Show interfaces inside a namespace
ip netns exec ns-test ip link

# Execute a shell inside a container's network namespace (Docker)
# Option A: nsenter
PID=$(docker inspect -f '{{.State.Pid}}' container_name)
nsenter -t $PID -n ip addr
nsenter -t $PID -n ss -tlnp
nsenter -t $PID -n tcpdump -i eth0 -nn

# Option B: ip netns (requires linking the namespace)
pid=$(docker inspect -f '{{.State.Pid}}' container_name)
mkdir -p /var/run/netns
ln -sf /proc/$pid/ns/net /var/run/netns/container_name
ip netns exec container_name ip addr
# Cleanup: rm /var/run/netns/container_name

# Kubernetes pod debugging:
# 1. Find the pod's container ID
kubectl get pod my-pod -o jsonpath='{.status.containerStatuses[?(@.name=="app")].containerID}' | sed 's/containerd:\/\///'
# 2. On the node, enter the namespace
crictl inspect <container_id> | jq -r '.info.pid'
nsenter -t <pid> -n ss -tlnp
```

### Quick Container Network Debug Script

```bash
#!/bin/bash
# container-net-debug.sh — network diagnostics for a container process
PID=$1
[ -z "$PID" ] && { echo "Usage: $0 CONTAINER_PID"; exit 1; }

echo "=== Interfaces ==="
nsenter -t $PID -n ip addr show

echo ""
echo "=== Routes ==="
nsenter -t $PID -n ip route show

echo ""
echo "=== Listening Sockets ==="
nsenter -t $PID -n ss -tlnp

echo ""
echo "=== iptables Rules ==="
nsenter -t $PID -n iptables -L -n -v --line-numbers

echo ""
echo "=== DNS Config ==="
nsenter -t $PID -n cat /etc/resolv.conf

echo ""
echo "=== Conntrack (if available) ==="
nsenter -t $PID -n conntrack -L 2>/dev/null | head -20 || echo "(conntrack not available)"

echo ""
echo "=== Can reach gateway? ==="
GW=$(nsenter -t $PID -n ip route | grep default | awk '{print $3}')
[ -n "$GW" ] && nsenter -t $PID -n ping -c 1 -W 2 "$GW" 2>&1 || echo "(no default gateway)"
```

---

## 6. MTU Issues

### What Is MTU?

MTU (Maximum Transmission Unit) is the largest packet size that can be sent over a network link without fragmentation. Standard Ethernet MTU is 1500 bytes. VPNs, tunnels, and some cloud networks have smaller MTUs.

When a packet exceeds the MTU and the DF (Don't Fragment) flag is set, the router should send back an ICMP "Fragmentation Needed" message. If this ICMP message is **blocked by a firewall**, the sender never knows to reduce packet size, and the connection silently hangs or times out. This is called a **Path MTU Discovery black hole**.

### Classic Scenario: WiFi Works, VPN Fails

> **Developer reports:** "My app works on office WiFi, but on the VPN, API calls time out with no error."
>
> ```bash
> # WiFi interface MTU = 1500 (standard Ethernet)
> $ ip link show wlan0 | grep mtu
> # mtu 1500
>
> # VPN interface MTU = 1300 (1400 - overhead for VPN encapsulation)
> $ ip link show tun0 | grep mtu
> # mtu 1300
> ```
>
> The app sends an HTTP request with large headers (>1380 bytes with TLS overhead). The TCP MSS is negotiated at 1460 (1500 - 40 for IP + TCP headers). But the VPN tunnel MTU is 1300, so the actual path MTU is ~1260. The 1460-byte packet with DF=1 hits the VPN gateway, which tries to fragment and fails. The VPN gateway sends ICMP "fragmentation needed" (type 3, code 4), but the corporate firewall blocks ALL ICMP. Path MTU discovery fails → packet dropped → TCP retransmits → eventually times out.
>
> **Fix:** Lower the MSS on the VPN interface or clamp MSS to PMTU on the VPN gateway:
> ```bash
> # On the client side:
> ip link set dev tun0 mtu 1300
> # Or with iptables (clamp TCP MSS in SYN packets):
> iptables -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
> ```

### MTU Diagnostic Commands

```bash
# Check current MTU on each interface
ip link show | grep mtu
# Or:
ifconfig | grep mtu

# Path MTU discovery test: find the largest packet that doesn't fragment
# Start at 1500 and decrement until "Frag needed" goes away
ping -M do -s 1472 8.8.8.8
# -M do: set DF (Don't Fragment) flag
# -s 1472: payload = 1472 + 28 (ICMP + IP headers) = 1500 total
# If "Frag needed and DF set" → MTU is smaller. Reduce and retry.

# Binary search for path MTU:
for size in 1500 1400 1300 1200; do
  echo -n "Payload $((size-28)) (total $size): "
  ping -M do -s $((size-28)) -c 1 -W 1 8.8.8.8 2>&1 | grep -E "Frag needed|1 received"
done

# Quick PMTU discovery tool (if available)
tracepath -n 8.8.8.8
# Shows path MTU at each hop

# Check for PMTU black holes (TCP connections hanging, no progress):
ss -tanop | grep -E "probe|timer"
# If you see "probe" in the timer field, TCP is probing for PMTU and not getting ICMP replies.

# Fix: disable PMTU discovery (last resort, reduces performance):
echo 1 > /proc/sys/net/ipv4/tcp_mtu_probing  # Enable PLPMTUD (Packetization Layer Path MTU Discovery)
# or: echo 0 > /proc/sys/net/ipv4/ip_no_pmtu_disc  # Disable PMTU entirely (use 536 min)
# Persistent: net.ipv4.tcp_mtu_probing = 1

# Force a lower MTU on a specific route:
ip route change 10.0.0.0/8 dev eth0 mtu 1400

# Jumbo frames (MTU 9000) for internal high-speed networks:
# Check if jumbo frames are working end-to-end:
ping -M do -s 8972 10.0.1.5
# MTU 9000 - 28 header = 8972 payload
```

---

## 7. conntrack

### Connection Tracking Table

The netfilter connection tracking (conntrack) table is the kernel's state table for all network connections. It tracks every TCP, UDP, ICMP, and other protocol flow so the kernel can apply stateful firewalling and NAT.

```bash
# List all tracked connections
conntrack -L

# Count tracked connections
conntrack -L | wc -l

# Connection tracking statistics
conntrack -S
# entries                 12345
# searched                987654321
# found                   876543210
# new                     500000
# invalid                 1234
# ignore                  56
# delete                  499876
# delete_list            1234
# insert                 500000
# insert_failed          0
# drop                   0
# early_drop             0
# error                  0
# search_restart          56

# If "insert_failed" or "drop" > 0, the conntrack table is full.
# Check dmesg for "table full" messages:
dmesg | grep -i "nf_conntrack: table full"
# [  234.567] nf_conntrack: nf_conntrack: table full, dropping packet

# Check table limits
cat /proc/sys/net/netfilter/nf_conntrack_max
# 262144  (default on modern kernels with >1GB RAM)
cat /proc/sys/net/netfilter/nf_conntrack_count
# 251234  (current usage)

# The table is calculated as:
# nf_conntrack_max = RAM (bytes) / 16384 / number_of_CPU_cores
# (minimum 64, maximum typically 4,194,304)

# Fix: increase the max
echo 524288 > /proc/sys/net/netfilter/nf_conntrack_max
# Persistent: /etc/sysctl.d/99-conntrack.conf:
# net.netfilter.nf_conntrack_max = 524288

# Also reduce the timeout for established connections:
cat /proc/sys/net/netfilter/nf_conntrack_tcp_timeout_established
# 432000 (5 days = 432000 seconds) — very high
# Reduce to 1 hour for high-traffic servers:
echo 3600 > /proc/sys/net/netfilter/nf_conntrack_tcp_timeout_established

# Watch conntrack table size live:
watch -n 1 "cat /proc/sys/net/netfilter/nf_conntrack_count"
# Or:
watch -n 1 "conntrack -C"

# Find connections from a specific IP:
conntrack -L -s 10.0.1.5
conntrack -L -d 10.0.2.100

# Delete specific connection (terminate a stuck connection)
conntrack -D -s 10.0.1.5 -d 10.0.2.100 -p tcp --sport 52341 --dport 443
```

---

## 8. iptables

### Quick Diagnostic Commands

```bash
# List all rules with packet/byte counters and line numbers
iptables -L -n -v --line-numbers
# Chain INPUT (policy ACCEPT 0 packets, 0 bytes)
# num   pkts bytes target     prot opt in     out     source               destination
# 1     12345 7890K ACCEPT     all  --  lo     *       0.0.0.0/0            0.0.0.0/0
# 2      5678 1234K ACCEPT     tcp  --  *      *       0.0.0.0/0            0.0.0.0/0            tcp dpt:22
# 3       890 45678K DROP       tcp  --  *      *       10.0.0.0/8           0.0.0.0/0            tcp dpt:80
#
# The 'pkts' and 'bytes' columns show how many packets matched each rule.
# This tells you which rules are being hit (and which are dead/wrong).

# List NAT table (SNAT, DNAT, MASQUERADE)
iptables -t nat -L -n -v

# List raw table (before conntrack)
iptables -t raw -L -n -v

# List mangle table (packet modification)
iptables -t mangle -L -n -v

# Check if a specific port is allowed by iptables
iptables -L INPUT -n -v | grep "dpt:80"
iptables -L INPUT -n -v | grep "dpt:443"

# Save current rules (for backup before changes)
iptables-save > /tmp/iptables-backup-$(date +%s).rules

# Diagnose: where is the packet being dropped?
# 1. Temporarily log all dropped packets (add to beginning of chain):
iptables -I INPUT 1 -j LOG --log-prefix "IPTABLES-DROP: " --log-level 4
# 2. Check the log:
dmesg | grep "IPTABLES-DROP" | tail -20
# 3. Remove the rule:
iptables -D INPUT -j LOG --log-prefix "IPTABLES-DROP: " --log-level 4

# Watch counters in real-time:
watch -n 1 'iptables -L -n -v --line-numbers'

# Flush all rules (DANGER: removes firewall protection)
# iptables -F  # flush all chains
# iptables -t nat -F  # flush NAT table too
```

### Common iptables Misconfigurations

```bash
# MISCONFIGURATION 1: Accepting a port but with wrong interface
# This allows SSH on eth0 but the connection comes from eth1:
# -A INPUT -i eth0 -p tcp --dport 22 -j ACCEPT
# Fix: -A INPUT -p tcp --dport 22 -j ACCEPT (all interfaces)
# OR: add eth1 rule too

# MISCONFIGURATION 2: DROP before ACCEPT
# -A INPUT -p tcp --dport 80 -j DROP
# -A INPUT -p tcp --sport 1024:65535 -m state --state ESTABLISHED -j ACCEPT
# Rule order matters. Put the ESTABLISHED rule BEFORE any DROP rules.

# MISCONFIGURATION 3: Forgetting RELATED for FTP/DNS
# -A INPUT -m state --state ESTABLISHED -j ACCEPT
# Should also include RELATED for active FTP data connections, ICMP errors:
# -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT

# MISCONFIGURATION 4: Missing loopback accept
# -A INPUT -i lo -j ACCEPT   <-- if missing, localhost apps break in mysterious ways
```

---

## 9. ethtool

### NIC-Level Diagnostics

```bash
# Show NIC driver info, speed, duplex, link status
ethtool eth0
# Settings for eth0:
#   Supported ports: [ TP ]
#   Supported link modes:   10baseT/Half 10baseT/Full ...
#   Speed: 10000Mb/s
#   Duplex: Full
#   Auto-negotiation: on
#   Port: Twisted Pair
#   PHYAD: 0
#   Link detected: yes
#
# Key checks:
# Speed: should match switch port (10Gb, 25Gb, etc.)
# Duplex: MUST be "Full". "Half" = misconfiguration or cable issue.
# Link detected: yes/no

# Check all NICs
for iface in $(ls /sys/class/net/ | grep -v lo); do
  echo "=== $iface ==="
  ethtool $iface 2>/dev/null | grep -E "Speed|Duplex|Link|driver"
done

# NIC statistics: drops, errors, overruns — signs of hardware or driver problems
ethtool -S eth0
# rx_packets: 987654321
# tx_packets: 876543210
# rx_bytes: 987654321000
# tx_bytes: 876543210000
# rx_errors: 0         <-- non-zero = physical errors (bad cable, EMI)
# tx_errors: 0         <-- non-zero = TX hardware problem
# rx_dropped: 123      <-- non-zero = NIC dropped packets (driver buffers full)
# tx_dropped: 0
# rx_over_errors: 0    <-- non-zero = RX FIFO overrun (CPU too slow to process)
# tx_over_errors: 0
# rx_crc_errors: 0     <-- non-zero = bad cable, switch port issue
# rx_frame_errors: 0
# rx_fifo_errors: 0
# rx_missed_errors: 0
# tx_aborted_errors: 0
# tx_carrier_errors: 0
# tx_fifo_errors: 0
# tx_heartbeat_errors: 0

# Key metrics to watch:
# rx_dropped > 0: the NIC ring buffer is too small for the traffic rate.
#   Increase ring buffer:
ethtool -g eth0       # Show current ring buffer settings
# Ring parameters for eth0:
# Pre-set maximums:
# RX:             4096
# RX Mini:        0
# RX Jumbo:       0
# TX:             4096
# Current hardware settings:
# RX:              512   <-- too small!
# TX:              512
ethtool -G eth0 rx 4096 tx 4096  # Increase to max

# rx_over_errors > 0: CPU can't process interrupts fast enough.
#   Check: ethtool -c eth0 (interrupt coalescing settings)
#   Increase coalescing to batch interrupts:
ethtool -C eth0 rx-usecs 50 rx-frames 32
# rx-usecs: delay interrupt up to 50us to batch packets
# rx-frames: delay interrupt until 32 frames or rx-usecs elapsed

# Check driver and firmware version
ethtool -i eth0
# driver: ixgbe                   ← Intel 10GbE driver
# version: 5.4.0-k
# firmware-version: 0x800009a2
# bus-info: 0000:06:00.0         ← PCI bus address
```

---

## 10. Python: TCP Connection Health Checker

```python
#!/usr/bin/env python3
"""
tcp-health-checker.py — comprehensive TCP connection diagnostics.
Tests DNS resolution, TCP connect, TLS handshake, and application-level response.
"""

import socket
import ssl
import time
import sys
import os
import struct
import errno
from datetime import datetime

class TCPHealthChecker:
    def __init__(self, host, port, timeout=5.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.results = {}

    def check_all(self):
        print(f"TCP Health Check: {self.host}:{self.port}")
        print(f"Timestamp: {datetime.now().isoformat()}")
        print("=" * 60)

        self._check_dns()
        if self.results.get('dns_resolved'):
            self._check_tcp_connect()
        if self.results.get('tcp_connected'):
            self._check_ssl()

        self._print_summary()
        return all(r.get('status') == 'OK' for r in self.results.values())

    def _check_dns(self):
        print("\n[DNS Resolution]")
        start = time.monotonic()
        try:
            addrinfo = socket.getaddrinfo(self.host, self.port,
                                          socket.AF_UNSPEC, socket.SOCK_STREAM)
            elapsed = (time.monotonic() - start) * 1000
            ips = list(set(ai[4][0] for ai in addrinfo))
            print(f"  Status: OK ({elapsed:.1f}ms)")
            print(f"  IPs:    {', '.join(ips)}")
            self.results['dns_resolved'] = True
            self.results['dns_ips'] = ips
            self.results['dns_time_ms'] = elapsed
        except socket.gaierror as e:
            print(f"  Status: FAILED — {e}")
            self.results['dns_resolved'] = False
            self.results['dns_error'] = str(e)

    def _check_tcp_connect(self):
        print("\n[TCP Connect]")
        for family in (socket.AF_INET, socket.AF_INET6):
            try:
                start = time.monotonic()
                sock = socket.socket(family, socket.SOCK_STREAM)
                sock.settimeout(self.timeout)
                sock.connect((self.host, self.port))
                elapsed = (time.monotonic() - start) * 1000

                peer = sock.getpeername()
                local = sock.getsockname()

                print(f"  Status:     OK ({elapsed:.1f}ms)")
                print(f"  Local:      {local[0]}:{local[1]}")
                print(f"  Peer:       {peer[0]}:{peer[1]}")
                print(f"  Family:     {'IPv6' if family == socket.AF_INET6 else 'IPv4'}")

                # Check TCP options
                tcp_info = self._get_tcp_info(sock)
                if tcp_info:
                    print(f"  RTT:        {tcp_info.get('rtt', 'N/A')} us")
                    print(f"  RTO:        {tcp_info.get('rto', 'N/A')} ms")
                    print(f"  MSS:        {tcp_info.get('snd_mss', 'N/A')}")
                    print(f"  CWND:       {tcp_info.get('snd_cwnd', 'N/A')}")
                    print(f"  Retrans:    {tcp_info.get('total_retrans', 'N/A')}")

                sock.close()
                self.results['tcp_connected'] = True
                self.results['tcp_time_ms'] = elapsed
                self.results['tcp_family'] = 'IPv6' if family == socket.AF_INET6 else 'IPv4'
                self.results['tcp_sock'] = None
                return  # success — don't try other family

            except socket.timeout:
                if family == socket.AF_INET6:
                    continue  # try IPv4
                print(f"  Status: TIMEOUT ({self.timeout}s)")
                self.results['tcp_connected'] = False
                self.results['tcp_error'] = 'timeout'
            except socket.error as e:
                if family == socket.AF_INET6 and e.errno == errno.ENETUNREACH:
                    continue
                print(f"  Status: FAILED — {e}")
                self.results['tcp_connected'] = False
                self.results['tcp_error'] = str(e)

    def _check_ssl(self):
        print("\n[TLS Handshake]")
        try:
            start = time.monotonic()
            ctx = ssl.create_default_context()
            ctx.check_hostname = True
            ctx.verify_mode = ssl.CERT_REQUIRED

            sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            ssock = ctx.wrap_socket(sock, server_hostname=self.host)
            elapsed = (time.monotonic() - start) * 1000

            print(f"  Status:    OK ({elapsed:.1f}ms)")
            print(f"  Protocol:  {ssock.version()}")
            cipher_name, protocol, bits = ssock.cipher()
            print(f"  Cipher:    {cipher_name} ({bits} bits)")

            cert = ssock.getpeercert()
            print(f"  Cert CN:   {cert.get('subject', [[('', '')]])[0][0][1]}")
            print(f"  SANs:      {', '.join(name[1] for name in cert.get('subjectAltName', []))}")
            not_after = ssl.cert_time_to_seconds(cert['notAfter'])
            days_left = (not_after - time.time()) / 86400
            print(f"  Expires:   {datetime.utcfromtimestamp(not_after).isoformat()} ({days_left:.0f} days)")

            ssock.close()
            self.results['ssl_ok'] = True
            self.results['ssl_time_ms'] = elapsed
            self.results['ssl_protocol'] = ssock.version()
            self.results['ssl_cipher'] = cipher_name

        except ssl.SSLCertVerificationError as e:
            print(f"  Status: CERT ERROR — {e.verify_message}")
            self.results['ssl_ok'] = False
            self.results['ssl_error'] = e.verify_message
        except ssl.SSLError as e:
            print(f"  Status: TLS ERROR — {e}")
            self.results['ssl_ok'] = False
            self.results['ssl_error'] = str(e)
        except socket.timeout:
            print(f"  Status: TLS TIMEOUT — TLS handshake timed out after {self.timeout}s")
            self.results['ssl_ok'] = False
            self.results['ssl_error'] = 'tls_handshake_timeout'
        except Exception as e:
            print(f"  Status: ERROR — {e}")
            self.results['ssl_ok'] = False
            self.results['ssl_error'] = str(e)

    def _get_tcp_info(self, sock):
        """Retrieve TCP connection info via getsockopt(TCP_INFO)."""
        try:
            fmt = "B"*8 + "I"*24  # rough; enough for key fields
            tcp_info = sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, 92)
            # This is OS-dependent; provide a best-effort implementation
            # On Linux, struct tcp_info has many fields. The key ones:
            # tcpi_rtt (offset ~4 bytes), tcpi_rto, tcpi_snd_mss, tcpi_rcv_mss,
            # tcpi_total_retrans
            return {
                'rtt': 'available (use ss -ti)',
                'rto': 'available (use ss -ti)',
                'snd_mss': 'available (use ss -ti)',
                'total_retrans': 'available (use ss -ti)',
            }
        except (OSError, AttributeError):
            return None

    def _print_summary(self):
        print("\n" + "=" * 60)
        all_ok = True
        for check, result in self.results.items():
            if isinstance(result, dict) and 'status' in result:
                pass
            elif isinstance(result, bool):
                status = "PASS" if result else "FAIL"
                if not result:
                    all_ok = False
                print(f"  [{status}] {check}")
        status = "ALL PASS" if all_ok else "SOME FAILED"
        print(f"\nSummary: {status}")
        return all_ok


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "example.com"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 443
    checker = TCPHealthChecker(host, port)
    sys.exit(0 if checker.check_all() else 1)
```

---

## 11. JS: Node net Module Debugging

```javascript
// tcp-debug.js — Comprehensive TCP connection debugging with error classification
const net = require('net');
const tls = require('tls');
const dns = require('dns').promises;
const { performance } = require('perf_hooks');

class TCPDebugger {
    constructor(host, port, options = {}) {
        this.host = host;
        this.port = port;
        this.timeout = options.timeout || 5000;
        this.useTLS = options.useTLS !== false;
        this.results = {};
    }

    async diagnose() {
        console.log(`TCP Diagnosis: ${this.host}:${this.port}`);
        console.log(`Timestamp: ${new Date().toISOString()}`);
        console.log('='.repeat(60));

        await this.checkDNS();
        if (this.results.dns) {
            await this.checkTCP();
        }
        if (this.results.tcp && this.useTLS) {
            await this.checkTLS();
        }
        this.summarize();
    }

    async checkDNS() {
        console.log('\n[DNS Resolution]');
        try {
            const start = performance.now();
            const addresses = await dns.resolve4(this.host).catch(() => null);
            const addresses6 = await dns.resolve6(this.host).catch(() => null);
            const elapsed = (performance.now() - start).toFixed(1);

            const allAddresses = [...(addresses || []), ...(addresses6 || [])];

            if (allAddresses.length === 0) {
                throw new Error('No addresses resolved');
            }

            console.log(`  Status: OK (${elapsed}ms)`);
            console.log(`  IPs:    ${allAddresses.join(', ')}`);
            this.results.dns = { ok: true, ips: allAddresses, time: elapsed };
        } catch (err) {
            console.log(`  Status: FAILED — ${err.message}`);
            this.results.dns = { ok: false, error: err.message };
        }
    }

    async checkTCP() {
        console.log('\n[TCP Connect]');
        return new Promise((resolve) => {
            const start = performance.now();
            const socket = new net.Socket();

            socket.setTimeout(this.timeout);

            socket.on('connect', () => {
                const elapsed = (performance.now() - start).toFixed(1);
                console.log(`  Status:     OK (${elapsed}ms)`);
                console.log(`  Local:      ${socket.localAddress}:${socket.localPort}`);
                console.log(`  Remote:     ${socket.remoteAddress}:${socket.remotePort}`);
                console.log(`  Family:     ${socket.remoteFamily}`);

                this.results.tcp = {
                    ok: true,
                    time: elapsed,
                    localAddress: socket.localAddress,
                    localPort: socket.localPort,
                    remoteAddress: socket.remoteAddress,
                    remotePort: socket.remotePort,
                    family: socket.remoteFamily,
                    bytesRead: socket.bytesRead,
                    bytesWritten: socket.bytesWritten,
                };

                socket.destroy();
                resolve();
            });

            socket.on('error', (err) => {
                this.classifyTCPError(err);
                this.results.tcp = { ok: false, error: err.code, message: err.message };
                resolve();
            });

            socket.on('timeout', () => {
                console.log(`  Status: TIMEOUT (${this.timeout}ms)`);
                this.results.tcp = { ok: false, error: 'ETIMEDOUT' };
                socket.destroy();
                resolve();
            });

            socket.connect(this.port, this.host);
        });
    }

    classifyTCPError(err) {
        const errorMessages = {
            'ECONNREFUSED':  'Connection refused — nothing listening on this port',
            'ECONNRESET':    'Connection reset — remote side forcibly closed (firewall reject, app crash)',
            'ETIMEDOUT':     'Connection timed out — network unreachable or firewall drop',
            'EHOSTUNREACH':  'Host unreachable — no route to host',
            'ENETUNREACH':   'Network unreachable — routing problem',
            'EHOSTDOWN':     'Host is down',
            'EADDRNOTAVAIL': 'Address not available — bind error on local side',
            'EACCES':        'Permission denied — may need root for privileged port',
            'EPIPE':         'Broken pipe — remote closed unexpectedly',
        };

        const description = errorMessages[err.code] || `Unknown error: ${err.code}`;
        console.log(`  Status: FAILED — ${err.code}: ${description}`);
    }

    async checkTLS() {
        console.log('\n[TLS Handshake]');
        return new Promise((resolve) => {
            const start = performance.now();
            const tlsSocket = tls.connect({
                host: this.host,
                port: this.port,
                servername: this.host,
                rejectUnauthorized: true,
                timeout: this.timeout,
            });

            tlsSocket.on('secureConnect', () => {
                const elapsed = (performance.now() - start).toFixed(1);
                const cert = tlsSocket.getPeerCertificate();

                console.log(`  Status:    OK (${elapsed}ms)`);
                console.log(`  Protocol:  ${tlsSocket.getProtocol()}`);
                console.log(`  Cipher:    ${tlsSocket.getCipher().name} (${tlsSocket.getCipher().version})`);
                console.log(`  Cert CN:   ${cert.subject?.CN || 'N/A'}`);
                console.log(`  Issuer:    ${cert.issuer?.CN || 'N/A'}`);

                const validTo = new Date(cert.valid_to);
                const daysLeft = Math.floor((validTo - new Date()) / (1000 * 60 * 60 * 24));
                console.log(`  Expires:   ${cert.valid_to} (${daysLeft} days)`);

                if (cert.subjectaltname) {
                    const sans = cert.subjectaltname
                        .replace(/DNS:/g, '')
                        .replace(/IP Address:/g, '')
                        .split(',')
                        .map(s => s.trim())
                        .join(', ');
                    console.log(`  SANs:      ${sans}`);
                }

                this.results.tls = {
                    ok: true,
                    time: elapsed,
                    protocol: tlsSocket.getProtocol(),
                    cipher: tlsSocket.getCipher().name,
                    certCN: cert.subject?.CN,
                    certExpiry: cert.valid_to,
                };

                tlsSocket.end();
                resolve();
            });

            tlsSocket.on('error', (err) => {
                this.classifyTLSError(err);
                this.results.tls = { ok: false, error: err.code, message: err.message };
                resolve();
            });

            tlsSocket.on('timeout', () => {
                console.log(`  Status: TLS HANDSHAKE TIMEOUT`);
                this.results.tls = { ok: false, error: 'TLS_TIMEOUT' };
                tlsSocket.destroy();
                resolve();
            });
        });
    }

    classifyTLSError(err) {
        const tlsErrors = {
            'UNABLE_TO_GET_ISSUER_CERT_LOCALLY': 'Missing CA certificate in trust store',
            'UNABLE_TO_VERIFY_LEAF_SIGNATURE':   'Certificate chain verification failed — missing intermediate?',
            'CERT_HAS_EXPIRED':                  'Server certificate has expired',
            'ERR_TLS_CERT_ALTNAME_INVALID':       'Hostname does not match certificate CN or SANs',
            'SELF_SIGNED_CERT_IN_CHAIN':          'Self-signed certificate in the chain',
            'DEPTH_ZERO_SELF_SIGNED_CERT':        'Server presented a self-signed certificate',
            'ERR_SSL_VERSION_OR_CIPHER_MISMATCH': 'No common TLS version or cipher suite between client and server',
            'ECONNRESET':                         'Connection reset during TLS handshake (plain HTTP on HTTPS port?)',
            'WRONG_VERSION_NUMBER':               'TLS version mismatch — server doesn\'t support client\'s TLS version',
            'HANDSHAKE_TIMEOUT':                  'TLS handshake timed out',
        };

        const description = tlsErrors[err.code] || `Unknown TLS error: ${err.code}`;
        console.log(`  Status: TLS ERROR — ${err.code}: ${description}`);
    }

    summarize() {
        console.log('\n' + '='.repeat(60));
        const checks = [
            { key: 'dns', label: 'DNS' },
            { key: 'tcp', label: 'TCP' },
            { key: 'tls', label: 'TLS' },
        ];

        let allPassed = true;
        for (const { key, label } of checks) {
            const result = this.results[key];
            if (!result) continue;
            const status = result.ok ? 'PASS' : 'FAIL';
            if (!result.ok) allPassed = false;
            console.log(`  [${status}] ${label}`);
        }

        console.log(`\nSummary: ${allPassed ? 'ALL PASS' : 'SOME FAILED'}`);
    }
}

// Usage:
const debugger = new TCPDebugger('api.example.com', 443, { timeout: 5000 });
debugger.diagnose().catch(console.error);

module.exports = TCPDebugger;
```

### Node.js: Basic Socket Debugging Patterns

```javascript
// Quick socket check with error handling
const net = require('net');

function quickTCPCheck(host, port, timeout = 3000) {
    return new Promise((resolve, reject) => {
        const socket = new net.Socket();
        const timer = setTimeout(() => {
            socket.destroy();
            reject(new Error(`Connection to ${host}:${port} timed out after ${timeout}ms`));
        }, timeout);

        socket.connect(port, host, () => {
            clearTimeout(timer);
            socket.destroy();
            resolve({ host, port, reachable: true });
        });

        socket.on('error', (err) => {
            clearTimeout(timer);
            socket.destroy();

            // Map error codes to actionable messages
            const errorMap = {
                'ECONNREFUSED': `${host}:${port} — nothing listening (check if service is running)`,
                'EHOSTUNREACH': `${host} — network unreachable (routing issue or wrong IP)`,
                'ENOTFOUND': `${host} — DNS resolution failed`,
                'ETIMEDOUT': `${host}:${port} — firewall likely blocking traffic`,
            };

            reject(new Error(errorMap[err.code] || `${host}:${port} — ${err.code}: ${err.message}`));
        });
    });
}

// Test multiple endpoints in parallel
(async () => {
    const endpoints = [
        { host: 'api.example.com', port: 443 },
        { host: 'db.internal', port: 5432 },
        { host: 'redis.internal', port: 6379 },
    ];

    const results = await Promise.allSettled(
        endpoints.map(ep => quickTCPCheck(ep.host, ep.port))
    );

    results.forEach((result, i) => {
        if (result.status === 'fulfilled') {
            console.log(`[OK] ${endpoints[i].host}:${endpoints[i].port}`);
        } else {
            console.log(`[FAIL] ${result.reason.message}`);
        }
    });
})();
```
