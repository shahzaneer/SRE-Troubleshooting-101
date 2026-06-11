# ECS/EKS Troubleshooting

> **Category:** AWS | ECS | EKS | Containers
> **Difficulty:** Intermediate to Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#aws` `#ecs` `#eks` `#kubernetes` `#oncall`

---

## Table of Contents

1. [ECS Task Failures](#ecs-task-failures)
2. [ECS Service Deployment Issues](#ecs-service-deployment-issues)
3. [EKS Pod Troubleshooting](#eks-pod-troubleshooting)
4. [EKS Pod Lifecycle & Probes](#eks-pod-lifecycle--probes)
5. [EKS Networking & Service Routing](#eks-networking--service-routing)
6. [EKS ConfigMaps & Secrets](#eks-configmaps--secrets)
7. [Python Health Checker](#python-health-checker)
8. [Production Deployment YAML](#production-deployment-yaml)

---

## ECS Task Failures

### Task Failing to Start

When an ECS task remains stuck in `PROVISIONING` or fails with `STOPPED`, inspect the `stoppedReason`:

```bash
aws ecs describe-tasks \
  --cluster my-cluster \
  --tasks abc123def456 \
  --query "tasks[0].{Status:lastStatus,Reason:stoppedReason,Containers:containers[*].{Name:name,Reason:reason,ExitCode:exitCode}}"
```

#### 1. IMAGE_PULL_ERROR

```text
stoppedReason: "CannotPullContainerError: manifest unknown"
  → The image tag exists in the task definition but not in ECR.
  → The CI pipeline pushed an image with tag v1.2.3 but the task
    definition was updated to v1.3.0 before the image was built.

  Fix:
  - Verify the image exists:
    aws ecr describe-images \
      --repository-name my-app \
      --image-ids imageTag=v1.3.0
  - If missing, trigger the CI pipeline that builds and pushes v1.3.0
  - Alternatively, revert the task definition to the previous version:
    aws ecs describe-task-definition --task-definition my-app --query \
      "taskDefinition.revision"  # → 45 (current, broken)
    aws ecs update-service --cluster my-cluster --service my-service \
      --task-definition my-app:44  # rollback to known-good rev

stoppedReason: "CannotPullContainerError: access denied"
  → The task execution role lacks ECR pull permissions.
  → Check the task execution role policy:
    aws iam get-role-policy --role-name ecsTaskExecutionRole \
      --policy-name ECRPullPolicy
  → Required permissions:
    - ecr:GetAuthorizationToken
    - ecr:BatchCheckLayerAvailability
    - ecr:GetDownloadUrlForLayer
    - ecr:BatchGetImage
```

#### 2. RESOURCE_LIMIT

```text
stoppedReason: "OutOfMemoryError: Container killed due to memory usage"
  → Task definition memory (container-level) < actual memory used.
  → Check with:
    aws ecs describe-task-definition --task-definition my-app \
      --query "taskDefinition.containerDefinitions[*].{Name:name,Memory:memory,MemoryReservation:memoryReservation}"

  Fix:
  - Increase container-level `memory` in the task definition.
  - Enable ECS Container Insights for memory metrics:
    aws ecs update-cluster-settings \
      --cluster my-cluster \
      --settings name=containerInsights,value=enabled
  - Check CloudWatch metric: MemoryUtilization (namespace: ECS/ContainerInsights)

stoppedReason: "RESOURCE:CPU"
  → Task CPU reservation > available CPU on any container instance.
  → Check container instance capacity:
    aws ecs describe-container-instances \
      --cluster my-cluster \
      --container-instances <instance-arn> \
      --query "containerInstances[*].remainingResources"

  Fix:
  - Reduce task CPU or scale out the cluster with more instances.
  - Use Fargate to avoid capacity management entirely.
```

#### 3. IAM Task Role Missing Permissions

```text
stoppedReason: "ResourceInitializationError: unable to pull secrets..."
  → Task role lacks secretsmanager:GetSecretValue or ssm:GetParameter.
  → Verify:
    aws secretsmanager describe-secret --secret-id my-secret
    aws iam simulate-principal-policy \
      --policy-source-arn arn:aws:iam::123456789:role/ecs-task-role \
      --action-names secretsmanager:GetSecretValue \
      --resource-arns arn:aws:secretsmanager:us-east-1:123456789:secret:my-secret
```

---

## ECS Service Deployment Issues

### Service Stuck in DRAINING

```text
Symptom: ECS service status shows DRAINING for >10 minutes during deployment.

  aws ecs describe-services \
    --cluster my-cluster \
    --services my-service \
    --query "services[0].{Status:status,Desired:desiredCount,Running:runningCount,Pending:pendingCount,Events:events[:5].message}"

  Common causes:
  1. Connection draining: ALB deregistration delay is too high.
     → Check target group attribute:
       aws elbv2 describe-target-group-attributes \
         --target-group-arn arn:aws:elasticloadbalancing:...:targetgroup/my-tg/xxx
       → deregistration_delay.timeout_seconds = 300 (5 min)
     → Reduce for faster deployments:
       aws elbv2 modify-target-group-attributes \
         --target-group-arn arn:... --attributes \
         Key=deregistration_delay.timeout_seconds,Value=30

  2. Tasks cannot stop because they're holding long-lived connections
     (WebSocket, gRPC streams). Set stopTimeout in task definition:
     "stopTimeout": 30  # max seconds to wait before force-killing

  3. Service is waiting for new tasks to become healthy.
     → Check cloudwatch logs for the new task revision.
```

### Service Event Log

```bash
# See deployment history, task placement failures, health check failures
aws ecs describe-services \
  --cluster my-cluster \
  --services my-service \
  --query "services[0].events[:20][].[createdAt,message]" \
  --output table
```

### Scenario: "ECS service deployment fails — new tasks keep cycling"

```text
Symptom: Rolling update starts, old tasks begin draining, new tasks launch
         but immediately fail health checks and are killed. Service cycles
         through tasks indefinitely with no stable version.

Investigation:
  aws logs tail /ecs/my-task-def --follow --since 5m

  Log shows: "FATAL: database connection refused"
  → The new task revision points to a database endpoint that was changed
    during the same deployment window but isn't accessible yet (DNS propagation).
  → Or: the new task's security group doesn't allow outbound to the RDS port.

Fix:
  1. IMMEDIATE: Roll back task definition revision:
     aws ecs update-service --cluster my-cluster --service my-service \
       --task-definition my-app:44
  2. Add health check grace period to task definition to give app time:
     "healthCheck": {
       "command": ["CMD-SHELL", "curl -f http://localhost:8080/health || exit 1"],
       "interval": 30,
       "timeout": 5,
       "retries": 3,
       "startPeriod": 60
     }
  3. Use ECS Circuit Breaker (stops the deployment if failures exceed threshold):
     aws ecs update-service --cluster my-cluster --service my-service \
       --deployment-configuration \
       "deploymentCircuitBreaker={enable=true,rollback=true}"
```

---

## EKS Pod Troubleshooting

### Pod Stuck in Pending

```bash
kubectl describe pod my-pod-7f8b9c6d5-abc12
```

Key events to look for:

```text
1. "0/3 nodes are available: 3 Insufficient cpu."
   → Requested CPU > available on any node.
   Fix: Reduce CPU request, scale out nodes, or use Cluster Autoscaler.

2. "0/3 nodes are available: 3 Insufficient memory."
   → Requested memory > available on any node.
   Fix: Reduce memory request, or add larger nodes.

3. "0/3 nodes are available: 3 node(s) didn't match Pod's node affinity/selector."
   → NodeSelector or nodeAffinity doesn't match any node labels.
   Check node labels:
     kubectl get nodes --show-labels | grep disk
   Pod spec has:
     nodeSelector:
       disk: ssd
   But all nodes are: disk=hdd
   Fix: Correct the label selector or label your nodes.

4. "0/3 nodes are available: 3 node(s) had taint {dedicated=critical:NoSchedule}, that the pod didn't tolerate."
   → Pod doesn't have a toleration for the node taint.
   Fix: Add toleration or remove taint.

5. "persistentvolumeclaim 'my-pvc' not found"
   → PVC referenced but doesn't exist in this namespace.
   Fix: Create PVC or fix the reference.

6. "running PreBind plugin 'VolumeBinding': binding volumes: timed out waiting for the condition"
   → PVC is Pending (no matching PV or StorageClass provisioner is broken).
   Check: kubectl describe pvc my-pvc
```

### CrashLoopBackOff

Container starts, exits, and Kubernetes restarts it repeatedly.

```bash
# See previous crash logs
kubectl logs my-pod-7f8b9c6d5-abc12 --previous

# Check exit code and reason
kubectl get pod my-pod-7f8b9c6d5-abc12 -o json | jq '.status.containerStatuses[0].lastState.terminated'
```

```text
Exit codes quick reference:
  0     → Normal exit. App completed or shut down.
           Check if ENTRYPOINT/CMD finishes (batch container?). 
  1     → Application error. Check logs for stack trace.
  137   → SIGKILL. Either OOMKilled (check memory limits) or killed by kubelet.
           If OOMKilled, "reason" field will say so.
  143   → SIGTERM. Graceful termination requested (deployment scale-down, node drain).
           App may be taking too long to shut down; check terminationGracePeriodSeconds.
  126   → Permission denied. Can't execute the binary.
           Check: Dockerfile has correct CMD? Executable bit set?
  127   → Command not found. Binary doesn't exist at specified path.
           Example: CMD ["gunicorn"] but pip didn't install it.
  139   → SIGSEGV (segmentation fault). Native code crash. Check core dumps.
```

### ImagePullBackOff

```bash
kubectl describe pod my-pod-7f8b9c6d5-abc12 | grep -A10 Events
```

```text
Common causes:
1. Image doesn't exist or tag is wrong.
   kubectl describe pod POD | grep "Failed to pull image"
   → Verify: docker pull 123456789.dkr.ecr.us-east-1.amazonaws.com/my-app:v1.0

2. ECR: Node lacks IAM permissions to pull.
   → Node's IAM role must have:
     - ecr:GetAuthorizationToken
     - ecr:BatchCheckLayerAvailability
     - ecr:GetDownloadUrlForLayer
     - ecr:BatchGetImage

3. Private registry (non-ECR): imagePullSecrets missing or wrong.
   kubectl get secret regcred -o yaml
   → If missing, create it:
     kubectl create secret docker-registry regcred \
       --docker-server=registry.example.com \
       --docker-username=user \
       --docker-password=token \
       --docker-email=ops@example.com
   → Reference in pod spec:
     imagePullSecrets:
     - name: regcred

4. Rate limiting: Docker Hub imposes pull rate limits (100/6h anonymous, 200/6h authenticated).
   → Use ECR cache or set up Docker Hub auth.
```

### OOMKilled

```bash
kubectl describe pod my-pod-7f8b9c6d5-abc12 | grep -A5 "Last State"
```

```text
Last State:  Terminated
  Reason:    OOMKilled
  Exit Code: 137

  → Container exceeded its memory limit (NOT request).
  → The Linux OOM killer sent SIGKILL.
  → Container restarted.

  Investigation:
  1. Check if it's a memory leak:
     kubectl top pod my-pod-7f8b9c6d5-abc12
     → Watch memory trend: is it monotonically increasing?
     → If yes: profile memory (Java heap dump, Python tracemalloc, Node.js heap snapshot).

  2. Check if limit is simply too low for normal workload:
     → Look at memory usage over time in Grafana/Prometheus.
     → If usage is flat near the limit: increase limits.

  3. Distinguish between burst and steady-state:
     → Burst: a particular request type allocates lots of memory.
       Fix: add request validation (reject oversized payloads), streaming instead of buffering.
     → Steady: the normal working set doesn't fit in the limit. Increase limit.

  Fix:
  resources:
    requests:
      memory: "256Mi"   # what it normally needs
    limits:
      memory: "512Mi"   # maximum allowed (was 256Mi, causing OOM)
```

---

## EKS Pod Lifecycle & Probes

### Understanding the Three Probes

```yaml
startupProbe:         # "Is it done starting up?"
  httpGet:
    path: /healthz
    port: 8080
  initialDelaySeconds: 0     # starts immediately
  periodSeconds: 10          # check every 10s
  timeoutSeconds: 5
  failureThreshold: 12       # 12 × 10s = 120s total for startup
  successThreshold: 1        # once success → liveness/readiness take over

readinessProbe:       # "Can it serve traffic?"
  httpGet:
    path: /ready
    port: 8080
  periodSeconds: 5
  failureThreshold: 3        # 3 failures → remove from service endpoints

livenessProbe:        # "Should we restart it?"
  httpGet:
    path: /healthz
    port: 8080
  initialDelaySeconds: 0     # startup probe covers initial delay
  periodSeconds: 15
  failureThreshold: 2        # 2 failures → kill and restart
```

### Probe Timing Scenarios

```text
Scenario 1: "App takes 90s to warm up, liveness probe fires at 30s killing it."
  The loop: container starts → 30s → liveness fails (app still warming) →
  container killed → restart → 30s → killed again → ∞ CrashLoopBackOff.

  Fix: Add a startupProbe that gives the app enough time:
    startupProbe:
      httpGet:
        path: /healthz
        port: 8080
      failureThreshold: 12     # 12 × 10s = 120s
      periodSeconds: 10
    # Remove liveness initialDelaySeconds (startup probe handles it)

Scenario 2: "App is healthy per liveness but can't accept traffic (database down)."
  Liveness check returns 200 (process is alive), but readiness check fails
  because /ready checks DB connectivity. Pod stays alive but is removed
  from service endpoints. Other pods handle the traffic. When DB recovers,
  readiness passes and pod rejoins.

  This is CORRECT behavior — liveness and readiness serve different purposes.
  Liveness: check the PROCESS (local state).
  Readiness: check the SERVICE (external dependencies).

Scenario 3: "Readiness probe flaps — pod repeatedly added/removed from endpoints."
  readinessProbe:
    periodSeconds: 5
    failureThreshold: 3  # 15s to be removed
    successThreshold: 2  # must succeed twice to be added back

  Root cause: transient failures (intermittent DB timeouts).
  Fix: increase failureThreshold so the pod isn't yanked on a single blip.
    Or fix the underlying transient failure.
```

### Probe Best Practices

```text
DO:
  ✓ Use startupProbe for apps that need >60s to initialize.
  ✓ Readiness should check ALL critical dependencies (DB, cache, queue).
  ✓ Liveness should check only the process itself (no external deps).
  ✓ Use HTTP probes over exec probes (less overhead).
  ✓ Set appropriate timeouts (don't let probe hang indefinitely).

DON'T:
  ✗ Check the same external dependency in both liveness and readiness.
     If DB goes down: readiness fails → pod removed from service (good).
                     liveness also fails → pod restarted (BAD — restarting
                     doesn't fix the DB, it just adds CPU load).
  ✗ Use a liveness probe that parses complex output or runs heavy scripts.
  ✗ Set initialDelaySeconds too long — use startupProbe instead.
  ✗ Forget to set failureThreshold — the default might be too aggressive.
```

---

## EKS Networking & Service Routing

### Service Not Routing Traffic

```bash
# If endpoints are empty, no pod is matching the service selector
kubectl get endpoints my-service

# GOOD output (has endpoints):
NAME         ENDPOINTS                                           AGE
my-service   10.0.1.23:8080,10.0.1.24:8080,10.0.2.15:8080      7d

# BAD output (empty endpoints):
NAME         ENDPOINTS   AGE
my-service   <none>      7d
```

```text
Diagnostic flow when endpoints are empty:

1. Service selector matches pod labels?
   kubectl get svc my-service -o jsonpath='{.spec.selector}'
     → {"app": "myapp"}
   kubectl get pods -l app=myapp --show-labels
     → If no pods: deployment has different labels.
     → If pods show but endpoints empty: readiness probe is failing.

2. Readiness check is failing:
   kubectl get pods -l app=myapp -o wide
   → Look at READY column: 0/1 means all containers are up but
     at least one readiness probe is failing.
   kubectl describe pod <pod-name> | grep -A5 "Readiness"
   → Shows probe failure details.

3. Namespace mismatch:
   → Service is in `staging` namespace, pods are in `default`.
   → kubectl get endpoints -n staging
   → Move service to correct namespace or use cross-namespace selectors.

4. Port mismatch:
   → Service targetPort doesn't match containerPort in pod spec.
     svc: targetPort: 8080
     pod: containerPort: 8081
   → No routing happens.

Scenario: "Deployment has labels app: myapp, version: v2 but service
          selector is app: myapp only. This works initially but when
          version label was added to deployment.spec.selector.matchLabels,
          new ReplicaSet couldn't adopt old pods because labels diverged.
          Now service selector matches both old and new pods? Actually no —
          the issue is that the Deployment is broken, not the Service."
```

### EndpointSlice (EKS 1.21+)

```bash
# Modern EKS uses EndpointSlices instead of Endpoints
kubectl get endpointslice -n my-namespace

# EndpointSlices are more scalable (100 endpoints per slice)
kubectl describe endpointslice my-service-abc12
```

---

## EKS ConfigMaps & Secrets

### ConfigMap/Secret Not Mounting

```bash
kubectl describe pod my-pod-7f8b9c6d5-abc12 | grep -A10 Events
```

```text
Common issues:
1. ConfigMap/Secret doesn't exist in the same namespace:
   kubectl get configmap my-config -n my-app
   → If not found: kubectl create configmap my-config --from-file=app.properties

2. Mount path is a directory that already exists in the container image.
   → Mounting at /etc/config will REPLACE that directory — all existing
     files in /etc/config are hidden.
   → Mount at /etc/config/app/ instead, or use subPath for specific files.

3. Using subPath wrongly:
   volumes:
   - name: config
     configMap:
       name: my-config
   volumeMounts:
   - name: config
     mountPath: /app/config/app.properties
     subPath: app.properties
   → subPath mounts don't auto-update when the ConfigMap changes.
     The pod must be restarted to pick up changes.

4. File permissions: ConfigMap mounts with 0644 by default.
   → Change with defaultMode:
     volumes:
     - name: config
       configMap:
         name: my-config
         defaultMode: 0400   # read-only for owner only

5. Optional ConfigMap missing:
   volumes:
   - name: config
     configMap:
       name: my-optional-config
       optional: true   # pod starts even if ConfigMap doesn't exist
```

---

## Python Health Checker

```python
#!/usr/bin/env python3
"""
EKS namespace health checker using the official Kubernetes Python client.
Checks pod statuses, recent events, resource usage, and failing probes.

Usage:
  python3 k8s_health_check.py --namespace production
"""

import argparse
import sys
from datetime import datetime, timezone, timedelta
from collections import Counter

from kubernetes import client, config
from kubernetes.client.rest import ApiException


class K8sHealthChecker:
    def __init__(self, namespace: str):
        self.namespace = namespace
        config.load_kube_config()  # or load_incluster_config() in-cluster
        self.core_v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()
        self.metrics = client.CustomObjectsApi()  # for metrics.k8s.io

    def check_pods(self) -> dict:
        """Check all pods in namespace for health issues."""
        pods = self.core_v1.list_namespaced_pod(self.namespace)
        issues = {
            "total": len(pods.items),
            "not_ready": [],
            "restarting": [],       # > 5 restarts
            "pending": [],          # stuck pending > 10 min
            "crashloop": [],        # CrashLoopBackOff
            "imagepullbackoff": [],
            "oomkilled": [],
            "terminating": [],      # stuck terminating > 5 min
        }

        now = datetime.now(timezone.utc)

        for pod in pods.items:
            name = pod.metadata.name
            phase = pod.status.phase

            if phase == "Pending":
                # Check how long it's been pending
                created = pod.metadata.creation_timestamp
                pending_minutes = (now - created).total_seconds() / 60
                issues["pending"].append({
                    "pod": name,
                    "minutes": round(pending_minutes, 1),
                })

            for cs in (pod.status.container_statuses or []):
                if not cs.ready and cs.started:
                    issues["not_ready"].append({
                        "pod": name,
                        "container": cs.name,
                    })

                # Check restart count
                if cs.restart_count > 5:
                    issues["restarting"].append({
                        "pod": name,
                        "container": cs.name,
                        "restarts": cs.restart_count,
                    })

                # Check last terminated state
                last = cs.last_state.terminated
                if last:
                    if last.reason == "OOMKilled":
                        issues["oomkilled"].append({
                            "pod": name,
                            "container": cs.name,
                            "exit_code": last.exit_code,
                        })

                # Check waiting state
                wait = cs.state.waiting
                if wait:
                    reason = wait.reason
                    if reason == "CrashLoopBackOff":
                        issues["crashloop"].append({
                            "pod": name,
                            "container": cs.name,
                            "message": wait.message,
                        })
                    elif reason == "ImagePullBackOff":
                        issues["imagepullbackoff"].append({
                            "pod": name,
                            "container": cs.name,
                            "message": wait.message,
                        })

            # Check if stuck terminating
            if pod.metadata.deletion_timestamp:
                deletion = pod.metadata.deletion_timestamp
                terminating_minutes = (now - deletion).total_seconds() / 60
                if terminating_minutes > 5:
                    issues["terminating"].append({
                        "pod": name,
                        "minutes": round(terminating_minutes, 1),
                    })

        return issues

    def check_events(self, since_minutes: int = 15) -> list[dict]:
        """Get recent Warning events in the namespace."""
        events = self.core_v1.list_namespaced_event(self.namespace)
        warnings = []

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)

        for event in events.items:
            if event.type == "Warning" and event.last_timestamp:
                if event.last_timestamp >= cutoff:
                    warnings.append({
                        "timestamp": event.last_timestamp.isoformat(),
                        "kind": event.involved_object.kind,
                        "name": event.involved_object.name,
                        "reason": event.reason,
                        "message": event.message,
                    })

        return sorted(warnings, key=lambda e: e["timestamp"], reverse=True)

    def check_deployments(self) -> list[dict]:
        """Check deployments for availability issues."""
        deployments = self.apps_v1.list_namespaced_deployment(self.namespace)
        unhealthy = []

        for dep in deployments.items:
            desired = dep.spec.replicas
            ready = dep.status.ready_replicas or 0
            available = dep.status.available_replicas or 0

            if ready < desired or available < desired:
                unhealthy.append({
                    "deployment": dep.metadata.name,
                    "desired": desired,
                    "ready": ready,
                    "available": available,
                    "conditions": [
                        {"type": c.type, "status": c.status, "message": c.message}
                        for c in (dep.status.conditions or [])
                        if c.status != "True"
                    ],
                })

        return unhealthy

    def run_full_check(self) -> int:
        """Run all checks and return exit code (0 = healthy, 1 = issues)."""
        print(f"=== EKS Health Check: namespace={self.namespace} ===")
        print(f"Time: {datetime.now(timezone.utc).isoformat()}\n")

        exit_code = 0

        # Pod check
        pods = self.check_pods()
        print(f"Pods: {pods['total']} total")
        for category, items in pods.items():
            if category == "total":
                continue
            if items:
                exit_code = 1
                print(f"\n  ⚠ {category.upper()} ({len(items)}):")
                for item in items[:5]:  # limit output
                    print(f"    - {item['pod']}: {item}")

        # Events check
        warnings = self.check_events()
        if warnings:
            exit_code = 1
            print(f"\n  ⚠ RECENT WARNING EVENTS ({len(warnings)}):")
            for w in warnings[:10]:
                print(f"    [{w['timestamp']}] {w['name']}: {w['reason']} - {w['message'][:120]}")

        # Deployment check
        deploys = self.check_deployments()
        if deploys:
            exit_code = 1
            print(f"\n  ⚠ UNHEALTHY DEPLOYMENTS ({len(deploys)}):")
            for d in deploys:
                print(f"    {d['deployment']}: desired={d['desired']} ready={d['ready']} available={d['available']}")

        print(f"\nExit code: {exit_code}  {'(healthy)' if exit_code == 0 else '(issues found)'}")
        return exit_code


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EKS Namespace Health Checker")
    parser.add_argument("--namespace", "-n", default="default", help="Namespace to check")
    args = parser.parse_args()

    checker = K8sHealthChecker(args.namespace)
    sys.exit(checker.run_full_check())
```

---

## Production Deployment YAML

```yaml
---
# Namespace
apiVersion: v1
kind: Namespace
metadata:
  name: production
  labels:
    environment: production
    istio-injection: enabled

---
# ConfigMap for non-sensitive config
apiVersion: v1
kind: ConfigMap
metadata:
  name: app-config
  namespace: production
data:
  LOG_LEVEL: "info"
  DB_TIMEOUT: "30"
  CACHE_TTL: "300"

---
# PodDisruptionBudget — ensure at least 2 pods are always running
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: myapp-pdb
  namespace: production
spec:
  minAvailable: 2
  selector:
    matchLabels:
      app: myapp

---
# Deployment with production best practices
apiVersion: apps/v1
kind: Deployment
metadata:
  name: myapp
  namespace: production
  labels:
    app: myapp
    version: v1
  annotations:
    # Trigger a rolling restart when ConfigMap changes
    configmap.reloader.stakater.com/reload: "app-config"
spec:
  replicas: 3
  revisionHistoryLimit: 5   # keep only last 5 revisions
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1           # 1 extra pod during rollout
      maxUnavailable: 0     # never drop below 3 pods
  selector:
    matchLabels:
      app: myapp
      version: v1
  template:
    metadata:
      labels:
        app: myapp
        version: v1
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "9090"
        prometheus.io/path: "/metrics"
    spec:
      # Topology spread: spread pods across AZs and hosts
      topologySpreadConstraints:
      - maxSkew: 1
        topologyKey: topology.kubernetes.io/zone
        whenUnsatisfiable: DoNotSchedule
        labelSelector:
          matchLabels:
            app: myapp
      - maxSkew: 1
        topologyKey: kubernetes.io/hostname
        whenUnsatisfiable: ScheduleAnyway
        labelSelector:
          matchLabels:
            app: myapp

      # Anti-affinity: prefer not to co-locate pods on same node
      affinity:
        podAntiAffinity:
          preferredDuringSchedulingIgnoredDuringExecution:
          - weight: 100
            podAffinityTerm:
              labelSelector:
                matchLabels:
                  app: myapp
              topologyKey: kubernetes.io/hostname

      # Graceful shutdown
      terminationGracePeriodSeconds: 60

      # Security context
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        runAsGroup: 1000
        fsGroup: 1000
        seccompProfile:
          type: RuntimeDefault

      # Init container: wait for DB migration to complete
      initContainers:
      - name: check-db
        image: busybox:1.36
        command:
        - sh
        - -c
        - |
          until nc -z -w 2 postgres.production.svc.cluster.local 5432; do
            echo "Waiting for PostgreSQL..."
            sleep 3
          done
          echo "PostgreSQL is ready"

      # Service account with minimal permissions
      serviceAccountName: myapp-sa
      automountServiceAccountToken: true

      containers:
      - name: app
        image: 123456789012.dkr.ecr.us-east-1.amazonaws.com/myapp:v1.2.3
        imagePullPolicy: IfNotPresent
        workingDir: /app
        command: ["gunicorn"]
        args:
        - "--bind=0.0.0.0:8080"
        - "--workers=4"
        - "--threads=2"
        - "--timeout=30"
        - "--max-requests=1000"
        - "--max-requests-jitter=100"
        - "--access-logfile=-"
        - "--error-logfile=-"
        - "app:application"

        ports:
        - name: http
          containerPort: 8080
          protocol: TCP
        - name: metrics
          containerPort: 9090
          protocol: TCP

        # Resource requests and limits — CRITICAL for scheduling
        resources:
          requests:
            cpu: "500m"
            memory: "256Mi"
          limits:
            cpu: "2000m"
            memory: "512Mi"

        # Environment from ConfigMap and Secret
        envFrom:
        - configMapRef:
            name: app-config
        env:
        - name: DB_PASSWORD
          valueFrom:
            secretKeyRef:
              name: app-secrets
              key: db-password
        - name: API_KEY
          valueFrom:
            secretKeyRef:
              name: app-secrets
              key: api-key
        - name: POD_NAME
          valueFrom:
            fieldRef:
              fieldPath: metadata.name
        - name: POD_NAMESPACE
          valueFrom:
            fieldRef:
              fieldPath: metadata.namespace
        - name: POD_IP
          valueFrom:
            fieldRef:
              fieldPath: status.podIP
        - name: NODE_NAME
          valueFrom:
            fieldRef:
              fieldPath: spec.nodeName

        # Startup probe: app needs up to 90s to start
        startupProbe:
          httpGet:
            path: /healthz
            port: http
          initialDelaySeconds: 5
          periodSeconds: 10
          timeoutSeconds: 5
          failureThreshold: 12   # 12 × 10s = 120s total
          successThreshold: 1

        # Liveness probe: is the process alive?
        livenessProbe:
          httpGet:
            path: /healthz
            port: http
          periodSeconds: 15
          timeoutSeconds: 5
          failureThreshold: 3    # 3 × 15s = 45s before restart

        # Readiness probe: can it serve traffic?
        readinessProbe:
          httpGet:
            path: /ready
            port: http
          periodSeconds: 5
          timeoutSeconds: 3
          failureThreshold: 2    # 2 × 5s = 10s before removed from service
          successThreshold: 1

        # Volume mounts
        volumeMounts:
        - name: config
          mountPath: /app/config
          readOnly: true
        - name: tmp
          mountPath: /tmp
        - name: cache
          mountPath: /app/cache

        # Lifecycle hooks
        lifecycle:
          preStop:
            exec:
              command:
              - sh
              - -c
              - |
                # Drain connections gracefully
                sleep 15
                echo "Shutting down..."

      # Volumes
      volumes:
      - name: config
        configMap:
          name: app-config
          defaultMode: 0444
      - name: tmp
        emptyDir:
          sizeLimit: 256Mi
      - name: cache
        emptyDir:
          sizeLimit: 512Mi

---
# HorizontalPodAutoscaler
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: myapp-hpa
  namespace: production
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: myapp
  minReplicas: 3
  maxReplicas: 20
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
  - type: Resource
    resource:
      name: memory
      target:
        type: Utilization
        averageUtilization: 80
  behavior:
    scaleDown:
      stabilizationWindowSeconds: 300   # wait 5 min before scaling down
      policies:
      - type: Pods
        value: 1
        periodSeconds: 60               # scale down 1 pod per minute max
    scaleUp:
      stabilizationWindowSeconds: 0     # scale up immediately
      policies:
      - type: Pods
        value: 4
        periodSeconds: 60               # scale up 4 pods per minute
      selectPolicy: Max

---
# Service
apiVersion: v1
kind: Service
metadata:
  name: myapp
  namespace: production
  labels:
    app: myapp
spec:
  type: ClusterIP
  selector:
    app: myapp
  ports:
  - name: http
    port: 80
    targetPort: http
    protocol: TCP
  - name: metrics
    port: 9090
    targetPort: metrics
    protocol: TCP
  sessionAffinity: None
```

---

## References

- [Amazon ECS Troubleshooting Guide](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/troubleshooting.html)
- [Amazon EKS Troubleshooting Guide](https://docs.aws.amazon.com/eks/latest/userguide/troubleshooting.html)
- [Kubernetes Debugging Pods](https://kubernetes.io/docs/tasks/debug/debug-application/debug-pods/)
- [Kubernetes Probe Configuration](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/)
- [Kubernetes Python Client](https://github.com/kubernetes-client/python)
- [Pod Topology Spread Constraints](https://kubernetes.io/docs/concepts/scheduling-eviction/topology-spread-constraints/)
