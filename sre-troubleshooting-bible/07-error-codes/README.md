# 07 — Error Codes

> **Category:** API | HTTP | Networking | Security
> **Difficulty:** Basic to Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#http` `#grpc` `#dns` `#tls` `#error-codes` `#oncall`

---

## Quick Navigation

| Section | Path | Topics |
|---------|------|--------|
| 4xx Client Errors | [4xx/client-errors.md](4xx/client-errors.md) | 400, 401, 403, 404, 405, 408, 409, 410, 422, 429, 499 |
| 5xx Server Errors | [5xx/server-errors.md](5xx/server-errors.md) | 500, 502, 503, 504, 507, 508 |
| gRPC Status Codes | [grpc-status-codes/grpc-errors.md](grpc-status-codes/grpc-errors.md) | All 17 gRPC codes, deadline propagation, channel states |
| DNS Errors | [dns-errors/dns-error-reference.md](dns-errors/dns-error-reference.md) | NXDOMAIN, SERVFAIL, REFUSED, TIMEOUT, FORMERR, NOTIMP |
| TLS/SSL Errors | [tls-errors/tls-error-reference.md](tls-errors/tls-error-reference.md) | Certificate, cipher, protocol, chain, SNI errors |

---

## HTTP Error Code Summary

### 4xx — Client Errors (The client did something wrong)

| Code | Name | Mnemonic | First Check |
|------|------|----------|-------------|
| **400** | Bad Request | Malformed payload | Request body logging, Pydantic/Zod validation output |
| **401** | Unauthorized | "Who are you?" | JWT expiry, signing key rotation, auth header format |
| **403** | Forbidden | "I know you, but no." | RBAC/ACL, CORS preflight, IAM policy evaluation |
| **404** | Not Found | Resource doesn't exist | Route registration, soft-delete flag, ASG version mismatch |
| **405** | Method Not Allowed | Wrong HTTP verb | CORS OPTIONS handler, route method decorators |
| **408** | Request Timeout | Client too slow | `client_body_timeout`, LB idle timeout, mobile upload |
| **409** | Conflict | Concurrent modification | Optimistic lock version, duplicate key, state machine |
| **410** | Gone | Permanently removed | Deprecated API, tombstoned resource, SEO headers |
| **422** | Unprocessable | Semantic error | Business rule validators, Pydantic `@validator`, Jakarta `@Constraint` |
| **429** | Too Many Requests | Rate limited | Redis rate limiter keys, `Retry-After` header, token bucket state |
| **499** | Client Closed (Nginx) | User bailed | `$request_time`, mobile backgrounding, p99 latency correlation |

### 5xx — Server Errors (The server failed)

| Code | Name | Mnemonic | First Check |
|------|------|----------|-------------|
| **500** | Internal Server Error | Unhandled exception | Stack trace, exception handler coverage, cron/async jobs |
| **502** | Bad Gateway | Upstream garbage | Upstream process alive? Listening? Responding valid HTTP? |
| **503** | Service Unavailable | Nobody home | Health checks, circuit breaker state, ready pods count |
| **504** | Gateway Timeout | Upstream too slow | Timeout hierarchy: app(10s) < nginx(15s) < LB(20s), DB query duration |
| **507** | Insufficient Storage | Disk full | `df -h`, `df -i`, ephemeral storage limit, log rotation |
| **508** | Loop Detected | Infinite redirect | `curl -L -v`, proxy_pass, service mesh routing rules |

### gRPC Status Codes

| Code # | Name | HTTP Map | Typical Trigger |
|--------|------|----------|-----------------|
| 0 | OK | 200 | Normal response |
| 1 | CANCELLED | 499 | Client cancelled, deadline exceeded client-side |
| 2 | UNKNOWN | 500 | Generic error, panic recovery |
| 3 | INVALID_ARGUMENT | 400 | Bad proto field, enum value out of range |
| 4 | DEADLINE_EXCEEDED | 504 | Deadline propagation through mesh |
| 5 | NOT_FOUND | 404 | Resource lookup failed |
| 6 | ALREADY_EXISTS | 409 | Duplicate create, optimistic lock |
| 7 | PERMISSION_DENIED | 403 | AuthZ failure |
| 8 | RESOURCE_EXHAUSTED | 429 | Rate limit, quota, backpressure |
| 9 | FAILED_PRECONDITION | 400 | E-Tag mismatch, state violation |
| 10 | ABORTED | 409 | Retriable conflict |
| 11 | OUT_OF_RANGE | 400 | Field value exceeds valid range |
| 12 | UNIMPLEMENTED | 501 | RPC method not implemented |
| 13 | INTERNAL | 500 | Library-level bug, assertion failure |
| 14 | UNAVAILABLE | 503 | Connection broken, transient failure |
| 15 | DATA_LOSS | 500 | Unrecoverable persistence loss |
| 16 | UNAUTHENTICATED | 401 | Missing/expired credentials |

### DNS Error Codes (RCODE)

| RCODE | Name | Meaning |
|-------|------|---------|
| 0 | NOERROR | Success |
| 1 | FORMERR | Format error — nameserver couldn't parse query |
| 2 | SERVFAIL | Server failure — upstream resolution broke |
| 3 | NXDOMAIN | Domain does not exist (authoritative answer) |
| 4 | NOTIMP | Query type not implemented |
| 5 | REFUSED | Query refused (policy, recursion not allowed) |

### TLS/SSL Error Categories

| Error Pattern | Root Cause Family |
|---------------|-------------------|
| `RECORD_TOO_LONG` | HTTP → HTTPS port mismatch |
| `CERTIFICATE_VERIFY_FAILED` | Chain incomplete, expired, self-signed |
| `WRONG_VERSION_NUMBER` | TLS version negotiation failure |
| `COMMON_NAME_INVALID` | SNI / hostname mismatch |
| `DATE_INVALID` | Expired or not-yet-valid certificate |
| `VERSION_OR_CIPHER_MISMATCH` | No shared cipher suite |
| `SELF_SIGNED_CERT` | Self-signed cert in chain |
| `ISSUER_CERT_LOCALLY` | Missing root CA in trust store |

---

## Diagnostic Workflow: Which Section to Open First

```
User reports: "API is broken"
            │
            ▼
┌──────────────────────┐
│ What's the HTTP code?│
│ Check LB/API gateway │
│ logs, browser DevTools│
└──────┬───────────────┘
       │
    4xx? ───────────► 4xx/client-errors.md
       │
    5xx? ───────────► 5xx/server-errors.md
       │
    DNS error? ─────► dns-errors/dns-error-reference.md
       │
    TLS error? ─────► tls-errors/tls-error-reference.md
       │
    gRPC service? ──► grpc-status-codes/grpc-errors.md
```

---

## Universal On-Call Checklist

When paged for elevated error rates, before deep-diving into code-specific diagnostics:

1. **Identify the error code**: Is it 4xx, 5xx, or a networking error?
2. **Establish the blast radius**: Single user? Single region? All users?
3. **Check the change calendar**: Any recent deployments, config changes, certificate rotations, DNS updates, or cron schedule changes?
4. **Check dependencies**: Database, cache, message queue, external APIs — any alerts there?
5. **Check infrastructure**: CPU, memory, disk, network saturation on affected instances.
6. **Narrow by dimension**: If error is tagged with `pod`, `AZ`, `node`, `version` — is it clustered?
7. **Roll back first, debug later**: If a deployment correlates and the impact is high, initiate rollback while continuing investigation.
8. **Capture a sample**: Save a raw request/response pair including headers before the condition clears.

---

## Monitoring Recommendations for Error Codes

### Dashboards to Build

1. **Error Rate by Status Code Family** — Stacked graph of 2xx/3xx/4xx/5xx rates per service
2. **Top 10 Error Codes (Non-2xx)** — Table sorted by count, grouped by endpoint + code
3. **Error Rate by Upstream Dependency** — 502/503/504 broken down by which upstream failed
4. **4xx vs 5xx Ratio** — A sudden shift from 4xx→5xx dominance signals a server-side degradation (upstream died, DB down)
5. **499 Rate** — Tracked separately because it correlates with latency, not errors
6. **Auth Error Dashboard** — 401 + 403 broken out, because these are often misdiagnosed interchangeably

### Alert Thresholds

| Metric | Yellow (Warning) | Red (Critical) | Window |
|--------|-------------------|----------------|--------|
| 5xx rate | > 0.5% of requests | > 2% of requests | 5 min |
| 4xx rate | > 10% of requests | > 25% of requests | 5 min |
| 499 rate | > 1% of requests | > 5% of requests | 5 min |
| Auth failures (401) | > 5/min | > 50/min | 5 min |
| DNS failures | > 10/min | > 100/min | 1 min |
| TLS failures | ANY | > 5/min | 1 min |

### Log Patterns to Monitor

```bash
# ELK / Splunk / Loki saved searches
status:[500 TO 599] AND NOT status:503    # 500 errors excluding maintenance 503s
status:502 AND upstream:"payment-gateway"  # Specific upstream failures
status:401 AND NOT path:"/health"          # Auth failures on actual endpoints
status:499 AND request_time>3000           # 499s correlated with high latency
```

### Prometheus AlertManager Rules

```yaml
groups:
  - name: http_errors
    rules:
      - alert: High5xxRate
        expr: rate(http_requests_total{status=~"5.."}[5m]) / rate(http_requests_total[5m]) > 0.02
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "5xx rate above 2% for {{ $labels.service }}"
          description: "Current rate: {{ $value | humanizePercentage }}"

      - alert: High4xxRate
        expr: rate(http_requests_total{status=~"4.."}[5m]) / rate(http_requests_total[5m]) > 0.25
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "4xx rate above 25% for {{ $labels.service }}"

      - alert: High499Rate
        expr: rate(http_requests_total{status="499"}[5m]) / rate(http_requests_total[5m]) > 0.05
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Client disconnect rate (499) elevated — check p99 latency"

      - alert: DNSErrorSpike
        expr: rate(dns_failures_total[1m]) > 10
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "DNS failures spiking — possible nameserver outage"
```

### Log Enrichment — Add These Headers to Your Access Logs

```
$request_time        # Total request duration (Nginx)
$upstream_response_time  # Upstream duration (Nginx)
$upstream_addr       # Which upstream handled it (Nginx)
$upstream_status     # Status from upstream (Nginx)
x-request-id         # Trace ID (your app)
x-amzn-trace-id      # AWS LB trace ID
true-client-ip       # Behind CloudFront / CDN
```

---

## Cross-Reference Map

| Symptom | Check Here First | Also Relevant |
|---------|-----------------|---------------|
| Mobile client gets random failures | `4xx/499` | `5xx/504` |
| New deployment broke auth | `4xx/401` | `tls-errors/` |
| Payment failures during peak | `5xx/504` | `5xx/503` |
| K8s rolling update causes blip | `5xx/503` | `5xx/502` |
| Intermittent 404 after deploy | `4xx/404` | `4xx/405` |
| API gateway returns 5xx, backend OK | `5xx/502` | `5xx/504`, `tls-errors/` |
| gRPC service mesh failing | `grpc-status-codes/` | `5xx/502` |
| Can't resolve internal service | `dns-errors/` | `5xx/502` |
| TLS handshake failures after cert rotation | `tls-errors/` | `4xx/401` |
| Rate limiting kicking in unexpectedly | `4xx/429` | `5xx/503` |

---

*Proceed to the individual section that matches your diagnostic need, or start with the on-call checklist above.*
