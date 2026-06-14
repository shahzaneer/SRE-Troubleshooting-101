# Cascading Failure Runbook

> **Category:** On-Call | Architecture | Critical
> **Difficulty:** Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#runbook` `#cascade` `#resilience` `#oncall`

---

## 1. RECOGNIZE A CASCADE

A cascading failure is not just "one service is down." The hallmark is **multiple unrelated services failing simultaneously** in a chain reaction.

**Signs of a cascade:**

| Symptom | What You'll See |
|---------|----------------|
| Multiple service alerts fire within minutes | PagerDuty floods with alerts from different services |
| Error rate spiking across service boundaries | Service A → 503 → Service B times out calling A → Service C's connection pool fills up waiting for B |
| Dashboard shows red across the dependency graph | All services downstream of the initial failure show red |
| "Everything is on fire" feeling | Not one bottleneck — multiple bottlenecks |
| Same timestamp for initial degradation | Check alert timelines — one service failed first, the rest followed |

---

## 2. STOP THE BLEEDING — Break the Chain

### Step 1: Identify the Source (First Domino)

```bash
# Check alert timelines — which service reported failure FIRST?
# PagerDuty / Opsgenie → sort alerts by time. The earliest alert is the source.

# Check deployment times — did Service A deploy, then fail, then Service B failed?
kubectl get events -n prod --sort-by='.lastTimestamp' | grep -iE "fail|error|restart|unhealthy" | head -20

# Check the dependency graph (from APM / service map):
# Datadog Service Map → find the service with the earliest latency spike.
# Grafana service graph → identify the root node.
```

### Step 2: Isolate — Break the Dependency Chain

If Service A's slowness is causing Service B to timeout, which causes Service C's thread pool to exhaust:

```
A (slow) → B (times out calling A) → C (connection pool full waiting for B)
```

**Break the chain by making B stop waiting for A:**

```bash
# Option 1: Circuit Breaker — force OPEN on the dependency:
# Hystrix (Java):
curl -X POST http://service-b:8080/actuator/hystrix.stream/forceOpen \
  -d '{"commandGroup":"ServiceA","commandKey":"callServiceA"}'

# Resilience4j:
curl -X POST http://service-b:8080/actuator/circuitbreakers/callServiceA \
  -H "Content-Type: application/json" \
  -d '{"transition":"OPEN"}'

# Option 2: Config-driven circuit breaker:
# If using a configuration service (Consul, ConfigMap):
consul kv put service/b/circuit-breaker/service-a/enabled true

# Option 3: Return stale/cached data instead of calling A:
# Flip the "useCacheForServiceA" feature flag ON.
```

### Step 3: Shed Load

Drop non-critical traffic to free capacity for core services.

```bash
# API Gateway / Nginx — return 503 for non-critical endpoints:
# Keep: /auth, /payments, /orders, /health
# Drop: /search, /recommendations, /analytics, /reports

# Nginx config update:
cat <<'EOF' > /etc/nginx/conf.d/load-shedding.conf
# Return 503 for non-critical paths during cascade
location /api/v1/search            { return 503; }
location /api/v1/recommendations   { return 503; }
location /api/v1/analytics         { return 503; }
location /api/v1/reports           { return 503; }
EOF
nginx -t && systemctl reload nginx

# ALB / API Gateway — use routing rules to shed non-critical traffic.
# Update listener rules to return fixed 503 response for non-critical paths.
```

### Step 4: Rate Limit Incoming Requests

```bash
# Nginx rate limiting:
# /etc/nginx/conf.d/rate-limit.conf
limit_req_zone $binary_remote_addr zone=cascade:10m rate=5r/s;
limit_req zone=cascade burst=10 nodelay;
# Reload: nginx -t && systemctl reload nginx

# Envoy rate limiting:
# Apply rate limit filter config to reduce incoming request rate.

# AWS WAF rate-based rule:
# Set aggressive rate limit (e.g., 100 requests/5min per IP)
```

---

## 3. CIRCUIT BREAKER ACTIVATION (Detailed)

### 3a. Hystrix (Legacy Spring Cloud)

```bash
# Dashboard URL: http://service:8080/hystrix
# OR via API:
# Force circuit open:
curl -X POST "http://service:8080/hystrix.stream" \
  -d "command=ServiceA.getOrders&type=HystrixCommand&group=ServiceAGroup" \
  -d "circuitBreaker.forceOpen=true"

# Force circuit closed (when recovering):
curl -X POST "http://service:8080/hystrix.stream" \
  -d "command=ServiceA.getOrders&type=HystrixCommand&group=ServiceAGroup" \
  -d "circuitBreaker.forceClosed=true"
```

### 3b. Resilience4j

```bash
# Spring Boot Actuator endpoint:
# Open circuit:
curl -X POST "http://service:8080/actuator/circuitbreakers/backendA" \
  -H "Content-Type: application/json" \
  -d '{"transition":"OPEN"}'

# Transition to half-open (try 1 request):
curl -X POST "http://service:8080/actuator/circuitbreakers/backendA" \
  -H "Content-Type: application/json" \
  -d '{"transition":"HALF_OPEN"}'

# List all circuit breakers and their state:
curl -s http://service:8080/actuator/circuitbreakers | jq '.circuitBreakers'
```

### 3c. Envoy / Istio

```bash
# Apply outlier detection and circuit breaking via EnvoyFilter or DestinationRule:
# Istio DestinationRule example (apply via kubectl):
cat <<EOF | kubectl apply -f -
apiVersion: networking.istio.io/v1beta1
kind: DestinationRule
metadata:
  name: service-a-circuit-breaker
  namespace: prod
spec:
  host: service-a.prod.svc.cluster.local
  trafficPolicy:
    connectionPool:
      tcp:
        maxConnections: 100
      http:
        http1MaxPendingRequests: 10
        maxRequestsPerConnection: 10
    outlierDetection:
      consecutive5xxErrors: 5
      interval: 10s
      baseEjectionTime: 30s
      maxEjectionPercent: 100
EOF
```

---

## 4. LOAD SHEDDING STRATEGY

### 4a. Identify Critical vs Non-Critical Traffic

| Priority | Traffic Type | Examples | Action |
|----------|-------------|----------|--------|
| **P0** | Core / Revenue | Auth, payments, order submission | Keep serving at all costs |
| **P1** | Essential | Order history, user profile, notifications | Keep if capacity allows |
| **P2** | Non-critical | Search, recommendations, analytics | Drop first |
| **P3** | Admin / Internal | Dashboards, reporting, batch jobs | Pause / disable |

### 4b. Implement Load Shedding

```bash
# Application-level (fastest):
# If your app has a concept of "feature flags" for load shedding:
# Turn off P2/P3 features via config.

# Nginx / proxy level:
# Map paths to shed:
cat <<'EOF' > /etc/nginx/conf.d/load-shedding.conf
map $request_uri $shed {
    default         0;
    ~^/api/v1/search           1;
    ~^/api/v1/recommendations  1;
    ~^/api/v1/analytics        1;
}

server {
    location / {
        if ($shed) {
            return 503;
        }
        proxy_pass http://backend;
    }
}
EOF
```

### 4c. Priority Queue at Entry Point

```
Users --> [Load Balancer / Gateway]
              |
              ├──> High Priority Queue (auth, payments) -- 70% capacity
              ├──> Normal Queue (orders, profile) -- 25% capacity
              └──> Low Priority Queue (search, reports) -- 5% capacity (drop when full)
```

### 4d. WARNING — Health Check 503s

> **Do NOT return 503 on the `/health` or `/healthcheck` endpoint.**
> Load balancers use health checks to determine if an instance is healthy.
> Returning 503 on health → LB marks instance as unhealthy → removes from rotation → **FEWER** instances serving traffic → cascade worsens.

---

## 5. PARTIAL DEGRADATION MODE

When you can't serve everything, serve what matters most.

| Feature | Safe to Disable? | How to Disable |
|---------|-----------------|----------------|
| Search indexing | Yes | Pause Kafka consumers |
| Recommendation engine | Yes | Feature flag off, return empty [] |
| Analytics / event tracking | Yes | Buffer to disk, flush later |
| Email notifications | Yes | Pause queue consumption |
| PDF/Report generation | Yes | Feature flag off |
| Mobile push notifications | Maybe | Pause if not critical |
| Authentication (login) | **NO** | Never disable — users can't access anything |
| Payment processing | **NO** | Never disable — direct revenue loss |
| Order placement | **NO** | Never disable — direct revenue loss |

---

## 6. RECOVERY ORDER

Bring services back in this order:

```
1.  Database (if restarted)
    ↓
2.  Authentication service
    ↓
3.  Core API (orders, payments, users)
    ↓
4.  Background workers (queue consumers)
    ↓
5.  Secondary services (search, recommendations)
    ↓
6.  Analytics / reporting
```

At each step: **wait for stabilization** (dashboard shows green for 5 minutes) before enabling the next service.

```bash
# Recovery checklist:
# Step 2: Authentication up? → curl -s https://auth.example.com/health
# Step 3: Core API healthy? → curl -s https://api.example.com/health
# Step 3: Smoke test: ./smoke-tests.sh prod --critical-only
# Step 4: Workers processing? → check queue depth decreasing
# Step 5: Enable secondary features → flip feature flags ON one at a time
# Monitor between each: are metrics stable? No new errors?
```

---

## 7. FULL-SYSTEM RESTART SEQUENCE (Last Resort)

If cascade is uncontrollable and partial recovery isn't working:

```bash
# WARNING: Complete outage. Only use when nothing else is working.
# Requires Incident Commander approval.

# Step 1: Stop ALL traffic at the edge:
# - Update Route 53 / DNS to return a static maintenance page
# - Or drop target group registration for all instances

# Step 2: Stop all application services:
kubectl scale deployment --all --replicas=0 -n prod

# Step 3: Restart database (if necessary):
aws rds reboot-db-instance --db-instance-identifier prod-db

# Step 4: Wait for DB to be available:
aws rds wait db-instance-available --db-instance-identifier prod-db

# Step 5: Start services from core outward:
kubectl scale deployment auth-service -n prod --replicas=3
# Wait: kubectl wait --for=condition=available deployment/auth-service -n prod
kubectl scale deployment api-server -n prod --replicas=5
# Wait: kubectl wait --for=condition=available deployment/api-server -n prod
kubectl scale deployment worker-service -n prod --replicas=3
# ... continue outward ...

# Step 6: Re-enable traffic:
# Update DNS to route back to load balancer
# Remove static maintenance page
```

---

## 8. VERIFY RECOVERY

```bash
# 1. All critical endpoints:
for ep in /health /v1/auth/login /v1/orders /v1/users; do
  echo -n "$ep: "
  curl -s -o /dev/null -w "%{http_code} (%{time_total}s)" "https://api.example.com${ep}"
  echo
done

# 2. All regions:
for region in us-east-1 us-west-2 eu-west-1; do
  echo "$region: $(curl -s -o /dev/null -w '%{http_code}' https://${region}.api.example.com/health)"
done

# 3. Smoke tests:
./smoke-tests.sh prod

# 4. Real user monitoring — RUM data / New Relic / Datadog RUM:
# Are real users successfully completing transactions?
```

---

## 9. ARCHITECTURAL FIXES (Post-Incident Priority)

These are the patterns that prevent cascades in the first place. File action items for each gap found.

| Pattern | What It Does | Implementation |
|---------|-------------|----------------|
| **Circuit Breaker** | Stops calling a failing service after threshold | Resilience4j, Hystrix, Envoy, Istio |
| **Bulkhead** | Isolates thread pools so one slow dependency doesn't starve others | Thread pool per dependency, connection pool per DB |
| **Timeout** | Every outbound call has a deadline | HTTP clients: connect timeout, read timeout |
| **Retry with Backoff** | Retry only up to a limit, with exponential backoff | `retry.maxAttempts=3`, `retry.backoff=exponential` |
| **Graceful Degradation** | Return partial results instead of failing | Feature-specific fallbacks (stale cache, defaults) |
| **Request Throttling** | Limit inbound requests to sustainable rate | Rate limiter at API gateway |
| **Health Check Independence** | Health check should be a trivial endpoint, not depend on DB | `/health` = "process alive", `/health/deep` = "all deps ok" |
| **Monitoring Dependency Graph** | Alert on % of circuit breakers open | Service graph in Datadog/Grafana |

---

## 10. POST-INCIDENT

```markdown
**Slack #incidents update:**
✅ RECOVERED — Cascading failure resolved.
Root cause: [Service X failure triggered chain reaction].
Services restarted in order: DB → Auth → API → Workers → Secondary.
Post-mortem scheduled: [date].
Architectural gaps identified: [circuit breakers missing on Service B→C, etc.]
```

- [ ] Post-mortem scheduled within 5 business days
- [ ] Circuit breaker implementation prioritized
- [ ] Bulkhead pattern added to affected services
- [ ] Timeout defaults reviewed across all service-to-service calls
- [ ] Load shedding playbook added to deployment docs
- [ ] Full-system restart procedure tested in staging

---

## ABORT CRITERIA

| Condition | Action | Timebox |
|-----------|--------|---------|
| Cascade expanding despite mitigations (more services joining the "on fire" list) | **Shed ALL non-critical traffic immediately.** Consider full-system restart. | Immediately |
| More than 3 services in cascade and not stabilizing | Page **all engineering teams** (not just on-call). | 10 min |
| Revenue-generating features (payments, orders) affected | **Escalate to Incident Commander + VP Engineering.** | Immediately |
| Full-system restart being considered | **Must get Incident Commander approval.** Communicate to all teams. | Before executing |
| On-call team overwhelmed (incident >60 min) | Request backup. Rotate incident commander. | 60 min |
| Unable to identify root cause of cascade | **Bring in architects + senior engineers.** Stop trying to fix symptoms. | 30 min |
