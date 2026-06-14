# 06 — API Troubleshooting

> **Section Lead:** SRE Team
> **Last Reviewed:** 2026-06
> **Scope:** REST, GraphQL, gRPC, WebSocket, Async Messaging

---

## Quick Navigation

| # | Topic | File | Difficulty | Key Signals |
|---|-------|------|------------|-------------|
| 1 | REST API Debugging | [rest-api/rest-debugging.md](rest-api/rest-debugging.md) | Basic–Intermediate | 5xx errors, 429s, slow responses, duplicate POSTs |
| 2 | GraphQL Troubleshooting | [graphql/graphql-troubleshooting.md](graphql/graphql-troubleshooting.md) | Intermediate–Advanced | N+1 queries, depth abuse, HTTP 200 with errors, subscription drops |
| 3 | gRPC Troubleshooting | [grpc/grpc-troubleshooting.md](grpc/grpc-troubleshooting.md) | Advanced | UNAVAILABLE, DEADLINE_EXCEEDED, UNIMPLEMENTED, streaming leaks |
| 4 | WebSocket Troubleshooting | [websockets/websocket-troubleshooting.md](websockets/websocket-troubleshooting.md) | Intermediate | Connection drops, 101 upgrade failures, CLOSE_WAIT buildup, silent disconnects |
| 5 | Async API & Message Queue Troubleshooting | [message-queues-as-api/async-api-troubleshooting.md](message-queues-as-api/async-api-troubleshooting.md) | Intermediate–Advanced | Consumer lag, DLQ backlog, duplicate processing, partition skew |

---

## Escalation Flow

```
First responder:
  ├── HTTP 5xx → Check API Gateway logs → Service logs → DB/Cache metrics
  ├── HTTP 4xx → Check request payload → Auth token → Rate limit counters
  ├── High latency → curl timing → APM trace → Identify slowest span (DB, upstream, serialization)
  └── Connection issues → DNS → TLS handshake → LB/Nginx config → Timeout chains

Escalate to:
  ├── Backend team: DEADLINE_EXCEEDED, INTERNAL, N+1 queries, serialization errors
  ├── Infra/Networking: UNAVAILABLE, connection refused, DNS failures, TLS errors
  ├── Platform team: Resource exhausted, rate limiting infra, API Gateway config
  └── Security: PERMISSION_DENIED spike, introspection abuse, unusual query depth
```

## Toolbelt Reference

| Tool | Use Case |
|------|----------|
| `curl -vw` | REST timing breakdown, header inspection |
| `grpcurl` | gRPC method list, invoke, metadata injection |
| `wscat` / browser dev tools | WebSocket connect debug |
| `kafkacat` / `aws sqs` CLI | Message queue inspection |
| `apollo-tracing` / DataDog APM | Trace N+1, trace waterfall |
| `mitmproxy` / Wireshark | Raw wire-level debugging |
| `hey` / `wrk` / `k6` | Load testing, rate limit testing |

## Critical Runbooks (linked)

1. [REST: 5xx Spike Playbook](rest-api/rest-debugging.md#5xx-spike-playbook)
2. [GraphQL: N+1 Detection & Remediation](graphql/graphql-troubleshooting.md#n1-detection-playbook)
3. [gRPC: DEADLINE_EXCEEDED Chain](grpc/grpc-troubleshooting.md#deadline-propagation)
4. [WebSocket: Silent Disconnect Recovery](websockets/websocket-troubleshooting.md#silent-disconnect-playbook)
5. [Async: DLQ Triage & Consumer Lag](message-queues-as-api/async-api-troubleshooting.md#lag-diagnosis)
