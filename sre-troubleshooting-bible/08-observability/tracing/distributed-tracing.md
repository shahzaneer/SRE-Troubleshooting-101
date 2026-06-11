# Distributed Tracing
> **Category:** Observability | Tracing | OpenTelemetry
> **Difficulty:** Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#observability` `#tracing` `#opentelemetry` `#oncall`

---

## OpenTelemetry Concepts

Distributed tracing follows a single request as it hops across service boundaries. Without it, a 3-second API call is a black box — you know it's slow but not why.

### Fundamental Building Blocks

```
Trace: The complete journey of a single request through your system.
  ├── Span A (API Gateway — 2ms)
  │   ├── Span B (Auth Service — 15ms)
  │   │   └── Span C (DB: SELECT user WHERE token=... — 12ms)
  │   └── Span D (Order Service — 2,800ms) ← THE SLOW ONE
  │       ├── Span E (Inventory Check — 45ms)
  │       └── Span F (DB: SELECT * FROM orders WHERE... — 2,720ms) ← MISSING INDEX
  └── Span G (Notification Service — 30ms)
```

Every span contains:
```json
{
  "trace_id": "a1b2c3d4e5f6789012345678abcdef01",  // Links all spans together
  "span_id": "1234abcd5678ef01",                    // Unique ID for this span
  "parent_span_id": "99aabbcc",                     // Points to parent (empty for root)
  "name": "POST /api/orders",                        // Human-readable operation name
  "start_time": "2026-06-11T10:14:20.100Z",
  "end_time": "2026-06-11T10:14:23.100Z",
  "duration_ms": 3000,
  "status": { "code": 0 },                           // 0=OK, 1=Error
  "attributes": {
    "http.method": "POST",
    "http.url": "https://api.example.com/orders",
    "http.status_code": 200,
    "service.name": "api-gateway",
    "user.id": "usr_abc123"
  },
  "events": [
    { "name": "cache.miss", "timestamp": "..." },
    { "name": "db.query.start", "timestamp": "..." }
  ]
}
```

### Context Propagation

Traces cross service boundaries via HTTP headers or gRPC metadata. The W3C Trace Context standard **must** be used for interoperability:

```
HTTP Headers:
  traceparent: 00-a1b2c3d4e5f6789012345678abcdef01-1234abcd5678ef01-01
               ^^ ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ ^^^^^^^^^^^^^^^^ ^^
               ver trace_id                          span_id         flags

  tracestate: vendor1=opaqueValue,vendor2=opaqueValue  (vendor-specific data)
```

Any service that doesn't propagate these headers breaks the trace. You get partial traces — the most frustrating debugging experience.

---

## Finding the Slow Span — Step-by-Step

### Real Scenario: Order API Takes 3 Seconds

```
User complaint: "The checkout page takes forever to load."

Step 1 — Reproduce the issue:
  curl -w "@curl-format.txt" -X POST https://api.example.com/orders \
    -H "Content-Type: application/json" \
    -d '{"items": [...], "total": 49.99}'

  curl-format.txt:
    time_namelookup:  %{time_namelookup}\n
    time_connect:     %{time_connect}\n
    time_starttransfer: %{time_starttransfer}\n
    time_total:       %{time_total}\n

  Result: time_total = 3.125s (not DNS, not connect — server processing time)

Step 2 — Open Jaeger/Honeycomb/Zipkin:
  Search: service=api-gateway operation="POST /orders" duration > 2s
  Select the trace with duration 3.125s.

Step 3 — Waterfall view shows:
  ┌───────────────────────────────────────────────────────────────┐
  │ POST /orders (api-gateway)                    ████████████ 3.1s
  │ ├─ ValidateJWT (auth-service)                 █ 15ms
  │ ├─ POST /internal/orders (order-service)      ██████████ 3.0s  ← SUSPECT
  │ │  ├─ CheckInventory (inventory-service)      █ 100ms
  │ │  ├─ DBQuery: findOrderByUserId              ██████ 2.7s     ← ROOT CAUSE
  │ │  └─ PublishOrderEvent (kafka)               █ 5ms
  │ └─ SendConfirmation (notification-service)    █ 30ms
  └───────────────────────────────────────────────────────────────┘

Step 4 — Identify root cause:
  Span: "DBQuery: findOrderByUserId" takes 2.7s for a simple SELECT.

  Check DB metrics: orders table has 50M rows, no index on user_id.
  SHOW INDEXES FROM orders;  → PRIMARY KEY only.

  EXPLAIN SELECT * FROM orders WHERE user_id = 12345;
  → FULL TABLE SCAN, 50M rows examined.

Step 5 — Fix:
  CREATE INDEX idx_orders_user_id_created ON orders(user_id, created_at DESC);
  Query time: 2.7s → 4ms.

Step 6 — Verify:
  Redeploy, re-run curl, total time now 350ms. Trace confirms.
```

### Trace Analysis Checklist

When looking at a trace, ask:
1. **Which span consumed the most time?** Click sort-by-duration in Jaeger.
2. **Was time spent in serial or parallel?** Sequential spans add up. Parallel spans don't.
3. **Is the slow span calling a DB?** Check for missing indexes, full scans, N+1 queries.
4. **Is the slow span calling another service?** Check that service's dashboard/traces.
5. **Are there gaps?** A gap between spans means un-instrumented code (or context propagation failure).

---

## Trace Sampling Strategies

Sampling is the art of deciding which requests to trace. Trace 100% and you'll bankrupt your tracing backend. Trace 1% and you'll miss the incident that took down production.

### Head Sampling (At Request Start)

```
Load Balancer
  │
  ├─ Request arrives
  ├─ Generate random number (0-1)
  ├─ If < 0.10 (10%): create trace_id, set sampled=true, propagate
  └─ If >= 0.10: set sampled=false, propagate empty trace_id
```

**Pros**: Simple, zero infrastructure overhead.
**Cons**: You might miss THE request that triggers a bug. 90% of errors are never traced.

```yaml
# OpenTelemetry Collector config for head sampling
processors:
  probabilistic_sampler:
    hash_seed: 22
    sampling_percentage: 10  # 10% of all requests
```

### Tail Sampling (At Request End — Based on Outcome)

```
OpenTelemetry Collector
  │
  ├─ Buffers ALL spans temporarily
  ├─ Waits for trace completion (or timeout)
  ├─ Decision:
  │   ├─ status=ERROR → KEEP (100%)
  │   ├─ duration > 2s  → KEEP (100%)
  │   ├─ http.status_code >= 500 → KEEP (100%)
  │   ├─ random sample → KEEP (10% of remaining)
  │   └─ otherwise → DISCARD
```

**Pros**: Every error and slow request is traced. You never miss an incident.
**Cons**: Requires buffering infrastructure. Latency added (~100ms). Higher collector costs.

```yaml
# OpenTelemetry Collector config for tail sampling
processors:
  tail_sampling:
    decision_wait: 10s  # Wait up to 10s for all spans
    num_traces: 50000   # Buffer up to 50K traces
    policies:
      - name: errors-only
        type: status_code
        status_code:
          status_codes: [ERROR]
      - name: slow-requests
        type: latency
        latency:
          threshold_ms: 2000
      - name: healthcheck-exclusion
        type: string_attribute
        string_attribute:
          key: http.url
          values: ["/health", "/metrics", "/ready"]
          enabled_regex_matching: true
          invert_match: true  # Keep if NOT a health check
```

### Real Scenario: Switching to Tail Sampling

```
Incident: P0 outage at 2 AM. Oncall engineer opens Jaeger to find the root cause.
Result: All affected traces had duration < 1s (they were failing fast, not slow).
The 10% head sampling missed every single one. Zero traces available.
Engineer had to read through 2GB of raw logs to manually reconstruct the call chain.

Fix: Implemented tail sampling with policy: "keep 100% of errors and 100% of
requests with duration > 500ms OR status != 200." Remaining healthy requests: 5%
random sample. Total trace volume: 12% of total requests (affordable).

Outcome: Every single error trace is now available in Jaeger within 30 seconds.
MTTR (Mean Time To Resolution) dropped from 45 minutes to 12 minutes.
```

---

## Error Budget and Tracing

Traces don't just find slow code — they protect your error budget by identifying which operations are burning it fastest.

### Finding Error Budget Consumers via Traces

```promql
# Assuming you have an SLO of 99.9% availability
# Error budget consumed = total errors / total requests allowed

# Find which span attributes are correlated with errors
# In Honeycomb:
#   GROUP BY span.name, http.status_code
#   WHERE http.status_code >= 500
#   COUNT
#   ORDER BY count DESC

# Result:
#   POST /api/payment/charge       452 errors  40% of error budget
#   GET /api/inventory/check       189 errors  17% of error budget
#   POST /api/orders/create        112 errors  10% of error budget
#   ...everything else             366 errors  33% of error budget

# Action: Fix POST /api/payment/charge first — single biggest consumer.
```

---

## Language-Specific Instrumentation

### Python: OpenTelemetry

```python
# pip install opentelemetry-api opentelemetry-sdk
# pip install opentelemetry-instrumentation-flask
# pip install opentelemetry-instrumentation-requests
# pip install opentelemetry-exporter-otlp-proto-grpc
# pip install opentelemetry-instrumentation-psycopg2

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.trace import Status, StatusCode

from flask import Flask, request
import requests

# --- Setup ---
resource = Resource.create({
    "service.name": "order-service",
    "service.version": "2.4.1",
    "deployment.environment": "production",
})

provider = TracerProvider(resource=resource)
processor = BatchSpanProcessor(
    OTLPSpanExporter(endpoint="http://otel-collector:4317", insecure=True)
)
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)

tracer = trace.get_tracer(__name__)

app = Flask(__name__)

# Auto-instrument Flask — all HTTP requests get spans automatically
FlaskInstrumentor().instrument_app(app)
# Auto-instrument outgoing requests — all requests.get/post get spans
RequestsInstrumentor().instrument()

@app.route('/orders', methods=['POST'])
def create_order():
    data = request.get_json()

    # --- Manual span for business logic ---
    with tracer.start_as_current_span("process_order") as span:
        span.set_attribute("order.items_count", len(data.get("items", [])))
        span.set_attribute("order.total", data.get("total", 0))
        span.set_attribute("user.id", data.get("user_id"))

        # --- Manual child span for payment ---
        with tracer.start_as_current_span("charge_payment") as payment_span:
            payment_span.set_attribute("payment.provider", "stripe")

            try:
                response = requests.post(
                    "http://payment-service/charge",
                    json={
                        "amount": data["total"],
                        "currency": data.get("currency", "usd"),
                    },
                    timeout=5
                )

                if response.status_code != 200:
                    payment_span.set_status(Status(StatusCode.ERROR))
                    payment_span.set_attribute("payment.error", response.text)
                    return {"error": "Payment failed"}, 500

                payment_span.set_attribute("payment.transaction_id",
                    response.json().get("transaction_id"))

            except requests.Timeout:
                payment_span.set_status(Status(StatusCode.ERROR, "Payment timeout"))
                payment_span.record_exception(
                    Exception(f"Payment provider timeout after 5s"),
                    attributes={"timeout_ms": 5000}
                )
                return {"error": "Payment timeout"}, 504

        # --- Add event to parent span ---
        span.add_event("payment_successful", attributes={
            "provider": "stripe",
            "amount": data["total"],
        })

    return {"order_id": "ORD-99999", "status": "created"}
```

### Java: OpenTelemetry Agent (Zero-Code Auto-Instrumentation)

```bash
# Download the agent JAR
curl -LO https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/download/v1.33.0/opentelemetry-javaagent.jar

# Run with agent — zero code changes needed
java -javaagent:opentelemetry-javaagent.jar \
     -Dotel.service.name=order-service \
     -Dotel.traces.exporter=otlp \
     -Dotel.metrics.exporter=otlp \
     -Dotel.logs.exporter=otlp \
     -Dotel.exporter.otlp.endpoint=http://otel-collector:4317 \
     -Dotel.resource.attributes=deployment.environment=production,service.version=2.4.1 \
     -jar order-service.jar
```

For manual spans when auto-instrumentation isn't enough:

```java
import io.opentelemetry.api.GlobalOpenTelemetry;
import io.opentelemetry.api.trace.Span;
import io.opentelemetry.api.trace.StatusCode;
import io.opentelemetry.api.trace.Tracer;
import io.opentelemetry.context.Scope;
import io.opentelemetry.instrumentation.annotations.WithSpan;

import org.springframework.web.bind.annotation.*;
import org.springframework.stereotype.Service;

@Service
class OrderProcessor {

    private final Tracer tracer = GlobalOpenTelemetry
        .getTracer("order-service", "2.4.1");

    @WithSpan("process-order")  // Auto-create span via annotation
    public OrderResult process(OrderRequest request) {
        Span span = Span.current();

        span.setAttribute("order.items_count", request.getItems().size());
        span.setAttribute("order.total", request.getTotal());
        span.setAttribute("user.id", request.getUserId());

        // Child span for payment
        Span paymentSpan = tracer.spanBuilder("charge-payment")
            .setParent(io.opentelemetry.context.Context.current().with(span))
            .startSpan();

        try (Scope ignored = paymentSpan.makeCurrent()) {
            paymentSpan.setAttribute("payment.provider", "stripe");
            paymentSpan.setAttribute("payment.amount", request.getTotal());

            PaymentResult payment = chargePayment(request);

            if (!payment.isSuccess()) {
                paymentSpan.setStatus(StatusCode.ERROR,
                    "Payment failed: " + payment.getErrorMessage());
                throw new PaymentFailedException(payment.getErrorMessage());
            }

            paymentSpan.setAttribute("payment.transaction_id",
                payment.getTransactionId());
            paymentSpan.addEvent("payment_successful");
            return new OrderResult("ORD-" + payment.getTransactionId());

        } catch (Exception e) {
            paymentSpan.recordException(e);
            paymentSpan.setStatus(StatusCode.ERROR, e.getMessage());
            throw e;
        } finally {
            paymentSpan.end();
        }
    }

    private PaymentResult chargePayment(OrderRequest request) {
        // Payment logic
        return new PaymentResult(true, "txn_abc123", null);
    }
}
```

### JavaScript: OpenTelemetry Node.js SDK

```javascript
// npm install @opentelemetry/api @opentelemetry/sdk-node
// npm install @opentelemetry/auto-instrumentations-node
// npm install @opentelemetry/exporter-trace-otlp-proto

const { NodeSDK } = require('@opentelemetry/sdk-node');
const { getNodeAutoInstrumentations } = require('@opentelemetry/auto-instrumentations-node');
const { OTLPTraceExporter } = require('@opentelemetry/exporter-trace-otlp-proto');

const sdk = new NodeSDK({
    resource: {
        'service.name': 'order-service',
        'service.version': '2.4.1',
        'deployment.environment': 'production',
    },
    traceExporter: new OTLPTraceExporter({
        url: 'http://otel-collector:4318/v1/traces',
    }),
    instrumentations: [getNodeAutoInstrumentations()],
});

sdk.start();

// --- Express app with manual spans ---
const express = require('express');
const { trace, SpanStatusCode } = require('@opentelemetry/api');

const app = express();
const tracer = trace.getTracer('order-service');

app.post('/orders', async (req, res) => {
    const span = trace.getActiveSpan();
    span.setAttribute('order.items_count', req.body.items?.length || 0);
    span.setAttribute('order.total', req.body.total || 0);

    // Child span for payment
    const paymentSpan = tracer.startSpan('charge-payment');
    paymentSpan.setAttribute('payment.provider', 'stripe');

    try {
        const paymentResult = await chargePayment(req.body.total);

        if (!paymentResult.success) {
            paymentSpan.setStatus({
                code: SpanStatusCode.ERROR,
                message: `Payment failed: ${paymentResult.error}`,
            });
            return res.status(500).json({ error: 'Payment failed' });
        }

        paymentSpan.setAttribute('payment.transaction_id', paymentResult.txnId);
        paymentSpan.addEvent('payment_successful');
        res.json({ order_id: `ORD-${paymentResult.txnId}` });

    } catch (err) {
        paymentSpan.recordException(err);
        paymentSpan.setStatus({ code: SpanStatusCode.ERROR, message: err.message });
        res.status(504).json({ error: 'Payment timeout' });
    } finally {
        paymentSpan.end();
    }
});
```

---

## Common Pitfalls

1. **Missing context propagation**: Service A creates a trace but doesn't forward `traceparent` to Service B → trace ends at Service A. Verify every HTTP client and gRPC client is instrumented.
2. **One span per request**: If your trace only has the auto-generated HTTP span, it's useless. Add manual spans for DB queries, external API calls, cache lookups, business logic.
3. **Sampling that discards errors**: Head sampling at 10% means 90% of errors are invisible. Tail sampling solves this.
4. **Tracing without logging correlation**: A slow span says WHERE but not WHY. Log `trace_id` and `span_id` in logs, click from Jaeger to Loki to see the exact error message.
5. **Retention too short**: Traces are only valuable if you can look back at "how things were before the issue." Minimum 7 days retention for traces.

---

*See also: [Metrics Guide](../metrics/metrics-guide.md) | [Structured Logging](../logging/structured-logging.md) | [Dashboard Design](../dashboards/dashboard-design.md)*
