# Controllers Troubleshooting

> **Category:** Kubernetes | Deployments | StatefulSets | DaemonSets | Jobs
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#kubernetes` `#deployments` `#statefulsets` `#daemonsets` `#jobs`

---

## Table of Contents

1. [Deployment Troubleshooting](#deployment-troubleshooting)
2. [StatefulSet Troubleshooting](#statefulset-troubleshooting)
3. [DaemonSet Troubleshooting](#daemonset-troubleshooting)
4. [Jobs & CronJobs Troubleshooting](#jobs--cronjobs-troubleshooting)

---

## Deployment Troubleshooting

### Quick Diagnosis

```bash
# Overall deployment status
kubectl get deployment DEPLOY -n NAMESPACE -o wide

# Check rollout status
kubectl rollout status deployment/DEPLOY -n NAMESPACE

# Deployment details
kubectl describe deployment DEPLOY -n NAMESPACE

# Events for this deployment
kubectl get events -n NAMESPACE --field-selector involvedObject.name=DEPLOY --sort-by=.lastTimestamp

# Check replicaset status
kubectl get rs -n NAMESPACE -l app=DEPLOY

# Compare old vs new replicaset
kubectl get rs -n NAMESPACE -l app=DEPLOY -o wide
```

### Common Deployment Issues

#### 1. Rollout Stuck / Not Progressing

```bash
kubectl rollout status deployment/myapp -n production
# Waiting for deployment "myapp" rollout to finish: 2 out of 5 new replicas available

# Check why new pods aren't becoming ready
kubectl get pods -n production -l app=myapp --sort-by=.metadata.creationTimestamp
# If new pods are in CrashLoopBackOff, ImagePullBackOff, or Pending → fix pod issue
# If new pods are Running but not Ready → check readiness probe
```

```text
Scenario: "Deployment stuck at 3/5 replicas updated"

Symptom: `kubectl rollout status` shows progress stopped at 3/5.
         kubectl get rs shows old RS still has 2 pods.

Diagnosis:
  kubectl describe deployment myapp -n production
  → Conditions:
      Available: True (3/5 replicas available)
      Progressing: False (ReplicaSet "myapp-abc123" has timed out progressing)

  New pods are crashing (CrashLoopBackOff) and the deployment's
  progressDeadlineSeconds (default 600s) has been exceeded. The
  deployment stops rolling out new pods to prevent total outage.

Fix:
  1. Fix the underlying pod issue (bad image, config, etc.)
  2. kubectl rollout undo deployment/myapp → rollback if quick fix
  3. Or fix and re-trigger: kubectl rollout restart deployment/myapp
  4. Increase progressDeadlineSeconds if your rollout legitimately takes >10min
```

#### 2. Revision History Lost

```bash
# Check rollout history
kubectl rollout history deployment/myapp -n production
# REVISION  CHANGE-CAUSE
# 1         <none>
# 2         <none>
# ...
# 10        <none>

# If revisionHistoryLimit is 10, only last 10 revisions are kept.
# Older revisions are pruned. You CAN'T rollback to revision 1 if
# only 10 revisions are kept and you're on revision 100.
```

```text
Fix: Set a higher revisionHistoryLimit or use Helm which stores
     full release manifests independently:
     spec:
       revisionHistoryLimit: 20
```

#### 3. maxSurge / maxUnavailable Issues

```text
Scenario: "Deployment scaled to 1, rollout fails because it can't create new pods"

The default rollout strategy is RollingUpdate, with:
  maxSurge: 25% (rounded up)
  maxUnavailable: 25% (rounded down)

With 1 replica: maxSurge=1, maxUnavailable=0
  → Can create 1 new pod but can't terminate the old one (maxUnavailable=0)
  → If the node has no capacity for +1 pod, rollout stalls

Fix: For single-replica deployments, set maxUnavailable: 1
     or increase cluster capacity, or use Recreate strategy.
```

### Deployment YAML Debugging

```bash
# See what the deployment ACTUALLY deployed (not what you think you applied)
kubectl get deployment myapp -n production -o yaml | kubectl neat

# Compare desired vs current
kubectl diff -f deployment.yaml

# Check if someone edited the deployment manually (bypassing GitOps)
kubectl rollout history deployment/myapp -n production
kubectl get deployment myapp -n production -o yaml | grep "kubernetes.io/change-cause"
```

---

## StatefulSet Troubleshooting

### StatefulSet-Specific Issues

```text
StatefulSets differ from Deployments:
  1. Pods have STABLE identities: myapp-0, myapp-1, myapp-2 (not random)
  2. Pods are created/deleted in ORDER (0→N for create, N→0 for delete)
  3. Each pod gets its OWN PVC (not shared)
  4. Rolling updates happen in REVERSE order (N→0)

These properties create unique failure modes.
```

#### 1. Pod Stuck in Unknown/Terminating — Blocks All Pods

```text
Symptom: StatefulSet has 3 replicas. myapp-2 is stuck Terminating.
         myapp-1 and myapp-0 continue running but NO scaling
         operations work (can't go to 4, can't go to 2).

Cause: StatefulSet enforces ORDERED operations. If myapp-2 is stuck
       Terminating, myapp-3 can't be created (must create in order).
       Scale-down is also blocked because myapp-2 couldn't terminate.
```

```bash
# Fix:
kubectl delete pod myapp-2 --force --grace-period=0
# If still stuck (finalizer or node gone):
kubectl patch pod myapp-2 -p '{"metadata":{"finalizers":[]}}' --type=merge
kubectl delete pod myapp-2 --force --grace-period=0
```

#### 2. PVC Not Deleted When StatefulSet Is Deleted

```text
StatefulSet PVCs are NOT automatically deleted when the StatefulSet
is deleted. This is by design (data safety).

If you delete/recreate a StatefulSet, old PVCs might still exist,
causing issues:
  - New myapp-0 tries to bind to a different PV because old PVC is still bound
  - Or new pod can't start because old PVC has a different storage class

Fix:
  kubectl delete pvc -l app=myapp    # delete all PVCs first
  kubectl delete statefulset myapp
  kubectl apply -f statefulset.yaml   # fresh start
```

#### 3. VolumeClaimTemplate Immutable Fields

```text
Once a StatefulSet is created, many volumeClaimTemplate fields
are IMMUTABLE (cannot be changed via kubectl apply):
  - storageClassName
  - resources.requests.storage

Error: "Forbidden: updates to statefulset spec for fields other than
       'replicas', 'template', and 'updateStrategy' are forbidden"

Fix:
  # Option A: Delete and recreate StatefulSet (pods + PVCs may persist)
  kubectl delete statefulset myapp --cascade=orphan
  kubectl apply -f statefulset-updated.yaml

  # Option B: If you only need to change storage size, you can
  # manually resize the PVC (if StorageClass supports expansion):
  kubectl patch pvc data-myapp-0 -p '{"spec":{"resources":{"requests":{"storage":"20Gi"}}}}'
```

#### 4. RollingUpdate Stuck (Partition)

```text
StatefulSet rolling updates have a `partition` field:
  spec:
    updateStrategy:
      rollingUpdate:
        partition: 3

Pods with ordinal >= partition are updated; pods < partition are NOT.

Symptom: StatefulSet shows "updateStrategy.type: RollingUpdate" but
         pods myapp-0 through myapp-2 never update.

Cause: Someone set `partition: 3` for a canary test. With 3 replicas,
       no pod has ordinal >= 3, so NO pods update.

Fix: Set partition: 0 to update all pods.
     kubectl patch statefulset myapp -p '{"spec":{"updateStrategy":{"rollingUpdate":{"partition":0}}}}'
```

---

## DaemonSet Troubleshooting

### DaemonSet-Specific Issues

#### 1. DaemonSet Pod Missing on Some Nodes

```bash
# Check which nodes should have pods
kubectl get nodes
kubectl get pods -n NAMESPACE -l app=myapp -o wide
# Compare: 5 nodes but only 4 daemonset pods

# Check DaemonSet status
kubectl describe daemonset myapp -n NAMESPACE
# Look for: "Number of Nodes Scheduled: 4 / 5"

# Check which nodes are missing pods
kubectl get pods -n NAMESPACE -l app=myapp -o custom-columns=NODE:.spec.nodeName | sort > /tmp/has-pod.txt
kubectl get nodes -o custom-columns=NODE:.metadata.name --no-headers | sort > /tmp/all-nodes.txt
diff /tmp/has-pod.txt /tmp/all-nodes.txt
```

Common reasons pods skip nodes:
| Reason | Diagnosis | Fix |
|--------|-----------|-----|
| **Node taint not tolerated** | `kubectl describe node missing-node \| grep Taints` | Add toleration to DaemonSet |
| **Node cordoned** | `kubectl get node missing-node` shows `SchedulingDisabled` | `kubectl uncordon NODE` |
| **NodeSelector mismatch** | DaemonSet has nodeSelector that doesn't match node labels | Update labels or nodeSelector |
| **Resource shortage** | Node doesn't have enough CPU/mem for pod requests | Reduce requests or add node capacity |

#### 2. DaemonSet Update Strategy

```bash
# Check update strategy
kubectl get daemonset myapp -n NAMESPACE -o jsonpath='{.spec.updateStrategy}'

# OnDelete strategy: pods only replaced when manually deleted
# RollingUpdate strategy: pods replaced one by one (with maxUnavailable control)
```

```text
Scenario: "DaemonSet pods not updating after configmap change"

Symptom: Updated ConfigMap, rolled out DaemonSet.
         New pods are created but old pods remain.

Cause: DaemonSet's updateStrategy is set to `OnDelete`.
       In OnDelete mode, pods are ONLY replaced when you manually
       delete them. The DaemonSet controller never auto-replaces pods.

Fix:
  # Change strategy to RollingUpdate
  kubectl patch daemonset myapp -n NAMESPACE \
    -p '{"spec":{"updateStrategy":{"type":"RollingUpdate","rollingUpdate":{"maxUnavailable":1}}}}'

  # Or if OnDelete is intentional (e.g., GPU workloads), manually delete:
  kubectl delete pod -n NAMESPACE -l app=myapp
```

---

## Jobs & CronJobs Troubleshooting

### Jobs

```bash
# Job status
kubectl get job my-job -n NAMESPACE
# NAME      COMPLETIONS   DURATION   AGE
# my-job    3/5           2m         5m

kubectl describe job my-job -n NAMESPACE

# Check job pods
kubectl get pods -n NAMESPACE -l job-name=my-job

# Check pod logs
kubectl logs job/my-job -n NAMESPACE

# Check ALL pod logs (including failed/completed)
kubectl logs -l job-name=my-job --all-containers=true
```

#### Common Job Issues

```text
1. Job never completes (infinite loop)
   → Check backoffLimit (default 6). After 6 pod failures, job marked Failed.
   → Check pod logs for why it's failing.
   → Consider setting activeDeadlineSeconds to prevent infinite runs.

2. Job creates too many pods
   → Check parallelism (default 1) and completions.
   → completions=100, parallelism=100 → 100 pods created at once.
   → completions=100, parallelism=10 → 10 pods at a time, 10 batches.

3. Job pods left after completion
   → Set ttlSecondsAfterFinished to auto-cleanup completed pods:
     spec:
       ttlSecondsAfterFinished: 3600  # delete after 1 hour

4. Job pod OOMKilled
   → Same diagnosis as pod OOMKilled. Increase memory limits.
   → Check backoffLimit — each OOM counts toward the limit.
```

### CronJobs

```bash
# Check cronjob schedule and last runs
kubectl get cronjob -n NAMESPACE
kubectl describe cronjob my-cronjob -n NAMESPACE

# Check if a specific scheduled run happened
kubectl get jobs -n NAMESPACE -l cronjob-name=my-cronjob

# Check last schedule time
kubectl get cronjob my-cronjob -n NAMESPACE -o json | jq '.status.lastScheduleTime'
```

#### Common CronJob Issues

```text
1. CronJob not firing
   → Check concurrencyPolicy:
     - Allow: always runs (may overlap)
     - Forbid: skips if previous job still running
     - Replace: kills previous job and starts new one
   → If concurrencyPolicy=Forbid and previous job hasn't finished,
     this run is SKIPPED. No error, just silent skip.
   
   → Check startingDeadlineSeconds: if >100 missed schedules, job is
     considered Failed and won't be retried.

2. Missed schedule (cluster was down)
   → If the control plane was down when a schedule should have fired,
     the CronJob controller will check if it missed schedules.
   → Only fires once per missed schedule if startingDeadlineSeconds
     is not exceeded.

3. Timezone issues
   → Kubernetes <=1.24: CronJob schedule uses controller's timezone (UTC).
   → Kubernetes >=1.25: Can set spec.timeZone: "America/New_York"

4. CronJob created jobs not auto-cleaned
   → Set successfulJobsHistoryLimit and failedJobsHistoryLimit
   → Default is 3 each; old jobs auto-deleted
   → Set to 0 to delete all completed jobs immediately
```

### Scenario: "CronJob silently not running"

```text
Symptom: CronJob scheduled for `*/5 * * * *` (every 5 min).
         Last run was 2 hours ago. No error events.

Diagnosis:
  kubectl get cronjob cleanup-job -n production -o yaml
  → concurrencyPolicy: Forbid
  → startingDeadlineSeconds: 300  (5 min)

  kubectl get jobs -n production -l cronjob-name=cleanup-job
  → cleanup-job-1718343000  Running  3h  ← a job that's been running for 3 HOURS
  → No other jobs

  The job from 3 hours ago is STILL RUNNING (hung on something).
  concurrencyPolicy=Forbid means all subsequent schedules are skipped
  because the previous job hasn't completed.

  kubectl logs job/cleanup-job-1718343000 -n production
  → Processing batch 1/1000000... ← stuck in an infinite loop
  → The script has a bug: it processes all items in a loop but
    never advances the cursor.

Fix:
  1. Kill the hung job: kubectl delete job cleanup-job-1718343000
  2. Fix the script's infinite loop bug
  3. Add activeDeadlineSeconds: 600 to prevent future hangs
  4. Or change concurrencyPolicy: Replace to auto-kill old jobs
```

---

## References

- [Deployments](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/)
- [StatefulSets](https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/)
- [DaemonSets](https://kubernetes.io/docs/concepts/workloads/controllers/daemonset/)
- [Jobs & CronJobs](https://kubernetes.io/docs/concepts/workloads/controllers/job/)
