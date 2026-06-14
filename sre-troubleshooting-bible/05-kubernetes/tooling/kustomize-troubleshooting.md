# Kustomize Troubleshooting

> **Category:** Kubernetes | Kustomize | Configuration
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#kubernetes` `#kustomize` `#configuration` `#gitops`

---

## Table of Contents

1. [Kustomize vs Helm](#kustomize-vs-helm)
2. [Overlay Merging Issues](#overlay-merging-issues)
3. [Patch Troubleshooting](#patch-troubleshooting)
4. [ConfigMap Generator Issues](#configmap-generator-issues)
5. [Common Kustomize Errors](#common-kustomize-errors)

---

## Kustomize vs Helm

```text
Helm:     Template engine. Text substitution with {{ .Values.foo }}.
Kustomize: Overlay engine. Patches base YAML without templates.

Kustomize is built into kubectl (kubectl apply -k ./overlays/prod/).

When to use which:
  Helm:     Complex apps with many configurable parameters
  Kustomize: Environment variants of the same app (dev/staging/prod)
```

### Quick Diagnosis

```bash
# Build and show final YAML without applying
kubectl kustomize ./overlays/production/
# or (older kubectl):
kustomize build ./overlays/production/

# See what would change compared to cluster
kubectl diff -k ./overlays/production/

# Apply
kubectl apply -k ./overlays/production/
# Or with pruning (delete resources removed from kustomization):
kubectl apply -k ./overlays/production/ --prune -l app=myapp
```

---

## Overlay Merging Issues

### Kustomize Directory Structure

```text
base/
├── kustomization.yaml     ← lists resources and common config
├── deployment.yaml
├── service.yaml
└── configmap.yaml

overlays/
├── staging/
│   ├── kustomization.yaml ← patches + namespace + namePrefix
│   ├── replica-count.yaml  ← strategic merge patch
│   └── env-config.properties
└── production/
    ├── kustomization.yaml
    ├── replicas-patch.yaml
    └── env-config.properties
```

### Common Merging Issues

| Issue | Symptom | Fix |
|-------|---------|-----|
| **Patch doesn't apply** | Expected change not in final YAML | Check patch target: name, kind, namespace must match exactly |
| **Patch targets wrong resource** | Change applied to wrong deployment | Add `name:` and `kind:` to patch metadata |
| **namespace not set** | Resources created in default namespace | Set `namespace:` in kustomization.yaml overlay |
| **namePrefix/Suffix not applied** | Resource names don't get prefix | Check kustomization.yaml has `namePrefix:` or `nameSuffix:` |
| **Duplicate resources** | Resources from multiple bases have same name | Use `namePrefix:` per base to disambiguate |

### Scenario: "Patch not applying — target selector mismatch"

```text
Base kustomization.yaml:
  resources:
  - deployment.yaml    # metadata.name: myapp, kind: Deployment

Overlay kustomization.yaml:
  patchesStrategicMerge:
  - patch.yaml

patch.yaml:
  apiVersion: apps/v1
  kind: Deployment
  metadata:
    name: myapp-prod    ← THIS DOESN'T MATCH base "myapp"
  spec:
    replicas: 5

The patch targets "myapp-prod" but the base deployment is named "myapp".
Patch doesn't match → silently ignored → replicas stay at base value.

Fix: Change patch metadata.name to "myapp" (match the base resource name)
     OR use namePrefix: prod- in overlay which renames the base resource.

Better approach: Use a targeted patch instead:
  patches:
  - target:
      kind: Deployment
      name: myapp
    patch: |-
      - op: replace
        path: /spec/replicas
        value: 5
```

---

## Patch Troubleshooting

### Three Patch Types

```yaml
# 1. Strategic Merge Patch (default for patchesStrategicMerge)
#    Merges lists by "name" key, replaces scalars
#    Example patch file:
apiVersion: apps/v1
kind: Deployment
metadata:
  name: myapp
spec:
  replicas: 5

# 2. JSON 6902 Patch (patchesJson6902)
#    Explicit operations: add, remove, replace, copy, move, test
#    Example:
patches:
- target:
    group: apps
    version: v1
    kind: Deployment
    name: myapp
  patch: |-
    - op: replace
      path: /spec/replicas
      value: 5
    - op: add
      path: /spec/template/spec/containers/0/env/-
      value:
        name: ENV
        value: production

# 3. Patch from file (patches)
#    Can use either strategic merge or JSON patch
patches:
- path: patch-file.yaml
- target:
    kind: Deployment
    name: myapp
  path: json-patch.yaml
```

### Troubleshooting Patches

```bash
# See what the patch resolves to
kubectl kustomize ./overlays/production/ | grep -A20 "kind: Deployment"

# Test: copy-paste the patch into a strategically merged file and kubectl diff
kubectl kustomize ./overlays/production/ > /tmp/rendered.yaml
kubectl diff -f /tmp/rendered.yaml
```

### Common Patch Errors

```text
1. "error: trouble configuring builtin PatchTransformer: 
    missing field 'patch'"
   → Using patchesJson6902 format without a 'patch' field
   → Fix: Add patch field with JSON patch operations

2. "no matches for /spec/template/spec/containers/0/resources"
   → JSON patch path doesn't exist in the base resource
   → Use "add" instead of "replace" if the field doesn't exist
   → Check path carefully (0-indexed containers array)

3. Strategic merge replaces entire list instead of merging
   → Lists of objects without a merge key replace instead of merging
   → For env vars, the merge key is "name" (each env must have unique name)
   → For volumes, the merge key is "name"
   → For container ports, there is NO merge key (ports are replaced entirely!)

4. "Error: accumulating resources: ..."
   → Path to resource file is wrong or file doesn't exist
   → Check kustomization.yaml paths are relative to kustomization file
```

---

## ConfigMap Generator Issues

### ConfigMap Generator

```yaml
# kustomization.yaml
configMapGenerator:
- name: app-config
  files:
  - app.properties
  - config.json
  literals:
  - ENV=production
  - DEBUG=false
  envs:
  - env-file.properties

# Output ConfigMap gets a CONTENT HASH SUFFIX:
# configmap/app-config-7895fgh6k7
# Hash changes when content changes → triggers rolling restart
```

### Common Issues

```text
1. ConfigMap name changed unexpectedly
   → The content hash suffix changes when ANY key changes
   → This triggers a Deployment rolling update (good for immutable config)
   → Disable hash: generatorOptions: { disableNameSuffixHash: true }
   → But this means pods won't auto-restart on config changes

2. Missing behavior: merge/replace/create
   → Kustomize default: CREATE only (fails if ConfigMap exists)
   → Add annotation for merge behavior:
     kubectl apply -k ./overlays/ --force-conflicts

3. ConfigMap too large
   → Cannot exceed 1MB (etcd limit)
   → Split into multiple ConfigMaps

4. Binary files in ConfigMap
   → Kustomize can't handle binary files in literals/files
   → Use kubectl create configmap --from-file=binary.bin separately
```

### Generator Options

```yaml
# kustomization.yaml
generatorOptions:
  disableNameSuffixHash: false     # keep hash suffix (recommended)
  labels:
    generated-by: kustomize
    environment: production
  annotations:
    config.kubernetes.io/local-config: "true"

configMapGenerator:
- name: app-config
  namespace: production
  behavior: create    # create, replace, or merge
  files:
  - app.properties
  literals:
  - KEY=VALUE
```

---

## Common Kustomize Errors

### Error Reference

| Error | Cause | Fix |
|-------|-------|-----|
| **"accumulating resources: ... is not a directory"** | kustomization.yaml references a directory that doesn't exist | Check path (relative to kustomization.yaml) |
| **"may not add resource with an already registered id"** | Two resources have same name/namespace/GVK | Add namePrefix to one base or rename resources |
| **"no matches for Id"** | Patch or variable reference targets non-existent resource | Check resource names and kinds |
| **"raw Resources failed to read Resources"** | YAML syntax error in a resource file | Run `yamllint` or `kubectl --dry-run=client -f FILE` |
| **"field is immutable"** | Trying to patch an immutable field (e.g., spec.selector in Deployment) | Remove patch for that field, or delete and recreate |
| **"cannot marshal"** | Invalid YAML in kustomization.yaml | Check indentation and YAML syntax |
| **"plugin: ... not found"** | Using a generator/transformer plugin not installed | Install plugin via krew or check ~/.config/kustomize/plugin/ |

### Scenario: "kubectl apply -k fails but kubectl kustomize works"

```text
Symptom: kubectl kustomize ./overlays/prod/ → valid YAML output.
         kubectl apply -k ./overlays/prod/ → fails with error.

Diagnosis:
  kubectl apply -k ./overlays/prod/ --dry-run=client
  → "Error from server (BadRequest): the namespace of the provided object 
     does not match the namespace in the request"

  The kustomization.yaml sets namespace: production, but some resources
  in the base have an explicit namespace set (e.g., namespace: default).
  
  kubectl kustomize ./overlays/prod/ | grep -A1 namespace
  → Shows some resources with namespace: production (from overlay)
  → Shows others with namespace: default (hardcoded in base resource)

  kubectl apply REQUIRES that the resource's namespace matches the
  kubectl context's namespace... wait, no. The issue is:
  
  Some resources have namespace explicitly set in their metadata.
  kubectl apply sends the request to /api/v1/namespaces/NAMESPACE/...
  If NAMESPACE in the URL doesn't match metadata.namespace, it fails.

Fix:
  # Remove hardcoded namespace from base resources (let overlay set it)
  # Or set namespace in overlay kustomization.yaml AND remove from base:
  kubectl kustomize ./overlays/prod/ | sed 's/namespace: default//' | kubectl apply -f -
```

### Debugging Kustomize

```bash
# Build with debug output
kubectl kustomize ./overlays/prod/ --stack-trace

# See the full resolved kustomization
kubectl kustomize ./overlays/prod/ > /tmp/full.yaml

# Diff against what's in the cluster already
kubectl diff -k ./overlays/prod/

# Validate without applying
kubectl apply -k ./overlays/prod/ --dry-run=client

# Check individual resource validity
kubectl kustomize ./overlays/prod/ | kubectl apply --dry-run=client -f -
```

### Var Reference Issues (Deprecated)

```text
Kustomize "vars" are deprecated in favor of replacements.

OLD (deprecated):
  vars:
  - name: SERVICE_NAME
    objref:
      kind: Service
      name: myapp-svc
      apiVersion: v1
    fieldref:
      fieldpath: metadata.name

NEW (replacements):
  replacements:
  - source:
      kind: Service
      name: myapp-svc
      fieldPath: metadata.name
    targets:
    - select:
        kind: Deployment
        name: myapp
      fieldPaths:
      - spec.template.spec.containers.[name=app].env.[name=SERVICE_NAME].value
```

---

## References

- [Kustomize Documentation](https://kubectl.docs.kubernetes.io/)
- [Kustomize GitHub](https://github.com/kubernetes-sigs/kustomize)
- [Kustomize Examples](https://github.com/kubernetes-sigs/kustomize/tree/master/examples)
