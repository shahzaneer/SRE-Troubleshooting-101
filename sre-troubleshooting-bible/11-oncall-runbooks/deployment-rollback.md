# Deployment Rollback Runbook

> **Category:** On-Call | CI/CD | Deployment
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#runbook` `#deployment` `#rollback` `#oncall`

---

## 1. DECISION — Rollback or Fix-Forward?

Before rolling back, ask:

| Question | Rollback | Fix-Forward |
|----------|----------|-------------|
| Error rate spike >5% post-deploy? | **Yes** | No |
| Latency degraded >2x baseline? | **Yes** | No |
| Data corruption detected? | **Yes (see warning)** | No (dangerous) |
| Security vulnerability introduced? | **Yes** | No |
| Trivial config change (wrong log level, etc.)? | No | **Yes** (fast fix) |
| Hotfix already ready and tested? | No | **Yes** |
| DB migration was part of deploy? | **Maybe** (see section 4) | **Maybe** (see section 4) |

**Decision flow:**

```
Error/Latency spike?
├── YES → Did we deploy in last 30 min?
│   ├── YES → ROLLBACK (this runbook)
│   └── NO  → Go to [High Error Rate](high-error-rate.md) / [High Latency](high-latency.md)
└── NO  → Not a deployment issue. Check other runbooks.
```

---

## 2. PRE-ROLLBACK CHECKS

```bash
# Confirm which deployment just happened:
# Kubernetes:
kubectl rollout history deployment/api-server -n prod | tail -3
# Shows revision numbers. The top one is current.

# Helm:
helm history api-server -n prod --max 5
# Shows revision, status, chart, app version.

# ECS:
aws ecs describe-services \
  --cluster prod \
  --services api-server \
  --region us-east-1 \
  --query "services[0].[deployments,taskDefinition]"

# Check who triggered the deploy and what changed:
git log --oneline --since="1 hour ago" -10
kubectl get events -n prod --sort-by='.lastTimestamp' | grep -i deploy | tail -10
```

---

## 3. ROLLBACK PROCEDURES

### 3a. Kubernetes — kubectl rollback

```bash
# Step 1: List revision history:
kubectl rollout history deployment/api-server -n prod

# Output:
# REVISION  CHANGE-CAUSE
# 1         <none>
# 2         <none>
# 3         <none>       ← latest (bad)
#
# Step 2: Check what was in the previous (good) revision:
kubectl rollout history deployment/api-server -n prod --revision=2

# Step 3: Rollback to previous revision:
kubectl rollout undo deployment/api-server -n prod

# Or to a specific revision:
kubectl rollout undo deployment/api-server -n prod --to-revision=2

# Step 4: Watch the rollback progress:
kubectl rollout status deployment/api-server -n prod --timeout=5m

# Step 5: Verify pods are running with the rolled-back image:
kubectl get pods -l app=api-server -n prod -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.containers[0].image}{"\n"}{end}'
```

### 3b. Helm Rollback

```bash
# Step 1: List Helm release history:
helm history api-server -n prod --max 5

# Output:
# REVISION  UPDATED                   STATUS     CHART           APP VERSION
# 1         Mon Jun 8 12:00:00 2026   superseded  api-server-1.2  1.2.0
# 2         Mon Jun 8 15:30:00 2026   superseded  api-server-1.3  1.3.0
# 3         Tue Jun 9 09:00:00 2026   deployed    api-server-1.4  1.4.0   ← bad

# Step 2: Rollback to previous revision:
helm rollback api-server -n prod

# Or to a specific revision:
helm rollback api-server 2 -n prod

# Step 3: Verify rollback succeeded:
helm history api-server -n prod --max 1
kubectl get pods -l app=api-server -n prod
```

### 3c. ECS Rollback

```bash
# Step 1: Find the previous task definition ARN:
aws ecs describe-services \
  --cluster prod \
  --services api-server \
  --region us-east-1 \
  --query "services[0].deployments[*].[status,taskDefinition,desiredCount]" \
  --output table

# Step 2: Find the previous (stable) task definition ARN:
# Option A: Look at the deployment history in ECS console
# Option B: List task definition revisions:
aws ecs list-task-definitions \
  --family-prefix api-server \
  --sort DESC \
  --max-items 10 \
  --region us-east-1

# Step 3: Update service to use the previous task definition:
aws ecs update-service \
  --cluster prod \
  --service api-server \
  --task-definition api-server:42 \
  --force-new-deployment \
  --region us-east-1

# Step 4: Watch deployment:
aws ecs describe-services \
  --cluster prod \
  --services api-server \
  --region us-east-1 \
  --query "services[0].deployments[*].[status,desiredCount,runningCount]"
```

### 3d. Feature Flag Rollback (Fastest — Zero Deployment)

If the bad code path is controlled by a feature flag:

```bash
# Toggle the feature flag OFF in configuration system:
# LaunchDarkly:
curl -X PATCH "https://app.launchdarkly.com/api/v2/flags/production/bad-feature" \
  -H "Authorization: $LD_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"environments":{"production":{"on":false}}}'

# Consul KV:
consul kv put service/api-server/feature-bad-code false

# Custom config service:
curl -X PUT "https://config.internal.example.com/api/v1/config/api-server" \
  -H "Authorization: Bearer $CONFIG_TOKEN" \
  -d '{"features": {"bad_feature": false}}'

# Verify traffic goes back to old code path:
# Check metrics — error rate should drop immediately.
# This takes SECONDS vs minutes for a deployment rollback.
```

---

## 4. DATABASE MIGRATION ROLLBACK (⚠️ DATA LOSS RISK)

> **WARNING:** Rolling back a database migration can cause **permanent data loss**. Be certain before proceeding.

### 4a. Flyway Rollback

```bash
# Check current migration version:
flyway -url="$DB_URL" -user="$DB_USER" -password="$DB_PASS" info

# Rollback the last migration:
flyway -url="$DB_URL" -user="$DB_USER" -password="$DB_PASS" undo

# WARNING: This runs the U (undo) migration. If your undo migration
# drops a table/column that was just created, data in that table is LOST.
```

### 4b. Liquibase Rollback

```bash
# Rollback last 1 change set:
liquibase --url="$DB_URL" --username="$DB_USER" --password="$DB_PASS" \
  rollbackCount 1

# Or rollback to specific tag:
liquibase --url="$DB_URL" --username="$DB_USER" --password="$DB_PASS" \
  rollback v1.2.0

# Or rollback to specific date:
liquibase --url="$DB_URL" --username="$DB_USER" --password="$DB_PASS" \
  rollbackToDate 2026-06-10T12:00:00
```

### 4c. Safe Migration Patterns

| Migration Type | Safe to Rollback? | How to Rollback |
|---------------|-------------------|----------------|
| **Add column** (nullable) | Yes | Drop the column |
| **Add table** | Yes | Drop the table |
| **Add index** | Yes | Drop the index |
| **Rename column** | Yes (if old code doesn't use old name) | Rename back |
| **Change column type** | **Risky** — data may be lost | Restore from backup |
| **Drop column** | **NO** — data is gone | Restore from backup |
| **Drop table** | **NO** — data is gone | Restore from backup |
| **Data migration (UPDATE)** | **Very risky** | Restore from backup or run reverse UPDATE |

### 4d. Golden Rule for Safe Migrations

> **Never ship destructive migrations (DROP, DELETE, data-altering UPDATE) in the same deployment as application code changes.**

1. Deploy code that works with **both** old and new schema (additive changes only)
2. Wait for that deploy to stabilize (1-3 days in production)
3. Then deploy the destructive migration separately
4. If the code deploy needs rollback, the migration hasn't happened yet = safe

---

## 5. VERIFY ROLLBACK

```bash
# 1. Health check:
curl -s -o /dev/null -w "%{http_code}" https://api.example.com/health
# Expected: 200

# 2. Check metrics (Grafana / Datadog):
# - Error rate returned to baseline (<1%)?
# - p95 latency returned to baseline?
# - Availability back to 100%?

# 3. Run smoke tests:
./smoke-tests.sh prod

# 4. All regions?
for region in us-east-1 us-west-2 eu-west-1; do
  echo -n "$region: "
  curl -s -o /dev/null -w "%{http_code}" "https://${region}.api.example.com/health"
  echo
done

# 5. Verify app version (which image/version is running):
kubectl get pods -l app=api-server -n prod -o jsonpath='{.items[0].spec.containers[0].image}'
```

---

## 6. POST-ROLLBACK ACTIONS

```bash
# 1. Tag the bad revision:
git tag -a "bad-deploy-$(date +%Y%m%d-%H%M)" <BAD_COMMIT_SHA> -m "Rolled back — incident INC-1234"
git push origin "bad-deploy-$(date +%Y%m%d-%H%M)"

# Or tag the container image:
docker tag app:bad-tag app:rolled-back-$(date +%Y%m%d)

# 2. Open a bug ticket:
# Title: "Rollback: api-server v1.4.0 caused error rate spike"
# Link to the incident ticket and post-mortem.

# 3. Pin the previous (good) version so it doesn't get auto-pruned:
kubectl annotate deployment/api-server -n prod \
  "rollback-pin/$(date +%s)=good-version:$(kubectl rollout history deployment/api-server -n prod | tail -2 | head -1 | awk '{print $1}')"

# 4. Prevent the bad version from being redeployed:
# - If using ArgoCD/Flux: pause sync, fix the manifest or container tag.
# - If using Spinnaker: disable the pipeline that deploys bad version.
```

---

## 7. ROLLBACK METRICS (Post-Mortem Data)

Capture the following for the post-mortem:

- Time from alert trigger to rollback initiation: _____ minutes
- Time from rollback initiation to service recovery: _____ minutes
- Total incident duration: _____ minutes
- Was rollback automated or manual? _____
- Why wasn't this caught in staging/CI?
- What monitoring would have caught this sooner?

---

## ABORT CRITERIA

| Condition | Action | Timebox |
|-----------|--------|---------|
| Rollback fails (pods stuck in CrashLoopBackOff, etc.) | **Stop.** Escalate to Deployment / Infra team. | Immediately |
| Rollback makes things worse (higher error rate) | **Stop rollback.** Re-deploy the previous version manually. Escalate. | Immediately |
| DB migration was part of deploy and data loss risk exists | Do NOT blindly rollback DB. Consult DBA. | Before rollback |
| Rollback takes >5 minutes (stuck in progress) | Escalate to Infra team. Consider manual intervention. | 5 min |
| Multiple services were deployed together | Rollback all in reverse order of deployment. Escalate to Incident Commander. | — |
