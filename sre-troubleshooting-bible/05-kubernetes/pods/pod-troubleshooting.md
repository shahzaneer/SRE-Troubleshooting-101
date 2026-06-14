# Pod Troubleshooting

> **Category:** Kubernetes | Pods | Debugging
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#kubernetes` `#pods` `#debugging` `#oncall`

---

## Table of Contents

1. [Pod Lifecycle & Statuses](#pod-lifecycle--statuses)
2. [CrashLoopBackOff](#crashloopbackoff)
3. [ImagePullBackOff & ErrImagePull](#imagepullbackoff--errimagepull)
4. [OOMKilled](#oomkilled)
5. [Pending Pods](#pending-pods)
6. [InitContainer Failures](#initcontainer-failures)
7. [Terminating Pods Stuck](#terminating-pods-stuck)

---

## Pod Lifecycle & Statuses

```text
Pending → Running → Succeeded (or Failed)
                 → Terminating (deletion requested)

Status values:
  Pending            Pod accepted, waiting for containers to be created
  Running            At least one container is running
  Succeeded          All containers exited with code 0
  Failed             All containers terminated, at least one with non-zero
  Unknown            Node lost contact with control plane
  CrashLoopBackOff   Container crashes, restarted repeatedly, backoff applied
  ImagePullBackOff   Image pull failed, retrying with backoff
  ErrImagePull       Image pull failed (first attempt)
  CreateContainerError  Runtime can't create the container
  Init:N/M           Init container N of M is running
  Init:Error         Init container failed
  PodInitializing    Pod created but init containers still running
```

---

## CrashLoopBackOff

### What It Means
The container starts, runs, crashes/exits, and Kubernetes keeps restarting it. After repeated failures, the kubelet applies exponential backoff (10s, 20s, 40s, ... up to 5min between restarts).

### Diagnosis

```bash
# Check pod status and restart count
kubectl get pods -A -o wide | grep CrashLoopBackOff

# Get details
kubectl describe pod POD -n NAMESPACE
# Look for: State, Last State, Exit Code, Events, Reason

# Check logs of CURRENT container (if it runs long enough)
kubectl logs POD -n NAMESPACE

# Check logs of PREVIOUS crashed container (most useful)
kubectl logs POD -n NAMESPACE --previous

# Check all containers in a multi-container pod
kubectl logs POD -n NAMESPACE -c sidecar --previous
kubectl logs POD -n NAMESPACE --all-containers=true --previous

# Get exit code directly
kubectl get pod POD -n NAMESPACE -o json | jq '.status.containerStatuses[] | {name: .name, restartCount: .restartCount, lastState: .lastState}'
```

### Common Causes & Fixes

| Exit Code | Cause | Fix |
|-----------|-------|-----|
| **0** | App completed its task. Batch job with `restartPolicy: Always` | Use `restartPolicy: OnFailure` or `Never`, or make app a long-running daemon |
| **1** | Application error: unhandled exception, config file missing | Check `--previous` logs for stack traces. Fix app code or config |
| **126** | Permission denied on ENTRYPOINT/CMD | `chmod +x` the binary in Dockerfile or fix USER permissions |
| **127** | Command not found | Binary path wrong, missing from $PATH, or not installed |
| **137** | OOMKilled (SIGKILL) | Increase memory limit or fix memory leak (see OOMKilled section) |
| **139** | SIGSEGV (segfault) | Native code crash. Check C/C++ extensions, use debug symbols |
| **143** | SIGTERM received but app didn't exit gracefully | App ignoring SIGTERM. Fix signal handling in code |

### Scenario: "App crashes after startup with exit code 1"

```text
Symptom: Pod restarts 8 times, status CrashLoopBackOff.
         kubectl logs POD --previous shows:
           panic: runtime error: invalid memory address
           [stack trace]

         The app starts, connects to a database, then crashes 20s later.

Diagnosis:
  1. kubectl logs POD --previous → stack trace shows DB connection
  2. kubectl exec -it POD -- env | grep DATABASE → DATABASE_URL=postgres://...
  3. From another pod: nslookup postgres-service → resolves fine
  4. From another pod: nc -zv postgres-service 5432 → connection works

  Wait — the app crashes AFTER connecting successfully. The error is
  "invalid memory address" not "connection refused". Looking at the
  stack trace: the app queries a table that doesn't exist yet because
  migrations haven't run. The nil pointer is from the empty result set.

Fix: Run database migrations (initContainer or Helm hook) before app starts.
```

---

## ImagePullBackOff & ErrImagePull

### Diagnosis

```bash
kubectl describe pod POD -n NAMESPACE | grep -A10 Events
# Events:
#   Failed to pull image "registry.example.com/myapp:v1.2.3": rpc error: code = NotFound
#   Failed to pull image "myapp:v1.2.3": pull access denied

# Check which nodes failed to pull
kubectl get events -n NAMESPACE --field-selector reason=Failed | grep POD

# Verify the image exists
docker pull registry.example.com/myapp:v1.2.3
# or from within the cluster:
kubectl run test-pull --image=registry.example.com/myapp:v1.2.3 --restart=Never --rm -it -- sh
```

### Common Causes & Fixes

| Cause | Symptom | Fix |
|-------|---------|-----|
| **Image doesn't exist** | `NotFound` or `manifest unknown` | Check tag spelling. Verify image was pushed. |
| **Registry credentials missing** | `pull access denied` or `unauthorized` | Create imagePullSecrets: `kubectl create secret docker-registry regcred --docker-server=...` |
| **ECR token expired** | `unauthorized: authentication required` | ECR tokens last 12h. Use `kubectl create secret docker-registry` with fresh token or use IRSA |
| **Private registry from wrong VPC** | `dial tcp: i/o timeout` | Registry not accessible from cluster VPC. Add VPC endpoint or peering. |
| **Rate limited (Docker Hub)** | `toomanyrequests: You have reached your pull rate limit` | Use authenticated pulls (Docker Hub account) or mirror to own registry |
| **Wrong image pull policy** | `ErrImagePull` on nodes without image | Set `imagePullPolicy: Always` or pre-pull images to nodes |

### Scenario: "ECR image pull fails on new nodes"

```text
Symptom: New pods on newly scaled-out nodes get ImagePullBackOff.
         Existing pods on old nodes work fine.

Diagnosis:
  kubectl describe pod myapp-abc123 -n production
  → Failed to pull image "123456789.dkr.ecr.us-east-1.amazonaws.com/myapp:v1.2.3"
  → "unauthorized: authentication required"

  kubectl get secret ecr-regcred -n production → secret exists
  kubectl get pod myapp-abc123 -o yaml | grep imagePullSecrets → secrets referenced

  But wait — ECR tokens expire after 12 hours. The secret was created
  3 days ago. The old nodes had the cached image, so no pull was needed.
  New nodes have no cached images, so they try to pull and fail.

Fix:
  # Option A: Use IRSA (IAM Roles for Service Accounts) for ECR
  # Option B: Regenerate the secret with a cron job
  kubectl create secret docker-registry ecr-regcred \
    --docker-server=123456789.dkr.ecr.us-east-1.amazonaws.com \
    --docker-username=AWS \
    --docker-password=$(aws ecr get-login-password --region us-east-1) \
    -n production --dry-run=client -o yaml | kubectl apply -f -

  # Option C: Use a tool like kube2iam or ECR refresh cron
```

---

## OOMKilled

### What It Means
The container used more memory than its `resources.limits.memory`. The kernel OOM killer sends SIGKILL (exit code 137).

### Diagnosis

```bash
# Check if pod was OOMKilled
kubectl describe pod POD -n NAMESPACE | grep -A5 "Last State"
# Last State:     Terminated
#   Reason:       OOMKilled
#   Exit Code:    137

# Check the pod's memory limits
kubectl get pod POD -n NAMESPACE -o json | jq '.spec.containers[].resources.limits.memory'

# Check current usage (if still running)
kubectl top pod POD -n NAMESPACE

# Check historical usage (if Prometheus)
# container_memory_working_set_bytes{pod="POD", container="CONTAINER"}

# Check node memory
kubectl top node NODE
kubectl describe node NODE | grep -A10 "Allocated resources"

# Check OOM events on node
kubectl get events -A --field-selector reason=OOMKilling
```

### Common Causes & Fixes

| Cause | Fix |
|-------|-----|
| **Memory limit too low** | Increase `resources.limits.memory`. Also set `resources.requests.memory` for scheduling. |
| **Memory leak** | Profile the application. Check for unbounded caches, goroutine leaks, unclosed connections. |
| **Traffic spike** | HPA scaled pods but memory per request is high. Increase limit or add caching. |
| **Java heap not constrained** | Set `-Xmx` flag BELOW container limit. Without it, JVM sees host memory. Use `-XX:MaxRAMPercentage=75.0` |
| **Node.js memory** | Set `--max-old-space-size` below container limit. Node.js GC doesn't know about cgroups by default. |
| **Python memory** | No automatic cgroup awareness in Python. Memory grows with requests. Use gunicorn `--max-requests` to recycle workers. |

### Scenario: "Java app OOMKilled despite low traffic"

```text
Symptom: Pod restarts with OOMKilled even though traffic is light.
         Container limit: 512Mi.

Diagnosis:
  kubectl logs POD --previous
  → Java HotSpot(TM) 64-Bit Server VM warning:
  → "The MaxRAMPercentage is not set, using 25% of host memory"
  
  The node has 64GB RAM, so JVM thinks it has 16GB available
  (25% of 64GB). It allocates aggressively, hitting the 512Mi
  cgroup limit quickly.

  kubectl exec -it POD -- java -XX:+PrintFlagsFinal -version 2>&1 | grep MaxHeap
  → uintx MaxHeapSize := 16877944832 (16GB!) — way over 512Mi limit

Fix: Add to container env or JAVA_OPTS:
  -XX:MaxRAMPercentage=75.0  (not -Xmx, which ignores cgroup)
  This tells Java: use 75% of the cgroup limit (512Mi * 0.75 = 384Mi)
  for heap.
```

---

## Pending Pods

### Why Pods Get Stuck in Pending

```bash
# Quick diagnosis
kubectl describe pod POD -n NAMESPACE | tail -20
# Look at the Events section — it tells you exactly why

# Common event messages and their meanings:
# "0/3 nodes are available: 3 Insufficient cpu"
#   → No node has enough CPU. Scale cluster or reduce requests.
#
# "0/3 nodes are available: 1 node(s) had taint {}, 2 Insufficient memory"
#   → Some nodes tainted, others out of memory capacity.
#
# "0/3 nodes are available: 3 node(s) didn't match node selector"
#   → nodeSelector doesn't match any node labels.
#
# "0/3 nodes are available: 3 node(s) didn't match Pod's node affinity"
#   → Affinity rules exclude all nodes.
#
# "pod has unbound immediate PersistentVolumeClaims"
#   → PVC waiting for PV binding.

# Check scheduler events
kubectl get events -n NAMESPACE --field-selector reason=FailedScheduling | tail -10
```

### Common Causes & Fixes

| Cause | Diagnosis | Fix |
|-------|-----------|-----|
| **Resource shortage** | `Insufficient cpu/memory` in events | Scale cluster, reduce resource requests, or evict low-priority pods |
| **Node taints** | `node(s) had taint` in events | Add toleration to pod spec, or remove taint from node |
| **Node selector mismatch** | `didn't match node selector` | Update nodeSelector to match existing labels or label nodes |
| **Affinity rules too strict** | `didn't match Pod's node affinity` | Relax affinity rules or add nodes matching the criteria |
| **PVC not binding** | `unbound immediate PersistentVolumeClaims` | Check PVC status: `kubectl get pvc`. Create PV or StorageClass. |
| **Topology spread constraints** | `didn't match pod topology spread constraints` | Relax constraints or add nodes in required zones |

### Scenario: "Pod Pending due to taint and no toleration"

```text
Symptom: Pod stuck in Pending for 30 minutes.
         kubectl describe pod myapp-xyz | tail -5:
         Warning  FailedScheduling  30m  default-scheduler  
         0/4 nodes are available: 1 node(s) had taint
         {dedicated=monitoring:NoSchedule}, 3 node(s) didn't
         match Pod's node affinity.

Diagnosis:
  # 1 node has a taint we don't tolerate, 3 nodes don't match affinity
  kubectl get nodes -o custom-columns=NAME:.metadata.name,TAINTS:.spec.taints
  → node-1: [dedicated=monitoring:NoSchedule]   ← tainted
  → node-2: [zone=us-east-1a]                    ← wrong zone
  → node-3: [zone=us-east-1b]                    ← wrong zone
  → node-4: [zone=us-east-1c]                    ← wrong zone

  kubectl get pod myapp-xyz -o yaml | grep -A5 affinity
  → requiredDuringScheduling:
      nodeSelectorTerms:
      - matchExpressions:
        - key: zone
          operator: In
          values: [us-east-1d]    ← no node has this zone!

Fix:
  # The affinity requires zone=us-east-1d but nodes only have a,b,c.
  # Either add nodes in zone-d, or expand the zone list:
  affinity:
    nodeAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms:
        - matchExpressions:
          - key: zone
            operator: In
            values: [us-east-1a, us-east-1b, us-east-1c, us-east-1d]
```

---

## InitContainer Failures

```bash
# Check init container logs
kubectl logs POD -n NAMESPACE -c init-db-migrate
# or
kubectl logs POD -n NAMESPACE -c init-check-config

# Check pod status
kubectl get pod POD -n NAMESPACE
# NAME     READY   STATUS            RESTARTS   AGE
# myapp-0  0/1     Init:1/2          0          2m
# myapp-0  0/1     Init:Error        0          3m    ← init container crashed

# Check init container exit code
kubectl get pod POD -n NAMESPACE -o json | jq '.status.initContainerStatuses[] | {name, state}'
```

### Scenario: "InitContainer can't reach external service"

```text
Symptom: Pod stuck at Init:0/1. Init container runs a DB migration
         but can't reach the database.

  kubectl logs myapp-0 -c init-db-migrate
  → psql: could not connect to server: Connection refused
  → Is the server running on host "postgres.production.svc" and accepting
  → TCP/IP connections on port 5432?

  # Check if postgres service exists
  kubectl get svc postgres -n production
  → NAME       TYPE        CLUSTER-IP   EXTERNAL-IP   PORT(S)
  → postgres   ClusterIP   10.43.1.5    <none>        5432/TCP

  # Check DNS resolution from another pod
  kubectl run dns-test --image=busybox --rm -it -- nslookup postgres.production.svc.cluster.local
  → resolves correctly

  # Problem: init container runs BEFORE the pod's network is fully set up?
  # No, that's wrong. Init containers share the pod network.

  # Actually, check if postgres pod is running
  kubectl get pods -n production -l app=postgres
  → No resources found

  # The postgres service exists but there are no pods backing it!
  kubectl get endpoints postgres -n production
  → No endpoints

Fix: Start the postgres pod first, or add an init container that
     polls until the DB is ready:
     initContainers:
     - name: wait-for-postgres
       image: busybox
       command: ['sh', '-c', 'until nc -z postgres 5432; do sleep 2; done']
```

---

## Terminating Pods Stuck

```bash
# Pod stuck in Terminating state for >30s
kubectl get pods -A | grep Terminating

# Check what's blocking deletion
kubectl describe pod POD -n NAMESPACE
# Look for: finalizers preventing deletion

kubectl get pod POD -n NAMESPACE -o json | jq '.metadata.finalizers'
# Empty = no finalizers? Check finalizers on associated resources (PVC, etc.)
```

### Common Causes

| Cause | Fix |
|-------|-----|
| **Finalizer stuck** | `kubectl patch pod POD -p '{"metadata":{"finalizers":[]}}' --type=merge` |
| **Graceful shutdown period** | Wait for `terminationGracePeriodSeconds` (default 30s). Or force: `--grace-period=0 --force` |
| **PreStop hook hung** | Check if PreStop hook is waiting on something unreachable |
| **PVC with finalizer** | `kubectl patch pvc PVC -p '{"metadata":{"finalizers":[]}}' --type=merge` |
| **Node disconnected** | If node is gone, pod can't terminate. Force delete: `--force --grace-period=0` |

---

## References

- [Kubernetes Pod Lifecycle](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/)
- [Debug Running Pods](https://kubernetes.io/docs/tasks/debug/debug-application/debug-running-pod/)
- [Determine Reason for Pod Failure](https://kubernetes.io/docs/tasks/debug/debug-application/determine-reason-pod-failure/)
