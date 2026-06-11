# Structured Logging
> **Category:** Observability | Logging
> **Difficulty:** Basic to Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#observability` `#logging` `#structured-logging` `#oncall`

---

## Why Structured Logging (JSON)

Plain text logs are dead. They require regex parsing, break on minor format changes, and cannot be queried efficiently.

### Before (Unstructured — the old way):

```
2026-06-11 10:14:23 ERROR [checkout-service] Failed to process order 45231 for user bob@example.com: DB timeout
```

To find all orders for user "bob@example.com", you need:
```bash
grep "bob@example.com" app.log | grep "ERROR" | grep "checkout-service" | cut -d' ' -f...  # fragile regex nightmare
```

### After (Structured JSON):

```json
{
  "timestamp": "2026-06-11T10:14:23.456Z",
  "level": "ERROR",
  "service": "checkout-service",
  "environment": "production",
  "trace_id": "a1b2c3d4e5f6789012345678abcdef01",
  "span_id": "1234abcd5678ef01",
  "user_id": "usr_abc123",
  "order_id": "45231",
  "message": "Failed to process order: DB timeout",
  "error": {
    "type": "DatabaseTimeoutError",
    "stack": "DatabaseTimeoutError: query exceeded 5000ms at connection.js:42...",
    "db_host": "orders-db-prod.cluster-xyz.us-east-1.rds.amazonaws.com",
    "query": "INSERT INTO orders VALUES (...)",
    "duration_ms": 5250
  },
  "http": {
    "method": "POST",
    "path": "/api/orders",
    "status": 500,
    "duration_ms": 5300,
    "client_ip": "203.0.113.42"
  },
  "correlation_id": "corr-xyz-789"
}
```

With structured logging, querying is trivial:
```bash
# Find all errors for a specific user
cat app.log | jq 'select(.user_id == "usr_abc123" and .level == "ERROR")'

# Count errors by type
cat app.log | jq -r '.error.type' | sort | uniq -c

# Find the slowest 10 requests
cat app.log | jq -s 'sort_by(-.http.duration_ms) | .[0:10]'

# Fetch all logs for a specific trace
cat app.log | jq 'select(.trace_id == "a1b2c3d4e5f6789012345678abcdef01")'

# CloudWatch Logs Insights
fields @timestamp, level, message, error.type
| filter service = "checkout-service" and level = "ERROR"
| stats count(*) by error.type
| sort count desc
```

---

## Required Fields — The Minimum Viable Log

Every log entry in a production system **MUST** contain these fields:

```json
{
  "timestamp": "2026-06-11T10:14:23.456Z",     // ISO 8601 with milliseconds
  "level": "INFO",                               // DEBUG | INFO | WARN | ERROR | FATAL
  "service": "checkout-service",                 // Which service wrote this
  "environment": "production",                   // prod | staging | dev
  "trace_id": "hex-string",                      // From OpenTelemetry (empty if no trace)
  "span_id": "hex-string",                       // From OpenTelemetry
  "message": "Order #45231 processed successfully" // Human-readable summary
}
```

And these optional fields whenever available:
```json
{
  "user_id": "usr_abc123",
  "request_id": "req-xyz-456",
  "correlation_id": "corr-xyz-789",
  "client_ip": "203.0.113.42",
  "duration_ms": 245,
  "http_status": 200
}
```

**Write your logging schema down and enforce it.** An undocumented logging schema is worse than no schema — half your services will use `trace_id` and the other half will use `traceId`, and neither can be joined.

---

## Correlation IDs — The Glue Between Services

The most powerful pattern in distributed logging. Without correlation IDs, you cannot trace a single user request across multiple services.

### Pattern: Generate at Edge, Propagate Everywhere

```
User Request → [X-Correlation-ID: gen-at-edge]
  → API Gateway (logs corr_id) → passes in headers
    → Auth Service (logs corr_id) → returns
    → Order Service (logs corr_id)
      → Payment Service (logs corr_id) → calls External Provider (no header support — log the call)
      → Inventory Service (logs corr_id)
    → Gateway returns response
```

Now to trace a single user's entire request: `grep "corr-xyz-789" all-services.log | sort` — you get the complete timeline across every service.

### Real Scenario: Payment Failure Investigation

```
User reports: "My payment failed but I was charged."
Support engineer: "What's the order ID?"
User: "ORD-12345"

Step 1: Find correlation ID from order-service logs:
  grep "ORD-12345" order-service.log | jq '.correlation_id'
  → "9f8a7b6c-5d4e-3f21-a098-7b6c5d4e3f21"

Step 2: Trace across all services:
  grep "9f8a7b6c" *.log | jq '{ts: .timestamp, svc: .service, msg: .message}'
  →
  {"ts": "10:14:20.100", "svc": "api-gateway", "msg": "POST /orders received"}
  {"ts": "10:14:20.150", "svc": "auth-service", "msg": "Token validated"}
  {"ts": "10:14:20.200", "svc": "order-service", "msg": "Order ORD-12345 created"}
  {"ts": "10:14:20.250", "svc": "payment-service", "msg": "Processing payment $49.99"}
  {"ts": "10:14:25.300", "svc": "payment-service", "msg": "TIMEOUT calling Stripe API"}
  {"ts": "10:14:25.310", "svc": "payment-service", "msg": "Returning 500 to caller"}
  {"ts": "10:14:25.320", "svc": "order-service", "msg": "Payment failed, rolling back order"}

Conclusion: Stripe API timed out after 5s. Order was rolled back.
The "charge" the user saw was a pre-auth that Stripe will auto-void in 7 days.
Reply to user: "Pre-auth will be voided within 7 days. Please try again."
```

---

## Log Levels — When to Use Each

```
Level  | Production Setting       | Usage
-------|--------------------------|--------------------------------------------------
DEBUG  | OFF (or 1% sampling)     | Variable values, function entry/exit, detail
INFO   | ON (100%)                | Request received, order placed, user registered
WARN   | ON (100%)                | Retry happened, degraded mode, rate limit applied
ERROR  | ON (100%) — alert on it  | Exception caught, API call failed, DB error
FATAL  | ON — process exits       | Cannot connect to DB, port in use, missing config
```

### Level Decision Flowchart

```
Is this a normal, expected event in the happy path?
  YES → INFO
  NO → Is the system still functioning despite this?
    YES → WARN
    NO → Can the process continue?
      YES → ERROR
      NO → FATAL
```

### What NOT to Log at Each Level

- **INFO**: Do NOT log request bodies, DB query results, or PII. "User bob@example.com placed order ORD-12345" is INFO. "SELECT * FROM users WHERE email='bob@example.com'" is NOT INFO — it's DEBUG.
- **WARN**: Do NOT warn for expected transient failures (DNS retry, connection pool recycling). Those are DEBUG.
- **ERROR**: Do NOT log user input validation failures as ERROR. A user sending a malformed email address is a 400 Bad Request logged at INFO. An unhandled NPE in payment processing is ERROR.

---

## Avoiding Log Noise

Log noise is the #1 reason engineers stop reading logs. When every line is ERROR, no line is ERROR.

### Common Sources of Noise and Fixes

| Noise Source | Fix |
|---|---|
| Logging every SQL query at INFO | Log only slow queries (>100ms) at WARN |
| Logging request/response bodies at INFO | Log at DEBUG; sample 1% at INFO |
| Health checks every 5s from LB | Filter `/health` and `/ready` endpoints |
| Stack traces for expected exceptions | Log just the message; full trace only at DEBUG |
| "Retry attempt 1 of 3" at INFO | Log at DEBUG; log only "Retry exhausted" at ERROR |

### Don't Log in Hot Paths Without Guard

```java
// BAD: Always computes the string, even if DEBUG is off
logger.debug("Processing item: " + expensiveToString(item));

// GOOD: Guarded — toString() only called if DEBUG is enabled
if (logger.isDebugEnabled()) {
    logger.debug("Processing item: {}", expensiveToString(item));
}

// BETTER: SLF4J parameterized messages (lazy evaluation)
logger.debug("Processing item: {}", () -> expensiveToString(item));
```

### Dynamic Sampling

```python
import random
import os

SHOULD_DEBUG_LOG = (
    os.environ.get("LOG_LEVEL") == "DEBUG"
    or random.random() < 0.01  # 1% sample in production
)

def debug_log(logger, message, **kwargs):
    if SHOULD_DEBUG_LOG:
        logger.debug(message, **kwargs)
```

### Real Scenario: Log Cost Reduction

```
Problem: Log storage costs $8,000/month in Datadog/CloudWatch.

Analysis:
  - 70% of logs are DEBUG-level HTTP request/response bodies (50 bytes avg × 50M requests/day = 2.5GB/day)
  - 15% are health check logs from ALB (/health endpoint)
  - 10% are framework noise (Spring Boot startup, connection pool lifecycle at DEBUG)

Actions:
  1. Set production log level to INFO
  2. Add 1% DEBUG sampling for /api/* endpoints
  3. Filter /health from log ingestion
  4. Add log retention policy: 30 days INFO, 7 days DEBUG

Result: $8,000 → $2,200/month. No loss of operational visibility.
```

---

## Language-Specific Implementations

### Python: structlog

```python
# requirements.txt:
# structlog>=24.1.0
# python-json-logger>=2.0.0

import structlog
import uuid
import time
import logging
from flask import Flask, request, g
import orjson  # Fast JSON serializer

# --- Configure structlog for JSON output ---
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,       # Merge thread-local context
        structlog.processors.add_log_level,            # Add 'level' field
        structlog.processors.TimeStamper(fmt="iso"),   # ISO 8601 timestamps
        structlog.processors.StackInfoRenderer(),      # Add stack trace if exception
        structlog.processors.format_exc_info,          # Format exception info
        structlog.processors.UnicodeDecoder(),         # Handle bytes
        structlog.processors.JSONRenderer(serializer=orjson.dumps),  # JSON output
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

app = Flask(__name__)

# --- Correlation ID Middleware ---
@app.before_request
def inject_correlation_id():
    """Get or generate correlation ID, store in request context."""
    corr_id = request.headers.get('X-Correlation-ID') or str(uuid.uuid4())
    trace_id = request.headers.get('traceparent', '').split('-')[1] if request.headers.get('traceparent') else ''

    # Bind to structlog context — automatically added to all logs in this request
    structlog.contextvars.bind_contextvars(
        correlation_id=corr_id,
        trace_id=trace_id,
        client_ip=request.headers.get('X-Forwarded-For', request.remote_addr),
        request_id=str(uuid.uuid4())[:8],
    )
    g.correlation_id = corr_id
    g.start_time = time.monotonic()

@app.after_request
def log_request(response):
    duration_ms = (time.monotonic() - g.start_time) * 1000
    logger.info(
        "request_completed",
        method=request.method,
        path=request.path,
        status=response.status_code,
        duration_ms=round(duration_ms, 2),
    )
    # Add correlation ID to response headers so client can report it
    response.headers['X-Correlation-ID'] = g.correlation_id
    structlog.contextvars.clear_contextvars()
    return response

# --- Example Route ---
@app.route('/orders', methods=['POST'])
def create_order():
    data = request.get_json()

    if not data or 'items' not in data:
        logger.warning(
            "invalid_request",
            reason="missing_items_field",
            body_preview=str(data)[:200]  # NEVER log full body with PII
        )
        return {"error": "items field required"}, 400

    try:
        order_id = process_order(data)
        logger.info(
            "order_created",
            order_id=order_id,
            item_count=len(data['items']),
            total_amount=data.get('total', 0),
        )
        return {"order_id": order_id, "status": "created"}, 201

    except PaymentFailedError as e:
        logger.error(
            "payment_failed",
            order_id=data.get('reference_id', 'unknown'),
            error=str(e),
            provider=str(e.provider),
            duration_ms=e.duration_ms,
        )
        return {"error": "Payment failed"}, 500

    except Exception:
        logger.exception("unexpected_error")  # structlog automatically adds exc_info
        return {"error": "Internal server error"}, 500

def process_order(data):
    return f"ORD-{uuid.uuid4().hex[:8]}"

class PaymentFailedError(Exception):
    def __init__(self, message, provider, duration_ms):
        super().__init__(message)
        self.provider = provider
        self.duration_ms = duration_ms

if __name__ == '__main__':
    app.run(port=8080)
```

### Java: Logback + SLF4J + MDC

```java
// build.gradle dependencies:
// - ch.qos.logback:logback-classic:1.5.0
// - net.logstash.logback:logstash-logback-encoder:7.4
// - org.slf4j:slf4j-api:2.0.0

// --- logback.xml ---
// <configuration>
//   <appender name="JSON" class="ch.qos.logback.core.ConsoleAppender">
//     <encoder class="net.logstash.logback.encoder.LogstashEncoder">
//       <includeMdcKeyName>correlationId</includeMdcKeyName>
//       <includeMdcKeyName>traceId</includeMdcKeyName>
//       <includeMdcKeyName>spanId</includeMdcKeyName>
//       <includeMdcKeyName>userId</includeMdcKeyName>
//       <customFields>{"service":"checkout-service","environment":"production"}</customFields>
//     </encoder>
//   </appender>
//   <root level="INFO">
//     <appender-ref ref="JSON"/>
//   </root>
// </configuration>

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import javax.servlet.FilterChain;
import javax.servlet.ServletException;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.UUID;

@Component
class CorrelationIdFilter extends OncePerRequestFilter {

    private static final String CORRELATION_ID_HEADER = "X-Correlation-ID";
    private static final String MDC_KEY = "correlationId";

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain filterChain)
            throws ServletException, IOException {

        String correlationId = request.getHeader(CORRELATION_ID_HEADER);
        if (correlationId == null || correlationId.isEmpty()) {
            correlationId = UUID.randomUUID().toString();
        }

        // Put in MDC — automatically included in every log statement in this thread
        MDC.put(MDC_KEY, correlationId);
        MDC.put("traceId",
            request.getHeader("traceparent") != null
                ? request.getHeader("traceparent").split("-")[1]
                : "");

        // Add to response header so caller can trace
        response.setHeader(CORRELATION_ID_HEADER, correlationId);

        try {
            filterChain.doFilter(request, response);
        } finally {
            // CRITICAL: Always clear MDC to prevent thread-pool contamination
            // If you forget this, the NEXT request on this thread inherits the previous correlation ID
            MDC.clear();
        }
    }
}

// --- Usage in service classes ---
@Service
public class OrderService {
    private static final Logger log = LoggerFactory.getLogger(OrderService.class);

    public Order createOrder(OrderRequest request) {
        log.info("Creating order for user {}", request.getUserId());

        try {
            // Business logic
            Order order = saveToDatabase(request);
            log.info("Order {} created successfully", order.getId());
            return order;

        } catch (DatabaseTimeoutException e) {
            // MDC fields automatically included in JSON output
            log.error("Database timeout while creating order", e);
            throw new OrderCreationException("Failed to create order", e);
        }
    }
}
```

### JavaScript: winston

```javascript
// package.json:
// "winston": "^3.13.0"
// "uuid": "^10.0.0"

const winston = require('winston');
const { v4: uuidv4 } = require('uuid');
const express = require('express');

// --- Create Winston Logger ---
const logger = winston.createLogger({
    level: process.env.LOG_LEVEL || 'info',
    defaultMeta: {
        service: 'checkout-service',
        environment: process.env.NODE_ENV || 'development',
    },
    format: winston.format.combine(
        winston.format.timestamp({ format: 'YYYY-MM-DDTHH:mm:ss.SSSZ' }),
        winston.format.errors({ stack: true }),
        winston.format.json()
    ),
    transports: [
        new winston.transports.Console(),
        // Optional: file transport for local dev
        // new winston.transports.File({ filename: 'combined.json' }),
    ],
    // Sample 1% of debug logs in production
    ...(process.env.NODE_ENV === 'production' && {
        level: 'info',
    }),
});

// --- Correlation ID Middleware ---
function correlationMiddleware(req, res, next) {
    const correlationId = req.headers['x-correlation-id'] || uuidv4();
    const traceId = req.headers['traceparent']
        ? req.headers['traceparent'].split('-')[1]
        : '';

    // Attach to request and response
    req.correlationId = correlationId;
    req.traceId = traceId;
    res.setHeader('X-Correlation-ID', correlationId);

    // Create a child logger with request-scoped metadata
    req.logger = logger.child({
        correlation_id: correlationId,
        trace_id: traceId,
        client_ip: req.headers['x-forwarded-for'] || req.ip,
        request_id: uuidv4().slice(0, 8),
    });

    const start = Date.now();

    // Log response completion
    res.on('finish', () => {
        req.logger.info('request_completed', {
            method: req.method,
            path: req.path,
            status: res.statusCode,
            duration_ms: Date.now() - start,
        });
    });

    next();
}

// --- Express App ---
const app = express();
app.use(correlationMiddleware);
app.use(express.json());

app.post('/orders', (req, res) => {
    const { items, total } = req.body;

    if (!items) {
        req.logger.warn('invalid_request', {
            reason: 'missing_items_field',
        });
        return res.status(400).json({ error: 'items field required' });
    }

    try {
        const orderId = `ORD-${uuidv4().slice(0, 8)}`;
        req.logger.info('order_created', {
            order_id: orderId,
            item_count: items.length,
            total_amount: total || 0,
        });
        res.status(201).json({ order_id: orderId, status: 'created' });
    } catch (err) {
        req.logger.error('order_failed', {
            error: err.message,
            stack: err.stack,
        });
        res.status(500).json({ error: 'Internal server error' });
    }
});

app.listen(8080, () => {
    logger.info('server_started', { port: 8080 });
});
```

---

## Common Pitfalls

1. **Forgetting to clear MDC/context** in thread-pooled environments. A correlation ID from request A leaks into request B. Always clear in `finally` block.
2. **Logging PII**: emails, credit cards, SSNs, addresses at INFO level. Use a redaction filter or PII-safe types.
3. **Logging request bodies at INFO**: a 10MB file upload becomes a 10MB log line. Log the size, not the body.
4. **Using `console.log` in production**: no structured fields, no correlation IDs, no level filtering. Use a proper logger.
5. **Inconsistent field names**: `trace_id` vs `traceId` vs `trace-id` across services. Standardize in your logging schema document.

---

*See also: [Metrics Guide](../metrics/metrics-guide.md) | [Distributed Tracing](../tracing/distributed-tracing.md) | [Dashboard Design](../dashboards/dashboard-design.md)*
