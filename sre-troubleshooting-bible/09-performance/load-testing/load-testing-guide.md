# Load Testing Guide
> **Category:** Performance | Load Testing
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#performance` `#load-testing` `#k6` `#oncall`

---

## Why Load Test?

Manual testing proves the code works for ONE user. Load testing proves it works for ALL users. Systems fail in ways that only emerge under concurrency: connection pool exhaustion, lock contention, GC pressure, cache stampedes, DB deadlocks.

A load test answers three questions:
1. **What is the maximum throughput** before latency exceeds SLO?
2. **How does latency degrade** as concurrency increases?
3. **Where is the bottleneck** when the system breaks?

---

## Tools — When to Use Each

| Tool | Strengths | Weaknesses | Best For |
|------|-----------|------------|----------|
| **wrk / wrk2** | 0-install, extremely fast, accurate | No scripting, JSON-only output | Quick sanity checks |
| **ab (Apache Bench)** | Ubiquitous, dead simple | No scripting, HTTP/1.0 behavior quirks | Throwaway tests |
| **k6** | JS scripting, Grafana integration, thresholds, checks | Single instance throughput limits (~50K RPS) | Realistic load scenarios |
| **Locust** | Python scripting, distributed mode, web UI | Slower than k6 per instance | Complex stateful user scenarios |
| **hey** | Simple, Go-based, good output format | No scripting | Quick non-Python/non-JS benchmarks |
| **Artillery** | YAML scenarios, Node.js based | Heavy for simple HTTP tests | Full-stack testing with protocols |

### Quick Benchmarks

```bash
# wrk — quick and dirty throughput test
wrk -t4 -c100 -d30s https://api.example.com/health
# -t4: 4 threads
# -c100: 100 concurrent connections
# -d30s: run for 30 seconds

# wrk2 — constant throughput (coordinated omission resistant)
wrk2 -t4 -c100 -d30s -R 500 https://api.example.com/api/orders
# -R 500: target 500 requests/second (not max throughput — controlled rate)

# ab — simplest possible benchmark
ab -n 10000 -c 100 https://api.example.com/api/users
# -n 10000: total requests to send
# -c 100: concurrent requests at a time

# hey — modern Go alternative to ab
hey -n 10000 -c 100 -m POST -H "Content-Type: application/json" \
  -d '{"user":"test"}' https://api.example.com/api/orders
```

---

## Interpreting Load Test Results

### Throughput (Requests/Second)

```
Throughput = total_requests_completed / test_duration_seconds

Example: 150,000 requests completed in 30s → 5,000 RPS

Headroom = (max_throughput / current_peak_throughput) - 1
If max = 5,000 RPS and current peak = 2,000 RPS → Headroom = 150%
```

**Throughput by itself is meaningless**. Anyone can claim 1M RPS if the response is `HTTP 200: "hello"`. Throughput is only meaningful in context of: (1) the SLO for latency at that throughput, and (2) the error rate at that throughput.

### Latency Percentiles

```
Realistic example output:
  p50  = 45ms    → "Half of users experience under 45ms" (your "normal" experience)
  p90  = 120ms   → "90% of users experience under 120ms"
  p95  = 180ms   → "95% of users (your SLO group) under 180ms" ✓ meeting 200ms SLO
  p99  = 2,500ms → "1% of users wait 2.5 SECONDS" ← THIS IS A PROBLEM
  p999 = 8,000ms → "0.1% wait 8 SECONDS" ← These users will never come back
```

If p50=50ms but p99=5s, you don't have a "slow system" — you have **stragglers**. Something makes ~1% of requests 100x slower than median. (Hint: GC pauses, connection pool waits, or a specific endpoint with a missing index.)

### Error Rate

```
Error Rate < 0.1%  → Healthy
Error Rate 0.1-1%  → Warning — investigate before going to production
Error Rate 1-5%    → Unacceptable — errors spike from specific concurrency level
Error Rate > 5%    → System is broken at this concurrency — this is the breaking point
```

### Concurrency vs. Latency Curve

The most important output from a load test:
```
RPS  | p50  | p95   | p99    | Error Rate
-----|------|-------|--------|-----------
100  | 15ms | 25ms  | 50ms   | 0.00%
500  | 18ms | 30ms  | 60ms   | 0.00%
1000 | 25ms | 45ms  | 80ms   | 0.01%
2000 | 45ms | 100ms | 180ms  | 0.05%
3000 | 80ms | 250ms | 450ms  | 0.10%
4000 | 200ms| 800ms | 2500ms | 0.80%  ← Knee point: non-linear degradation starts here
5000 | 500ms| 3000ms| 8000ms | 5.20%  ← Breaking point: errors, massive latency
```

**The knee point** (~3500 RPS in this example) is where latency grows non-linearly. This is the maximum safe operating throughput.

---

## Finding the Breaking Point — Ramp-Up Test

```bash
# k6 ramp-up — find where SLO is violated
```

```javascript
// ramp-test.js — k6
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const errorRate = new Rate('errors');
const checkoutLatency = new Trend('checkout_latency');

export const options = {
    stages: [
        { duration: '2m', target: 100 },   // Ramp up to 100 RPS over 2 min
        { duration: '3m', target: 100 },   // Stay at 100 RPS for 3 min
        { duration: '2m', target: 500 },   // Ramp to 500 RPS
        { duration: '3m', target: 500 },   // Stay at 500 RPS
        { duration: '2m', target: 1000 },  // Ramp to 1000 RPS
        { duration: '3m', target: 1000 },  // Stay at 1000 RPS
        { duration: '2m', target: 2000 },  // Ramp to 2000 RPS
        { duration: '3m', target: 2000 },  // Stay at 2000 RPS
        { duration: '2m', target: 5000 },  // Ramp to 5000 RPS
        { duration: '3m', target: 5000 },  // Stay until it breaks
        { duration: '2m', target: 0 },     // Ramp down to 0
    ],
    thresholds: {
        'http_req_duration': ['p(95)<200'],          // SLO: p95 < 200ms
        'errors': ['rate<0.01'],                      // Error rate < 1%
        'http_req_failed': ['rate<0.01'],             // Failed requests < 1%
    },
};

export default function () {
    const payload = JSON.stringify({
        items: [
            { id: 'item_1', quantity: 2 },
            { id: 'item_2', quantity: 1 },
        ],
        total: 49.99,
    });

    const params = {
        headers: { 'Content-Type': 'application/json' },
    };

    const res = http.post('https://api.staging.example.com/checkout', payload, params);

    const success = check(res, {
        'status is 200': (r) => r.status === 200,
        'response time < 500ms': (r) => r.timings.duration < 500,
    });

    errorRate.add(!success);
    checkoutLatency.add(res.timings.duration);

    sleep(1);  // Simulate real user think time
}
```

```bash
# Run the ramp test
k6 run ramp-test.js --out influxdb=http://localhost:8086/k6

# Open Grafana, import k6 dashboard (ID: 2587), watch live.
# When p95 crosses 200ms OR errors cross 1% → that's your breaking point.
```

---

## Complete k6 Realistic Scenario

```javascript
// checkout-load-test.js — production-grade k6 script
import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';
import { randomIntBetween, randomItem } from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';

// Custom metrics
const failedCheckouts = new Counter('failed_checkouts');
const successCheckouts = new Counter('successful_checkouts');
const checkoutDuration = new Trend('checkout_duration', true); // true = include conservative percentiles
const paymentLatency = new Trend('payment_latency', true);

// Test configuration
export const options = {
    scenarios: {
        // Ramp-up pattern
        ramping_load: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                { duration: '5m', target: 100 },    // Warm-up to 100 VUs
                { duration: '10m', target: 100 },   // Steady 100 VUs (baseline)
                { duration: '10m', target: 500 },   // Ramp to production peak of 500 VUs
                { duration: '10m', target: 500 },   // Hold at production peak
                { duration: '10m', target: 1000 },  // Ramp to 2x peak (capacity test)
                { duration: '10m', target: 1000 },  // Hold at 2x peak
                { duration: '5m', target: 0 },      // Ramp down
            ],
        },
    },
    thresholds: {
        'http_req_duration': [
            'p(95)<200',       // SLO: 95% of requests under 200ms
            'p(99)<1000',      // 99% under 1s
        ],
        'http_req_failed': ['rate<0.01'],  // <1% error rate
        'failed_checkouts': ['count<100'],  // Max 100 failed checkouts
        'checkout_duration': [
            'p(95)<500',       // Checkout operation p95 < 500ms
            'p(99)<2000',      // p99 < 2s
        ],
    },
    // Abort on critical failures
    abortOnFail: true,
    maxRedirects: 0,
};

// User data pools (simulate different user types)
const USER_IDS = Array.from({ length: 1000 }, (_, i) => `loadtest_user_${i}`);
const PRODUCT_IDS = Array.from({ length: 500 }, (_, i) => `product_${i}`);
const PAYMENT_METHODS = ['credit_card', 'debit_card', 'paypal', 'wallet'];

// User behavior: complete checkout flow
export default function () {
    const userId = randomItem(USER_IDS);

    group('Checkout Flow', function () {
        // Step 1: View cart
        const cartRes = http.get(`https://api.staging.example.com/cart/${userId}`);
        check(cartRes, {
            'cart loaded': (r) => r.status === 200,
        });

        sleep(randomIntBetween(1, 3));

        // Step 2: Add item to cart (sometimes)
        if (Math.random() > 0.3) {  // 70% of users add an item
            const itemPayload = JSON.stringify({
                product_id: randomItem(PRODUCT_IDS),
                quantity: randomIntBetween(1, 5),
            });
            const addRes = http.post(
                `https://api.staging.example.com/cart/${userId}/items`,
                itemPayload,
                { headers: { 'Content-Type': 'application/json' } }
            );
            check(addRes, { 'item added': (r) => r.status === 201 });
            sleep(randomIntBetween(1, 2));
        }

        // Step 3: Initiate checkout
        const checkoutPayload = JSON.stringify({
            payment_method: randomItem(PAYMENT_METHODS),
            shipping_address_id: `addr_${userId}`,
        });

        const checkoutStart = Date.now();
        const checkoutRes = http.post(
            `https://api.staging.example.com/checkout`,
            checkoutPayload,
            {
                headers: { 'Content-Type': 'application/json' },
                tags: { name: 'checkout' },
            }
        );
        checkoutDuration.add(Date.now() - checkoutStart);

        const success = check(checkoutRes, {
            'checkout successful': (r) => r.status === 200 || r.status === 201,
        });

        if (success) {
            successCheckouts.add(1);
            // Track payment-specific latency from response
            if (checkoutRes.json('payment_latency_ms')) {
                paymentLatency.add(checkoutRes.json('payment_latency_ms'));
            }
        } else {
            failedCheckouts.add(1);
            console.log(`Checkout failed for ${userId}: ${checkoutRes.status} ${checkoutRes.body}`);
        }

        sleep(randomIntBetween(2, 5));  // User reviews confirmation page
    });

    // Simulate background browsing
    group('Browse Products', function () {
        const browseRes = http.get('https://api.staging.example.com/products?page=1');
        check(browseRes, { 'products loaded': (r) => r.status === 200 });
        sleep(randomIntBetween(1, 4));
    });
}

// Custom summary output
export function handleSummary(data) {
    return {
        'stdout': JSON.stringify({
            timestamp: new Date().toISOString(),
            total_requests: data.metrics.http_reqs?.values?.count || 0,
            total_failed: data.metrics.http_req_failed?.values?.passes || 0,
            p95_latency_ms: data.metrics.http_req_duration?.values?.['p(95)'] || 0,
            p99_latency_ms: data.metrics.http_req_duration?.values?.['p(99)'] || 0,
            failed_checkouts: data.metrics.failed_checkouts?.values?.count || 0,
        }, null, 2),
    };
}
```

---

## Locust Example — Python-Based Distributed Load Test

```python
# locustfile.py — pip install locust
from locust import HttpUser, task, between, events
from locust.runners import MasterRunner
import random
import time

# Custom tracking
from locust.stats import RequestStats

class CheckoutUser(HttpUser):
    wait_time = between(1, 5)  # Random wait between tasks (think time)

    def on_start(self):
        """Login once per simulated user."""
        self.user_id = f"loadtest_user_{random.randint(1, 10000)}"
        self.auth_token = self.login()

    def login(self):
        response = self.client.post("/auth/login", json={
            "username": self.user_id,
            "password": "testpass123",
        })
        if response.status_code == 200:
            return response.json().get("token")
        return None

    @task(3)  # Weight: 3/7 ≈ 43% of tasks
    def browse_products(self):
        page = random.randint(1, 10)
        with self.client.get(f"/api/products?page={page}",
                             headers={"Authorization": f"Bearer {self.auth_token}"},
                             catch_response=True) as response:
            if response.status_code != 200:
                response.failure(f"Browse failed: {response.status_code}")
            elif response.elapsed.total_seconds() > 0.5:
                response.failure(f"Browse too slow: {response.elapsed.total_seconds()}s")

    @task(2)  # Weight: 2/7 ≈ 29%
    def view_cart(self):
        self.client.get(f"/api/cart/{self.user_id}",
                        headers={"Authorization": f"Bearer {self.auth_token}"})

    @task(1)  # Weight: 1/7 ≈ 14%
    def add_to_cart(self):
        product_id = f"product_{random.randint(1, 500)}"
        self.client.post(f"/api/cart/{self.user_id}/items",
                         json={"product_id": product_id, "quantity": random.randint(1, 3)},
                         headers={"Authorization": f"Bearer {self.auth_token}"})

    @task(1)  # Weight: 1/7 ≈ 14%
    def checkout(self):
        checkout_start = time.time()
        response = self.client.post("/api/checkout",
            json={
                "payment_method": random.choice(["credit_card", "paypal"]),
                "shipping_address_id": f"addr_{self.user_id}",
            },
            headers={"Authorization": f"Bearer {self.auth_token}"},
            name="Checkout"
        )
        duration = time.time() - checkout_start

        if response.status_code not in (200, 201):
            response.failure(f"Checkout failed: {response.status_code} — {response.text}")
        elif duration > 2.0:
            response.failure(f"Checkout too slow: {duration:.2f}s")

@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    print("Load test starting...")

@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    print("Load test completed.")
    stats = environment.stats
    print(f"\nSummary:")
    print(f"  Total requests: {stats.total.num_requests}")
    print(f"  Failures: {stats.total.num_failures}")
    print(f"  Median response: {stats.total.median_response_time}ms")
    print(f"  95th percentile: {stats.total.get_response_time_percentile(0.95)}ms")
    print(f"  RPS: {stats.total.total_rps:.1f}")
```

```bash
# Run Locust (single process)
locust -f locustfile.py --host=https://api.staging.example.com

# Distributed mode: 1 master + N workers
# Master:
locust -f locustfile.py --master --host=https://api.staging.example.com

# Workers (on separate machines):
locust -f locustfile.py --worker --master-host=<master-ip> \
  --host=https://api.staging.example.com

# Headless mode (no web UI):
locust -f locustfile.py --host=https://api.staging.example.com \
  --headless --users 1000 --spawn-rate 50 --run-time 30m
```

---

## Common Load Test Mistakes (and How to Avoid Them)

### 1. Testing from a Single Machine
```
Mistake: Running `wrk -c1000` from your laptop.
Reality: Your laptop's network card or CPU is the bottleneck, not the server.
         You're testing your laptop, not your service.

Fix: Use distributed load generators (k6 cloud, Locust distributed, AWS distributed).
     Or at minimum verify: generator CPU < 80%, generator network < 70%.
```

### 2. Cold-Start Skew
```
Mistake: Starting a 30-second test and including the first 5 seconds in results.
Reality: First 5 seconds: connection pool warming, JIT compilation, cache population.
         These are NOT representative of steady-state performance.

Fix: Include a warm-up phase. Run for 5 minutes, discard first 60 seconds.
     k6: stages: [{duration: '1m', target: 100}, ...]
     Locust: use wait_time between tasks
```

### 3. Not Monitoring Server-Side During Test
```
Mistake: Only looking at k6/Locust output ("throughput = 5000 RPS ✓").
Reality: Server might be choking — dropping connections, swapping, GC thrashing —
         but still returning SOME successes on time.

Fix: During every load test, also monitor:
     - Server CPU, memory, disk I/O (Grafana/Prometheus)
     - DB connections, query latency, deadlocks (DB dashboard)
     - Error logs (NOT just HTTP error codes — app-level errors)
     - GC pauses (JVM), event loop lag (Node.js), GIL contention (Python)
```

### 4. Testing Non-Production with Different Config
```
Mistake: Load testing staging with 2 instances, but production has 20.
Reality: Single-instance bottlenecks (connection pools, file descriptor limits,
         per-instance caches) look like system bottlenecks.

Fix: Match production topology or scale down proportionally.
     If staging has 1 instance and prod has 10, divide expected RPS by 10.
     Or: spin up a production-similar ephemeral environment.
```

### 5. Unrealistic Data Volumes
```
Mistake: Load testing with 100 rows in the database.
Reality: Production has 50M rows. Query that scans 100 rows in 1ms takes 2.5s
         on 50M rows if no index exists.

Fix: Seed test data to match production volumes. If production has 50M orders,
     your load test database needs at least 10M. Use a subset (10-20%) for
     cost efficiency, but never less than 10%.

### Real Scenario: Black Friday Load Test Failure

Scenario:
  E-commerce company. Pre-Black Friday load test in staging.
  Test: 5000 RPS, p95 = 200ms, 0% error rate. ✓ PASS. Ready for Black Friday.

Black Friday (actual):
  8000 RPS peak, p95 = 8s, 10% error rate. Orders lost.
  Customer trust destroyed. $2.3M lost revenue.

Post-mortem root cause:
  Load test used test accounts with 5-20 historical orders each.
  Real customers have 100-50,000 historical orders.
  The query `SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC`
  had no index on (user_id, created_at).

  With 20 orders: full scan of 20 rows = 2ms. Looks fine.
  With 50,000 orders: full scan of 50,000 rows per request = 2600ms per query.
  500 concurrent users × 2600ms query = 1300 seconds of DB time per second of wall time.

Fix implemented after incident:
  1. Seeded staging DB with 10M orders across realistic user distributions
  2. Added user scenarios with varying order counts (1, 100, 1000, 50000)
  3. Added database query latency monitoring during load tests
  4. Created pre-release load test checklist that includes data volume verification

Lesson: Load test data must match production distribution, not just production volume.
        1% of users with 50K orders will still break your system if that's 5000 users.
```

---

*See also: [Application Profiling](../profiling/application-profiling.md) | [Bottleneck Analysis](../bottleneck-analysis/bottleneck-guide.md) | [Caching Strategies](../caching/caching-strategies.md)*
