# 08 — Observability

> SRE troubleshooting begins and ends with what you can see. This section covers the three pillars of observability (metrics, logging, tracing) plus the dashboards that tie them together.

---

## Section Contents

| # | Document | Description |
|---|----------|-------------|
| 1 | [Metrics Guide](metrics/metrics-guide.md) | Four Golden Signals, PromQL cookbook, metric types, multi-language instrumentation |
| 2 | [Structured Logging](logging/structured-logging.md) | JSON logging patterns, correlation IDs, log levels, noise reduction, Python/Java/JS examples |
| 3 | [Distributed Tracing](tracing/distributed-tracing.md) | OpenTelemetry, span analysis, sampling strategies, multi-language instrumentation |
| 4 | [Dashboard Design](dashboards/dashboard-design.md) | Grafana layout tiers, SLO dashboards, alert fatigue prevention, real scenario walkthroughs |

---

## The Three Pillars (and Why You Need All Three)

```
Pillar    | Answers                           | Best Tool
----------|-----------------------------------|------------------
Metrics   | "Is something wrong?"             | Prometheus
Logging   | "What's the full context?"        | Loki / ELK
Tracing   | "Where in the call chain?"        | Jaeger / Tempo
```

- **Metrics** tell you the system is degraded (p99 latency spiked).
- **Logging** shows the error message and request payload.
- **Tracing** reveals which downstream service caused the slowdown.

A dashboard without one pillar leaves blind spots. A dashboard with all three gives you actionable answers before an alert even fires.

---

## Quick Diagnostic Flow

```
Alert fires (p99 > 1s)
  → Open Service Dashboard: confirm metric spike
  → Click exemplar: jump to Jaeger trace
  → Identify slow span: PaymentService.GetAuthToken: 2800ms
  → Filter logs by trace_id in Loki: "timeout calling auth provider"
  → Root cause: external auth provider degraded
  → Action: check provider status page, enable circuit breaker
```

---

## Prerequisites

- Prometheus + Grafana (or equivalent monitoring stack)
- Loki / Elasticsearch + Kibana (or equivalent log aggregator)
- Jaeger / Zipkin / Tempo (or equivalent distributed tracing)
- OpenTelemetry SDK in your application

---

## Learning Path

- **Beginner**: Start with Metrics Guide → understand the Four Golden Signals
- **Intermediate**: Add Structured Logging → implement correlation IDs across services
- **Advanced**: Implement Distributed Tracing → analyze traces to find bottlenecks
- **Master**: Design effective dashboards that prevent alert fatigue

---

*Next Section: [10 — Performance](../10-performance/README.md)*
