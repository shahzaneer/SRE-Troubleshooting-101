# Operators & CRDs Troubleshooting

> **Category:** Kubernetes | Operators | CRDs | Custom Resources
> **Difficulty:** Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#kubernetes` `#operators` `#crds` `#custom-resources`

---

## Table of Contents

1. [CRD Lifecycle & Issues](#crd-lifecycle--issues)
2. [Operator Troubleshooting](#operator-troubleshooting)
3. [Finalizer Issues](#finalizer-issues)
4. [CRD Versioning & Conversion](#crd-versioning--conversion)

---

## CRD Lifecycle & Issues

### Quick Diagnosis

```bash
# List all CRDs
kubectl get crd

# Check CRD details
kubectl describe crd CRD_NAME

# Check CRD versions
kubectl get crd CRD_NAME -o json | jq '.spec.versions[] | {name: .name, served: .served, storage: .storage}'

# Check for stuck CRD deletions (finalizers)
kubectl get crd CRD_NAME -o json | jq '.metadata.deletionTimestamp, .metadata.finalizers'

# List all custom resources of a type
kubectl get RESOURCE_NAME -A
```

### Common CRD Issues

| Issue | Symptom | Fix |
|-------|---------|-----|
| **CRD stuck in Terminating** | `kubectl delete crd` hangs forever | Remove finalizers: `kubectl patch crd CRD -p '{"metadata":{"finalizers":[]}}' --type=merge` |
| **Custom resource not recognized** | "no matches for kind" | CRD not installed or wrong API group/version |
| **Schema validation fails** | "error: ... is invalid" | Check CRD's OpenAPI schema: `kubectl get crd CRD -o yaml \| grep -A50 openAPIV3Schema` |
| **Stored version removed** | "the server could not find the requested resource" | A served=false version was removed but old objects still exist in that version |
| **Too many CRDs** | API server latency spikes | Each CRD adds watch overhead. Limit to <200 CRDs per cluster. |

### Scenario: "Custom resource can't be created — schema validation error"

```text
Symptom: kubectl apply -f myresource.yaml fails:
         "The CustomResourceDefinition "myresources.stable.example.com" is invalid:
         metadata.annotations: Too long: must have at most 262144 bytes"

Wait, that's wrong. The actual schema validation:

Error: "MyResource.stable.example.com "prod-instance" is invalid:
        spec.replicas: Invalid value: 0: spec.replicas in body should be greater than or equal to 1"

Diagnosis:
  kubectl get crd myresources.stable.example.com -o json | jq '.spec.versions[0].schema.openAPIV3Schema.properties.spec.properties.replicas'
  → "minimum": 1

  The CRD schema defines minimum: 1 for replicas.
  But the YAML has replicas: 0. Schema validation catches it.

Fix: Set replicas >= 1, or update the CRD schema to allow 0:
  kubectl patch crd myresources.stable.example.com --type='json' \
    -p='[{"op":"replace","path":"/spec/versions/0/schema/openAPIV3Schema/properties/spec/properties/replicas/minimum","value":0}]'
```

### CRD Finalizer Deadlock

```text
When a CRD with resources is deleted:
  1. CRD gets deletionTimestamp
  2. Controller manager waits for ALL custom resources to be deleted
  3. Custom resources can only be deleted if their controller removes finalizers
  4. If the controller is down, custom resources are STUCK
  5. CRD is stuck in Terminating forever

Fix:
  # Delete ALL custom resources FIRST:
  kubectl get RESOURCE_NAME -A --no-headers | awk '{print $1, $2}' | xargs -n2 sh -c 'kubectl delete $0 $1 -n $2 --force --grace-period=0' || true
  
  # Remove finalizers from stuck CRs:
  for cr in $(kubectl get RESOURCE_NAME -A -o name); do
    kubectl patch $cr -p '{"metadata":{"finalizers":[]}}' --type=merge
  done
  
  # Now delete the CRD:
  kubectl delete crd CRD --force --grace-period=0
```

---

## Operator Troubleshooting

### Operator Architecture

```text
An Operator = CRD + Controller (Deployment) + RBAC

Controller watches custom resources and reconciles:
  1. Watch: Monitor custom resources (and related resources)
  2. Compare: Desired state vs actual state
  3. Act: Create/update/delete Kubernetes resources to match desired state

Common operators: Prometheus Operator, Cert Manager, Strimzi (Kafka), etcd Operator
```

### Diagnosis

```bash
# Check operator deployment
kubectl get deployment -A | grep operator

# Check operator logs
kubectl logs deployment/OPERATOR -n NAMESPACE --tail=50

# Check operator RBAC (it needs permissions for its resources)
kubectl get clusterrole,clusterrolebinding -A | grep OPERATOR

# Check if operator is reconciling
kubectl get events -A --sort-by=.lastTimestamp | grep OPERATOR

# Check operator metrics (if exposed)
kubectl exec deployment/OPERATOR -n NAMESPACE -- curl localhost:8080/metrics
```

### Common Operator Issues

```text
1. Operator not reconciling
   → Check operator pod logs for errors
   → Check operator has RBAC to manage its resources
   → Check if CRD is in the "established" state:
     kubectl get crd CRD_NAME -o json | jq '.status.conditions'

2. Operator in CrashLoopBackOff
   → Check operator's own health
   → OOMKilled? Operator may be loading too many CRs into memory
   → API server throttling? Operator makes too many requests

3. Operator creates infinite resources
   → Controller creates resource A → triggers watch event → creates resource B → etc.
   → Look for reconciliation loops in operator logs
   → Check for "level" or "triggered by" chain in events

4. Operator ignores updates to custom resources
   → Check if operator watches correctly (correct label/field selectors)
   → Check operator logs for "ignoring update" or similar
```

### Scenario: "Cert Manager not issuing certificates"

```text
Symptom: Created a Certificate resource. Status shows "Ready: False, Reason: Failed".
         No TLS secret created.

Diagnosis:
  kubectl get certificate -A
  → NAMESPACE  NAME     READY   SECRET    AGE
  → production myapp    False   my-tls    5m

  kubectl describe certificate myapp -n production
  → Status: False, Reason: Failed
  → CertificateRequest "myapp-abc123" is in state "Failed"

  kubectl get certificaterequest -n production
  → myapp-abc123  False   Failed  5m

  kubectl describe certificaterequest myapp-abc123 -n production
  → "Failed to perform ACME challenge: Unable to update ingress"

  kubectl get pods -n cert-manager
  → cert-manager-xxx  Running
  → cert-manager-cainjector-xxx  Running
  → cert-manager-webhook-xxx  Running

  kubectl logs deployment/cert-manager -n cert-manager --tail=20
  → "failed to create Order: acme: error: 400 :: urn:ietf:params:acme:error:rateLimited
  → Too many certificates already issued for myapp.example.com"

  Rate limited by Let's Encrypt! You can only issue 5 certificates
  per domain per week. Multiple failed attempts counted toward the limit.

Fix:
  # Wait until the rate limit resets (7 days from first failed attempt)
  # Or use the Let's Encrypt staging environment for testing:
  # issuer spec.acme.server: https://acme-staging-v02.api.letsencrypt.org/directory
  # (staging has much higher rate limits)
```

---

## Finalizer Issues

### What Are Finalizers?

```text
Finalizers are pre-delete hooks. When a resource has a finalizer,
the DELETE request sets deletionTimestamp but the resource is NOT
removed until the finalizer is cleared.

Kubernetes controllers use finalizers for cleanup:
  - PVC finalizer: don't delete PVC while pod is using it
  - Namespace finalizer: don't delete namespace until all resources are gone
  - Custom resource finalizers: operator cleanup (delete external resources)
```

### Stuck Finalizer Diagnosis

```bash
# Check if a resource has finalizers
kubectl get RESOURCE NAME -o json | jq '.metadata.finalizers'

# Check if deletionTimestamp is set
kubectl get RESOURCE NAME -o json | jq '.metadata.deletionTimestamp'

# If deletionTimestamp is set AND finalizers is non-empty → stuck
# The resource will never be deleted until finalizers are removed
```

### Removing Stuck Finalizers

```bash
# Namespace stuck Terminating
kubectl get namespace stuck-ns -o json | jq '.spec.finalizers = []' | kubectl replace --raw "/api/v1/namespaces/stuck-ns/finalize" -f -

# Or use patch:
kubectl patch namespace stuck-ns -p '{"spec":{"finalizers":[]}}' --type=merge

# Pod stuck Terminating
kubectl patch pod POD -n NAMESPACE -p '{"metadata":{"finalizers":[]}}' --type=merge

# PVC stuck Terminating
kubectl patch pvc PVC -n NAMESPACE -p '{"metadata":{"finalizers":[]}}' --type=merge

# CRD stuck Terminating
kubectl patch crd CRD_NAME -p '{"metadata":{"finalizers":[]}}' --type=merge
```

### Scenario: "Namespace stuck Terminating for hours"

```text
Symptom: kubectl delete namespace old-namespace → operation times out.
         Namespace shows "Terminating" for hours.

Diagnosis:
  kubectl get namespace old-namespace -o json | jq '.spec.finalizers'
  → ["kubernetes"]

  # Check what's still in the namespace
  kubectl get all -n old-namespace
  → No resources found (seems empty, but isn't)

  # Check for stuck resources (some resources don't show in "kubectl get all")
  kubectl api-resources --verbs=list --namespaced -o name | \
    xargs -n1 kubectl get --ignore-not-found -n old-namespace

  → Found: APIService "v1alpha1.mygroup.example.com" still exists
  → This APIService blocks namespace deletion

  kubectl delete apiservice v1alpha1.mygroup.example.com
  → Still stuck (APIService has its own finalizer)

  kubectl patch apiservice v1alpha1.mygroup.example.com \
    -p '{"metadata":{"finalizers":[]}}' --type=merge
  kubectl delete apiservice v1alpha1.mygroup.example.com

  # Now namespace should finish terminating
  # If still stuck, force finalizer removal:
  kubectl get namespace old-namespace -o json | \
    jq '.spec.finalizers = []' | \
    kubectl replace --raw "/api/v1/namespaces/old-namespace/finalize" -f -
```

---

## CRD Versioning & Conversion

### Version Lifecycle

```yaml
spec:
  versions:
  - name: v1alpha1
    served: true         # can be used in API requests
    storage: false       # NOT the stored version
    deprecated: true
    deprecationWarning: "v1alpha1 is deprecated, use v1"
  - name: v1
    served: true
    storage: true        # IS the stored version
```

### Common Version Issues

```text
1. A version is removed (served: false) but old objects were stored in that version
   → kubectl get shows error: "undefined version: v1alpha1"
   → Fix: Use stored version: kubectl get CR.v1.example.com or migrate objects

2. Conversion webhook not working
   → kubectl get crd -o yaml | grep conversion
   → If conversion strategy is Webhook, the webhook must be running
   → If webhook is down, API server can't serve different versions

3. Multiple stored versions
   → Only ONE version can have storage: true
   → Changing stored version requires migration
```

### Migrating Stored Version

```bash
# Check current stored version
kubectl get crd myresources.example.com -o json | jq '.status.storedVersions'

# Change storage version (CRD must already have the new version)
kubectl patch crd myresources.example.com --type='json' \
  -p='[{"op":"replace","path":"/spec/versions/0/storage","value":false},
       {"op":"replace","path":"/spec/versions/1/storage","value":true}]'

# Old objects remain in old version until they're updated.
# Force re-encoding by reading and updating:
kubectl get myresource --all-namespaces -o json | \
  jq '.items[] | {apiVersion, kind, metadata: {name, namespace}}' | \
  kubectl replace --save-config -f -
```

---

## References

- [Custom Resource Definitions](https://kubernetes.io/docs/concepts/extend-kubernetes/api-extension/custom-resources/)
- [Operator Pattern](https://kubernetes.io/docs/concepts/extend-kubernetes/operator/)
- [CRD Versioning](https://kubernetes.io/docs/tasks/extend-kubernetes/custom-resources/custom-resource-definition-versioning/)
