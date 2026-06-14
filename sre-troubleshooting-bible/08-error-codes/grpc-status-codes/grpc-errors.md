# gRPC Status Codes
> **Category:** gRPC | API | Error Codes
> **Difficulty:** Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#grpc` `#protobuf` `#microservices` `#oncall`

---

## Overview

gRPC uses a well-defined set of 17 status codes that are richer than HTTP status codes. Every gRPC response includes a status code and an optional error message. Unlike HTTP where you need to infer meaning from numeric ranges (4xx vs 5xx), each gRPC code has precise semantics.

The gRPC status codes are defined in [`google.rpc.Code`](https://github.com/googleapis/googleapis/blob/master/google/rpc/code.proto) and the error model at [`google.rpc.Status`](https://github.com/googleapis/googleapis/blob/master/google/rpc/status.proto).

---

## Complete Status Code Reference

### 0 — OK

- **HTTP mapping**: 200
- **Meaning**: The operation completed successfully.
- **What triggers it**: Normal, non-error response.
- **What to check**: Nothing — this is the success case.
- **Real scenario**: A `GetUser` RPC returns the user profile successfully.

---

### 1 — CANCELLED

- **HTTP mapping**: 499 (client closed request)
- **Meaning**: The operation was cancelled, typically by the caller.
- **What triggers it**:
  - Client called `context.cancel()` or the client-streaming stub called `cancel()`
  - The client's deadline was exceeded and the gRPC runtime cancelled in-flight requests
  - gRPC bidirectional stream where one side cancelled
- **What to check**:
  - Is the client setting an appropriate deadline?
  - Is the client cancelling intentionally, or is a framework doing it?
  - Are there mobile clients backgrounding and cancelling?
- **Example fix**: Increase the deadline on the client side if the operation is legitimately long-running.
- **Real scenario**: "Mobile app sends a `SearchProducts` RPC with a 2-second deadline. The search service takes 2.5 seconds during peak. gRPC runtime cancels the RPC at exactly 2 seconds and the client code receives `CANCELLED`. The search may have completed on the server but the response was discarded."

---

### 2 — UNKNOWN

- **HTTP mapping**: 500
- **Meaning**: An unknown error occurred. Typically used when the error doesn't fit any other status code, or when a panic/recover handler catches something unexpected.
- **What triggers it**:
  - Unhandled exception in the gRPC handler that the framework catches
  - `panic()` recovery in Go gRPC server
  - Errors from libraries that don't map to gRPC codes
- **What to check**: Server-side logs for the exception. Treat as an unhandled error.
- **Example fix**: Wrap the handler in proper exception handling that maps known exceptions to specific gRPC codes.
- **Real scenario**: "A nil pointer dereference in the handler panics. The gRPC interceptor catches the panic and returns UNKNOWN. The client just sees 'unknown error' with no actionable detail."

---

### 3 — INVALID_ARGUMENT

- **HTTP mapping**: 400
- **Meaning**: The client specified an invalid argument. Unlike `FAILED_PRECONDITION`, this indicates arguments that are problematic regardless of system state.
- **What triggers it**:
  - Required protobuf field not set
  - Field value out of valid range (e.g., negative page_size)
  - Malformed string field (e.g., invalid email format)
  - Enum field set to an unrecognized value (protobuf defaults to 0, which may not be a valid enum value)
- **What to check**:
  - Server-side validation logic — log the full request to see which field failed
  - The protobuf field being sent — is the client using an outdated `.proto`?
  - Enum values — protobuf's zero-value problem with enums
- **Example fix**: Return a structured error in the `details` field of `google.rpc.Status` using `BadRequest` from `google.rpc.error_details`.
- **Real scenario**: "A new field `discount_percent` is added to the `CreateOrderRequest` proto with range `[0, 100]`. The mobile team updates the proto but doesn't update their code, so the field is sent as 0 (protobuf default). The server rejects with `INVALID_ARGUMENT: discount_percent must be between 1 and 100`. All orders from the old mobile version fail."

---

### 4 — DEADLINE_EXCEEDED

- **HTTP mapping**: 504
- **Meaning**: The deadline expired before the operation could complete. This is the server-side equivalent of `CANCELLED` when the server noticed the deadline passed.
- **What triggers it**:
  - Client set a deadline that's shorter than the operation takes
  - Deadline propagation through the service mesh — an upstream's deadline is already close to expiring when it calls downstream
  - Database query running longer than the deadline
- **What to check**:
  - The deadline budget for the entire call chain
  - Are deadlines being properly propagated?
  - Which service in the chain consumed most of the deadline budget?
- **Example fix**: Increase the client deadline, optimize the slow service, or return partial results before the deadline.
- **Real scenario**: "Client calls `ProcessOrder` with a 5-second deadline. Service A takes 1s, calls Service B (takes 2s), which calls Service C. By the time Service C receives the request, only 2s remain. Service C's DB query takes 3s. Service C returns `DEADLINE_EXCEEDED` at exactly the 5-second mark."

---

### 5 — NOT_FOUND

- **HTTP mapping**: 404
- **Meaning**: The requested entity was not found.
- **What triggers it**:
  - Resource lookup by ID returned nothing
  - File not found in a storage RPC
  - The entity existed but was deleted
- **What to check**: Is the resource supposed to exist? Check if the ID is correct, if a delete operation ran recently, or if there are data consistency issues.
- **Example fix**: Return NOT_FOUND with resource name in the message. Use `ResourceInfo` from `google.rpc.error_details`.
- **Real scenario**: "A user's profile is requested via `GetUser(user_id='usr_abc')`. The ID exists in the auth database but hasn't been replicated to the profile service yet (eventual consistency delay). The profile service returns NOT_FOUND. Client should retry with backoff."

---

### 6 — ALREADY_EXISTS

- **HTTP mapping**: 409
- **Meaning**: The entity that a client attempted to create already exists.
- **What triggers it**:
  - Duplicate insert on a unique key
  - Creating a resource that already exists (idempotent create)
  - Registering a username that's taken
- **What to check**: Is this a retry of a successful request? Check if the client is using idempotency keys correctly.
- **Example fix**: Use `ALREADY_EXISTS` for idempotent create semantics. The client should treat it as success if the resource matches.
- **Real scenario**: "Microservice A creates a user via `CreateUser`. The request succeeds, but the response is lost due to a network blip. Microservice A retries with the same request. Service B detects the duplicate and returns `ALREADY_EXISTS`. Microservice A treats this as a success because the user was created."

---

### 7 — PERMISSION_DENIED

- **HTTP mapping**: 403
- **Meaning**: The caller does not have permission to execute the specified operation. Unlike `UNAUTHENTICATED`, this means the caller's identity is known.
- **What triggers it**:
  - IAM policy denial
  - Resource-level ACL check failure
  - "You don't own this resource" check
  - API key doesn't have access to the requested API
- **What to check**: The caller's permissions — IAM roles, API key restrictions, resource ACLs. Check if permissions were recently changed.
- **Example fix**: Return `ResourceInfo` and `ErrorInfo` in the details.
- **Real scenario**: "A new team member is added to a project but only given `roles/viewer`. Their code tries to call `UpdateDeployment`. The gRPC interceptor checks IAM and returns `PERMISSION_DENIED`. They need `roles/editor`."

---

### 8 — RESOURCE_EXHAUSTED

- **HTTP mapping**: 429
- **Meaning**: A resource quota has been exhausted, or per-user rate limit has been reached.
- **What triggers it**:
  - Rate limit hit (requests per second / per day)
  - Storage quota exceeded (e.g., 10GB per project)
  - Concurrent operation limit (e.g., max 5 concurrent long-running operations)
  - Memory/CPU allocation exhausted
- **What to check**: Quota dashboards, rate limiter metrics. Check if the client has a retry loop that's burning quota.
- **Example fix**: Return `QuotaFailure` in the `details`. Include the specific quota that was exceeded. Client should respect `RetryInfo`.
- **Real scenario**: "A batch job calls `TranslateText` 10,000 times. The Cloud Translation API has a quota of 6,000 requests per minute. At request 6,001, the API returns `RESOURCE_EXHAUSTED` with `QuotaFailure { quota: 'requests-per-minute', limit: 6000 }`. The batch job must throttle or request a quota increase."

---

### 9 — FAILED_PRECONDITION

- **HTTP mapping**: 400
- **Meaning**: The operation was rejected because the system is not in a state required for the operation's execution. Unlike `INVALID_ARGUMENT`, the request itself may be valid, but the system state prevents it.
- **What triggers it**:
  - Trying to delete a non-empty directory
  - Trying to modify a resource that's being modified by another operation
  - E-Tag / If-Match precondition failure
  - State machine: "can't ship an order that hasn't been paid"
  - Trying to use a resource that's being deleted
- **What to check**: The current state of the resource. Run a GET before the modification to see the state.
- **Example fix**: Return `PreconditionFailure` in the details describing which precondition failed.
- **Real scenario**: "A CI/CD pipeline calls `DeleteEnvironment(env='staging')`. But there are 3 active deployments in staging. The server returns `FAILED_PRECONDITION: environment 'staging' has 3 active deployments. Delete them first or use force=true.`"

---

### 10 — ABORTED

- **HTTP mapping**: 409
- **Meaning**: The operation was aborted, typically due to a concurrency issue like a sequencer check failure or transaction abort. Unlike `FAILED_PRECONDITION`, `ABORTED` signals that the client **should retry** at a higher level.
- **What triggers it**:
  - Optimistic locking failure on a database write
  - Transaction conflict in a distributed transaction
  - CAS (Compare-And-Swap) failure
- **What to check**: The version/sequence number. The client should re-read the current state and retry.
- **Example fix**: Return `RetryInfo` with a suggested backoff delay. Client should re-fetch the resource and re-apply the operation.
- **Real scenario**: "Two servers process the same inventory deduction using optimistic locking. Server A reads version=5, Server B reads version=5. Server A writes: `UPDATE ... SET version=6 WHERE version=5` — succeeds. Server B writes: `UPDATE ... SET version=6 WHERE version=5` — fails (0 rows). Server B returns `ABORTED` to its caller, which retries with the updated version."

---

### 11 — OUT_OF_RANGE

- **HTTP mapping**: 400
- **Meaning**: The operation was attempted past the valid range, e.g., seeking past end of file. Unlike `INVALID_ARGUMENT`, this error indicates a problem that MAY be fixable if the system state changes (e.g., more data becomes available).
- **What triggers it**:
  - Pagination: `page_token` points beyond available data
  - Reading past end of stream/file
  - Array/list index out of bounds
- **What to check**: The current size/length of the data. The client should re-request with correct bounds.
- **Example fix**: Include the valid range in the error message so the client can adjust.
- **Real scenario**: "Client calls `ListOrders(page_size=50, page_token='abc')`. The server processes the token but the underlying database has been cleaned up, so the token no longer points to a valid position. Server returns `OUT_OF_RANGE: page_token 'abc' is no longer valid. Start from the beginning.`"

---

### 12 — UNIMPLEMENTED

- **HTTP mapping**: 501
- **Meaning**: The operation is not implemented or is not supported/enabled in this service.
- **What triggers it**:
  - The RPC method is defined in the proto but not implemented in the server
  - The server is running an older version without this RPC
  - A feature flag disables this RPC
  - The service doesn't support this transport
- **What to check**: The gRPC server's service registration. Is the method handler registered? Is the server running the correct version?
- **Example fix**: Either implement the method or return UNIMPLEMENTED so clients know not to call it.
- **Real scenario**: "Canary deployment: 10% of traffic goes to new server version with `SearchV2` RPC. 90% goes to old server without it. Clients get `UNIMPLEMENTED` on 90% of `SearchV2` calls. The service mesh should route `SearchV2` traffic only to the new server version."

---

### 13 — INTERNAL

- **HTTP mapping**: 500
- **Meaning**: Internal errors. This means the system is broken, not the request. Use this for invariant violations, assertion failures, and library-level bugs.
- **What triggers it**:
  - Null pointer / nil dereference caught by error handler
  - Assertion failure in business logic
  - Corrupted internal state
  - Library returns an error that indicates a bug, not a transient condition
- **What to check**: Server logs. This should never happen in production — it indicates a bug.
- **Example fix**: Fix the bug. In the meantime, the gRPC interceptor should return `INTERNAL` with a generic message; never expose stack traces to the client.
- **Real scenario**: "A data migration adds a new enum value to the proto. The server code has a `switch` statement that doesn't handle the new value. The default case hits: `return status.Error(codes.Internal, "unexpected status: " + status)`. All RPCs with the new status value get INTERNAL."

---

### 14 — UNAVAILABLE

- **HTTP mapping**: 503
- **Meaning**: The service is currently unavailable. This is a transient condition; the client can back off and retry. Unlike `INTERNAL`, this is not a bug — the service is temporarily down.
- **What triggers it**:
  - All backend instances are unhealthy
  - Connection pool to database is exhausted
  - Circuit breaker is OPEN
  - Service is starting up (not yet ready)
  - DNS resolution failure for a dependency
- **What to check**: Health check status of the service. Load balancer target health. Connection pool metrics.
- **Example fix**: Return `RetryInfo` in the details. Client should use exponential backoff. Ensure the service restarts quickly or falls back gracefully.
- **Real scenario**: "A rolling restart of the payment service: Kubernetes kills a pod → gRPC health check fails → load balancer marks it unhealthy → new pod starts but takes 15 seconds → during those 15 seconds, 1/3 of requests hit the draining pod and get `UNAVAILABLE`. Client retries with backoff, eventually hitting healthy pods."

---

### 15 — DATA_LOSS

- **HTTP mapping**: 500
- **Meaning**: Unrecoverable data loss or data corruption. This is worse than `INTERNAL` — data has been permanently lost.
- **What triggers it**:
  - Data was written to a volume that was destroyed
  - Replication lag caused a write to be lost
  - Corruption detected by checksum
  - Deleted data that should not have been deletable
- **What to check**: Restore from backup. Investigate the root cause of the loss.
- **Example fix**: This should trigger an immediate page and incident. Return DATALOSS to signal that retrying won't help — the data is gone.
- **Real scenario**: "A disk failure on the primary database node. Failover to replica. The replica was 5 seconds behind, so the last 5 seconds of writes are lost. When clients read back the data they just wrote, it's missing. The system detects the inconsistency and returns `DATA_LOSS`."

---

### 16 — UNAUTHENTICATED

- **HTTP mapping**: 401
- **Meaning**: The request does not have valid authentication credentials for the operation. Unlike `PERMISSION_DENIED`, the caller's identity is unknown.
- **What triggers it**:
  - Missing `Authorization` metadata header
  - Expired or invalid JWT/OAuth token
  - TLS client certificate missing or invalid
  - API key missing or revoked
- **What to check**: The `authorization` metadata in the gRPC request. Check if the token is expired. Check if the auth service is reachable.
- **Example fix**: Return `google.rpc.ErrorInfo` with reason `"auth_token_expired"` so the client knows to refresh.
- **Real scenario**: "A long-running gRPC stream has been open for 45 minutes. The initial auth token was valid for 60 minutes. At minute 61, the next message on the stream triggers a re-auth check. The token is expired. The server returns `UNAUTHENTICATED`. The client must re-authenticate and re-establish the stream."

---

## grpcurl Command Examples

`grpcurl` is the gRPC equivalent of `curl`. It lets you interact with gRPC services from the command line.

```bash
# Install grpcurl
go install github.com/fullstorydev/grpcurl/cmd/grpcurl@latest

# List available services on a gRPC server (requires reflection)
grpcurl -plaintext localhost:50051 list
# Output:
#   user.v1.UserService
#   grpc.reflection.v1alpha.ServerReflection

# List methods on a service
grpcurl -plaintext localhost:50051 list user.v1.UserService
# Output:
#   user.v1.UserService.GetUser
#   user.v1.UserService.ListUsers
#   user.v1.UserService.CreateUser

# Describe a method to see request/response types
grpcurl -plaintext localhost:50051 describe user.v1.UserService.GetUser
# Output:
#   user.v1.UserService.GetUser is a method:
#   rpc GetUser ( .user.v1.GetUserRequest ) returns ( .user.v1.GetUserResponse );

# Call a method with JSON body
grpcurl -plaintext \
  -d '{"user_id": "usr_123"}' \
  localhost:50051 \
  user.v1.UserService/GetUser
# Output:
#   {
#     "user": {
#       "id": "usr_123",
#       "name": "Alice"
#     }
#   }

# Call with metadata (headers)
grpcurl -plaintext \
  -H "Authorization: Bearer eyJhbGciOi..." \
  -d '{"user_id": "usr_123"}' \
  localhost:50051 \
  user.v1.UserService/GetUser

# Call with deadline (2 seconds)
grpcurl -plaintext \
  -connect-timeout 2 \
  -d '{"query": "test"}' \
  localhost:50051 \
  search.v1.SearchService/Search

# If reflection is disabled, use proto files
grpcurl -plaintext \
  -import-path ./protos \
  -proto user/v1/user.proto \
  -d '{"user_id": "usr_123"}' \
  localhost:50051 \
  user.v1.UserService/GetUser

# Debug: Get response status and trailers
grpcurl -v -plaintext \
  -d '{"user_id": "nonexistent"}' \
  localhost:50051 \
  user.v1.UserService/GetUser 2>&1
# Output shows:
#   ERROR:
#     Code: NotFound
#     Message: user 'nonexistent' not found
#   Response trailers received:
#     grpc-status: 5
#     grpc-message: user 'nonexistent' not found
```

---

## Protobuf Field Number Evolution Pitfalls

Protobuf field numbers are critical for backward compatibility. Changing them breaks serialization.

```protobuf
// VERSION 1 — deployed to production
message User {
  int64 id = 1;
  string name = 2;      // ← field number 2
  string email = 3;
}

// VERSION 2 — BREAKS COMPATIBILITY
message User {
  int64 id = 1;
  string email = 3;     // ← swapped positions — field 2 is now email!
  string name = 2;
}
// Old clients sending {id: 1, name: "Alice", email: "alice@example.com"}
// are interpreted by new server as {id: 1, name: "alice@example.com", email: "Alice"}
// This causes data corruption, not a clean error.

// CORRECT — never renumber fields
message User {
  int64 id = 1;
  string name = 2;
  string email = 3;
  string phone = 4;     // ← new field gets a new number
}
```

**Rules for safe proto evolution:**
1. **Never change a field number** — it's a wire-format identifier
2. **Never remove a required field** — make it optional first
3. **Never change a field type** — `int32` → `int64` changes wire format
4. **Reserve deleted field numbers** — use `reserved 5, 6, 10 to 12;`
5. **Add new fields with new numbers** — never reuse old numbers

```protobuf
message User {
  int64 id = 1;
  string name = 2;
  string email = 3;
  reserved 4, 5, 6;           // Old field numbers that were removed
  reserved "old_field_name";  // Old field name that was removed
  string phone = 7;           // New field
}
```

---

## Deadline Propagation

Deadlines in gRPC propagate through the call chain. The client sets a deadline, and each service in the chain subtracts its processing time.

```
Client deadline: 10s
     │
     ▼
Service A (takes 2s) — remaining: 8s
     │
     ▼
Service B (takes 3s) — remaining: 5s
     │
     ▼
Service C (takes 6s) → DEADLINE_EXCEEDED at 5s!
```

### Python — Deadline Propagation

```python
import grpc
from concurrent import futures

# --- Service B: respects incoming deadline when calling Service C ---
class ServiceB(user_pb2_grpc.UserServiceServicer):
    def GetUser(self, request, context):
        # context.time_remaining() tells us how much time is left
        remaining = context.time_remaining()
        logger.info(f"Service B: {remaining:.2f}s remaining for GetUser")

        if remaining is not None and remaining < 1.0:
            context.set_code(grpc.StatusCode.DEADLINE_EXCEEDED)
            context.set_details("Insufficient time remaining for downstream call")
            return user_pb2.GetUserResponse()

        # Propagate deadline to downstream call
        try:
            response = self.service_c_stub.GetUser(
                request,
                timeout=remaining,  # Propagate the remaining deadline
            )
            return response
        except grpc.RpcError as e:
            if e.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
                # Downstream timed out — return a degraded response
                logger.warning("Service C deadline exceeded, returning cached result")
                return self.get_cached_user(request.user_id)
            raise

# --- Client: sets the initial deadline ---
channel = grpc.insecure_channel('localhost:50051')
stub = user_pb2_grpc.UserServiceStub(channel)

try:
    response = stub.GetUser(
        user_pb2.GetUserRequest(user_id="usr_123"),
        timeout=10.0,  # 10 second deadline for the entire chain
    )
    print(f"Got user: {response.user.name}")
except grpc.RpcError as e:
    print(f"gRPC error: {e.code()} - {e.details()}")
```

---

## Channel State Debugging

gRPC channels go through a state machine. Understanding the current state helps diagnose connectivity issues.

```
IDLE ──► CONNECTING ──► READY ──► (normal operation)
  ▲                       │
  │                       │ connection breaks
  │                       ▼
  │               TRANSIENT_FAILURE
  │                       │
  │                       │ backoff timer expires
  │                       ▼
  │               CONNECTING ──► READY (recovered)
  │
  └──── SHUTDOWN (channel closed, will not reconnect)
```

### Python — Channel State Monitoring

```python
import grpc
import threading
import time

channel = grpc.insecure_channel('localhost:50051')

def monitor_channel_state(channel, stop_event):
    """Monitor and log channel state changes."""
    current_state = channel._channel.check_connectivity_state(True)
    print(f"Initial state: {current_state}")

    while not stop_event.is_set():
        state = channel._channel.check_connectivity_state(False)

        if state != current_state:
            state_name = {
                grpc.ChannelConnectivity.IDLE: "IDLE",
                grpc.ChannelConnectivity.CONNECTING: "CONNECTING",
                grpc.ChannelConnectivity.READY: "READY",
                grpc.ChannelConnectivity.TRANSIENT_FAILURE: "TRANSIENT_FAILURE",
                grpc.ChannelConnectivity.SHUTDOWN: "SHUTDOWN",
            }.get(state, f"UNKNOWN({state})")

            print(f"Channel state changed: {state_name}")
            current_state = state

            if state == grpc.ChannelConnectivity.TRANSIENT_FAILURE:
                print("  → Will retry with exponential backoff")
            elif state == grpc.ChannelConnectivity.READY:
                print("  → Channel is healthy")

        time.sleep(1)

stop_event = threading.Event()
monitor_thread = threading.Thread(
    target=monitor_channel_state,
    args=(channel, stop_event),
    daemon=True,
)
monitor_thread.start()

# ... use the channel ...

# When done:
channel.close()
stop_event.set()
```

---

## Full Client Examples

### Python — gRPC Client with Deadline and Retry Interceptor

```python
import grpc
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class RetryInterceptor(grpc.UnaryUnaryClientInterceptor):
    """Interceptor that retries on UNAVAILABLE and DEADLINE_EXCEEDED."""

    RETRIABLE_CODES = {
        grpc.StatusCode.UNAVAILABLE,
        grpc.StatusCode.DEADLINE_EXCEEDED,
        grpc.StatusCode.RESOURCE_EXHAUSTED,
        grpc.StatusCode.ABORTED,
    }

    def __init__(self, max_retries: int = 3, base_delay: float = 0.1):
        self.max_retries = max_retries
        self.base_delay = base_delay

    def intercept_unary_unary(self, continuation, client_call_details, request):
        for attempt in range(self.max_retries + 1):
            response = continuation(client_call_details, request)

            if response.code() not in self.RETRIABLE_CODES:
                return response

            if attempt < self.max_retries:
                delay = self.base_delay * (2 ** attempt)
                logger.warning(
                    f"Retrying gRPC call ({client_call_details.method}), "
                    f"code={response.code()}, attempt={attempt + 1}, "
                    f"delay={delay:.2f}s"
                )
                time.sleep(delay)
                continue

            return response

        return response  # Shouldn't reach here

# --- Create the channel with interceptors ---
interceptors = [RetryInterceptor(max_retries=3, base_delay=0.2)]

channel = grpc.intercept_channel(
    grpc.insecure_channel('localhost:50051'),
    *interceptors,
)

stub = user_pb2_grpc.UserServiceStub(channel)

# Call with deadline
try:
    response = stub.GetUser(
        user_pb2.GetUserRequest(user_id="usr_123"),
        timeout=10.0,
        metadata=[
            ('x-request-id', 'req-abc-123'),
            ('x-client-version', '2.1.0'),
        ],
    )
    print(f"User: {response.user.name}")
except grpc.RpcError as e:
    code_name = e.code()
    details = e.details()

    print(f"gRPC call failed:")
    print(f"  Code: {code_name}")
    print(f"  Details: {details}")

    # Access trailing metadata for debugging
    trailing_meta = e.trailing_metadata()
    for key, value in trailing_meta:
        print(f"  Trailing: {key} = {value}")

    # Handle specific errors
    if e.code() == grpc.StatusCode.UNAUTHENTICATED:
        print("→ Need to re-authenticate")
        # trigger re-auth flow
    elif e.code() == grpc.StatusCode.UNAVAILABLE:
        print("→ Service unavailable, retry with backoff")
    elif e.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
        print("→ Operation took too long, try with longer deadline")
```

### Java — gRPC Stub with Deadline

```java
import io.grpc.*;
import io.grpc.stub.StreamObserver;

import java.util.concurrent.TimeUnit;
import java.util.logging.Logger;

public class GrpcClient {

    private static final Logger log = Logger.getLogger(GrpcClient.class.getName());

    private final UserServiceGrpc.UserServiceBlockingStub blockingStub;
    private final UserServiceGrpc.UserServiceFutureStub futureStub;

    public GrpcClient(String host, int port) {
        ManagedChannel channel = ManagedChannelBuilder
            .forAddress(host, port)
            .usePlaintext()
            .enableRetry()  // Enable gRPC built-in retry
            .maxRetryAttempts(3)
            .keepAliveTime(30, TimeUnit.SECONDS)
            .keepAliveTimeout(10, TimeUnit.SECONDS)
            .keepAliveWithoutCalls(true)
            .build();

        this.blockingStub = UserServiceGrpc.newBlockingStub(channel);
        this.futureStub = UserServiceGrpc.newFutureStub(channel);
    }

    /**
     * Blocking call with deadline.
     */
    public GetUserResponse getUser(String userId) {
        GetUserRequest request = GetUserRequest.newBuilder()
            .setUserId(userId)
            .build();

        try {
            return blockingStub
                .withDeadlineAfter(10, TimeUnit.SECONDS)  // 10s deadline
                .withWaitForReady()  // Wait for channel to be READY
                .getUser(request);

        } catch (StatusRuntimeException e) {
            Status.Code code = e.getStatus().getCode();
            String description = e.getStatus().getDescription();

            log.warning(String.format(
                "gRPC call failed: code=%s, description=%s",
                code, description
            ));

            // Check trailing metadata for error details
            Metadata trailers = Status.trailersFromThrowable(e);
            if (trailers != null) {
                trailers.keys().forEach(key ->
                    log.info("Trailer: " + key + " = " +
                        trailers.get(Metadata.Key.of(key, Metadata.ASCII_STRING_MARSHALLER)))
                );
            }

            switch (code) {
                case UNAVAILABLE:
                    throw new ServiceUnavailableException(
                        "User service unavailable", e
                    );
                case DEADLINE_EXCEEDED:
                    throw new DeadlineExceededException(
                        "User service call timed out after 10s", e
                    );
                case UNAUTHENTICATED:
                    throw new AuthenticationException(
                        "Authentication required", e
                    );
                case NOT_FOUND:
                    return null;  // User not found — return empty
                default:
                    throw new RuntimeException(
                        "gRPC error: " + code, e
                    );
            }
        }
    }

    /**
     * Async call with deadline propagation.
     */
    public void getUserAsync(
            String userId,
            StreamObserver<GetUserResponse> responseObserver) {

        GetUserRequest request = GetUserRequest.newBuilder()
            .setUserId(userId)
            .build();

        futureStub
            .withDeadlineAfter(10, TimeUnit.SECONDS)
            .getUser(request, new StreamObserver<GetUserResponse>() {
                @Override
                public void onNext(GetUserResponse response) {
                    responseObserver.onNext(response);
                }

                @Override
                public void onError(Throwable t) {
                    log.severe("Async gRPC call failed: " + t.getMessage());
                    responseObserver.onError(t);
                }

                @Override
                public void onCompleted() {
                    responseObserver.onCompleted();
                }
            });
    }

    /**
     * Retry configuration in Java service config (retry_policy.json):
     *
     * {
     *   "methodConfig": [{
     *     "name": [{"service": "user.v1.UserService"}],
     *     "retryPolicy": {
     *       "maxAttempts": 3,
     *       "initialBackoff": "0.1s",
     *       "maxBackoff": "5s",
     *       "backoffMultiplier": 2,
     *       "retryableStatusCodes": ["UNAVAILABLE", "DEADLINE_EXCEEDED"]
     *     }
     *   }]
     * }
     */
}
```

### JavaScript — @grpc/grpc-js Client

```javascript
import * as grpc from '@grpc/grpc-js';
import * as protoLoader from '@grpc/proto-loader';
import path from 'path';

// Load proto
const PROTO_PATH = path.join(__dirname, 'protos/user/v1/user.proto');
const packageDefinition = protoLoader.loadSync(PROTO_PATH, {
  keepCase: true,
  longs: String,
  enums: String,
  defaults: true,
  oneofs: true,
});
const userProto = grpc.loadPackageDefinition(packageDefinition).user.v1;

// Create client with retry and deadline configuration
const client = new userProto.UserService(
  'localhost:50051',
  grpc.credentials.createInsecure(),
  {
    // Channel options
    'grpc.keepalive_time_ms': 30000,
    'grpc.keepalive_timeout_ms': 10000,
    'grpc.http2.max_frame_size': 16384,
    // Service config with retry policy
    'grpc.service_config': JSON.stringify({
      methodConfig: [
        {
          name: [{ service: 'user.v1.UserService' }],
          timeout: '10s',
          retryPolicy: {
            maxAttempts: 3,
            initialBackoff: '0.1s',
            maxBackoff: '5s',
            backoffMultiplier: 2,
            retryableStatusCodes: [
              'UNAVAILABLE',
              'DEADLINE_EXCEEDED',
              'RESOURCE_EXHAUSTED',
            ],
          },
        },
      ],
    }),
  }
);

// Helper: convert gRPC status code to name
const statusCodeName = (code) => {
  const names = {
    [grpc.status.OK]: 'OK',
    [grpc.status.CANCELLED]: 'CANCELLED',
    [grpc.status.UNKNOWN]: 'UNKNOWN',
    [grpc.status.INVALID_ARGUMENT]: 'INVALID_ARGUMENT',
    [grpc.status.DEADLINE_EXCEEDED]: 'DEADLINE_EXCEEDED',
    [grpc.status.NOT_FOUND]: 'NOT_FOUND',
    [grpc.status.ALREADY_EXISTS]: 'ALREADY_EXISTS',
    [grpc.status.PERMISSION_DENIED]: 'PERMISSION_DENIED',
    [grpc.status.RESOURCE_EXHAUSTED]: 'RESOURCE_EXHAUSTED',
    [grpc.status.FAILED_PRECONDITION]: 'FAILED_PRECONDITION',
    [grpc.status.ABORTED]: 'ABORTED',
    [grpc.status.OUT_OF_RANGE]: 'OUT_OF_RANGE',
    [grpc.status.UNIMPLEMENTED]: 'UNIMPLEMENTED',
    [grpc.status.INTERNAL]: 'INTERNAL',
    [grpc.status.UNAVAILABLE]: 'UNAVAILABLE',
    [grpc.status.DATA_LOSS]: 'DATA_LOSS',
    [grpc.status.UNAUTHENTICATED]: 'UNAUTHENTICATED',
  };
  return names[code] || `UNKNOWN(${code})`;
};

// Unary call with deadline
function getUser(userId, timeoutMs = 10000) {
  const deadline = new Date(Date.now() + timeoutMs);

  const metadata = new grpc.Metadata();
  metadata.add('x-request-id', `req-${Date.now()}`);
  metadata.add('authorization', 'Bearer eyJhbGciOi...');

  client.getUser(
    { user_id: userId },
    metadata,
    { deadline },
    (error, response) => {
      if (error) {
        const code = error.code;
        const codeName = statusCodeName(code);

        console.error(`gRPC call failed:`, {
          code: codeName,
          details: error.details,
          metadata: error.metadata?.getMap(),
        });

        switch (code) {
          case grpc.status.NOT_FOUND:
            console.log('User not found — handle gracefully');
            break;
          case grpc.status.DEADLINE_EXCEEDED:
            console.log('Request timed out — consider increasing timeout');
            break;
          case grpc.status.UNAVAILABLE:
            console.log('Service unavailable — retry with backoff');
            break;
          case grpc.status.UNAUTHENTICATED:
            console.log('Need to re-authenticate');
            break;
          default:
            console.error(`Unexpected gRPC error: ${codeName}`);
        }
        return;
      }

      console.log('Got user:', response.user.name);
    }
  );
}

// Server streaming call
function searchUsers(query) {
  const deadline = new Date(Date.now() + 5000);
  const stream = client.searchUsers(
    { query, page_size: 10 },
    { deadline }
  );

  stream.on('data', (user) => {
    console.log(`Found user: ${user.name}`);
  });

  stream.on('error', (error) => {
    console.error(`Search stream error: ${statusCodeName(error.code)} - ${error.details}`);
  });

  stream.on('end', () => {
    console.log('Search stream ended');
  });
}

// Graceful shutdown
process.on('SIGTERM', () => {
  console.log('Shutting down gRPC client...');
  client.close();
});
```

---

## gRPC Error Details (Rich Error Model)

gRPC supports attaching structured error details to a status using `google.rpc.Status.details`. This is more useful than a plain string message.

### Python — Returning Rich Error Details

```python
from google.rpc import error_details_pb2, code_pb2
from grpc_status import rpc_status
import grpc

def create_user_handler(request, context):
    # Check if user already exists
    existing = db.find_user_by_email(request.email)
    if existing:
        # Build rich error status
        status = rpc_status.status_pb2.Status()
        status.code = code_pb2.ALREADY_EXISTS
        status.message = f"User with email {request.email} already exists"

        # Add detail: the conflicting resource
        resource_info = error_details_pb2.ResourceInfo()
        resource_info.resource_type = "user"
        resource_info.resource_name = f"users/{existing.id}"
        resource_info.owner = "user-service"
        resource_info.description = "Email address already registered"
        status.details.add().Pack(resource_info)

        # Add detail: helps client fix the issue
        help_info = error_details_pb2.Help()
        link = help_info.links.add()
        link.description = "Reset password"
        link.url = f"https://app.example.com/reset-password?email={request.email}"
        status.details.add().Pack(help_info)

        context.abort_with_status(rpc_status.to_status(status))

    # ... create user ...

# Client-side: extracting rich error details
try:
    response = stub.CreateUser(request)
except grpc.RpcError as e:
    status = rpc_status.from_call(e)

    if status:
        for detail in status.details:
            if detail.Is(error_details_pb2.ResourceInfo.DESCRIPTOR):
                resource = error_details_pb2.ResourceInfo()
                detail.Unpack(resource)
                print(f"Conflict resource: {resource.resource_name}")
            elif detail.Is(error_details_pb2.Help.DESCRIPTOR):
                help_info = error_details_pb2.Help()
                detail.Unpack(help_info)
                for link in help_info.links:
                    print(f"Help: {link.description} → {link.url}")

```

---

## JSON Mapping for gRPC (Transcoding)

When gRPC is exposed via REST (using gRPC-Gateway or Envoy transcoding), the status codes are mapped:

| gRPC Code | HTTP Status | gRPC-Gateway Behavior |
|-----------|-------------|----------------------|
| OK | 200 | Body is the protobuf JSON |
| CANCELLED | 499 | Client closed request |
| UNKNOWN | 500 | Internal server error |
| INVALID_ARGUMENT | 400 | Bad request with field violations |
| DEADLINE_EXCEEDED | 504 | Gateway timeout |
| NOT_FOUND | 404 | Resource not found |
| ALREADY_EXISTS | 409 | Conflict |
| PERMISSION_DENIED | 403 | Forbidden |
| RESOURCE_EXHAUSTED | 429 | Too many requests with Retry-After |
| FAILED_PRECONDITION | 400 | Bad request |
| ABORTED | 409 | Conflict |
| OUT_OF_RANGE | 400 | Bad request |
| UNIMPLEMENTED | 501 | Not implemented |
| INTERNAL | 500 | Internal server error |
| UNAVAILABLE | 503 | Service unavailable |
| DATA_LOSS | 500 | Internal server error |
| UNAUTHENTICATED | 401 | Unauthorized |

---

## Monitoring Recommendations

### Metrics to Track

```prometheus
# gRPC server metrics
grpc_server_handled_total{grpc_code="..."}    # Total RPCs by status code
grpc_server_handling_seconds{grpc_method="..."}  # Latency histogram by method
grpc_server_started_total{grpc_method="..."}  # Total RPCs started
grpc_server_msg_received_total                # Messages received
grpc_server_msg_sent_total                   # Messages sent

# gRPC client metrics
grpc_client_handled_total{grpc_code="..."}    # Client-side RPCs by status code
grpc_client_handling_seconds{grpc_method="..."}  # Client-perceived latency
grpc_client_started_total{grpc_method="..."}  # Client-side RPCs started
grpc_client_msg_received_total
grpc_client_msg_sent_total
```

### Alert Thresholds

| Metric | Warning | Critical | Window |
|--------|---------|----------|--------|
| gRPC error rate (all non-OK) | > 1% | > 5% | 5 min |
| DEADLINE_EXCEEDED rate | > 0.5% | > 2% | 5 min |
| UNAVAILABLE rate | ANY | > 1% | 1 min |
| INTERNAL rate | ANY | ANY | 1 min |
| DATA_LOSS rate | ANY | ANY | Immediate |

### Debug Checklist

1. **Check the status code**: `grpcurl` shows the exact code and message
2. **Check trailing metadata**: Often contains error details, request IDs, retry info
3. **Check deadlines**: `context.time_remaining()` on the server, client timeout config
4. **Check channel state**: READY? TRANSIENT_FAILURE? CONNECTING?
5. **Check proto compatibility**: Client proto version matches server?
6. **Check load balancing**: All backends healthy? Any DNS resolution failures?
7. **Enable gRPC tracing**: `GRPC_TRACE=all GRPC_VERBOSITY=DEBUG`

---

*Return to [07 Error Codes Home](../README.md)*
