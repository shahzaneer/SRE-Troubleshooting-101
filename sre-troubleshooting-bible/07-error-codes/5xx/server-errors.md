# 5xx Server Error Codes
> **Category:** API | HTTP | Error Codes
> **Difficulty:** Intermediate to Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#http` `#5xx` `#server-errors` `#oncall`

---

## Overview

5xx status codes mean the **server** failed to fulfill a valid request. The client did nothing wrong; the problem is on the backend. In a microservices architecture, a 5xx from one service can cascade into 5xx responses across the dependency graph. Diagnosing 5xx errors requires understanding the entire request path — from the load balancer through the application to the database.

---

## 500 Internal Server Error

### Technical Definition

> The server encountered an unexpected condition that prevented it from fulfilling the request. — RFC 9110 §15.6.1

This is the **catch-all** error. It means "something went wrong and I don't have a more specific code for you." Production-grade systems should minimize 500s by catching exceptions and converting them to more specific codes.

### Unhandled Exception Deep Dive

#### Python Traceback Anatomy

```
Traceback (most recent call last):
  File "/app/api/orders.py", line 42, in create_order
    total = price / quantity       ← The failing line
ZeroDivisionError: division by zero
```

Key parts:
- **`File`**: Which file and line number
- **`in create_order`**: Which function
- **`total = price / quantity`**: The exact code that failed
- **`ZeroDivisionError`**: The exception type
- Stack frames in reverse chronological order (most recent call last)

#### Java Exception Chain (Caused By)

```
jakarta.persistence.PersistenceException: Error flushing statements
  at org.hibernate.internal.SessionImpl.flush(SessionImpl.java:1207)
Caused by: java.sql.SQLIntegrityConstraintViolationException: Duplicate entry 'user_42@example.com' for key 'idx_email'
  at com.mysql.cj.jdbc.ClientPreparedStatement.execute(ClientPreparedStatement.java:380)
  at com.mysql.cj.jdbc.ClientPreparedStatement.executeUpdate(ClientPreparedStatement.java:418)
```

Key parts:
- **`Caused by`**: The root cause — this is the exception you need to fix
- The top-level exception is a wrapper (Hibernate `PersistenceException`)
- The chain shows how the error propagated through abstraction layers

#### JavaScript Error.stack

```
TypeError: Cannot read properties of undefined (reading 'email')
    at UserService.getEmail (user-service.js:15:28)
    at OrderController.create (order-controller.js:42:12)
    at Layer.handle [as handle_request] (express/lib/router/layer.js:95:5)
```

Key parts:
- **`TypeError: Cannot read properties of undefined`**: Trying to access a property on `undefined` or `null`
- Each `at` line shows the call stack from bottom to top

### Centralized Error Handlers

#### Python — FastAPI Exception Handlers

```python
import traceback
import logging
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import ValidationError

logger = logging.getLogger(__name__)

app = FastAPI()

@app.exception_handler(ValidationError)
async def validation_handler(request: Request, exc: ValidationError):
    return JSONResponse(status_code=400, content={"error": str(exc)})

@app.exception_handler(HTTPException)
async def http_handler(request: Request, exc: HTTPException):
    # Pass through HTTPExceptions that already have status codes
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail},
    )

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # Catch-all for unhandled exceptions → 500
    error_id = str(uuid.uuid4())[:8]
    logger.exception(
        f"Unhandled exception [{error_id}]",
        extra={
            "error_id": error_id,
            "method": request.method,
            "path": str(request.url.path),
            "client_ip": request.client.host if request.client else None,
        },
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_server_error",
            "message": "An unexpected error occurred",
            "error_id": error_id,  # Give the client an ID to reference
        },
    )
```

#### Java — Spring @ControllerAdvice

```java
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ControllerAdvice;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.context.request.WebRequest;

import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

@ControllerAdvice
public class GlobalExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);

    // Known business exceptions — map to specific HTTP codes
    @ExceptionHandler(ResourceNotFoundException.class)
    public ResponseEntity<Map<String, Object>> handleNotFound(
            ResourceNotFoundException ex, WebRequest request) {
        Map<String, Object> body = new HashMap<>();
        body.put("error", "not_found");
        body.put("message", ex.getMessage());
        return ResponseEntity.status(HttpStatus.NOT_FOUND).body(body);
    }

    @ExceptionHandler(ValidationException.class)
    public ResponseEntity<Map<String, Object>> handleValidation(
            ValidationException ex) {
        Map<String, Object> body = new HashMap<>();
        body.put("error", "bad_request");
        body.put("message", ex.getMessage());
        return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(body);
    }

    // Catch-all for unhandled exceptions
    @ExceptionHandler(Exception.class)
    public ResponseEntity<Map<String, Object>> handleGlobal(
            Exception ex, WebRequest request) {

        String errorId = UUID.randomUUID().toString().substring(0, 8);

        log.error("Unhandled exception [errorId={}] path={}",
            errorId,
            request.getDescription(false),
            ex  // Stack trace in logs only
        );

        Map<String, Object> body = new HashMap<>();
        body.put("error", "internal_server_error");
        body.put("message", "An unexpected error occurred");
        body.put("error_id", errorId);
        // NEVER include stack traces in API responses in production

        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body(body);
    }
}
```

#### JavaScript — Express Error Middleware

```javascript
import { v4 as uuidv4 } from 'uuid';

// Known error types → specific codes
class AppError extends Error {
  constructor(message, statusCode, code) {
    super(message);
    this.statusCode = statusCode;
    this.code = code;
    this.isOperational = true;  // Expected errors (validation, not found, etc.)
  }
}

// Centralized error handling middleware — MUST have 4 params
function errorHandler(err, req, res, next) {
  const errorId = uuidv4().slice(0, 8);

  // Log the full error with context
  console.error('Unhandled error', {
    error_id: errorId,
    message: err.message,
    stack: err.stack,
    method: req.method,
    path: req.path,
    query: req.query,
    user: req.user?.sub,
    isOperational: err.isOperational,
  });

  // If it's a known error type, use its status code
  if (err.isOperational) {
    return res.status(err.statusCode).json({
      error: err.code,
      message: err.message,
    });
  }

  // Unknown error: 500 with minimal info
  // In dev mode, include stack trace for debugging
  const isDev = process.env.NODE_ENV === 'development';
  res.status(500).json({
    error: 'internal_server_error',
    message: isDev ? err.message : 'An unexpected error occurred',
    error_id: errorId,
    ...(isDev && { stack: err.stack.split('\n') }),
  });
}

// Must be registered LAST in the middleware chain
app.use(errorHandler);
```

### Real Scenario

> **"Production 500 spike every Monday at 9 AM — cron job updates user credits with integer division that truncates, causing zero balance for some users, which triggers a downstream assertion failure."**
>
> *Root cause:* A weekly cron job runs `update user_credits set credits = total_spent / 100;` (integer division). Users with `total_spent < 100` get `credits = 0`. The next time they access a paid feature, the app checks `if credits <= 0: raise Exception("Invalid credit balance")`, which is an unhandled exception → 500.
>
> *Detection:* 500 spike every Monday 9:00–9:15 AM exactly. All failing requests have `user_id` with `total_spent < 100`. Cron job log shows the UPDATE statement.
>
> *Fix:* Use decimal division: `total_spent / 100.0`, or handle zero-credit users with a specific response instead of crashing.

### Related Sections
- [502 Bad Gateway](#502-bad-gateway) — Upstream invalid response
- [503 Service Unavailable](#503-service-unavailable) — Planned vs unplanned outage
- [4xx/400 Bad Request](../4xx/client-errors.md#400-bad-request) — Client error that should have been caught

### Monitoring Recommendations
- **Log `error_id` in response AND logs** — allows clients to reference the exact error
- **Track 500 rate by endpoint and exception type** — identify which code paths produce unhandled errors
- **Alert**: ANY 500 on critical paths (checkout, login, payment) → immediate page
- **Alert**: 500 rate > 0.1% of traffic → investigate
- **Set up exception tracking** (Sentry, Datadog Error Tracking, Rollbar) — group by stack trace fingerprint

---

## 502 Bad Gateway

### Technical Definition

> The server, while acting as a gateway or proxy, received an invalid response from an inbound server it accessed while attempting to fulfill the request. — RFC 9110 §15.6.3

The reverse proxy (Nginx, HAProxy, Envoy, ALB) received a response from the upstream that is not valid HTTP, or the TCP connection was reset while reading the response.

### The Classic Nginx Log Line

```
upstream sent invalid header while reading response header from upstream
```

This means: Nginx connected to the upstream (your app server), sent the proxied request, and tried to read the HTTP response. What it got back was not a valid HTTP response (e.g., the upstream crashed mid-response, or it sent raw text instead of HTTP).

### Diagnostic Tree

```
502 Bad Gateway
   │
   ├── Is the upstream process RUNNING?
   │     ├── No → Start it. Check for crashes (OOM, segfault).
   │     └── Yes → Continue.
   │
   ├── Is it LISTENING on the expected port?
   │     ├── Check: ss -tlnp | grep <port>
   │     ├── If no → Config mismatch. Check startup logs.
   │     └── If yes → Continue.
   │
   ├── Is it ACCEPTING connections?
   │     ├── Check: curl -v http://127.0.0.1:<port>/health
   │     ├── Connection refused → Port bound but not accepting (backlog full?)
   │     ├── Connection timeout → Firewall? Listening on wrong interface?
   │     └── Connects → Continue.
   │
   ├── Is it responding with VALID HTTP?
   │     ├── Check: curl -v http://127.0.0.1:<port>/health 2>&1 | head -20
   │     ├── Raw text response → App not using HTTP server
   │     ├── Empty response → App crashed mid-handler
   │     ├── Partial headers → Buffer overflow, header too large
   │     └── Valid HTTP → Continue.
   │
   └── Protocol mismatch?
         ├── HTTP/2 → HTTP/1.1: Nginx proxying to upstream that doesn't speak HTTP/2
         ├── gRPC → HTTP/1.1: gRPC requires HTTP/2; Nginx needs grpc_pass, not proxy_pass
         └── HTTPS → HTTP: Nginx configured with proxy_pass https:// but upstream is plain HTTP
```

### LB <-> Upstream Protocol Mismatch

| LB Config | Upstream Expects | Result |
|-----------|-----------------|--------|
| HTTP/2 (ALB) | HTTP/1.1 (Nginx) | 502 — upstream sent invalid header |
| gRPC (Envoy) | HTTP/1.1 (REST) | 502 — protocol error |
| HTTPS | HTTP | 502 or SSL error |
| HTTP/1.1 | h2c (cleartext HTTP/2) | 502 — upstream expects HTTP/2 preface |

### Real Scenario

> **"After enabling HTTP/2 on ALB, all services return 502 — backend Nginx only configured for HTTP/1.1."**
>
> *Root cause:* The team enables HTTP/2 on the AWS Application Load Balancer to improve performance. The ALB now speaks HTTP/2 to clients on the frontend, but by default it also negotiates HTTP/2 to backends. The backend Nginx is configured only for HTTP/1.1 (`listen 80;` without `http2`). Nginx receives an HTTP/2 preface and doesn't understand it, so it closes the connection. ALB gets a connection reset → returns 502.
>
> *Detection:* All endpoints return 502. ALB target group shows all targets unhealthy. Direct `curl` to backend (bypassing ALB) works fine. Nginx error log shows `client sent invalid HTTP/2 preface` or similar.
>
> *Fix:* Configure ALB to use HTTP/1.1 for backend connections (the default is usually HTTP/1.1, but the team had explicitly set protocol version to HTTP/2). Or configure Nginx to accept HTTP/2 from the ALB.

### Code Examples

#### Python — Health Check Endpoint That Actually Verifies Dependencies

```python
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
import asyncio

app = FastAPI()

@app.get("/health")
async def health():
    """Deep health check: verifies app is not just running, but functional."""
    status = {"status": "ok", "checks": {}}

    # Check 1: Database
    try:
        result = await db.execute("SELECT 1")
        status["checks"]["database"] = "ok"
    except Exception as e:
        status["status"] = "degraded"
        status["checks"]["database"] = f"error: {str(e)}"

    # Check 2: Redis cache
    try:
        await redis.ping()
        status["checks"]["cache"] = "ok"
    except Exception as e:
        status["status"] = "degraded"
        status["checks"]["cache"] = f"error: {str(e)}"

    # Check 3: External dependency (optional, can slow health check)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://payment-gateway.example.com/health",
                timeout=2.0,
            )
        status["checks"]["payment_gateway"] = "ok" if resp.status_code == 200 else "error"
    except Exception as e:
        status["checks"]["payment_gateway"] = f"error: {str(e)}"

    http_status = 200 if status["status"] == "ok" else 503
    return JSONResponse(status_code=http_status, content=status)

# Graceful shutdown — prevents 502s when draining
import signal

@app.on_event("shutdown")
async def shutdown():
    """Graceful shutdown: stop accepting new requests, finish in-flight."""
    logger.info("Received shutdown signal, draining connections...")
    # If using uvicorn/gunicorn, this is handled automatically
    # with the --graceful-timeout flag
    await db.disconnect()
    await redis.close()
    logger.info("Shutdown complete")
```

#### Java — Spring Boot Graceful Shutdown

```java
// application.yml
spring:
  lifecycle:
    timeout-per-shutdown-phase: 30s  # Wait up to 30s for in-flight requests

server:
  shutdown: graceful  # Instead of immediate

// Health indicator for load balancer
@Component
public class CustomHealthIndicator implements HealthIndicator {

    private final DataSource dataSource;
    private final RedisConnectionFactory redisFactory;

    public CustomHealthIndicator(
            DataSource dataSource,
            RedisConnectionFactory redisFactory) {
        this.dataSource = dataSource;
        this.redisFactory = redisFactory;
    }

    @Override
    public Health health() {
        Health.Builder builder = new Health.Builder();

        // Database
        try (Connection conn = dataSource.getConnection()) {
            conn.createStatement().execute("SELECT 1");
            builder.withDetail("database", "UP");
        } catch (Exception e) {
            builder.withDetail("database", "DOWN: " + e.getMessage());
            builder.down();
            return builder.build();
        }

        // Redis
        try {
            RedisConnection redis = redisFactory.getConnection();
            redis.ping();
            redis.close();
            builder.withDetail("cache", "UP");
        } catch (Exception e) {
            builder.withDetail("cache", "DOWN: " + e.getMessage());
            builder.down();
            return builder.build();
        }

        return builder.up().build();
    }
}
```

### Related Sections
- [504 Gateway Timeout](#504-gateway-timeout) — Upstream timed out (still alive, just slow)
- [503 Service Unavailable](#503-service-unavailable) — Upstream deliberately not serving
- [TLS/SSL Errors](../tls-errors/tls-error-reference.md) — TLS handshake failures between LB and upstream

### Monitoring Recommendations
- **Track 502 by upstream** — `$upstream_addr` in Nginx logs tells you which backend returned the bad response
- **Check health endpoint status in real-time** — if health checks fail, 502s are imminent
- **Alert**: ANY 502 on critical paths → immediate investigation
- **Correlate 502 with `$upstream_connect_time`** (Nginx) — if connect time is 0, upstream process wasn't listening

---

## 503 Service Unavailable

### Technical Definition

> The server is currently unable to handle the request due to a temporary overloading or maintenance of the server. — RFC 9110 §15.6.4

The server SHOULD include a `Retry-After` header.

### Common Causes

| Cause | Mechanism | Detection |
|-------|-----------|-----------|
| **All upstream instances unhealthy** | Health checks failing, all targets drained | ALB target group shows 0 healthy targets |
| **Connection pool exhausted** | All DB/HTTP connections in use, new requests queue up | `active_connections == max_connections` |
| **Circuit breaker OPEN** | Too many failures to downstream, circuit tripped | resilience4j/Hystrix metrics |
| **Rate limiter triggered (server-side)** | Too many requests queued, overflow protection | `X-RateLimit-Remaining: 0` |
| **Scheduled maintenance** | Intentional 503 with Retry-After | Maintenance flag in config or load balancer rule |
| **Zero ready pods (Kubernetes)** | Rolling deploy killed old pods before new ones ready | `kubectl get pods -l app=myapp` shows 0 READY |

### Circuit Breaker State Machine

```
     ┌─────────┐
     │ CLOSED  │ ← Normal operation. Requests flow through.
     └────┬────┘
          │ Failure rate exceeds threshold
          ▼
     ┌─────────┐
     │  OPEN   │ ← ALL requests fail fast with 503.
     └────┬────┘
          │ Wait duration expires
          ▼
     ┌──────────────┐
     │  HALF_OPEN   │ ← Limited probe requests allowed.
     └────┬─────────┘
          │ Probe succeeds → CLOSED
          │ Probe fails → OPEN (reset wait timer)
```

### Scheduled Maintenance — Proper 503

```python
# FastAPI middleware for maintenance mode
from fastapi import Request
from fastapi.responses import JSONResponse
from datetime import datetime, timedelta, timezone

MAINTENANCE_END = None  # Set to datetime to enable maintenance mode

@app.middleware("http")
async def maintenance_middleware(request: Request, call_next):
    if MAINTENANCE_END and datetime.now(timezone.utc) < MAINTENANCE_END:
        retry_after = int((MAINTENANCE_END - datetime.now(timezone.utc)).total_seconds())
        return JSONResponse(
            status_code=503,
            content={
                "error": "service_unavailable",
                "message": "Scheduled maintenance in progress",
                "expected_completion": MAINTENANCE_END.isoformat(),
            },
            headers={
                "Retry-After": str(retry_after),
                "Cache-Control": "no-cache, no-store, must-revalidate",
            },
        )
    return await call_next(request)
```

### Cascade Failure — The 503 → 503 Loop

```
Service A depends on Service B.
Service B starts returning 503.
Service A's health check calls Service B.
Service A's health check now fails.
Load balancer marks Service A as unhealthy.
Service A now ALSO returns 503.
Other services depending on A start failing.
[... domino effect ...]
```

**Prevention:**
1. **Health checks should NOT depend on downstream services.** Only check what the service itself needs to function (DB, cache). Downstream failures are runtime conditions, not health failures.
2. **Circuit breakers protect the caller**, but health checks protect the service.

### Real Scenario

> **"Rolling deployment kills all old pods before new ones pass readiness probe — 503 for 45 seconds."**
>
> *Root cause:* A Kubernetes Deployment has `maxSurge: 0` and `maxUnavailable: 1`. The rolling update strategy kills one old pod, then creates one new pod. But the new pod takes 30 seconds to start (image pull + app boot + readiness probe). With 3 replicas: kill pod 1 → create new pod 1 (30s wait) → kill pod 2 → create new pod 2 (30s wait) → kill pod 3 → create new pod 3. During this, at times there are only 2 pods. But if the readiness probe is misconfigured (e.g., it checks a downstream service that's also restarting), the new pods never become ready. The deployment keeps killing old pods but new pods never become Ready. Eventually, 0 pods are ready → 503.
>
> *Detection:* `kubectl get pods` shows pods in `Running` but `0/1 READY`. `kubectl describe pod` shows readiness probe failing. ALB/Ingress shows 503. Latency spikes for in-flight requests that get killed.
>
> *Fix:* Increase `maxSurge: 1` so new pods are created before old ones are killed. Fix the readiness probe to not depend on dependencies. Use `terminationGracePeriodSeconds` to let in-flight requests complete.

### Code Examples

#### Python — Circuit Breaker Pattern

```python
import asyncio
import time
from enum import Enum
from functools import wraps
import logging

logger = logging.getLogger(__name__)

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 3,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0
        self.half_open_calls = 0

    def call(self, func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            if self.state == CircuitState.OPEN:
                if time.monotonic() - self.last_failure_time >= self.recovery_timeout:
                    self.state = CircuitState.HALF_OPEN
                    self.half_open_calls = 0
                    logger.info("Circuit breaker transitioning to HALF_OPEN")
                else:
                    raise CircuitBreakerOpenError(
                        f"Circuit is OPEN. Retry after {self.recovery_timeout}s"
                    )

            if self.state == CircuitState.HALF_OPEN:
                if self.half_open_calls >= self.half_open_max_calls:
                    raise CircuitBreakerOpenError(
                        "Circuit is HALF_OPEN — too many probe requests"
                    )
                self.half_open_calls += 1

            try:
                result = await func(*args, **kwargs)
                self._on_success()
                return result
            except Exception as e:
                self._on_failure(e)
                raise

        return wrapper

    def _on_success(self):
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            logger.info("Circuit breaker CLOSED — service recovered")

    def _on_failure(self, error):
        self.failure_count += 1
        self.last_failure_time = time.monotonic()

        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            logger.error(f"HALF_OPEN probe failed: {error}")
            return

        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.error(
                f"Circuit breaker OPEN after {self.failure_count} failures. "
                f"Last error: {error}"
            )

class CircuitBreakerOpenError(Exception):
    pass

# Usage
payment_circuit = CircuitBreaker(
    failure_threshold=3,
    recovery_timeout=30,
)

@payment_circuit.call
async def call_payment_gateway(amount: float):
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.post(
            "https://payment-gateway.example.com/charge",
            json={"amount": amount},
        )
        resp.raise_for_status()
        return resp.json()

# In your route handler
@app.post("/charge")
async def charge(amount: float):
    try:
        result = await call_payment_gateway(amount)
        return result
    except CircuitBreakerOpenError:
        return JSONResponse(
            status_code=503,
            content={
                "error": "service_unavailable",
                "message": "Payment service temporarily unavailable. Please try again.",
            },
            headers={"Retry-After": "30"},
        )
    except httpx.HTTPStatusError as e:
        return JSONResponse(
            status_code=502,
            content={"error": "bad_gateway", "message": f"Payment service error: {e.response.status_code}"},
        )
```

#### Java — Resilience4j Circuit Breaker

```java
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.circuitbreaker.CircuitBreakerConfig;
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import io.github.resilience4j.decorators.Decorators;

import java.time.Duration;

@Configuration
public class CircuitBreakerConfig {

    @Bean
    public CircuitBreaker paymentCircuitBreaker() {
        CircuitBreakerConfig config = CircuitBreakerConfig.custom()
            .failureRateThreshold(50)                     // 50% failure rate opens circuit
            .slowCallRateThreshold(100)                   // 100% slow calls count as failure
            .slowCallDurationThreshold(Duration.ofSeconds(2)) // >2s = slow
            .minimumNumberOfCalls(10)                     // Need 10 calls before evaluating
            .slidingWindowType(SlidingWindowType.COUNT_BASED)
            .slidingWindowSize(100)                       // Evaluate last 100 calls
            .waitDurationInOpenState(Duration.ofSeconds(30)) // HALF_OPEN after 30s
            .permittedNumberOfCallsInHalfOpenState(3)     // 3 probe calls in HALF_OPEN
            .automaticTransitionFromOpenToHalfOpenEnabled(true)
            .build();

        return CircuitBreaker.of("paymentService", config);
    }
}

// Usage in service
@Service
public class PaymentService {

    private final CircuitBreaker circuitBreaker;
    private final RestTemplate restTemplate;

    public ChargeResponse charge(double amount) {
        Supplier<ChargeResponse> supplier = () -> {
            ResponseEntity<ChargeResponse> resp = restTemplate.postForEntity(
                "https://payment-gateway/charge",
                new ChargeRequest(amount),
                ChargeResponse.class
            );
            if (resp.getStatusCode().is5xxServerError()) {
                throw new UpstreamServiceException("Payment gateway 5xx");
            }
            return resp.getBody();
        };

        try {
            return Decorators.ofSupplier(supplier)
                .withCircuitBreaker(circuitBreaker)
                .get();
        } catch (CallNotPermittedException e) {
            // Circuit is OPEN
            throw new ServiceUnavailableException(
                "Payment service temporarily unavailable"
            );
        }
    }
}

// Global handler for ServiceUnavailable
@ExceptionHandler(ServiceUnavailableException.class)
public ResponseEntity<Map<String, Object>> handleUnavailable(
        ServiceUnavailableException ex) {
    Map<String, Object> body = new HashMap<>();
    body.put("error", "service_unavailable");
    body.put("message", ex.getMessage());
    return ResponseEntity
        .status(HttpStatus.SERVICE_UNAVAILABLE)
        .header("Retry-After", "30")
        .body(body);
}
```

#### JavaScript — Opossum Circuit Breaker

```javascript
import CircuitBreaker from 'opossum';
import axios from 'axios';

const paymentCircuit = new CircuitBreaker(
  async (amount) => {
    const response = await axios.post(
      'https://payment-gateway.example.com/charge',
      { amount },
      { timeout: 5000 }
    );
    return response.data;
  },
  {
    timeout: 5000,                // If function takes longer, considered failure
    errorThresholdPercentage: 50,  // Open when 50% of requests fail
    resetTimeout: 30000,           // Try HALF_OPEN after 30s
    rollingCountTimeout: 10000,    // Window duration
    rollingCountBuckets: 10,       // Number of buckets in the window
    volumeThreshold: 5,            // Minimum calls before evaluating
    allowWarmUp: true,             // Don't trip circuit during warm-up
  }
);

// Circuit breaker events
paymentCircuit.on('open', () =>
  console.error('CIRCUIT OPEN — payment gateway failing')
);
paymentCircuit.on('halfOpen', () =>
  console.warn('Circuit half-open, testing payment gateway')
);
paymentCircuit.on('close', () =>
  console.log('Circuit closed — payment gateway recovered')
);
paymentCircuit.on('failure', (err) =>
  console.error('Circuit breaker recorded failure:', err.message)
);

// Fallback function when circuit is open
paymentCircuit.fallback(() => ({
  error: 'service_unavailable',
  message: 'Payment service temporarily unavailable. Please try again in 30 seconds.',
}));

// Usage in Express route
app.post('/charge', async (req, res) => {
  try {
    const result = await paymentCircuit.fire(req.body.amount);
    res.json(result);
  } catch (err) {
    if (err.type === 'open') {
      res.set('Retry-After', '30');
      return res.status(503).json({
        error: 'service_unavailable',
        message: 'Payment service temporarily unavailable',
      });
    }
    // Other errors
    res.status(502).json({
      error: 'bad_gateway',
      message: 'Payment service returned an error',
    });
  }
});
```

### Related Sections
- [502 Bad Gateway](#502-bad-gateway) — Upstream unhealthy but returning invalid HTTP
- [504 Gateway Timeout](#504-gateway-timeout) — Upstream too slow
- [4xx/429 Too Many Requests](../4xx/client-errors.md#429-too-many-requests) — Client-side rate limiting vs server-side 503

### Monitoring Recommendations
- **Track circuit breaker state over time** — OPEN duration tells you how long the dependency was down
- **Alert**: Circuit breaker OPEN for any dependency → critical
- **Alert**: 0 healthy targets in any ALB Target Group / K8s Service → critical
- **K8s**: Alert on `kube_deployment_status_replicas_ready == 0` for > 1 minute

---

## 504 Gateway Timeout

### Technical Definition

> The server, while acting as a gateway or proxy, did not receive a timely response from an upstream server it needed to access in order to complete the request. — RFC 9110 §15.6.5

The distinguishing factor from 502: the upstream was reached, it just didn't respond in time. The connection was established (unlike 502 which often means the connection was refused or reset).

### Connection Timeout vs Read Timeout — Critical Distinction

| Timeout Type | What It Means | When It Triggers |
|-------------|---------------|-----------------|
| **Connection Timeout** | TCP handshake (SYN, SYN-ACK, ACK) didn't complete | Upstream process not listening, firewall blocking, network down |
| **Read Timeout** | TCP connected, but no data received within time | Upstream slow, DB query running long, deadlock |
| **Write Timeout** | TCP connected, but sending request body timed out | Upstream not reading fast enough, network congestion |

A 504 typically means **read timeout**: the proxy connected to upstream, sent the request, and waited for a response. The upstream didn't send bytes back within the configured timeout.

### The Timeout Hierarchy

**Each layer MUST have a shorter timeout than the layer above it.** If a lower layer times out first, the upper layers get clean error responses. If an upper layer times out first, the lower layers waste resources processing requests that will never be served.

```
Client (browser):          30s timeout
     │  MUST be < ──────────────────────┐
     ▼                                  │
CDN (CloudFront):          25s timeout  │
     │  MUST be < ───────────────┐      │
     ▼                           │      │
LB (ALB/NLB):              20s idle     │
     │  MUST be < ────────┐   │      │
     ▼                    │   │      │
Nginx/Envoy:             15s proxy_read │
     │  MUST be < ───┐   │   │      │
     ▼               │   │   │      │
App Server:         10s request │   │
     │  MUST be < ┐  │   │   │      │
     ▼            │  │   │   │      │
DB Driver:       5s query   │   │
                     │  │   │   │
Database:      query runs   │   │   │
                     │  │   │   │
                     ▼  ▼   ▼   ▼      ▼
If DB query runs 30s but app timeout is 10s:
  App gives up after 10s → returns 504
  DB keeps running the query for 30s (wasting resources)
  Client gets 504 at 10s instead of waiting 30s
```

### Real Scenario

> **"Payment gateway takes 8 seconds during peak — Nginx has proxy_read_timeout 5s → 504 on 30% of payments."**
>
> *Root cause:* The payment gateway SLA is <2 seconds. The app's Nginx config has `proxy_read_timeout 5s` which was adequate for normal operation. During a flash sale, the payment gateway's response time spikes to 8 seconds due to load. Every payment that takes >5 seconds gets a 504 from Nginx. The app never sees the response. The customer sees a failure but their card might have been charged (payment gateway processed it, but Nginx timed out before reading the response).
>
> *Detection:* 504 rate spikes to 30% on `/api/payments`. Payment gateway dashboard shows 8s p99 latency. Nginx error log shows `upstream timed out (110: Connection timed out) while reading response header from upstream`. Payment gateway shows successful charges (200) that the app thinks failed (504).
>
> *Fix:* Increase `proxy_read_timeout` to 15s (below LB 20s timeout). But more importantly, fix the payment gateway latency, or implement asynchronous payment processing (accept payment → return 202 Accepted → process in background → webhook callback).

### Code Examples

#### Python — Requests with Explicit Connect and Read Timeouts

```python
import httpx
import asyncio
from contextlib import asynccontextmanager
from typing import Optional

# --- Client with proper timeout configuration ---
class PaymentClient:
    """Client that respects the timeout hierarchy."""

    def __init__(self, base_url: str):
        self.client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(
                connect=2.0,    # TCP handshake: must be fast (same DC)
                read=8.0,       # Wait for response: must be < Nginx proxy_read (15s)
                write=5.0,      # Send request body: must be fast
                pool=2.0,       # Wait for connection from pool
            ),
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
            ),
        )

    async def charge(self, amount: float, idempotency_key: str) -> dict:
        try:
            resp = await self.client.post(
                "/charge",
                json={
                    "amount": amount,
                    "idempotency_key": idempotency_key,
                },
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectTimeout:
            logger.error("Payment gateway connection timeout — is it running?")
            raise PaymentGatewayTimeout(
                "Could not connect to payment gateway"
            )
        except httpx.ReadTimeout:
            logger.error(
                f"Payment gateway read timeout — "
                f"request may have been processed but response was not received. "
                f"Idempotency key: {idempotency_key}"
            )
            raise PaymentGatewayTimeout(
                "Payment gateway did not respond in time — check transaction status"
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 504:
                logger.error(
                    "Upstream 504 — their dependency timed out. "
                    "Our request may or may not have been processed."
                )
            raise

    async def close(self):
        await self.client.aclose()

# --- Retry wrapper with idempotency protection ---
class IdempotentRetry:
    """Retry payments safely using idempotency keys."""

    def __init__(self, client: PaymentClient, max_retries: int = 3):
        self.client = client
        self.max_retries = max_retries

    async def charge_with_retry(
        self, amount: float, idempotency_key: Optional[str] = None
    ) -> dict:
        if idempotency_key is None:
            idempotency_key = str(uuid.uuid4())

        for attempt in range(self.max_retries + 1):
            try:
                return await self.client.charge(amount, idempotency_key)
            except PaymentGatewayTimeout:
                if attempt < self.max_retries:
                    backoff = 2 ** attempt
                    logger.warning(
                        f"Payment gateway timeout, retrying in {backoff}s "
                        f"(attempt {attempt + 1}/{self.max_retries})"
                    )
                    await asyncio.sleep(backoff)
                    continue
                raise
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 409:
                    # Duplicate idempotency key — request was processed
                    logger.info("Idempotent retry detected — returning cached result")
                    return e.response.json()
                raise
```

#### Java — HttpClient with Timeout Chain

```java
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;

@Service
public class PaymentGatewayClient {

    private final HttpClient client;

    public PaymentGatewayClient() {
        this.client = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(2))       // TCP handshake
            .version(HttpClient.Version.HTTP_1_1)
            .build();
    }

    public ChargeResponse charge(double amount) {
        String idempotencyKey = UUID.randomUUID().toString();

        HttpRequest request = HttpRequest.newBuilder()
            .uri(URI.create("https://payment-gateway/charge"))
            .timeout(Duration.ofSeconds(8))              // Total request timeout
            .header("Content-Type", "application/json")
            .header("Idempotency-Key", idempotencyKey)
            .POST(HttpRequest.BodyPublishers.ofString(
                String.format("{\"amount\": %.2f}", amount)
            ))
            .build();

        try {
            HttpResponse<String> response = client.send(
                request,
                HttpResponse.BodyHandlers.ofString()
            );

            if (response.statusCode() == 504) {
                // Payment gateway returned 504 — its upstream timed out
                log.error(
                    "Payment gateway 504. Idempotency key: {}. "
                    + "The payment may have been processed — "
                    + "check idempotency before retrying.",
                    idempotencyKey
                );
                throw new GatewayTimeoutException(
                    "Payment gateway timed out. Idempotency key: " + idempotencyKey
                );
            }

            if (response.statusCode() >= 500) {
                throw new UpstreamServiceException(
                    "Payment gateway error: " + response.statusCode()
                );
            }

            return parseResponse(response.body());

        } catch (HttpTimeoutException e) {
            log.error(
                "Our HTTP client timed out waiting for payment gateway. "
                + "Idempotency key: {}",
                idempotencyKey
            );
            throw new GatewayTimeoutException(
                "Timeout waiting for payment gateway. Key: " + idempotencyKey
            );
        } catch (HttpConnectTimeoutException e) {
            log.error("Cannot connect to payment gateway");
            throw new GatewayTimeoutException("Payment gateway unreachable");
        }
    }

    // Async version with retry
    @Retryable(
        retryFor = GatewayTimeoutException.class,
        maxAttempts = 3,
        backoff = @Backoff(delay = 2000, multiplier = 2)
    )
    public CompletableFuture<ChargeResponse> chargeAsync(double amount) {
        return CompletableFuture.supplyAsync(() -> charge(amount));
    }
}
```

### Related Sections
- [502 Bad Gateway](#502-bad-gateway) — Upstream invalid response (connection-level)
- [503 Service Unavailable](#503-service-unavailable) — Upstream deliberately not serving
- [4xx/408 Request Timeout](../4xx/client-errors.md#408-request-timeout) — Client timed out sending request

### Monitoring Recommendations
- **Stacked timeout graph**: `upstream_response_time` broken down by upstream service — identifies which dependency is slow
- **Alert**: 504 rate > 1% for any endpoint → warning; > 5% → critical
- **Log `$upstream_response_time` for 504 requests** (Nginx) — tells you exactly how long before the timeout
- **Track upstream response time percentiles** — if p50 is fast but p99 is near timeout, you have long-tail latency

---

## 507 Insufficient Storage

### Technical Definition

> The method could not be performed on the resource because the server is unable to store the representation needed to successfully complete the request. — RFC 4918 §11.5 (WebDAV)

### Disk Full Detection

```bash
# Check disk usage — look for 100%
df -h
# Example output:
# /dev/xvda1       50G   50G     0  100% /

# Check inode usage — if inodes exhausted, you can't create files
df -i
# Example output:
# /dev/xvda1     3276800 3276800   0  100% /var
# This means: all inodes used, can't create new files even if disk has space

# Find what's taking up space
du -sh /* 2>/dev/null | sort -rh | head -20

# Find largest directories
du -h --max-depth=1 /var/log | sort -rh
```

### Common Causes

1. **Log files not rotated** — `logrotate` not configured, running out of space at `/var/log`
2. **Docker overlay2 filling up** — `docker system prune -a` to clean unused images/containers/volumes
3. **Database tablespace full** — PostgreSQL `pg_xlog`, MySQL `ibdata1` growing without bound
4. **File upload directory full** — uploaded files not cleaned up after processing
5. **Core dumps** — crashing processes writing core dumps to `/var/crash`
6. **Kubernetes ephemeral storage** — pod's `emptyDir` or container writable layer exceeding `resources.requests.ephemeral-storage`

### Real Scenario

> **"Kubernetes node shows 507 — ephemeral storage limit hit because of log file not being rotated."**
>
> *Root cause:* A microservice writes application logs to `stdout` (collected by Fluentd) but also writes debug logs to `/tmp/debug.log` (an `emptyDir` mount). The ephemeral storage limit is set to 1Gi. Over 3 days, `debug.log` grows to 1Gi. The kubelet evicts the pod with reason `EphemeralStorageExceedsReservation`. The service returns 507 for file-upload endpoints because the node has no ephemeral storage left.
>
> *Detection:* `kubectl describe pod` shows `Status: Evicted`, reason: `EphemeralStorage`. Node metrics show 100% ephemeral storage usage. `kubectl top pod` shows 1Gi memory usage (the log file).
>
> *Fix:* Configure log rotation for the local log file. Increase ephemeral storage limit. Better: stop writing debug logs to disk; use `stdout`/`stderr` only and let the logging pipeline handle it.

### Code Examples

```python
# Python: Check disk space before accepting upload
import os
import shutil
from fastapi import FastAPI, File, UploadFile, HTTPException

app = FastAPI()

def check_disk_space(min_bytes: int = 100 * 1024 * 1024) -> bool:
    """Check if we have at least min_bytes of free space."""
    stat = shutil.disk_usage("/tmp/uploads")
    return stat.free >= min_bytes

@app.post("/upload")
async def upload_file(file: UploadFile):
    # Check disk space before accepting the upload
    if not check_disk_space():
        raise HTTPException(
            status_code=507,
            detail={
                "error": "insufficient_storage",
                "message": "Server storage is full. Please try again later or contact support.",
                "retry_after_seconds": 300,
            },
        )

    content = await file.read()
    if len(content) > 100 * 1024 * 1024:  # 100MB limit
        raise HTTPException(
            status_code=413,
            detail={"error": "payload_too_large", "max_mb": 100},
        )

    file_path = f"/tmp/uploads/{file.filename}"
    with open(file_path, "wb") as f:
        f.write(content)

    return {"filename": file.filename, "size": len(content)}
```

```bash
# Kubernetes: Check ephemeral storage usage of pods
kubectl top pod --containers
# Look for EPHEMERAL STORAGE values approaching limits

# Check pod resource definitions for ephemeral-storage
kubectl get pod <pod-name> -o jsonpath='{.spec.containers[*].resources}'
```

### Related Sections
- [413 Content Too Large](../4xx/client-errors.md) — Client sent payload exceeding server limit
- [500 Internal Server Error](#500-internal-server-error) — Write operations failing due to full disk

### Monitoring Recommendations
- **Node disk usage alert**: > 80% → warning, > 90% → critical, > 95% → immediate page
- **Inode usage alert**: > 80% → warning, > 95% → critical
- **K8s**: Alert on pod evictions due to ephemeral storage
- **Log rate of growth**: `df -h /var/log` trended daily to predict when disk will fill

---

## 508 Loop Detected

### Technical Definition

> The server terminated an operation because it encountered an infinite loop while processing a request. — RFC 5842

### Redirect Loop

The most common form: a series of 301/302 redirects that form a cycle.

```bash
# Diagnose with curl -L (follow redirects) and -v (verbose)
curl -L -v https://example.com/api/auth 2>&1 | grep -E "^< (HTTP|Location)"
# Example output:
# < HTTP/2 301
# < Location: https://example.com/api/auth/            # Added trailing slash
# < HTTP/2 301
# < Location: https://example.com/api/auth             # Removed trailing slash
# curl: (47) Maximum (50) redirects followed           # Loop!
```

### Nginx proxy_pass Misconfiguration

```nginx
# WRONG — creates a loop
location /api/ {
    proxy_pass http://localhost:8080/api/;
    # If the upstream itself proxies /api/ back to Nginx, infinite loop
}

# WRONG — Nginx sending requests to itself
location /api/ {
    proxy_pass http://127.0.0.1:80;  # Port 80 IS Nginx
}
```

### Service Mesh Routing Loop

```
Envoy sidecar on Pod A routes /api/auth → Service B's ClusterIP
Service B's Envoy sidecar doesn't have /api/auth in its route table
Service B's Envoy falls back to default route → sends to Service A
Service A's Envoy routes /api/auth → Service B's ClusterIP
[... infinite loop ...]
```

### Real Scenario

> **"New ingress rule sends `/api/auth` traffic back to itself creating an infinite loop."**
>
> *Root cause:* A team adds a new Kubernetes Ingress rule:
> ```yaml
> paths:
>   - path: /api/auth
>     backend:
>       serviceName: auth-service
>       servicePort: 80
> ```
> But `auth-service` already has a route for `/api/auth` that proxies to itself (misconfigured `nginx.conf` inside the pod). Every request to `/api/auth` hits the Ingress → routes to auth-service → auth-service proxies to `/api/auth` → hits the Ingress → [...].
>
> *Detection:* Latency for `/api/auth` jumps from 50ms to timeout. Nginx returns 508 after 10 internal redirects. `curl -L -v` shows 10 identical `Location: /api/auth` headers.
>
> *Fix:* Fix the auth-service's internal proxy configuration to point to the actual backend, not to itself.

### Related Sections
- [302/301 Redirect Debugging](../4xx/client-errors.md) — Understanding redirect chains
- [502 Bad Gateway](#502-bad-gateway) — If proxy misconfiguration causes upstream errors

### Monitoring Recommendations
- **Alert**: ANY 508 → investigate (should never happen in production)
- **Monitor redirect chain length** — set a max redirect limit in your proxy (e.g., `proxy_redirect_max 5`)
- **Trace header**: Add `X-Forwarded-For` or a hop-count header that increments with each proxy hop

---

*Return to [07 Error Codes Home](../README.md)*
