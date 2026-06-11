# SRE Mindset

> **Category:** Foundations | SRE | Philosophy
> **Difficulty:** Basic
> **Last Reviewed:** 2026-06
> **Tags:** `#sre` `#slo` `#error-budget` `#toil`

---

## Table of Contents

1. [Error Budgets](#error-budgets)
2. [SLO / SLI / SLA](#sloslisla)
3. [Toil](#toil)
4. [The SRE Budget (50/50 Rule)](#the-sre-budget-5050-rule)
5. [Worst Practices](#worst-practices)
6. [Real-World Scenarios](#real-world-scenarios)

---

## Error Budgets

### The Core Innovation

An Error Budget is the single most important concept in SRE. It mathematically encodes the tension between reliability and feature velocity.

```
Error Budget = 1 - SLO
```

- **SLO** is your reliability target (e.g., 99.9% availability).
- **Error Budget** is the *acceptable* amount of unreliability per time window.

### How It Works (Concrete)

| SLO | Downtime per 30 days | Downtime per quarter |
|-----|---------------------|----------------------|
| 99.999% ("five nines") | 25.9 seconds | 1.3 minutes |
| 99.99% ("four nines") | 4.3 minutes | 13 minutes |
| 99.9% ("three nines") | 43.2 minutes | 2.2 hours |
| 99.5% | 3.6 hours | 10.8 hours |
| 99% | 7.2 hours | 21.6 hours |

### Error Budget Policy (Example)

```text
Error Budget Status           | Action
------------------------------|----------------------------------------
Budget > 50% remaining        | Normal operations. Deploy at will.
Budget 20-50% remaining       | Caution. Scrutinize risky changes.
Budget < 20% remaining        | Freeze non-critical deploys.
Budget exhausted (0%)         | FREEZE ALL FEATURE WORK.
                              | All engineering effort → reliability.
                              | VP approval required for exceptions.
```

### Scenario: Error Budget Exhausted

```
DATE:       2024-03-12
SERVICE:    api-gateway
SLO:        99.9% (43.2 min downtime/30 days)
STATUS:     Error budget exhausted at T+23 days into the window.

TRIGGER:    Three incidents in 7 days:
            1. March 5  — DNS misconfig: 8 min downtime
            2. March 8  — DB failover lag:  12 min downtime
            3. March 11 — Cache stampede:   28 min downtime
            Total: 48 min > 43.2 min budget

CONSEQUENCE:
  - CD pipeline blocked for feature deploys
  - All sprint work paused
  - 3-week reliability sprint mandated:
    - DNS: deploy health-checked DNS with canary
    - DB:  implement automated failover testing
    - Cache: add circuit breaker + request coalescing
  - New SLO review meeting added to sprint planning
```

**Key insight**: The error budget is not a penalty — it's a *safety valve*. Without it, product managers would push features until everything breaks. With it, there's a clear, data-driven reason to slow down before things break catastrophically.

---

## SLO / SLI / SLA

### Definitions with Concrete Examples

```text
SLI (Service Level Indicator)
  What you MEASURE.
  "99th percentile latency over last 5 minutes, measured at the load balancer"

SLO (Service Level Objective)
  Your INTERNAL target.
  "99.9% of requests over a 30-day rolling window must have p99 latency < 500ms"

SLA (Service Level Agreement)
  Your EXTERNAL promise to customers. Often includes financial penalties.
  "99.5% availability per calendar month, or customer gets 10% service credit"
```

### The Buffer Between SLO and SLA

```
          SLA (99.5%)                ← Customer contract, money-back guarantee
           | 0.4% buffer
          SLO (99.9%)                ← Internal target, triggers alerts
           | 0.09% buffer
          Operational reality         ← What engineering actually aims for
```

**Why this matters**: If your SLO = 99.9% and your SLA = 99.5%, you have a 0.4% buffer. Your pager fires at SLO breach (internal alert) long before the customer contract is threatened (SLA breach). This gives you time to fix issues *before* they cost money.

### Real SLO Definition (Prometheus-Based)

```yaml
# SLO: 99.9% of HTTP requests complete in under 500ms over 30 days
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: api-gateway-slo
spec:
  groups:
  - name: api-gateway-slo
    interval: 30s
    rules:
    # SLI: ratio of fast requests to all requests
    - record: job:http_request_duration_seconds_count:rate5m
      expr: rate(http_request_duration_seconds_count[5m])

    - record: job:http_request_duration_seconds_bucket:rate5m
      expr: rate(http_request_duration_seconds_bucket[5m])

    # This is the SLI: fraction of requests under 500ms
    - record: sli:api_gateway:latency_p99_ratio
      expr: |
        sum(rate(http_request_duration_seconds_bucket{le="0.5"}[5m]))
          by (service)
        /
        sum(rate(http_request_duration_seconds_count[5m]))
          by (service)

    # Error budget burn rate alert
    # Burn rate > 14.4 means you'll exhaust 30-day budget in 1 hour
    - alert: HighErrorBudgetBurnRate
      expr: |
        (
          1 - sli:api_gateway:latency_p99_ratio
        ) > 14.4 * (1 - 0.999)
      for: 10m
      labels:
        severity: critical
      annotations:
        summary: "Error budget burn rate > 14.4x for {{ $labels.service }}"
        description: |
          Service {{ $labels.service }} is burning error budget at {{
          $value | humanizePercentage }}. At this rate, the 30-day
          error budget will be exhausted in ~1 hour.
```

---

## Toil

### Definition (from Google SRE Book)

> Toil is the kind of work tied to running a production service that tends to be **manual, repetitive, automatable, tactical, devoid of enduring value**, and that scales linearly as a service grows.

### Toil vs. Engineering Work

| Toil | Engineering Work |
|------|-----------------|
| Manually restarting a flaky service | Writing a self-healing process supervisor |
| SSH-ing into 50 boxes to rotate logs | Configuring logrotate with Ansible |
| Hand-creating SSL certs for new domains | Building certbot integration with Let's Encrypt |
| Copy-pasting SQL to fix a data issue | Building an admin tool for common data operations |
| Resizing disks by hand when they fill up | Setting up LVM + auto-grow with monitoring |

### The 50% Toil Cap

Google SRE mandates that SRE teams spend no more than **50% of their time on toil** (ops work). The remaining 50% must go to **engineering project work** that reduces future toil and improves long-term reliability.

### Scenario: Toil Identification and Elimination

```
TEAM:     Platform SRE
PERIOD:   Q1 2024

TOIL AUDIT (time tracking over 2 weeks):

Task                                  | hrs/wk | Class     | Automatable?
--------------------------------------|--------|-----------|-------------
Manually creating SSL certs           | 10     | TOIL      | Yes
Restarting flaky service X            | 4      | TOIL      | Yes
Scaling up K8s nodes for spike traffic| 6      | TOIL      | Yes
Answering "can you add permission X"  | 8      | TOIL      | Yes
Fix production bug in payment module  | 3      | ENG       | No
Build new monitoring dashboard        | 5      | ENG       | N/A
On-call incident response             | 6      | TOIL/ENG  | Partial
Meetings                              | 8      | NEUTRAL   | N/A

TOTAL: 50 hrs/week
TOIL:  28 hrs/week = 56%  ← OVER THE 50% CAP

RESULT: New feature requests frozen until toil drops below 50%.

AUTOMATION SPRINT (2 weeks):
  1. SSL certs: Deployed cert-manager with Let's Encrypt → reclaimed 10 hrs/wk
  2. Flaky service: Added liveness probe + auto-restart → reclaimed 4 hrs/wk
  3. K8s scaling: Implemented HPA with custom metrics → reclaimed 6 hrs/wk
  4. Permissions: Built self-service RBAC portal → reclaimed 8 hrs/wk

NEW TOIL: 0 hrs/wk = 0%  ✓
```

### Toil Kill Commandment

```
If you do a manual task more than TWICE, ask:
  - Can this be scripted?
  - Can this be a self-service tool?
  - Can this be eliminated entirely?

If you do a manual task more than THREE TIMES,
  you MUST file a ticket to automate it.

If you do a manual task more than FIVE TIMES,
  STOP DOING IT until automation exists.
```

---

## The SRE Budget (50/50 Rule)

The SRE team's time must be split:

- **≤ 50% Ops work**: On-call, incident response, manual interventions, toil
- **≥ 50% Project work**: Automation, reliability improvements, monitoring, performance tuning, architectural changes

### Enforcement

```text
WEEKLY REVIEW:
  - Every Friday, lead reviews Jira/Linear for time allocation
  - If ops > 50% for 2 consecutive weeks → escalate to engineering manager
  - If ops > 50% for 4 consecutive weeks → VP freezes feature work

TRACKING:
  - All tickets tagged #ops or #project
  - Automated dashboard refreshes each Monday
  - Visual: red/green gauge showing ops vs project ratio
```

### Scenario: Ops Spiral

```
TEAM:     Payments SRE (4 engineers)
PERIOD:   January 2024

WEEK 1:   Ops 55% / Project 45%  ← borderline
WEEK 2:   Ops 62% / Project 38%  ← escalated to EM
WEEK 3:   Ops 68% / Project 32%  ← VP notified
WEEK 4:   Ops 71% / Project 29%  ← FEATURE FREEZE INITIATED

ROOT CAUSE ANALYSIS:
  - 3 out of 4 engineers were doing manual DB migrations weekly
  - Flaky payment provider integration required constant hand-holding
  - No automated capacity planning → manual scaling 2x/day

REMEDIATION (2-week freeze):
  - Built Flyway migration pipeline → eliminated manual migrations
  - Implemented circuit breaker + retry logic for payment provider
  - Deployed KEDA autoscaling based on queue depth

POST FREEZE (Week 7):  Ops 32% / Project 68%  ✓
```

---

## Worst Practices

### 1. Alerting on Everything

```
Symptom:  350 alerts/day. Engineers mute Slack. PagerDuty becomes noise.
Reality:  "When everything is an emergency, nothing is."

BAD:
  alert: HighCPU
  expr: cpu_usage > 50%      # pages for normal spikes
  for: 1m                      # pages for transient spikes
  severity: critical            # wakes people up

GOOD:
  alert: HighCPU
  expr: cpu_usage > 90%
  for: 15m                      # sustained high CPU is a real issue
  severity: warning              # don't wake people for this
  runbook_url: http://wiki.internal/high-cpu-runbook
```

### 2. Deploying on Friday

```
Friday 5 PM deploy → breaks at 5:30 PM
  → Primary on-call leaves at 6 PM for dinner
    → Secondary on-call is at a movie
      → Incident unresolved until 9 PM
        → 3.5 hours of downtime that could have been 15 minutes

RULE: No deploys after Thursday noon. Exceptions require VP + EM approval.
```

### 3. 100% Reliability Target

```
100% availability is:
  - Mathematically impossible (humans make mistakes, hardware fails)
  - Financially insane (the gap from 99.99% → 99.999% → 99.9999% grows exponentially)
  - Velocity-killing (every change is "too risky")
  - Misleading (users don't notice the difference between 99.9% and 99.99% for most apps)

Cost of additional "9":
  99.9%   → $10K/month    (basic HA)
  99.99%  → $100K/month   (multi-AZ, automated failover)
  99.999% → $1M+/month    (multi-region active-active, chaos engineering)
  99.9999% → $10M+/month  (dedicated hardware, formally verified software)

The right question: "What reliability do users ACTUALLY need?"
If users can tolerate a 5-second retry, 99% is fine.
```

### 4. Manual Runbooks

```text
Runbook: "Restart Service X"

Manual version (BAD):
  1. SSH into box-01
  2. sudo systemctl restart service-x
  3. tail -f /var/log/service-x.log
  4. Wait for "ready" message

Problems:
  - Which box? (production has 12 instances)
  - What if "ready" doesn't appear?
  - What if SSH is broken?
  - What if you're on vacation and the new hire tries this?

Automated version (GOOD):
  kubectl rollout restart deployment/service-x -n production
  kubectl rollout status deployment/service-x -n production --timeout=5m

If that fails:
  - Runbook links to escalation procedure
  - Runbook links to relevant dashboards
  - Runbook contains last-known-good config
```

---

## Real-World Scenarios

### Scenario 1: The Over-Alerted Team

```
A team of 8 engineers receives 900 alerts/day across 5 services.
Result: They haven't acknowledged a real P0 in 6 months because
they've been conditioned to ignore all alerts.

Fix:
  1. Classify all alerts: critical / warning / info
  2. Delete all "info" alerts (they go to dashboard, not pager)
  3. Only critical alerts wake people up
  4. Result: 12 alerts/day, all actionable, 100% acknowledgment rate
```

### Scenario 2: The Friday Deploy Disaster

```
A new feature deploys at 4:30 PM Friday. Database migration fails
at row 3.2M, leaving schema in a corrupted state. No DBA on call.
Service down for 18 hours over the weekend.

Post-mortem action items:
  1. Deployment moratorium: Thu 12:00 PM - Mon 9:00 AM
  2. All migrations must be tested against prod-sized dataset
  3. DBA on-call rotation created
  4. Rollback procedure added to every migration PR template
```

### Scenario 3: The Error Budget Wake-Up Call

```
Product manager: "Why are all my feature deploys blocked?"
SRE lead: "Because the error budget hit zero at 3 AM this morning."
PM: "So?"
SRE lead: "So we've had 52 minutes of downtime this month. Our target
          was 43 minutes. Users are noticing. Until we fix the root
          causes, every feature we ship might make it worse."
PM: "How long until we can deploy again?"
SRE lead: "We need to go 14 days without another incident to rebuild
          half the budget. Realistically: 3 weeks."
PM: "Okay. What do you need from my team?"
SRE lead: "Two backend engineers for the reliability sprint. And a
          commitment that the next 3 sprints include reliability
          tickets equal to 20% of capacity."

This is the error budget working as designed.
```
