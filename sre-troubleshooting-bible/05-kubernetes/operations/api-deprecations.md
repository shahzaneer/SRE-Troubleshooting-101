# API Deprecations & Upgrades

> **Category:** Kubernetes | API Deprecations | Cluster Upgrades
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#kubernetes` `#api-deprecation` `#upgrades` `#versioning`

---

## Table of Contents

1. [Detecting Deprecated APIs](#detecting-deprecated-apis)
2. [Common Deprecations by Version](#common-deprecations-by-version)
3. [Pre-Upgrade Checks](#pre-upgrade-checks)
4. [Post-Upgrade Recovery](#post-upgrade-recovery)

---

## Detecting Deprecated APIs

### Check Before Upgrade

```bash
# Check Kubernetes version
kubectl version --short

# Check API resources available
kubectl api-resources

# Check specific API versions for a resource
kubectl explain deployment --api-version=apps/v1

# Check deprecation status (K8s 1.25+)
kubectl get --raw /metrics | grep apiserver_requested_deprecated_apis

# Use pluto to detect deprecated APIs (FairWinds tool)
pluto detect-helm -n NAMESPACE
pluto detect-files -d ./manifests/

# Use kubent (kube-no-trouble) to find deprecated resources in cluster
kubent
# Output:
#   NAMESPACE    KIND              VERSION           REPLACEMENT
#   production   Ingress           extensions/v1beta1  networking.k8s.io/v1
#   default      PodSecurityPolicy  policy/v1beta1     (removed)
```

### API Deprecation Policy

```text
Kubernetes follows this deprecation policy:
  - Alpha (v1alpha1): may be removed at any time. NO DEPRECATION NOTICE.
  - Beta (v1beta1): deprecated 3 releases or 9 months (whichever is longer)
    AFTER the replacement reaches GA.
  - GA (v1): deprecated in a newer API version. Support for at least 12 months
    or 3 releases (whichever is longer).

Example:
  networking.k8s.io/v1beta1 Ingress → deprecated in 1.19, removed in 1.22
  (3 releases later: 1.19, 1.20, 1.21 → removed in 1.22)
```

---

## Common Deprecations by Version

### v1.29 (Current as of 2026)

| Removed/Changed | Replacement |
|-----------------|-------------|
| In-tree cloud providers fully removed | Use external cloud controller managers |
| `flowcontrol.apiserver.k8s.io/v1beta2` | `flowcontrol.apiserver.k8s.io/v1beta3` |
| `security.openshift.io/v1` (some fields) | Updated security context constraints |

### v1.28

| Removed | Replacement |
|---------|-------------|
| `policy/v1beta1` PodSecurityPolicy | Pod Security Admission (built-in) |
| `autoscaling/v2beta2` HorizontalPodAutoscaler | `autoscaling/v2` |

### v1.27

| Removed | Replacement |
|---------|-------------|
| CSIStorageCapacity `storage.k8s.io/v1beta1` | `storage.k8s.io/v1` |
| In-tree storage plugin migration (GA) | CSI drivers for all volume types |

### v1.26

| Removed | Replacement |
|---------|-------------|
| `flowcontrol.apiserver.k8s.io/v1beta1` | `flowcontrol.apiserver.k8s.io/v1beta2` |

### v1.25

| Removed | Replacement |
|---------|-------------|
| `policy/v1beta1` PodDisruptionBudget | `policy/v1` PDB |
| `batch/v1beta1` CronJob | `batch/v1` CronJob |
| `discovery.k8s.io/v1beta1` EndpointSlice | `discovery.k8s.io/v1` |
| `autoscaling/v2beta1` HorizontalPodAutoscaler | `autoscaling/v2` |
| `node.k8s.io/v1beta1` RuntimeClass | `node.k8s.io/v1` |
| `events.k8s.io/v1beta1` | `events.k8s.io/v1` |

### v1.22 (Major Cleanup)

| Removed | Replacement |
|---------|-------------|
| `extensions/v1beta1`, `networking.k8s.io/v1beta1` Ingress | `networking.k8s.io/v1` |
| `apiextensions.k8s.io/v1beta1` CRD | `apiextensions.k8s.io/v1` |
| `certificates.k8s.io/v1beta1` CSR | `certificates.k8s.io/v1` |
| `rbac.authorization.k8s.io/v1beta1` | `rbac.authorization.k8s.io/v1` |
| `admissionregistration.k8s.io/v1beta1` | `admissionregistration.k8s.io/v1` |

---

## Pre-Upgrade Checks

### Checklist

```bash
# 1. Current version
kubectl version --short

# 2. Find all deprecated resources IN CLUSTER
kubent    # https://github.com/doitintl/kube-no-trouble
# OR:
kubectl get --raw /metrics | grep apiserver_requested_deprecated_apis

# 3. Find all deprecated resources IN MANIFESTS (git/GitOps)
pluto detect-files -d ./kubernetes/

# 4. Check Helm releases for deprecated APIs
pluto detect-helm -A

# 5. Check CRDs for compatibility
kubectl get crd -o yaml | grep -E "served|storage|version"

# 6. Check addons compatibility (metrics-server, ingress, CSI, etc.)
kubectl get pods -n kube-system -o wide
kubectl get pods -n ingress-nginx -o wide

# 7. Take etcd backup (see etcd Backup & Restore guide)

# 8. Check for custom webhooks
kubectl get validatingwebhookconfigurations,mutatingwebhookconfigurations

# 9. Verify all control plane nodes are healthy
kubectl get nodes -l node-role.kubernetes.io/control-plane
```

### Migrating Deprecated API Resources

```bash
# Example: Migrate Ingress from extensions/v1beta1 to networking.k8s.io/v1

# 1. Get all ingresses using old API
kubectl get ingress -A -o yaml > /tmp/old-ingresses.yaml

# 2. Convert (manual or tool-based)
#    Key changes:
#    - apiVersion: networking.k8s.io/v1
#    - spec.backend → spec.defaultBackend
#    - backend.serviceName → backend.service.name
#    - backend.servicePort → backend.service.port.number
#    - pathType is REQUIRED (Prefix, Exact, ImplementationSpecific)

# 3. Or use kubectl convert plugin:
kubectl-convert -f old-ingress.yaml --output-version networking.k8s.io/v1

# 4. Verify
kubectl apply -f new-ingress.yaml --dry-run=client

# 5. Delete old and apply new (Ingress is immutable for apiVersion changes)
kubectl delete ingress OLD_INGRESS -n NAMESPACE
kubectl apply -f new-ingress.yaml
```

---

## Post-Upgrade Recovery

### Common Post-Upgrade Issues

| Issue | Cause | Fix |
|-------|-------|-----|
| **"no matches for kind"** | Resource YAML uses removed API version | Update YAML to supported API version |
| **CRD not serving** | CRD schema incompatible with new K8s version | Update CRD version, run conversion |
| **Webhook not working** | Webhook uses removed API version | Update webhook to current API version |
| **CSI driver not working** | Driver incompatible with new K8s version | Update CSI driver to compatible version |
| **Feature gate removed** | Code relying on alpha feature that was removed | Update to stable alternative or re-enable if graduated |

### Scenario: "All ingresses broken after upgrade to 1.22"

```text
Symptom: After upgrading from K8s 1.21 to 1.22, all ingresses return 404.
         kubectl get ingress shows no ingresses.

Diagnosis:
  kubectl get ingress -A
  → No resources found

  But ingresses WERE there before the upgrade. What happened?

  K8s 1.22 REMOVED extensions/v1beta1 and networking.k8s.io/v1beta1
  Ingresses. If your ingresses were created with the old API version,
  they were stored in etcd with that version.
  
  When the API version is removed, the API server can no longer
  serve those resources (even though they exist in etcd).

  The INGRESS CONTROLLER (nginx-ingress) can read ingresses in
  either old or new format... but if the API server doesn't
  recognize the old version, the controller can't read them.

Fix:
  # 1. The old ingresses still exist in etcd but are inaccessible.
  #    You need to restore from etcd backup (before upgrade) OR
  #    recreate ingresses using networking.k8s.io/v1.

  # 2. This is why you MUST migrate BEFORE upgrading.
  
  # 3. If you have the manifest files (GitOps):
  for f in ./kubernetes/ingress/*.yaml; do
    # Update apiVersion to networking.k8s.io/v1
    # Update schema (pathType required, backend structure)
    kubectl apply -f $f
  done

  # 4. Verify:
  kubectl get ingress -A
```

### Scenario: "kubectl convert not available"

```text
kubectl convert was removed from kubectl in 1.24.
It's now a separate binary.

Install:
  curl -LO https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl-convert
  chmod +x kubectl-convert
  sudo mv kubectl-convert /usr/local/bin/

Or use inline conversion:
  # Ingress v1beta1 → v1
  kubectl get ingress myapp -n production -o yaml | sed \
    -e 's|extensions/v1beta1|networking.k8s.io/v1|' \
    -e 's|networking.k8s.io/v1beta1|networking.k8s.io/v1|' \
    -e '/^status:/,$d' \
    | kubectl apply -f -
```

### Upgrade Rollback

```text
Can you rollback a Kubernetes upgrade?
  Control plane: YES (kubeadm, but etcd must NOT have been upgraded)
  Worker nodes:  YES (if API versions are still compatible)
  etcd:          Can only downgrade ONE minor version at a time

kubeadm upgrade rollback: NOT SUPPORTED for multi-version jumps.
  If upgrade from 1.27 → 1.28 fails:
  - API server: can restart old binary
  - etcd: if NOT upgraded, can run old control plane
  - If etcd WAS upgraded: recovery may require backup restore

BEST PRACTICE:
  - Always backup etcd BEFORE any upgrade
  - Test upgrades in staging cluster first
  - Upgrade ONE minor version at a time (1.27 → 1.28, not 1.27 → 1.30)
```

---

## References

- [Kubernetes Deprecation Policy](https://kubernetes.io/docs/reference/using-api/deprecation-policy/)
- [API Version Changes](https://kubernetes.io/docs/reference/using-api/deprecation-guide/)
- [Pluto (FairWinds)](https://github.com/FairwindsOps/pluto)
- [kube-no-trouble](https://github.com/doitintl/kube-no-trouble)
- [kubectl-convert](https://kubernetes.io/docs/tasks/tools/included/kubectl-convert-overview/)
