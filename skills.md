Here's your comprehensive OpenCode prompt. This is engineered to generate a production-grade SRE troubleshooting repository:

---

## 🧠 OpenCode Prompt: 10X SRE Troubleshooting Repository

```
You are an expert Staff-level Site Reliability Engineer with 15+ years of experience at hyperscale companies (Netflix, Google, AWS, Meta). Your task is to scaffold a complete, production-grade SRE Troubleshooting Repository from scratch. This will be the definitive internal knowledge base for an on-call SRE team.

---

## REPOSITORY NAME: `sre-troubleshooting-bible`

## OBJECTIVE
Create a deeply detailed, well-structured GitHub repository that serves as the ultimate SRE troubleshooting reference. Every section must have real, battle-tested commands, code examples in Python, Java, and JavaScript (where applicable), real-world scenarios, runbooks, and explanations of root cause → symptom → fix chains.

---

## TOP-LEVEL DIRECTORY STRUCTURE

Create the following directories, each with its own README.md and subdirectories:

```
sre-troubleshooting-bible/
├── README.md                          # Master index with navigation
├── 00-foundations/
├── 01-linux-debugging/
├── 02-networking/
├── 03-aws/
├── 04-kubernetes-containers/
├── 05-databases/
├── 06-api-troubleshooting/
├── 07-error-codes/
├── 08-observability/
├── 09-performance/
├── 10-oncall-runbooks/
├── 11-10x-sre-playbooks/
├── 12-security-incidents/
├── 13-ci-cd/
├── 14-messaging-queues/
├── 15-scripts-toolkit/
└── GLOSSARY.md
```

---

## DETAILED SECTION SPECIFICATIONS

### `/README.md` (Root)
- A stunning, detailed master index
- Quick-reference table linking every section
- "How to use this repo during an incident" guide
- On-call checklist (first 5 minutes of an incident)
- Severity classification table (P0/P1/P2/P3) with response SLAs
- Links to all runbooks sorted by frequency of use

---

### `00-foundations/`
Files to create:
- `sre-mindset.md` — The SRE philosophy: error budgets, toil, SLOs/SLIs/SLAs with real calculation examples
- `incident-lifecycle.md` — Detection → Triage → Mitigation → Resolution → Post-mortem with timelines
- `blameless-postmortem-template.md` — Full postmortem template with all sections
- `oncall-survival-guide.md` — Mental model for being on-call, how to not panic, escalation paths
- `debugging-methodology.md` — The scientific method applied to debugging: hypothesis → test → observe loop. Include USE Method (Utilization, Saturation, Errors) and RED Method (Rate, Errors, Duration)

---

### `01-linux-debugging/`

#### `cpu/cpu-troubleshooting.md`
Cover every scenario with exact commands:
- High CPU: `top`, `htop`, `ps aux --sort=-%cpu`, `pidstat`, `perf top`, `mpstat -P ALL 1`
- CPU steal time in VMs: what it means, how to detect
- CPU wait (iowait): `iostat -x 1`, relationship to I/O
- Load average vs CPU usage distinction (critical concept)
- Runaway processes: how to find, safe kill procedures
- CPU affinity: `taskset`, NUMA topology with `numactl`
- Code examples:
  - Python: script using `psutil` to monitor CPU per-process with alerting threshold
  - Java: thread dump analysis, JVM CPU profiling with async-profiler commands
  - JS/Node: `--prof` flag, `0x` flamegraph tool usage

#### `memory/memory-troubleshooting.md`
- OOM killer: reading `/var/log/kern.log`, `dmesg | grep -i oom`, understanding OOM scores
- Memory leak detection: `valgrind`, `pmap`, `smaps`, `/proc/[pid]/status`
- Swap usage: when it's fine, when it's catastrophic
- Page cache vs actual memory usage: `free -h` interpretation, `vmstat`
- `slabtop` for kernel memory
- Huge pages: THP issues, how to disable
- Python: `tracemalloc` example for leak detection
- Java: heap dump analysis with `jmap`, `jstat -gcutil`, MAT tool commands
- JS: V8 heap snapshot analysis, `--max-old-space-size`

#### `disk/disk-troubleshooting.md`
- Disk full emergencies: `df -h`, `du -sh /*`, `ncdu`, finding largest files/dirs
- Inode exhaustion: `df -i`, why it's as bad as disk full, how to find culprits
- I/O bottlenecks: `iostat -xz 1`, `iotop`, `blktrace`, `fio` benchmarks
- Filesystem corruption: `fsck` procedures, when to run, when NOT to
- `/proc/diskstats` interpretation
- Disk latency percentiles: `ioping`
- LVM operations under pressure: extending volumes live
- Python: disk monitoring script with alert on >85% usage or inode exhaustion
- Java: NIO file operations and detecting disk errors

#### `processes/process-troubleshooting.md`
- Zombie processes: what causes them, how to reap, when to worry
- Defunct processes vs zombie: distinction
- `strace` mastery: tracing syscalls, `-p PID`, `-e trace=network`, timing with `-T`
- `lsof` mastery: open files, network connections, deleted-but-open files
- `fuser`: find what's using a file/port
- Process limits: `ulimit -a`, `/etc/security/limits.conf`, `systemctl show service --property=LimitNOFILE`
- Signal handling: SIGTERM vs SIGKILL, graceful shutdown patterns
- `/proc/[pid]/` filesystem deep dive: `fd`, `maps`, `net`, `status`, `cmdline`

#### `network/linux-network-debugging.md`
- `ss -tulnp` vs `netstat` (netstat is legacy — explain why)
- TCP state machine: TIME_WAIT accumulation, CLOSE_WAIT stuck connections
- `tcpdump` recipes: capture by port, host, filter SYN/FIN, write to pcap
- `wireshark` on remote server: how to capture and analyze locally
- `curl -v` and `curl --trace`: reading full HTTP negotiation
- Network namespace debugging: `ip netns`
- MTU issues: fragmentation, `ping -M do -s 1472`
- Connection tracking: `conntrack -L`, table full issues
- iptables: reading rules, tracing packet flow, common gotchas
- `ethtool`: NIC stats, ring buffer drops
- Python: TCP connection health checker with timeout and retry logic
- JS: `net` module connection debugging

#### `logs/log-analysis.md`
- `journalctl` mastery: `-u service`, `--since`, `--until`, `-f`, `-p err`, `--no-pager`
- `grep` power patterns: `-E` extended regex, `-A/-B/-C` context, `-v` invert
- `awk` for log parsing: extract fields, sum values, count occurrences
- `sed` for log transformation
- Log rotation: `logrotate` config, troubleshooting rotation failures
- Finding patterns across millions of lines: `grep -c`, `sort | uniq -c | sort -rn`
- Syslog facility and severity levels
- Python: log parser script with regex extraction and anomaly detection
- Real scenario: "Find all IPs that hit 429 in the last hour from nginx access logs"

#### `systemd/systemd-troubleshooting.md`
- `systemctl status`, `is-active`, `is-failed`, `list-units --failed`
- Unit file anatomy: ExecStart, Restart policies, dependencies
- Service won't start: full diagnostic flow
- Socket activation debugging
- Cgroup resource limits via systemd
- Timer units vs cron: migration and debugging

---

### `02-networking/`

#### `dns/dns-troubleshooting.md`
- `dig` deep dive: `+trace`, `+short`, `@resolver`, `ANY` queries, SOA records
- `nslookup` vs `dig` (dig wins, explain why)
- `host` command
- DNS propagation delays: TTL math, why changes don't take effect
- Split-horizon DNS: internal vs external resolution differences
- `/etc/resolv.conf` and `/etc/nsswitch.conf` interaction
- DNS over TCP vs UDP: when fallback happens (>512 bytes)
- DNSSEC validation failures
- Common DNS errors and exact causes:
  - SERVFAIL: upstream resolver failure
  - NXDOMAIN: authoritative says doesn't exist
  - REFUSED: resolver policy rejection
  - TIMEOUT: what network path fails here
- Scenario: "App can't resolve internal service — full 10-step diagnostic"
- Python: DNS lookup script with fallback resolvers and timing
- Java: `InetAddress` debugging, custom DNS resolver

#### `tls-ssl/tls-troubleshooting.md`
- `openssl s_client -connect host:443 -showcerts`: reading certificate chains
- Certificate expiry checking: `openssl x509 -noout -dates`
- SNI issues: `-servername` flag importance
- Certificate chain validation failures: intermediate cert missing
- TLS version negotiation: forcing TLS 1.2 vs 1.3
- Cipher suite debugging
- mTLS: client certificate issues
- `curl --cert`, `--key`, `--cacert` usage
- Python: `ssl` module certificate verification bypass (dev only!) and proper verification
- Java: keystore/truststore debugging, `javax.net.debug=ssl`

#### `loadbalancer/lb-troubleshooting.md`
- Health check failures: HTTP vs TCP health checks
- Session persistence/sticky sessions issues
- Connection draining during deployments
- 502/503/504 from LB perspective: what each means at LB layer
- ELB/ALB/NLB (AWS) specific troubleshooting
- HAProxy: stats page, `show stat`, log format interpretation
- Nginx upstream: `upstream timed out`, `no live upstreams`, `connect() failed`

---

### `03-aws/`

#### `ec2/ec2-troubleshooting.md`
- Instance not reachable: SSH debug flow (Security Group → NACL → Route Table → IGW → Key → OS)
- EC2 console screenshot for hung instances
- Instance metadata service (IMDS): `curl http://169.254.169.254/latest/meta-data/`
- CloudWatch agent: missing metrics troubleshooting
- EBS volume performance: IOPS limits, burst bucket, `gp2` vs `gp3` differences
- Instance types and CPU credits: T-series burstable, credit exhaustion
- ENI limits, IP exhaustion in subnets
- Placement groups: spread vs partition vs cluster
- `ec2-instance-connect` vs SSH key auth issues

#### `rds/rds-troubleshooting.md`
- Connection limit exhaustion: `max_connections`, RDS proxy as solution
- Slow queries: Performance Insights, `slow_query_log`, `EXPLAIN ANALYZE`
- Failover behavior: Multi-AZ, CNAME flip timing (60-120s)
- Read replica lag: `ReplicaLag` CloudWatch metric, causes
- IOPS throttling: burst balance, storage autoscaling
- Deadlocks: how to detect, read deadlock logs
- Parameter group changes: static vs dynamic parameters
- Backup/restore RTO/RPO math
- Python: RDS connection pool with retry on failover (using `tenacity`)
- Java: JDBC connection pool (HikariCP) with failover config

#### `ecs-eks/container-orchestration.md`
- ECS task failing to start: image pull errors, IAM task role, resource limits
- ECS service stuck in DRAINING
- ECS service event log reading
- EKS: `kubectl` debugging commands (full cheatsheet)
- Pod stuck in Pending: resource requests vs node capacity, `kubectl describe pod`
- CrashLoopBackOff: reading logs, exit codes meaning
- ImagePullBackOff: ECR auth, network policy, image tag doesn't exist
- OOMKilled: finding the right memory limit
- Liveness vs Readiness vs Startup probes: misconfigurations
- Service not routing: Endpoints object, kube-proxy rules
- ConfigMap/Secret not mounting: permissions, volume syntax
- Python: Kubernetes client health checker script
- YAML: complete deployment with all best practices (requests/limits, probes, PDB)

#### `s3/s3-troubleshooting.md`
- 403 Forbidden anatomy: bucket policy vs IAM policy vs ACL vs Block Public Access — which wins
- S3 request throttling (503 Slow Down): prefix hashing strategy
- Eventual consistency (now strong consistency — explain the 2020 change)
- Cross-account access patterns and common mistakes
- S3 Transfer Acceleration vs direct upload
- Presigned URL expiry issues
- Lifecycle policy not running: common config mistakes
- Python: S3 retry logic with exponential backoff using `boto3`

#### `iam/iam-troubleshooting.md`
- "Access Denied" forensics: `aws sts get-caller-identity`, CloudTrail
- Policy evaluation logic: explicit deny > allow, permission boundaries
- AssumeRole chain debugging
- Service-linked roles
- Instance profile not attached: `curl` IMDS to verify
- Cross-account role trust policies

#### `cloudwatch/cloudwatch-troubleshooting.md`
- Alarm stuck in INSUFFICIENT_DATA
- Metric math: common patterns
- Log Insights query language: top N, stats, filter patterns
- Custom metrics via CloudWatch agent
- Missing logs: agent config, IAM permissions
- Log group retention settings

#### `vpc/vpc-networking.md`
- VPC Flow Logs: reading ACCEPT/REJECT entries, decoding format
- Security Group vs NACL: stateful vs stateless, evaluation order
- VPC Peering: route table requirements, overlapping CIDR
- Transit Gateway: route propagation issues
- NAT Gateway: connectivity from private subnet, SNAT exhaustion
- VPC Endpoints: interface vs gateway, DNS resolution for S3/DynamoDB

---

### `04-kubernetes-containers/`

#### `kubectl-cheatsheet.md`
Every essential kubectl command with explanation:
- `get`, `describe`, `logs`, `exec`, `port-forward`, `cp`
- `top nodes`, `top pods`
- `rollout status`, `rollout history`, `rollout undo`
- `cordon`, `drain`, `uncordon`
- `get events --sort-by=.lastTimestamp`
- JSONPath and custom columns
- Debug containers: `kubectl debug`

#### `container-debugging.md`
- Docker: `docker stats`, `docker inspect`, `docker events`, `docker logs --tail --follow`
- Container exit codes: 0, 1, 137 (OOM/SIGKILL), 143 (SIGTERM), 126 (permission), 127 (not found)
- Dockerfile best practices that prevent incidents
- Multi-stage builds: debugging intermediate layers
- `nsenter` to enter container namespaces from host
- `docker system df` and cleanup

#### `helm-troubleshooting.md`
- Release stuck in pending-upgrade
- `helm diff`, `helm rollback`
- Chart rendering errors: `helm template --debug`
- Values override precedence

---

### `05-databases/`

#### `postgresql/postgres-troubleshooting.md`
- `pg_stat_activity`: finding long-running queries, idle in transaction
- Lock contention: `pg_locks` join with `pg_stat_activity`
- Autovacuum: when it's not keeping up, table bloat, `pg_stat_user_tables`
- Connection exhaustion: `max_connections`, `pg_bouncer` config
- WAL: replication lag, WAL archiving failures
- `EXPLAIN (ANALYZE, BUFFERS)` deep reading
- Bloat: `pgstattuple`, `VACUUM FULL` vs regular VACUUM
- Index bloat and rebuild
- Python: psycopg2 connection pool with health check
- Java: JDBC PreparedStatement and N+1 detection

#### `mysql/mysql-troubleshooting.md`  
- `SHOW PROCESSLIST`, `SHOW ENGINE INNODB STATUS`
- Deadlock detection from InnoDB status
- Replication: `SHOW SLAVE STATUS`, lag causes, how to fix
- Table locking: MyISAM vs InnoDB differences
- Query cache (deprecated in 8.0 — explain why and alternatives)
- Binary log: point-in-time recovery
- `pt-query-digest` from Percona toolkit

#### `redis/redis-troubleshooting.md`
- `redis-cli INFO all` sections: memory, stats, replication
- Memory eviction policies: when keys disappear
- `SLOWLOG GET`: finding slow commands
- `MONITOR`: live command stream (careful in prod!)
- Key expiry not working: TTL debugging
- Cluster: slot migration, `CLUSTERINFO`
- `OBJECT ENCODING`: memory optimization
- Python: Redis connection with retry and circuit breaker pattern
- Java: Jedis/Lettuce connection pool

---

### `06-api-troubleshooting/`

#### `rest-api/rest-debugging.md`
- Full `curl` debugging toolkit:
  - `-v` verbose, `--trace-ascii`, `-w` write-out format for timing breakdown
  - `-H` headers, `-d` body, `--compressed`
  - Timing: `time_namelookup`, `time_connect`, `time_appconnect`, `time_pretransfer`, `time_starttransfer`, `time_total`
- Request/response cycle: what happens at each network hop
- Idempotency: GET/PUT/DELETE vs POST
- REST versioning strategies and their failure modes
- Pagination: cursor vs offset and their edge cases
- Rate limiting patterns: token bucket, leaky bucket, fixed window
- Retry logic: exponential backoff with jitter (full Python and Java examples)
- API gateway issues: timeout chains, request/response transformation errors
- Python: `requests` session with retry adapter, timeout, cert verification
- Java: `HttpClient` (Java 11+) with retry and timeout
- JS: `axios` interceptors for retry and error normalization

#### `graphql/graphql-troubleshooting.md`
- N+1 problem: detection via query analysis, DataLoader solution
- Query complexity limits: depth limiting, cost analysis
- Introspection abuse: disabling in prod
- Schema stitching errors
- Subscriptions: WebSocket connection issues
- Error format in GraphQL: how it differs from REST (always 200, check `errors` field)
- Python: `strawberry` / `graphene` debugging
- JS: Apollo Server error handling

#### `grpc/grpc-troubleshooting.md`
This is critical — cover deeply:
- gRPC status codes full reference:
  - OK (0), CANCELLED (1), UNKNOWN (2), INVALID_ARGUMENT (3), DEADLINE_EXCEEDED (4), NOT_FOUND (5), ALREADY_EXISTS (6), PERMISSION_DENIED (7), RESOURCE_EXHAUSTED (8), FAILED_PRECONDITION (9), ABORTED (10), OUT_OF_RANGE (11), UNIMPLEMENTED (12), INTERNAL (13), UNAVAILABLE (14), DATA_LOSS (15), UNAUTHENTICATED (16)
  - For each: what triggers it, what to check, example fix
- `grpcurl` tool: listing services, making calls, metadata headers
- Protobuf: field number changes causing silent breakage, `unknown fields`
- Deadline propagation: how deadlines cascade through service mesh
- Streaming: client/server/bidi stream issues, flow control
- Load balancing in gRPC: why HTTP/2 multiplexing breaks L4 LB
- TLS in gRPC: cert issues, channel credentials
- Interceptors: logging, retry, auth — debugging them
- Channel connectivity: `IDLE`, `CONNECTING`, `READY`, `TRANSIENT_FAILURE`, `SHUTDOWN`
- Python: gRPC client with deadline, metadata, retry interceptor
- Java: gRPC stub with deadline and error handling
- JS: `@grpc/grpc-js` basic client debugging

#### `websockets/websocket-troubleshooting.md`
- Connection upgrade failures: HTTP 101, missing headers
- Proxy/LB timeout on idle connections: keepalive ping config
- Message frame size limits
- Reconnection logic with exponential backoff
- JS: WebSocket client with reconnection and heartbeat

#### `message-queues-as-api/async-api-troubleshooting.md`
- Consumer lag: cause, measurement, remediation
- Dead letter queues: when and why messages end up there
- Message ordering guarantees: SQS FIFO vs Standard
- Idempotency keys for async processing
- Poison pills: messages that always fail

---

### `07-error-codes/`

This section is a complete reference. For each HTTP error code:
- **Exact technical definition**
- **Common causes ranked by frequency**
- **Debugging steps in order**
- **Code-level fixes (Python, Java, JS)**
- **Infrastructure fixes**
- **Monitoring: what metric/alert to set up**

#### `4xx/client-errors.md`
**400 Bad Request**
- Malformed JSON, missing required fields, type mismatch, encoding issues
- How to diagnose from server logs vs client-side
- Validation framework examples: Python (pydantic), Java (Bean Validation), JS (zod/joi)

**401 Unauthorized**
- Token expired vs token invalid vs token missing
- JWT: `exp`, `iat`, `nbf` field issues; signature verification failure
- OAuth flow failures: scope mismatch, refresh token expiry
- API key rotation causing 401 spike
- Python: JWT decode with all validation checks
- Java: Spring Security filter chain debugging

**403 Forbidden**
- Authentication succeeded, authorization failed — key distinction from 401
- RBAC: missing role, wrong scope
- IP allowlist/blocklist
- CORS preflight resulting in 403 — this is a common confusion
- AWS 403: S3 policy, IAM, resource policy evaluation

**404 Not Found**
- Route not registered vs resource doesn't exist vs resource deleted
- Trailing slash issues in routers
- Case sensitivity in URLs
- Soft delete vs hard delete and client caching stale references

**405 Method Not Allowed**
- CORS preflight OPTIONS not handled
- Route registered for wrong verb
- HATEOAS links wrong

**408 Request Timeout**
- Client didn't send body in time
- Slow POST body upload
- LB idle timeout shorter than client think-time

**409 Conflict**
- Optimistic locking failure: `version` field mismatch
- Duplicate key on insert
- State machine violation (trying to ship an already-shipped order)
- Python: retry-on-409 with fresh GET pattern

**410 Gone**
- Permanent removal vs temporary (404)
- When to use 410 over 404: SEO and API versioning

**422 Unprocessable Entity**
- Syntactically valid but semantically wrong
- Business rule validation failures
- File format valid but content invalid (e.g., password too short)

**429 Too Many Requests**
- Rate limiting: per-user, per-IP, per-endpoint, global
- `Retry-After` header: how to read and honor it
- Different algorithms: token bucket, sliding window, fixed window — which gives what behavior
- Client-side handling: exponential backoff, circuit breaker
- Distinguishing 429 from upstream vs from your app
- Python: retry with Retry-After header respect
- Java: resilience4j RateLimiter
- JS: axios-retry with 429 handling

**499 Client Closed Request** (Nginx specific)
- Client disconnected before response
- Mobile clients, user navigation, load test tools
- When to worry (spike means your responses are too slow)
- Correlation with p99 latency

#### `5xx/server-errors.md`
**500 Internal Server Error**
- Unhandled exception: need centralized error handling
- NullPointerException / AttributeError in production
- Database query returning unexpected result type
- Environment variable missing
- Reading stack traces: Python traceback, Java exception chain, JS Error.stack
- Centralized error handlers: Python (Flask/FastAPI), Java (Spring @ControllerAdvice), JS (Express error middleware)

**502 Bad Gateway**
- Upstream returned invalid response
- Upstream crashed mid-response
- LB <-> upstream protocol mismatch
- Nginx: `upstream sent invalid header`
- Distinguishing 502 (upstream bad response) vs 503 (upstream unreachable) vs 504 (upstream too slow)
- Full diagnostic tree

**503 Service Unavailable**
- All upstreams down: health checks failing
- Upstream pool exhausted: connection limits
- Circuit breaker OPEN state
- Deployment with zero instances briefly
- Scheduled maintenance (proper 503 with Retry-After)
- Kubernetes: no ready pods
- Cascade failure: your dependency is 503, causing you to 503
- Python: requests to check upstream health before serving
- Java: Spring Boot Actuator health endpoint custom check

**504 Gateway Timeout**
- Upstream didn't respond in time
- LB timeout < app processing time: timeout waterfall (always configure outer > inner)
- Database query running long
- External API call not completing
- Different timeouts: connect timeout vs read timeout — understand the difference
- Timeout hierarchy diagram: Client → CDN → LB → App → DB (each layer's timeout setting)

**507 Insufficient Storage**
- Disk full on app server
- File upload target full
- Database storage full

**508 Loop Detected**
- Redirect loops
- Service mesh misconfiguration
- Nginx proxy_pass loop

#### `grpc-status-codes/grpc-errors.md`
Map each gRPC status code to HTTP equivalent and real-world scenarios (reference the gRPC section in 06)

#### `dns-errors/dns-error-reference.md`
- NXDOMAIN, SERVFAIL, REFUSED, TIMEOUT, FORMERR — each with causes and fixes

#### `tls-errors/tls-error-reference.md`
- SSL_ERROR_RX_RECORD_TOO_LONG
- CERTIFICATE_VERIFY_FAILED
- SSL: WRONG_VERSION_NUMBER
- ERR_CERT_COMMON_NAME_INVALID
- Each: openssl diagnostic command + fix

---

### `08-observability/`

#### `metrics/metrics-guide.md`
- Four Golden Signals: Latency, Traffic, Errors, Saturation — how to instrument each
- RED Method: Rate, Errors, Duration — for services
- USE Method: Utilization, Saturation, Errors — for resources
- Histogram vs Summary: when to use which, quantile accuracy
- Counter vs Gauge vs Histogram vs Summary
- Prometheus query language (PromQL) cookbook:
  - Rate calculation: `rate()` vs `irate()`
  - Percentile from histogram: `histogram_quantile(0.99, ...)`
  - Alerting rules: `ALERTS` metric, `for` duration
  - Recording rules for expensive queries
- Python: `prometheus_client` instrumentation of a Flask app
- Java: Micrometer with Prometheus registry

#### `logging/structured-logging.md`
- Why structured logging (JSON) over plaintext
- Required fields: timestamp, level, service, trace_id, span_id, user_id, request_id
- Correlation IDs: how to propagate through async calls
- Log levels: when to use DEBUG/INFO/WARN/ERROR/FATAL
- Avoiding log noise: sampling high-volume logs
- Python: structlog configuration with context vars
- Java: Logback + MDC for correlation ID propagation
- JS: winston with JSON format and request middleware

#### `tracing/distributed-tracing.md`
- OpenTelemetry: spans, traces, context propagation
- Trace sampling strategies
- Finding the slow span in a distributed trace
- Correlation: linking traces to logs to metrics
- Python: OpenTelemetry auto-instrumentation
- Java: OpenTelemetry Java agent
- JS: OpenTelemetry Node.js SDK

#### `dashboards/dashboard-design.md`
- Alert fatigue: what it is and how to fight it
- Dashboard layout: overview → service → instance drill-down
- SLO dashboards: error budget burn rate alerting
- Grafana: variable templating, exemplars, alert annotations

---

### `09-performance/`

#### `profiling/application-profiling.md`
- Python: cProfile, py-spy (zero-overhead sampling), memory_profiler
- Java: async-profiler, JFR (Java Flight Recorder), heap dump analysis
- JS/Node: V8 profiler, clinic.js, `--inspect` with Chrome DevTools
- Flame graphs: how to read them, what flat top means

#### `load-testing/load-testing-guide.md`
- `wrk`, `ab`, `k6`, `locust` — when to use each
- Interpreting results: throughput, p50/p95/p99 latency, error rate
- Finding the breaking point: ramp-up patterns
- Locust: Python-based user scenarios
- k6: JS scripting
- Common load test mistakes that give false results

#### `bottleneck-analysis/bottleneck-guide.md`
- Amdahl's Law: why you can't always scale out
- Queueing theory basics: Little's Law (L = λW), utilization cliff at >80%
- Finding the bottleneck: CPU → Memory → I/O → Network → External call
- Database: N+1 queries, missing indexes, connection pool saturation
- Thread pool exhaustion in Java: Tomcat/Netty thread pool tuning

#### `caching/caching-strategies.md`
- Cache-aside, write-through, write-behind, read-through
- Cache stampede: thundering herd problem and solutions (mutex lock, probabilistic early expiry)
- Cache invalidation: the second hard problem in CS
- Negative caching: caching 404s/empty results
- CDN: cache-control headers, purging, Vary header complexity
- Python: Redis cache decorator with TTL and stale-while-revalidate
- Java: Spring Cache with Caffeine (local) + Redis (distributed)

---

### `10-oncall-runbooks/`

Create individual runbook files for each scenario:

#### `high-error-rate.md`
Step-by-step: detect → isolate (which service?) → find root cause → mitigate → fix → verify → monitor

#### `high-latency.md`
Latency spike investigation flow: is it all endpoints or one? DB slow? External call? What percentile?

#### `service-down.md`
Complete "service is returning 503" runbook from alarm to all-clear

#### `database-connection-exhaustion.md`
Immediate mitigation (kill idle connections) → root cause → permanent fix

#### `disk-full-emergency.md`
Step 1: buy time (remove logs, tmp files) → find culprit → permanent fix

#### `memory-leak-in-production.md`
Detect → capture heap dump → analyze → rolling restart as mitigation → fix deployment

#### `ddos-under-attack.md`
Initial response → AWS Shield/WAF activation → blocking patterns → coordinate with upstream

#### `ssl-cert-expired.md`
Full renewal runbook for different scenarios: Let's Encrypt, AWS ACM, manual cert

#### `deployment-rollback.md`
When to rollback vs when to fix-forward → Kubernetes rollback → ECS rollback → database migration rollback

#### `cascading-failure.md`
Recognizing cascade → circuit breaker activation → load shedding → partial degradation mode

---

### `11-10x-sre-playbooks/`

#### `10x-mindset.md`
- The 5 Whys technique with a real production incident example
- Blameless culture: psychological safety in incident response
- Communication during incidents: status page updates, stakeholder updates
- Toil identification and elimination
- SLO as a decision-making framework: error budget driving risk tolerance

#### `advanced-debugging-tricks.md`
**The Dark Arts of Production Debugging:**
- `gdb` attach to running process (with caveats)
- `perf record -g` + flamegraph.pl: CPU profiling without instrumentation
- `bpftrace` one-liners for zero-overhead tracing:
  - "Which process is doing the most syscalls?"
  - "What files is this app opening?"
  - "Show me TCP connections being made"
- `tcpdump` + `tshark` scripted analysis
- `/proc` filesystem as a debugging interface
- `inotifywait`: watch files for changes
- `auditd`: security and debugging syscall audit
- Port knocking and temporary firewall rules for debugging access

#### `chaos-engineering.md`
- Principles of Chaos (Netflix model)
- `tc netem`: inject latency, packet loss, corruption from command line
- `stress-ng`: CPU, memory, I/O stress
- `toxiproxy`: TCP proxy with built-in failure modes
- Designing chaos experiments: hypothesis → blast radius → abort criteria
- Python: simple chaos monkey script for ECS/K8s

#### `capacity-planning.md`
- Traffic forecasting: seasonal patterns, growth rate
- Resource extrapolation from current metrics
- Headroom calculation
- Load testing to find limits
- Database IOPS planning for RDS

#### `incident-command.md`
- Incident Commander role: what it is, what it isn't
- Communication channels: war room setup, status page, internal comms
- Delegation: who does what
- Timeline keeping: why it matters for post-mortem
- Severity escalation criteria

---

### `12-security-incidents/`

#### `auth-breach-response.md`
- Detecting credential stuffing attacks from logs
- Immediate response: force logout, rotate secrets
- CloudTrail: finding what was accessed
- IAM: reviewing and revoking credentials

#### `secrets-leaked.md`
- Secret in git: GitHub secret scanning, `git-secrets`
- Rotation playbook: priority order (most critical first)
- Vault/AWS Secrets Manager: detecting unauthorized access

#### `dependency-vulnerability.md`
- CVE triage: CVSS score interpretation
- Python: `safety`, `pip-audit`
- Java: `OWASP Dependency Check`
- JS: `npm audit`, `snyk`
- Patch deployment strategies: canary, blue-green

---

### `13-ci-cd/`

#### `pipeline-failures.md`
- Flaky tests: detection, quarantine, root cause
- Docker build cache invalidation: why it's slower than expected
- Registry push failures: credentials, disk space, rate limits
- Deployment stuck: health check timing, rollback triggers
- GitHub Actions / Jenkins / GitLab CI specific debugging

#### `deployment-strategies.md`
- Blue-Green: benefits, costs, when to use
- Canary: traffic splitting, metric thresholds for promotion
- Rolling: disruption vs speed tradeoff
- Feature flags: decoupling deploy from release

---

### `14-messaging-queues/`

#### `kafka/kafka-troubleshooting.md`
- Consumer lag: `kafka-consumer-groups.sh --describe`
- Partition imbalance: leader distribution
- Under-replicated partitions: `kafka-topics.sh --describe --under-replicated-partitions`
- Message too large: `message.max.bytes` vs `replica.fetch.max.bytes`
- Consumer group rebalancing storms
- Topic retention: data loss when retention too short
- Python: kafka-python consumer with offset management
- Java: kafka-clients consumer with error handling

#### `sqs/sqs-troubleshooting.md`
- Message visibility timeout: messages appearing twice
- Dead letter queue depth: monitoring and alerting
- Long polling vs short polling: cost and latency
- FIFO vs Standard: ordering guarantees and deduplication
- Message attributes for routing
- Python: boto3 SQS consumer with DLQ handling

---

### `15-scripts-toolkit/`

A collection of ready-to-run scripts:

#### Python scripts:
- `health_checker.py` — HTTP health check with retries, alerts
- `log_analyzer.py` — Parse nginx/app logs, extract error patterns, output summary
- `aws_cost_spike_detector.py` — CloudWatch cost anomaly check
- `db_connection_pool_monitor.py` — Check RDS connections vs max
- `cert_expiry_checker.py` — Check SSL cert expiry for list of domains
- `rate_limit_respecter.py` — API client that reads Retry-After and backs off
- `service_dependency_checker.py` — Check all dependencies are healthy before starting

#### Bash scripts:
- `disk_emergency.sh` — Find and safely remove large files to buy time
- `process_killer.sh` — Find and kill runaway processes by CPU threshold
- `log_tail_multi.sh` — Tail multiple log files with service name prefix
- `aws_ecs_redeploy.sh` — Force new deployment with health check wait
- `k8s_pod_debug.sh` — Interactive pod debugger (shows logs, events, describe)
- `tcp_connection_audit.sh` — Show all connections grouped by state and remote IP

#### `GLOSSARY.md`
Comprehensive glossary of every term used: SLO, SLI, SLA, MTTR, MTBF, RTO, RPO, toil, error budget, blast radius, circuit breaker, bulkhead, backpressure, head-of-line blocking, thundering herd, cache stampede, blue-green, canary, chaos engineering, observability, cardinality, and 50+ more.

---

## FORMATTING STANDARDS (apply to every .md file)

1. Every .md file starts with a metadata block:
```markdown
# Title
> **Category:** Linux | AWS | API | etc.  
> **Difficulty:** Basic | Intermediate | Advanced  
> **Last Reviewed:** YYYY-MM  
> **Tags:** `#oncall` `#linux` `#memory` etc.
```

2. Use callout blocks throughout:
```markdown
> ⚠️ **WARNING:** Running this in production will...
> 💡 **PRO TIP:** 10x engineers always check X before Y
> 🚨 **INCIDENT SIGNAL:** If you see X, it means Y
> ✅ **QUICK WIN:** This single command will tell you 80% of what you need
```

3. Every scenario follows this template:
```markdown
## Scenario: [Title]
**Symptoms:** What the engineer observes
**Impact:** User-facing effect
**Diagnosis Commands:** (in order, fastest to most invasive)
**Root Cause:** The actual thing that went wrong
**Fix:** Immediate mitigation + permanent fix
**Prevention:** How to prevent recurrence
**Code Example:** (Python/Java/JS as applicable)
```

4. Code blocks ALWAYS specify language:
```python
# Python example with full context — not just snippets
```

5. Every section ends with:
- "Related Sections" links
- "Further Reading" with book/doc references

---

## QUALITY REQUIREMENTS

- Every command must have a comment explaining what it does and what to look for in the output
- Real error messages must be included (not just "error occurred")
- Timing matters: specify "this takes 2-3 seconds" vs "this may take 10 minutes"
- Safe vs destructive commands must be clearly marked
- All AWS CLI commands must include the region flag pattern
- Python code must be Python 3.10+ compatible
- Java examples use Java 17 LTS
- Every runbook must have an "abort criteria" — when to escalate instead of continuing

---

## WHAT TO BUILD FIRST (Priority Order)

1. Repository scaffold (all directories and empty READMEs)
2. `07-error-codes/` — highest daily value
3. `01-linux-debugging/` — foundation of everything
4. `06-api-troubleshooting/grpc/` — most requested
5. `10-oncall-runbooks/` — immediate incident value
6. Remaining sections

Begin with the full scaffold, then populate each section in priority order. Do not create placeholder stubs — every file must have real, actionable content.
```

---

This prompt will generate a repository that covers every major SRE domain. A few tips for using it with OpenCode:

- **Run it in sections** — paste the full prompt but tell it to start with the scaffold + one section at a time to avoid token limits
- **Iterate the error codes section** separately — it's the highest-value section and deserves extra depth
- **For the scripts toolkit**, ask OpenCode to actually run and test the scripts before committing them
- You can add to the prompt: *"also add a `CHANGELOG.md` and `CONTRIBUTING.md` so teammates can submit their own incident learnings"* — that turns it into a living document