# Deployment Strategies
> **Category:** CI/CD | Deployment | Strategy
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-08
> **Tags:** `#ci-cd` `#deployment` `#strategy` `#release-engineering` `#rollback`

---

## What This Guide Solves

A deployment strategy is the controlled way a new version reaches production. The right strategy depends on workload type, blast radius, rollback speed, infrastructure cost, database compatibility, and how much downtime the business can tolerate.

The goal is not to use the most advanced strategy everywhere. The goal is to choose a deployment method where:

- Users do not see avoidable downtime.
- Bad releases are detected before full impact.
- Rollback is faster than the incident grows.
- Cost during deployment is known before the release starts.
- Stateful components, queues, database migrations, and long-lived connections are handled intentionally.

---

## Deployment Terms

| Term | Meaning |
|------|---------|
| **Deployment** | Shipping a new artifact or configuration into an environment. |
| **Release** | Exposing deployed behavior to users. A feature flag can deploy code today and release it next week. |
| **Rollback** | Returning traffic or runtime state to the previous known-good version. |
| **Fix-forward** | Shipping another change to repair the issue instead of returning to the previous version. |
| **Blast radius** | The percentage of users, requests, regions, tenants, or jobs affected by a bad change. |
| **Drain** | Stop sending new work to an instance while allowing in-flight work to finish. |
| **Surge capacity** | Extra temporary capacity created during deployment. |
| **Backward compatible change** | A change where old and new versions can run at the same time without breaking each other. |
| **User-visible downtime** | Time users cannot successfully use the service. This is different from reduced capacity. |

---

## Executive Decision Matrix

| Strategy | Best For | User Downtime | Deployment Cost | Rollback Speed | Main Risk |
|----------|----------|---------------|-----------------|----------------|-----------|
| **Recreate** | Internal tools, dev/staging, low-traffic apps | High, usually seconds to minutes | 1x baseline | Medium | Full outage during replacement |
| **Rolling** | Stateless services, routine releases | Usually zero if capacity is healthy | 1.0x to 1.3x | Minutes | Mixed versions and reduced capacity |
| **Blue-Green** | Critical services, monoliths, VM workloads | Near zero at traffic switch | Up to 2x during overlap | Seconds | Expensive duplicate environment |
| **Canary** | Most production services, high-risk code | Usually zero | 1.0x to 1.3x | Seconds to minutes | Needs good metrics and traffic routing |
| **Feature Flag** | Risky behavior changes, product experiments | Zero for disable/enable | 1x runtime, plus flag platform cost | Seconds | Flag debt and code path complexity |
| **Shadow Traffic** | Read-heavy validation, migrations, performance tests | Zero, because shadow does not serve users | 1.2x to 2x depending on duplicate traffic | Stop shadow instantly | Duplicate side effects if not isolated |
| **A/B Test** | Product experiments and UX comparison | Usually zero | 1x to 1.2x | Seconds if flag based | Business metrics can hide system regressions |
| **Immutable Infrastructure** | VM/AMI releases, regulated environments | Near zero with LB drain | 1.2x to 2x during replacement | Minutes | Slower provisioning and image pipeline failures |

Cost values are practical rules of thumb, not cloud-provider pricing. Use them to estimate relative cost during the deployment window.

---

## Workload-Based Selection Guide

| Workload | Recommended Strategy | Cost During Deploy | Downtime Expectation | Notes |
|----------|----------------------|--------------------|----------------------|-------|
| **Stateless HTTP API** | Rolling or Canary | 1.0x to 1.3x | Zero if readiness and capacity are correct | Use canary for risky releases, rolling for routine patches. |
| **Critical payment/auth service** | Canary + Feature Flags, or Blue-Green | 1.1x to 2x | Zero target | Prefer small blast radius and instant disable path. |
| **Legacy monolith on VMs** | Blue-Green or Immutable Infrastructure | 1.5x to 2x | Near zero at LB switch | Duplicate environment is often safer than in-place mutation. |
| **Small internal admin app** | Rolling or Recreate | 1x | Recreate may cause short downtime | Use recreate only when the business accepts interruption. |
| **WebSocket or streaming API** | Rolling with connection draining, or Canary | 1.1x to 1.5x | No outage, but old connections may disconnect | Need graceful shutdown and client reconnect behavior. |
| **Kafka/SQS/RabbitMQ consumers** | Rolling or Canary by consumer group | 1x to 1.3x | No user outage, but backlog may grow | Watch lag, retry rate, DLQ, and processing latency. |
| **Batch jobs / cron workloads** | Immutable job image, Shadow run, or Blue-Green scheduler | 1x to 2x per overlapping run | No online downtime | Prevent duplicate job execution with locks/idempotency. |
| **Stateful application nodes** | Rolling with quorum rules | 1.1x to 1.5x | Zero only if quorum is maintained | Respect leader election, replication, and minimum available nodes. |
| **Databases** | Expand-contract migration | 1x to 2x depending on replicas/backfill | Usually zero for compatible migrations | Never depend on application rollback to undo destructive schema changes. |
| **Mobile-backed APIs** | Feature Flags + Backward Compatible API | 1x | Zero target | Old mobile clients can live for months; never break old contracts abruptly. |
| **ML model serving** | Canary + Shadow evaluation | 1.2x to 2x | Zero target | Compare latency, cost per inference, accuracy, and business guardrails. |
| **CDN/static frontend** | Immutable versioned assets + gradual cache purge | 1x to 1.1x | Usually zero | Keep old assets available until all HTML references expire. |
| **Serverless functions** | Weighted alias/canary release | 1x to 1.2x | Usually zero | Watch cold starts, concurrency, throttles, and downstream limits. |

---

## Cost Model

Deployment cost has two parts:

```text
Total deployment cost =
  baseline runtime cost
+ temporary extra compute
+ temporary extra storage/network
+ duplicate third-party/API usage
+ observability and testing overhead
+ engineering/on-call time
```

Use this quick estimate before choosing a strategy:

```text
Temporary compute cost =
  extra_instances * cost_per_instance_per_hour * deployment_hours

Blue-green overlap cost =
  baseline_hourly_cost * overlap_hours

Canary extra cost =
  extra_canary_capacity * cost_per_unit * rollout_hours

Shadow traffic cost =
  duplicate_request_rate * cost_per_request * shadow_hours
```

Example:

```text
Service baseline: 10 pods
Approx pod cost: $0.08 per pod-hour
Deployment window: 1 hour

Rolling with maxSurge=2:
  Extra cost = 2 * $0.08 * 1 = $0.16

Blue-green for 1 hour:
  Extra cost = 10 * $0.08 * 1 = $0.80

Shadow traffic at 100% for 1 hour:
  Compute can approach 2x.
  Downstream API, database, cache, logging, and tracing cost may also approach 2x.
```

For high-volume services, the hidden cost is often not compute. It is duplicate database reads, cache misses, third-party API calls, logs, metrics, traces, and queue messages.

---

## Downtime Model

There are three different failure modes people casually call "downtime":

| Type | Meaning | Example |
|------|---------|---------|
| **Hard downtime** | Users cannot reach the service. | Recreate deployment stops all pods before new pods are ready. |
| **Soft downtime** | Service is reachable but too slow or error-prone. | Rolling deploy reduces capacity and p99 latency exceeds SLO. |
| **Functional downtime** | Service is up, but a feature is broken. | Checkout page loads, but payment authorization fails. |

Estimate downtime using:

```text
Expected downtime =
  traffic_switch_time
+ readiness_gap
+ connection_drain_gap
+ rollback_detection_time
+ rollback_execution_time
```

The largest number is usually not the technical rollback. It is detection time. Canary and feature flags reduce blast radius because they let you detect failure before 100% traffic is impacted.

---

## Strategy 1: Recreate Deployment

### Definition

Recreate deployment shuts down the old version first, then starts the new version. Only one version runs at a time.

```text
Before: users -> v1
Deploy: users -> no healthy instance
After:  users -> v2
```

### When To Use

- Development, staging, demo, or low-criticality internal systems.
- Single-instance applications where downtime is explicitly acceptable.
- Workloads that cannot safely run two versions at once and have a planned maintenance window.
- Small admin apps where a short interruption is cheaper than maintaining deployment complexity.

### Avoid When

- Public user traffic must remain available.
- The workload is revenue-critical.
- Startup time is slow or unpredictable.
- The application has long-running requests or connections.
- Rollback requires manual investigation.

### Cost

| Cost Area | Impact |
|-----------|--------|
| Compute | Lowest, usually 1x baseline. |
| Storage | No duplicate environment. |
| Operational cost | Low automation complexity, high incident risk if used on critical systems. |
| Hidden cost | User interruption and support tickets. |

### Downtime

Downtime equals the time between stopping the old version and the new version becoming ready.

```text
Downtime = stop_time + startup_time + readiness_time + failed_attempt_recovery
```

If startup takes 90 seconds and readiness takes 30 seconds, expect at least 2 minutes of hard downtime.

### Kubernetes Example

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: internal-admin
spec:
  replicas: 1
  strategy:
    type: Recreate
  template:
    spec:
      containers:
        - name: app
          image: internal-admin:v2.0.0
```

### Rollback

```bash
kubectl rollout undo deployment/internal-admin -n internal
kubectl rollout status deployment/internal-admin -n internal --timeout=5m
```

### SRE Notes

- Announce a maintenance window if users depend on the tool.
- Use only when outage is acceptable.
- Keep a tested rollback command ready before starting.

---

## Strategy 2: Rolling Deployment

### Definition

Rolling deployment replaces old instances with new instances gradually. Some old and new versions run at the same time.

```text
Step 1: 10 old, 0 new
Step 2:  8 old, 2 new
Step 3:  6 old, 4 new
Step 4:  0 old, 10 new
```

### When To Use

- Stateless HTTP/gRPC services.
- Routine releases with low schema risk.
- Services with good readiness/liveness probes.
- Clusters with enough spare capacity for surge pods.
- Applications that support graceful shutdown.

### Avoid When

- Old and new versions cannot run together.
- The deployment includes destructive database changes.
- The application has poor readiness checks.
- Startup is slow and capacity is already tight.
- Long-lived sessions cannot reconnect.

### Cost

| Cost Area | Impact |
|-----------|--------|
| Compute | Usually 1x to 1.3x during deploy because of surge capacity. |
| Load balancer | No duplicate LB required. |
| Observability | Normal to slightly higher due to mixed-version analysis. |
| Engineering | Low complexity if platform supports it natively. |

Cost example:

```text
Desired replicas: 20
maxSurge: 25% = 5 extra pods
Deployment overlap: 30 minutes

Temporary compute cost = 5 pod-units * 0.5 hours
```

### Downtime

Rolling deploys should have zero hard downtime when:

- `maxUnavailable` does not reduce capacity below required traffic load.
- New pods are marked ready only when they can serve real traffic.
- Old pods drain before termination.
- Pod disruption budgets and autoscaling are configured correctly.

Soft downtime can happen if capacity drops during the rollout.

```text
Available capacity =
  ready_old_instances + ready_new_instances - draining_instances
```

If traffic requires 9 pods and `maxUnavailable` allows the service to run with only 8 ready pods, users may see latency or errors even though the deployment is "healthy" from Kubernetes' point of view.

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
      maxSurge: 2
      maxUnavailable: 1
  minReadySeconds: 20
  progressDeadlineSeconds: 600
  template:
    spec:
      terminationGracePeriodSeconds: 60
      containers:
        - name: api
          image: api:v1.1.0
          ports:
            - containerPort: 8080
          readinessProbe:
            httpGet:
              path: /ready
              port: 8080
            periodSeconds: 5
            failureThreshold: 3
          livenessProbe:
            httpGet:
              path: /health
              port: 8080
            periodSeconds: 10
```

### Safe Rolling Commands

```bash
# Start rollout
kubectl set image deployment/api api=api:v1.1.0 -n prod

# Watch status
kubectl rollout status deployment/api -n prod --timeout=10m

# Check mixed versions
kubectl get pods -n prod -l app=api \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.containers[0].image}{"\t"}{.status.containerStatuses[0].ready}{"\n"}{end}'

# Rollback
kubectl rollout undo deployment/api -n prod
kubectl rollout status deployment/api -n prod --timeout=10m
```

### Rolling Deployment Failure Scenario

```text
Initial state:
  10 pods v1.0, all healthy

Rollout config:
  maxSurge=2
  maxUnavailable=2

Failure:
  2 new v1.1 pods start but crash.
  Kubernetes also allows 2 old pods to be unavailable.

Result:
  8 healthy old pods
  2 crashed new pods
  Service has only 80% capacity
```

Recovery:

```bash
kubectl rollout undo deployment/api -n prod
kubectl describe deployment/api -n prod
kubectl get events -n prod --sort-by='.lastTimestamp' | tail -30
```

### Graceful Shutdown Requirements

Applications must handle termination cleanly:

```text
1. Pod receives SIGTERM.
2. Pod stops accepting new requests.
3. Pod is removed from Service endpoints.
4. Pod finishes in-flight requests.
5. Pod exits before terminationGracePeriodSeconds.
```

Example:

```yaml
spec:
  terminationGracePeriodSeconds: 60
  containers:
    - name: api
      lifecycle:
        preStop:
          exec:
            command: ["/bin/sh", "-c", "sleep 10"]
```

### SRE Notes

- Good default for normal stateless services.
- Use conservative `maxUnavailable` for high-traffic services.
- Do not use rolling as a substitute for schema compatibility.
- Combine with canary when the release is risky.

---

## Strategy 3: Blue-Green Deployment

### Definition

Blue-green deployment runs two complete environments:

- **Blue:** current production.
- **Green:** new version, fully deployed but not yet receiving production traffic.

Traffic switches from Blue to Green after validation.

```text
               +----------------+
Users -------> | Load Balancer  |
               +-------+--------+
                       |
             active    |    idle/tested
                    +--v--+   +-----+
                    |Blue |   |Green|
                    |v1.0 |   |v1.1 |
                    +-----+   +-----+
```

After switch:

```text
Users -> Load Balancer -> Green v1.1
Blue remains available for rollback
```

### When To Use

- Critical services where rollback must be nearly instant.
- Monoliths that are hard to roll gradually.
- VM, AMI, or image-based infrastructure.
- Releases needing full-environment smoke tests before traffic.
- Systems where temporary 2x cost is cheaper than outage risk.

### Avoid When

- The database cannot support both versions.
- Duplicate environments are too expensive.
- External dependencies cannot tolerate duplicate connections.
- There is large state stored locally on instances.
- Traffic switching can break long-lived sessions without drain.

### Cost

| Cost Area | Impact |
|-----------|--------|
| Compute | Up to 2x while both environments run. |
| Database | Usually shared, but may need extra read replicas or migration capacity. |
| Load balancer | May need extra target groups or routing rules. |
| Cache | May duplicate warmup cost. |
| Observability | More metrics/log streams during overlap. |

Cost depends on how long Blue stays running after Green becomes active.

```text
Baseline hourly cost: $500/hour
Green pre-warm and validation: 30 minutes
Blue rollback window after switch: 60 minutes

Extra blue-green cost:
  $500/hour * 1.5 hours = $750
```

Keeping Blue for a rollback window is usually worth it for critical services. Keeping it forever doubles steady-state cost and should be intentional.

### Downtime

Hard downtime is usually near zero because only routing changes. Actual user impact depends on:

- Load balancer propagation time.
- DNS TTL if switching by DNS.
- Connection drain behavior.
- Session affinity and sticky sessions.
- Whether Green is fully warmed before traffic.

Avoid DNS-only blue-green for urgent rollback unless TTLs are very low and clients respect them. Load balancer target group switching is usually faster and more predictable.

### Kubernetes Blue-Green Example

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api-blue
spec:
  replicas: 10
  selector:
    matchLabels:
      app: api
      track: blue
  template:
    metadata:
      labels:
        app: api
        track: blue
    spec:
      containers:
        - name: api
          image: api:v1.0.0
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api-green
spec:
  replicas: 10
  selector:
    matchLabels:
      app: api
      track: green
  template:
    metadata:
      labels:
        app: api
        track: green
    spec:
      containers:
        - name: api
          image: api:v1.1.0
---
apiVersion: v1
kind: Service
metadata:
  name: api
spec:
  selector:
    app: api
    track: blue
  ports:
    - port: 80
      targetPort: 8080
```

Switch:

```bash
# Wait for Green
kubectl wait --for=condition=available deployment/api-green -n prod --timeout=5m

# Smoke test Green through an internal route or port-forward
kubectl port-forward deployment/api-green 8081:8080 -n prod &
PF_PID=$!
curl -fsS http://127.0.0.1:8081/health
kill "$PF_PID"

# Switch traffic from Blue to Green
kubectl patch service api -n prod \
  -p '{"spec":{"selector":{"app":"api","track":"green"}}}'

# Rollback if needed
kubectl patch service api -n prod \
  -p '{"spec":{"selector":{"app":"api","track":"blue"}}}'
```

### AWS ALB Target Group Switch

```bash
aws elbv2 modify-listener \
  --listener-arn "$LISTENER_ARN" \
  --default-actions Type=forward,TargetGroupArn="$GREEN_TG_ARN"

# Rollback
aws elbv2 modify-listener \
  --listener-arn "$LISTENER_ARN" \
  --default-actions Type=forward,TargetGroupArn="$BLUE_TG_ARN"
```

### SRE Notes

- Pre-warm caches and JIT/runtime paths before switch.
- Confirm Green has production config, secrets, IAM, network policies, and autoscaling.
- Keep Blue alive for a defined rollback window, commonly 15 to 60 minutes.
- Never run destructive schema changes at the same time as a blue-green app switch.

---

## Strategy 4: Canary Deployment

### Definition

Canary deployment sends a small percentage of traffic to the new version, watches health signals, then gradually increases exposure.

```text
Phase 1:  1% new, 99% old
Phase 2:  5% new, 95% old
Phase 3: 25% new, 75% old
Phase 4: 50% new, 50% old
Phase 5: 100% new
```

### When To Use

- Most production services.
- High-traffic APIs where a small percentage provides useful signal.
- Risky code changes that may only fail under real production traffic.
- Services with strong metrics, logs, tracing, and automated rollback.
- ML model releases where live distribution matters.

### Avoid When

- Traffic is too low for a 1% or 5% canary to be meaningful.
- Requests are not safely routable by percentage, header, tenant, or region.
- The new version produces irreversible side effects.
- Monitoring cannot compare old vs new versions.
- Old and new versions cannot share the database or queue safely.

### Cost

| Cost Area | Impact |
|-----------|--------|
| Compute | Usually 1.0x to 1.3x if canary adds replicas. |
| Routing | May require service mesh, ingress controller, ALB weighted rules, or progressive delivery tool. |
| Observability | Higher cost due to per-version dashboards, metrics, traces, and alerts. |
| Engineering | Higher setup complexity than rolling. |

Canary cost is usually lower than blue-green because only a small slice of extra capacity is needed. The main cost is operational maturity: metrics, automation, and reliable rollback rules.

### Downtime

Canary should have zero hard downtime. The benefit is reduced blast radius:

```text
Bad full release impact:
  100% users affected until rollback

Bad 5% canary impact:
  5% users affected until rollback
```

Rollback speed depends on routing:

- Feature flag or service mesh: seconds.
- Kubernetes scale-down: seconds to minutes.
- DNS-based canary: usually too slow for urgent rollback.

### Practical Rollout Schedule

| Phase | Traffic | Watch Time | Required Signal |
|-------|---------|------------|-----------------|
| 1 | 1% | 5-10 min | No crash loops, no obvious 5xx spike. |
| 2 | 5% | 10-15 min | p95/p99 latency within threshold. |
| 3 | 10% | 15 min | Error budget burn is acceptable. |
| 4 | 25% | 15-30 min | Downstream dependencies stable. |
| 5 | 50% | 30 min | Business metrics and infra metrics stable. |
| 6 | 100% | Monitor for 1-2 hours | Remove old version after rollback window. |

High-risk systems can add tenant-based or region-based phases before percentage rollout.

### Metrics To Watch

| Metric | Example Threshold | Action |
|--------|-------------------|--------|
| 5xx error rate | >2x baseline or >0.1% absolute | Roll back or pause. |
| p99 latency | >150% baseline for 5 minutes | Pause and investigate. |
| Saturation | CPU/memory >80% limit | Pause and scale or rollback. |
| CrashLoopBackOff | Any new crash loop | Roll back. |
| Dependency errors | DB/cache/API errors above baseline | Pause. |
| Queue lag | Lag grows continuously for 10 minutes | Pause or rollback. |
| Business KPI | Checkout/auth/search success drops | Roll back. |
| Error budget burn | Burn rate violates policy | Roll back. |

### Kubernetes Manual Canary

```bash
# Stable deployment: 10 replicas at v1.0
# Canary deployment: 1 replica at v1.1
# Approx canary share if both select behind same Service: 1/11 = 9%

kubectl scale deployment/api-stable --replicas=10 -n prod
kubectl scale deployment/api-canary --replicas=1 -n prod

# Watch canary pods
kubectl get pods -n prod -l app=api,track=canary -w

# Rollback canary
kubectl scale deployment/api-canary --replicas=0 -n prod

# Promote gradually
kubectl scale deployment/api-canary --replicas=3 -n prod
kubectl scale deployment/api-stable --replicas=8 -n prod
```

Replica-based canary is approximate because traffic is distributed by endpoints, connections, and load balancer behavior. For precise percentage rollout, use an ingress controller, service mesh, or progressive delivery controller.

### Ingress Weighted Canary Example

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: api-canary
  annotations:
    nginx.ingress.kubernetes.io/canary: "true"
    nginx.ingress.kubernetes.io/canary-weight: "5"
spec:
  rules:
    - host: api.example.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: api-canary
                port:
                  number: 80
```

### Canary Failure Scenario

```text
Context:
  New code changes order query logic.
  QA passed with a small dataset.

At 10% canary:
  Stable p99 latency: 45ms
  Canary p99 latency: 280ms
  Stable 5xx: 0.02%
  Canary 5xx: 0.02%

Decision:
  Pause or rollback despite no 5xx spike.

Likely issue:
  N+1 query appears only with production-sized accounts.
```

### SRE Notes

- Canary without metrics is just a slow rollout.
- Use version labels in metrics: `version`, `release`, `track`, or `git_sha`.
- Keep old version running until 100% new version passes the rollback window.
- Define rollback thresholds before deployment starts.

---

## Strategy 5: Feature Flags

### Definition

Feature flags separate deployment from release. Code is deployed to production, but behavior is enabled only for selected users, tenants, regions, or percentages.

```text
Deploy:
  Code path exists in production.
  Flag is OFF.

Release:
  Enable flag for internal users.
  Enable for 1%.
  Enable for 10%.
  Enable for 100%.
```

### When To Use

- Risky behavior changes.
- Product experiments.
- Gradual tenant or region enablement.
- Kill switches for expensive or fragile code paths.
- Mobile/API compatibility where clients update slowly.
- Migrations where new reads/writes must be phased.

### Avoid When

- The flag controls deep architectural behavior without clear ownership.
- The old and new code paths cannot both be tested.
- The team has no process to remove stale flags.
- The change is a simple bug fix that does not need runtime control.

### Cost

| Cost Area | Impact |
|-----------|--------|
| Runtime | Usually 1x. Both code paths exist, but one may be inactive. |
| Platform | May include feature flag service cost. |
| Engineering | Adds testing matrix and cleanup responsibility. |
| Observability | Need metrics split by flag state. |

Feature flags are cheap operationally when flags are short-lived. They become expensive when stale flags create permanent branching logic.

### Downtime

Turning a flag off is usually a zero-downtime rollback. It is often the fastest mitigation available.

```text
Bad deployment without flag:
  rollback artifact -> restart services -> wait for rollout

Bad behavior behind flag:
  disable flag -> clients return to old path
```

### Code Example

```java
@GetMapping("/checkout")
public CheckoutResponse checkout(@RequestAttribute User user) {
    if (flags.enabled("new-payment-flow", user.getId())) {
        return newPaymentFlow(user);
    }

    return oldPaymentFlow(user);
}
```

### Flag Rollout Pattern

| Stage | Audience | Purpose |
|-------|----------|---------|
| 1 | Developers/internal users | Catch obvious breakage. |
| 2 | One test tenant | Validate real production configuration. |
| 3 | 1% random users | Detect broad issues with low blast radius. |
| 4 | 10% to 50% | Validate scale and downstream load. |
| 5 | 100% | Complete release. |
| 6 | Cleanup | Remove old path and delete flag. |

### Flag Hygiene

Every production flag needs:

- Owner.
- Creation date.
- Expiry or cleanup date.
- Default value.
- Rollback behavior.
- Metrics split by enabled/disabled.
- Test coverage for both paths while both paths exist.

Anti-pattern:

```java
if (flagA && (flagB || (flagC && !flagD))) {
    runNewPaymentAndDiscountAndTaxPath();
}
```

This is operational debt. Complex flag combinations should be temporary, documented, and deleted quickly.

### SRE Notes

- Feature flags protect behavior, not infrastructure startup failures.
- Keep kill switches separate from experiment flags.
- Cache flag values carefully. A flag service outage should not take down the app.
- Default to the safest behavior if the flag provider is unreachable.

---

## Strategy 6: Shadow Traffic

### Definition

Shadow traffic duplicates real production requests to a new version, but responses from the new version are not returned to users.

```text
User request -> Stable service -> User response
             \
              -> Shadow service -> Response discarded
```

### When To Use

- Performance validation under real request shapes.
- ML model comparison.
- Search ranking changes.
- Read-heavy API rewrites.
- Database migration validation.
- New service replacing a legacy service.

### Avoid When

- Requests create side effects such as charges, emails, writes, or notifications.
- The shadow service calls paid third-party APIs without controls.
- Duplicate traffic can overload downstream dependencies.
- Response comparison is not defined.

### Cost

| Cost Area | Impact |
|-----------|--------|
| Compute | Can approach 2x if mirroring 100% of traffic. |
| Database/cache | Can approach 2x read load. |
| Third-party APIs | Can become very expensive if not stubbed or blocked. |
| Logs/traces | Often double unless sampled. |

Shadow traffic is one of the safest user-impact strategies and one of the easiest ways to accidentally double backend cost.

### Downtime

User-visible downtime is zero because shadow responses are discarded. Risk comes from shared downstream pressure. If shadow traffic doubles database read load and saturates the database, users can still be affected indirectly.

### Shadow Guardrails

- Disable writes, emails, payments, webhooks, and notifications.
- Use read-only credentials where possible.
- Add a header such as `X-Shadow-Traffic: true`.
- Exclude shadow traffic from business analytics.
- Rate limit shadow traffic independently.
- Sample logs/traces more aggressively than production traffic.

### Example Nginx Mirror

```nginx
location / {
    mirror /shadow;
    proxy_pass http://stable_backend;
}

location /shadow {
    internal;
    proxy_set_header X-Shadow-Traffic true;
    proxy_pass http://shadow_backend;
}
```

### SRE Notes

- Shadow first, canary second is strong for rewrites.
- Compare status codes, latency, response shape, and key business outputs.
- Never assume shadow traffic is harmless because users do not see the response.

---

## Strategy 7: A/B Testing

### Definition

A/B testing sends different users to different behavior variants to compare product or business outcomes.

```text
50% users -> Variant A
50% users -> Variant B
```

### When To Use

- UX experiments.
- Pricing or checkout flow experiments.
- Search ranking comparison.
- Recommendation algorithm evaluation.
- Measuring conversion, retention, engagement, or support impact.

### Deployment vs Experiment

Canary answers:

```text
Is this release technically safe?
```

A/B testing answers:

```text
Is this behavior better for users or the business?
```

Do not use A/B testing as the only safety mechanism for infrastructure risk. A variant can improve conversion while also increasing p99 latency or database load.

### Cost

| Cost Area | Impact |
|-----------|--------|
| Runtime | Usually 1x if both variants live in the same service. |
| Analytics | Extra event collection and analysis. |
| Engineering | Experiment setup, segmentation, and cleanup. |
| Support | Confusion if different users see different behavior. |

### Downtime

Usually zero if implemented with flags or routing. Rollback is often a flag change.

### SRE Notes

- Always watch technical guardrails alongside business metrics.
- Keep user assignment stable so users do not jump between variants.
- Exclude bots, test accounts, and internal traffic from experiment results.
- Remove losing variants after decision.

---

## Strategy 8: Immutable Infrastructure

### Definition

Immutable deployment creates a new machine image, container image, or infrastructure version instead of changing existing servers in place. Old infrastructure is replaced, not modified.

```text
Build image v2 -> provision new instances -> attach to LB -> drain old instances
```

### When To Use

- VM-based workloads.
- Regulated systems needing auditable artifacts.
- Environments where configuration drift causes incidents.
- Services that need reproducible rollback.
- Legacy apps that do not fit container orchestration cleanly.

### Avoid When

- Provisioning time is too slow for urgent rollback.
- Image build pipeline is unreliable.
- Stateful local disk makes replacement risky.
- Secrets/config are baked into images instead of injected at runtime.

### Cost

| Cost Area | Impact |
|-----------|--------|
| Compute | 1.2x to 2x during replacement. |
| Image storage | More AMIs/images retained. |
| Build pipeline | More build time and artifact scanning. |
| Operations | Lower drift, higher release pipeline discipline. |

### Downtime

Near zero if the load balancer drains old instances only after new instances are healthy. Downtime can happen if:

- New instances fail health checks.
- Autoscaling group replacement is too aggressive.
- Connection draining timeout is shorter than real request duration.
- Database migrations are not compatible.

### SRE Notes

- Tag every image with version, commit SHA, build time, and SBOM/scanning status.
- Keep previous image available until rollback window closes.
- Validate boot, config, IAM, network, and health checks before shifting traffic.

---

## Database Changes With Any Deployment Strategy

Database changes are where otherwise safe deployments become dangerous. The application can usually roll back; the database often cannot.

### Safe Pattern: Expand, Migrate, Contract

Use a multi-release process:

```text
Release 1: Expand
  Add new nullable column/table/index.
  Old code still works.
  New code may dual-write.

Release 2: Migrate
  Backfill data.
  New code reads new structure.
  Old code can still tolerate the schema.

Release 3: Contract
  Remove old column/table only after all code no longer needs it.
```

### Example

Unsafe:

```sql
ALTER TABLE orders RENAME COLUMN total_amount TO amount_total;
```

This can break old application instances during rolling, canary, or rollback.

Safer:

```sql
-- Release 1: expand
ALTER TABLE orders ADD COLUMN amount_total DECIMAL(10,2);

-- Application dual-writes total_amount and amount_total.
-- Backfill existing rows in batches.

-- Release 2: migrate reads
-- Application reads amount_total but can fall back to total_amount.

-- Release 3: contract
ALTER TABLE orders DROP COLUMN total_amount;
```

### Migration Cost

| Cost Area | Impact |
|-----------|--------|
| Database CPU/IO | Backfills can consume significant resources. |
| Locks | Some DDL can block reads/writes. |
| Storage | Dual columns/tables temporarily increase storage. |
| Replication | Large migrations can create replica lag. |
| Engineering | Requires multiple releases and cleanup. |

### Migration Downtime

Zero-downtime migration requires:

- Backward-compatible schema.
- Batched backfill.
- Lock-aware DDL.
- Rollback plan for application behavior.
- Clear point of no return for destructive operations.

If the change requires exclusive locks or irreversible transformation, schedule a maintenance window and communicate expected downtime.

---

## Queue And Worker Deployments

Queue workers have different deployment risks than request/response services.

### Key Risks

- Duplicate processing during rollout.
- Message schema incompatibility.
- Consumer lag growth.
- Poison messages after new code deploys.
- Dead-letter queue growth.
- In-flight job interruption.

### Recommended Strategies

| Worker Type | Strategy | Notes |
|-------------|----------|-------|
| Idempotent consumers | Rolling or Canary | Safe if duplicate processing is handled. |
| Payment/email workers | Canary with strict idempotency | Prevent duplicate external side effects. |
| Long-running jobs | Drain before termination | Visibility timeout must exceed processing time. |
| Schema-changing consumers | Expand-contract message schema | Producers and consumers must overlap safely. |
| High-volume stream processors | Canary by partition or consumer group | Watch lag and rebalance behavior. |

### Worker Deployment Checklist

- Stop polling before shutdown.
- Finish or checkpoint in-flight jobs.
- Make handlers idempotent.
- Version message schemas.
- Keep old consumers until all old messages are drained.
- Watch queue lag, retry count, processing latency, and DLQ.

---

## WebSocket And Long-Lived Connection Deployments

Long-lived connections make "zero downtime" harder because users can be connected to old instances for minutes or hours.

### Recommended Strategy

- Rolling or canary with connection draining.
- Client reconnect with exponential backoff.
- Server sends shutdown notice before termination if protocol supports it.
- Load balancer stops new connections before terminating old ones.

### Cost

Usually 1.1x to 1.5x during deployment because old instances may need to remain alive until connections drain.

### Downtime

Hard downtime can be zero, but users may see reconnects. Treat reconnect storms as a deployment risk.

### SRE Notes

- Monitor active connections by version.
- Set maximum connection lifetime if old versions must disappear.
- Avoid terminating all old instances at once.
- Test mobile and unreliable network reconnect behavior.

---

## Serverless Deployment Notes

Serverless platforms often provide aliases, versions, traffic weights, or deployment preferences.

### Recommended Strategy

- Publish immutable function version.
- Route small percentage to new version.
- Watch errors, latency, throttles, concurrency, and cold starts.
- Increase weight gradually.
- Roll back alias to previous version.

### Cost

Usually close to 1x, but cost can rise if:

- Both versions are provisioned with reserved/provisioned concurrency.
- Cold starts increase duration.
- Shadow invocations are used.
- Retries increase because of errors.

### Downtime

Usually zero if alias routing is used. Functional downtime can happen if the function depends on incompatible event schema or downstream permissions.

---

## Static Frontend And CDN Deployments

Static assets have a common deployment trap: new HTML references new JS/CSS, but old cached assets or old HTML still exist at the edge.

### Recommended Strategy

- Use immutable, content-hashed asset filenames.
- Upload assets before HTML.
- Keep old assets until cache TTL expires.
- Roll out HTML/cache invalidation gradually if possible.
- Roll back by serving previous HTML version.

### Cost

Usually near 1x. Extra cost comes from storage retention, cache invalidation, and increased origin fetches during cache churn.

### Downtime

Hard downtime is rare. Functional downtime happens when users receive HTML that references missing assets.

### SRE Notes

- Never delete old static assets immediately after deployment.
- Make frontend and backend API changes backward compatible.
- Watch frontend error rate, Core Web Vitals, CDN 4xx/5xx, and API error rate by frontend version.

---

## Rollback Decision By Strategy

| Strategy | Fastest Rollback | Data Risk | Typical Rollback Time |
|----------|------------------|-----------|-----------------------|
| Recreate | Redeploy previous version | Medium | Minutes |
| Rolling | `kubectl rollout undo` or previous artifact | Medium if DB changed | Minutes |
| Blue-Green | Switch load balancer back | Medium if DB changed | Seconds |
| Canary | Route traffic back to stable | Low to medium | Seconds to minutes |
| Feature Flag | Disable flag | Low if old path is intact | Seconds |
| Shadow | Stop mirroring | Low for users, medium for shared systems | Seconds |
| A/B Test | Disable losing variant | Low | Seconds |
| Immutable Infra | Reattach old ASG/image or previous version | Medium | Minutes |

Rollback is unsafe when:

- The new version wrote data the old version cannot read.
- A destructive migration already ran.
- Messages emitted by the new version break old consumers.
- External side effects already happened.
- Old artifacts/images are no longer available.

---

## Pre-Deployment Checklist

Use this before any production deployment:

- Deployment strategy selected and documented.
- Rollback owner assigned.
- Previous artifact/image/chart still available.
- Database changes are backward compatible.
- Message/event schemas are backward compatible.
- Readiness and health checks validate real dependencies.
- Dashboards show metrics by version.
- Alerts are active and routed to the deploy owner.
- Error budget impact is understood.
- Expected temporary cost is known.
- Maintenance window approved if downtime is expected.
- Customer/support communication prepared if user impact is possible.

---

## During-Deployment Checklist

Watch:

- 5xx and 4xx error rate.
- p50, p95, p99 latency.
- Saturation: CPU, memory, disk, network, file descriptors.
- Pod/container restarts.
- Queue lag and DLQ growth.
- Database connection count, locks, replication lag, slow queries.
- Cache hit rate and cache errors.
- Downstream dependency errors.
- Business success metrics such as login, checkout, search, signup, payment authorization.

Pause or rollback when:

- Error rate exceeds the pre-defined threshold.
- p99 latency exceeds SLO or rises sharply against baseline.
- Canary is worse than stable for multiple windows.
- Queue lag grows continuously.
- Database saturation appears.
- New version emits unknown or incompatible events.
- On-call cannot explain the failure quickly.

---

## Post-Deployment Checklist

- Confirm 100% of traffic is on intended version.
- Confirm no old pods/instances are accidentally serving traffic.
- Keep old version available until rollback window closes.
- Check error budget burn for at least one full business cycle if the service is critical.
- Remove temporary canary/green/shadow resources.
- Delete stale feature flags after rollout is complete.
- Close migration cleanup tasks.
- Record deployment notes, incident links, and rollback lessons.

---

## Strategy Selection Flow

```text
Can the business tolerate downtime?
  Yes -> Recreate may be acceptable for low-criticality workloads.
  No  -> Continue.

Can old and new versions run together safely?
  No  -> Use Blue-Green with strict switch, or schedule maintenance.
  Yes -> Continue.

Is this a risky user-facing behavior change?
  Yes -> Use Feature Flag + Canary.
  No  -> Continue.

Is traffic high enough for a meaningful small sample?
  Yes -> Canary.
  No  -> Rolling or Blue-Green depending on criticality.

Is the workload stateful or connection-heavy?
  Yes -> Rolling/Canary with drain rules and quorum/session checks.
  No  -> Rolling is usually fine for routine deploys.

Does the release include database/schema changes?
  Yes -> Use expand-contract, regardless of deployment strategy.
```

---

## Practical Defaults

| Environment | Default Strategy | Reason |
|-------------|------------------|--------|
| Local/dev | Recreate | Fast feedback, low risk. |
| Staging | Rolling or Blue-Green | Match production where possible. |
| Internal low-criticality app | Rolling | Simple and usually no downtime. |
| Production stateless service | Canary | Controls blast radius. |
| Production critical service | Canary + Feature Flag, or Blue-Green | Fast rollback and low user impact. |
| Production monolith | Blue-Green | Easier full-environment validation. |
| Production worker | Canary/Rolling with drain | Protect queues and side effects. |
| Production database | Expand-contract | App deploy strategy cannot protect destructive schema changes. |

---

## Common Anti-Patterns

| Anti-Pattern | Why It Fails | Better Approach |
|--------------|--------------|-----------------|
| Deploy and pray | Failure is discovered by users. | Use canary, alerts, and rollout gates. |
| Health check only returns `200 OK` | Bad version receives traffic while broken. | Check dependencies and readiness separately. |
| Destructive DB migration with rolling deploy | Old pods crash against new schema. | Expand-contract migration. |
| DNS-only rollback | DNS caches delay recovery. | Switch load balancer target groups where possible. |
| Feature flags never removed | Code becomes untestable. | Add owner and expiry date. |
| No version labels in metrics | Cannot compare stable vs canary. | Add `version` or `release` label. |
| Terminating pods immediately | In-flight requests fail. | Graceful shutdown and connection draining. |
| Shadow traffic with side effects | Duplicate payments/emails/writes. | Read-only shadow path and side-effect blocking. |
| Scaling down stable too early | Rollback requires rebuilding capacity. | Keep stable until rollback window closes. |

---

## Quick Strategy Examples

### Routine API Patch

```text
Workload:
  Stateless API, no DB migration, strong tests.

Use:
  Rolling deployment.

Cost:
  1.0x to 1.2x during rollout.

Downtime:
  Zero target.

Rollback:
  kubectl rollout undo.
```

### Risky Checkout Change

```text
Workload:
  Payment flow, high business impact.

Use:
  Deploy behind feature flag.
  Enable internal users.
  Canary 1%, 5%, 10%, 25%, 50%, 100%.

Cost:
  Around 1x runtime plus flag platform/observability cost.

Downtime:
  Zero target.

Rollback:
  Disable flag first.
  Roll back artifact only if infrastructure or startup is broken.
```

### Legacy Monolith Upgrade

```text
Workload:
  VM-based monolith with long startup time.

Use:
  Blue-green or immutable infrastructure.

Cost:
  Up to 2x during overlap.

Downtime:
  Near zero if LB switch and drain work.

Rollback:
  Switch LB back to old target group.
```

### Kafka Consumer Change

```text
Workload:
  High-volume consumer that writes to database.

Use:
  Canary by consumer group or partition subset.

Cost:
  1x to 1.3x compute.

Downtime:
  No user-facing outage, but backlog risk.

Rollback:
  Stop new consumer group and resume stable consumers.
```

### Database Column Rename

```text
Workload:
  API and database schema change.

Use:
  Expand-contract migration over multiple releases.

Cost:
  Extra storage and backfill IO.

Downtime:
  Zero if migration is compatible and lock-safe.

Rollback:
  Roll back application behavior.
  Do not drop old column until rollback window is closed.
```

---

## Final Rule Of Thumb

Use this default unless there is a clear reason not to:

```text
Stateless routine change:
  Rolling deployment

User-facing risky change:
  Feature flag + canary

Critical service or monolith:
  Blue-green or canary with instant rollback

Queue/worker:
  Canary or rolling with drain and idempotency

Database:
  Expand-contract migration

Large rewrite:
  Shadow traffic -> canary -> gradual release
```

The safest deployment is not the one with the fanciest tool. It is the one where cost, downtime, rollback, state, and blast radius are understood before the first production instance changes.
