# Scheduling Troubleshooting

> **Category:** Kubernetes | Scheduling | Taints | Affinity
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#kubernetes` `#scheduling` `#taints` `#affinity` `#nodes`

---

## Table of Contents

1. [Scheduler Basics & Diagnosis](#scheduler-basics--diagnosis)
2. [Taints & Tolerations](#taints--tolerations)
3. [Node Selector & Node Affinity](#node-selector--node-affinity)
4. [Pod Affinity & Anti-Affinity](#pod-affinity--anti-affinity)
5. [Topology Spread Constraints](#topology-spread-constraints)

---

## Scheduler Basics & Diagnosis

```bash
# Check scheduler status
kubectl get pods -n kube-system -l component=kube-scheduler

# Check scheduler logs
kubectl logs -n kube-system -l component=kube-scheduler --tail=50

# Why isn't my pod scheduled?
kubectl describe pod POD -n NAMESPACE | tail -20
# The Events section shows scheduler rejections

# Get scheduler events
kubectl get events -A --field-selector reason=FailedScheduling -o wide

# Simulate scheduling decision (doesn't exist natively, but debug with dry runs)
# The closest: look at pod events and node conditions
```

### Scheduling Pipeline

```text
The scheduler goes through these steps for each pod:

1. Filtering: Remove nodes that can't run the pod
   - Node has insufficient resources (CPU, memory, GPU, etc.)
   - Node has taints not tolerated by the pod
   - Node doesn't match nodeSelector/nodeAffinity
   - Node has disk/memory pressure
   - Port conflicts (hostPort/hostNetwork)
   - Volume zone mismatch

2. Scoring: Rank remaining nodes
   - Least requested resources (spread pods evenly)
   - Pod affinity/anti-affinity scores
   - Image locality (node already has image)
   - Node affinity preferences (weighted)

3. Binding: Assign pod to the highest-scoring node
```

---

## Taints & Tolerations

### How They Work

```text
Taint on node:     "This node should repel certain pods"
Toleration on pod: "This pod can tolerate certain taints"

Taint effects:
  NoSchedule:       Don't schedule NEW pods here (existing pods stay)
  PreferNoSchedule: Try not to schedule (soft preference)
  NoExecute:        Don't schedule AND evict EXISTING pods without toleration

Taint format: key=value:Effect
Example: node-role.kubernetes.io/control-plane:NoSchedule
         dedicated=monitoring:NoExecute
```

### Diagnosis

```bash
# View all node taints
kubectl get nodes -o custom-columns=NAME:.metadata.name,TAINTS:.spec.taints

# View a pod's tolerations
kubectl get pod POD -n NAMESPACE -o jsonpath='{.spec.tolerations}' | jq .

# Check why a pod wasn't scheduled on a specific node
kubectl describe node NODE | grep -A20 "Allocated resources"
# If the pod should be on this node but isn't, check taints
```

### Scenario: "Pod not scheduling after adding GPU taint"

```text
Symptom: Added taint nvidia.com/gpu=true:NoSchedule to GPU nodes.
         Now existing GPU-using pods are fine, but new pods won't schedule.

Diagnosis:
  kubectl get nodes -l nvidia.com/gpu=true -o custom-columns=NAME:.metadata.name,TAINTS:.spec.taints
  → gpu-node-1: [nvidia.com/gpu=true:NoSchedule]

  kubectl describe pod gpu-job-xyz
  → Warning  FailedScheduling  2m  default-scheduler
  → 0/1 nodes are available: 1 node(s) had untolerated taint {nvidia.com/gpu: true}

  The pod spec is missing the toleration:
  kubectl get pod gpu-job-xyz -o jsonpath='{.spec.tolerations}'
  → []

Fix:
  kubectl patch pod gpu-job-xyz -p '{"spec":{"tolerations":[{"key":"nvidia.com/gpu","operator":"Equal","value":"true","effect":"NoSchedule"}]}}'
  # But pods are mostly immutable after creation. Recreate instead:
  kubectl delete pod gpu-job-xyz  # if managed by a controller
  # Add toleration to the Deployment/Job spec
```

### Common Taint Patterns

```yaml
# Control plane nodes (automatic)
spec:
  tolerations:
  - key: node-role.kubernetes.io/control-plane
    operator: Exists
    effect: NoSchedule

# Dedicated node pool (e.g., only monitoring pods)
# Node taint: kubectl taint nodes NODE dedicated=monitoring:NoSchedule
# Pod toleration:
spec:
  tolerations:
  - key: dedicated
    operator: Equal
    value: monitoring
    effect: NoSchedule

# Allow everything on a node (remove all taints)
# kubectl taint nodes NODE node-role.kubernetes.io/control-plane:NoSchedule-
# The trailing "-" removes the taint
```

---

## Node Selector & Node Affinity

### Node Selector (Simple, Hard Requirement)

```yaml
spec:
  nodeSelector:
    disktype: ssd
    zone: us-east-1a
```

```bash
# Check if any nodes match
kubectl get nodes -l disktype=ssd,zone=us-east-1a

# If no nodes match, pod stays Pending forever with:
# "0/5 nodes are available: 5 node(s) didn't match node selector"
```

### Node Affinity (Complex, Hard + Soft)

```yaml
spec:
  affinity:
    nodeAffinity:
      # HARD requirement — pod won't schedule if not met
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms:
        - matchExpressions:
          - key: topology.kubernetes.io/zone
            operator: In
            values: [us-east-1a, us-east-1b]
      # SOFT preference — scheduler tries but doesn't guarantee
      preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        preference:
          matchExpressions:
          - key: instance-type
            operator: In
            values: [c5.xlarge, c5.2xlarge]
```

### Scenario: "Pod stuck because nodeSelector doesn't match any node"

```text
Symptom: All pods in a namespace are Pending.
         Events: "0/3 nodes are available: 3 node(s) didn't match node selector"

Diagnosis:
  kubectl get deployment myapp -n production -o yaml | grep -A5 nodeSelector
  → nodeSelector:
      env: production
      region: us-west-2

  kubectl get nodes --show-labels | grep "env=production"
  → 3 nodes with env=production

  kubectl get nodes --show-labels | grep "region=us-west-2"
  → 0 nodes! All nodes have region=us-east-1

  The region label was set during cluster bootstrapping and was
  never updated when the cluster was migrated to us-east-1.
  (Or more likely: someone copy-pasted from a us-west-2 config.)

Fix:
  # Option A: Fix the nodeSelector in the deployment
  kubectl patch deployment myapp -n production \
    -p '{"spec":{"template":{"spec":{"nodeSelector":{"region":"us-east-1"}}}}}'

  # Option B: Label the nodes correctly
  kubectl label nodes NODE region=us-west-2 --overwrite
  # (but this is wrong — the region really IS us-east-1)
```

### Operator Values

```text
nodeSelectorTerm matchExpressions operators:
  In:        value must be one of values[]
  NotIn:     value must NOT be in values[]
  Exists:    key exists (value ignored)
  DoesNotExist: key does not exist
  Gt:        value > given number (strings compared lexically!)
  Lt:        value < given number (strings compared lexically!)
```

---

## Pod Affinity & Anti-Affinity

### How It Works

```text
Pod affinity:     "Schedule me NEAR pods that match these labels"
Pod anti-affinity: "Schedule me AWAY FROM pods that match these labels"

Topology key: defines what "near" means
  kubernetes.io/hostname   → same node
  topology.kubernetes.io/zone → same availability zone
  topology.kubernetes.io/region → same region

Use cases:
  Affinity: co-locate cache with app for low latency
  Anti-affinity: spread replicas across zones for HA
```

### Anti-Affinity Failures

```bash
kubectl describe pod POD -n NAMESPACE | tail -20
# "0/5 nodes are available: 2 node(s) didn't match pod affinity/anti-affinity rules,
#  3 node(s) didn't match pod anti-affinity rules"

# This happens when:
# 1. You have 3 replicas but only 2 availability zones
# 2. Anti-affinity requires each pod in different zone
# 3. The 3rd pod has nowhere to go
```

### Scenario: "Deployment scaled to 4 but 3rd and 4th pods Pending"

```text
Symptom: Deployment with 4 replicas. Pods 1 and 2 are Running.
         Pods 3 and 4 are Pending.

Pod spec:
  affinity:
    podAntiAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
      - labelSelector:
          matchLabels:
            app: redis
        topologyKey: kubernetes.io/hostname

Diagnosis:
  kubectl get nodes
  → 2 worker nodes (node-1, node-2)

  The anti-affinity rule says: each pod must be on a DIFFERENT host.
  With 2 nodes, only 2 pods can be scheduled. Pods 3 and 4 have
  no node to go to.

Fix:
  # Option A: Use preferredDuringScheduling instead (soft):
  podAntiAffinity:
    preferredDuringSchedulingIgnoredDuringExecution:
    - weight: 100
      podAffinityTerm:
        labelSelector:
          matchLabels:
            app: redis
        topologyKey: kubernetes.io/hostname

  # Option B: Add more worker nodes
  # Option C: Reduce replicas to match available topology
  # Option D: Use topologyKey: topology.kubernetes.io/zone (coarser grain)
```

### Affinity/Anti-Affinity Performance Impact

```text
For large clusters (>1000 nodes, >100k pods):
  - requiredDuringScheduling rules are evaluated during FILTERING phase
  - Filters are fast but can slow down scheduling of many pods
  
  - Anti-affinity with required semantics is expensive:
    The scheduler must check ALL pods in the cluster against the rule.
    With 10,000 pods and an anti-affinity rule, that's 10,000 checks per pod.
  
  - Prefer soft (preferredDuringScheduling) over hard (required) when possible
  - Use smaller label selectors (don't use matchLabels: {})
  - Consider topology spread constraints as an alternative (more performant)
```

---

## Topology Spread Constraints

### How They Work (K8s 1.19+)

```yaml
spec:
  topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: topology.kubernetes.io/zone
    whenUnsatisfiable: DoNotSchedule
    labelSelector:
      matchLabels:
        app: myapp
```

```text
This ensures pods with app=myapp are evenly spread across zones.
maxSkew: 1 means no zone can have more than 1 pod more than any other zone.

Example: 5 replicas spread across 3 zones
  zone-a: 2 pods
  zone-b: 2 pods
  zone-c: 1 pod
  (max difference between any two zones = 1)
```

### Common Issues

```text
1. whenUnsatisfiable: DoNotSchedule + maxSkew: 1 + 5 zones, 4 pods
   → Pods: 1+1+1+1+0 (4 pods in 4 zones, 1 zone empty)
   → 5th pod won't schedule because it would create skew of 2
   → Use whenUnsatisfiable: ScheduleAnyway to allow skew violation

2. Default constraints from PodTopologySpread plugin
   → K8s 1.24+: kube-scheduler has default topology spread plugin
   → Can conflict with explicit podTopologySpreadConstraints

3. Missing label on some nodes
   → If a node doesn't have the topologyKey label, it's excluded
   → Check: kubectl get nodes --show-labels | grep topology
```

### Scenario: "Pod can't schedule due to topology spread constraint"

```text
Symptom: Deployment has 3 replicas, 3 zones. 2 pods Running, 3rd Pending.

  topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: topology.kubernetes.io/zone
    whenUnsatisfiable: DoNotSchedule

Diagnosis:
  kubectl get nodes -l topology.kubernetes.io/zone --show-labels
  → 4 nodes, only 2 zones labeled (zone-a, zone-b)
  → 2 nodes in zone-a, 2 nodes in zone-b
  → The 3rd node has no zone label — excluded from topology domain

  The 3rd pod would make either zone-a or zone-b have skew=1 (2 pods
  in one zone, 0 in the other), which violates maxSkew=1.

Wait — skew is calculated across ALL zones. With existing pods:
  zone-a: 1 pod
  zone-b: 1 pod
  (unnamed): 0 pods

  maxSkew = max(1, 1, 0) - min(1, 1, 0) = 1 → NOT exceeded yet
  3rd pod could go to either zone-a or zone-b (skew would be 1 in that zone)
  → Should be schedulable

  Actually, the unlabeled node is EXCLUDED from the topology domain.
  Only zone-a and zone-b are considered. Max skew between them is 0.
  Adding a 3rd pod to one zone would create skew=1. With 3 pods:
    zone-a: 2, zone-b: 1 → skew=1 → within limits!
    zone-a: 1, zone-b: 2 → skew=1 → within limits!
  3rd pod SHOULD be schedulable... unless another constraint blocks it.

  Check if there's also a nodeSelector or nodeAffinity restricting
  which nodes the pod can land on.

Fix: Check ALL scheduling constraints together. Use:
  kubectl describe pod POD -n NAMESPACE | tail -20
  # The scheduler event tells you exactly which constraints failed.
```

---

## References

- [Kubernetes Scheduler](https://kubernetes.io/docs/concepts/scheduling-eviction/kube-scheduler/)
- [Taints and Tolerations](https://kubernetes.io/docs/concepts/scheduling-eviction/taint-and-toleration/)
- [Node Affinity](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#affinity-and-anti-affinity)
- [Topology Spread Constraints](https://kubernetes.io/docs/concepts/scheduling-eviction/topology-spread-constraints/)
