# gRPC Troubleshooting

> **Category:** API | gRPC | Microservices
> **Difficulty:** Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#grpc` `#protobuf` `#microservices` `#oncall`

---

## gRPC Status Codes — Full Reference

gRPC defines 17 canonical status codes (0–16). Unlike REST where status codes are in the HTTP layer, gRPC status codes are embedded in the HTTP/2 trailer (the `grpc-status` header). The `grpc-message` header carries a human-readable description.

### 0 — OK

| Property | Value |
|---|---|
| HTTP Mapping | 200 |
| Trigger | Operation completed successfully |
| Diagnostic | Not an error — normal operation |
| Scenario | Unary RPC returned expected response |

```bash
# grpcurl success
grpcurl -d '{"name":"Alice"}' -plaintext localhost:50051 \
  example.UserService/GetUser
# Returns JSON response — status 0 (OK)
```

---

### 1 — CANCELLED

| Property | Value |
|---|---|
| HTTP Mapping | 499 (Client Closed Request) |
| Trigger | Client cancelled the call (deadline passed, context cancelled, or explicit Cancel) |
| Diagnostic | Check client-side logs for context cancellation source |
| Scenario | Mobile user switches tabs, client cancels in-flight gRPC call |

**Scenario:** A mobile app makes a gRPC call to fetch order history. The user navigates to another screen, and the client context is cancelled:

```java
// Client cancels
context.cancel(new RuntimeException("User navigated away"));
```

**Server logs:** `CANCELLED: Client cancelled the request`. This is **normal behavior**, not an error requiring investigation. Distinguish from server-side cancellations.

**Fix:** If you see a spike in CANCELLED, check if client-side timeouts are too aggressive (e.g., 100ms deadline for an operation that averages 150ms).

---

### 2 — UNKNOWN

| Property | Value |
|---|---|
| HTTP Mapping | 500 |
| Trigger | Exception handler returned a non-gRPC error, or the error was not properly wrapped |
| Diagnostic | Check the `cause` field; search server logs for uncaught exceptions near the timestamp |
| Scenario | Server throws `NullPointerException` but the gRPC interceptor doesn't catch it properly |

**Fix:** Always wrap exceptions in a proper gRPC status:

```java
// Bad:
throw new RuntimeException("DB connection lost");

// Good:
throw Status.UNAVAILABLE
    .withDescription("Database connection lost")
    .withCause(new RuntimeException("DB connection lost"))
    .asRuntimeException();
```

---

### 3 — INVALID_ARGUMENT

| Property | Value |
|---|---|
| HTTP Mapping | 400 |
| Trigger | Client sent a request that is syntactically correct but semantically invalid |
| Diagnostic | Check `grpc-message` for details on the invalid field; validate proto field types and ranges |
| Scenario | Proto `int32` field receives value `3000000000` (exceeds `2^31-1`), or required field is missing |

**Proto:**
```protobuf
message CreateOrderRequest {
    int32 quantity = 1;   // max is 2,147,483,647
    string product_id = 2;
}
```

**Client sends:**
```json
{"quantity": 3000000000, "product_id": "abc"}
```

**Response:** `INVALID_ARGUMENT: quantity value 3000000000 out of range for int32`

**Debugging:**
```bash
grpcurl -d '{"quantity": 5, "product_id": "abc"}' \
  -plaintext localhost:50051 Orders/CreateOrder
```

---

### 4 — DEADLINE_EXCEEDED

| Property | Value |
|---|---|
| HTTP Mapping | 504 |
| Trigger | Operation did not complete within the deadline specified by the client |
| Diagnostic | Check timeline: client deadline vs. server processing time. Is deadline realistic for the operation? |
| Scenario | Service A calls Service B with 500ms deadline, but Service B takes 800ms due to cold start |

**Detailed Scenario:** Order service calls inventory service with a 500ms deadline. Inventory service has a cold-start JVM (JIT compilation, connection pool initialization) that takes 800ms for the first request after deployment.

**Diagnosis:**
```
Timeline:
  t=0ms    Service A sends request to Service B (deadline: 500ms)
  t=500ms  gRPC deadline fires on Service A side → DEADLINE_EXCEEDED raised
  t=800ms  Service B finally responds (response is discarded)
  t=800ms+ Service B wasted 800ms of CPU processing a request nobody cares about
```

**Fix:**
1. Increase deadline for cold-start scenarios (e.g., 2s instead of 500ms).
2. Use warm-up probes before declaring service ready.
3. Check deadline propagation — is each hop correctly subtracting its processing time?

---

### 5 — NOT_FOUND

| Property | Value |
|---|---|
| HTTP Mapping | 404 |
| Trigger | Requested resource (entity) does not exist |
| Diagnostic | Check if resource ID is correct; check if resource was deleted by another process |
| Scenario | `GetUser(id=999)` — no user with ID 999 exists |

**Differentiate from PERMISSION_DENIED:** If a user exists but the caller shouldn't see it, return `PERMISSION_DENIED`, not `NOT_FOUND` (prevents information leakage: "does this user exist?" enumeration).

---

### 6 — ALREADY_EXISTS

| Property | Value |
|---|---|
| HTTP Mapping | 409 |
| Trigger | Attempt to create a resource that already exists |
| Diagnostic | Check if client is retrying a `Create` call; check if idempotency key was used |
| Scenario | `CreateUser(id=42)` — user 42 already exists. Duplicate POST from retry without idempotency key. |

---

### 7 — PERMISSION_DENIED

| Property | Value |
|---|---|
| HTTP Mapping | 403 |
| Trigger | Caller authenticated but does not have permission to access the resource |
| Diagnostic | Check RBAC roles, scopes, and resource-level permissions |
| Scenario | User with "viewer" role tries `UpdateOrder`. Auth succeeded, but the role lacks write permission. |

**Note:** Use PERMISSION_DENIED (not UNAUTHENTICATED) when the caller has valid credentials but insufficient privileges.

---

### 8 — RESOURCE_EXHAUSTED

| Property | Value |
|---|---|
| HTTP Mapping | 429 |
| Trigger | Resource quota has been exhausted (rate limit, memory, connections, disk) |
| Diagnostic | Check rate limit counters, DB connection pool, memory metrics |
| Scenario | Client sending 10,000 req/s hits server-side rate limiter. Server out of memory — cannot accept new requests. |

**Rate limit response metadata:**
```java
responseObserver.onError(
    Status.RESOURCE_EXHAUSTED
        .withDescription("Rate limit exceeded. Quota: 100 req/s")
        .asRuntimeException()
);
// Include retry delay in trailing metadata
Metadata trailers = new Metadata();
trailers.put(
    Metadata.Key.of("retry-delay-ms", Metadata.ASCII_STRING_MARSHALLER),
    "1000"
);
```

---

### 9 — FAILED_PRECONDITION

| Property | Value |
|---|---|
| HTTP Mapping | 400 |
| Trigger | Operation was rejected because the system is not in the required state |
| Diagnostic | Check the state of the resource before the operation |
| Scenario | `DeleteOrder(id=123)` — order 123 is in "PROCESSING" state, can only delete orders in "PENDING" or "COMPLETED" state. |

**Example:**
```python
if order.status == OrderStatus.PROCESSING:
    context.abort(grpc.StatusCode.FAILED_PRECONDITION,
                  f"Cannot delete order {order.id}: status is {order.status}")
```

---

### 10 — ABORTED

| Property | Value |
|---|---|
| HTTP Mapping | 409 |
| Trigger | Concurrency conflict — optimistic locking failure, transaction serialization error |
| Diagnostic | Check if multiple writers are contending for the same resource |
| Scenario | Two simultaneous writes to same record; second writer's version check fails, gets ABORTED. |

**Detailed Scenario:**

```
Client A and Client B both read Order #42 with version=7.

Client A writes: "UPDATE orders SET status='SHIPPED', version=8
                   WHERE id=42 AND version=7" → SUCCESS (rows_affected=1)

Client B writes: "UPDATE orders SET status='CANCELLED', version=8
                   WHERE id=42 AND version=7" → FAILS (rows_affected=0, version already 8)

Server returns ABORTED to Client B.
```

**Client handling:**
```java
} catch (StatusRuntimeException e) {
    if (e.getStatus().getCode() == Status.Code.ABORTED) {
        // Re-read the entity and retry the operation with the new version
        Order fresh = orderService.getOrder(orderId);
        orderService.updateOrderWithRetry(fresh);
    }
}
```

---

### 11 — OUT_OF_RANGE

| Property | Value |
|---|---|
| HTTP Mapping | 400 |
| Trigger | Operation attempted past the valid range (e.g., pagination beyond available pages) |
| Diagnostic | Check if client is requesting page numbers or offsets beyond what's available |
| Scenario | `ListOrders(page=100)` — only 3 pages of 20 results exist. Requesting page 100 returns OUT_OF_RANGE. |

**Different from FAILED_PRECONDITION** — use OUT_OF_RANGE for numeric range violations, FAILED_PRECONDITION for state violations.

---

### 12 — UNIMPLEMENTED

| Property | Value |
|---|---|
| HTTP Mapping | 501 |
| Trigger | Service does not implement the requested RPC method |
| Diagnostic | Check if client and server are on different proto versions |
| Scenario | New proto method `GetOrderAnalytics` deployed to clients but not yet to the server. |

**Detailed Scenario:**

```
Proto file v2 adds: rpc GetOrderAnalytics(GetOrderAnalyticsRequest)
    returns (GetOrderAnalyticsResponse);

Client (v2) → Server (v1, doesn't know about GetOrderAnalytics)

Client receives: UNIMPLEMENTED: Method Orders/GetOrderAnalytics is not implemented
```

**Fix:** Deploy server updates before client updates (backward compatibility principle: servers must handle unknown methods gracefully; clients must handle UNIMPLEMENTED gracefully).

---

### 13 — INTERNAL

| Property | Value |
|---|---|
| HTTP Mapping | 500 |
| Trigger | Invariant broken — bug, NPE, assertion failure, panic |
| Diagnostic | Sever-side stack trace in logs; check for bugs, race conditions, unhandled edge cases |
| Scenario | `NullPointerException` in business logic: server received a valid request but crashed due to a bug. |

**Rule:** If you're returning INTERNAL, you have a bug. File a ticket. Add a test case for the input that triggered it.

---

### 14 — UNAVAILABLE

| Property | Value |
|---|---|
| HTTP Mapping | 503 |
| Trigger | Service is temporarily unavailable — connection refused, DNS resolution fails, TCP connection reset, or no healthy backend |
| Diagnostic | Check service health; check DNS resolution; check network connectivity; check for recent deploys |
| Scenario | Pod killed during rolling deployment. In-flight requests get UNAVAILABLE as the TCP connection drops. |

**Detailed Scenario — Deployment-Induced Outage:**

```
t=0s:  Kubernetes sends SIGTERM to pod-1.
t=0s:  Service mesh removes pod-1 from load balancer pool.
       But: In-flight gRPC streams are still active on pod-1!
t=1s:  Client on in-flight stream tries to send next message → RST packet
       → UNAVAILABLE: Connection reset by peer

t=0-5s: Between SIGTERM and SIGKILL (terminationGracePeriodSeconds),
        pod-1 connections receive UNAVAILABLE for every call during this window.
```

**Fix:**
1. `terminationGracePeriodSeconds: 30` — give in-flight requests time to complete.
2. `preStop` hook: wait for connection draining (`sleep 25`).
3. Client-side retry with exponential backoff for UNAVAILABLE.

---

### 15 — DATA_LOSS

| Property | Value |
|---|---|
| HTTP Mapping | 500 |
| Trigger | Unrecoverable data loss or corruption |
| Diagnostic | Check persistent storage integrity; check message queue delivery semantics |
| Scenario | Kafka producer with `acks=0` and broker crashes before flushing to disk. Message permanently lost. |

**Should be rare.** If you see DATA_LOSS, escalate to P0.

---

### 16 — UNAUTHENTICATED

| Property | Value |
|---|---|
| HTTP Mapping | 401 |
| Trigger | No valid credentials (token missing, expired, or invalid signature) |
| Diagnostic | Check JWT expiry; check token signing key; check metadata headers |
| Scenario | Client forgot to attach `authorization` metadata header, or JWT is expired. |

**Metadata enforcement in interceptor:**
```python
def auth_interceptor(continuation, client_call_details, request):
    metadata = []
    for key, value in client_call_details.metadata:
        metadata.append((key, value))
    metadata.append(('authorization', f'Bearer {get_token()}'))
    new_details = _ClientCallDetails(
        client_call_details.method,
        client_call_details.timeout,
        metadata,
        client_call_details.credentials,
        client_call_details.wait_for_ready
    )
    return continuation(new_details, request)
```

---

## Status Code Decision Flowchart

```
Error in RPC handler?
│
├── Is the error caused by client input?
│   ├── Bad format/missing field? → INVALID_ARGUMENT (3)
│   ├── Resource not found? → NOT_FOUND (5)
│   ├── Resource already exists? → ALREADY_EXISTS (6)
│   ├── Value out of valid range? → OUT_OF_RANGE (11)
│   └── Operation invalid for current state? → FAILED_PRECONDITION (9)
│
├── Is the error caused by auth?
│   ├── No credentials? → UNAUTHENTICATED (16)
│   └── Has credentials but lacks permission? → PERMISSION_DENIED (7)
│
├── Is the error related to resource limits?
│   ├── Rate limited? → RESOURCE_EXHAUSTED (8)
│   └── Concurrency conflict? → ABORTED (10)
│
├── Is the error transient / infrastructure?
│   ├── Timeout? → DEADLINE_EXCEEDED (4)
│   ├── Service down / unreachable? → UNAVAILABLE (14)
│   ├── Data lost? → DATA_LOSS (15)
│   ├── Not implemented? → UNIMPLEMENTED (12)
│   └── Client cancelled? → CANCELLED (1)
│
└── None of the above (bug!) → INTERNAL (13)
```

---

## grpcurl Tool

### Installation
```bash
# macOS
brew install grpcurl

# Linux
go install github.com/fullstorydev/grpcurl/cmd/grpcurl@latest

# Or download binary
curl -L https://github.com/fullstorydev/grpcurl/releases/latest/download/grpcurl_$(uname -s)_$(uname -m).tar.gz | tar xz
```

### Operations

```bash
# List all services on the server (requires reflection)
grpcurl -plaintext localhost:50051 list

# List with TLS
grpcurl -cacert ca.crt localhost:50051 list

# List with mTLS
grpcurl -cacert ca.crt -cert client.crt -key client.key localhost:50051 list

# Describe a specific service
grpcurl -plaintext localhost:50051 describe orders.OrderService

# Describe a method
grpcurl -plaintext localhost:50051 describe orders.OrderService.CreateOrder

# Invoke a unary RPC
grpcurl -d '{"product_id": "prod_123", "quantity": 2}' \
  -plaintext localhost:50051 \
  orders.OrderService/CreateOrder

# Invoke with metadata (headers)
grpcurl -H "authorization: Bearer $TOKEN" \
  -H "x-request-id: $(uuidgen)" \
  -d '{"product_id": "prod_123", "quantity": 2}' \
  -plaintext localhost:50051 \
  orders.OrderService/CreateOrder

# Include response headers/trailers
grpcurl -v -d '{"product_id": "prod_123"}' \
  -plaintext localhost:50051 \
  orders.OrderService/CreateOrder

# Set a deadline
grpcurl -connect-timeout 5s \
  -max-time 10s \
  -d '{}' -plaintext localhost:50051 orders.OrderService/ListAllOrders

# Server reflection via proto file (when server lacks reflection)
grpcurl -import-path ./protos -proto orders.proto \
  -d '{"product_id": "prod_123"}' \
  -plaintext localhost:50051 \
  orders.OrderService/CreateOrder

# Server stream
grpcurl -d '{"page_size": 100}' \
  -plaintext localhost:50051 \
  orders.OrderService/StreamOrders

# Client stream (provide messages via stdin)
echo '{"product_id":"a"}{"product_id":"b"}{"product_id":"c"}' | \
  grpcurl -d @ -plaintext localhost:50051 orders.OrderService/BatchCreateOrders
```

---

## Protobuf Evolution Pitfalls

### Field Numbers — The Cardinal Rule

```protobuf
// v1
message Order {
    int64 order_id = 1;
    string status = 2;
    string customer_email = 3;
    double total_amount = 4;
}

// v2 — BAD: Reused field number 3
message Order {
    int64 order_id = 1;
    string status = 2;
    int64 customer_id = 3;    // Was customer_email (string), now customer_id (int64)
    double total_amount = 4;
}
```

**Catastrophic failure:** A v1 client sends `customer_email = "alice@example.com"`. The v2 server reads field 3 as `int64`, deserializes the wire bytes of the string as an integer → silent data corruption. No error is raised because protobuf is backward-compatible at the wire level.

**Fix:** Never reuse field numbers. Use `reserved`:

```protobuf
message Order {
    int64 order_id = 1;
    string status = 2;

    reserved 3;
    reserved "customer_email";
    // Or: reserved 3, 5, 7 to 10;

    int64 customer_id = 5;  // New field gets a new number
    double total_amount = 4;
}
```

### Adding a Required Field

```protobuf
// v2: Adding a required field
message Order {
    int64 order_id = 1;
    string status = 2;
    string tracking_number = 5;  // New field — OK, old clients just won't send it

    // DON'T DO THIS without migration:
    // bool is_gift = 6 [required=true];  // Breaks old clients that don't know about is_gift
}
```

**Rule for adding fields:**
- Adding a new field is safe (old clients ignore it; server uses default value if absent).
- Never mark a new field as required without a migration plan (all old clients will fail).

### Changing Field Types

| Original Type | Changed To | Safe? | Notes |
|---|---|---|---|
| `int32` | `int64` | **Yes** (wire compatible) | Old client sends int32, new server reads as int64 |
| `int64` | `int32` | **No** | Data loss: value > 2^31-1 will be truncated |
| `string` | `bytes` | **Yes** (wire compatible) | Same wire encoding (UTF-8 bytes) |
| `bytes` | `string` | **Risky** | Bytes might not be valid UTF-8 |
| `fixed32` | `sfixed32` | **Yes** | Same wire size |
| `enum` | `enum` (add value) | **Yes** | Old client ignores unknown enum values |
| `enum` | `int32` | **No** | Different wire encoding |
| `bool` | `int32` | **Yes** (wire compatible in proto3) | |

---

## Deadline Propagation

### How Deadlines Cascade

```
Client sets deadline: 1000ms
         │
         ▼
    Service A receives request with remaining: 1000ms
         │
         ├── Processes for: 50ms
         │   Remaining: 950ms
         │
         ├── Calls Service B with deadline: 950ms
         │        │
         │        ▼
         │   Service B receives request with remaining: 950ms
         │        │
         │        ├── Processes for: 30ms
         │        │   Remaining: 920ms
         │        │
         │        ├── Calls Service C with deadline: 920ms
         │        │        │
         │        │        ▼
         │        │   Service C receives request with remaining: 920ms
         │        │        │
         │        │        ├── DB query takes: 150ms  ✓ (within budget)
         │        │        └── Returns to Service B at t=180ms from start
         │        │
         │        └── Returns to Service A at t=210ms from start
         │
         └── Returns to Client at t=260ms from start
```

### Deadline Exceeded — Chain Failure

```
Client sets deadline: 500ms
         │
         ▼
    Service A: processes 50ms, remaining 450ms
         │
         ├── Service B: processes 100ms, remaining 350ms
         │        │
         │        └── Service C: starts processing at t=150ms, remaining 350ms
         │                       │
         │                       └── DB query runs for 400ms
         │                           At t=500ms: DEADLINE_EXCEEDED
```

**Critical detail:** When `Service C` hits the deadline at 500ms:
1. Service C aborts its DB query.
2. Service C returns DEADLINE_EXCEEDED to Service B.
3. Service B should NOT retry Service C (the deadline already expired for the root request).
4. Service B should propagate DEADLINE_EXCEEDED up to Service A.
5. Service A returns DEADLINE_EXCEEDED to the client.

If Service B retries Service C, it wastes resources on a request whose root context has already been cancelled.

### Implementation in Code

**Python:**
```python
import grpc
import time

def call_with_deadline(stub, request, timeout_ms):
    deadline = time.time() + timeout_ms / 1000.0
    try:
        response = stub.MyMethod(request, timeout=timeout_ms / 1000.0)
        return response
    except grpc.RpcError as e:
        if e.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
            print(f"Call exceeded deadline of {timeout_ms}ms")
        raise
```

**Propagating deadline to downstream calls:**
```python
def forward_deadline(context, stub, request):
    # Extract remaining time from incoming context
    remaining = context.time_remaining()  # seconds
    if remaining is not None and remaining <= 0:
        context.abort(grpc.StatusCode.DEADLINE_EXCEEDED, "Deadline already expired")

    # Subtract your processing overhead
    processing_overhead = 0.05  # 50ms
    downstream_deadline = remaining - processing_overhead

    if downstream_deadline <= 0:
        context.abort(grpc.StatusCode.DEADLINE_EXCEEDED,
                      f"Insufficient time remaining: {remaining}s")

    try:
        return stub.MyMethod(request, timeout=downstream_deadline)
    except grpc.RpcError as e:
        if e.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
            context.set_code(grpc.StatusCode.DEADLINE_EXCEEDED)
            context.set_details("Upstream call timed out after deferred propagation")
        raise
```

---

## Streaming Issues

### Stream Types

| Type | Client Sends | Server Sends | Use Case |
|---|---|---|---|
| Unary | 1 message | 1 message | Simple RPC |
| Server Streaming | 1 message | 0..N messages | Download large result set |
| Client Streaming | 0..N messages | 1 message | Upload file chunks |
| Bidi Streaming | 0..N messages | 0..N messages | Chat, real-time collaboration |

### Flow Control — Server Stream Overrun

**Scenario:** "Server sends 1M messages via server stream. Client only processes 100 messages/sec. Client memory grows unbounded."

```
Server (fast) ─────▶ Client (slow)
  1M msg/s             100 msg/s

Client-side buffer (64KB default in gRPC Java)
fills up → backpressure signal to server
Server gRPC library pauses sending until client drains buffer
```

**Problem occurs when:** Client code does not process messages from the stream in a timely manner. The gRPC library buffers messages internally. If the client's `onNext` handler is slow (blocking I/O, heavy computation), the internal buffer fills up, and the server is backpressured — but the client already has thousands of messages queued in heap.

**Fix:** Client should process stream messages asynchronously or throttle the stream:

```java
// Client-side: use reactive stream with backpressure
StreamObserver<OrderResponse> responseObserver = new StreamObserver<>() {
    @Override
    public void onNext(OrderResponse response) {
        // Process asynchronously
        executor.submit(() -> processOrder(response));
        // Request next batch only when current batch is processed
        stub.request(1);
    }
    // ...
};
```

**Server-side: Check client readiness with `onReady`:**
```java
public void onNext(OrderResponse response) {
    if (responseObserver.isReady()) {
        responseObserver.onNext(response);
    } else {
        // Buffer or drop
        pendingMessages.add(response);
        responseObserver.setOnReadyHandler(() -> drainBuffer());
    }
}
```

---

## Load Balancing

### HTTP/2 Multiplexing Breaks L4 LB

**The Problem:**

```
        ┌──────────────────────┐
        │      L4 LB (TCP)    │
        │  (HAProxy/NLB)      │
        └──────┬──────────────┘
               │  Single TCP connection
               │  ┌──────────────────────────┐
               │  │ Stream 1, Stream 2, .....│
               │  │ Stream 1000              │
               │  └──────────────────────────┘
               ▼
        ┌──────────────┐
        │ Backend Pod A│  (All 1000 streams go here!)
        └──────────────┘
        ┌──────────────┐
        │ Backend Pod B│  (Idle — no streams)
        └──────────────┘
        ┌──────────────┐
        │ Backend Pod C│  (Idle — no streams)
        └──────────────┘
```

**Root Cause:** gRPC uses HTTP/2, which multiplexes many concurrent streams over a single TCP connection. An L4 load balancer routes at the TCP connection level — it sees one connection and sends all traffic to the same backend. The other backends sit idle.

**Result:** Uneven load distribution. Backend Pool A handles 100% of traffic while B and C handle 0%.

### Solutions

**1. L7 Load Balancer (Envoy, Linkerd, Traefik):**
- Terminates HTTP/2 at the proxy.
- Routes individual gRPC requests (streams) to different backends based on load.
- Envoy has native gRPC load balancing via xDS protocol.

```yaml
# Envoy configuration excerpt
clusters:
  - name: order-service
    type: STRICT_DNS
    lb_policy: ROUND_ROBIN
    http2_protocol_options: {}
    hosts:
      - socket_address:
          address: order-service
          port_value: 50051
```

**2. Client-Side Load Balancing with Resolver:**
- Client resolves service name to multiple backend addresses.
- Client maintains connections to multiple backends.
- Client chooses backend per call (e.g., round-robin).

```java
// gRPC Java with client-side LB
ManagedChannel channel = ManagedChannelBuilder
    .forTarget("dns:///order-service.internal:50051")
    .defaultLoadBalancingPolicy("round_robin")
    .usePlaintext()
    .build();
```

**3. Connection Pooling (per-backend):**
- Client opens N TCP connections to each backend.
- Spread streams across all connections and backends.

---

## Channel State Machine

```
           ┌─────────┐
           │  IDLE   │  (initial state, no TCP connection)
           └────┬────┘
                │  Call arrives
                ▼
        ┌───────────────┐
        │  CONNECTING   │  (DNS resolution, TCP handshake, TLS negotiation)
        └───────┬───────┘
                │
         ┌──────┴──────┐
         │              │
         ▼              ▼
   ┌──────────┐   ┌────────────────────┐
   │  READY   │   │ TRANSIENT_FAILURE  │
   │ (healthy │   │ (connection lost,  │
   │  active) │   │  DNS failure,      │
   └────┬─────┘   │  TLS error, etc.)  │
        │         └────────┬───────────┘
        │                  │
        │    After backoff  │
        │    (10s default)  │
        │                  ▼
        │         ┌───────────────┐
        └────────▶│  CONNECTING   │  (reconnect attempt)
                  └───────────────┘
        │
        │  channel.shutdown()
        ▼
   ┌──────────┐
   │ SHUTDOWN │  (graceful close)
   └──────────┘
```

**Observing states (Java):**
```java
ConnectivityState state = channel.getState(false);
switch (state) {
    case READY:
        System.out.println("Channel is healthy");
        break;
    case TRANSIENT_FAILURE:
        System.out.println("Channel is failing, will retry");
        break;
    case CONNECTING:
        System.out.println("Channel is connecting/reconnecting");
        break;
    case SHUTDOWN:
        System.out.println("Channel is shut down");
        break;
}
```

**Watching state changes:**
```java
channel.notifyWhenStateChanged(
    channel.getState(false),
    () -> System.out.println("State changed to: " + channel.getState(false))
);
```

---

## Code Examples

### Python: gRPC Client with Deadline, Metadata Interceptor, Retry Interceptor

```python
import logging
import random
import time
from contextlib import contextmanager

import grpc
from google.protobuf import json_format

# Example protos — replace with your generated code
# import orders_pb2
# import orders_pb2_grpc

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# --- Metadata Injector (Auth + Tracing) ---
class MetadataInterceptor(grpc.UnaryUnaryClientInterceptor):
    def __init__(self, get_token_fn, service_name: str):
        self._get_token = get_token_fn
        self._service_name = service_name

    def intercept_unary_unary(self, continuation, client_call_details, request):
        metadata = list(client_call_details.metadata) if client_call_details.metadata else []
        metadata.append(("authorization", f"Bearer {self._get_token()}"))
        metadata.append(("x-service-name", self._service_name))
        metadata.append(("x-request-id", _generate_request_id()))

        new_details = _ClientCallDetails(
            method=client_call_details.method,
            timeout=client_call_details.timeout,
            metadata=metadata,
            credentials=client_call_details.credentials,
            wait_for_ready=client_call_details.wait_for_ready,
        )
        return continuation(new_details, request)


class _ClientCallDetails(
    grpc.ClientCallDetails,
    grpc.StreamStreamClientInterceptor,
):
    def __init__(self, method, timeout, metadata, credentials, wait_for_ready):
        self.method = method
        self.timeout = timeout
        self.metadata = metadata
        self.credentials = credentials
        self.wait_for_ready = wait_for_ready


def _generate_request_id() -> str:
    import uuid
    return str(uuid.uuid4())


# --- Retry Interceptor for UNAVAILABLE with Exponential Backoff ---
class RetryInterceptor(grpc.UnaryUnaryClientInterceptor):
    RETRYABLE_CODES = {
        grpc.StatusCode.UNAVAILABLE,
        grpc.StatusCode.DEADLINE_EXCEEDED,
        grpc.StatusCode.RESOURCE_EXHAUSTED,
        grpc.StatusCode.ABORTED,
    }

    def __init__(self, max_retries: int = 3, base_delay_ms: int = 1000, max_delay_ms: int = 30000):
        self.max_retries = max_retries
        self.base_delay_ms = base_delay_ms
        self.max_delay_ms = max_delay_ms

    def intercept_unary_unary(self, continuation, client_call_details, request):
        last_status = None

        for attempt in range(self.max_retries + 1):
            try:
                start = time.time()
                response = continuation(client_call_details, request)
                elapsed = time.time() - start
                logger.info(f"gRPC call succeeded in {elapsed*1000:.0f}ms (attempt {attempt})")
                return response
            except grpc.RpcError as e:
                code = e.code()
                elapsed = time.time() - start
                last_status = code

                if code not in self.RETRYABLE_CODES:
                    logger.error(f"Non-retryable gRPC error: {code} — {e.details()}")
                    raise

                if attempt >= self.max_retries:
                    logger.error(f"Max retries ({self.max_retries}) exhausted. Last error: {code}")
                    raise

                delay = self._compute_delay(attempt)
                logger.warning(
                    f"Retryable gRPC error: {code} — {e.details()}. "
                    f"Retrying in {delay:.0f}ms (attempt {attempt+1}/{self.max_retries})"
                )
                time.sleep(delay / 1000.0)

        raise grpc.RpcError(f"Retries exhausted. Last status: {last_status}")

    def _compute_delay(self, attempt: int) -> float:
        exponential = min(self.max_delay_ms, self.base_delay_ms * (2 ** attempt))
        jitter = random.uniform(0, exponential * 0.3)
        return exponential + jitter


# --- Channel Factory ---
def create_grpc_channel(
    target: str,
    get_token_fn,
    service_name: str = "unknown",
    max_retries: int = 3,
    tls: bool = True,
) -> grpc.Channel:
    interceptors = [
        MetadataInterceptor(get_token_fn, service_name),
        RetryInterceptor(max_retries=max_retries),
    ]

    if tls:
        credentials = grpc.ssl_channel_credentials()
    else:
        credentials = None  # insecure — dev only

    return grpc.intercept_channel(
        grpc.secure_channel(target, credentials) if tls else grpc.insecure_channel(target),
        *interceptors,
    )


# --- Client Helper with Error Mapping ---
def call_with_error_mapping(stub_method, request, timeout_ms: int = 5000):
    """Maps gRPC status codes to meaningful exceptions."""
    try:
        return stub_method(request, timeout=timeout_ms / 1000.0)
    except grpc.RpcError as e:
        code = e.code()
        if code == grpc.StatusCode.DEADLINE_EXCEEDED:
            logger.error(f"DEADLINE_EXCEEDED after {timeout_ms}ms: {e.details()}")
            raise TimeoutError(f"Call exceeded {timeout_ms}ms deadline") from e
        elif code == grpc.StatusCode.UNAVAILABLE:
            logger.error(f"Service UNAVAILABLE: {e.details()}")
            raise ConnectionError(f"Service unavailable: {e.details()}") from e
        elif code == grpc.StatusCode.RESOURCE_EXHAUSTED:
            retry_delay = _extract_metadata(e, "retry-delay-ms")
            logger.warning(f"Rate limited. Retry after {retry_delay}ms")
            raise RateLimitError(retry_delay) from e
        elif code == grpc.StatusCode.PERMISSION_DENIED:
            logger.error(f"PERMISSION_DENIED: {e.details()}")
            raise PermissionError(e.details()) from e
        elif code == grpc.StatusCode.UNAUTHENTICATED:
            logger.error(f"UNAUTHENTICATED: {e.details()}")
            raise PermissionError("Authentication required") from e
        elif code == grpc.StatusCode.NOT_FOUND:
            raise KeyError(f"Not found: {e.details()}") from e
        elif code == grpc.StatusCode.INTERNAL:
            logger.error(f"INTERNAL server error: {e.details()}")
            raise RuntimeError(f"Internal server error: {e.details()}") from e
        else:
            logger.error(f"gRPC error {code}: {e.details()}")
            raise


def _extract_metadata(exception: grpc.RpcError, key: str) -> str:
    try:
        return dict(exception.trailing_metadata()).get(key, "unknown")
    except Exception:
        return "unknown"


class RateLimitError(Exception):
    def __init__(self, retry_after_ms: str):
        self.retry_after_ms = retry_after_ms
        super().__init__(f"Rate limited. Retry after {retry_after_ms}ms")


# --- Usage Example ---
# channel = create_grpc_channel(
#     "orders.internal:50051",
#     get_token_fn=lambda: "my-jwt-token",
#     service_name="payment-service",
# )
# stub = orders_pb2_grpc.OrderServiceStub(channel)
#
# try:
#     response = call_with_error_mapping(
#         stub.CreateOrder,
#         orders_pb2.CreateOrderRequest(product_id="prod_123", quantity=2),
#         timeout_ms=800,
#     )
#     print(f"Order created: {response.order_id}")
# except TimeoutError:
#     print("Order creation timed out. Circuit breaker opened.")
# except ConnectionError:
#     print("Order service is down. Falling back to async queue.")
# except RateLimitError as e:
#     time.sleep(int(e.retry_after_ms) / 1000.0)
#     # Retry after delay
```

### Java: ManagedChannel with Deadline, Retry Policy, Error Handling

```java
package com.example.grpc;

import io.grpc.*;
import io.grpc.stub.MetadataUtils;

import java.util.Random;
import java.util.Set;
import java.util.concurrent.TimeUnit;
import java.util.function.Supplier;

public class ResilientGrpcClient {

    private static final Set<Status.Code> RETRYABLE_CODES = Set.of(
        Status.Code.UNAVAILABLE,
        Status.Code.DEADLINE_EXCEEDED,
        Status.Code.RESOURCE_EXHAUSTED,
        Status.Code.ABORTED
    );

    private final ManagedChannel channel;
    private final int maxRetries;
    private final long baseDelayMs;
    private final long maxDelayMs;
    private final Random random = new Random();

    /**
     * Creates a managed channel with interceptors for auth and retries.
     */
    public static ManagedChannel createChannel(
            String target,
            Supplier<String> tokenSupplier,
            String serviceName
    ) {
        return ManagedChannelBuilder.forTarget(target)
                .useTransportSecurity()
                .defaultServiceConfig(buildServiceConfig())
                .intercept(new AuthInterceptor(tokenSupplier, serviceName))
                .intercept(new TracingInterceptor())
                .build();
    }

    private static Map<String, Object> buildServiceConfig() {
        return Map.of(
            "methodConfig", List.of(Map.of(
                "name", List.of(Map.of("service", "", "method", "")),
                "retryPolicy", Map.of(
                    "maxAttempts", 4,
                    "initialBackoff", "0.5s",
                    "maxBackoff", "30s",
                    "backoffMultiplier", 3,
                    "retryableStatusCodes", List.of(
                        "UNAVAILABLE", "DEADLINE_EXCEEDED"
                    )
                ),
                "timeout", "5s"
            ))
        );
    }

    /**
     * Executes a call with manual retry and error classification.
     */
    public static <ReqT, RespT> RespT execute(
            Supplier<RespT> call,
            int maxRetries,
            long baseDelayMs,
            long maxDelayMs,
            String operationName
    ) {
        StatusException lastException = null;

        for (int attempt = 0; attempt <= maxRetries; attempt++) {
            try {
                long start = System.currentTimeMillis();
                RespT response = call.get();
                long elapsed = System.currentTimeMillis() - start;
                System.out.printf("[INFO] %s succeeded in %dms (attempt %d)%n",
                    operationName, elapsed, attempt);
                return response;
            } catch (StatusRuntimeException e) {
                Status.Code code = e.getStatus().getCode();
                lastException = e;

                if (!RETRYABLE_CODES.contains(code)) {
                    throw mapToApplicationException(e);
                }

                if (attempt >= maxRetries) {
                    System.err.printf("[ERROR] %s failed after %d retries: %s%n",
                        operationName, maxRetries, code);
                    throw mapToApplicationException(e);
                }

                long delay = computeDelay(attempt, baseDelayMs, maxDelayMs);
                System.err.printf("[WARN] %s returned %s. Retrying in %dms (attempt %d/%d)%n",
                    operationName, code, delay, attempt + 1, maxRetries);

                try {
                    Thread.sleep(delay);
                } catch (InterruptedException ie) {
                    Thread.currentThread().interrupt();
                    throw new RuntimeException("Interrupted during backoff", ie);
                }
            }
        }

        throw mapToApplicationException(lastException);
    }

    public static RuntimeException mapToApplicationException(StatusRuntimeException e) {
        Status.Code code = e.getStatus().getCode();
        return switch (code) {
            case DEADLINE_EXCEEDED -> new TimeoutException("Call exceeded deadline", e);
            case UNAVAILABLE -> new ServiceUnavailableException("Service unavailable", e);
            case RESOURCE_EXHAUSTED -> new RateLimitException("Rate limited", e);
            case PERMISSION_DENIED -> new ForbiddenException("Permission denied", e);
            case UNAUTHENTICATED -> new AuthenticationException("Authentication required", e);
            case NOT_FOUND -> new ResourceNotFoundException("Resource not found", e);
            case INVALID_ARGUMENT -> new BadRequestException("Invalid argument: " +
                e.getStatus().getDescription(), e);
            case ALREADY_EXISTS -> new ConflictException("Resource already exists", e);
            case ABORTED -> new ConflictException("Concurrency conflict — retry", e);
            case INTERNAL -> new InternalServerException("Internal server error", e);
            default -> new RuntimeException("gRPC error: " + code, e);
        };
    }

    private static long computeDelay(int attempt, long baseDelayMs, long maxDelayMs) {
        Random random = new Random();
        long exponential = Math.min(maxDelayMs, baseDelayMs * (1L << attempt));
        long jitter = (long) (random.nextDouble() * exponential * 0.3);
        return exponential + jitter;
    }


    // --- Custom Exception Hierarchy ---
    public static class TimeoutException extends RuntimeException {
        public TimeoutException(String msg, Throwable cause) { super(msg, cause); }
    }
    public static class ServiceUnavailableException extends RuntimeException {
        public ServiceUnavailableException(String msg, Throwable cause) { super(msg, cause); }
    }
    public static class RateLimitException extends RuntimeException {
        public RateLimitException(String msg, Throwable cause) { super(msg, cause); }
    }
    public static class ForbiddenException extends RuntimeException {
        public ForbiddenException(String msg, Throwable cause) { super(msg, cause); }
    }
    public static class AuthenticationException extends RuntimeException {
        public AuthenticationException(String msg, Throwable cause) { super(msg, cause); }
    }
    public static class ResourceNotFoundException extends RuntimeException {
        public ResourceNotFoundException(String msg, Throwable cause) { super(msg, cause); }
    }
    public static class BadRequestException extends RuntimeException {
        public BadRequestException(String msg, Throwable cause) { super(msg, cause); }
    }
    public static class ConflictException extends RuntimeException {
        public ConflictException(String msg, Throwable cause) { super(msg, cause); }
    }
    public static class InternalServerException extends RuntimeException {
        public InternalServerException(String msg, Throwable cause) { super(msg, cause); }
    }


    // --- Interceptors ---
    static class AuthInterceptor implements ClientInterceptor {
        private final Supplier<String> tokenSupplier;
        private final String serviceName;

        AuthInterceptor(Supplier<String> tokenSupplier, String serviceName) {
            this.tokenSupplier = tokenSupplier;
            this.serviceName = serviceName;
        }

        @Override
        public <ReqT, RespT> ClientCall<ReqT, RespT> interceptCall(
                MethodDescriptor<ReqT, RespT> method,
                CallOptions callOptions,
                Channel next
        ) {
            return new ForwardingClientCall.SimpleForwardingClientCall<>(
                    next.newCall(method, callOptions)
            ) {
                @Override
                public void start(Listener<RespT> responseListener, Metadata headers) {
                    headers.put(
                        Metadata.Key.of("authorization", Metadata.ASCII_STRING_MARSHALLER),
                        "Bearer " + tokenSupplier.get()
                    );
                    headers.put(
                        Metadata.Key.of("x-service-name", Metadata.ASCII_STRING_MARSHALLER),
                        serviceName
                    );
                    super.start(responseListener, headers);
                }
            };
        }
    }

    static class TracingInterceptor implements ClientInterceptor {
        @Override
        public <ReqT, RespT> ClientCall<ReqT, RespT> interceptCall(
                MethodDescriptor<ReqT, RespT> method,
                CallOptions callOptions,
                Channel next
        ) {
            return new ForwardingClientCall.SimpleForwardingClientCall<>(
                    next.newCall(method, callOptions)
            ) {
                @Override
                public void start(Listener<RespT> responseListener, Metadata headers) {
                    headers.put(
                        Metadata.Key.of("x-request-id", Metadata.ASCII_STRING_MARSHALLER),
                        java.util.UUID.randomUUID().toString()
                    );
                    super.start(responseListener, headers);
                }
            };
        }
    }


    // --- Main: example usage ---
    public static void main(String[] args) {
        ManagedChannel channel = createChannel(
            "orders.internal:50051",
            () -> "my-jwt-token",
            "payment-service"
        );

        try {
            // OrderServiceGrpc.OrderServiceBlockingStub stub =
            //     OrderServiceGrpc.newBlockingStub(channel)
            //         .withDeadlineAfter(5, TimeUnit.SECONDS);
            //
            // CreateOrderRequest request = CreateOrderRequest.newBuilder()
            //     .setProductId("prod_123")
            //     .setQuantity(2)
            //     .build();
            //
            // CreateOrderResponse response = ResilientGrpcClient.execute(
            //     () -> stub.createOrder(request),
            //     3, 1000, 30_000,
            //     "CreateOrder"
            // );
            // System.out.println("Order created: " + response.getOrderId());
            System.out.println("Channel created. Uncomment stub usage with generated proto code.");
        } finally {
            channel.shutdownNow();
        }
    }
}
```

### JavaScript: @grpc/grpc-js Client with Deadline, Interceptors, Error Handling

```javascript
const grpc = require('@grpc/grpc-js');
const protoLoader = require('@grpc/proto-loader');

const RETRYABLE_CODES = new Set([
  grpc.status.UNAVAILABLE,
  grpc.status.DEADLINE_EXCEEDED,
  grpc.status.RESOURCE_EXHAUSTED,
  grpc.status.ABORTED,
]);

function createDeadline(ms) {
  return new Date(Date.now() + ms);
}

function computeDelay(attempt, baseDelayMs = 1000, maxDelayMs = 30000) {
  const exponential = Math.min(maxDelayMs, baseDelayMs * Math.pow(2, attempt));
  const jitter = Math.random() * exponential * 0.3;
  return exponential + jitter;
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function mapGrpcError(err) {
  const code = err.code;
  const details = err.details || err.message;

  switch (code) {
    case grpc.status.DEADLINE_EXCEEDED:
      return new TimeoutError(`Call exceeded deadline: ${details}`);
    case grpc.status.UNAVAILABLE:
      return new ServiceUnavailableError(`Service unavailable: ${details}`);
    case grpc.status.RESOURCE_EXHAUSTED:
      return new RateLimitError(`Rate limited: ${details}`);
    case grpc.status.PERMISSION_DENIED:
      return new ForbiddenError(`Permission denied: ${details}`);
    case grpc.status.UNAUTHENTICATED:
      return new AuthenticationError(`Not authenticated: ${details}`);
    case grpc.status.NOT_FOUND:
      return new NotFoundError(`Not found: ${details}`);
    case grpc.status.INVALID_ARGUMENT:
      return new BadRequestError(`Invalid argument: ${details}`);
    case grpc.status.ALREADY_EXISTS:
      return new ConflictError(`Already exists: ${details}`);
    case grpc.status.ABORTED:
      return new ConflictError(`Concurrency conflict: ${details}`);
    case grpc.status.INTERNAL:
      return new InternalError(`Internal server error: ${details}`);
    default:
      return err;
  }
}

class TimeoutError extends Error { constructor(m) { super(m); this.name = 'TimeoutError'; } }
class ServiceUnavailableError extends Error { constructor(m) { super(m); this.name = 'ServiceUnavailableError'; } }
class RateLimitError extends Error { constructor(m) { super(m); this.name = 'RateLimitError'; } }
class ForbiddenError extends Error { constructor(m) { super(m); this.name = 'ForbiddenError'; } }
class AuthenticationError extends Error { constructor(m) { super(m); this.name = 'AuthenticationError'; } }
class NotFoundError extends Error { constructor(m) { super(m); this.name = 'NotFoundError'; } }
class BadRequestError extends Error { constructor(m) { super(m); this.name = 'BadRequestError'; } }
class ConflictError extends Error { constructor(m) { super(m); this.name = 'ConflictError'; } }
class InternalError extends Error { constructor(m) { super(m); this.name = 'InternalError'; } }

class ResilientGrpcClient {
  constructor(target, credentials = grpc.credentials.createSsl()) {
    this.target = target;
    this.client = new grpc.Client(target, credentials, {
      'grpc.keepalive_time_ms': 30000,
      'grpc.keepalive_timeout_ms': 10000,
      'grpc.keepalive_permit_without_calls': 1,
    });

    this.client.waitForReady(Date.now() + 5000, (err) => {
      if (err) {
        console.error(`[gRPC] Failed to connect to ${target}: ${err.message}`);
      } else {
        console.log(`[gRPC] Connected to ${target}`);
      }
    });
  }

  unaryCall(methodPath, serialize, deserialize, request, options = {}) {
    return this._unaryCallWithRetry(methodPath, serialize, deserialize, request, 0, options);
  }

  async _unaryCallWithRetry(methodPath, serialize, deserialize, request, attempt, options) {
    const maxRetries = options.maxRetries ?? 3;
    const deadline = options.deadlineMs ?? 5000;
    const token = options.token ?? null;
    const requestId = options.requestId ?? generateRequestId();

    const metadata = new grpc.Metadata();
    if (token) metadata.add('authorization', `Bearer ${token}`);
    metadata.add('x-request-id', requestId);
    if (options.idempotencyKey) metadata.add('idempotency-key', options.idempotencyKey);

    return new Promise((resolve, reject) => {
      const startTime = Date.now();

      this.client.makeUnaryRequest(
        methodPath,
        serialize,
        deserialize,
        request,
        metadata,
        { deadline: createDeadline(deadline) },
        (err, response) => {
          const elapsed = Date.now() - startTime;

          if (err) {
            const code = err.code;
            console.error(
              `[gRPC] ${methodPath} failed: ${code} — ${err.details} ` +
              `(${elapsed}ms, attempt ${attempt + 1})`
            );

            if (!RETRYABLE_CODES.has(code) || attempt >= maxRetries) {
              reject(mapGrpcError(err));
              return;
            }

            const delay = computeDelay(attempt);
            console.warn(
              `[gRPC] ${methodPath} retrying in ${Math.round(delay)}ms ` +
              `(attempt ${attempt + 1}/${maxRetries})`
            );

            sleep(delay).then(() => {
              this._unaryCallWithRetry(
                methodPath, serialize, deserialize, request, attempt + 1, options
              ).then(resolve, reject);
            });
            return;
          }

          console.log(`[gRPC] ${methodPath} succeeded in ${elapsed}ms (attempt ${attempt + 1})`);
          resolve(response);
        }
      );
    });
  }

  close() {
    this.client.close();
    console.log(`[gRPC] Connection closed to ${this.target}`);
  }
}

function generateRequestId() {
  return `${Date.now()}-${Math.random().toString(36).substring(2, 10)}`;
}

// --- Example usage with proto-loaded stubs ---
// const PROTO_PATH = './protos/orders.proto';
// const packageDefinition = protoLoader.loadSync(PROTO_PATH, {
//   keepCase: true,
//   longs: String,
//   enums: String,
//   defaults: true,
//   oneofs: true,
// });
// const ordersProto = grpc.loadPackageDefinition(packageDefinition).orders;
//
// const client = new ResilientGrpcClient('orders.internal:50051');
//
// async function createOrder(productId, quantity) {
//   const request = { product_id: productId, quantity };
//   const methodPath = '/orders.OrderService/CreateOrder';
//   const serialize = (req) =>
//       ordersProto.CreateOrderRequest.encode(req).finish();
//   const deserialize = (buf) =>
//       ordersProto.CreateOrderResponse.decode(buf);
//
//   try {
//     const response = await client.unaryCall(
//       methodPath, serialize, deserialize, request,
//       { deadlineMs: 5000, token: 'my-jwt-token', idempotencyKey: crypto.randomUUID() }
//     );
//     return response;
//   } catch (err) {
//     if (err instanceof TimeoutError) {
//       console.error('Order creation timed out');
//       // Fall back to async / circuit-break
//     } else if (err instanceof ServiceUnavailableError) {
//       console.error('Order service is down');
//     }
//     throw err;
//   }
// }

module.exports = {
  ResilientGrpcClient,
  computeDelay,
  mapGrpcError,
  TimeoutError,
  ServiceUnavailableError,
  RateLimitError,
  ForbiddenError,
  AuthenticationError,
  NotFoundError,
  BadRequestError,
  ConflictError,
  InternalError,
};
```
