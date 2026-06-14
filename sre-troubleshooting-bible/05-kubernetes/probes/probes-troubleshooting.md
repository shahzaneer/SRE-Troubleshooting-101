# Probes Troubleshooting

> **Category:** Kubernetes | Probes | Health Checks
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#kubernetes` `#probes` `#health-check` `#liveness` `#readiness`

---

## Table of Contents

1. [Probe Types & Ordering](#probe-types--ordering)
2. [Startup Probe Failures](#startup-probe-failures)
3. [Liveness Probe Failures](#liveness-probe-failures)
4. [Readiness Probe Failures](#readiness-probe-failures)
5. [Probe Configuration Mistakes](#probe-configuration-mistakes)

---

## Probe Types & Ordering

```text
3 probe types, 1 execution order:

1. startupProbe  — "Has the app STARTED?" (runs FIRST and ONLY)
   If it fails: container is killed and restarted.
   If it succeeds: startup probe STOPS, liveness probe TAKES OVER.
   USE CASE: Slow-starting apps (JVM, DB migrations).

2. livenessProbe — "Is the app ALIVE?" (runs AFTER startup, CONTINUOUSLY)
   If it fails: container is killed and restarted.
   USE CASE: Deadlocked apps that can't recover.

3. readinessProbe — "Is the app READY to serve traffic?" (runs CONTINUOUSLY)
   If it fails: pod is removed from Service endpoints (no traffic).
   If it succeeds: pod is re-added to endpoints.
   USE CASE: Temporary overload, warming caches, DB reconnection.

Order of execution:
  startupProbe runs FIRST (if configured)
  → On success: livenessProbe + readinessProbe take over
  → On failure: container restarted (kubelet applies restartPolicy)
  
  If NO startupProbe:
  → livenessProbe + readinessProbe start IMMEDIATELY after container start

  If BOTH startupProbe and livenessProbe:
  → livenessProbe is DISABLED until startupProbe succeeds
```

### Quick Diagnosis

```bash
# Check probe configuration
kubectl get pod POD -n NAMESPACE -o yaml | grep -A20 "readinessProbe\|livenessProbe\|startupProbe"

# Check pod conditions (Ready, ContainersReady)
kubectl get pod POD -n NAMESPACE -o json | jq '.status.conditions[] | {type: .type, status: .status}'

# Check probe events
kubectl describe pod POD -n NAMESPACE | grep -A2 "Liveness\|Readiness\|Startup"

# Check if pod was killed by liveness probe
kubectl describe pod POD -n NAMESPACE | grep "Liveness probe failed"

# Manually test a probe endpoint
kubectl exec POD -n NAMESPACE -- curl -v http://localhost:8080/healthz
kubectl exec POD -n NAMESPACE -- /bin/sh -c "test -f /tmp/healthy && echo OK || echo FAIL"
```

---

## Startup Probe Failures

### What It Means

```text
Container starts but the startup probe never succeeds.
Kubelet keeps checking, and after failureThreshold * periodSeconds,
it kills and restarts the container.

This creates a RESTART LOOP similar to CrashLoopBackOff, but the
reason is "Startup probe failed", not exit code.
```

### Diagnosis

```bash
kubectl describe pod POD -n NAMESPACE | grep -A5 "Startup"
# Warning  Unhealthy  2m  kubelet  Startup probe failed: Get "http://10.244.1.5:8080/healthz": dial tcp 10.244.1.5:8080: connect: connection refused

# Common patterns:
# "connection refused" → app hasn't started listening yet
# "context deadline exceeded" → timeout too short
# "HTTP probe failed with statuscode: 500" → app is starting but failing health check
```

### Common Causes & Fixes

| Cause | Symptom | Fix |
|-------|---------|-----|
| **App takes longer than failureThreshold** | Probe starts failing at the threshold limit | Increase `failureThreshold * periodSeconds` to > startup time |
| **startupProbe not configured** | See below (Scenario) | Add startupProbe with generous threshold |
| **Health endpoint returns non-200** | Probe fails, container restarts | Fix health endpoint or use a simpler check (e.g., TCP socket) |
| **Port wrong** | connection refused forever | Fix port number in probe config |

### Scenario: "App restarts continuously — liveness probe kills it before it starts"

```text
THIS IS THE #1 PROBE MISTAKE.

Symptom: Pod restarts 10+ times. Logs show app starting, running migrations,
         then container suddenly killed. Restart loop.

Pod spec:
  livenessProbe:
    httpGet:
      path: /healthz
      port: 8080
    initialDelaySeconds: 30
    periodSeconds: 10
    failureThreshold: 3

  # NO startupProbe!

  App startup: 90 seconds (JVM warmup + Flyway migrations).
  initialDelaySeconds: 30 (gives 30s head start).
  After 30s, liveness probe starts checking.
  App is NOT ready yet (still migrating DB at 45s).
  Probe fails at 30s, 40s, 50s.
  failureThreshold=3 → after 3rd failure (50s) → container KILLED.
  Container restarts, migrations start AGAIN, loop repeats.

The migrations can NEVER complete because the liveness probe kills
the container before it finishes!

Fix:
  # Add startupProbe to give migrations time:
  startupProbe:
    httpGet:
      path: /healthz
      port: 8080
    initialDelaySeconds: 0
    periodSeconds: 5
    failureThreshold: 30   # 30 * 5s = 150s total startup grace
  livenessProbe:
    httpGet:
      path: /healthz
      port: 8080
    periodSeconds: 10
    failureThreshold: 3
    # initialDelaySeconds is NOT needed — startupProbe handles it
```

---

## Liveness Probe Failures

### Liveness vs Readiness: When to Use Each

```text
LIVENESS: "Restart me, I'm dead."
  Use when: app state is unrecoverable (deadlocked, memory corrupted)
  DON'T use: for transient failures (DB timeout, 3rd party API down)
  
  WRONG: liveness probe checks DB connection → DB restart → all pods killed!
  RIGHT: liveness probe checks app process health only (self-contained)

READINESS: "Stop sending me traffic, I'm busy."
  Use when: app can't serve requests temporarily (warming up, overloaded)
  DON'T use: for permanent failures (liveness should handle crash loops)

  RIGHT: readiness probe fails when thread pool is full, backpressure triggers
  WRONG: readiness probe never fails → broken app keeps getting traffic
```

### TCP Socket Probe (Simplest)

```yaml
# Use when app has no HTTP health endpoint
livenessProbe:
  tcpSocket:
    port: 8080
  initialDelaySeconds: 15
  periodSeconds: 10
# Succeeds if TCP connection succeeds (port is listening)
```

### Exec Probe (Command-Based)

```yaml
livenessProbe:
  exec:
    command:
    - /bin/sh
    - -c
    - pg_isready -h localhost -p 5432
  initialDelaySeconds: 5
  periodSeconds: 10
```

### Scenario: "All pods killed simultaneously by liveness probe"

```text
Symptom: All 10 replicas killed and restarted at the same time.
         kubectl describe pod shows "Liveness probe failed".
         Happens every few hours.

Diagnosis:
  kubectl describe pod myapp-xxx -n production | grep -A3 Liveness
  → Liveness probe failed: HTTP probe failed with statuscode: 500

  The /healthz endpoint checks:
  - Application health: OK
  - Database connection: SELECT 1
  - Redis connection: PING

  When Redis had a brief blip (2s network partition), ALL pods'
  /healthz returned 500 simultaneously. ALL liveness probes failed
  simultaneously. Kubelet killed ALL pods.

  This is a CASCADING FAILURE. Liveness probe should NOT check
  external dependencies.

Fix:
  # Liveness: check ONLY the app process
  livenessProbe:
    httpGet:
      path: /healthz
      port: 8080
    periodSeconds: 10
    failureThreshold: 3
  
  # /healthz endpoint should be:
  # "Is my event loop running? Yes → 200."
  # NOT: "Is Redis healthy? Is Postgres healthy?"

  # Readiness: check external dependencies
  readinessProbe:
    httpGet:
      path: /ready
      port: 8080
    periodSeconds: 5
    failureThreshold: 3

  # /ready endpoint:
  # "Can I serve requests? Check Redis, DB, etc."
  # If dependencies down, pod stays alive but removed from endpoints.
```

---

## Readiness Probe Failures

### What It Means

```text
Pod is Running but Not Ready → Removed from Service endpoints.

Traffic stops flowing to this pod. Other pods handle the load.
If ALL pods become NotReady → Service has no endpoints → 503 errors.

Readiness probe should be DESIGNED to fail when the pod cannot
handle more requests (saturation, backpressure, dependency issues).
```

### Common Issues

```text
1. Readiness probe checks too many dependencies
   → One dependency down → all pods NotReady → 100% outage
   → Better: Check only CRITICAL dependencies needed to serve requests

2. Readiness probe identical to liveness probe
   → What's the point of two probes if they check the same thing?
   → Readiness should be more stringent than liveness

3. Readiness probe never fails
   → Pods stay "Ready" even when overloaded → no backpressure signal
   → Implement a health endpoint that returns 503 under high load

4. Readiness probe too aggressive (low failureThreshold)
   → Brief hiccup removes pod from endpoints → unnecessary disruption
   → Set failureThreshold: 3-5 to tolerate momentary blips
```

### Readiness Gates (Advanced)

```yaml
# ReadinessGates: external conditions that ALSO determine readiness
spec:
  readinessGates:
  - conditionType: "cloud.example.com/load-balancer-healthy"
  - conditionType: "cloud.example.com/certificate-provisioned"

# Pod is Ready only when:
# 1. All containers are ready (probes pass)
# 2. All readiness gate conditions are True
```

---

## Probe Configuration Mistakes

### 1. initialDelaySeconds Without startupProbe

```text
problem: initialDelaySeconds is a GUESS at how long startup takes.
         If it's too short → container killed before it starts.
         If it's too long → container is unhealthy for too long.

Fix: Use startupProbe instead (removes the guessing).
```

### 2. failureThreshold Too Low

```yaml
failureThreshold: 1   # 1 failure = restart. Too aggressive.
failureThreshold: 5   # 5 failures = restart. More tolerant.
```

### 3. periodSeconds Too Low

```yaml
periodSeconds: 1    # Check every second. High overhead.
periodSeconds: 10   # Check every 10 seconds. Reasonable.
periodSeconds: 30   # Check every 30 seconds. For high-failureThreshold combos.
```

### 4. timeoutSeconds Too Low

```yaml
timeoutSeconds: 1   # 1s timeout. Many apps need >1s under load.
timeoutSeconds: 5   # 5s timeout. Better for most apps.
```

### 5. Using HTTP GET Where TCP Socket Works

```yaml
# BAD: HTTP health endpoint that doesn't exist
livenessProbe:
  httpGet:
    path: /
    port: 8080

# GOOD: Simple TCP check
livenessProbe:
  tcpSocket:
    port: 8080
```

### Recommended Probe Configurations

```yaml
# Slow-starting app (JVM, migrations, ML model loading)
startupProbe:
  httpGet:
    path: /healthz
    port: 8080
  periodSeconds: 5
  failureThreshold: 36    # 36 * 5 = 180s max startup time
livenessProbe:
  httpGet:
    path: /healthz
    port: 8080
  periodSeconds: 10
  timeoutSeconds: 5
  failureThreshold: 3    # 3 * 10 = 30s before restart
readinessProbe:
  httpGet:
    path: /ready
    port: 8080
  periodSeconds: 5
  timeoutSeconds: 3
  failureThreshold: 5    # 5 * 5 = 25s before removing from endpoints
```

---

## References

- [Configure Liveness, Readiness and Startup Probes](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/)
- [Pod Lifecycle](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/)
- [Container Probes Deep Dive](https://kubernetes.io/blog/2018/05/kubernetes-best-practices-setting-up-health-checks-with-readiness-and-liveness-probes/)
