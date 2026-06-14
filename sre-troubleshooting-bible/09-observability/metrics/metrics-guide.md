# Metrics Guide
> **Category:** Observability | Metrics | Prometheus
> **Difficulty:** Intermediate to Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#observability` `#metrics` `#prometheus` `#oncall`

---

## The Four Golden Signals (Google SRE Book)

Every service must expose these four signals. No exceptions. If you can only monitor four things, monitor these.

### 1. Latency

Time to serve a request. **Always track percentiles, never averages.**

| Percentile | Meaning | Action |
|------------|---------|--------|
| p50 | Half of users are faster than this | Baseline: "what does normal look like?" |
| p95 | 95% of users are faster than this | SLO target: "what do we promise users?" |
| p99 | 99% of users are faster than this | Worst-case: "how bad does it get?" |
| p999 | 99.9% of users are faster | Tail-of-tail: "edge case detector" |

**Critical rule**: separate successful requests from error responses. A 500 error returning in 2ms will drag your p99 DOWN and mask real problems. Query successful and error latencies separately:

```promql
# Latency of successful requests (p99)
histogram_quantile(0.99,
  sum(rate(http_request_duration_seconds_bucket{status=~"2..|3.."}[5m])) by (le, service))

# Latency of failed requests (p99) — often much faster due to early termination
histogram_quantile(0.99,
  sum(rate(http_request_duration_seconds_bucket{status=~"5.."}[5m])) by (le, service))
```

### 2. Traffic

Demand on your system. Measures throughput at every layer.

```
Layer          | Metric                            | Unit
---------------|-----------------------------------|-----------------
HTTP           | http_requests_total               | requests/sec
Database       | mysql_queries_total               | queries/sec
Message Queue  | kafka_messages_in_total            | messages/sec
gRPC           | grpc_server_handled_total          | calls/sec
```

### 3. Errors

Failed requests. Separate **client errors** (4xx — their fault) from **server errors** (5xx — your fault).

```promql
# Error rate percentage
sum(rate(http_requests_total{status=~"5.."}[5m])) by (service)
  /
sum(rate(http_requests_total[5m])) by (service)
  * 100
```

Watch for: error rate floor (it's never zero — network glitches happen). A healthy service runs <0.1% error rate.

### 4. Saturation

How "full" your system is. The most predictive signal — saturation rises BEFORE latency spikes.

```
Resource         | Metric                    | Danger Zone
-----------------|---------------------------|-----------------
CPU              | cpu_utilization_percent   | >80%
Memory           | memory_used_percent       | >85%
Disk             | disk_used_percent         | >85%
Disk I/O         | disk_await_seconds        | >0.01 (10ms)
Network          | network_bytes_total       | >70% of link speed
Queue Depth      | queue_length              | >capacity * 0.5
Thread Pool      | executor_pool_active_percent | >80%
DB Connections   | hikaricp_connections_active_percent | >80%
```

---

## Metric Types

### Counter
Only increases (or resets to 0 on restart). **NEVER use a counter for a current value.**

```
Use cases:   request_count_total, bytes_sent_total, errors_total
Bad use:     current_users_total (use a Gauge!)
Why:         rate() and increase() require counters. Gauges with rate() give wrong answers.
```

Prometheus client guarantees that `rate()` handles counter resets correctly by detecting and ignoring them.

### Gauge
Goes up and down. Reports current state.

```
Use cases:   memory_usage_bytes, queue_depth, in_flight_requests
Good:        cpu_temperature_celsius, thread_pool_active, connection_pool_size
```

### Histogram
Observations bucketed into configurable ranges. **The default choice for latency.**

```python
# Bucket definition (think about your SLO when choosing buckets)
buckets = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
#          ^---- SLO target: 500ms ----^       ^--- catch slow outliers ---^
```

Bad buckets = useless quantiles. If your SLO is 500ms and your smallest bucket is 1s, you can NEVER calculate your SLO compliance.

### Summary
Client-side quantile calculation. **Not aggregatable across instances.** Use only when server-side histogram aggregation is impossible (e.g., edge devices reporting directly to users).

---

## Histogram vs Summary: Decision Matrix

| Property | Histogram | Summary |
|----------|-----------|---------|
| Aggregatable across instances | Yes | No |
| Quantile precision | Configurable via buckets | Exact (no bucketing error) |
| CPU cost | Low (fixed buckets) | Higher (sorted list per instance) |
| Memory cost | Fixed (bucket count) | Grows with observations |
| Use in alerts | Yes | No (not aggregatable) |
| Recommendation | **Default choice** | Special cases only |

**Verbatim rule**: If you're not sure, use Histogram. Summary is almost never the right answer in a multi-instance microservices environment.

---

## PromQL Cookbook

### Rate and Increase

```promql
# Per-second rate over 5-minute window (handles counter resets)
rate(http_requests_total[5m])

# Instant rate (higher granularity, spikes like crazy — DO NOT alert on this)
irate(http_requests_total[5m])

# Total increase over window (useful for billing, not for rate analysis)
increase(http_requests_total[5m])
```

**Why `rate` and not `irate` for alerting?** `irate` looks at the last two data points only. A single dropped scrape interval produces a massive spike that triggers flapping alerts. `rate` smooths over the window and ignores single-point anomalies.

### Histogram Queries

```promql
# p99 latency per service
histogram_quantile(0.99,
  sum(rate(http_request_duration_seconds_bucket[5m])) by (le, service))

# p50/p95/p99 in one dashboard panel (Grafana table transform)
histogram_quantile(0.50, sum(rate(http_request_duration_seconds_bucket[5m])) by (le, service))
histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[5m])) by (le, service))
histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket[5m])) by (le, service))

# SLO compliance: percentage of requests under 500ms
sum(rate(http_request_duration_seconds_bucket{le="0.5"}[5m])) by (service)
  /
sum(rate(http_request_duration_seconds_count[5m])) by (service)
```

### Working with Labels

```promql
# Filter by label
rate(http_requests_total{service="payment", status="500"}[5m])

# Aggregate away a label
sum(rate(http_requests_total[5m])) without (instance, pod)

# Top 5 endpoints by request rate
topk(5, rate(http_requests_total[5m]))

# Exclude health check noise
rate(http_requests_total{path!~"/health|/metrics|/ready"}[5m])
```

### Alerting Rules

```yaml
# rules.yml — alerting rules
groups:
- name: slo-alerts
  rules:
  - alert: HighErrorRate
    expr: |
      sum(rate(http_requests_total{status=~"5.."}[5m])) by (service)
      /
      sum(rate(http_requests_total[5m])) by (service) > 0.01
    for: 5m   # Alert must persist for 5 min before firing (avoids flapping)
    labels:
      severity: page
      team: backend
    annotations:
      summary: "{{ $labels.service }} error rate > 1%"
      description: "Error rate is {{ $value | humanizePercentage }} for the last 5 minutes."
      runbook: "https://runbooks.example.com/{{ $labels.service }}/high-error-rate"

  - alert: HighLatency
    expr: |
      histogram_quantile(0.99,
        sum(rate(http_request_duration_seconds_bucket[5m])) by (le, service)) > 1.0
    for: 5m
    labels:
      severity: page
    annotations:
      summary: "{{ $labels.service }} p99 latency > 1s"
      description: "p99 is {{ $value }}s. Check exemplars in Grafana."
```

**The `for` clause is non-negotiable for paging alerts.** Without it, a 30-second spike wakes up an oncall engineer for nothing. Minimum 5 minutes for all paging alerts.

### Recording Rules

Pre-compute expensive queries so dashboards load instantly and alerting rules are fast:

```yaml
# rules.yml — recording rules
groups:
- name: precomputed-rates
  interval: 30s
  rules:
  - record: job:http_requests_total:rate5m
    expr: rate(http_requests_total[5m])

  - record: job:http_request_duration_seconds_p99:5m
    expr: |
      histogram_quantile(0.99,
        sum(rate(http_request_duration_seconds_bucket[5m])) by (le, service))
```

Now dashboards query `job:http_requests_total:rate5m` instead of computing rates repeatedly.

---

## Real Scenario: Straggler Detection

```
Alert: p99 latency > 1s for checkout-service

Step 1 — Confirm the alert:
  histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket
    {service="checkout"}[5m])) by (le))
  Result: 1.5s ✓ (alert is legitimate)

Step 2 — Check the median:
  histogram_quantile(0.50, sum(rate(http_request_duration_seconds_bucket
    {service="checkout"}[5m])) by (le))
  Result: 50ms

Step 3 — Diagnosis:
  p50 = 50ms, p99 = 1500ms → 30x difference.
  This is NOT a system-wide slowdown. ~1% of requests are "stragglers."
  System-wide slowdowns show p50 increasing proportionally.

Step 4 — Find the stragglers:
  histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket
    {service="checkout"}[5m])) by (le, endpoint))
  Result:
    GET /checkout/cart     → 40ms
    POST /checkout/pay     → 35ms
    GET /checkout/invoice  → 3200ms ← HERE!

Step 5 — Check downstream dependency for /checkout/invoice:
  histogram_quantile(0.99, sum(rate(http_client_request_duration_seconds_bucket
    {service="checkout"}[5m])) by (le, target))
  Result:
    target="invoice-service" → 3100ms of 3200ms spent waiting for invoice-service.

Step 6 — Jump to invoice-service dashboard.
  Found: invoice-service doing full table scan on invoices table (grew 10x in last month).
  Fix: add index on (user_id, created_at). Latency back to 50ms.
```

---

## Language-Specific Instrumentation

### Python: prometheus_client (Flask App)

```python
import time
import random
from flask import Flask, request, jsonify
from prometheus_client import (
    Counter, Histogram, Gauge, generate_latest,
    CollectorRegistry, multiprocess
)
from prometheus_client import make_wsgi_app
from werkzeug.middleware.dispatcher import DispatcherMiddleware

app = Flask(__name__)

# --- Metric Definitions ---
REQUEST_COUNT = Counter(
    'http_requests_total',
    'Total HTTP requests',
    ['method', 'endpoint', 'status']
)

REQUEST_LATENCY = Histogram(
    'http_request_duration_seconds',
    'HTTP request latency',
    ['method', 'endpoint'],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)

IN_FLIGHT = Gauge(
    'http_requests_in_flight',
    'Currently in-flight requests',
    ['method']
)

DB_QUERY_LATENCY = Histogram(
    'db_query_duration_seconds',
    'Database query latency',
    ['query_type'],
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0]
)

CART_ITEM_COUNT = Gauge(
    'checkout_cart_items_total',
    'Number of items in active carts'
)

# --- Middleware ---
@app.before_request
def before_request():
    request.start_time = time.time()
    IN_FLIGHT.labels(method=request.method).inc()

@app.after_request
def after_request(response):
    duration = time.time() - request.start_time
    REQUEST_COUNT.labels(
        method=request.method,
        endpoint=request.path,
        status=response.status_code
    ).inc()
    REQUEST_LATENCY.labels(
        method=request.method,
        endpoint=request.path
    ).observe(duration)
    IN_FLIGHT.labels(method=request.method).dec()
    return response

# --- Business Logic ---
@app.route('/checkout', methods=['POST'])
def checkout():
    # Simulate DB query
    with DB_QUERY_LATENCY.labels(query_type='select_cart').time():
        time.sleep(random.uniform(0.01, 0.05))

    # Update gauge with actual value
    cart_items = random.randint(1, 20)
    CART_ITEM_COUNT.set(cart_items)

    # Simulate payment processing
    with DB_QUERY_LATENCY.labels(query_type='insert_order').time():
        time.sleep(random.uniform(0.02, 0.1))

    # Simulate occasional slow response (stragglers!)
    if random.random() < 0.01:  # 1% of requests
        time.sleep(random.uniform(1.0, 3.0))

    return jsonify({"status": "ok", "order_id": random.randint(10000, 99999)})

@app.route('/health')
def health():
    return jsonify({"status": "healthy"})

# Expose metrics on /metrics endpoint
app.wsgi_app = DispatcherMiddleware(app.wsgi_app, {
    '/metrics': make_wsgi_app()
})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
```

### Java: Micrometer + Prometheus (Spring Boot)

```java
// build.gradle / pom.xml dependencies:
// - micrometer-registry-prometheus
// - spring-boot-starter-actuator
// - spring-boot-starter-aop (for @Timed)

// --- Application Config (application.yml) ---
// management:
//   endpoints:
//     web:
//       exposure:
//         include: health,metrics,prometheus
//   metrics:
//     distribution:
//       percentiles-histogram:
//         http.server.requests: true  # Enable histogram buckets for HTTP
//       slo:
//         http.server.requests: 10ms,50ms,100ms,250ms,500ms,1s,2s,5s

import io.micrometer.core.annotation.Timed;
import io.micrometer.core.annotation.Counted;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import org.springframework.web.bind.annotation.*;
import org.springframework.stereotype.Service;
import org.springframework.beans.factory.annotation.Autowired;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.TimeUnit;

@RestController
@RequestMapping("/api")
public class CheckoutController {

    private final CheckoutService checkoutService;
    private final MeterRegistry meterRegistry;

    @Autowired
    public CheckoutController(CheckoutService checkoutService, MeterRegistry meterRegistry) {
        this.checkoutService = checkoutService;
        this.meterRegistry = meterRegistry;
    }

    @PostMapping("/checkout")
    @Timed(value = "checkout.duration", description = "Checkout operation latency",
           histogram = true, percentiles = {0.5, 0.95, 0.99})
    @Counted(value = "checkout.requests", description = "Checkout request count")
    public CheckoutResponse checkout(@RequestBody CheckoutRequest request) {
        // Track cart size as gauge
        meterRegistry.gauge("checkout.cart.size",
            request.getItems(), items -> (double) items.size());

        return checkoutService.processCheckout(request);
    }
}

@Service
class CheckoutService {

    private final MeterRegistry meterRegistry;

    public CheckoutService(MeterRegistry meterRegistry) {
        this.meterRegistry = meterRegistry;
    }

    public CheckoutResponse processCheckout(CheckoutRequest request) {
        Timer.Sample sample = Timer.start(meterRegistry);

        try {
            // Simulate DB query timing
            Timer dbTimer = meterRegistry.timer("db.query.latency", "query", "select_cart");
            dbTimer.record(() -> {
                try {
                    Thread.sleep(ThreadLocalRandom.current().nextLong(10, 50));
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                }
            });

            // Simulate payment
            Timer payTimer = meterRegistry.timer("external.payment.latency", "provider", "stripe");
            payTimer.record(() -> {
                try {
                    Thread.sleep(ThreadLocalRandom.current().nextLong(20, 100));
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                }
            });

            // Simulate stragglers
            if (ThreadLocalRandom.current().nextDouble() < 0.01) {
                meterRegistry.counter("checkout.straggler.detected").increment();
                try {
                    Thread.sleep(ThreadLocalRandom.current().nextLong(1000, 3000));
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                }
            }

            return new CheckoutResponse("ok", ThreadLocalRandom.current().nextLong(10000, 99999));

        } finally {
            sample.stop(meterRegistry.timer("checkout.total.duration"));
        }
    }
}

// --- Custom metrics definition using @Configuration ---

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.Gauge;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
class MetricsConfig {

    @Bean
    public Counter checkoutErrors(MeterRegistry registry) {
        return Counter.builder("checkout.errors.total")
            .description("Checkout processing errors")
            .tag("service", "checkout")
            .register(registry);
    }
}
```

### JavaScript/Node.js: prom-client (Express App)

```javascript
// package.json dependencies:
// "prom-client": "^15.1.0",
// "express": "^4.18.0"

const express = require('express');
const client = require('prom-client');

const app = express();

// --- Create a Registry ---
const register = new client.Registry();
client.collectDefaultMetrics({
    register,
    prefix: 'app_',
    gcDurationBuckets: [0.001, 0.01, 0.1, 1, 2, 5],
});

// --- Custom Metrics ---
const httpRequestDurationMicroseconds = new client.Histogram({
    name: 'http_request_duration_seconds',
    help: 'Duration of HTTP requests in seconds',
    labelNames: ['method', 'route', 'status_code'],
    buckets: [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
    registers: [register],
});

const httpRequestCounter = new client.Counter({
    name: 'http_requests_total',
    help: 'Total number of HTTP requests',
    labelNames: ['method', 'route', 'status_code'],
    registers: [register],
});

const activeConnections = new client.Gauge({
    name: 'http_connections_active',
    help: 'Number of active HTTP connections',
    registers: [register],
});

const dbQueryDuration = new client.Histogram({
    name: 'db_query_duration_seconds',
    help: 'Database query duration',
    labelNames: ['operation'],
    buckets: [0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0],
    registers: [register],
});

// --- Middleware ---
app.use((req, res, next) => {
    activeConnections.inc();
    const end = httpRequestDurationMicroseconds.startTimer();
    const originalEnd = res.end;

    res.end = function (...args) {
        const duration = end();
        const route = req.route ? req.route.path : req.path;
        httpRequestCounter.inc({
            method: req.method,
            route: route,
            status_code: res.statusCode,
        });
        activeConnections.dec();
        originalEnd.apply(res, args);
    };

    next();
});

// --- Routes ---
app.post('/checkout', async (req, res) => {
    // Simulate DB query
    const dbEnd = dbQueryDuration.startTimer({ operation: 'checkout_insert' });
    await new Promise(r => setTimeout(r, 20 + Math.random() * 80));
    dbEnd();

    // Simulate stragglers
    if (Math.random() < 0.01) {
        await new Promise(r => setTimeout(r, 1000 + Math.random() * 2000));
    }

    res.json({ status: 'ok', order_id: Math.floor(Math.random() * 90000) + 10000 });
});

// --- Metrics Endpoint ---
app.get('/metrics', async (req, res) => {
    res.setHeader('Content-Type', register.contentType);
    res.end(await register.metrics());
});

app.listen(8080, () => {
    console.log('App listening on port 8080');
});
```

---

## Common Pitfalls

1. **Using `average` for latency**. The average hides the 1% of users experiencing 10-second delays. Always use p50, p95, p99.
2. **Alerting on `irate`**. A single missing scrape point produces a spike. Use `rate`.
3. **Wrong histogram buckets**. If your SLO is 100ms and your smallest bucket is 500ms, you cannot measure compliance.
4. **Using Counter as Gauge**. `current_temperature_total` is a Counter — it will only go up (or reset to 0). Use a Gauge.
5. **No `for` clause in alerting rules**. Every transient spike becomes a 3 AM page.
6. **Not separating success/error latency**. Fast-failing 500 errors drag p99 DOWN and mask real performance issues.
7. **Too many labels**. Each label combination creates a new time series. `user_id` as a label with 1M users = 1M time series = Prometheus OOM.

---

*See also: [Structured Logging](../logging/structured-logging.md) | [Distributed Tracing](../tracing/distributed-tracing.md) | [Dashboard Design](../dashboards/dashboard-design.md)*
