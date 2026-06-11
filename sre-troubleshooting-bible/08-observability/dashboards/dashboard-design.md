# Dashboard Design
> **Category:** Observability | Dashboards | Grafana
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#observability` `#dashboards` `#grafana` `#oncall`

---

## Alert Fatigue — The Silent Killer

Alert fatigue is when engineers stop paying attention to alerts because there are too many. It's the #1 cause of missed incidents.

### How Organizations Get Here

```
Week 1:  "We should alert when CPU > 80%."          [1 alert]
Week 5:  "Let's add an alert for memory > 80%."      [2 alerts]
Week 10: "Add alert for p99 > 500ms."                 [3 alerts]
...
Week 30: 47 alerts. Engineer's phone buzzes 80 times/day.
          All alerts reflexively snoozed. None are read.
```

### The Cure: Only Alert on SLO-Threatening Conditions

Ask this question before creating ANY alert: **"If this condition persists, will it cause an SLO violation within the next 24 hours?"**

If the answer is NO → do not page. Make it a dashboard panel instead.

| Instead of alerting on... | Put it on a dashboard as... |
|---|---|
| CPU > 80% | CPU utilization line chart with 80% reference line |
| Memory > 80% | Memory gauge with color thresholds |
| Disk usage increased 10% | Disk usage rate-of-change chart |
| p99 latency > 500ms | Latency percentiles line chart |

### Error Budget Burn Rate Alerts

The SRE book's solution to alert fatigue: alert on **how fast** you're burning error budget, not on absolute thresholds.

```
Error budget burn rate = (error rate / allowed error rate)

Burn Rate  | Time to Exhaust  | Alert Severity  | Response Time
-----------|------------------|-----------------|----------------
x1         | 30 days          | No alert        | N/A (you're fine)
x2         | 15 days          | Ticket           | 8 business hours
x5         | 6 days           | Ticket           | 2 business hours
x10        | 3 days           | Page             | 30 minutes
x14.4      | 1 hour           | Page (critical)  | 5 minutes
x100       | 8.6 minutes      | Page (emergency) | Immediate
```

**Why this works**: A 1% error rate on a service with 99.9% SLO (allows 0.1% errors) = x10 burn rate = page. But 1% error rate on a service with 99% SLO (allows 1% errors) = x1 = no alert. Same metric, different urgency based on SLO context.

---

## Dashboard Layout — The Golden Pattern

Three tiers. Each tier answers different questions at different zoom levels.

### Tier 1: Overview Dashboard (Single Screen — All Services)

**Purpose**: "Is anything on fire?" — Answered in 5 seconds or less.

```
┌──────────────────────────────────────────────────────────────────────┐
│ SERVICE HEALTH — PRODUCTION                                    [UTC] │
├──────────────┬──────────────┬──────────────┬──────────────┐
│ ● API Gateway│ ● Auth Svc   │ ● Order Svc  │ ● Payment    │
│ ● GREEN      │ ● GREEN      │ ● YELLOW     │ ● GREEN      │
│ 50ms / 0.01% │ 12ms / 0.00% │ 800ms / 1.2% │ 30ms / 0.05% │
├──────────────┴──────────────┴──────────────┴──────────────┤
│                                                              │
│ GLOBAL ERROR RATE    GLOBAL P99 LATENCY    OPEN ALERTS      │
│ ████████ 0.08%       ████████ 450ms        ⚠ 1 (Order Svc) │
│                                                              │
│ RECENT DEPLOYMENTS                    ERROR BUDGET STATUS   │
│ order-svc v2.4.1 (10:05)             order-svc: ████████ 12%│
│ payment-svc v1.9.0 (09:45)           auth-svc:  ████████ 98%│
│                                       payment:   ████████ 85%│
└──────────────────────────────────────────────────────────────┘
```

### Tier 2: Service Dashboard (Single Service Deep Dive)

**Purpose**: "What's wrong with Order Service?" — Clicked from Tier 1 yellow/red indicator.

Must contain:
1. **Four Golden Signals** — latency, traffic, errors, saturation (2x2 grid)
2. **SLO Dashboard** — error budget gauge, burn rate chart
3. **Dependency Health** — downstream services (DB, cache, queue, external APIs)
4. **Deployment Markers** — vertical lines on time-series charts showing when deploys happened
5. **Exemplars** — data points on latency charts that link to traces in Jaeger/Tempo

### Tier 3: Instance Dashboard (Single Pod/Host)

**Purpose**: "Why is pod order-svc-7f8d9 killing itself?" — Drilled from Tier 2 anomaly.

Must contain:
1. **Resource usage**: CPU, memory, disk, network per container
2. **Application metrics**: thread pool, connection pool, GC stats, heap usage
3. **Process-level**: open file descriptors, goroutines (Go), event loop lag (Node), JVM heap (Java)
4. **OOM killer events**: `kube_pod_container_status_terminated_reason{reason="OOMKilled"}`

---

## SLO Dashboard Design

The single most important dashboard for an SRE. Everything else supports this.

### Panel 1: Error Budget Remaining (Big Gauge)

```promql
# Error budget remaining (%)
100 * (1 - (
  sum(rate(http_requests_total{service="$service", status=~"5.."}[28d])) by (service)
  /
  sum(rate(http_requests_total{service="$service"}[28d])) by (service)
  /
  (1 - 0.999)  # 99.9% SLO = 0.1% allowed error rate
))
```

Grafana visualization:
```
Single Stat / Gauge
  Colors:
    Green: 100-50%   ("Healthy — normal operations")
    Orange: 50-10%   ("Warning — slow down feature deploys")
    Red: 10-0%       ("Critical — freeze deploys, focus on reliability")
```

### Panel 2: Error Budget Burn Rate (Time Series)

```promql
# Burn rate over 1-hour window
(
  sum(rate(http_requests_total{service="$service", status=~"5.."}[1h])) by (service)
  /
  sum(rate(http_requests_total{service="$service"}[1h])) by (service)
)
/
(1 - 0.999)  # Divide by allowed error rate to get burn rate multiplier
```

Add threshold lines at x1, x5, x10:
```
Grafana Thresholds:
  x1.0  → solid green line  (budget is safe)
  x5.0  → dashed yellow     (2 business hours — ticket)
  x10.0 → dashed orange     (30 min — page)
  x14.4 → solid red line    (5 min — critical page)
```

---

## Grafana Pro Tips

### Variable Templating

```promql
# Grafana dashboard variable definitions
# Name: service | Type: Query
label_values(http_requests_total, service)

# Name: endpoint | Type: Query
label_values(http_requests_total{service="$service"}, endpoint)

# Name: instance | Type: Query
label_values(http_requests_total{service="$service"}, instance)
```

Now every panel can use `$service`, `$endpoint`, `$instance` — same dashboard works for every service.

### Exemplars (Trace-to-Metric Linking)

Enable exemplars in Prometheus:
```yaml
# prometheus.yml
global:
  external_labels:
    cluster: prod-us-east-1
  exemplar_storage:
    max_exemplars: 100000  # Store up to 100K exemplars
```

In your application:
```python
# prometheus_client with exemplar support
histogram.observe(duration, exemplar={'trace_id': span_context.trace_id})
```

In Grafana: any spike on a latency graph has a clickable dot → opens Jaeger trace. This is the holy grail of observability: metric → trace → log in 2 clicks.

### Alert Annotations

```bash
# Query Prometheus alerts as Grafana annotations
# Data source: Prometheus
# Query: ALERTS{severity="page"}
# Step: 60s
# Tags: alertname, severity
```

Now every time an alert fires, a red vertical line appears on every dashboard. When investigating a latency spike, you instantly know: "Was this known? Was the oncall already paged?"

### Units — Always Set Them

Raw numbers on a metric panel are a bug. 0.051 seconds and 51347294 bytes mean nothing at 3 AM.

```
PromQL: http_request_duration_seconds → Grafana Unit: seconds (s) → Auto-formats to 51ms
PromQL: http_response_size_bytes      → Grafana Unit: bytes (dec) → Auto-formats to 51.3 MiB
PromQL: memory_usage_bytes / memory_total_bytes → Unit: percent (0.0-1.0) → 78.2%
```

---

## Real Scenario: 3 AM Dashboard Design Failure

```
Incident: Engineer opens dashboard at 3 AM. Dashboard has 47 panels.
Engineer looks at the same error rate graph they always look at.
Error rate line is within normal bounds (0.02%—normal is 0.01%).
Engineer says "looks fine" and goes back to sleep.

What engineer missed:
  - A tiny 1-pixel spike in error rate that happened 2 minutes prior
  - The spike was from a NEW, un-monitored endpoint (`/api/v2/payment-intent`)
  - The spike was 15% error rate for that one endpoint
  - It was drowning in the aggregate error rate (averaged across all endpoints)

Post-mortem finding: The endpoint was deployed 4 hours before the incident.
It was processing 0.1% of total traffic but generating 100% of errors.
Aggregate metrics hid the problem.

Fix:
  1. Added a "Top Errors by Endpoint" table (not graph) at the top of Tier 2 dashboard
  2. Shows: endpoint, error count (last 5 min), error rate %, trend arrow
  3. Sorted descending by error rate
  4. Red background if error rate > 5%

Template:
┌──────────────────────────────────────────────────┐
│ TOP ERRORS BY ENDPOINT (last 5 min)              │
├──────────────────────┬────────┬─────────┬────────┤
│ Endpoint             │ Errors │ Rate %  │ Trend  │
├──────────────────────┼────────┼─────────┼────────┤
│ /api/v2/payment-int  │   142  │ 14.8% ⬆│  🔴    │
│ /api/v1/orders       │    12  │  0.02% ➡│  🟢    │
│ /api/v1/cart         │     3  │  0.01% ➡│  🟢    │
└──────────────────────┴────────┴─────────┴────────┘

New 3 AM experience: Engineer opens dashboard. Sees bold red row. Clicks it.
Drills into endpoint-specific trace. Finds root cause. Fixes. Goes back to sleep. 12 min total.
```

---

## Dashboard Review Checklist

Every month, review every dashboard against these criteria:

- [ ] Can a new team member understand this dashboard in <60 seconds?
- [ ] Are all alerts represented as horizontal lines/thresholds?
- [ ] Are there links to the relevant runbooks? (Panel descriptions)
- [ ] Are units set on every panel?
- [ ] Is there a deployment marker annotation enabled?
- [ ] Would clicking on a data point take you to the trace? (Exemplars enabled)
- [ ] Is there at least one "Big Number" showing the most critical metric?
- [ ] Are the dashboards linked? (Tier 1 → Tier 2 → Tier 3 → logs)

---

*See also: [Metrics Guide](../metrics/metrics-guide.md) | [Structured Logging](../logging/structured-logging.md) | [Distributed Tracing](../tracing/distributed-tracing.md)*
