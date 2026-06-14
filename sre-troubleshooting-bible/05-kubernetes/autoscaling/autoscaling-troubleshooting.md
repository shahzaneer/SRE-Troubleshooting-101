# Autoscaling Troubleshooting

> **Category:** Kubernetes | HPA | VPA | Cluster Autoscaler
> **Difficulty:** Intermediate to Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#kubernetes` `#autoscaling` `#hpa` `#vpa` `#cluster-autoscaler`

---

## Table of Contents

1. [HPA Not Scaling](#hpa-not-scaling)
2. [HPA Scaling Too Aggressively](#hpa-scaling-too-aggressively)
3. [VPA Issues](#vpa-issues)
4. [Cluster Autoscaler Issues](#cluster-autoscaler-issues)

---

## HPA Not Scaling

### Diagnosis

```bash
# Check HPA status
kubectl get hpa -A

# Detailed HPA info
kubectl describe hpa HPA_NAME -n NAMESPACE

# Check HPA events
kubectl get events -n NAMESPACE --field-selector involvedObject.name=HPA_NAME

# Check metrics-server (required for CPU/mem HPA)
kubectl get pods -n kube-system -l k8s-app=metrics-server
kubectl top pods -n NAMESPACE    # verify metrics-server works
kubectl top nodes                # verify node metrics
```

### Common HPA Failures

| Symptom | Cause | Fix |
|---------|-------|-----|
| **HPA shows unknown/unknown** | metrics-server not running or not reporting | Check metrics-server: `kubectl get --raw /apis/metrics.k8s.io/v1beta1` |
| **HPA shows `<unknown>` for metric** | Pod missing resource requests | HPA needs `resources.requests.cpu` or `resources.requests.memory` on pods |
| **HPA target: 0%/50%** | No traffic → 0% utilization → HPA doesn't scale | Set minReplicas higher, or use external/custom metrics |
| **HPA stuck at same replicas** | Scale-up would exceed maxReplicas | Check maxReplicas: `kubectl get hpa -o yaml \| grep maxReplicas` |
| **HPA flapping** | Scale-up/down cooldown too short | Increase stabilization window (default 5 min down, 0 min up) |
| **HPA can't read custom metric** | Prometheus adapter not running or misconfigured | Check: `kubectl get --raw /apis/custom.metrics.k8s.io/v1beta1` |
| **HPA shows 0 metrics for pod** | Pod annotation mismatch or metric label wrong | Verify metric labels match pod labels |

### Scenario: "HPA shows unknown/unknown — metrics-server issue"

```text
Symptom: HPA shows CPU: <unknown>/50%, never scales.

Diagnosis:
  kubectl get hpa myapp -n production
  → TARGETS: <unknown>/50%

  kubectl get pods -n kube-system -l k8s-app=metrics-server
  → No resources found in kube-system namespace.
  → metrics-server is NOT installed!

  kubectl top pods
  → error: Metrics API not available

  The cluster was set up with kubeadm but metrics-server was never
  deployed. Without metrics-server, HPA can't get CPU/memory metrics.

Fix:
  kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
  
  # Wait and verify:
  kubectl get pods -n kube-system -l k8s-app=metrics-server
  kubectl top nodes
```

### Scenario: "HPA shows 0%/50% but pods are at 80% CPU"

```text
Symptom: HPA target is 50% CPU but shows current usage as 0%.
         kubectl top pods shows actual CPU is 80%.

Diagnosis:
  kubectl describe hpa myapp -n production
  → Metrics:                                               current / target
  → resource cpu on pods (as a percentage of request):     0% (0) / 50%

  The pod spec has NO resource requests set for CPU.
  kubectl get deployment myapp -n production -o yaml | grep -A5 resources
  → resources: {}   ← empty!

  HPA calculates utilization as: current_usage / requested * 100
  Without requests, HPA can't calculate the percentage → reports 0%.

Fix:
  kubectl patch deployment myapp -n production -p '{
    "spec": {
      "template": {
        "spec": {
          "containers": [{
            "name": "app",
            "resources": {
              "requests": {"cpu": "200m", "memory": "256Mi"},
              "limits": {"cpu": "500m", "memory": "512Mi"}
            }
          }]
        }
      }
    }
  }'
  # This triggers a rolling restart. After restart, HPA will see requests.
```

### HPA Behavior Tuning

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: myapp
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: myapp
  minReplicas: 2
  maxReplicas: 10
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 50
  - type: Resource
    resource:
      name: memory
      target:
        type: Utilization
        averageUtilization: 70
  behavior:
    scaleDown:
      stabilizationWindowSeconds: 300   # wait 5min before scaling down
      policies:
      - type: Percent
        value: 50                       # remove at most 50% of pods per period
        periodSeconds: 60
      - type: Pods
        value: 2                        # OR at most 2 pods
        periodSeconds: 60
      selectPolicy: Min                 # use the MORE conservative policy (scale down less)
    scaleUp:
      stabilizationWindowSeconds: 0     # scale up immediately
      policies:
      - type: Percent
        value: 100
        periodSeconds: 15
      - type: Pods
        value: 4
        periodSeconds: 15
      selectPolicy: Max                 # use the MORE aggressive policy (scale up more)
```

### Custom & External Metrics

```bash
# Check if Prometheus adapter is installed
kubectl get pods -n monitoring | grep prometheus-adapter
# Or:
kubectl get apiservice v1beta1.custom.metrics.k8s.io

# Check available custom metrics
kubectl get --raw /apis/custom.metrics.k8s.io/v1beta1 | jq '.resources[].name' | head

# Example HPA using Prometheus metric:
# metrics:
# - type: Object
#   object:
#     metric:
#       name: nginx_ingress_controller_requests_per_second
#     describedObject:
#       apiVersion: networking.k8s.io/v1
#       kind: Ingress
#       name: myapp
#     target:
#       type: Value
#       value: "100"
```

---

## HPA Scaling Too Aggressively

### HPA + Cluster Autoscaler Cascading

```text
The dangerous cascade:
  1. Traffic spikes → HPA scales pods from 3 → 10
  2. New pods are Pending (no nodes available)
  3. Cluster Autoscaler adds nodes (takes 3-5 minutes)
  4. During those 3-5 min, HPA sees CPU still high on existing 3 pods
  5. HPA scales again: 10 → 30 pods
  6. Cluster Autoscaler adds MORE nodes
  7. All pods start, CPU drops to 5%
  8. HPA scales down: 30 → 3 pods
  9. Cluster Autoscaler removes excess nodes

Prevention:
  - Set maxReplicas to a reasonable cap
  - Use HPA + Cluster Autoscaler with Overprovisioning:
    - Run low-priority "placeholder" pods that occupy space
    - When real pods need space, placeholder pods are evicted
    - Node is already warm (no CA delay)
  - Use KEDA for event-driven autoscaling
```

### Stabilization Windows

```yaml
behavior:
  scaleDown:
    stabilizationWindowSeconds: 300  # don't scale down for 5 min
  scaleUp:
    stabilizationWindowSeconds: 60   # wait 1 min before scaling up more
```

---

## VPA Issues

### What VPA Does

```text
VPA (Vertical Pod Autoscaler) adjusts resource REQUESTS based on
historical usage. Three modes:

1. Off:      Only provides recommendations (no changes)
2. Initial:  Sets requests at pod creation (no restarts)
3. Auto:     Sets requests AND restarts pods with new values

VPA is NOT a replacement for HPA — they solve different problems.
HPA: adds/removes pods (horizontal)
VPA: increases/decreases pod resources (vertical)
```

### Diagnosis

```bash
# Check VPA recommendations
kubectl get vpa -A

# Detailed VPA info
kubectl describe vpa VPA_NAME -n NAMESPACE

# Check VPA components (if using upstream VPA)
kubectl get pods -n kube-system | grep vpa
```

### Common VPA Issues

```text
1. VPA in Auto mode restarts pods unexpectedly
   → VPA evicts pod to apply new resource requests
   → Pod disruption can cause brief outages
   → Use Initial mode for production:
     VPA sets requests at pod creation, pod lifecycle manages itself

2. VPA + HPA conflict
   → VPA adjusts CPU requests (changing the baseline)
   → HPA scales based on CPU utilization (based on those requests)
   → If VPA increases requests from 200m to 800m, HPA utilization drops
   → HPA may scale DOWN even though workload is the same!
   → Fix: Use VPA with HPA, but configure HPA on custom metric (not CPU%)
   → Or: Only use VPA for memory, HPA for CPU

3. VPA recommendations not showing
   → VPA needs metrics-server or Prometheus
   → Check VPA recommender logs:
     kubectl logs -n kube-system deployment/vpa-recommender
```

---

## Cluster Autoscaler Issues

### Diagnosis

```bash
# Check Cluster Autoscaler pod
kubectl get pods -n kube-system -l app=cluster-autoscaler

# Check CA logs
kubectl logs -n kube-system deployment/cluster-autoscaler --tail=50

# Check CA status configmap
kubectl get configmap cluster-autoscaler-status -n kube-system -o yaml
```

### Common CA Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| **Pending pods, CA adds no nodes** | CA can't scale node group (IAM, quota, or ASG limit) | Check CA logs for: "could not scale up", check IAM permissions |
| **CA removes node with pods on it** | Pods don't have PDB (PodDisruptionBudget), or use emptyDir | CA won't drain nodes with pods lacking controllers or with local storage |
| **Node scale up takes too long** | AMI has many packages to install, or large bootstrap script | Use pre-baked AMIs with minimal bootstrap |
| **CA not scaling down** | Pods have local storage, or are not controlled by a controller | Pods must be managed by Deployment/StatefulSet/etc. |
| **Node underutilized but CA keeps it** | Scale-down delay (default 10 min) or node has annotation | Check: `kubectl describe node NODE | grep scale-down` |
| **CA scales up wrong node group** | Similar node groups with overlapping resource requests | Use nodeSelector or nodeAffinity to target specific node groups |

### Scenario: "CA not scaling down idle nodes"

```text
Symptom: Cluster has 10 nodes. After traffic drop, only 3 are needed.
         7 nodes sit idle at 0% CPU/5% memory. CA doesn't scale down.

Diagnosis:
  kubectl logs -n kube-system deployment/cluster-autoscaler | grep "scale down"
  → "Node node-5 is not suitable for removal - node is annotated with scale-down disabled"

  kubectl describe node node-5 | grep scale-down
  → "cluster-autoscaler.kubernetes.io/scale-down-disabled: true"

  Someone annotated the node to protect it from scale-down. CA respects
  this annotation and skips the node.

  Check other nodes:
  kubectl get nodes -o json | jq -r '.items[] | select(.metadata.annotations["cluster-autoscaler.kubernetes.io/scale-down-disabled"] == "true") | .metadata.name'
  → node-5 through node-10: all annotated

Fix:
  kubectl annotate node node-5 node-6 node-7 node-8 node-9 node-10 \
    cluster-autoscaler.kubernetes.io/scale-down-disabled-
```

### Pod Disruption Budget (PDB)

```yaml
# Without PDB, CA can evict ALL pods from a deployment simultaneously
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: myapp-pdb
spec:
  minAvailable: 2       # at least 2 pods must always be running
  # OR:
  # maxUnavailable: 1   # at most 1 pod can be unavailable
  selector:
    matchLabels:
      app: myapp
```

```bash
# Check PDBs
kubectl get pdb -A

# Check if PDB is blocking node drain or CA scale-down
kubectl describe pdb myapp-pdb -n NAMESPACE
# "Allowed disruptions: 0"
# This means: no pods can be disrupted without violating the PDB.
```

---

## References

- [HPA Walkthrough](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale-walkthrough/)
- [HPA Behavior](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/#configurable-scaling-behavior)
- [VPA (Vertical Pod Autoscaler)](https://github.com/kubernetes/autoscaler/tree/master/vertical-pod-autoscaler)
- [Cluster Autoscaler FAQ](https://github.com/kubernetes/autoscaler/blob/master/cluster-autoscaler/FAQ.md)
