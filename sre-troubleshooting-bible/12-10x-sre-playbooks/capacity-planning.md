# Capacity Planning
> **Category:** 10x SRE | Capacity | Planning
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#capacity` `#planning` `#scaling` `#10x`

---

## Why Capacity Planning Matters

Capacity is the difference between "we had a great Black Friday" and "our CTO is on CNN explaining the outage." You cannot scale reactively when traffic doubles in 10 minutes. By the time your auto-scaler provisions new instances, users have already left.

```
Reactive scaling timeline (what most teams do):
  10:00 — Traffic spike begins (promotional email sent)
  10:03 — CPU crosses 80%
  10:05 — Auto-scaler detects metric
  10:08 — New instance requested (AWS EC2)
  10:12 — Instance provisioned and booting
  10:15 — Instance registered with load balancer
  10:17 — Health check passes, instance serving traffic

  That's 17 minutes of degraded service.
  Users don't wait 17 minutes. They refresh, see errors, and leave.

Proactive capacity planning timeline:
  September: Marketing says "Black Friday campaign launching Nov 24, expecting 3x traffic."
  October: Capacity model updated. 3x forecast × current 2000 RPS = 6000 RPS expected.
  November 1: Load test at 6000 RPS. Find DB bottleneck at 4500 RPS.
  November 10: Add read replicas. Indexes optimized. Retest at 8000 RPS — passes.
  November 23: Pre-warm instances. 150% of expected capacity running.
  November 24: Black Friday. Peak 6800 RPS. p95 = 120ms. Zero errors.
```

---

## Traffic Forecasting

### Seasonality Patterns

Every business has predictable traffic patterns. Your capacity plan must account for them.

```
E-commerce:
  Q1 (Jan-Mar): Post-holiday lull. Lowest traffic. Good time for migrations.
  Q2 (Apr-Jun): Steady. Mother's Day spike.
  Q3 (Jul-Sep): Back-to-school (Aug), steady otherwise.
  Q4 (Oct-Dec): BLACK FRIDAY. Cyber Monday. Holiday shopping. 3-5x normal traffic.

Fintech:
  Tax season (Jan-Apr): 2x traffic. Every user checks their tax documents.
  End of month: Payroll processing peaks.
  End of quarter: Reporting + reconciliation runs.
  End of year: Year-end statements + tax prep. 3x normal traffic.

Streaming:
  Evenings: 2x daily traffic (people watching after work).
  Weekends: 3x weekday traffic.
  Major events: Super Bowl, World Cup, new season launch = 10-50x spike.

Edtech:
  Back-to-school (Aug-Sep): 3x traffic.
  Finals week: 2x traffic.
  Summer: 0.3x traffic (dead zone — good for infra changes).
```

### Growth Projections

```
Linear growth (steady business):
  Month 1: 1000 RPS
  Month 6: 1200 RPS (+20%)
  Prediction: Month 12 → 1400 RPS
  Capacity plan: Add 20% headroom every 6 months.

Viral growth (startup, product launch):
  Month 1: 100 RPS
  Month 3: 5000 RPS (+4900% — viral moment)
  Prediction: Unknown. Must monitor daily.
  Capacity plan: 3x your expected peak. Auto-scale reacts, but you pre-warm capacity.

Event-driven spikes:
  Marketing campaign: +200% for 3 days.
  Press coverage: +500% for 2 hours.
  Capacity plan: Pre-warm instances before event. Scale down after.
```

---

## Resource Extrapolation

### Linear Scaling (CPU, Network, Throughput)

```
Given:
  Current traffic T1 = 2000 RPS
  Current resource R1 = 40 instances at 60% CPU average

Projected traffic T2 = 5000 RPS (2.5x increase)

Linear extrapolation:
  R2 = R1 × (T2 / T1)
  R2 = 40 × (5000 / 2000) = 100 instances

  CPU per instance at 5000 RPS:
    60% × (5000/2000) = 150% ← EXCEEDS 100%. Cannot scale linearly.

  Instances needed for < 70% CPU target:
    40 × (5000/2000) / 0.70 = 40 × 2.5 / 0.70 = 143 instances

Always add headroom target. "Need X instances" = "Need X instances AT <Y% utilization."
```

### Non-Linear Scaling (Databases, Connections, Memory)

Some resources do NOT scale linearly with RPS. You must understand the relationship.

```
Database connections (quadratic in worst case):
  Each request opens 2 DB connections (read + write).
  2000 RPS → 4000 connections/sec.
  Connection pool size = 200 per instance × 40 instances = 8000 max connections.
  DB max_connections = 5000.

  At 2000 RPS: 4000 of 5000 used (80%) — OK.
  At 5000 RPS: 10000 needed, 5000 max — BROKEN.

  Fix: Use PgBouncer (connection pooling in front of DB) to multiplex connections.
  After PgBouncer: 100 pools × 40 instances = 4000 connections. Same as before!

Memory (grows with unique users, not requests):
  Cache hit ratio depends on unique items, not request count.
  If cache holds 1M user profiles (1GB each) and you add 1M new users:
    Cache needed: 2GB → 4GB per instance (NOT linear with RPS).

  If a viral event brings 10x traffic from NEW users:
    Cache hit ratio drops from 95% to 40% (new users = cache misses).
    Each miss = 200ms DB query instead of 2ms cache hit.
    Effective latency: 0.95×2 + 0.05×200 = 11.9ms → 0.40×2 + 0.60×200 = 120.8ms.
    Same instance count, 10x latency increase. NOT a CPU problem — a cache problem.
```

---

## Headroom Calculation

### The Formula

```
Headroom = (max_capacity - current_peak) / max_capacity

If max_capacity = 10000 RPS and current_peak = 7000 RPS:
  Headroom = (10000 - 7000) / 10000 = 30%

Target: 50% headroom at steady state peak.
```

### The 50% Rule

```
Why 50%?
  40%: Good. Room for spikes and growth.
  30%: Warning. Can handle minor spikes. No room for an instance failure.
  20%: Dangerous. One AZ failure = you're over capacity.
  10%: Critical. Any traffic anomaly triggers degradation.
  0%: Incident. You're already at capacity.
```

### Real Scenario: Headroom Math

```
Current:
  40 EC2 instances (m5.xlarge)
  Peak CPU = 70% (aggregated across cluster)
  Max capacity per instance = 100% CPU (but 85% is practical max)
  Headroom = (85% - 70%) / 85% = 17.6% ← BAD (below 50%)

Growth projection: 50% traffic growth expected over next quarter.

Calculate needed instances:
  New CPU per instance with current config:
    70% × 1.5 = 105% ← Exceeds practical max (85%).

  Instances needed for 85% max at 1.5x traffic:
    40 × (105% / 85%) = 40 × 1.235 = 49.4 → 50 instances at current traffic × 1.5.

  BUT we want 50% HEADROOM (CPU < 42.5% after growth):
    40 × (105% / 42.5%) = 40 × 2.47 = 98.8 → 99 instances.

  Realistic option:
    Add 25 instances now (65 total) for 50% headroom at CURRENT traffic.
    Monitor growth monthly. Adjust quarterly.

  OR:
    Right-size to m5.2xlarge (2x CPU). Each instance handles 2x RPS.
    20 instances at m5.2xlarge = same capacity as 40 m5.xlarge.
    Add 12 more for growth headroom = 32 instances total.
    Saves money too (less overhead per instance).
```

---

## Load Testing to Find Per-Instance Limits

You can't capacity plan without knowing the max throughput per instance.

### Discover Per-Instance Max

```
1. Deploy a single instance of your service in a test environment.
2. Route traffic ONLY to that instance (remove it from the LB pool, test directly).
3. Run k6/Locust against it with increasing RPS:
   Start at 10 RPS → 50 RPS → 100 RPS → 200 RPS → ...
   Wait 3 minutes at each level. Record latency percentiles.
4. Find the point where p95 crosses SLO (e.g., 200ms).
5. That's max_per_instance.

Example:
  RPS,  p50,  p95,  p99,  Error%
  100:  5ms, 12ms, 25ms,  0.00%
  300:  8ms, 25ms, 50ms,  0.00%
  500:  15ms, 45ms, 90ms, 0.02%
  700:  30ms, 85ms, 180ms, 0.10%  ← SLO bound (p95 < 200ms, OK)
  900:  60ms, 230ms, 500ms, 0.50% ← SLO VIOLATED (p95 > 200ms)

  max_per_instance = 700 RPS (safe maximum)

Total needed capacity:
  Expected total RPS = 10000
  Instances_needed = 10000 / 700 = 14.3 → 15 instances at 100% load.

  With 50% headroom:
  Instances_needed = 10000 / (700 × 0.5) = 10000 / 350 = 28.6 → 29 instances.
```

### The Saturation Cascade

Services don't fail one at a time — they cascade. Capacity planning must consider the whole system.

```
Service A (API Gateway):
  Max: 800 RPS per instance. Needs 20 instances for 16000 RPS.

Service B (Order Service):
  Max: 400 RPS per instance. Called by EVERY request through Service A.
  At 16000 RPS through Gateway = 16000 RPS to Order Service.
  Needs 16000 / 400 = 40 instances.

Service C (Payment Service):
  Max: 200 RPS per instance. Called by 40% of Order Service requests.
  At 16000 RPS × 0.4 = 6400 RPS.
  Needs 6400 / 200 = 32 instances.

Service D (PostgreSQL):
  Max: 5000 RPS of queries. Each OrderService request = 3 queries.
  16000 × 3 = 48000 queries/sec → 9.6x over capacity. BROKEN.
  Even with read replicas: 48000 / 5 instances = 9600 each → each instance overloaded.

  THIS is the real bottleneck. Adding 1000 app instances cannot help if the DB
  can only handle 5000 queries/sec. Capacity planning without DB planning is
  fantasy.
```

---

## Database Capacity Planning

### RDS / Aurora IOPS Planning

```
AWS gp3 storage:
  Baseline: 3000 IOPS (free)
  Provisioned: 500 IOPS per 0.01 cents/month above baseline
  Throughput: 125 MB/s baseline

Capacity forecast:
  Current IOPS peak: 4000 (including read replicas)
  Growth: +20% per year
  Next year peak: 4000 × 1.20 = 4800 IOPS

  Need: 4800 IOPS. Baseline gives 3000. Provision 1800 IOPS.
  Cost: 1800 × $0.01/IOPS-month = $18/month for IOPS.

  Read replicas: Each replica gets its own IOPS allocation.
  2 replicas = 3× read capacity (primary + 2 replicas).
  But: replication lag adds latency risk.

Aurora:
  IOPS charged per million I/Os, not provisioned.
  Better for variable workloads (burst-capable).
  Read replicas share the same storage volume (no replication overhead).

When to add a read replica:
  Primary CPU > 70% consistently.
  Read throughput exceeds 70% of primary capacity.
  Read latency p95 > 10ms (and queries are indexed).
```

### Connection Pool Sizing

```
HikariCP formula:
  pool_size = Tn × (Cm - 1) + 1

  Tn = maximum number of threads
  Cm = maximum number of simultaneous connections held by a single thread
       (usually 1 for simple web apps)

For 200 Tomcat threads, 1 connection per thread:
  pool_size = 200 × (1 - 1) + 1 = 1? ← WRONG for concurrent apps.

Better formula (empirical):
  pool_size = ((core_count × 2) + effective_spindle_count)

  core_count = CPUs available to the app
  effective_spindle_count = number of disks serving the DB (SSD = 1)

For 4-core app server, SSD:
  pool_size = (4 × 2) + 1 = 9

  BUT: this is for optimal connection utilization, not maximum concurrency.

Production formula (realistic):
  max_active = peak_qps × (avg_query_time_ms / 1000) / num_instances × safety_factor

  Example: 2000 QPS, 50ms avg query, 10 instances, 2x safety factor:
  max_active = 2000 × (50/1000) / 10 × 2 = 2000 × 0.05 / 10 × 2 = 20

  Set pool size to 20 + idle (5) = 25.
  Monitor hikaricp_connections_pending. If > 0 → increase pool size.
```

---

## Cost Modeling

Capacity planning IS cost planning. Every instance decision has a dollar value.

```
Compute cost model:
  40 m5.xlarge instances × $0.192/hour × 730 hours/month = $5,606.40/month

  At 50% growth (60 instances):
  60 × $0.192 × 730 = $8,409.60/month
  +$2,803.20/month for growth.

  Savings opportunities:
  - Reserved Instances (1-year): 30% discount → $5,886.72/month for 60 instances
  - Spot Instances: 70% discount but can be reclaimed → good for stateless workers

  Right-sizing:
  Switch to m5.large if CPU < 50%: $0.096/hour → $4,204.80/month for 60 instances
  Or: Graviton (m6g.xlarge): $0.154/hour → 20% cheaper, same performance

DB cost model:
  1 db.r5.2xlarge (8 vCPU, 64GB RAM): $0.96/hour × 730 = $700.80/month
  1 read replica: $700.80/month
  Storage: 500GB gp3 × $0.08/GB = $40/month
  IOPS provisioned: 2000 × $0.01 = $20/month

  Total DB: $700.80 × 2 + $40 + $20 = $1,461.60/month

  At 50% growth:
  Need 1 more read replica for read capacity: +$700.80/month
  Need more storage (500GB → 800GB): +$24/month
  Total DB: $2,186.40/month
```

---

## Annual Capacity Planning Calendar

```
Q1 (Jan-Mar):
  - Review Q4 performance. Did we meet SLOs?
  - Analyze Black Friday data. Were our predictions accurate?
  - Begin Q4 forecasts. What's the growth trend?

Q2 (Apr-Jun):
  - Mid-year capacity review. Are we on track?
  - Order hardware / commit to reserved instances for Q4.
  - Run load tests with updated forecasts.

Q3 (Jul-Sep):
  - Pre-Black Friday load test. Find and fix bottlenecks.
  - Pre-warm infrastructure for Q4.
  - Final capacity review. Any surprises from product roadmap?

Q4 (Oct-Dec):
  - BLACK FRIDAY. Execute plan. Monitor. Adapt.
  - DO NOT make infrastructure changes in December.
  - Post-Q4 review. What did we learn?
```

---

## Quick Reference: Capacity Planning Formulas

```
Linear resource need:    R2 = R1 × (T2/T1)
Required headroom:       Instances = demand / (max_per_instance × target_utilization)
DB pool size:             pool = (peak_qps × avg_query_time_sec / instances) × 2
IOPS provision:           IOPS_needed = forecast_iops - baseline_iops (gp3: 3000)
Storage forecast:         Size_next_yr = size_now × (1 + growth_rate)^time_periods
Cost per request:         $/req = total_monthly_cost / (RPS × 86400 × 30)
```

---

*See also: [10x Mindset](10x-mindset.md) | [Chaos Engineering](chaos-engineering.md) | [Incident Command](incident-command.md)*
