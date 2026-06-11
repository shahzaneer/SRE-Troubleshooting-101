# Load Balancer Troubleshooting

> **Category:** Networking | Load Balancing
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#loadbalancer` `#lb` `#oncall`

---

## Table of Contents

1. [LB Architecture Primer](#lb-architecture-primer)
2. [Health Check Failures](#health-check-failures)
3. [Session Persistence / Sticky Sessions](#session-persistence--sticky-sessions)
4. [Connection Draining (Deregistration Delay)](#connection-draining-deregistration-delay)
5. [502 Bad Gateway](#502-bad-gateway)
6. [503 Service Unavailable](#503-service-unavailable)
7. [504 Gateway Timeout](#504-gateway-timeout)
8. [ELB/ALB/NLB Deep Dive](#elbalbnlb-deep-dive)
9. [HAProxy Troubleshooting](#haproxy-troubleshooting)
10. [Nginx Upstream Troubleshooting](#nginx-upstream-troubleshooting)

---

## LB Architecture Primer

Understanding the flow helps diagnose which component is failing:

```text
Client (Browser/API consumer)
    │
    │  DNS: api.example.com → LB public IP 1.2.3.4
    │
    └───── TCP + TLS ──────> Load Balancer
                                  │
                                  │  ↓ Health checks (every N seconds)
                                  │  ↓ Connection routing
                                  │
                    ┌─────────────┼─────────────┐
                    │             │             │
                    ▼             ▼             ▼
               Backend 1     Backend 2     Backend 3
               (10.0.1.10)   (10.0.1.11)   (10.0.1.12)
               /healthz       /healthz       /healthz
               returns 200    returns 200    returns 500
               HEALTHY        HEALTHY        UNHEALTHY
               ^ receives      ^ receives
               traffic         traffic
```

### Key Metrics at the LB Layer

| Metric | What It Tells You |
|--------|-------------------|
| `HTTPCode_ELB_5XX` | LB itself generated a 5xx (vs. backend) |
| `HTTPCode_Backend_5XX` | Backend generated a 5xx |
| `TargetResponseTime` | Backend latency from LB's perspective |
| `RequestCount` | Total requests hitting the LB |
| `HealthyHostCount` | Backends passing health checks |
| `UnHealthyHostCount` | Backends failing health checks |
| `ActiveConnectionCount` | Current open connections |
| `RejectedConnectionCount` | Connections dropped at LB (overload) |

---

## Health Check Failures

Health checks determine if a backend receives traffic. If the check fails, the LB marks it unhealthy and stops routing traffic.

### Types of Health Checks

```text
TCP HEALTH CHECK:
  LB: TCP SYN → Backend:port
  Backend: TCP SYN-ACK ← Must respond within timeout
  LB: TCP RST (closes connection)
  → Just checks if a process is listening on that port.

HTTP/HTTPS HEALTH CHECK:
  LB: GET /healthz HTTP/1.1 → Backend
  Backend: HTTP 200 OK ← Must return 2xx within timeout
  → Checks that the app is alive AND functioning.

gRPC HEALTH CHECK (NLB/ALB):
  LB: gRPC Health/Check → Backend
  Backend: SERVING status ← Must respond within timeout
```

### Scenario: "Health Check Endpoint Too Slow"

```text
SYMPTOM: "ALB marks all instances as unhealthy. All traffic gets 503.
         But the app is running — I can curl /healthz from the instance
         and it returns 200 OK. What gives?"

INVESTIGATION:
$ # Test the health check endpoint and TIME it
$ time curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/healthz
200
real  0m8.241s             ← Takes 8.2 seconds!
user  0m0.010s
sys   0m0.005s

$ # Check the health check code
$ cat /app/healthcheck.py
def healthz():
    # Checks DB, Redis, S3, downstream services...
    db_ok = check_database()      # 3 seconds timeout
    redis_ok = check_redis()      # 2 seconds timeout
    s3_ok = check_s3()            # 3 seconds timeout
    return all([db_ok, redis_ok, s3_ok])
    # Total: up to 8 seconds with sequential checks

$ # ALB health check config:
$ aws elbv2 describe-target-groups --target-group-arn arn:aws:... \
  --query "TargetGroups[0].{Timeout:HealthCheckTimeoutSeconds,
                              Interval:HealthCheckIntervalSeconds,
                              Path:HealthCheckPath}"
{
    "Timeout": 5,           ← ALB waits 5 seconds MAX
    "Interval": 30,
    "Path": "/healthz"
}

ROOT CAUSE: The /healthz endpoint does a full dependency check
(DB + Redis + S3) and takes ~8 seconds. The ALB health check
timeout is 5 seconds. The ALB never gets a response within the
timeout window → marks instance unhealthy.

FIX — Split liveness from readiness:
  /healthz  (liveness):  "Is the process running?"   → fast check, no deps
  /ready    (readiness): "Can this instance serve?"   → full dependency check

  Configure ALB to use /healthz for health checks (keep instances alive)
  Use /ready only for Kubernetes readiness probes or deployment gating

IMPLEMENTATION:
  @app.route('/healthz')
  def liveness():
      return jsonify(status="alive"), 200     # Always returns immediately

  @app.route('/ready')
  def readiness():
      checks = run_dependency_checks()        # Can take seconds
      if all(checks.values()):
          return jsonify(status="ready"), 200
      else:
          return jsonify(status="not_ready", checks=checks), 503
```

### Health Check Parameter Tuning

| Parameter | Default (ALB) | What to Consider |
|-----------|---------------|------------------|
| **Timeout** | 5s | Must be less than Interval. If your endpoint takes 2s, set timeout to 3s. |
| **Interval** | 30s | Dictates how quickly you detect failure. 30s means up to 30s of traffic to dead instances. Set to 5-10s for critical services. |
| **Healthy threshold** | 5 | Number of consecutive successes to mark healthy. Lower for faster recovery, higher to avoid flapping. |
| **Unhealthy threshold** | 2 | Number of consecutive failures to mark unhealthy. Lower for faster detection. |
| **Success codes** | 200 | Accept custom range: `200-399` if your health endpoint returns 302 redirect. |

### Quick LB Health Check Diagnostic Commands

```bash
# ALB: Check target health
aws elbv2 describe-target-health --target-group-arn arn:aws:elasticloadbalancing:...

# HAProxy: Check server states via stats socket
echo "show stat" | socat unix-connect:/var/run/haproxy.sock stdio | grep -v "^#"

# Nginx Plus (commercial): Check upstream health via API
curl http://localhost:8080/api/6/http/upstreams/backend/servers/

# Kubernetes Ingress: Check endpoint health
kubectl get endpoints my-service -n namespace
kubectl describe endpoints my-service -n namespace
```

---

## Session Persistence / Sticky Sessions

Session persistence ensures a user is routed to the same backend for all requests. Useful for stateful applications, but dangerous with canary deployments.

### How Stickiness Works

```text
WITHOUT STICKINESS (round-robin):
  Request 1 → Backend A  (login, session created in memory)
  Request 2 → Backend B  (no session found → redirect to login!)
  Request 3 → Backend C  (no session found → redirect to login!)
  Result: User loops infinitely on login page.

WITH STICKINESS (ALB cookie):
  ALB sets: AWSALB=XXXXXXXXX (encrypted cookie pointing to Backend A)
  Request 1 → Backend A  (login, session created)
  Request 2 → Backend A  (cookie → ALB routes to same backend)
  Request 3 → Backend A  (cookie → same backend)
  Result: Session works, but user is PINNED to one backend.
```

### Scenario: "Canary Deployment + Sticky Sessions = Stuck Users"

```text
SYMPTOM: "We deployed a canary — 10% of traffic to new version,
         90% to stable. Some users on the canary get 500 errors and
         keep getting 500 errors on every refresh. They can't go
         back to the stable version."

INVESTIGATION:
  → ALB Target Group with 90% weight: stable-v1
  → ALB Target Group with 10% weight: canary-v2
  → Stickiness: ENABLED (AWSALB cookie, duration: 1 day)

  User Journey:
  1. First request → ALB assigns AWSALB cookie → routes to canary-v2 (10% chance)
  2. Response: 500 error (bug in canary-v2)
  3. User refreshes → ALB reads AWSALB cookie → routes to canary-v2 AGAIN
  4. Response: 500 error
  5. User refreshes 10 more times → STILL canary-v2 (sticky!)
  6. User gives up and leaves.

ROOT CAUSE: Sticky sessions pin users to the backend they first hit.
With canary deployments, 10% of users get BAD LUCK — they're pinned
to a broken canary for the duration of the stickiness cookie.

FIX: Never use sticky sessions with canary deployments.
  - Disable stickiness during canary rollout
  - Use application-level routing based on feature flags
    (user gets same version regardless of which backend they hit)
  - Or use a session store (Redis, DynamoDB) so session data is
    available from ALL backends, eliminating the need for stickiness
```

### Checking and Disabling Stickiness

```bash
# ALB: Check stickiness configuration
aws elbv2 describe-target-group-attributes --target-group-arn arn:aws:...

# Look for:
# "stickiness.enabled": "true"
# "stickiness.type": "lb_cookie"
# "stickiness.lb_cookie.duration_seconds": "86400"

# Disable via CLI:
aws elbv2 modify-target-group-attributes --target-group-arn arn:aws:... \
  --attributes Key=stickiness.enabled,Value=false
```

---

## Connection Draining (Deregistration Delay)

When an instance is deregistered (during deployment, scale-in, or health failure), the LB stops new connections but allows existing connections to complete within a grace period.

### How It Works

```text
TIME 0s  — Instance deregistered from target group
TIME 0s  — LB stops sending NEW connections to this instance
TIME 0s  — In-flight connections (3 requests currently processing)
              Allowed to complete within deregistration delay window
TIME 30s — Deregistration delay expires
TIME 30s — LB forcefully terminates any remaining in-flight connections
TIME 30s — Instance removed completely
```

### Scenario: "Rolling Deployment Takes Forever"

```text
SYMPTOM: "A rolling deployment of 50 instances takes 15 minutes
         to complete. Each instance takes 2 seconds to actually
         stop, but the deployment step takes 5+ minutes per instance."

INVESTIGATION:
  → Deregistration delay (connection draining timeout): 300 seconds (5 min)
  → 50 instances × 300s = 25,000 seconds = ~416 minutes max theoretical
  → In practice: ASG waits for instance to fully deregister before
    starting the next one in the batch

  If instances have 0 in-flight requests (drain instantly):
    Still, the LB holds the deregistration slot for 300s.

  FIX: Reduce deregistration delay for services with SHORT request durations.
       Set it to max_request_duration + 5 seconds as a buffer.

  Example: API has 10-second max processing time.
           Set deregistration delay to 15s.
           50 instances × 15s = 750s ≈ 12 minutes (better than 416)

  For really fast deployments: set delay to 0s
  (acceptable if clients have retry logic for the occasional
   in-flight request that gets terminated)
```

### Checking and Tuning Deregistration Delay

```bash
# ALB: Check current deregistration delay
aws elbv2 describe-target-group-attributes --target-group-arn arn:aws:... \
  --query "Attributes[?Key=='deregistration_delay.timeout_seconds']"
# Default: 300 seconds

# Set to 30s for fast-deploy services
aws elbv2 modify-target-group-attributes --target-group-arn arn:aws:... \
  --attributes Key=deregistration_delay.timeout_seconds,Value=30
```

---

## 502 Bad Gateway

**502 Bad Gateway**: The load balancer received an invalid response from the backend.

```text
Possible causes:
  ├─ Backend returned malformed HTTP (missing headers, bad chunked encoding)
  ├─ Backend closed the connection before sending a complete response
  ├─ Backend sent raw TCP data (not HTTP) to an HTTP listener
  ├─ Backend uses HTTP/2 but LB expects HTTP/1.1
  ├─ Backend response exceeds max header size
  └─ Backend process crashed mid-response
```

### Scenario: "HTTP/2 Upgrade Breaks ALB → Nginx Pipeline"

```text
SYMPTOM: "We enabled HTTP/2 on the ALB. Now all requests return 502.
         Nothing changed on the Nginx backend. HTTP/1.1 requests still
         work when we test directly to the backend."

INVESTIGATION:
  → ALB listener:   HTTPS:443, protocol: HTTP/2
  → Target group:   HTTP:80, protocol version: HTTP1 (default)

  ALB receives HTTP/2 request from client
    → Terminates TLS, decodes HTTP/2 frames
    → Forwards to backend as HTTP/1.1 by default ✓

  BUT: IF target group protocol version is set to "HTTP2" or "gRPC":
    ALB forwards HTTP/2 frames to Nginx
    Nginx configured for HTTP/1.1 only
    Nginx doesn't understand HTTP/2 frames
    Nginx either closes connection or returns garbage
    ALB sees invalid response → 502

ROOT CAUSE: Protocol mismatch. ALB target group protocol version
must match what the backend actually speaks.

CORRECT CONFIGURATION:
  ALB Listener:     HTTPS:443  → Target group HTTP:80  (ALB terminates TLS)
  Target protocol:  HTTP1      → Backend HTTP/1.1       (matches Nginx)

  OR (if backend supports HTTP/2):
  Target protocol:  HTTP2      → Backend HTTP/2         (backend must support it)
```

### Debugging 502 Errors

```bash
# 1. Check if backend is actually listening and responding
curl -v http://backend-ip:80/healthz

# 2. Check the protocol — does the backend speak HTTP?
nc -vz backend-ip 80
echo -e "GET / HTTP/1.0\r\n\r\n" | nc backend-ip 80 | head -20

# 3. Check if the backend is closing connections prematurely
curl -sv http://backend-ip:80/ 2>&1 | grep -i "connection:"

# 4. Check ALB access logs for specific error
# Look for: "backend_status_code" = "-" (backend didn't respond)
#    vs     "backend_status_code" = "502" (backend sent 502)

# 5. Check if response headers are too large
# (ALB limits: 64KB total headers, 256 cookies)
curl -sv http://backend-ip:80/ 2>&1 | wc -c
```

### Nginx → Backend 502

```text
Nginx error: "upstream prematurely closed connection while
              reading response header from upstream"

MEANS: The backend accepted the connection but closed it
       before sending any HTTP response.

CAUSES:
  - Backend process crashed (OOM kill, segfault)
  - Backend accepted connection but its listen queue was full
  - Backend has keepalive_timeout set too low, closed idle connection
  - Backend firewall resetting the connection
  - Backend's max_children reached, accepted but couldn't process

DIAGNOSIS:
  $ strace -p <backend-pid> -e trace=network
    → Look for accept() followed by close() with no send() in between

  $ ss -tnp state time-wait | wc -l
    → If >10,000 TIME_WAIT connections, ephemeral port exhaustion

  $ grep "max_children" /etc/php-fpm.d/www.conf
    → If reached, PHP-FPM queues requests until timeout
```

---

## 503 Service Unavailable

**503 Service Unavailable**: The load balancer cannot find ANY healthy backend to route to.

```text
Possible causes:
  ├─ ALL backends failed health checks (0 healthy hosts)
  ├─ Target group has 0 registered targets
  ├─ Target group was deleted but listener still references it
  ├─ All backends at max capacity (surge queue full)
  └─ Security group change blocks LB → backend traffic
```

### Scenario: "Zero Healthy Hosts After Deployment"

```text
SYMPTOM: "Terraform applied a new launch template. ASG started
         cycling instances. Now the site is down — 503 on all
         requests. CloudWatch shows 0 healthy hosts."

TIMELINE:
  T+0:00  — Terraform applies new launch template
  T+0:30  — ASG starts terminating old instances (connection draining)
  T+1:00  — First new instance comes up
  T+1:30  — New instance HEALTH CHECK FAILS (wrong port in launch template)
  T+2:00  — Second new instance comes up, also fails health check
  T+3:00  — Third new instance... same issue
  T+5:00  — All old instances drained and terminated
  T+5:00  — All new instances failing health checks
  T+5:00  — → 0 healthy hosts → ALB returns 503 for all requests

ROOT CAUSE: The new launch template had the wrong port configured
(8080 instead of 80). The health check endpoint was on port 80,
but the app was listening on port 8080. All new instances failed
health checks, and the old instances were already terminated.

PREVENTION:
  1. Use "instance refresh" with minimum healthy percentage (e.g., 50%)
  2. Set up CloudWatch alarms on HealthyHostCount < 1
  3. Use canary deployment (1 instance first, verify, then full rollout)
  4. Smoke test the new launch template in a staging ASG first
```

### Debugging 503

```bash
# ALB: Count healthy vs unhealthy targets
aws elbv2 describe-target-health --target-group-arn arn:aws:... \
  --query "TargetHealthDescriptions[*].TargetHealth.State" --output text \
  | sort | uniq -c

# Check if ANY targets are healthy
aws elbv2 describe-target-health --target-group-arn arn:aws:... \
  --query "TargetHealthDescriptions[?TargetHealth.State=='healthy']"

# Check specific unhealthy target's reason
aws elbv2 describe-target-health --target-group-arn arn:aws:... \
  --query "TargetHealthDescriptions[?TargetHealth.State!='healthy'].{Id:Target.Id,Reason:TargetHealth.Reason,Description:TargetHealth.Description}"

# CloudWatch: check HealthyHostCount metric
aws cloudwatch get-metric-statistics \
  --namespace AWS/ApplicationELB \
  --metric-name HealthyHostCount \
  --dimensions Name=TargetGroup,Value=arn:aws:... \
  --start-time 2026-06-11T00:00:00Z --end-time 2026-06-11T12:00:00Z \
  --period 300 --statistics Minimum
```

---

## 504 Gateway Timeout

**504 Gateway Timeout**: The backend did not respond within the load balancer's timeout.

```text
ALB/NLB idle timeout:    60 seconds (default, configurable 1-4000)
ALB request timeout:     The backend has this long to START responding
                         (send headers). Default 60s, max 4000s.
```

### Scenario: "Large File Uploads Fail With 504"

```text
SYMPTOM: "Our file upload endpoint works for files < 10MB,
         but fails with 504 for files > 50MB. The upload takes
         about 90 seconds on a slow connection."

INVESTIGATION:
  Client: POST /upload (90 second upload over slow 4G connection)
  ALB:    Idle timeout = 60 seconds
          At T+60s, ALB has received no new data from client for
          >60s → ALB drops the connection → returns 504 to client

  The backend never even gets the request — ALB killed it before
  the upload completed.

ROOT CAUSE: ALB idle timeout (60s) < file upload duration (90s).
  The ALB's idle timeout applies to BOTH directions:
  - Client → ALB: if no data for 60s, disconnect
  - ALB → Backend: if no data for 60s, disconnect

  Long uploads: client is sending data slowly, ALB times out
  Long responses: backend is generating data slowly, ALB times out
  WebSocket: idle timeout kills idle connections

FIX:
  1. Increase ALB idle timeout to match worst-case request duration + buffer
     aws elbv2 modify-load-balancer-attributes \
       --load-balancer-arn arn:aws:... \
       --attributes Key=idle_timeout.timeout_seconds,Value=300
  2. Or: use NLB (network LB, L4) — no idle timeout for TCP
     NLB passes bytes through without any timeout
  3. Or: use presigned S3 URLs for uploads, bypass the LB entirely
```

### Timeout Chain Across Layers

Understanding where timeouts stack up:

```text
Client timeout:              120s (browser/curl --max-time)
  │
  └─ ALB idle timeout:       60s  ← if 60 < 120, ALB kills first
       │
       └─ Nginx proxy_read_timeout: 30s  ← if 30 < 60, Nginx kills first
            │
            └─ App process timeout: 300s
                 │
                 └─ DB query timeout: 30s  ← if 30 < 300, DB kills first

The SHORTEST timeout wins. If any layer has a shorter timeout
than the one above it, that layer will kill the connection.
```

**Golden rule**: Timeouts should **increase** as you go deeper.
```
Client (120s) > LB (90s) > Reverse Proxy (60s) > App (30s) > DB (15s)
```

### Debugging 504

```bash
# ALB: Check idle timeout setting
aws elbv2 describe-load-balancer-attributes --load-balancer-arn arn:aws:... \
  --query "Attributes[?Key=='idle_timeout.timeout_seconds']"

# Nginx: Check proxy timeout settings
grep -E "proxy_read_timeout|proxy_connect_timeout|proxy_send_timeout" \
  /etc/nginx/nginx.conf

# HAProxy: Check timeout settings in config
grep "timeout" /etc/haproxy/haproxy.cfg

# Test from a client perspective
time curl -v --max-time 120 https://app.example.com/slow-endpoint
# Observe WHERE the timeout occurs — at the LB (quick reset) or backend (slow response)

# Check if backend is actually slow or if LB is too aggressive
curl -v --connect-timeout 5 --max-time 15 http://backend-direct-ip:80/
# If direct to backend works fast, LB timeout is the issue
# If direct to backend is also slow, backend is the issue
```

---

## ELB/ALB/NLB Deep Dive

### Quick Decision Matrix

| Feature | CLB (ELB v1) | ALB | NLB | GWLB |
|---------|-------------|-----|-----|------|
| **OSI Layer** | L4 & L7 | L7 | L4 | L3 |
| **Protocols** | HTTP, HTTPS, TCP, SSL | HTTP, HTTPS, gRPC | TCP, UDP, TLS | IP |
| **Path-based routing** | No | Yes | No | No |
| **Host-based routing** | No | Yes | No | No |
| **WebSocket** | Yes (TCP/SSL mode) | Yes (native) | Yes (native) | No |
| **Static IP** | No | No | Yes (per-AZ) | No |
| **PrivateLink** | No | No | Yes | Yes |
| **Latency** | ~1-3ms | ~1-3ms | <1ms | <1ms |
| **Client IP to backend** | Proxy Protocol | X-Forwarded-For header | Preserved (or Proxy Protocol) | Preserved |
| **Health checks** | TCP/HTTP/HTTPS | TCP/HTTP/HTTPS/gRPC | TCP/HTTP/HTTPS | None |
| **Auth (OIDC/Cognito)** | No | Yes | No | No |
| **WAF integration** | No | Yes | No | No |
| **Cross-zone LB** | Optional | Always on | Optional | N/A |
| **Idle timeout** | 60s (fixed) | 1-4000s (configurable) | 350s (fixed TCP) | N/A |

### ELB (Classic) — Legacy, Avoid for New Deployments

```text
Still in use at many companies but deprecated by AWS.
Key limitations:
  - One SSL certificate per ELB (no SNI)
  - No path-based routing (all backends must serve all paths)
  - Fixed idle timeout (60s)
  - No WebSockets on HTTP/HTTPS listeners (need TCP listener)
```

### ALB — The Default for HTTP/HTTPS Workloads

```text
Key features:
  - Content-based routing: route /api/* to one TG, /admin/* to another
  - Host-based routing: api.example.com → TG-A, admin.example.com → TG-B
  - Slow start mode: gradually ramp traffic to new instances
  - Redirect and fixed response actions (no backend needed)
  - Built-in auth (OIDC, Cognito, SAML)
  - Lambda as a target (no instances!)
```

#### ALB Access Logs

```text
Format (one line per request):
  timestamp elb client:port target:port request_processing_time
  target_processing_time response_processing_time elb_status_code
  target_status_code received_bytes sent_bytes "request" "user_agent"
  ssl_cipher ssl_protocol target_group_arn "trace_id"

Key fields to grep for incident response:
  elb_status_code:    502, 503, 504—tells you if ALB is generating the error
  target_status_code:  -  means target didn't respond at all
  request_processing_time:  time LB spent waiting for client to send request
  target_processing_time:   time LB spent waiting for target to respond
                            (THIS is your backend latency)
  response_processing_time: time LB spent sending response to client
```

```bash
# Query ALB access logs (if in S3 + Athena)
SELECT
  COUNT(*) AS count,
  elb_status_code,
  target_status_code
FROM alb_logs
WHERE timestamp BETWEEN timestamp '2026-06-11 10:00' AND timestamp '2026-06-11 10:15'
GROUP BY elb_status_code, target_status_code
ORDER BY count DESC;
```

### NLB — For TCP/UDP/TLS, Ultra-Low Latency, Static IPs

```text
Key features:
  - Works at L4 — passes TCP/UDP packets through without inspecting HTTP
  - No ALB features (no routing, no auth, no WAF)
  - Static IP per AZ (assign Elastic IPs)
  - Preserves source IP (no X-Forwarded-For needed)
  - No idle timeout on TCP listeners (connection stays open forever)
  - PrivateLink: expose services across VPCs without IGW/NAT

When to use NLB over ALB:
  - Non-HTTP protocols (gaming, IoT, custom TCP protocols)
  - Need client source IP preserved for security/auditing
  - Need static IP addresses for firewall whitelisting
  - WebSocket at massive scale (millions of concurrent connections)
  - Latency-sensitive apps (HFT, real-time bidding)
```

---

## HAProxy Troubleshooting

### HAProxy Stats Socket

```bash
# Interactive stats
echo "show stat" | socat unix-connect:/var/run/haproxy.sock stdio | head -1
echo "show stat" | socat unix-connect:/var/run/haproxy.sock stdio | column -t -s,

# Show only servers that are DOWN
echo "show stat" | socat unix-connect:/var/run/haproxy.sock stdio \
  | grep DOWN

# Show errors for a specific backend
echo "show errors" | socat unix-connect:/var/run/haproxy.sock stdio

# Show current sessions
echo "show sess" | socat unix-connect:/var/run/haproxy.sock stdio | head -30

# Show compiled info (version, settings)
echo "show info" | socat unix-connect:/var/run/haproxy.sock stdio
```

### HAProxy Log Format Decoded

```text
haproxy[12345]: 192.168.1.100:54321 [11/Jun/2026:10:15:30.123]
  myfrontend mybackend/myserver
  0/0/0/245/245 200 1234 - - ---- 2/2/0/1/0 0/0
  "GET /api/v1/users HTTP/1.1"

Timing breakdown (Tq/Tw/Tc/Tr/Tt):
  Tq (0ms):   Time to get the client request (time spent reading HTTP headers)
              If Tq is high → client is sending slowly
  Tw (0ms):   Time spent in backend queue (waiting for a connection slot)
              If Tw is high → backend pool exhausted, need more servers
  Tc (0ms):   Time to connect to backend (TCP handshake)
              If Tc is high → backend network latency or TCP issues
  Tr (245ms): Server response time (time backend spent processing)
              THIS IS THE MOST IMPORTANT — actual backend latency
  Tt (245ms): Total time (Tq+Tw+Tc+Tr) — end-to-end client experience

Backend disconnect reason flags (----):
  ----   No special status
  C--    Client closed connection
  -S-    Server closed connection
  --N    No session (timeout)
  --D    Session killed during soft-stop (draining)
```

### Common HAProxy Error Scenarios

```text
ERROR: "Server mybackend/myserver is DOWN, reason: Layer4 connection problem"

MEANS:  TCP connection to backend failed (SYN didn't get SYN-ACK)
        - Backend process not running
        - Wrong port
        - Security group/firewall blocking LB → backend
        - Backend listen queue full (kernel dropping SYNs)

FIX:    nc -vz backend-ip 8080  from the HAProxy host

───────────────────────────────────────────────────────────────

ERROR: "Server mybackend/myserver is DOWN, reason: Layer4 timeout"

MEANS:  TCP connection to backend timed out (never completed handshake)
        - Backend in different network without route
        - Backend firewall silently dropping packets (no RST)

FIX:    traceroute backend-ip; tcpdump -i eth0 host backend-ip and port 8080

───────────────────────────────────────────────────────────────

ERROR: "Server mybackend/myserver is DOWN, reason: Layer7 wrong status"

MEANS:  HTTP health check returned non-2xx/3xx status
        - Health check endpoint returning 500
        - Backend app not fully started

FIX:    curl -v http://backend-ip:8080/healthz

───────────────────────────────────────────────────────────────

ERROR: "srv mybackend reached maxconn, all sessions queueing"

MEANS:  All backend connections are busy, new requests queue up.
        Check the backend's maxconn setting and actual connection count.
        If Tw (queue time) starts climbing, users experience latency.

FIX:    Increase maxconn on backend, or add more backends.
        maxconn should be based on: (available RAM / request memory) * instances
```

---

## Nginx Upstream Troubleshooting

### Common Nginx Upstream Error Messages

#### "upstream timed out (110: Connection timed out)"

```text
MEANS: Nginx waited proxy_read_timeout seconds for backend to respond
       but got nothing.

Nginx config:
  proxy_connect_timeout 5s;   # Time to establish TCP to backend
  proxy_read_timeout 60s;     # Time to wait for BACKEND RESPONSE
  proxy_send_timeout 60s;     # Time to wait for sending to backend

DIAGNOSIS:
  1. Is the backend actually slow, or is nginx timeout too short?
     $ time curl http://backend:8080/
     Compare to proxy_read_timeout.

  2. Is the backend hanging/crashing?
     $ strace -p <backend-pid> -e trace=read,write
     See if it's stuck on a read() syscall (waiting for DB, etc.)

  3. Is the backend accepting but not processing (queue full)?
     $ ss -tnp | grep :8080 | wc -l
     If Recv-Q has backlog, nginx sent request but backend hasn't read it

FIX:
  If backend is legitimately slow: increase proxy_read_timeout
  If backend is stuck/crashing: fix the backend
  Quick mitigation: increase upstream server count to spread load
```

#### "connect() failed (111: Connection refused)"

```text
MEANS: Nginx tried to connect to backend but backend actively refused
       the TCP connection (sent RST).

CAUSES:
  - Backend process is not running
  - Backend is running but not listening on the configured port
  - Backend listen queue full (kernel parameter net.core.somaxconn)

DIAGNOSIS:
  $ ss -tlnp | grep :8080
  → Is a process listening? On which IP (0.0.0.0 vs 127.0.0.1)?

  $ systemctl status myapp
  → Is the service running? Crashed? Exited?

FIX:
  - Start/restart the backend process
  - Verify port in nginx config matches backend's listening port
  - If listen queue is full: increase net.core.somaxconn on backend
```

#### "no live upstreams"

```text
MEANS: All servers in the upstream block are marked as down/failed.
       Nginx has nowhere to route requests.

CAUSES:
  - All backends failed health checks (if using health_check module)
  - All backends have max_fails reached (see below)
  - All backends explicitly marked as 'down' in config
  - DNS resolution of upstream names failed

CHECK:
  $ nginx -T 2>/dev/null | grep -A10 "upstream backend"
    upstream backend {
        server 10.0.1.10:8080 max_fails=3 fail_timeout=30s;
        server 10.0.1.11:8080 max_fails=3 fail_timeout=30s;
    }

  With max_fails=3 and fail_timeout=30s:
    If backend fails 3 health checks within 30 seconds,
    Nginx marks it as unavailable for fail_timeout seconds.
    If ALL backends hit this limit → no live upstreams.

FIX:
  - Increase max_fails temporarily if backends are flapping
  - Reset failure counter: nginx -s reload
  - Check WHY backends are failing (health checks? requests?)
  - Add more backends or increase fail_timeout
```

#### "upstream prematurely closed connection"

```text
MEANS: Backend accepted the connection but closed it before sending
       a complete HTTP response.

CAUSES:
  - Backend process crashed (OOM, segfault)
  - Backend keepalive_timeout expired on an idle connection
    (Nginx was holding it open in a connection pool)
  - Backend's max_execution_time exceeded, process killed
  - Backend sent response but used HTTP/1.0 with wrong Content-Length
    (connection closed before client got all data)

DIAGNOSIS:
  $ dmesg -T | grep -i "out of memory\|killed process"
  → OOM killer killed the backend mid-request?

  $ journalctl -u myapp --since "5 minutes ago" | grep -i error
  → Backend logs for crashes/timeouts

  Backend config for PHP-FPM:
    request_terminate_timeout = 30s    # Kills process after 30s
    If nginx's proxy_read_timeout > 30s → backend kills first → nginx sees close
```

### Nginx Upstream Health Monitoring Script

```bash
#!/bin/bash
# nginx-upstream-health.sh — check all upstream servers in nginx config
# Usage: ./nginx-upstream-health.sh

set -euo pipefail

# Extract upstream servers from nginx config
NGINX_CONFIG="${1:-/etc/nginx/nginx.conf}"

echo "=== Nginx Upstream Health Check ==="
echo ""

# Parse upstream blocks and test each server
grep -E "^\s*server\s+" "$NGINX_CONFIG" | while read -r line; do
    # Extract IP:port from "server 10.0.0.1:8080;" style lines
    SERVER=$(echo "$line" | sed -n 's/.*server \([^ ;]\+\).*/\1/p')
    if [ -n "$SERVER" ]; then
        HOST="${SERVER%:*}"
        PORT="${SERVER#*:}"

        # TCP check
        if nc -z -w 2 "$HOST" "$PORT" 2>/dev/null; then
            TCP="✓"
        else
            TCP="✗"
        fi

        # HTTP check
        HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 \
            "http://${HOST}:${PORT}/" 2>/dev/null || echo "000")
        if [ "$HTTP_CODE" != "000" ]; then
            HTTP="✓ ($HTTP_CODE)"
        else
            HTTP="✗"
        fi

        printf "  %-30s TCP: %s  HTTP: %s\n" "$SERVER" "$TCP" "$HTTP"
    fi
done
```

---

## References

- [AWS ALB Troubleshooting Guide](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-troubleshooting.html)
- [AWS NLB Troubleshooting Guide](https://docs.aws.amazon.com/elasticloadbalancing/latest/network/load-balancer-troubleshooting.html)
- [HAProxy Log Format — Official Documentation](https://www.haproxy.com/documentation/haproxy-configuration-manual/latest/#8.2.3)
- [HAProxy Management Guide (stats socket)](https://www.haproxy.com/documentation/haproxy-configuration-manual/latest/#9.2)
- [Nginx Upstream Module Documentation](https://nginx.org/en/docs/http/ngx_http_upstream_module.html)
- [Nginx Admin Guide — Load Balancing](https://docs.nginx.com/nginx/admin-guide/load-balancer/http-load-balancer/)
