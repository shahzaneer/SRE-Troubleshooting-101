# REST API Debugging

> **Category:** API | REST | HTTP
> **Difficulty:** Basic to Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#rest` `#api` `#http` `#oncall`

---

## curl Full Debugging Toolkit

### Verbose Mode (`-v`)

```bash
curl -v https://api.example.com/v1/orders/12345
```

The `-v` flag outputs:
- **Request headers** (lines starting with `>`): method, path, Host, User-Agent, Accept, etc.
- **Response headers** (lines starting with `<`): HTTP status, Content-Type, set-cookie, rate-limit headers.
- **TLS handshake info**: certificate CN, issuer, TLS version negotiated, cipher suite.
- **Connection reuse**: `* Connection #0 to host api.example.com left intact`.

### Full Trace (`--trace-ascii`)

```bash
curl --trace-ascii /tmp/trace.out https://api.example.com/v1/orders/12345
# Or stream to stdout:
curl --trace-ascii - https://api.example.com/v1/orders/12345
```

Outputs every byte sent and received, including hex dumps. Use when you suspect a proxy or middleware is mutating headers or body, or when the server returns garbage bytes that aren't valid HTTP.

### Timing Breakdown (`-w`)

```bash
curl -w "\n\
time_namelookup:  %{time_namelookup}\n\
time_connect:     %{time_connect}\n\
time_appconnect:  %{time_appconnect}\n\
time_pretransfer: %{time_pretransfer}\n\
time_redirect:    %{time_redirect}\n\
time_starttransfer: %{time_starttransfer}\n\
time_total:       %{time_total}\n\
size_download:    %{size_download}\n\
speed_download:   %{speed_download}\n" \
  -o /dev/null -s https://api.example.com/health
```

#### Meaning of Each Timing Metric

| Variable | Description | Hop |
|---|---|---|
| `time_namelookup` | DNS resolution time. From start until name resolution completed. | Client → DNS |
| `time_connect` | TCP three-way handshake. From start until TCP connect to remote host completed. Includes `time_namelookup`. | Client → Server |
| `time_appconnect` | TLS/SSL handshake. From start until SSL connect to remote host completed. Includes `time_connect`. | Client → Server (TLS) |
| `time_pretransfer` | From start until file transfer is about to begin. Includes all pre-transfer commands and negotiations specific to the protocol. | Client ready |
| `time_redirect` | Total time taken for all redirect steps including name lookup, connect, pretransfer, and transfer before final transaction. | Redirect chain |
| `time_starttransfer` | Time from start until the first byte is received from the server. **This is the key metric for server-side processing time.** | Client ← Server TTFB |
| `time_total` | Total wall-clock time for the entire operation. | End-to-end |

#### Isolating Each Hop

| Metric | Subtract from... | Meaning |
|---|---|---|
| DNS time | `time_namelookup` (standalone) | Pure DNS resolution |
| TCP connect time | `time_connect - time_namelookup` | Pure network RTT |
| TLS time | `time_appconnect - time_connect` | TLS handshake duration |
| Server processing | `time_starttransfer - time_pretransfer` | Server think time + queuing + DB + upstream calls |
| Transfer time | `time_total - time_starttransfer` | Data download (bandwidth-limited) |

### curl Flags Reference

```bash
curl -X POST https://api.example.com/v1/orders \          # Method override
  -H "Authorization: Bearer $TOKEN" \                      # Custom header
  -H "Content-Type: application/json" \                    # Content type
  -H "Idempotency-Key: abc-123-def" \                       # Idempotency
  -d '{"product_id": 42, "quantity": 1}' \                 # POST body
  -w "\ntotal: %{time_total}s\n" \                         # Timing
  -o /dev/null -s \                                         # Silent, discard body
  --connect-timeout 5 \                                     # TCP connect timeout
  --max-time 10 \                                           # Total timeout
  -k \                                                      # Ignore TLS errors (dev only!)
  -L \                                                      # Follow redirects
  --resolve "api.example.com:443:10.0.1.5" \               # Bypass DNS, point to IP
  --cert /path/to/client.crt --key /path/to/client.key      # mTLS
```

---

## curl Timing Diagnostic Scenario

### Scenario: "E-commerce checkout API takes 5 seconds, but app code reports 500ms"

**Context:** The monitoring dashboard shows p99 latency at 5.2s for the `/checkout` endpoint. The backend team says their application code executes in under 500ms. Where is the other 4.7s spent?

**Run the diagnostic:**

```bash
curl -w "\n\
time_namelookup:  %{time_namelookup}\n\
time_connect:     %{time_connect}\n\
time_appconnect:  %{time_appconnect}\n\
time_pretransfer: %{time_pretransfer}\n\
time_starttransfer: %{time_starttransfer}\n\
time_total:       %{time_total}\n" \
  -o /dev/null -s \
  -X POST -H "Content-Type: application/json" \
  -d '{"cart_id": "cart_abc", "payment_token": "tok_xyz"}' \
  https://api.example.com/v1/checkout
```

**Results:**

```
time_namelookup:  0.001
time_connect:     0.003
time_appconnect:  0.050
time_pretransfer: 0.051
time_starttransfer: 5.201
time_total:       5.210
```

**Analysis:**

| Metric | Value | Diagnostic |
|---|---|---|
| `time_namelookup` | 0.001s | DNS is fast — not a DNS problem |
| `time_connect - time_namelookup` | 0.002s | Network RTT is 2ms — not a network problem |
| `time_appconnect - time_connect` | 0.047s | TLS handshake is 47ms — normal |
| `time_pretransfer - time_appconnect` | 0.001s | Preflight is fast — not a protocol negotiation issue |
| `time_starttransfer - time_pretransfer` | **5.150s** | **Server processing time is 5.15 seconds** |
| `time_total - time_starttransfer` | 0.009s | Response body download is fast (small payload) |

**Diagnosis:** The 5.15s gap between `time_pretransfer` and `time_starttransfer` is server processing time — the time from when the request is fully sent until the first response byte arrives. This means:
- The network, DNS, and TLS are healthy.
- The application code times 500ms, but the server is spending 4.65s in middleware, queuing, or waiting on upstream services.
- Possible culprits: slow DB query with connection pool exhaustion, slow upstream gRPC call to inventory service, or thread pool saturation at the API layer.

**Next Steps:** Enable distributed tracing on the `/checkout` endpoint. Check spans for DB queries, cache lookups, and upstream calls. The slowest span will identify the bottleneck.

---

## Request/Response Cycle — Full Hop Breakdown

```
Client (browser/mobile)
  │
  │  1. DNS Resolution (time_namelookup)
  │     Resolve api.example.com → 10.1.2.3
  ▼
DNS Server
  │
  │  2. TCP Connection (time_connect)
  │     SYN → SYN-ACK → ACK
  │     Client:ephemeral_port ←→ Server:443
  ▼
CDN / Edge (optional)
  │  - TLS Termination
  │  - Static asset caching
  │  - DDoS protection (Cloudflare/Akamai/Fastly)
  │  - WAF rules applied
  │
  ▼
Load Balancer (ALB/ELB/Nginx/HAProxy)
  │  - SSL termination (if not done at CDN)
  │  - Health check routing
  │  - Sticky sessions (if configured)
  │  - Rate limiting counters
  │  - Request queuing (surge queue)
  │  - TLS re-encryption (to backend)
  │
  ▼
API Gateway (Kong/Ambassador/AWS API GW/Apigee)
  │  - Authentication (API key, JWT, OAuth2)
  │  - Authorization (RBAC, scopes)
  │  - Request validation (schema)
  │  - Request/response transformation
  │  - Rate limiting / throttling
  │  - Analytics / logging
  │  - Routing to correct upstream service
  │
  ▼
Service (container/pod)
  │  - Deserialization (JSON → object)
  │  - Input validation
  │  - Business logic
  │  - AuthZ (fine-grained permissions)
  │  - Upstream calls:
  │       ├── Internal service (gRPC/HTTP)
  │       ├── Database query (PostgreSQL/MySQL)
  │       ├── Cache lookup (Redis/Memcached)
  │       ├── Message queue publish (Kafka/SQS)
  │       └── External API call (payment gateway, email service)
  │  - Response serialization (object → JSON)
  │
  ▼
Response flows back through same chain (reverse):
  Service → API Gateway → LB → CDN → Client
```

### Time Budgeting

For a target p95 of 200ms end-to-end:

| Hop | Budget |
|---|---|
| DNS | 5ms |
| TCP + TLS | 20ms |
| LB + API Gateway | 15ms |
| Service processing | 120ms |
| DB query | 30ms |
| Response serialization | 5ms |
| CDN/LB reverse path | 5ms |

If any hop exceeds its budget, that hop becomes the bottleneck that needs optimization.

---

## Idempotency

### Definition

| Method | Idempotent? | Safe? | Notes |
|---|---|---|---|
| GET | Yes | Yes | Repeated identical GETs return same response, no side effects |
| HEAD | Yes | Yes | Like GET but no body |
| OPTIONS | Yes | Yes | Metadata about available methods |
| PUT | Yes | No | Full resource replacement; same body → same result |
| DELETE | Yes | No | First call deletes (200/204), subsequent calls return 404 |
| POST | **No** | No | Each call creates a new resource **unless idempotency key is used** |
| PATCH | **No** | No | Partial update; repeated application may differ (e.g., `increment counter`) |

### Idempotency Key Pattern (Stripe Model)

**Request:**
```http
POST /v1/charges HTTP/1.1
Host: api.stripe.com
Authorization: Bearer sk_live_xxxx
Idempotency-Key: 8d9b09e0-5ebf-4e6c-9c9a-3e615e7b4e3a
Content-Type: application/x-www-form-urlencoded

amount=2000&currency=usd&source=tok_visa
```

**Server logic:**
1. Extract `Idempotency-Key` from request header.
2. Check idempotency cache (Redis with TTL of 24 hours): key → {status, body}.
3. If found → return cached response (same status code and body).
4. If not found → process the request, store result in cache, return response.
5. Concurrent requests with the same key: the first one acquired lock and processes; subsequent ones block and return the cached result.

### Scenario: Duplicate Orders from Double-Click

**Problem:** User clicks "Place Order" button, sees no immediate response (spinner), clicks again. Two POST requests reach the server → two orders created → customer charged twice, inventory decremented twice.

**Root Cause:** Client submits order form via `POST /orders`. No idempotency protection on the server.

**Fix:**

**Client side:** Generate UUID on page load for the order form. Disable button after first click.
```javascript
const idempotencyKey = crypto.randomUUID();
fetch('/api/orders', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Idempotency-Key': idempotencyKey
  },
  body: JSON.stringify(orderData)
});
```

**Server side (pseudo):**
```python
idempotency_key = request.headers.get('Idempotency-Key')
if cached := redis.get(f"idem:{idempotency_key}"):
    return cached  # Return previously computed response
# Acquire distributed lock
with redis.lock(f"lock:idem:{idempotency_key}", timeout=30):
    if cached := redis.get(f"idem:{idempotency_key}"):
        return cached  # Another request finished while we waited
    result = process_order(request.body)
    redis.setex(f"idem:{idempotency_key}", 86400, result)
    return result
```

**Alert signal:** Sudden spike in order count where `count(today) > 2 * count(yesterday_same_hour)` with same user_id appearing within 5 seconds.

---

## Pagination

### Cursor-Based Pagination (Recommended)

```
GET /api/products?cursor=eyJpZCI6NDJ9&limit=20
```

**Response:**
```json
{
  "data": [...],
  "paging": {
    "next": "eyJpZCI6NjJ9",
    "has_more": true
  }
}
```

**Characteristics:**
- Cursor is an opaque, base64-encoded reference (e.g., last-seen ID + timestamp).
- Stable: even if new rows are inserted between page fetches, cursor position doesn't shift.
- Requires indexed column for efficient `WHERE id > ? ORDER BY id LIMIT ?`.
- Cannot jump to arbitrary page (no "page 7 of 100" without scanning all previous pages).

### Offset-Based Pagination

```
GET /api/products?offset=40&limit=20
```

**Characteristics:**
- Simple: offset = page_number * page_size.
- Problem: "phantom reads." If an item is inserted at position 10 while user is on page 2, all subsequent items shift by 1.
  - Item at position 20 moves to position 21.
  - Page 2 fetches items 21-40 → user sees the item **twice** (once on page 1, once on page 2).
  - If an item is deleted at position 5, user **skips** an item.
- Performance: `OFFSET 1000000` requires the DB to scan and discard 1M rows (slow query).

### Scenario: Duplicate Products on Different Pages

**Problem:** "User scrolls product catalog on mobile. Sees the same Nike Air Max on page 2 and page 3. Some products are missing entirely."

**Reproduction:**
```bash
# Page 2
curl "https://api.shop.com/products?offset=20&limit=20" | jq '.data[].id'
# Returns: [21, 22, 23, 24, 25, ...]
# Wait 2 seconds (new products inserted by CMS)
# Page 3
curl "https://api.shop.com/products?offset=40&limit=20" | jq '.data[].id'
# Returns: [40, 21, 42, 43, ...]  ← Product 21 appeared again, product 41 is missing
```

**Root Cause:** Offset-based pagination with frequent inserts. Products were added at IDs 30-35 while user was reading page 2.

**Fix:** Switch to cursor-based pagination using `WHERE id > :last_seen_id ORDER BY id ASC LIMIT 20`. Cursor is stable because it references an absolute position. Items inserted "behind" the cursor are invisible; items inserted "ahead" are naturally included in the next page.

---

## Rate Limiting

### Algorithm Comparison

| Algorithm | Burst Tolerance | Smoothing | Edge Effect | Implementation Complexity |
|---|---|---|---|---|
| Token Bucket | Yes (bucket size allows bursts) | No | None | Low |
| Leaky Bucket | No (queue drains at fixed rate) | Yes | None | Medium |
| Fixed Window | Yes (at window start) | No | **Yes** (double rate at boundary) | Very Low |
| Sliding Window Log | Yes | Moderate | None | Medium |
| Sliding Window Counter | Yes | Moderate | Minimal | Medium |

### Token Bucket

- Bucket has `capacity` (max tokens) and refills at `rate` (tokens/second).
- Burst: client can use up to `capacity` tokens at once, then refills at `rate`.
- Example: 100 tokens capacity, 10 tokens/sec refill → burst of 100 requests allowed, sustained 10 req/s.

**Implementation (pseudo-Redis):**
```lua
-- Lua script for atomic token bucket in Redis
local key = KEYS[1]
local rate = tonumber(ARGV[1])     -- tokens per second
local capacity = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])

local bucket = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens = tonumber(bucket[1]) or capacity
local last_refill = tonumber(bucket[2]) or now

local elapsed = now - last_refill
tokens = math.min(capacity, tokens + elapsed * rate)
last_refill = now

if tokens >= requested then
    tokens = tokens - requested
    redis.call('HMSET', key, 'tokens', tokens, 'last_refill', last_refill)
    redis.call('EXPIRE', key, 60)
    return 1  -- allowed
else
    redis.call('HMSET', key, 'tokens', tokens, 'last_refill', last_refill)
    redis.call('EXPIRE', key, 60)
    return 0  -- denied
end
```

### Retry-After Header

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 30
Content-Type: application/json

{"error": "rate_limit_exceeded", "retry_after_seconds": 30}
```

Two formats:
- `Retry-After: 30` — seconds until retry is allowed.
- `Retry-After: Thu, 11 Jun 2026 14:30:00 GMT` — absolute HTTP-date.

**Client must parse both formats.**

### Scenario: Fixed Window Boundary Attack

**Problem:** API allows 100 requests per minute. Client sends 100 requests at 12:00:59 and another 100 at 12:01:00. Total: 200 requests within 2 seconds.

**Root Cause:** Fixed window resets at minute boundary. Counter for window `[12:00:00, 12:01:00)` allows 100; counter for `[12:01:00, 12:02:00)` allows another 100. Two bursts at boundary consume 2x quota.

**Fix:** Sliding window. For each request, count requests in the last 60 seconds (not calendar-aligned 60-second window). Use `ZADD` + `ZCOUNT` in Redis with timestamps as scores.

---

## Retry Logic

### Exponential Backoff with Jitter

```
sleep = min(cap, base * 2^attempt) + random(0, sleep/2)
```

| Attempt | Base Sleep | Jitter Range | Effective Sleep |
|---|---|---|---|
| 0 (first retry) | 1s | 0–0.5s | 1.0–1.5s |
| 1 | 2s | 0–1.0s | 2.0–3.0s |
| 2 | 4s | 0–2.0s | 4.0–6.0s |
| 3 | 8s | 0–4.0s | 8.0–12.0s |
| 4 | 16s (cap 30s) | 0–8.0s | 16.0–24.0s |
| 5 | 30s (capped) | 0–15.0s | 30.0–45.0s |

**Why jitter is non-negotiable:**

### Scenario: Thundering Herd Retry

**Problem:** Production incident at 3 AM. Load balancer health check fails for 2 seconds due to momentary network blip. All 500 application instances detect the failure simultaneously. Without jitter, every instance retries after exactly 1 second.

**Consequence:**
- `t=0s`: 500 requests fail.
- `t=1s`: 500 retries hit the recovering service at the exact same millisecond → service crashes → 500 more retries at `t=2s` → cascade failure.
- Service never recovers because retry waves hammer it at synchronized intervals.

**With jitter:** 500 retries spread across `t=1.0s` to `t=1.5s` (approx 333 retries/sec). Service has time to process between bursts and recovers.

**Implementation rule:** Always add jitter to retry delay. Never use `sleep = base * 2^attempt` without random jitter.

### Retry Safety

**Only retry idempotent operations.** Do NOT retry:
- POST without idempotency key (creates duplicates)
- Non-idempotent PATCH (`increment counter`)
- Operations with side effects (send email, charge credit card)

**Retryable HTTP status codes:**
- `429 Too Many Requests` (honor Retry-After)
- `502 Bad Gateway`
- `503 Service Unavailable`
- `504 Gateway Timeout`

**Non-retryable:**
- `400 Bad Request` (fix the client, retrying won't help)
- `401 Unauthorized` / `403 Forbidden` (fix auth, retrying won't help)
- `404 Not Found` (resource doesn't exist)
- `422 Unprocessable Entity` (validation error)

---

## API Gateway Issues

### Timeout Chain Configuration

```
Client timeout:     35s
    ↓
API Gateway:        30s (gateway_idle_timeout)
    ↓
Service A:          20s (upstream_read_timeout)
    ↓
Service B (gRPC):   10s (deadline)
    ↓
Database:            5s (statement_timeout)
```

**Problem:** If Service B times out at 10s, Service A's 20s timeout never fires cleanly. The API gateway at 30s is the final line of defense. Mismatched timeouts cause:
- API gateway returns 504 to client while Service A is still processing (wasted resources).
- Service A retries the upstream call while the gateway has already aborted the client connection.
- DB connections held for 20s by Service A even though the DB statement was cancelled at 5s.

**Rule:** Ensure timeouts cascade correctly: **Client > Gateway > Service > Upstream > DB.** Each hop must have a shorter timeout than the caller above it.

### Request/Response Transformation Errors

API Gateways often transform requests/responses. Common failure modes:
- Gateway adds `X-Forwarded-For` but strips original client IP.
- Gateway converts JSON body but mangles nested objects.
- Gateway rewrites URL path and drops query parameters.
- Gateway enforces response schema; service returns unexpected field → 502 to client.

**Debug:** Compare what the gateway logs vs. what the service logs. Use `--trace-ascii` on curl to see exactly what the gateway sends upstream.

---

## 5xx Spike Playbook

```
1. Confirm scope:
   curl -w "%{http_code}" -o /dev/null -s https://api.example.com/health
   # If 200, issue is specific endpoint. If 5xx, issue is systemic.

2. Check recent deploys:
   kubectl rollout history deployment/orders-service
   git log --oneline -10

3. Check upstream dependencies:
   # DB connection pool
   SHOW PROCESSLIST;  -- MySQL
   SELECT count(*) FROM pg_stat_activity WHERE state = 'active';  -- PostgreSQL

   # Redis
   redis-cli PING
   redis-cli INFO stats | grep rejected_connections

   # DB query latency
   SELECT query, mean_exec_time, calls FROM pg_stat_statements
     ORDER BY mean_exec_time DESC LIMIT 10;

4. Check thread/connection pool saturation:
   # JVM
   jstack <pid> | grep -c "BLOCKED"
   # Go
   curl localhost:6060/debug/pprof/goroutine?debug=1

5. Kill switch if needed:
   kubectl scale deployment/orders-service --replicas=10  # Scale up
   # Or circuit-break failing upstream
   curl -X POST https://gateway.internal/circuit-breaker/upstream-payment/OPEN
```

---

## Code Examples

### Python: Resilient REST Client

```python
import random
import time
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def parse_retry_after(header_value: str) -> float:
    try:
        return float(header_value)
    except ValueError:
        from email.utils import parsedate_to_datetime
        return (parsedate_to_datetime(header_value) - datetime.datetime.now(datetime.timezone.utc)).total_seconds()


class ResilientSession(requests.Session):
    def __init__(
        self,
        total_retries: int = 3,
        backoff_factor: float = 0.5,
        status_forcelist: tuple = (429, 502, 503, 504),
    ):
        super().__init__()
        retry_strategy = Retry(
            total=total_retries,
            backoff_factor=backoff_factor,
            status_forcelist=status_forcelist,
            allowed_methods={"HEAD", "GET", "PUT", "DELETE", "OPTIONS"},
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.mount("https://", adapter)
        self.mount("http://", adapter)

    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", (3.05, 10))  # (connect, read)
        kwargs.setdefault("verify", True)  # Never disable in production
        return super().request(method, url, **kwargs)


def request_with_exponential_backoff(
    method: str,
    url: str,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    **kwargs,
) -> requests.Response:
    last_exception: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            response = requests.request(
                method, url,
                timeout=(3.05, 10),
                **kwargs,
            )
            if response.status_code in (429, 502, 503, 504) and attempt < max_retries:
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        sleep_time = parse_retry_after(retry_after)
                    else:
                        sleep_time = min(max_delay, base_delay * (2 ** attempt))
                else:
                    sleep_time = min(max_delay, base_delay * (2 ** attempt))
                jitter = random.uniform(0, sleep_time / 2)
                time.sleep(sleep_time + jitter)
                continue
            response.raise_for_status()
            return response
        except (requests.ConnectionError, requests.Timeout) as e:
            last_exception = e
            if attempt < max_retries:
                sleep_time = min(max_delay, base_delay * (2 ** attempt))
                jitter = random.uniform(0, sleep_time / 2)
                time.sleep(sleep_time + jitter)
    raise last_exception or RuntimeError("Max retries exceeded")


session = ResilientSession()

response = session.get(
    "https://api.example.com/v1/orders/12345",
    headers={"Authorization": "Bearer sk_test_xxx"},
)
print(f"Status: {response.status_code}")
print(f"RateLimit-Remaining: {response.headers.get('X-RateLimit-Remaining', 'N/A')}")
print(f"Body: {response.json()}")
```

### Java: Resilient HTTP Client (Java 11+)

```java
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.http.HttpResponse.BodyHandlers;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Random;
import java.util.concurrent.CompletableFuture;

public class ResilientHttpClient {

    private static final Random RANDOM = new Random();
    private static final List<Integer> RETRYABLE_STATUSES = List.of(429, 502, 503, 504);

    private final HttpClient client;
    private final int maxRetries;
    private final long baseDelayMs;
    private final long maxDelayMs;

    public ResilientHttpClient(int maxRetries, long baseDelayMs, long maxDelayMs) {
        this.maxRetries = maxRetries;
        this.baseDelayMs = baseDelayMs;
        this.maxDelayMs = maxDelayMs;
        this.client = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(3))
                .version(HttpClient.Version.HTTP_2)
                .followRedirects(HttpClient.Redirect.NORMAL)
                .build();
    }

    public CompletableFuture<HttpResponse<String>> sendWithRetry(HttpRequest request) {
        return sendWithRetry(request, 0);
    }

    private CompletableFuture<HttpResponse<String>> sendWithRetry(HttpRequest request, int attempt) {
        CompletableFuture<HttpResponse<String>> future = client.sendAsync(request, BodyHandlers.ofString());

        if (attempt >= maxRetries) {
            return future;
        }

        return future.thenCompose(response -> {
            int status = response.statusCode();

            if (RETRYABLE_STATUSES.contains(status)) {
                long delay = computeDelay(attempt);
                System.out.printf(
                    "[WARN] Retryable status %d, retrying in %dms (attempt %d/%d)%n",
                    status, delay, attempt + 1, maxRetries
                );
                return CompletableFuture
                        .supplyAsync(() -> {
                            sleepUnchecked(delay);
                            return null;
                        })
                        .thenCompose(v -> sendWithRetry(request, attempt + 1));
            }
            return CompletableFuture.completedFuture(response);
        }).exceptionallyCompose(ex -> {
            if (attempt < maxRetries) {
                long delay = computeDelay(attempt);
                System.out.printf(
                    "[WARN] Exception '%s', retrying in %dms (attempt %d/%d)%n",
                    ex.getMessage(), delay, attempt + 1, maxRetries
                );
                return CompletableFuture
                        .supplyAsync(() -> {
                            sleepUnchecked(delay);
                            return null;
                        })
                        .thenCompose(v -> sendWithRetry(request, attempt + 1));
            }
            throw new RuntimeException("Max retries exhausted after " + maxRetries + " attempts", ex);
        });
    }

    private long computeDelay(int attempt) {
        long exponentialDelay = Math.min(maxDelayMs, baseDelayMs * (1L << attempt));
        long jitter = (long) (RANDOM.nextDouble() * exponentialDelay / 2);
        return exponentialDelay + jitter;
    }

    private void sleepUnchecked(long millis) {
        try {
            Thread.sleep(millis);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new RuntimeException("Interrupted during backoff", e);
        }
    }

    // --- Wire the whole thing together ---

    public static void main(String[] args) throws Exception {
        ResilientHttpClient resilient = new ResilientHttpClient(3, 1000, 30_000);

        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create("https://api.example.com/v1/orders/12345"))
                .header("Authorization", "Bearer sk_test_xxx")
                .header("Accept", "application/json")
                .timeout(Duration.ofSeconds(10))
                .GET()
                .build();

        Instant start = Instant.now();
        HttpResponse<String> response = resilient.sendWithRetry(request).join();
        Instant end = Instant.now();

        System.out.printf("Status: %d%n", response.statusCode());
        System.out.printf("Duration: %dms%n", Duration.between(start, end).toMillis());
        System.out.printf("Body: %s%n", response.body());
        System.out.printf("Retry-After: %s%n",
            response.headers().firstValue("Retry-After").orElse("N/A"));
    }
}
```

### JavaScript: Axios with Interceptors and Exponential Backoff

```javascript
const axios = require('axios');

const RETRYABLE_STATUSES = new Set([429, 502, 503, 504]);
const MAX_RETRIES = 3;
const BASE_DELAY_MS = 1000;
const MAX_DELAY_MS = 30000;

function parseRetryAfter(value) {
  const seconds = Number(value);
  if (!isNaN(seconds)) return seconds * 1000;
  const date = new Date(value);
  if (!isNaN(date.getTime())) return Math.max(0, date.getTime() - Date.now());
  return null;
}

function computeDelay(attempt) {
  const exponential = Math.min(MAX_DELAY_MS, BASE_DELAY_MS * Math.pow(2, attempt));
  const jitter = Math.random() * exponential * 0.5;
  return exponential + jitter;
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

const apiClient = axios.create({
  timeout: 10000,
  headers: { 'Accept': 'application/json' },
});

// --- Request interceptor (inject idempotency key) ---
apiClient.interceptors.request.use((config) => {
  config.metadata = { startTime: Date.now(), retryCount: 0 };
  if (config.method === 'post' && !config.headers['Idempotency-Key']) {
    // Only generate if not provided
    // config.headers['Idempotency-Key'] = crypto.randomUUID();
  }
  return config;
});

// --- Response interceptor (retry logic) ---
apiClient.interceptors.response.use(
  (response) => {
    const duration = Date.now() - response.config.metadata.startTime;
    console.log(`[INFO] ${response.config.method} ${response.config.url} ${response.status} ${duration}ms`);
    return response;
  },
  async (error) => {
    const config = error.config;
    if (!config || !config.metadata) {
      return Promise.reject(error);
    }

    const retryCount = config.metadata.retryCount || 0;
    const status = error.response?.status;

    if (
      retryCount >= MAX_RETRIES ||
      !status ||
      !RETRYABLE_STATUSES.has(status)
    ) {
      console.error(
        `[ERROR] ${config.method} ${config.url} ${status || 'NETWORK_ERROR'} ` +
        `after ${retryCount} retries`
      );
      return Promise.reject(error);
    }

    let delay;
    if (status === 429 && error.response.headers['retry-after']) {
      const parsed = parseRetryAfter(error.response.headers['retry-after']);
      delay = parsed != null ? parsed : computeDelay(retryCount);
    } else {
      delay = computeDelay(retryCount);
    }

    console.log(
      `[WARN] ${config.method} ${config.url} → ${status}. ` +
      `Retrying in ${Math.round(delay)}ms (attempt ${retryCount + 1}/${MAX_RETRIES})`
    );

    config.metadata.retryCount = retryCount + 1;
    await sleep(delay);
    return apiClient(config);
  }
);

// --- Usage ---
async function getOrder(orderId) {
  try {
    const { data } = await apiClient.get(
      `https://api.example.com/v1/orders/${orderId}`,
      { headers: { 'Authorization': 'Bearer sk_test_xxx' } }
    );
    return data;
  } catch (err) {
    if (err.response) {
      console.error(`Order fetch failed: HTTP ${err.response.status}`, err.response.data);
    } else if (err.request) {
      console.error('Order fetch failed: No response received', err.message);
    } else {
      console.error('Order fetch failed: Request setup error', err.message);
    }
    throw err;
  }
}

async function createOrder(orderData) {
  try {
    const { data } = await apiClient.post(
      'https://api.example.com/v1/orders',
      orderData,
      {
        headers: {
          'Authorization': 'Bearer sk_test_xxx',
          'Idempotency-Key': crypto.randomUUID(),
        },
      }
    );
    return data;
  } catch (err) {
    console.error('Order creation failed', err.response?.data || err.message);
    throw err;
  }
}

module.exports = { apiClient, getOrder, createOrder };
```
