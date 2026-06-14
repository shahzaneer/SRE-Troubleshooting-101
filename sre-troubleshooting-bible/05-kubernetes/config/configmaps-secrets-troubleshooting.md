# ConfigMaps & Secrets Troubleshooting

> **Category:** Kubernetes | ConfigMaps | Secrets
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#kubernetes` `#configmaps` `#secrets` `#configuration`

---

## Table of Contents

1. [ConfigMap Not Updating](#configmap-not-updating)
2. [Secret Mount Failures](#secret-mount-failures)
3. [SubPath Gotchas](#subpath-gotchas)
4. [Environment Variable Issues](#environment-variable-issues)
5. [Secret Encryption & Etcd](#secret-encryption--etcd)

---

## ConfigMap Not Updating

### SubPath Mounts Don't Auto-Update

```text
THE #1 ConfigMap gotcha: When you mount a ConfigMap/Secret using
subPath, the file DOES NOT auto-update when the ConfigMap changes.

# This DOES auto-update (whole volume mount):
volumes:
- name: config
  configMap:
    name: my-config
containers:
- volumeMounts:
  - name: config
    mountPath: /etc/app/config/

# This DOES NOT auto-update (subPath mount):
containers:
- volumeMounts:
  - name: config
    mountPath: /etc/app/config.yaml
    subPath: config.yaml    ← NO auto-update!
```

```bash
# Check if ConfigMap updates are propagating (non-subPath)
kubectl get configmap my-config -n NAMESPACE -o yaml
kubectl exec POD -n NAMESPACE -- cat /etc/app/config/config.yaml

# If they don't match after 60s, check kubelet sync period
# Default kubelet --sync-frequency is 1m
# ConfigMap updates typically reach pods in ~90s (kubelet sync + propagation)

# For subPath mounts: must restart pod
kubectl rollout restart deployment/DEPLOY -n NAMESPACE
```

### Scenario: "ConfigMap updated, app still using old values"

```text
Symptom: Updated ConfigMap 10 min ago. App logs still show old values.

Diagnosis:
  kubectl get configmap my-config -n production -o jsonpath='{.data}'
  → Shows new values ✓

  kubectl exec deploy/myapp -n production -- cat /etc/app/config/database.yaml
  → Shows OLD values ✗

  Check how the config is mounted:
  kubectl get deploy myapp -n production -o yaml | grep -A10 volumeMounts
  → mountPath: /etc/app/config/database.yaml
  → subPath: database.yaml    ← SUBPATH! Won't auto-update.

  Also check: did the pod actually restart?
  kubectl get pods -n production -l app=myapp
  → AGE: 3d    ← pod hasn't restarted. subPath mount still has old values.

  But wait — even with subPath, the ConfigMap should sync when
  the file is symlinked from the kubelet cache. The issue is that
  subPath mounts use bind mounts, which DON'T follow symlink updates.

Fix:
  1. Remove subPath: mount the entire ConfigMap directory
  2. Or use `kubectl rollout restart deployment/myapp` after ConfigMap changes
  3. Or use a tool like Reloader to auto-restart: 
     https://github.com/stakater/Reloader
  4. Or mount as whole directory and have app watch for file changes
```

### Full-Directory Mount Example (Auto-Updates)

```yaml
volumes:
- name: config
  configMap:
    name: my-config
containers:
- volumeMounts:
  - name: config
    mountPath: /etc/app/config/    # directory, not file
```

---

## Secret Mount Failures

### Common Issues

```bash
# Check if secret exists
kubectl get secret my-secret -n NAMESPACE

# Check pod events for mount failures
kubectl describe pod POD -n NAMESPACE | grep -A5 Events
# "MountVolume.SetUp failed for volume "secret" : secret "my-secret" not found"

# Check if secret is in same namespace
kubectl get secret my-secret -n NAMESPACE
# Error from server (NotFound): secrets "my-secret" not found
```

| Issue | Symptom | Fix |
|-------|---------|-----|
| **Secret doesn't exist** | Pod stuck in ContainerCreating | Create the secret or fix the reference name |
| **Secret in wrong namespace** | Pod can't find secret | Secrets must be in same namespace as pod |
| **Secret key doesn't exist** | Empty env var or missing file | Verify key names: `kubectl get secret -o json | jq '.data \| keys'` |
| **Permission denied reading file** | App can't read mounted secret | Set defaultMode in volume spec: `defaultMode: 0400` (owner read only) |
| **Secret too large** | Pod creation fails | Max secret size: 1MB. Use external secret store (Vault, AWS Secrets Manager) for large secrets |

### Scenario: "Secret mounted but env var empty"

```text
Symptom: Pod running but DATABASE_URL env var is empty.
         Secret exists and has the key.

Pod spec:
  env:
  - name: DATABASE_URL
    valueFrom:
      secretKeyRef:
        name: db-secret
        key: DATABASE_URL    ← CASE SENSITIVE

Diagnosis:
  kubectl get secret db-secret -n production -o json | jq '.data | keys'
  → ["database_url"]    ← lowercase! Doesn't match "DATABASE_URL"

  Secret keys ARE case-sensitive. "DATABASE_URL" != "database_url".

Fix: Align key casing — change pod spec to use "database_url"
     or update the secret key to "DATABASE_URL".
```

### Base64 Encoding Traps

```bash
# View decoded secret values
kubectl get secret my-secret -n NAMESPACE -o jsonpath='{.data.password}' | base64 -d

# Creating secrets: values must be base64 encoded
# kubectl create secret generic does this automatically
kubectl create secret generic my-secret --from-literal=password=s3cret

# But if using YAML manifest, you MUST base64 encode:
echo -n "s3cret" | base64    # c2VjcmV0
# Then in YAML:
# data:
#   password: c2VjcmV0

# Or use stringData (Kubernetes encodes for you):
# stringData:
#   password: s3cret
```

---

## SubPath Gotchas

### SubPath + ConfigMap/Secret Doesn't Update

```text
Mounted with subPath → file is a bind mount of a symlink target.
When ConfigMap updates, kubelet updates the symlink, but the bind
mount still points to the OLD target.

Fix: Never use subPath if you need live updates.
```

### SubPath + Missing Optional ConfigMap

```text
If a ConfigMap is marked optional but a subPath references a
non-existent key, the pod gets stuck in ContainerCreating.

spec:
  containers:
  - volumeMounts:
    - name: config
      mountPath: /etc/app/missing.yaml
      subPath: missing.yaml     ← key doesn't exist

volumes:
- name: config
  configMap:
    name: my-config
    optional: true    ← configmap itself exists, but key doesn't

Error: "MountVolume.SetUp failed for volume "config": key "missing.yaml"
       not found in configmap "my-config"

Fix: Either ensure the key exists or don't mount it directly.
     Use an initContainer to check if key exists before main container starts.
```

### subPathExpr for Dynamic Paths (K8s 1.17+)

```yaml
# Mount config per pod ordinal (StatefulSet)
volumeMounts:
- name: config
  mountPath: /etc/app/pod-config.yaml
  subPathExpr: $(POD_NAME).yaml    # expands to myapp-0.yaml, myapp-1.yaml, etc.

env:
- name: POD_NAME
  valueFrom:
    fieldRef:
      apiVersion: v1
      fieldPath: metadata.name
```

---

## Environment Variable Issues

### EnvFrom: Not All Keys

```bash
# envFrom imports ALL keys from a ConfigMap/Secret as env vars
# If the ConfigMap has 100 keys, all become env vars

spec:
  containers:
  - envFrom:
    - configMapRef:
        name: my-config
    - secretRef:
        name: my-secret

# PROBLEM: Key names must be valid env var names (alphanumeric, _)
# Invalid keys are SKIPPED silently!

# ConfigMap with key "my-key": not a valid env var name → SKIPPED
# Secret with key "db.password": not valid → SKIPPED
```

### Precedence: env > envFrom

```text
Environment variable precedence:
  1. env (highest)
  2. envFrom (lower, and order within envFrom matters: last wins)

spec:
  containers:
  - envFrom:
    - configMapRef:
        name: config-a      # sets DATABASE_URL=postgres://a
    - configMapRef:
        name: config-b      # sets DATABASE_URL=postgres://b  ← WINS
  - env:
    - name: DATABASE_URL
      value: "postgres://override"    ← WINS over all envFrom
```

---

## Secret Encryption & Etcd

```bash
# Check if encryption at rest is enabled
kubectl get --raw /api/v1/namespaces/kube-system/secrets | head

# Check encryption config (if using static encryption)
ps aux | grep kube-apiserver | grep encryption-provider-config

# If no encryption-config flag, secrets are stored as PLAIN base64 in etcd
# Base64 is NOT encryption — anyone with etcd access can decode secrets.
# Enable encryption at rest:
# https://kubernetes.io/docs/tasks/administer-cluster/encrypt-data/
```

### Scenario: "Secrets vanished after cluster upgrade"

```text
Symptom: After upgrading from K8s 1.27 to 1.29, some secrets appear
         corrupted or empty when read by pods.

Cause: Encryption configuration migration wasn't completed.
       If you changed encryption providers, old secrets were encrypted
       with the old provider and new ones with the new provider.
       During the migration, the old provider was removed before all
       secrets were re-encrypted.

Fix:
  # Before removing old encryption provider:
  kubectl get secrets -A -o json | kubectl replace -f -
  # This forces all secrets to be re-encrypted with the current provider.

  # Verify:
  kubectl get secrets -A --no-headers | wc -l
  # All secrets should be readable after encryption migration.
```

---

## References

- [ConfigMaps](https://kubernetes.io/docs/concepts/configuration/configmap/)
- [Secrets](https://kubernetes.io/docs/concepts/configuration/secret/)
- [Encrypting Secret Data at Rest](https://kubernetes.io/docs/tasks/administer-cluster/encrypt-data/)
- [Stakater Reloader](https://github.com/stakater/Reloader)
