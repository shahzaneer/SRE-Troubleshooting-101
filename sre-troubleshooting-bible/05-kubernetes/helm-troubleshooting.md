# Helm Troubleshooting

> **Category:** Kubernetes | Helm
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#helm` `#kubernetes` `#deployment` `#oncall`

---

## Table of Contents

1. [Release Stuck States](#release-stuck-states)
2. [Helm Diff and Dry-Run](#helm-diff-and-dry-run)
3. [Helm Rollback](#helm-rollback)
4. [Chart Rendering Errors](#chart-rendering-errors)
5. [Values Override Precedence](#values-override-precedence)
6. [Helm Hooks and Lifecycle](#helm-hooks-and-lifecycle)
7. [Diagnostic Commands](#diagnostic-commands)

---

## Release Stuck States

### pending-install / pending-upgrade / pending-rollback

```text
These states occur when a Helm operation was interrupted before completion
(OOM kill, network timeout, CI job canceled, user Ctrl+C).

The release is locked. Helm maintains a lock in the release secret:
  kubectl get secret -n kube-system -l owner=helm,name=RELEASE

To diagnose:
```

```bash
# Check release status
helm status RELEASE -n NAMESPACE
helm history RELEASE -n NAMESPACE --max 10

# Look at the latest release secret
helm get manifest RELEASE -n NAMESPACE    # what was deployed?
helm get values RELEASE -n NAMESPACE      # what values were used?
helm get notes RELEASE -n NAMESPACE       # install notes

# Check for Helm lock
kubectl get secrets -n NAMESPACE -l name=RELEASE,owner=helm
```

### Fixing Stuck Releases

```bash
# Option 1: Rollback to last known-good revision
helm rollback RELEASE -n NAMESPACE
# If that fails:
helm rollback RELEASE 3 -n NAMESPACE --wait --timeout 5m

# Option 2: Force upgrade (ignores current state)
helm upgrade RELEASE CHART -n NAMESPACE --force --wait --timeout 10m

# Option 3: Uninstall and reinstall (nuclear option)
helm uninstall RELEASE -n NAMESPACE --wait
kubectl delete secret -n NAMESPACE -l owner=helm,name=RELEASE  # cleanup secrets
helm install RELEASE CHART -n NAMESPACE --wait

# Option 4: Delete pending state directly (Helm 3 only)
kubectl delete secret -n NAMESPACE sh.helm.release.v1.RELEASE.v5  # remove broken revision
helm install RELEASE CHART -n NAMESPACE --wait
```

### Scenario: "Helm Release Stuck in pending-install for 2 Hours"

```text
Symptom: `helm install` was running in a CI pipeline. The CI job ran
         out of memory (OOM) and was killed by the orchestrator.
         The release shows `pending-install` and all subsequent
         `helm install` or `helm upgrade` commands fail with:
         "another operation (install/upgrade/rollback) is in progress"

Debugging:
  kubectl get secrets -n production -l owner=helm,name=myapp
  → Shows secrets for revisions 1, 2 (both with status: pending-install)

  helm history myapp -n production
  → 1  Mon Jun  9 14:22:30 2026    pending-install   myapp-1.0.0
  → 2  Mon Jun  9 14:30:15 2026    pending-install   myapp-1.0.0

  Both revisions are stuck. Revision 1 got interrupted, revision 2
  tried to install again and also got stuck.

Fix:
  # Helm 3 stores releases as secrets with status labels
  kubectl get secrets -n production -l owner=helm,name=myapp \
    -o custom-columns=NAME:.metadata.name,STATUS:.metadata.labels.status,REV:.metadata.labels.version

  # Rollback to reset the pending state
  helm rollback myapp -n production
  # If rollback also fails (because ALL revisions are stuck):
  # Delete ALL release secrets and start fresh:
  kubectl delete secret -n production -l owner=helm,name=myapp
  helm install myapp ./chart -n production --wait --timeout 10m

  Prevention:
  - Set generous --timeout (10m+) on CI installs
  - Set Helm --atomic flag (auto-rollback on failure)
  - Add --wait flag to let Helm verify resources become ready
```

---

## Helm Diff and Dry-Run

```bash
# Install helm-diff plugin (once per machine)
helm plugin install https://github.com/databus23/helm-diff

# Show what will change before applying
helm diff upgrade RELEASE CHART -n NAMESPACE --values prod-values.yaml
helm diff upgrade RELEASE CHART -n NAMESPACE --values prod.yaml --detailed-exitcode
# Exit codes: 0=no changes, 1=error, 2=changes detected

# Dry-run: render templates without deploying
helm install --dry-run --debug RELEASE CHART
helm upgrade --dry-run --debug RELEASE CHART -n NAMESPACE
helm template RELEASE CHART --values prod.yaml --debug  # local-only rendering

# See exactly what Kubernetes resources will be created
helm install --dry-run RELEASE CHART | kubectl apply --dry-run=client -f -
```

---

## Helm Rollback

```bash
# Show revision history
helm history RELEASE -n NAMESPACE --max 10
# Output:
#   REVISION  UPDATED                  STATUS           CHART           DESCRIPTION
#   1         Wed Jun  9 14:00:00 2026 superseded       myapp-1.0.0    Install complete
#   2         Wed Jun  9 14:15:00 2026 superseded       myapp-1.1.0    Upgrade complete
#   3         Wed Jun  9 14:30:00 2026 deployed         myapp-1.2.0    Upgrade complete

# Rollback to previous revision
helm rollback RELEASE -n NAMESPACE

# Rollback to specific revision
helm rollback RELEASE 2 -n NAMESPACE --wait --timeout 5m

# Rollback with different values (override values from target revision)
helm rollback RELEASE 2 -n NAMESPACE --reuse-values  # keep current values

# Rollback and see what happened
helm rollback RELEASE -n NAMESPACE --wait --debug

# Verify after rollback
helm history RELEASE -n NAMESPACE --max 5
helm status RELEASE -n NAMESPACE
```

---

## Chart Rendering Errors

```bash
# Render templates without deploying (local testing)
helm template RELEASE ./chart --values dev-values.yaml --debug

# Common error patterns:
# "nil pointer evaluating interface {}.field"
#   → Missing required value. Template expects .Values.database.host but it's not set.
#   Fix: check values.yaml for defaults OR supply the value via --set or --values.

# "wrong type for value; expected string; got int"
#   → Template expects string but got number. Use | quote in template or set as string.
#   Fix: values: dbPort: "5432" (quoted) or template: {{ .Values.dbPort | quote }}

# "template: myapp/templates/deployment.yaml:NN: function 'XXX' not defined"
#   → Using a function from an unloaded template. Check _helpers.tpl is loaded.

# "Error: YAML parse error on myapp/templates/deployment.yaml"
#   → YAML indentation error or unclosed string. helm template shows the broken output.
```

### Debugging Template Rendering

```bash
# Render a specific template
helm template RELEASE ./chart -x templates/deployment.yaml -f values.yaml > rendered.yaml
# Inspect the rendered YAML for syntax errors

# Use --debug to see values being used
helm template RELEASE ./chart --values prod.yaml --debug 2>&1 | head -50

# Check all available values (including defaults from values.yaml)
helm show values CHART                    # chart's default values.yaml
helm get values RELEASE -n NAMESPACE      # values currently deployed
```

### Scenario: "Upgrade Fails With Nil Pointer Error"

```text
Error: "Error: template: myapp/templates/deployment.yaml:42:7: executing
       "myapp/templates/deployment.yaml" at <.Values.redis.host>: nil
       pointer evaluating interface {}.host"

Cause: The chart template references `{{ .Values.redis.host }}` but
       the `redis` key is not present in the values file being used.

Debugging:
  # 1. Check what values the chart expects (defaults in chart's values.yaml)
  helm show values ./chart | grep -A5 "redis"

  # 2. Check what values are being passed
  helm template RELEASE ./chart -f custom-values.yaml --debug 2>&1 | grep "redis"

  # 3. If the redis section exists in chart values.yaml but is overridden
  #    by a values file that doesn't have it → nil pointer.
  #    Values merge is DEEP MERGE, but if an upstream file sets redis: null,
  #    it overrides the chart defaults.

Fix:
  # Option A: Supply the missing value
  --set redis.host=redis.production.svc.cluster.local

  # Option B: Add to your custom values.yaml
  redis:
    host: "redis.production.svc.cluster.local"
    port: 6379

  # Option C: Template fix with default
  # In the chart template: {{ .Values.redis.host | default "redis.default.svc" }}
  # Better: mark as required in Notes.txt and document in README
```

---

## Values Override Precedence

Values are merged in this order (last wins for conflicting keys):

```text
1. Chart's values.yaml (lowest priority — defaults)
2. Parent chart's values.yaml (if subchart — parent overrides)
3. User-supplied --values / -f files (multiple files, last wins)
4. User-supplied --set flags (highest priority)

Example:
  Chart defaults:          replicaCount: 2
  values-prod.yaml:        replicaCount: 5
  --set replicaCount=10    replicaCount: 10  ← wins

  # Check what the final merged values are:
  helm template RELEASE ./chart -f values-prod.yaml --set replicaCount=10 \
    --debug 2>&1 | grep -A20 "^USER-SUPPLIED VALUES"
```

### Scenario: "Override Not Working — Value Comes from Wrong Place"

```text
Symptom: I set replicaCount=10 in my custom values file, but after
         upgrade the deployment still shows 5 replicas.

Debugging:
  helm get values RELEASE -n production --revision=5
  → Shows replicaCount: 5 (NOT 10!)

  Check values file being applied:
  helm upgrade RELEASE ./chart -n production -f custom-values.yaml --dry-run

  But wait — there's ALSO a `--values staging-values.yaml` in the CI
  pipeline script. staging-values.yaml has replicaCount: 5 and is
  applied AFTER custom-values.yaml.

  In the CI pipeline:
    helm upgrade RELEASE ./chart -f custom-values.yaml -f staging-values.yaml
    # custom-values.yaml sets replicaCount: 10
    # staging-values.yaml overrides it with replicaCount: 5
    # staging-values.yaml WINS (last file specified)

Fix: Remove the override from staging-values.yaml, or specify the correct
     value last, or use --set replicaCount=10 which overrides all files.
```

---

## Helm Hooks and Lifecycle

```text
Helm hooks run at specific points in the release lifecycle:
  pre-install   → before any resources are created
  post-install  → after all resources are created
  pre-delete    → before deletion
  post-delete   → after deletion
  pre-upgrade   → before upgrade
  post-upgrade  → after upgrade
  pre-rollback  → before rollback
  post-rollback → after rollback
  test          → when `helm test` is run

Hooks are Kubernetes resources with annotations:
  "helm.sh/hook": post-install,post-upgrade
  "helm.sh/hook-weight": "5"          (execution order, lowest first)
  "helm.sh/hook-delete-policy": before-hook-creation,hook-succeeded

Common hook issues:
  - Hooks don't auto-delete → stale Jobs/Secrets lingering
  - Hook failure blocks release → set hook-delete-policy: hook-failed
  - Hook timeouts → "timed out waiting for the condition"
```

### Diagnosing Hook Failures

```bash
# Check hook status
helm status RELEASE -n NAMESPACE
# Look for: "LAST DEPLOYED: ..." and "STATUS: pending-upgrade" (hook may be running)

# Find hook resources
kubectl get jobs -n NAMESPACE -l "helm.sh/hook"
kubectl get pods -n NAMESPACE -l "helm.sh/hook"

# Check hook logs
kubectl logs -n NAMESPACE job/helm-hook-post-install

# Delete a failed hook and retry
kubectl delete job -n NAMESPACE -l "helm.sh/hook"
helm upgrade RELEASE CHART -n NAMESPACE --wait --timeout 10m
```

---

## Diagnostic Commands

```bash
# List all releases across all namespaces
helm list -A
helm list -A --all                   # including failed/pending
helm list -A --date                  # sorted by date
helm list -A --uninstalled           # releases that were deleted
helm list -A --failed                # only failed releases
helm list -A -o yaml                 # full output

# Inspect a release
helm status RELEASE -n NAMESPACE
helm history RELEASE -n NAMESPACE
helm get manifest RELEASE -n NAMESPACE         # deployed YAML
helm get manifest RELEASE -n NAMESPACE --revision=3
helm get values RELEASE -n NAMESPACE            # deployed values
helm get values RELEASE -n NAMESPACE --all      # all values (computed + user)
helm get hooks RELEASE -n NAMESPACE             # hooks used
helm get notes RELEASE -n NAMESPACE             # release notes

# Chart inspection
helm show chart CHART
helm show values CHART
helm show readme CHART
helm show all CHART                              # everything

# Repository management
helm repo list
helm repo update
helm search repo nginx                            # search across all repos
helm search hub nginx                             # search Artifact Hub

# Plugin management
helm plugin list
helm plugin install https://github.com/databus23/helm-diff
helm plugin install https://github.com/aslafy-z/helm-git
helm plugin install https://github.com/hypnoglow/helm-s3

# Testing
helm test RELEASE -n NAMESPACE                    # run test hooks
helm test RELEASE -n NAMESPACE --logs             # with logs

# Environment config
helm env
helm version
```

---

## References

- [Helm Documentation](https://helm.sh/docs/)
- [Helm Chart Template Guide](https://helm.sh/docs/chart_template_guide/)
- [Helm diff Plugin](https://github.com/databus23/helm-diff)
- [Helm Hooks](https://helm.sh/docs/topics/charts_hooks/)
- [Helm Values Precedence](https://helm.sh/docs/chart_template_guide/values_files/)
