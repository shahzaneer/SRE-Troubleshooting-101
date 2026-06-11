# Incident Lifecycle

> **Category:** Foundations | Incident Management
> **Difficulty:** Basic
> **Last Reviewed:** 2026-06
> **Tags:** `#incident` `#lifecycle` `#process`

---

## Table of Contents

1. [Overview](#overview)
2. [Full Lifecycle with Timeline](#full-lifecycle-with-timeline)
3. [Severity Classification](#severity-classification)
4. [Roles](#roles)
5. [Scenario Walkthroughs](#scenario-walkthroughs)
6. [Anti-Patterns](#anti-patterns)

---

## Overview

```
┌──────────┐   ┌──────────────┐   ┌─────────┐   ┌──────────────┐
│ DETECTION │ → │ ACKNOWLEDGMENT │ → │ TRIAGE  │ → │ INVESTIGATION │
└──────────┘   └──────────────┘   └─────────┘   └──────────────┘
                                                      │
                                                      ▼
┌─────────────┐   ┌────────────┐   ┌────────────┐   ┌──────────────┐
│ POST-MORTEM │ ← │ MONITORING │ ← │ RESOLUTION │ ← │ MITIGATION   │
└─────────────┘   └────────────┘   └────────────┘   └──────────────┘
```

**Golden Rule**: Mitigation comes before investigation. Stop the bleeding first, then figure out why.

---

## Full Lifecycle with Timeline

### Phase 1: Detection (T+0)

| Method | Example | Time to Detect |
|--------|---------|---------------|
| Monitoring alert | Prometheus fires `HighLatency` alert | < 1 minute |
| Synthetics / probes | Blackbox exporter detects 5xx | < 1 minute |
| User report | Customer emails support "App is down" | 5-30 minutes |
| Social media | Twitter explodes | 10-60 minutes |
| Internal engineer | Dev notices error spikes in dashboard | Variable |

**Goal**: Automated detection is always faster than manual. If a user reports an issue before your monitoring catches it, your monitoring is broken.

```promql
# Example alert that should fire BEFORE users notice
- alert: ApiErrorRateHigh
  expr: |
    sum(rate(http_requests_total{status=~"5.."}[5m])) by (service)
    /
    sum(rate(http_requests_total[5m])) by (service)
    > 0.01
  for: 2m
  labels:
    severity: page
  annotations:
    summary: "API 5xx rate > 1% for {{ $labels.service }}"
    runbook_url: "https://wiki.internal/runbooks/api-5xx"
```

---

### Phase 2: Acknowledgment (T+0:30)

```
TIMELINE:
  T+0:00   Alert fires, PagerDuty notifies primary on-call
  T+0:30   Primary acknowledges (or snoozes with reason)
  T+5:00   If no ack → escalate to secondary on-call
  T+10:00  If still no ack → escalate to engineering manager
  T+15:00  If still no ack → escalate to director on-call
  T+20:00  If still no ack → VP engineering (this is EVERYONE's problem now)
```

**Acknowledging means**: "I have seen this alert and I am taking ownership. I may not have a solution yet, but I am working on it."

---

### Phase 3: Triage (T+2 min)

**Triage Questions** (answered in the first 5 minutes):

```text
1. SEVERITY: Is this P0, P1, P2, or P3?
2. BLAST RADIUS: How many users? What % of traffic? Which regions?
3. SCOPE: Single service? Multiple services? Infrastructure?
4. TIMING: Did something just deploy? Did a certificate expire?
5. OWNERSHIP: Who else needs to be involved? (DB team, Network team, etc.)
```

**Declare the incident**:

```text
/incident declare
  title: "api-gateway returning 502 on /v2/payments"
  severity: P0
  commander: @alice
  channel: #inc-20260315-payments-502
  slack: https://slack.com/...
  statuspage: https://status.example.com
```

---

### Phase 4: Investigation (T+5 min)

**Scientific method approach** — do NOT guess:

```text
1. OBSERVE:   "api-gateway returning 502 for 30% of requests"
2. HYPOTHESIZE: "Upstream payment-service may be down"
3. PREDICT:     "If true, I'll see payment-service health checks failing"
4. TEST:        "curl payment-service.internal/health → Connection refused"
5. CONCLUDE:    "payment-service is not accepting connections"

New hypothesis:
1. HYPOTHESIZE: "payment-service pods may have crashed"
2. PREDICT:     "If true, kubectl get pods will show CrashLoopBackOff"
3. TEST:        "kubectl get pods -n payment → 3/7 in CrashLoopBackOff"
4. CONCLUDE:    "3 out of 7 pods crashed. Root cause still unknown.
                 MITIGATE FIRST → scale up pods immediately."
```

**Initial Investigation Checklist**:

```
□ Check service dashboards (Grafana/Datadog/New Relic)
  - Latency, error rate, throughput — RED metrics
  - CPU, memory, disk, network — USE metrics

□ Check recent deploys (last 1 hour)
  - git log --oneline --since="1 hour ago"
  - Deployment pipeline status
  - Any config changes?

□ Check upstream dependencies
  - DB: connection pool status, slow queries, replication lag
  - Cache: hit rate, eviction rate
  - Queue: depth, consumer lag
  - External APIs: status pages, latency

□ Check infrastructure
  - Cloud provider status page (AWS/GCP/Azure)
  - Node status (disk full? memory pressure? zombie processes?)
  - Network (DNS resolution? firewall rules? load balancer health?)

□ Check recent incidents
  - Is this a recurrence of a previous incident?
  - Is there a known fix / workaround?
```

---

### Phase 5: Mitigation (T+10 min)

**MITIGATE FIRST. Root cause can wait.**

```text
Mitigation Strategies (by class of problem):

PROBLEM: Service unavailable
  → Restart pods: kubectl rollout restart deployment/X
  → Scale up: kubectl scale deployment/X --replicas=20
  → Fail over to standby: trigger DNS cutover

PROBLEM: Database slow
  → Kill long-running queries: SELECT pg_terminate_backend(pid)
  → Fail over to read replica for read traffic
  → Scale up DB instance (more CPU/RAM)

PROBLEM: Traffic spike (legitimate or DDoS)
  → Circuit break: rate-limit at load balancer / API gateway
  → Shed load: return 503 for non-critical endpoints
  → Geo-block if attack is regional

PROBLEM: Bad deploy
  → Rollback: helm rollback / kubectl rollout undo
  → Scale down new pods, scale up old pods

PROBLEM: Cache stampede / thundering herd
  → Enable request coalescing
  → Pre-warm cache
  → Implement circuit breaker
```

**Scenario: Mitigating before investigating**

```
T+2:   Alert fires: "payment-api p99 latency = 20s"
T+3:   Triage: P0, global, 100% of payment traffic affected
T+5:   Engineer notices DB connection pool is at 100%
T+6:   Engineer runs: SELECT pg_terminate_backend(pid)
         FROM pg_stat_activity
         WHERE state = 'idle in transaction'
         AND age(now(), query_start) > interval '30 seconds';
       → Kills 47 idle-in-transaction connections
T+8:   Pool drops to 40%. Latency drops to 200ms. SERVICE RESTORED.
T+9:   Announce mitigation on incident channel.

T+15:  NOW investigate root cause.
       → Found: New deployment changed connection pool timeout from 30s to 300s.
       → Connections held open 10x longer.

Lesson: 6 minutes to mitigate. Without mitigation-first mindset,
        investigation could have taken 30+ minutes. 30 minutes of P0 outage.
```

---

### Phase 6: Resolution (T+30 min to T+4 hours)

**Resolution = root cause is understood AND fixed.** Mitigation is temporary; resolution is permanent.

```text
RESOLUTION CHECKLIST:
□ Root cause identified and documented
□ Fix implemented and validated
□ Fix deployed to production
□ Monitored for 15 min → no regression
□ Incident commander declares "RESOLVED"
□ Status page updated
□ Incident channel archived

TEMPORARY MITIGATION → PERMANENT FIX:
  Mitigation: Killed idle connections → service restored
  Root cause: Connection pool timeout changed 30s → 300s
  Fix: Revert timeout to 30s. Add connection pool monitoring. Add alert
        when pool utilization > 80%.
  Ticket: ENG-4721 "Add connection pool timeout to deployment acceptance tests"
```

---

### Phase 7: Monitoring (T+30 min to T+8 hours)

**Watch for recurrence.** Duration depends on severity:

| Severity | Monitor Period | Required |
|----------|---------------|----------|
| P0 | 2 hours | Continuous watching by incident commander + on-call |
| P1 | 1 hour | Check dashboards every 15 min |
| P2 | 30 minutes | Spot check after 30 min |
| P3 | Normal monitoring | No special watch |

```text
POST-RESOLUTION MONITORING:
  - For P0: DO NOT close your laptop for 2 hours
  - Watch: error rate, latency, saturation
  - Be ready: the "fix" might unmask a deeper problem
  - Real scenario: Fixed DB pool → 15 min later app server runs OOM
    because the app server had been throttled by slow DB; now with
    fast DB, it processes more requests and hits memory ceiling.
```

---

### Phase 8: Post-Mortem (within 48 hours)

Must be completed while memory is fresh. See [Blameless Post-Mortem Template](blameless-postmortem-template.md) for full format.

Deadlines:

| Severity | Post-Mortem Deadline |
|----------|---------------------|
| P0 | 24 hours (draft) / 48 hours (final) |
| P1 | 48 hours |
| P2 | 1 week |
| P3 | No post-mortem required (ticket for fix) |

---

## Severity Classification

```text
P0 - CRITICAL
  - Complete service outage OR data loss/corruption
  - Example: api-gateway returning 503 for ALL requests globally
  - Response: Immediate page. War room within 5 min.
  - SLA: Respond < 5 min, Resolve < 1 hour

P1 - MAJOR
  - Significant degradation. Core feature broken for many users.
  - Example: Payment processing failing for 30% of users in US-East
  - Response: Immediate page.
  - SLA: Respond < 15 min, Resolve < 4 hours

P2 - MINOR
  - Non-critical feature broken. Workaround exists.
  - Example: Export-to-CSV fails, but users can see data in UI
  - Response: During business hours. No page.
  - SLA: Respond < 4 hours, Resolve < 24 hours

P3 - COSMETIC
  - Visual bug, typos, non-functional issues
  - Example: Logo not loading on settings page
  - Response: Next sprint.
  - SLA: Respond < 24 hours, Resolve < 1 week
```

---

## Roles

During a P0/P1 incident, declare these roles explicitly:

```text
INCIDENT COMMANDER (IC)
  - Runs the incident. Makes decisions. Delegates.
  - Says: "Bob, check the DB. Alice, look at recent deploys."
  - Says: "We are rolling back. Do it now."
  - The IC can be anyone, not just the most senior engineer.

COMMUNICATIONS LEAD (CL)
  - Manages stakeholder communication
  - Updates status page every 15 min
  - Posts updates to #inc-* channel
  - Handles executive questions so IC can focus
  - Says: "Status page updated. ETA 30 min."

SUBJECT MATTER EXPERTS (SMEs)
  - Investigate their area of expertise
  - DB SME: checks slow queries, replication, connection pools
  - Network SME: checks DNS, load balancers, firewall rules
  - App SME: checks recent deploys, app logs, error traces
```

---

## Scenario Walkthroughs

### Scenario 1: P0 — Complete Payment Outage

```text
=== TIMELINE ===

2026-03-15 14:23:00 UTC  [DETECTION]
  Prometheus fires alert: "PaymentErrorRate = 100%"
  Alert rule: sum(rate(http_requests_total{status=~"5..",service="payments"}[5m]))
              / sum(rate(http_requests_total{service="payments"}[5m])) > 0.05

2026-03-15 14:23:45 UTC  [ACKNOWLEDGMENT]
  Alice (primary on-call) acknowledges on PagerDuty
  Opens laptop, connects to VPN

2026-03-15 14:25:00 UTC  [TRIAGE]
  Alice checks Grafana:
    - Error rate: 100% (all requests failing)
    - Latency: N/A (no successful requests)
    - Throughput: dropping (clients giving up)
  Severity: P0 (complete outage of core business function)
  Alice declares incident:
    /incident declare severity=P0 service=payments commander=alice

2026-03-15 14:28:00 UTC  [INVESTIGATION - Initial]
  Alice checks recent deploys:
    - Pipeline shows deploy #3847 completed at 14:20
    - Commit: "Update payment provider TLS cert"
  Hypothesis: TLS cert change broke connection to Stripe
  Test: curl https://api.stripe.com from inside the cluster
  Result: "SSL certificate problem: certificate has expired"

2026-03-15 14:30:00 UTC  [MITIGATION]
  Alice initiates rollback:
    kubectl rollout undo deployment/payment-service -n payments
  Waits for rollback to complete:
    kubectl rollout status deployment/payment-service -n payments
  → Rollback complete at 14:32

2026-03-15 14:33:00 UTC
  Alice verifies: error rate drops to 0%. Payments succeeding.
  Alice announces: "Service restored via rollback. Monitoring now."

2026-03-15 14:35:00 UTC  [INVESTIGATION - Root Cause]
  Root cause found: New TLS cert was a SHA-1 certificate.
  Stripe deprecated SHA-1 on 2026-03-15 (today).
  The engineer who updated the cert didn't check the signature algorithm.

2026-03-15 14:50:00 UTC  [RESOLUTION]
  Fix: Deploy new cert (SHA-256) to staging. Validate. Deploy to prod.
  Fix verified: error rate 0%, latency p50 = 120ms.

2026-03-15 15:30:00 UTC
  Alice declares RESOLVED.

2026-03-15 16:30:00 UTC  [MONITORING END]
  2 hours of P0 monitoring complete. No recurrence.

2026-03-16 10:00:00 UTC  [POST-MORTEM]
  Post-mortem drafted and submitted.

=== SUMMARY ===
  Duration: 9 minutes (14:23 → 14:32)
  Method: Rollback (mitigation) then fix (resolution)
  Impact: ~8,400 payment requests failed
  Revenue lost: ~$3,200

  What went well:
    - Detection was instant (alerted before users complained)
    - Rollback was fast (2 minutes)
    - Commander communicated clearly in Slack

  What went wrong:
    - TLS cert update was not tested against Stripe staging
    - No automated cert validation pipeline (check algorithm, expiry, chain)
    - No canary deploy — went straight to 100%

  Action items:
    1. Add cert validation step to CI/CD: verify algorithm = SHA-256+
    2. Implement canary deploys for payment-service
    3. Create integration test that validates Stripe connectivity in staging
```

---

### Scenario 2: P1 — Slow Database (The "Don't Jump to Conclusions" Story)

```text
=== TIMELINE ===

2026-04-02 09:15:00 UTC  [DETECTION]
  User reports via Slack: "Dashboard loading really slow, 30+ seconds"

2026-04-02 09:17:00 UTC  [TRIAGE]
  Engineer Bob checks Grafana:
    - dashboard-api p99 latency: 22s (normal: 200ms)
    - dashboard-api error rate: 2% (some timeouts at 30s)
    - dashboard-api throughput: normal
  Severity: P1 (significant degradation, workaround exists — data still correct)

2026-04-02 09:20:00 UTC  [INVESTIGATION - BAD APPROACH]
  Bob thinks: "Slow dashboard, must be the DB query I wrote last week."
  Spends 15 minutes looking at his query plan. Sees nothing wrong.
  Tries adding an index. No improvement.
  Colleague Carol joins: "Are you sure it's the DB?"
  Bob: "What else could it be?"

2026-04-02 09:35:00 UTC  [INVESTIGATION - GOOD APPROACH]
  Carol: "Let's do the half-split method."
  Check: Is it the app, DB, network, or infrastructure?

  FRONT-TO-BACK:
    1. curl dashboard-api.internal/health → responds in 5ms ✓
    2. curl dashboard-api.internal/v2/dashboard → responds in 18s ✗
       → Problem is server-side, in the request path

  HALF-SPLIT:
    3. Is it the app code or the DB?
       App logs show: "db_query_time=18.2s query=SELECT * FROM metrics..."
       → It's the DB.

  CHECK DB:
    4. SELECT * FROM pg_stat_activity WHERE state != 'idle';
       Shows 12 queries running, 8 of them on `metrics` table, all > 15s

    5. EXPLAIN ANALYZE SELECT * FROM metrics WHERE ...;
       → Seq Scan on metrics (cost=0.00..52341.00 rows=15000000 width=156)
       → 15 million rows scanned. No index used.

    6. Check if index exists:
       SELECT indexname FROM pg_indexes WHERE tablename = 'metrics';
       → idx_metrics_timestamp EXISTS
       → idx_metrics_service_id EXISTS

    7. Wait, why is it doing a seq scan?
       → The query filters on `timestamp` AND `service_id` with OR,
         not AND. Query planner can't use both indexes.

2026-04-02 09:40:00 UTC  [MITIGATION]
  Carol identifies the query is from a new Grafana dashboard
  deployed yesterday. The query is:
    SELECT * FROM metrics
    WHERE timestamp > now() - interval '7 days'
    AND (service_id = 'X' OR service_id = 'Y' OR service_id = 'Z');

  Mitigation: Replace OR with UNION ALL:

    SELECT * FROM metrics WHERE timestamp > now() - interval '7 days'
    AND service_id = 'X'
    UNION ALL
    SELECT * FROM metrics WHERE timestamp > now() - interval '7 days'
    AND service_id = 'Y'
    UNION ALL
    SELECT * FROM metrics WHERE timestamp > now() - interval '7 days'
    AND service_id = 'Z';

  → Query time drops from 18s to 50ms. Problem solved.

2026-04-02 09:45:00 UTC  [RESOLUTION]
  PR opened to fix the Grafana dashboard query.
  Ticket filed: "Create composite index on metrics(timestamp, service_id)
                 for OR queries" ← P2, not urgent since query is already fixed.

=== SUMMARY ===
  Duration: 30 minutes (09:15 → 09:45)
  Wasted time: 15 minutes (Bob investigating the wrong query)

  Lesson: Don't start with your hypothesis. Start with observations.
          Bob assumed it was his code. Carol observed first, then hypothesized.
```

---

## Anti-Patterns

### 1. "Hero Syndrome"

```text
One engineer refuses to wake up anyone else, insists they can fix it
alone. Outage extends from 30 min → 3 hours.

FIX: Rotate roles. No single person holds all knowledge. Celebrate
     escalating (it's a skill, not weakness).
```

### 2. "Five People Debugging in Silence"

```text
Everyone staring at logs. No one talking. No one knows what anyone
else is doing. Duplicate work. No coordination.

FIX: Incident commander explicitly delegates: "Alice: DB. Bob: Network.
     Carol: Recent deploys. Report back in 5 minutes."
```

### 3. "Root Cause Tunnel Vision"

```text
Engineer is 100% convinced the issue is X. Spends 45 minutes
proving X. Ignores evidence that disproves X.

FIX: Set a 15-minute timer. If your hypothesis isn't confirmed in
     15 minutes, try a completely different hypothesis or escalate.
```

### 4. "Mitigation Delayed"

```text
Engineer knows what the mitigation is (rollback, restart, kill queries)
but doesn't do it because "I'm close to finding the root cause."
Outage continues while they investigate.

FIX: Incident commander enforces: "Mitigate now. Investigate after."
     If engineer pushes back: "I'm overriding. Mitigate."
```

### 5. "The Silent Incident"

```text
Incident is happening but:
  - No incident channel declared
  - No status page updated
  - No communication with stakeholders
  - Support team has no idea what to tell customers
  - CEO finds out from Twitter

FIX: The first action after acknowledgment is declaring the incident.
     Communication lead updates stakeholders every 15 minutes
     even if the update is "Still investigating. No ETA yet."
```
