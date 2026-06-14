# Security Troubleshooting

> **Category:** Kubernetes | RBAC | ServiceAccounts | PodSecurity | ResourceQuotas
> **Difficulty:** Intermediate to Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#kubernetes` `#security` `#rbac` `#serviceaccounts`

---

## Table of Contents

1. [RBAC Troubleshooting](#rbac-troubleshooting)
2. [ServiceAccount Issues](#serviceaccount-issues)
3. [Pod Security Troubleshooting](#pod-security-troubleshooting)
4. [ResourceQuota & LimitRange](#resourcequota--limitrange)

---

## RBAC Troubleshooting

### Quick Diagnosis

```bash
# Check if you can perform an action
kubectl auth can-i create pods
kubectl auth can-i delete pods --namespace production
kubectl auth can-i '*' '*' --all-namespaces    # am I admin?

# Check what a service account can do
kubectl auth can-i list pods --as system:serviceaccount:default:my-sa
kubectl auth can-i --list --as system:serviceaccount:production:deployer-sa

# List all RBAC resources
kubectl get roles,rolebindings -A
kubectl get clusterroles,clusterrolebindings

# Show who has what access (imperative)
kubectl get rolebindings,clusterrolebindings -A -o custom-columns=\
KIND:.kind,\
NAME:.metadata.name,\
ROLE:.roleRef.name,\
SUBJECTS:.subjects[*].name

# Describe specific role
kubectl describe role my-role -n NAMESPACE
kubectl describe clusterrole my-clusterrole
```

### Common RBAC Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| **"cannot list resource pods"** | Missing Role/ClusterRole for the verb+resource | Create appropriate Role + RoleBinding |
| **"cannot get resource pods in API group"** | Role references wrong API group | Check `apiGroups` in Role (e.g., `["apps"]` for deployments, `[""]` for pods) |
| **Can access pods but not pod logs** | pods/log is a SUBRESOURCE, needs separate permission | Role must include `resources: ["pods/log"]` |
| **Can exec into pods but not list them** | pods/exec is a subresource | Role: `resources: ["pods/exec"]` |
| **ClusterRole vs Role confusion** | Role is NAMESPACED, ClusterRole is GLOBAL | Role + RoleBinding: only in that namespace. ClusterRole + RoleBinding: only in that namespace with global definition |
| **Can't create CRDs** | CRD creation requires cluster-admin or specific customresourcedefinitions permission | Grant appropriate ClusterRole |
| **RBAC changes not taking effect** | RBAC is eventually consistent (seconds to minutes) | Wait or restart the component (kube-apiserver caches RBAC) |

### Scenario: "Service account can't list pods in its own namespace"

```text
Symptom: A CI/CD pipeline runs `kubectl get pods -n production` and fails:
         "Error from server (Forbidden): pods is forbidden: User
          "system:serviceaccount:production:deployer-sa" cannot list
          resource "pods" in API group "" in the namespace "production""

Diagnosis:
  # Check if the service account exists
  kubectl get sa deployer-sa -n production
  → Exists ✓

  # Check what roles are bound to it
  kubectl get rolebindings -n production -o yaml | grep -A10 deployer-sa
  → No RoleBinding found for deployer-sa ✗

  # Check what ClusterRoleBindings reference it
  kubectl get clusterrolebindings -o yaml | grep -A10 deployer-sa
  → No ClusterRoleBinding found ✗

  # Check what permissions the SA actually has
  kubectl auth can-i list pods --as system:serviceaccount:production:deployer-sa -n production
  → No

Fix:
  # Create Role and RoleBinding
  kubectl create role deployer-role -n production \
    --verb=get,list,watch,create,update,patch,delete \
    --resource=pods,deployments,services,configmaps,secrets

  kubectl create rolebinding deployer-binding -n production \
    --role=deployer-role \
    --serviceaccount=production:deployer-sa
```

### Scenario: "kubectl exec works but kubectl logs doesn't"

```text
Role YAML:
  rules:
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list"]
  - apiGroups: [""]
    resources: ["pods/exec"]
    verbs: ["create"]

kubectl exec -it myapp -- /bin/bash → works ✓
kubectl logs myapp → "Forbidden: pods/log is forbidden" ✗

Cause: pods/log is a SEPARATE subresource from pods/exec.
       Each subresource needs its own explicit permission.

Fix:
  rules:
  - apiGroups: [""]
    resources: ["pods", "pods/log", "pods/exec", "pods/portforward"]
    verbs: ["get", "list", "create"]
```

### Debugging RBAC: Who Can Do What?

```bash
# Find ALL role bindings for a specific user/service account
kubectl get rolebindings,clusterrolebindings -A -o json | \
  jq -r '.items[] | select(.subjects[]? .name=="deployer-sa") | 
  "\(.kind): \(.metadata.namespace)/\(.metadata.name) → \(.roleRef.name)"'

# Find all users with a specific ClusterRole
kubectl get clusterrolebindings -o json | \
  jq -r '.items[] | select(.roleRef.name=="cluster-admin") | 
  .subjects[]? | "\(.kind): \(.name)"'

# Trace a subject's effective permissions (manual)
# Check: ClusterRoleBindings → ClusterRoles → rules
# Check: RoleBindings → Roles → rules
# The effective permissions = UNION of all matching rules
```

### RBAC for CRDs (Custom Resources)

```text
CRDs require apiGroups in the Role:
  rules:
  - apiGroups: ["stable.example.com"]   # CRD group
    resources: ["myresources"]
    verbs: ["get", "list", "create"]

CRD subresources:
  resources: ["myresources/status"]
  → needed to update .status subresource
```

---

## ServiceAccount Issues

### ServiceAccount Tokens (K8s 1.24+)

```text
K8s 1.24+: Service account tokens are NOT automatically mounted.
           You must explicitly create a Secret or use TokenRequest.

OLD behavior (≤1.23):
  Every SA had a Secret with a long-lived token auto-created.

NEW behavior (≥1.24):
  Tokens are obtained via TokenRequest API (bound tokens, time-limited).
  OR you manually create a Secret.
```

### Diagnosis

```bash
# Check service account
kubectl get sa my-sa -n NAMESPACE -o yaml

# Check if SA has an auto-generated token secret (≤1.23)
kubectl get secrets -n NAMESPACE | grep my-sa-token

# For 1.24+, create a token:
kubectl create token my-sa -n NAMESPACE
# Or:
kubectl create token my-sa -n NAMESPACE --duration=1h

# Check which SA a pod uses
kubectl get pod POD -n NAMESPACE -o jsonpath='{.spec.serviceAccountName}'

# Check mounted token
kubectl exec POD -n NAMESPACE -- cat /var/run/secrets/kubernetes.io/serviceaccount/token
```

### Common SA Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| **Pod uses "default" SA and gets 403** | default SA has minimal/no permissions | Create a dedicated SA with proper RBAC |
| **Token not auto-mounted** | `automountServiceAccountToken: false` in spec | Set to `true` or mount manually |
| **Token expired** (1.24+ bound tokens) | Token lifetime exceeded (default 1h) | Use TokenRequest for fresh token; pods auto-refresh |
| **SA in different namespace** | Pod can only use SAs in its own namespace | Create SA in the same namespace |
| **IRSA not working (EKS)** | Service account annotation missing or OIDC provider misconfigured | Check SA annotation: `eks.amazonaws.com/role-arn` |

### Scenario: "Pod can't access AWS resources via IRSA"

```text
Symptom: Pod runs `aws s3 ls` and gets "AccessDenied".

Diagnosis:
  # Check SA annotation for IRSA role
  kubectl get sa myapp-sa -n production -o yaml | grep eks.amazonaws.com/role-arn
  → eks.amazonaws.com/role-arn: arn:aws:iam::123456789:role/myapp-role ✓

  # Check if pod is using the SA
  kubectl get pod myapp-abc -n production -o yaml | grep serviceAccountName
  → serviceAccountName: myapp-sa ✓

  # Check the AWS credentials file in the pod
  kubectl exec myapp-abc -n production -- cat $AWS_WEB_IDENTITY_TOKEN_FILE
  → Token exists ✓

  # Check the IAM role's trust policy
  aws iam get-role --role-name myapp-role
  → Trust policy has wrong OIDC provider URL (old cluster's OIDC)

Fix: Update IAM role trust policy to use current cluster's OIDC provider.
```

---

## Pod Security Troubleshooting

### Pod Security Standards (PSS)

```text
K8s 1.25+: Pod Security Admission replaces PodSecurityPolicies (deprecated).

Three levels:
  privileged:  Unrestricted (like PSP didn't exist)
  baseline:    Prevents known privilege escalations
  restricted:  Strict pod hardening (best practice for multi-tenant)

Three modes:
  enforce:  Violations are REJECTED
  audit:    Violations are LOGGED but allowed
  warn:     Violations get a WARNING but are allowed
```

### Diagnosis

```bash
# Check namespace Pod Security labels
kubectl get namespace NAMESPACE -o yaml | grep pod-security

# Check if pod is rejected due to PSS
kubectl get events -n NAMESPACE --field-selector reason=FailedCreate
# Look for: "violates PodSecurity..."

# Check audit logs for PSS violations
kubectl logs -n kube-system -l component=kube-apiserver | grep pod-security

# Check running pods against PSS standards
kubectl label --dry-run=server --overwrite ns NAMESPACE \
  pod-security.kubernetes.io/enforce=restricted
# If this would break pods, it'll show warnings
```

### Scenario: "Pod rejected by Pod Security Admission"

```text
Symptom: New deployment pods fail to create.
         Events: "violates PodSecurity 'restricted:latest': allowPrivilegeEscalation != false"

Pod spec:
  containers:
  - name: app
    image: myapp:v1
    # Missing securityContext!

The namespace has:
  pod-security.kubernetes.io/enforce: restricted

Restricted PSS requires:
  - Must NOT run as root (runAsNonRoot: true)
  - Must drop ALL capabilities (drop: [ALL])
  - Must NOT allow privilege escalation (allowPrivilegeEscalation: false)
  - Seccomp profile must be RuntimeDefault or Localhost

Fix:
  containers:
  - name: app
    image: myapp:v1
    securityContext:
      runAsNonRoot: true
      allowPrivilegeEscalation: false
      capabilities:
        drop: ["ALL"]
      seccompProfile:
        type: RuntimeDefault
```

### Pod Security Context Reference

```yaml
spec:
  # Pod-level security context
  securityContext:
    runAsUser: 1000
    runAsGroup: 3000
    fsGroup: 2000
    fsGroupChangePolicy: OnRootMismatch
  
  containers:
  - name: app
    # Container-level security context (overrides pod-level)
    securityContext:
      runAsNonRoot: true
      allowPrivilegeEscalation: false
      readOnlyRootFilesystem: true
      capabilities:
        drop:
        - ALL
      seccompProfile:
        type: RuntimeDefault
```

---

## ResourceQuota & LimitRange

### ResourceQuota Diagnosis

```bash
# List all quotas
kubectl get resourcequota -A

# Check quota usage
kubectl describe resourcequota QUOTA_NAME -n NAMESPACE

# Check if quota is blocking resource creation
kubectl get events -n NAMESPACE --field-selector reason=FailedCreate
# "exceeded quota: default-quota, requested: pods=1, used: pods=10, limited: pods=10"
```

### Common Issues

```text
1. "Cannot create resource: exceeded quota"
   → Namespace has hit its limit for a resource (pods, CPU, memory, etc.)
   → kubectl describe resourcequota shows current usage

2. "Cannot create pods: request CPU exceeds limitrange"
   → LimitRange sets min/max/default CPU per pod
   → Pod spec must have resources within allowed range

3. "Cannot create PVC: exceeded quota for storage requests"
   → ResourceQuota limits total PVC storage
   → Delete unused PVCs or increase quota

4. Pod created but immediately killed (not CrashLoopBackOff)
   → LimitRange might have default limits that are too low
   → Check: kubectl describe limitrange -n NAMESPACE
```

### Scenario: "Can't create any new pods despite available resources"

```text
Symptom: Nodes have plenty of free CPU/memory. But any new pod
         creation fails with "exceeded quota".

Diagnosis:
  kubectl get resourcequota -n production
  → NAME           AGE   REQUEST                                     LIMIT
  → default-quota  30d   requests.cpu: 8/8, requests.memory: 16Gi/16Gi

  The namespace quota for CPU requests is 8 cores total.
  8 cores are already requested by existing pods.
  Any new pod, even with minimal CPU request, exceeds the quota.

  kubectl describe resourcequota default-quota -n production
  → Shows all 8 CPU cores allocated across 12 pods.
  → Each pod requests 500m-1000m CPU.

Fix:
  # Option A: Increase the quota
  kubectl patch resourcequota default-quota -n production \
    -p '{"spec":{"hard":{"requests.cpu":"12"}}}'

  # Option B: Reduce CPU requests on existing pods (if over-requested)
  kubectl top pods -n production
  # If actual usage is much lower than requests, lower them

  # Option C: Use LimitRange to set MAXIMUM request per pod
  # (prevents any single pod from consuming too much quota)
```

### LimitRange Reference

```yaml
apiVersion: v1
kind: LimitRange
metadata:
  name: pod-limits
spec:
  limits:
  - type: Container
    max:
      cpu: "2"
      memory: "2Gi"
    min:
      cpu: "100m"
      memory: "64Mi"
    default:
      cpu: "500m"
      memory: "256Mi"
    defaultRequest:
      cpu: "200m"
      memory: "128Mi"
    maxLimitRequestRatio:
      cpu: "4"    # limit can be at most 4x request
      memory: "2"
```

---

## References

- [RBAC Authorization](https://kubernetes.io/docs/reference/access-authn-authz/rbac/)
- [Service Accounts](https://kubernetes.io/docs/concepts/security/service-accounts/)
- [Pod Security Standards](https://kubernetes.io/docs/concepts/security/pod-security-standards/)
- [Resource Quotas](https://kubernetes.io/docs/concepts/policy/resource-quotas/)
- [Limit Ranges](https://kubernetes.io/docs/concepts/policy/limit-range/)
