# Blameless Post-Mortem Template

> **Category:** Foundations | Incident Management
> **Difficulty:** Basic
> **Last Reviewed:** 2026-06
> **Tags:** `#postmortem` `#incident` `#template`

---

## Table of Contents

1. [Template Reference](#template-reference)
2. [Filled Example: Payment Service Outage (2024-07-22)](#filled-example)
3. [Writing a Good Post-Mortem](#writing-a-good-post-mortem)

---

## Template Reference

Copy-paste the sections below for a new post-mortem. Every section is mandatory for P0/P1 incidents.

```markdown
# Incident Post-Mortem: [Brief Title]

| Field | Value |
|-------|-------|
| **Incident ID** | INC-YYYYMMDD-XXX |
| **Date** | YYYY-MM-DD |
| **Duration** | X hours Y minutes (HH:MM UTC → HH:MM UTC) |
| **Severity** | P0 / P1 / P2 / P3 |
| **Status** | Draft / In Review / Final |
| **Authors** | Name(s) |
| **Reviewers** | Name(s) |
| **Slack Channel** | #inc-YYYYMMDD-[description] |
| **Related Tickets** | JIRA/Linear links |

---

## 1. Executive Summary

[One paragraph — what happened, business impact, duration, how it was resolved.]

---

## 2. Severity Justification

[Why this severity? Reference the severity classification matrix.]

---

## 3. Timeline (All times UTC)

| Time | Event | Source |
|------|-------|--------|
| HH:MM | [What happened] | Alert / Manual / Deploy |
| HH:MM | [Who did what] | Action |
| ... | ... | ... |

> **Source legend**: Alert (automated), Deploy (CI/CD pipeline), Human (manual observation or action), Customer (user report)

---

## 4. Detection

- **How was the incident detected?**
- **Time to detect (MTTD):** X minutes
- **If automated**: Which monitor fired? Include the alert name and query.
- **If manual**: How can we automate detection? File a ticket.

---

## 5. Impact

| Metric | Value |
|--------|-------|
| **Users affected** | X (Y% of active users) |
| **Failed requests** | X requests |
| **Revenue lost** | $X (estimated) |
| **Data lost/corrupted** | None / X records |
| **Regions affected** | us-east-1, eu-west-1, global |
| **Services affected** | api-gateway, payment-service |

---

## 6. Root Cause

[The *technical* root cause. Not "human error" — that's a symptom of broken systems.]

Format:
```
WHAT: [The specific technical failure]
WHY:  [Why did this failure occur]
HOW:  [How did this failure propagate to cause user impact]
```

Example (bad): "Alice made a mistake in the deployment script."
Example (good): "The deployment script did not validate health check responses before routing traffic to new instances. When the health check endpoint returned 200 but the app was not fully initialized, requests failed."

---

## 7. Five Whys Analysis

```
Why did the incident occur?
  1. [Immediate cause]
    Why?
  2. [Next level cause]
    Why?
  3. [Process/system cause]
    Why?
  4. [Cultural/organizational cause]
    Why?
  5. [Root cause — typically a missing process, safety net, or validation]
```

---

## 8. What Went Well

- [Celebrate good decisions. This is not just a catalogue of failures.]
- [Example: "Communication was fast and clear in #inc-..."]
- [Example: "Rollback was smooth and completed in 2 minutes"]
- [Example: "On-call engineer escalated to DB team quickly when it was clear the issue was DB-related"]

---

## 9. What Went Wrong

- [Be specific. Avoid "communication could have been better."]
- [Example: "No one updated the status page for the first 30 minutes"]
- [Example: "The deployment pipeline did not run integration tests against production-like data"]
- [Example: "The runbook for 'DB connection pool exhaustion' was 6 months out of date"]

---

## 10. Action Items

| # | Description | Type | Owner | Deadline | Priority | Ticket |
|---|------------|------|-------|----------|----------|--------|
| 1 | [What needs to be done] | Preventive/Detective/Corrective | @name | YYYY-MM-DD | P0/P1/P2 | JIRA-1234 |
| 2 | ... | ... | ... | ... | ... | ... |

**Type definitions:**
- **Preventive**: Stops this class of incident from happening again (e.g., add pre-deploy validation)
- **Detective**: Catches the problem faster next time (e.g., add monitoring alert)
- **Corrective**: Fixes the underlying system weakness (e.g., refactor fragile component)

---

## 11. Lessons Learned

[Written for future on-call engineers. What would you tell someone encountering this incident next year?]

Format:
```
IF [symptoms you observe],
THEN [most likely cause and fastest mitigation],
BECAUSE [why this happens].
```

Example:
```
IF dashboard-api p99 latency > 5s AND postgres connection count > 80% of max,
THEN check pg_stat_activity for 'idle in transaction' connections and
     terminate connections idle > 30s,
BECAUSE orphaned idle-in-transaction connections hold resources and
      block new connections, causing connection pool exhaustion.
```

---

## 12. Appendix

- **Dashboards**: [Links to relevant Grafana/Datadog dashboards]
- **Logs**: [Links to relevant log queries / log streams]
- **Slack Threads**: [Links to key Slack conversations]
- **PRs/Deploys**: [Links to relevant code changes]
- **Previous Related Incidents**: [Links to past post-mortems for same service]
```

---

## Filled Example

# Incident Post-Mortem: Payment API 100% Failure During DB Migration

| Field | Value |
|-------|-------|
| **Incident ID** | INC-20240722-001 |
| **Date** | 2024-07-22 |
| **Duration** | 48 minutes (14:23 UTC → 15:11 UTC) |
| **Severity** | P0 |
| **Status** | Final |
| **Authors** | Carol Chen (SRE), David Park (Backend) |
| **Reviewers** | Alice Johnson (EM), Bob Smith (Staff SRE) |
| **Slack Channel** | #inc-20240722-payment-outage |
| **Related Tickets** | ENG-8930, ENG-8931, ENG-8932, ENG-8933 |

---

## 1. Executive Summary

On 2024-07-22 at 14:23 UTC, the payment-api service began returning 500 errors for 100% of requests. The outage lasted 48 minutes and affected all payment processing globally. Approximately 127,000 transactions failed, resulting in an estimated $23,400 in lost revenue. The root cause was a database migration that added a `NOT NULL` column without a default value to the `transactions` table, which was still being written to by the currently-running application code. The migration held an `ACCESS EXCLUSIVE` lock on the table, blocking all writes from `payment-api`. The incident was resolved by killing the migration query and rolling back the schema change.

---

## 2. Severity Justification

- **P0** — Complete outage of a critical business function (payment processing).
- 100% of requests failed for all users globally.
- Direct revenue impact > $10,000.

Severity criteria reference: [Internal Severity Matrix](https://wiki.internal/incident-severity)

---

## 3. Timeline (All times UTC)

| Time | Event | Source |
|------|-------|--------|
| 14:16 | DB migration `V2024.07.22.001__add_collection_status.sql` deployed to production via Flyway | Deploy |
| 14:17 | Migration begins executing. Acquires `ACCESS EXCLUSIVE` lock on `transactions` table. | — |
| 14:20 | `transactions` table has ~85M rows. Migration still running, lock still held. | — |
| 14:23 | `payment-api` instances begin timing out on DB writes. Error rate spikes. | Alert |
| 14:23 | Prometheus alert fires: `PaymentErrorRateHigh > 5%` | Alert |
| 14:24 | Carol (primary on-call) acknowledges page. Opens laptop. | Human |
| 14:26 | Carol declares P0 in #inc-20240722-payment-outage. Assigns IC to self. | Human |
| 14:28 | Carol checks Grafana: 100% error rate on `payment-api`. 502 from `api-gateway`. | Human |
| 14:30 | Carol checks recent deploys. Sees Flyway migration at 14:16. No application deploy. | Human |
| 14:32 | Carol checks RDS Performance Insights. Sees `transactions` table has ~12,000 sessions in `wait/io/table/sql/handler` state. | Human |
| 14:34 | Carol queries: `SELECT pid, state, wait_event, query FROM pg_stat_activity WHERE wait_event IS NOT NULL;` → Finds migration blocking 11,847 queries. | Human |
| 14:36 | Carol kills the migration: `SELECT pg_terminate_backend(28471);` | Action |
| 14:37 | Lock is released. Writes resume. Error rate begins dropping. | Alert |
| 14:39 | Error rate back to 0%. Carol announces: "service restored, monitoring now." | Human |
| 14:40 | David (backend lead) joins to investigate root cause. | Human |
| 14:45 | Root cause identified: migration adds `NOT NULL` column without default. This requires a full table rewrite with an `ACCESS EXCLUSIVE` lock. | Human |
| 14:50 | David opens PR #8923 to revert the migration. | Action |
| 14:55 | Revert migration merged and deployed. Schema back to pre-migration state. | Deploy |
| 15:05 | David opens PR #8924 with the corrected migration: add column as nullable → backfill data → add NOT NULL constraint. | Action |
| 15:10 | All dashboards green. 25+ min of clean monitoring. | Human |
| 15:11 | Carol declares RESOLVED. | Human |

---

## 4. Detection

- **Detected by**: Automated Prometheus alert `PaymentErrorRateHigh`.
- **MTTD**: ~0 minutes (alert fired within seconds of error rate exceeding threshold).
- **Alert definition**:

```promql
- alert: PaymentErrorRateHigh
  expr: |
    sum(rate(http_requests_total{service="payment-api",status=~"5.."}[5m]))
    /
    sum(rate(http_requests_total{service="payment-api"}[5m]))
    > 0.05
  for: 1m
  labels:
    severity: page
  annotations:
    summary: "Payment API error rate > 5%"
    runbook_url: "https://wiki.internal/runbooks/payment-api-errors"
```

**Assessment**: Detection was excellent. The alert fired before any user reports came in. No improvement needed.

---

## 5. Impact

| Metric | Value |
|--------|-------|
| **Users affected** | ~85,000 unique users (all users attempting payments) |
| **Failed transactions** | ~127,000 |
| **Revenue lost (estimated)** | $23,400 (based on avg transaction value * failed txns * conversion recovery rate of 65%) |
| **Data lost/corrupted** | None. Migration was killed before completion. No data was modified. |
| **Regions affected** | Global (single DB primary, all regions affected) |
| **Services affected** | `payment-api`, `api-gateway` (cascading), `checkout-ui` (cascading) |

---

## 6. Root Cause

**WHAT**: Database migration `V2024.07.22.001` added a `NOT NULL` column (`collection_status VARCHAR(50) NOT NULL`) to the `transactions` table without providing a `DEFAULT` value. PostgreSQL's implementation of `ADD COLUMN ... NOT NULL` without a default requires a full table scan to verify no null values exist, during which it holds an `ACCESS EXCLUSIVE` lock on the table. This lock blocks ALL concurrent reads and writes.

```sql
-- DANGEROUS: holds ACCESS EXCLUSIVE lock for entire table scan
ALTER TABLE transactions
ADD COLUMN collection_status VARCHAR(50) NOT NULL;

-- SAFE: three-step approach, locks are brief
-- Step 1: Add column as nullable (instant, no lock)
ALTER TABLE transactions
ADD COLUMN collection_status VARCHAR(50);

-- Step 2: Backfill data in batches (no long-lived lock)
UPDATE transactions SET collection_status = 'pending'
WHERE collection_status IS NULL;
-- (repeat until all rows updated)

-- Step 3: Add NOT NULL constraint (fast after data is populated)
ALTER TABLE transactions
ALTER COLUMN collection_status SET NOT NULL;
```

**WHY**: The migration was written by a developer unfamiliar with PostgreSQL's locking behavior for schema changes. The code review did not catch this because the reviewer was also unfamiliar with this PostgreSQL-specific behavior.

**HOW**: The `ACCESS EXCLUSIVE` lock blocked approximately 11,800 concurrent write queries from 24 `payment-api` instances. Application-level connection pools filled up within 20 seconds. Connection pool exhaustion caused every subsequent request to fail immediately. The `api-gateway` responded with 502 errors to clients.

---

## 7. Five Whys Analysis

```
1. Why did payment-api return 500 errors for all requests?
   → The transactions table was locked by a schema migration, blocking all writes.

2. Why did the migration lock the table?
   → The migration used `ALTER TABLE ... ADD COLUMN ... NOT NULL`, which
     acquires an ACCESS EXCLUSIVE lock for a full table scan on PostgreSQL.

3. Why was this migration approved and deployed?
   → The code reviewer did not identify the locking impact.
     The CI pipeline does not include a migration safety check.

4. Why does the CI pipeline not check for dangerous migrations?
   → We have not yet implemented a migration linter (like
     squawk or strong_migrations) in CI. This was on the
     platform team's backlog for Q3.

5. Why was migration safety de-prioritized?
   → No incident had occurred before to demonstrate the risk.
     Platform team was under pressure to deliver feature work.
     Risk was theoretical; now it is proven.
```

---

## 8. What Went Well

1. **Detection was immediate.** The `PaymentErrorRateHigh` alert fired within seconds, before any user reports.
2. **Incident commander declared quickly.** Carol declared P0 and assigned herself IC within 2 minutes of acknowledging.
3. **Mitigation was fast.** Carol identified the blocking query within 8 minutes and killed it. Service restored at T+14 minutes.
4. **Communication was clear.** Carol posted updates to #inc-* channel every 3-5 minutes. The Communications Lead (auto-assigned by bot) updated the status page at T+15.
5. **Handoff was seamless.** David joined at T+17 and took over root cause investigation while Carol monitored.

---

## 9. What Went Wrong

1. **No migration safety check in CI.** The pipeline accepted a migration that performs a full-table lock on an 85-million-row table with no warnings.
2. **Migration was not tested against a production-sized dataset.** The staging database has ~500K rows in `transactions` (vs 85M in prod). The migration completed in 3 seconds in staging and was approved.
3. **No automated pre-deploy checklist for DB migrations.** There is no prompt asking: "Have you tested this on a prod-sized dataset? Does this migration lock the table? What is the expected duration?"
4. **"Silent" part of the outage (14:17-14:23).** The migration started at 14:17 but the alert fired at 14:23. This 6-minute gap was because the connection pools had not yet fully saturated. Adding a lock-wait monitoring alert would have caught it sooner.
5. **No integrated migration linter.** Tools like Squawk or `strong_migrations` for Ruby or `django-migration-linter` for Django would have blocked this at CI stage.

---

## 10. Action Items

| # | Description | Type | Owner | Deadline | Priority | Ticket |
|---|------------|------|-------|----------|----------|--------|
| 1 | Add `squawk` migration linter to CI pipeline. Block any migration that holds `ACCESS EXCLUSIVE` lock for > 1 second on tables > 1M rows. | Preventive | @david | 2024-08-01 | P0 | ENG-8930 |
| 2 | Create a staging environment with a production-scale anonymized dataset for migration testing (> 50M rows in key tables). | Preventive | @david | 2024-08-15 | P1 | ENG-8931 |
| 3 | Add PostgreSQL lock-wait monitoring alert: `pg_stat_activity` with `wait_event` not null for > 10 seconds. | Detective | @carol | 2024-07-26 | P0 | ENG-8932 |
| 4 | Add mandatory pre-deploy checklist item in the DB migration PR template: "Tested against >= 50M row dataset? Lock duration? Rollback plan?" | Preventive | @carol | 2024-07-26 | P1 | ENG-8933 |
| 5 | Update the `payment-api` runbook with a section on "DB lock contention" including the mitigation steps used in this incident. | Corrective | @carol | 2024-07-28 | P2 | ENG-8934 |
| 6 | Schedule a knowledge-sharing session on PostgreSQL locking behavior for all backend and SRE engineers. | Preventive | @david | 2024-08-08 | P2 | ENG-8935 |

---

## 11. Lessons Learned

```
IF payment-api begins returning 500 errors globally with no recent application deploy,
AND the errors correlate with a recent Flyway migration,
AND pg_stat_activity shows many sessions with wait_event = 'relation' or 'lock',
THEN identify and kill the blocking query:
       SELECT pid, query, wait_event, state,
              age(now(), query_start) AS duration
       FROM pg_stat_activity
       WHERE wait_event IS NOT NULL;
       SELECT pg_terminate_backend(<pid>);
BECAUSE a schema migration holding ACCESS EXCLUSIVE lock blocks all
      concurrent access to the table, causing cascading failures.

ALSO: Never deploy a migration that adds a NOT NULL column without a
      DEFAULT to a large table (>1M rows) outside of a maintenance window.
      Use the three-step approach (nullable → backfill → NOT NULL).
```

---

## 12. Appendix

- **Grafana Dashboard**: [Payment API Dashboard](https://grafana.internal/d/payment-api)
- **Grafana Dashboard**: [RDS Performance Insights](https://grafana.internal/d/rds-payments)
- **Slack Thread**: [#inc-20240722-payment-outage](https://slack.internal/archives/inc-20240722-payment-outage)
- **PR (Revert)**: [#8923 — Revert V2024.07.22.001](https://github.internal/company/service/pull/8923)
- **PR (Correct Fix)**: [#8924 — Safe migration: add collection_status](https://github.internal/company/service/pull/8924)
- **Flyway Migration**: `V2024.07.22.001__add_collection_status.sql`
- **Logs Query (Loki)**: `{service="payment-api"} |= "transaction" |= "500"` for 14:16-15:11 UTC
- **Lock Diagnosis Query**:
  ```sql
  SELECT
    blocked.pid AS blocked_pid,
    blocked.query AS blocked_query,
    blocking.pid AS blocking_pid,
    blocking.query AS blocking_query,
    age(now(), blocked.query_start) AS blocked_duration
  FROM pg_stat_activity AS blocked
  JOIN pg_stat_activity AS blocking
    ON blocked.wait_event_type = 'Lock'
    AND blocking.pid != blocked.pid
  WHERE blocked.wait_event IS NOT NULL;
  ```

---

## Writing a Good Post-Mortem

### DO:

- **Be specific.** "Deployment took 4 minutes instead of 30 seconds because..." not "Deployment was slow."
- **Be blameless.** "The migration script did not check..." not "Alice wrote a bad migration."
- **Write for the future on-call engineer.** Two years from now, someone should be able to read this and immediately know what happened and what to do.
- **Include query outputs, commands, screenshots.** The more concrete, the more useful.
- **Set deadlines for action items.** "Soon" means "never."

### DON'T:

- **Don't use "human error" as a root cause.** It's lazy and doesn't prevent recurrence. Ask "why was the human able to make this error?"
- **Don't name-and-shame.** Post-mortems are read by the entire engineering org. Your colleague's mistake today could be yours tomorrow.
- **Don't skip the "What Went Well" section.** It's not just feel-good filler — it reinforces good practices and decisions under pressure.
- **Don't write a novel.** Be thorough but concise. A 20-page post-mortem won't get read.
- **Don't delay.** Write the post-mortem within 48 hours while memory is fresh. If you wait 2 weeks, critical details will be lost.
