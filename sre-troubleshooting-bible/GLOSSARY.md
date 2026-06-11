# SRE Glossary
> **Category:** Reference
> **Last Reviewed:** 2026-06

A comprehensive glossary of Site Reliability Engineering terminology. Each term includes a concise definition, usage context, and a concrete example.

---

## Core SRE Concepts

**SLO (Service Level Objective)** — A target value for a service's reliability, measured by an SLI. Example: "99.9% of requests return successfully within 300ms over a 28-day rolling window."

**SLI (Service Level Indicator)** — A quantitative measure of some aspect of a service's behavior. Example: The ratio of HTTP 200 responses to total requests, measured at the load balancer.

**SLA (Service Level Agreement)** — A contractual agreement with customers that defines the consequences of missing SLOs. Example: "If uptime falls below 99.5% in a month, customers receive a 25% credit." SLAs are typically looser than SLOs.

**Error Budget** — The amount of acceptable unreliability: 100% minus the SLO. Example: An SLO of 99.9% over 30 days allows 43 minutes of downtime. The error budget is consumed by incidents, failed deployments, and planned maintenance. When the error budget is exhausted, feature deploys are frozen until reliability is restored.

**Toil** — Manual, repetitive, automatable, tactical work with no enduring value. Example: Manually resizing a disk volume every time an alert fires, instead of writing an auto-scaling policy. SRE teams should spend no more than 50% of their time on toil.

**MTTR (Mean Time to Resolve)** — Average time from incident detection to full resolution. Lower is better. Example: "Our MTTR for P1 incidents dropped from 45 minutes to 12 minutes after implementing runbooks." Includes detection time, diagnosis time, repair time, and verification time.

**MTBF (Mean Time Between Failures)** — Average time between one failure and the next. Higher is better. Example: "The database had an MTBF of 90 days before we implemented connection pooling."

**MTTD (Mean Time to Detect)** — Average time from failure occurrence to detection by monitoring. Example: "Our MTTD was 15 minutes for disk-full incidents before we added predictive disk space alerting." Often confused with MTTR but measures only the detection portion.

**RTO (Recovery Time Objective)** — The maximum acceptable time to restore service after a disruption. Drives disaster recovery design. Example: "Payment service RTO is 15 minutes — we must fail over to the DR region within that window."

**RPO (Recovery Point Objective)** — The maximum acceptable amount of data loss measured in time. Drives backup frequency. Example: "RPO of 5 minutes means we take transaction log backups every 5 minutes and can lose at most 5 minutes of data."

**Blast Radius** — The scope of impact when something fails. The goal is to minimize blast radius through isolation. Example: "By sharding the database by customer, a single shard failure only affects 10% of users instead of 100%."

**Change Failure Rate** — The percentage of deployments that cause an incident or require rollback. One of the four DORA metrics. Example: "Our change failure rate dropped from 15% to 4% after implementing canary deployments."

**Deployment Frequency** — How often code is deployed to production. One of the DORA metrics. Elite performers deploy multiple times per day. Example: "We moved from weekly deploys (14% change failure rate) to daily deploys (3% change failure rate) — smaller batches = fewer failures."

**Lead Time for Changes** — Time from code committed to code running in production. One of the DORA metrics. Example: "Lead time went from 3 days to 4 hours after adopting trunk-based development and feature flags."

---

## Incident Management

**Post-mortem** — A blameless, written analysis of an incident that documents what happened, why, the impact, and the remediation items. Example: "After the 45-minute payment outage, we wrote a post-mortem identifying the missing database index as the root cause and added it to the next sprint."

**Blameless Culture** — An organizational norm where incidents are treated as learning opportunities, not opportunities to assign fault. Engineers are comfortable escalating issues because they won't be punished for mistakes. Counterexample: "Who deployed that broken config?" → blameless version: "Our deploy system allowed a broken config to reach production — how do we prevent that?"

**5 Whys** — A root cause analysis technique: ask "why?" five times to drill past symptoms to the systemic cause. Example: "Why was the database down? The disk was full. Why? Log rotation was disabled. Why? The configuration management script had a bug. Why? It was manually edited and not tested. Why? We have no testing for infrastructure code." Root cause: no testing for infrastructure code changes.

**Incident Commander (IC)** — The person who coordinates the incident response. The IC delegates tasks, tracks the timeline, and communicates with stakeholders. The IC does NOT fix the problem — they keep the team organized.

**War Room** — A dedicated communication channel (video call, chat room) where the incident response team coordinates during a major incident. The war room is stood up by the IC and stood down when the incident is resolved.

**Status Page** — A public-facing page showing service health. Example: status.example.com. Updated by the incident communications lead during outages. Should be hosted on separate infrastructure so it remains available even during a major outage.

**Severity Levels (P0-P3)** — Standard incident classification:
- **P0 (Critical):** Complete service outage, data loss, security breach. All hands on deck.
- **P1 (High):** Major feature broken, severe degradation. Immediate response.
- **P2 (Medium):** Partial feature broken, workaround exists. Response within business hours.
- **P3 (Low):** Cosmetic issue, minor bug. Scheduled fix.

**Cascade Failure** — A failure in one component triggers failures in dependent components, which trigger further failures, cascading through the system. Example: A Redis cache outage causes all requests to hit the database directly, overwhelming the database, causing the entire application to fail.

**Runbook** — A documented, step-by-step procedure for handling a specific type of incident. Example: "Database connection pool exhaustion runbook" contains diagnosis commands, answers to common questions, and escalation paths.

---

## Reliability Patterns

**Circuit Breaker** — A pattern that prevents cascading failures by detecting when a downstream service is unhealthy and failing fast instead of making calls that will time out. States: Closed (normal), Open (failing fast), Half-Open (testing if downstream recovered). Example: Netflix Hystrix / resilience4j.

**Bulkhead** — Isolating resources so that a failure in one area doesn't exhaust resources needed by another. Named after ship compartments. Example: Separate thread pools for different API endpoints so a slow endpoint doesn't starve all threads.

**Backpressure** — A signal from a downstream service to an upstream service to slow down because it can't keep up. Example: Kafka consumer polling less frequently when processing is slow; Reactive Streams `request(N)`.

**Load Shedding** — Intentionally dropping some requests when the system is overloaded to preserve the ability to serve critical requests. Example: Returning HTTP 503 for non-critical endpoints while still serving the payment API when DB connections are exhausted.

**Graceful Degradation** — Continuing to serve a reduced set of functionality when dependencies are unavailable. Example: A product page shows cached prices and images but hides real-time inventory when the inventory service is down, instead of showing a 500 error.

**Fail-open vs Fail-closed** — In fail-open, the system allows access when the auth system is down (availability over security). In fail-closed, the system denies all access when the auth system is down (security over availability). Example: A building door lock: fail-open lets people exit during a fire; fail-closed keeps intruders out during a power outage.

**Retry Budget** — A limit on how many times a request can be retried to prevent retry storms. Example: "Maximum 3 retries with exponential backoff and jitter. After that, fail the request."

**Exponential Backoff** — Increasing the wait time between retries exponentially (1s, 2s, 4s, 8s...) to avoid overwhelming a recovering service. Must be combined with a maximum retry cap.

**Jitter** — Random variation added to retry delay to prevent retry synchronization (thundering herd on retries). Example: Instead of waiting exactly 1s, wait a random duration between 0.5s and 1.5s.

**Idempotency** — The property that an operation can be applied multiple times without changing the result beyond the first application. Essential for safe retries. Example: A payment request with an idempotency key — the payment processor deduplicates by the key so the customer is charged only once.

**Dead Letter Queue (DLQ)** — A queue where messages that repeatedly fail processing are moved. Example: An SQS message that has been received 5 times without being successfully deleted moves to the DLQ for manual inspection.

**Poison Pill** — A message that causes the consumer to crash or fail deterministically, which then retries indefinitely. Example: A malformed JSON message that throws an unhandled exception in the consumer every time. DLQs exist to quarantine poison pills.

---

## Performance

**Thundering Herd** — Many processes or threads waking up simultaneously in response to a single event, overwhelming the system. Example: A popular cache entry expires, and 1,000 application servers all try to recompute and cache it simultaneously.

**Cache Stampede** — A specific type of thundering herd: when a cached value expires and many concurrent requests all try to regenerate it. Mitigation: probabilistic early recomputation or request coalescing.

**Head-of-Line Blocking** — A request or message at the front of a queue blocks all subsequent items, even if they could be processed independently. Example: In HTTP/1.1, a slow response on one request blocks all subsequent requests on the same connection. HTTP/2 with multiplexing solves this.

**Utilization Cliff** — A system runs fine at 60% utilization, but performance collapses non-linearly above 80%. Example: A database with 50 connections runs at 10ms p99 latency. At 55 connections, p99 is 15ms. At 60 connections (the limit), p99 jumps to 5 seconds as requests queue waiting for a connection.

**Amdahl's Law** — The theoretical maximum speedup of a task is limited by the portion that cannot be parallelized. If 20% of a program is sequential, the maximum speedup with infinite parallelism is 5x (1 / 0.20).

**Little's Law** — L = λW: The average number of items in a system (L) equals the average arrival rate (λ) multiplied by the average time an item spends in the system (W). Example: If 1,000 requests arrive per second and each takes 50ms, the system has 50 requests in-flight at any given time.

**N+1 Query** — An ORM or data access pattern where fetching N related entities results in N additional queries. Example: Fetching 100 orders, then fetching the customer for each order individually (101 queries total instead of 1 JOIN query).

**Connection Pool Contention** — Multiple threads competing for a limited pool of database connections, causing threads to block waiting for connections. Example: A connection pool of 20 with 100 concurrent request threads — 80 threads are waiting at any given time.

**GC Pause** — Garbage Collection pause: the JVM/CLR/Go runtime stops all application threads to reclaim memory. Long GC pauses cause latency spikes. Example: A Java service with a 4GB heap and no GC tuning has 2-second stop-the-world pauses during full GC.

**Cold Start** — The initial period when a system has no cached data, compiled code, or warmed connections, resulting in higher latency. Example: A Lambda function invoked after 30 minutes of inactivity takes 3 seconds instead of 100ms because the execution environment must be initialized.

**Warm Start** — When a system already has caches populated, JIT-compiled code ready, and connection pools established, resulting in optimal performance. Example: A Lambda function invoked again within 5 minutes reuses the warm execution environment and responds in 100ms.

---

## Observability

**Four Golden Signals** — Google's four essential monitoring metrics:
1. **Latency:** How long it takes to serve a request. Measure p50, p95, p99.
2. **Traffic:** How much demand. Measure requests/sec, connections, I/O throughput.
3. **Errors:** The rate of failed requests. Distinguish explicit (HTTP 500) from implicit (HTTP 200 with wrong content).
4. **Saturation:** How full the service is. Measure CPU, memory, queue depth, connection count.

**RED Method** — A subset of Golden Signals focused on microservices: Rate (requests/sec), Errors (failure rate), Duration (latency distribution). Good for request-driven services.

**USE Method** — A resource-focused monitoring approach: Utilization (percent of resource used), Saturation (amount of queued work), Errors (hardware/software errors). Good for infrastructure: CPUs, disks, network interfaces.

**Histogram** — A Prometheus metric type that counts observations in configurable buckets. Example: An HTTP request duration histogram with buckets [0.01, 0.05, 0.1, 0.25, 0.5, 1, 5, 10] seconds. You can compute p50, p95, p99 from the bucket distribution.

**Summary** — A Prometheus metric type that calculates quantiles (p50, p95, p99) on the client side. Unlike histograms, summaries cannot be aggregated across instances. Prefer histograms in most cases.

**Counter** — A Prometheus metric type that only increases (or resets to zero). Used for counting events. Example: `http_requests_total`. Use `rate()` to compute per-second rates.

**Gauge** — A Prometheus metric type that can go up or down. Used for current values. Example: `node_memory_MemAvailable_bytes`, `go_goroutines`.

**Trace** — A recording of a request's path through a distributed system, composed of spans. Example: A trace for "GET /checkout" includes spans for the API gateway, auth service, cart service, payment service, and database queries.

**Span** — A single operation within a trace, with a start time, duration, and metadata. Example: A span for "SELECT * FROM carts WHERE user_id = ?" with duration 15ms, inside a trace for the checkout request.

**Sampling** — Only recording a subset of traces to control data volume. **Head sampling** decides whether to trace at the start (e.g., sample 1% of all requests). **Tail sampling** decides after the request completes (e.g., always sample errors and requests with p99+ latency).

**Structured Logging** — Writing logs as structured data (JSON) instead of free-form text, enabling machine parsing and analysis. Example: `{"timestamp": "2026-06-11T14:30:00Z", "level": "ERROR", "message": "Payment failed", "user_id": "abc-123", "order_id": "ord-456", "error": "insufficient_funds"}`.

**Correlation ID** — A unique identifier that follows a request across all services and logs, enabling end-to-end traceability. Example: An `X-Correlation-ID` HTTP header generated at the edge and propagated through all service calls.

**Cardinality** — The number of unique values for a metric label or log field. High cardinality labels (e.g., user IDs) cause memory explosion in time-series databases and should be avoided. Low cardinality labels (e.g., HTTP method: GET/POST/PUT/DELETE) are safe.

**Exemplar** — A reference to a specific trace from a metric point, linking metrics and traces. Example: A Prometheus histogram bucket with an exemplar pointing to the trace ID of the slowest request in that bucket.

---

## Deployment Strategies

**Blue-Green Deployment** — Running two identical environments (Blue = active, Green = idle). Switch traffic from Blue to Green after deploying new code to Green. Instant rollback by switching back. Costs: double infrastructure during deploy.

**Canary Deployment** — Gradually shifting a percentage of traffic to the new version while monitoring metrics. If error rates or latency spike, the canary is automatically rolled back. Example: 1% traffic → 10% → 50% → 100%, with metric checks at each step.

**Rolling Deployment** — Incrementally replacing instances of the old version with the new version, one at a time. Example: A Kubernetes rolling update kills 2 pods at a time and starts 2 new pods (maxSurge + maxUnavailable = 2).

**Feature Flag** — Deploying code behind a boolean flag that can be toggled at runtime without redeploying. Decouples deployment from release. Example: Code for a new checkout flow is deployed to all instances but only activated for 1% of users via a LaunchDarkly flag.

**Dark Launch** — Deploying code that runs in production but is invisible to users. The code processes real data, generates output, and the output is discarded after comparison with the existing system. Used to validate new systems with zero user impact.

**Rollback** — Reverting to a previous known-good version of code or configuration. **Roll-forward** is the alternative: deploying a fix for the broken version rather than reverting. Roll-forward is preferred when the fix is simple and the broken version was complex.

---

## Chaos Engineering

**Chaos Engineering** — The practice of deliberately injecting failures into a production system to verify its resilience. Goal: discover unknown weaknesses before they cause real incidents. Example: Terminating a random Kubernetes pod and observing that the service continues to handle requests within SLO.

**Chaos Monkey** — Netflix's original chaos engineering tool that randomly terminates instances during business hours. Extended by the Simian Army: Latency Monkey (adds delays), Conformity Monkey (terminates non-conforming instances), etc.

**Game Day** — A planned exercise where a team simulates a major incident and practices their response. Includes injecting failures, running through the incident management process, and writing a post-mortem. Example: "Today's game day: the primary database is unavailable. Can the team fail over to the read replica within the RTO?"

**Steady State** — The normal, healthy behavior of a system used as a baseline for chaos experiments. Example: "During steady state, the checkout endpoint serves 500 req/s with p99 latency of 120ms and 0.01% error rate." A chaos experiment succeeds if the steady state is maintained during the experiment.

---

## Networking

**Split-Horizon DNS** — Different DNS responses for the same hostname depending on the origin of the query. Internal clients resolve to private IPs; external clients resolve to public IPs. Example: `db.internal.example.com` resolves to 10.0.1.50 internally and NXDOMAIN externally.

**MTU (Maximum Transmission Unit)** — The largest packet size a network path can transmit without fragmentation. Ethernet default: 1500 bytes. Jumbo frames: 9000 bytes. Mismatched MTUs cause packet loss and mysterious connectivity issues (ICMP "fragmentation needed" messages may be blocked).

**SNI (Server Name Indication)** — A TLS extension that allows a client to specify the hostname it wants to connect to before the TLS handshake, enabling virtual hosting on a single IP. Required for HTTPS sites behind CDNs. Example: Without SNI, a server at 1.2.3.4 could only serve one TLS certificate. With SNI, it can serve different certs for api.example.com and app.example.com.

**mTLS (Mutual TLS)** — Both the client AND server present certificates to each other during the TLS handshake. Used for service-to-service authentication in zero-trust architectures. Example: In a service mesh (Istio/Linkerd), every service presents a certificate to every other service, and unauthorized calls are rejected at the network layer.

**TIME_WAIT** — A TCP state after a socket is closed (active close). The socket stays in TIME_WAIT for 2×MSL (typically 60 seconds) to ensure any delayed packets are handled. High TIME_WAIT counts on busy servers are normal. Can be a problem if >10,000 accumulate.

**CLOSE_WAIT** — A TCP state where the remote peer has closed the connection (sent FIN), but the local application hasn't called close() yet. CLOSE_WAIT connections that persist are a BUG — the application is leaking connections by not closing them.

**Conntrack** — Linux connection tracking table that stores state for each network connection (NAT mapping, state, timeout). If the table fills, new connections are dropped. Default size: 65,536 entries. For high-throughput NAT gateways, increase `nf_conntrack_max`.

**DNSSEC** — DNS Security Extensions: cryptographic signing of DNS records to prevent DNS spoofing/cache poisoning. Example: Without DNSSEC, an attacker can poison a DNS cache to redirect bank.example.com to their own IP. With DNSSEC, the resolver validates the signature and rejects the forged record.

**VPC Peering** — A private network connection between two VPCs that allows them to communicate using private IPs. Not transitive: if VPC A peers with VPC B and VPC B peers with VPC C, A cannot talk to C through B.

**Transit Gateway** — An AWS network hub that connects multiple VPCs and on-premises networks. Unlike VPC peering, Transit Gateway is transitive and scales to thousands of VPCs.

**NAT Gateway** — Allows instances in a private subnet to access the internet (outbound only) while preventing inbound connections. Managed by AWS. Charges per GB processed. For high-throughput: use VPC endpoints (see below) to avoid NAT gateway costs.

**VPC Endpoint** — A private connection from a VPC to AWS services (S3, DynamoDB, etc.) that doesn't traverse the public internet. Two types: Gateway Endpoint (S3, DynamoDB, free) and Interface Endpoint (everything else, charged per hour + per GB).

---

## Kubernetes

**Pod** — The smallest deployable unit in Kubernetes: one or more containers sharing a network namespace and storage. Pods are ephemeral — they can be killed and replaced at any time.

**Deployment** — A Kubernetes resource that manages a set of identical pods, providing rolling updates, scaling, and rollback. The most common way to run stateless applications.

**StatefulSet** — A Kubernetes resource for stateful applications (databases, Kafka, Zookeeper). Provides stable, unique pod identities (pod-0, pod-1, ...), persistent storage per pod, and ordered deployment/scaling. Each pod has its own PersistentVolumeClaim.

**DaemonSet** — A Kubernetes resource that ensures a copy of a pod runs on every (or some) nodes. Used for cluster-level services: log collectors (Fluentd, Filebeat), monitoring agents (Datadog agent, Prometheus Node Exporter), network plugins.

**ConfigMap** — A Kubernetes object that stores non-sensitive configuration as key-value pairs. Mounted as files in pods or injected as environment variables. Changes to ConfigMaps are eventually consistent (not immediate) for mounted files.

**Secret** — Similar to ConfigMap but for sensitive data (passwords, tokens, keys). Base64-encoded at rest (not encrypted by default — use etcd encryption or external secret management). Use `kubectl create secret` instead of declaring secrets in YAML.

**Readiness Probe** — A health check that determines if a pod is ready to serve traffic. If the readiness probe fails, the pod is removed from service endpoints. Used to prevent traffic from reaching pods that haven't finished initializing or that are in a degraded state.

**Liveness Probe** — A health check that determines if a pod needs to be restarted. If the liveness probe fails, Kubernetes kills and restarts the pod. Should be more lenient than the readiness probe — avoid killing pods that are temporarily slow.

**Startup Probe** — A health check for applications that have a long startup time. When a startup probe is configured, liveness and readiness probes are disabled until the startup probe succeeds. Prevents slow-starting apps from being killed by liveness probes.

**PDB (PodDisruptionBudget)** — A policy that limits the number of pods that can be voluntarily disrupted simultaneously during maintenance (e.g., node drains). Example: `minAvailable: 2` ensures at least 2 pods are always running during voluntary disruptions.

**Node Affinity** — A rule that constrains which nodes a pod can be scheduled on based on node labels. More expressive than `nodeSelector`. Supports "preferred" (soft) and "required" (hard) rules.

**Taint/Toleration** — A node taint repels pods that don't tolerate it. A toleration on a pod allows it to be scheduled on a tainted node. Example: Taint a GPU node with `gpu=true:NoSchedule` so only pods that explicitly tolerate the taint are scheduled there.
