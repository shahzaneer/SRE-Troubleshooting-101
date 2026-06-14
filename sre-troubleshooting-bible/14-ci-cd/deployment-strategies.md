# Deployment Strategies
> **Category:** CI/CD | Deployment | Strategy
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#ci-cd` `#deployment` `#strategy`

---

## Decision Matrix

| Factor | Blue-Green | Canary | Rolling | Feature Flag |
|--------|------------|--------|---------|--------------|
| **Rollback time** | Seconds (flip LB) | Seconds (auto) | 5-10 min | Seconds (toggle) |
| **Infra cost during deploy** | 2x | Normal | Normal + surge | Normal |
| **DB schema changes** | Must be backward-compatible | Must be backward-compatible | Must be backward-compatible | Same code, no issue |
| **Requires service mesh?** | No | Helps, not required | No | No |
| **Stateful apps (WebSocket, etc.)** | Connection drop on switch | Connection drop if killed | Connection drop if killed | No impact |
| **Complexity** | Medium | High | Low | Medium |
| **Best for** | Critical services, DB migrations | All services (recommended) | Stateless, large clusters | New features, risky changes |

---

## Blue-Green Deployment

### Architecture

```
                    ┌─────────────┐
                    │  Load       │
Clients ───────────→│  Balancer   │
                    │             │
                    └──┬──────┬───┘
                       │      │
                 ┌─────▼┐ ┌───▼─────┐
                 │ BLUE │ │ GREEN   │  ← NEW version deployed here
                 │ v1.0 │ │ v1.1    │     (not serving traffic yet)
                 └──────┘ └─────────┘
                  ↑ Active   ↑ Inactive
```

### Process

1. Green environment is provisioned with the new version (same size as Blue)
2. Smoke tests run against Green (internal URL, not public)
3. If tests pass, the load balancer is updated to point to Green
4. Blue becomes idle. Keep it running for rollback window (15-30 min)
5. After verification: tear down Blue or keep it for the next deploy (flip roles)

### Nginx Blue-Green Switch

```nginx
# /etc/nginx/conf.d/app.conf
upstream app_backend {
    server blue.internal:8080;   # Currently active
}

# To switch to green:
# 1. Update upstream to: server green.internal:8080;
# 2. Reload: nginx -s reload
# Rollback: revert the upstream line and reload again

# AWS ALB version:
aws elbv2 modify-listener --listener-arn $LISTENER_ARN \
  --default-actions Type=forward,TargetGroupArn=$GREEN_TG_ARN
```

### Kubernetes Blue-Green with Services

```yaml
# Blue deployment (active)
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api-blue
spec:
  replicas: 10
  selector:
    matchLabels:
      app: api
      version: blue
  template:
    metadata:
      labels:
        app: api
        version: blue
    spec:
      containers:
        - name: api
          image: api:v1.0.0
---
# Green deployment (new, initially idle)
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api-green
spec:
  replicas: 10
  selector:
    matchLabels:
      app: api
      version: green
  template:
    metadata:
      labels:
        app: api
        version: green
    spec:
      containers:
        - name: api
          image: api:v1.1.0
---
# Service — points to the active version
apiVersion: v1
kind: Service
metadata:
  name: api
spec:
  selector:
    app: api
    version: blue   # ← Change to "green" to switch
  ports:
    - port: 80
      targetPort: 8080
```

### Switch and Rollback

```bash
# Deploy Green
kubectl apply -f deployment-green.yaml
kubectl wait --for=condition=ready pod -l version=green --timeout=300s

# Smoke test Green (port-forward or use a separate test service)
kubectl port-forward deployment/api-green 8081:8080 &
sleep 2
curl -f http://localhost:8081/health && echo "Green healthy" || { echo "Green FAILED"; kill %1; exit 1; }
kill %1

# Flip traffic to Green
kubectl patch service api -p '{"spec":{"selector":{"version":"green"}}}'

# Monitor for 15 minutes, then:
# ROLLBACK if needed (flip back to Blue):
kubectl patch service api -p '{"spec":{"selector":{"version":"blue"}}}'

# After verification, scale down Blue
kubectl scale deployment api-blue --replicas=0
```

### DB Migration Gotcha

```sql
-- WRONG: Rename column in migration — breaks old code
ALTER TABLE orders RENAME COLUMN total_amount TO amount_total;

-- RIGHT: Two-phase migration
-- Phase 1 (before deploy): Add new column, keep old one, dual-write both
ALTER TABLE orders ADD COLUMN amount_total DECIMAL(10,2);
-- Application writes to both columns, reads from total_amount (old)

-- Phase 2 (after all instances are new code): Stop writing old column, switch reads
-- Application reads from amount_total (new), writes only amount_total

-- Phase 3 (next deploy): Drop old column
ALTER TABLE orders DROP COLUMN total_amount;
```

---

## Canary Deployment

### Traffic Splitting Schedule

```
Phase 1:  1% traffic → 5 min watch → metrics check
Phase 2:  5% traffic → 5 min watch → metrics check
Phase 3: 10% traffic → 5 min watch → metrics check
Phase 4: 25% traffic → 10 min watch → metrics check
Phase 5: 50% traffic → 10 min watch → metrics check
Phase 6: 100% traffic
```

### Metrics to Watch at Each Phase

| Metric | Threshold | Action if Exceeded |
|--------|-----------|-------------------|
| Error rate (5xx) | > 0.1% (2x baseline) | Auto-rollback |
| p99 latency | > 150% of baseline | Pause canary, investigate |
| Throughput (req/s) | No change | Normal |
| CPU/memory | > 80% of limit | Pause canary |
| Crash loop | Any new crashes | Auto-rollback |
| Failed health checks | > 0 | Auto-rollback |

### Kubernetes Canary (Manual)

```bash
# Stable deployment: 10 replicas at v1.0
# Canary deployment: 2 replicas at v1.1
# Total: 12 pods, ~17% canary (2/12)

kubectl scale deployment api-stable --replicas=8
kubectl scale deployment api-canary --replicas=2

# Monitor canary logs vs stable logs for error rate
# If canary error rate > stable error rate → rollback
kubectl scale deployment api-canary --replicas=0
kubectl scale deployment api-stable --replicas=10

# If canary is healthy → promote
kubectl scale deployment api-canary --replicas=5
kubectl scale deployment api-stable --replicas=5
# ... eventually:
kubectl scale deployment api-canary --replicas=10
kubectl scale deployment api-stable --replicas=0
```

### Scenario: Canary Detects N+1 Regression

**Context:** New code changes query logic. QA passes with small dataset (50 records).

**Canary at 10% traffic:**
```
Metric:       Baseline (stable)    Canary (10%)
p50 latency:  12ms                 14ms       (+17%) ✓ OK
p99 latency:  45ms                 280ms      (+522%) ✗ ALERT
Error rate:   0.02%                0.02%       ✓ OK
Throughput:   850 req/s            855 req/s   ✓ OK
```

**Diagnosis:**
```bash
# Trace sampling shows the difference
# Stable code: GET /orders → 1 query (SELECT * FROM orders WHERE user_id = ?)
# Canary code: GET /orders → 1 + N queries
#   SELECT * FROM orders WHERE user_id = ?     (1 query)
#   SELECT * FROM order_items WHERE order_id = ?  (×50 items = 50 more queries)

# Fix: Eager load order_items with a JOIN
# SELECT o.*, oi.* FROM orders o LEFT JOIN order_items oi ON o.id = oi.order_id WHERE o.user_id = ?
```

---

## Rolling Deployment

### Kubernetes Rolling Update

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
spec:
  replicas: 10
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 2          # Create at most 2 extra pods above desired count
      maxUnavailable: 2    # Allow at most 2 pods to be unavailable
  # During deploy: up to 12 pods running, at least 8 pods ready
  template:
    spec:
      containers:
        - name: api
          image: api:v1.1.0
```

### Scenario: Rolling Deploy with Crash

```
Initial state:  10 pods (v1.0) — all healthy
Step 1:         Scale up 2 v1.1 pods (total: 12)
                New pods crash (CrashLoopBackOff)
Step 2:         maxUnavailable=2 → 2 old pods terminated
                Now: 8 healthy v1.0 + 2 crashed v1.1 = degraded (80% capacity)
Step 3:         maxSurge limit reached (2 extra) → no more new pods
                Rollout stuck at 8/10 ready

Recovery:
kubectl rollout undo deployment/api  # Roll back to v1.0
# Rolling update resumes: creates v1.0 pods, terminates crashed v1.1 pods
# Back to 10/10 healthy after ~2 minutes
```

### Graceful Shutdown Requirements

```bash
# The pod lifecycle during rolling update:
# 1. Pod gets SIGTERM
# 2. Pod enters "Terminating" state
# 3. Pod is removed from Service endpoints
# 4. Pod has terminationGracePeriodSeconds to finish in-flight requests
# 5. After grace period: SIGKILL

# Application MUST:
# - Handle SIGTERM (stop accepting new requests, finish current ones)
# - Be removed from endpoints before termination (preStop hook helps)
# - Have terminationGracePeriodSeconds > longest request timeout

# Example pod spec with proper graceful shutdown:
spec:
  terminationGracePeriodSeconds: 60  # 60 seconds to finish requests
  containers:
    - name: api
      lifecycle:
        preStop:
          exec:
            command: ["/bin/sh", "-c", "sleep 10"]  # Wait for endpoint removal to propagate
```

---

## Feature Flags

### Architecture

```
                    ┌──────────────┐
                    │ Feature Flag │
                    │  Service     │
                    │ (LaunchDarkly│
                    │  / Split /   │
                    │  ConfigCat)  │
                    └──────┬───────┘
                           │ Check flag
                    ┌──────▼───────┐
                    │ Application  │
                    │              │
                    │ if flag      │
                    │   new_code() │
                    │ else         │
                    │   old_code() │
                    └──────────────┘
```

### Code Example

```java
// Java — LaunchDarkly SDK
@Autowired private LDClient ldClient;

@GetMapping("/checkout")
public CheckoutResponse checkout(@RequestAttribute User user) {
    // Feature flag key: "new-payment-flow"
    // Targeting: 5% of users, specific beta testers, internal employees
    if (ldClient.boolVariation("new-payment-flow", buildLDUser(user), false)) {
        return newPaymentFlow(user);  // New code path
    }
    return oldPaymentFlow(user);  // Old code path (stable)
}

private LDUser buildLDUser(User user) {
    return new LDUser.Builder(user.getId())
        .email(user.getEmail())
        .custom("beta_tester", user.isInBetaProgram())
        .custom("organization", user.getOrganizationId())
        .build();
}
```

### Scenario: Feature Flag Saves the Day

**Context:** New checkout flow deployed Friday at 5 PM (behind a feature flag, OFF by default).

**Monday morning:** Enable flag for 1% of users. p99 latency jumps 3x. Error rate 0.5%.

**Response:** Turn flag OFF. Zero deployment. Bug fixed the following week.

**Without feature flags:** Friday deploy to 100% → bug discovered Monday → emergency rollback → 3 hours of degraded service + weekend incident.

### Feature Flag Hygiene

```java
// When removing a flag (after 100% rollout for 2 weeks):
// Step 1: Remove the flag check, keep only new code
public CheckoutResponse checkout(@RequestAttribute User user) {
    return newPaymentFlow(user);
}

// Step 2: Delete the flag from LaunchDarkly
// Step 3: Remove the flag key constant and unused imports
// Step 4: Add a ticket: "Remove new-payment-flow flag" in the cleanup sprint

// Anti-pattern: flag spaghetti
if (flagA && (flagB || (flagC && !flagD))) {
    // Nobody knows what this does. Delete it.
}
```

---

## Comparison: Scenario Walkthrough

**The same deploy — 4 different strategies:**

1. **Kubernetes Rolling Update:** `kubectl set image deployment/api api=api:v1.1.0` — gradually replaces pods. If new pods crash, you're at reduced capacity for 2-5 minutes during rollback. Good for routine deploys of stateless services.

2. **Blue-Green:** Deploy to idle Green environment. Smoke test. Flip the Service selector. New version is serving 100% traffic instantly. If it fails, flip back to Blue (seconds). Good for critical services where every second of degradation matters.

3. **Canary:** Deploy 2 canary pods, send 10% traffic. Watch metrics for 15 minutes. If p99 latency is 280ms vs 45ms baseline → auto-rollback. Promoted gradually. Best general-purpose strategy.

4. **Feature Flag:** Deploy code with both old and new paths. New path behind a flag (OFF). Deploy is zero-risk because nothing changes behaviorally. Enable flag for 1% users. If metrics bad, turn flag off. No redeploy needed. Best for risky behavioral changes.
