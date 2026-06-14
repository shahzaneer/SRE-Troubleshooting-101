# 09 — Performance

> Performance is not a feature. It's the absence of catastrophe under load. This section covers profiling, load testing, bottleneck analysis, and caching — everything you need to make software fast and keep it fast.

---

## Section Contents

| # | Document | Description |
|---|----------|-------------|
| 1 | [Application Profiling](profiling/application-profiling.md) | CPU, memory, and allocation profiling in Python, Java, and Node.js; flamegraph interpretation |
| 2 | [Load Testing Guide](load-testing/load-testing-guide.md) | wrk, k6, Locust; interpreting results, finding breaking points, realistic scenarios |
| 3 | [Bottleneck Analysis](bottleneck-analysis/bottleneck-guide.md) | Amdahl's Law, Little's Law, utilization cliff, thread pool exhaustion, connection pool saturation |
| 4 | [Caching Strategies](caching/caching-strategies.md) | Cache patterns, stampede prevention, invalidation, CDN, Redis optimization, multi-language examples |

---

## The Performance Investigation Flow

```
Is there a performance problem?
  │
  ├─ YES → Do you know WHERE the slowness is?
  │   ├─ NO  → Use profiling (flamegraphs) to find hot function
  │   └─ YES → Is it CPU, memory, I/O, or external dependency?
  │       ├─ CPU → Optimize algorithm, reduce work, parallelize
  │       ├─ Memory → Reduce allocations, fix leak, tune GC
  │       ├─ I/O → Add caching, batch operations, use async I/O
  │       └─ External → Add timeouts, circuit breakers, fallback cache
  │
  └─ NOT SURE → Run a load test
      └─ Find where latency breaks SLO → that's your bottleneck
```

---

## Prerequisites

- Access to production-like environment for load testing
- Profiling tools installed: py-spy, async-profiler, clinic.js
- Monitoring: Prometheus + Grafana (or equivalent) for server-side metrics during load tests
- Distributed tracing: Jaeger/Zipkin for end-to-end latency breakdown

---

## Learning Path

- **Beginner**: Read Caching Strategies → implement cache-aside pattern
- **Intermediate**: Read Load Testing Guide → run realistic load tests before releases
- **Advanced**: Read Bottleneck Analysis → understand queueing theory and thread pool dynamics
- **Master**: Read Application Profiling → extract and interpret flamegraphs from production

---

*Previous Section: [09 — Observability](../09-observability/README.md)*
*Next Section: [11 — On-Call Runbooks](../11-oncall-runbooks/README.md)*
