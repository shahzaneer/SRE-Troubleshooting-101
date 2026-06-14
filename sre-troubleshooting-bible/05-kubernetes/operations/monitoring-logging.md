# Monitoring & Logging

> **Category:** Kubernetes | Monitoring | Logging | Observability
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#kubernetes` `#monitoring` `#logging` `#metrics-server` `#prometheus`

---

## Table of Contents

1. [Metrics-Server Troubleshooting](#metrics-server-troubleshooting)
2. [Prometheus Health](#prometheus-health)
3. [Logging Architecture](#logging-architecture)
4. [Common Monitoring Gaps](#common-monitoring-gaps)

---

## Metrics-Server Troubleshooting

metrics-server is the minimal metrics pipeline for Kubernetes (needed for HPA, `kubectl top`).

### Diagnosis

```bash
# Check if metrics-server is running
kubectl get pods -n kube-system -l k8s-app=metrics-server

# Check metrics-server API availability
kubectl get --raw /apis/metrics.k8s.io/v1beta1

# Check if metrics are being collected
kubectl top nodes
kubectl top pods -A

# Check metrics-server logs
kubectl logs -n kube-system deployment/metrics-server --tail=50

# Check metrics-server service
kubectl get svc metrics-server -n kube-system
```

### Common Metrics-Server Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| **"Error from server (ServiceUnavailable)"** | metrics-server not registered | Check apiService: `kubectl get apiservice v1beta1.metrics.k8s.io` |
| **"metrics not available"** | metrics-server can't scrape kubelets | Check metrics-server can reach node IPs on port 10250 |
| **"no metrics known for pod"** | Pod doesn't have resource requests or is too new | Wait 30-60s for first scrape |
| **HPA shows unknown** | metrics-server APIService not Available | Check: `kubectl get apiservice v1beta1.metrics.k8s.io -o yaml` |
| **metrics-server CrashLoopBackOff** | TLS cert issues with kubelet scraping | Add `--kubelet-insecure-tls` flag (dev) or provide proper CA |

### Scenario: "metrics-server can't scrape kubelets — TLS issue"

```text
Symptom: metrics-server pods running but `kubectl top nodes` returns no data.
         metrics-server logs: "unable to fully scrape metrics: 
         unable to fully collect metrics: ... x509: cannot validate 
         certificate for 10.0.1.5"

  metrics-server connects to kubelets on port 10250 (HTTPS).
  Kubelets use self-signed certificates by default.
  metrics-server needs to either:
    a) Trust the kubelet CA
    b) Skip TLS verification (--kubelet-insecure-tls)

Fix:
  # In metrics-server deployment args, add:
  --kubelet-insecure-tls
  # OR provide the kubelet CA:
  --kubelet-certificate-authority=/etc/kubernetes/pki/ca.crt
```

---

## Prometheus Health

### Key Components

```bash
# Prometheus Server
kubectl get pods -n monitoring -l app.kubernetes.io/name=prometheus
kubectl logs -n monitoring statefulset/prometheus --tail=20

# Alertmanager
kubectl get pods -n monitoring -l app.kubernetes.io/name=alertmanager

# Grafana
kubectl get pods -n monitoring -l app.kubernetes.io/name=grafana

# kube-state-metrics (generates K8s object metrics)
kubectl get pods -n monitoring -l app.kubernetes.io/name=kube-state-metrics

# node-exporter (per-node system metrics)
kubectl get pods -n monitoring -l app.kubernetes.io/name=node-exporter
```

### Prometheus Server Issues

| Issue | Diagnosis | Fix |
|-------|-----------|-----|
| **Prometheus not scraping targets** | Check Targets page: `http://prometheus:9090/targets` | Fix network, service monitors, or annotation selectors |
| **Disk full** | Check TSDB size: `du -sh /prometheus/` | Reduce retention (`--storage.tsdb.retention.time`) or increase PVC |
| **OOMKilled** | Prometheus uses too much memory | Increase memory limit, reduce cardinality (drop unused metrics), or shard |
| **"out of bounds" queries** | Retention period shorter than query range | Increase retention period |
| **Alertmanager not sending alerts** | Check Alertmanager UI for inhibited/silenced alerts | Check inhibition rules, silence list |
| **Grafana dashboards show "No data"** | Prometheus datasource misconfigured or down | Check Grafana datasource configuration |

### Prometheus TSDB Issues

```bash
# Check TSDB health
kubectl exec -n monitoring prometheus-0 -- promtool tsdb analyze /prometheus/

# Check for WAL corruption
kubectl exec -n monitoring prometheus-0 -- ls -la /prometheus/wal/

# If WAL is corrupted:
# 1. Stop Prometheus
# 2. Delete the WAL directory (data loss: ~2h of recent data)
# 3. Restart Prometheus
kubectl scale statefulset prometheus -n monitoring --replicas=0
kubectl exec -n monitoring prometheus-0 -- rm -rf /prometheus/wal/
kubectl scale statefulset prometheus -n monitoring --replicas=1
```

### Key Prometheus Metrics for Troubleshooting

```promql
# Is Prometheus up and scraping itself?
up{job="prometheus"}

# Scrape duration (high → targets are slow)
rate(prometheus_target_interval_length_seconds_sum[5m]) / rate(prometheus_target_interval_length_seconds_count[5m])

# TSDB compaction duration (high → disk issues)
rate(prometheus_tsdb_compaction_duration_seconds_sum[1h]) / rate(prometheus_tsdb_compaction_duration_seconds_count[1h])

# Number of active time series (high → cardinality issues)
prometheus_tsdb_head_series

# Rule evaluation duration (high → too many rules or slow queries)
rate(prometheus_rule_evaluation_duration_seconds_sum[5m])
```

---

## Logging Architecture

### Components

```text
Kubernetes logging stack:
  1. Container logs → stdout/stderr → /var/log/containers/ on node
  2. Kubelet rotates logs (max size + max files)
  3. Log agent collects (Fluentd, Fluent Bit, Vector, Filebeat)
  4. Log aggregator stores (Elasticsearch, Loki, Splunk, CloudWatch)
  5. Log query UI (Kibana, Grafana, Splunk UI)

Common issues at each layer:
  - Container logs not reaching stdout (app logs to file instead)
  - Kubelet log rotation deleting logs before agent collects them
  - Log agent consumes too many resources (CPU throttling)
  - Log aggregator disk full / shard failures
  - Query latency due to large index / poor retention policy
```

### Quick Log Access

```bash
# View pod logs
kubectl logs POD -n NAMESPACE --tail=100
kubectl logs POD -n NAMESPACE --since=5m
kubectl logs POD -n NAMESPACE --previous   # crashed container
kubectl logs POD -n NAMESPACE -c CONTAINER --timestamps

# View logs across multiple pods (stern or kubectl plugin)
kubectl logs -l app=myapp --all-containers=true --tail=50
# Or install stern:
stern -n NAMESPACE myapp --tail=50

# Check log rotation config on kubelet
cat /var/lib/kubelet/config.yaml | grep containerLog
# containerLogMaxSize: "10Mi"
# containerLogMaxFiles: 5

# Check node log disk usage
du -sh /var/log/containers/
du -sh /var/log/pods/
```

### Scenario: "Missing logs during incident — log rotation issue"

```text
Symptom: Investigation of an incident that happened 2 hours ago.
         kubectl logs --since=2h shows logs starting from only 15 min ago.
         Earlier logs are gone.

Diagnosis:
  # The application was writing high-volume debug logs during the incident.
  # Log files reached containerLogMaxSize (10Mi) quickly.
  # With containerLogMaxFiles: 5 and each file 10Mi:
  #   Max logs retained per container: 50Mi
  
  # During the incident, the app wrote 50Mi of logs in 15 minutes.
  # Log rotation deleted logs older than 15 minutes.
  # The incident evidence was lost.

Fix:
  # 1. Increase log retention:
  containerLogMaxSize: "50Mi"    # bigger individual files
  containerLogMaxFiles: 10       # more files to rotate

  # 2. DEPLOY A LOG AGGREGATOR (Fluentd, Loki, CloudWatch, etc.)
  #    that ships logs off-node BEFORE rotation deletes them.
  #    Kubelet rotation should be LAST RESORT, not primary retention.

  # 3. For debugging high-volume logs, use sampling:
  #    - Log agent sampling (e.g., Fluentd sample filter)
  #    - Or app-side log level control (reduce to WARN during normal ops)
```

---

## Common Monitoring Gaps

### What's Often Missing

```text
1. Control plane metrics
   → API server latency/errors (apiserver_request_duration_seconds)
   → etcd disk sync duration, leader changes
   → Scheduler pending pods, scheduling latency
   → Controller manager work queue depth

2. Kubelet metrics
   → Pod startup latency
   → Container runtime operations latency
   → Volume mount/attach operations

3. kube-proxy metrics
   → iptables/ipvs rules sync duration
   → Service endpoint changes

4. CNI metrics
   → Network policy evaluation time
   → Packet drops

5. Application metrics (USE/RED)
   → USE: Utilization, Saturation, Errors (for every resource)
   → RED: Rate, Errors, Duration (for every service)

6. Synthetic probes / blackbox monitoring
   → Can external users reach the service?
   → TLS certificate expiry
   → DNS resolution

7. Node-level monitoring
   → Disk I/O latency (not just usage %)
   → Network errors/drops
   → OOM events
```

### Critical Alerts to Configure

```yaml
# Alert rules everyone should have:
groups:
- name: kubernetes-critical
  rules:
  - alert: KubeAPIDown
    expr: up{job="apiserver"} == 0
    for: 5m
    labels:
      severity: critical
    annotations:
      summary: "API server is down"

  - alert: NodeNotReady
    expr: kube_node_status_condition{condition="Ready",status="true"} == 0
    for: 5m
    labels:
      severity: critical

  - alert: KubeletDown
    expr: up{job="kubelet"} == 0
    for: 10m
    labels:
      severity: warning

  - alert: KubeCPUThrottling
    expr: rate(container_cpu_cfs_throttled_seconds_total[5m]) > 0.1
    for: 15m
    labels:
      severity: warning
    annotations:
      summary: "Pod {{ $labels.pod }} is being CPU throttled"

  - alert: PodCrashLooping
    expr: rate(kube_pod_container_status_restarts_total[15m]) > 0
    for: 5m
    labels:
      severity: warning

  - alert: PersistentVolumeFilling
    expr: kubelet_volume_stats_available_bytes / kubelet_volume_stats_capacity_bytes < 0.1
    for: 5m
    labels:
      severity: critical

  - alert: TLSCertificateExpiring
    expr: probe_ssl_earliest_cert_expiry - time() < 86400 * 14
    for: 1h
    labels:
      severity: warning
    annotations:
      summary: "TLS cert for {{ $labels.instance }} expires in less than 14 days"
```

---

## References

- [Metrics Server](https://github.com/kubernetes-sigs/metrics-server)
- [Prometheus Operator](https://github.com/prometheus-operator/prometheus-operator)
- [Kubernetes Monitoring Architecture](https://kubernetes.io/docs/tasks/debug/debug-cluster/resource-usage-monitoring/)
- [Logging Architecture](https://kubernetes.io/docs/concepts/cluster-administration/logging/)
