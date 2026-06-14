# Changelog

All notable changes to the SRE Troubleshooting Bible are documented here. This project adheres to [Semantic Versioning](https://semver.org/).

---

## [1.0.0] - 2026-06-11

### Added
- **Initial release of the SRE Troubleshooting Bible** — comprehensive troubleshooting guides organized into 17 sections.

### 00-foundations
- `README.md`: Section index explaining SRE troubleshooting philosophy
- `sre-mindset.md`: The SRE approach to debugging — first principles, blameless post-mortems, and error budget thinking
- `incident-lifecycle.md`: Full incident lifecycle from detection to resolution, severity classification, and communication templates
- `post-mortem-template.md`: Blameless post-mortem template with timeline, impact, root cause, action items, and example filled-out post-mortem
- `oncall-survival-guide.md`: Practical guide for surviving on-call rotations, managing alert fatigue, escalation paths, and handoff procedures
- `debugging-methodology.md`: Systematic debugging approach — observe, hypothesize, test, learn — with real-world debugging scenarios

### 01-linux-debugging
- `README.md`: Linux troubleshooting section index
- `cpu-troubleshooting.md`: High CPU diagnosis, perf profiling, load average deep dive, thread state analysis, CPU throttling
- `memory-troubleshooting.md`: OOM killer, memory leak detection, swap analysis, RSS vs VSZ, kernel memory
- `disk-troubleshooting.md`: Disk I/O bottlenecks, inode exhaustion, filesystem corruption, LVM operations
- `process-troubleshooting.md`: Strace, lsof, process states, zombie processes, cgroup limits
- `network-troubleshooting.md`: TCP states, ss/ip, packet capture with tcpdump, network latency diagnosis, DNS resolution debugging
- `log-analysis.md`: journald, syslog, log rotation, structured logging analysis patterns
- `systemd-troubleshooting.md`: Service management, unit file debugging, dependency resolution, timer troubleshooting

### 02-networking
- `README.md`: Networking section index
- `dns-troubleshooting.md`: DNS resolution chain diagnosis, dig/nslookup deep dive, DNSSEC, split-horizon DNS, CDN routing issues
- `tls-ssl-troubleshooting.md`: Certificate chain validation, cipher suite debugging, mTLS issues, SNI problems, HSTS
- `load-balancer-troubleshooting.md`: ALB/NLB connection issues, health check debugging, sticky sessions, cross-zone load balancing

### 03-aws
- `README.md`: AWS troubleshooting section index
- `ec2-troubleshooting.md`: Instance status checks, system status checks, EBS volume performance, placement groups, launch failures
- `rds-troubleshooting.md`: Connection pool exhaustion, replication lag, failover debugging, parameter group tuning
- `ecs-eks-troubleshooting.md`: Task placement, service auto-scaling, container instance draining, Fargate limits
- `s3-troubleshooting.md`: Access denied patterns, bucket policy debugging, replication latency, versioning issues
- `iam-troubleshooting.md`: Permission boundaries, trust policy debugging, cross-account access, service-linked roles
- `cloudwatch-troubleshooting.md`: Log group throttling, metric math, alarm configuration, Logs Insights query optimization
- `vpc-troubleshooting.md`: NACL vs Security Group, transit gateway routing, VPC endpoint connectivity, NAT gateway issues

### 04-containers
- `README.md`: Container troubleshooting section index
- `container-debugging.md`: Docker/containerd container inspection, exit codes, Dockerfile best practices, nsenter, health check debugging

### 05-kubernetes
- `README.md`: Kubernetes troubleshooting section index with common gotchas
- `kubectl-cheatsheet.md`: Essential kubectl commands organized by category, multi-container debugging, JSONPath recipes
- `helm-troubleshooting.md`: Helm release failures, template debugging, hook failures, dependency resolution, rollback procedures
- `pods/pod-troubleshooting.md`: CrashLoopBackOff, ImagePullBackOff, OOMKilled, Pending, InitContainer failures
- `controllers/controllers-troubleshooting.md`: Deployments, StatefulSets, DaemonSets, Jobs/CronJobs
- `services/service-troubleshooting.md`: ClusterIP, NodePort, LoadBalancer, Endpoints, CoreDNS
- `ingress/ingress-troubleshooting.md`: Ingress controllers, TLS, routing rules, 503 backends
- `networking/network-policies-troubleshooting.md`: CNI, NetworkPolicies, CoreDNS issues
- `config/configmaps-secrets-troubleshooting.md`: Mount failures, subPath, secret rotation
- `storage/storage-troubleshooting.md`: PV/PVC binding, StorageClasses, CSI drivers
- `scheduling/scheduling-troubleshooting.md`: Taints/Tolerations, Affinity/AntiAffinity, TopologySpread
- `autoscaling/autoscaling-troubleshooting.md`: HPA, VPA, Cluster Autoscaler
- `security/security-troubleshooting.md`: RBAC, ServiceAccounts, PodSecurity, ResourceQuotas
- `probes/probes-troubleshooting.md`: Startup, Readiness, Liveness probes with timing/config patterns
- `operators/operators-crds-troubleshooting.md`: CRD versioning, operator lifecycle, finalizer issues
- `tooling/kustomize-troubleshooting.md`: Overlay merging, patching, ConfigMap generators
- `operations/node-troubleshooting.md`: Node conditions, disk/memory pressure, kubelet issues
- `operations/etcd-backup-restore.md`: Disaster recovery, snapshot/restore procedures
- `operations/api-deprecations.md`: Detecting and migrating deprecated APIs
- `operations/monitoring-logging.md`: Metrics-server, Prometheus health, logging architecture

### 06-databases
- `README.md`: Databases section index
- `postgresql/postgresql-troubleshooting.md`: Query performance (EXPLAIN ANALYZE), locking (deadlock detection), replication issues, vacuum troubleshooting
- `mysql/mysql-troubleshooting.md`: InnoDB locking, replication lag, slow query log analysis, connection pool exhaustion
- `redis/redis-troubleshooting.md`: Memory eviction policies, persistence issues (RDB/AOF), cluster resharding, latency diagnosis

### 07-api-troubleshooting
- `README.md`: API troubleshooting section index
- `rest-troubleshooting.md`: HTTP status code diagnosis, serialization errors, CORS issues, rate limiting, content negotiation
- `graphql-troubleshooting.md`: N+1 query detection, query complexity analysis, subscription connection issues, schema validation
- `grpc-troubleshooting.md`: Protocol buffer versioning, deadline/cancellation propagation, streaming issues, gRPC status code mapping
- `websocket-troubleshooting.md`: Connection lifecycle, reconnection strategies, session management, proxy/load balancer configuration
- `async-api-troubleshooting.md`: Event schema evolution, out-of-order delivery, duplicate processing, dead letter handling

### 08-error-codes
- `README.md`: Error codes reference index
- `http-4xx.md`: Complete HTTP 400-499 error reference with causes, debugging steps, and examples for each code
- `http-5xx.md`: Complete HTTP 500-599 error reference with causes, debugging steps, and examples for each code
- `grpc-status-codes.md`: All gRPC status codes with protobuf mappings, when to use each, and debugging approaches
- `dns-tls-errors.md`: DNS RCODE values, TLS alert codes, common certificate errors with mitigation steps

### 09-observability
- `README.md`: Observability section index
- `metrics-deep-dive.md`: Prometheus Metric types (counter, gauge, histogram, summary), recording rules, relabeling, remote write, cardinality management
- `structured-logging.md`: Structured logging patterns in Python/Java/Node.js, log aggregation architecture, sensitive data redaction, log sampling
- `distributed-tracing.md`: OpenTelemetry instrumentation, span context propagation, sampling strategies (head vs tail), trace-derived metrics
- `dashboard-design.md`: Dashboard design principles, Grafana panel types, alert-worthy vs informational dashboards, "Four Golden Signals" dashboard template

### 10-performance
- `README.md`: Performance section index
- `application-profiling.md`: CPU profiling (pprof, async-profiler), memory profiling (heap dumps), flame graph interpretation, allocation tracking
- `load-testing.md`: k6/JMeter script design, production-like test data, gradual ramp-up profiles, soak testing, spike testing, interpreting results
- `bottleneck-analysis.md`: Systematic approach to finding bottlenecks — utilization vs saturation vs errors, queueing theory applied to production
- `caching-strategies.md`: Cache-Aside, Read-Through, Write-Through, Write-Behind patterns, cache invalidation strategies, Redis/Memcached sizing

### 11-oncall-runbooks
- `README.md`: On-call runbooks section index
- 10 detailed production runbooks covering: disk full, OOM, database connection exhaustion, TLS certificate expiry, Kubernetes node failure, DNS outage, S3 access denied spike, API 5xx spike, Redis memory exhaustion, deployment rollback

### 12-10x-sre-playbooks
- `README.md`: Advanced SRE section index
- `sre-mindset-advanced.md`: Mental models for elite SREs — MTTD minimization, error budget negotiation, toil elimination strategies
- `advanced-debugging.md`: Advanced strace, eBPF/BCC tools, kernel debugging, crash dump analysis, memory forensics with gdb
- `chaos-engineering.md`: Chaos experiment design, Game Day planning, steady state verification, blast radius containment
- `capacity-planning.md`: Demand forecasting, load prediction models, lead time analysis, cost optimization with reserved capacity
- `incident-command.md`: Incident Command System (ICS) for SRE, communication templates, stakeholder management, post-incident review facilitation

### 13-security-incidents
- `README.md`: Security incidents section index
- `auth-breach-response.md`: Credential stuffing detection, immediate response playbook, CloudTrail forensics (S3/Athena queries), IAM credential review and rotation. Python CloudTrail event analyzer. Java Spring Security auth failure listener
- `secrets-leaked.md`: Secret detection (GitHub Advanced Security, truffleHog, gitleaks, git-secrets), rotation priority matrix (critical/high/medium/low), AWS Secrets Manager rotation, Vault revocation procedures. Python GitHub secret scanner. Bash git-secrets history scan wrapper
- `dependency-vulnerability.md`: CVE triage framework (CVSS scoring), scanner tools (pip-audit, OWASP Dependency Check, Snyk, Trivy), patch deployment strategies (canary/blue-green/emergency), SBOM generation. Python CVE impact analyzer

### 14-ci-cd
- `README.md`: CI/CD section index
- `pipeline-failures.md`: Flaky test detection and quarantine process (Python flaky finder), Docker build cache optimization, registry push failure recovery, deployment stuck diagnosis, GitHub Actions and Jenkins specific debugging
- `deployment-strategies.md`: Blue-Green, Canary, Rolling, Feature Flag strategies with Kubernetes YAML examples, traffic splitting schedules, DB migration compatibility patterns, rollback procedures, comparison table with decision matrix

### 15-messaging-queues
- `README.md`: Messaging section index
- `kafka/kafka-troubleshooting.md`: Consumer lag diagnosis, partition leader imbalance (hot-spotting), under-replicated partitions, rebalance storm detection and fix, message size limits, topic retention. Python resilient Kafka consumer with DLQ. Java Kafka consumer with error handling and graceful shutdown
- `sqs/sqs-troubleshooting.md`: Visibility timeout tuning, DLQ redrive operations, long vs short polling cost analysis, FIFO vs Standard queue selection, message attribute routing. Python SQS consumer with DynamoDB idempotency. Java SQS consumer with visibility extension

### 16-scripts-toolkit
- `README.md`: Scripts toolkit index
- `health_checker.py`: Multi-endpoint HTTP health checker with retries, exponential backoff, concurrent execution, JSON output
- `log_analyzer.py`: Nginx/JSON log parser with top IPs, endpoints, status distribution, error rate anomaly detection via standard deviation
- `cert_expiry_checker.py`: SSL/TLS certificate expiry checker with color output, CSV export, concurrent domain checking
- `db_connection_pool_monitor.py`: PostgreSQL connection pool utilization monitor with wait events, long-running queries, idle-in-transaction detection
- `disk_emergency.sh`: Disk space emergency script — find large files, detect deleted-but-open files, inode usage, cleanup recommendations
- `tcp_connection_audit.sh`: TCP connection state auditor with CLOSE_WAIT leak detection, TIME_WAIT analysis, conntrack table monitoring
- `k8s_pod_debug.sh`: Kubernetes pod debugger — comprehensive pod inspection, OOM detection, image pull error diagnosis, ConfigMap/Secret references, interactive shell

### Documentation
- `GLOSSARY.md`: 60+ SRE terms with definitions, context, and examples organized into 10 categories: Core SRE, Incident Management, Reliability Patterns, Performance, Observability, Deployment, Chaos Engineering, Networking, and Kubernetes
- `CHANGELOG.md`: This file — tracking all versions and changes
- `CONTRIBUTING.md`: Contribution guidelines, standard template, pull request process

### Structure
- 17 section directories with README indexes
- Comprehensive troubleshooting guides (individual .md files)
- 7 production-ready scripts (4 Python, 3 Bash)
- Cross-referenced with GitHub Flavored Markdown links between related sections
- Consistent tagging system (`#security`, `#oncall`, `#kafka`, etc.) for discoverability
- Difficulty ratings on every guide (Basic / Intermediate / Advanced)
