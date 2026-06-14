# 05 — Kubernetes Troubleshooting

> **Debugging Kubernetes workloads: pods, controllers, networking, storage, security, and cluster operations.**
> Kubernetes automates everything until it doesn't. This section covers what breaks, how to diagnose it, and how to fix it — with real scenarios.

---

## Quick Navigation

### Core Tools
| # | Document | What You'll Learn | Time to Read |
|---|----------|-------------------|--------------|
| 1 | [kubectl Cheatsheet](kubectl-cheatsheet.md) | Every essential kubectl command organized by purpose | 15 min |
| 2 | [Helm Troubleshooting](helm-troubleshooting.md) | Stuck releases, values precedence, rollback, chart rendering | 10 min |

### Workloads
| # | Document | What You'll Learn |
|---|----------|-------------------|
| 3 | [Pod Troubleshooting](pods/pod-troubleshooting.md) | CrashLoopBackOff, ImagePullBackOff, OOMKilled, Pending, InitContainers |
| 4 | [Controllers Troubleshooting](controllers/controllers-troubleshooting.md) | Deployments, StatefulSets, DaemonSets, Jobs/CronJobs |
| 5 | [Probes Troubleshooting](probes/probes-troubleshooting.md) | Startup, Readiness, Liveness probes — timing, ordering, and failure modes |

### Networking
| # | Document | What You'll Learn |
|---|----------|-------------------|
| 6 | [Service Troubleshooting](services/service-troubleshooting.md) | ClusterIP, NodePort, LoadBalancer, Endpoints, EndpointSlices |
| 7 | [Ingress Troubleshooting](ingress/ingress-troubleshooting.md) | Ingress controllers, TLS, routing rules, 503 backends |
| 8 | [Network Policy Troubleshooting](networking/network-policies-troubleshooting.md) | CNI plugins, NetworkPolicies, CoreDNS, pod-to-pod communication |

### Configuration & Storage
| # | Document | What You'll Learn |
|---|----------|-------------------|
| 9 | [ConfigMaps & Secrets](config/configmaps-secrets-troubleshooting.md) | Mount failures, subPath gotchas, secret rotation, env injection |
| 10 | [Storage Troubleshooting](storage/storage-troubleshooting.md) | PV/PVC binding, StorageClasses, CSI drivers, volume mount failures |

### Scheduling & Scaling
| # | Document | What You'll Learn |
|---|----------|-------------------|
| 11 | [Scheduling Troubleshooting](scheduling/scheduling-troubleshooting.md) | Taints/Tolerations, Affinity/AntiAffinity, NodeSelector, TopologySpread |
| 12 | [Autoscaling Troubleshooting](autoscaling/autoscaling-troubleshooting.md) | HPA, VPA, Cluster Autoscaler — scaling decisions and failures |

### Security & Access
| # | Document | What You'll Learn |
|---|----------|-------------------|
| 13 | [Security Troubleshooting](security/security-troubleshooting.md) | RBAC, ServiceAccounts, PodSecurity, ResourceQuotas, LimitRanges |

### Tooling
| # | Document | What You'll Learn |
|---|----------|-------------------|
| 14 | [Kustomize Troubleshooting](tooling/kustomize-troubleshooting.md) | Overlay merging, patching, ConfigMap generators |
| 15 | [Operators & CRDs](operators/operators-crds-troubleshooting.md) | Operator lifecycle, CRD versioning, finalizer issues |

### Operations
| # | Document | What You'll Learn |
|---|----------|-------------------|
| 16 | [Node Troubleshooting](operations/node-troubleshooting.md) | Node conditions, disk/memory pressure, kubelet issues |
| 17 | [etcd Backup & Restore](operations/etcd-backup-restore.md) | Disaster recovery, snapshot procedures |
| 18 | [API Deprecations](operations/api-deprecations.md) | Detecting and migrating deprecated APIs |
| 19 | [Monitoring & Logging](operations/monitoring-logging.md) | Metrics-server, Prometheus health, logging architecture |

---

## Kubernetes First 30 Seconds

```bash
# What's running?
kubectl get pods -A -o wide

# What's broken?
kubectl get pods -A --field-selector=status.phase!=Running

# What just happened?
kubectl get events -A --sort-by=.lastTimestamp | tail -20

# Which nodes are healthy?
kubectl get nodes
kubectl describe nodes | grep -A5 Conditions

# What's using resources?
kubectl top pods -A --sort-by=cpu
kubectl top nodes
```

---

## Common Kubernetes Gotchas

| Gotcha | Explanation |
|--------|-------------|
| **ImagePullBackOff** | Registry unreachable, image doesn't exist, missing imagePullSecrets, or ECR auth expired |
| **CrashLoopBackOff** | Container exits immediately. Check logs with `--previous`. Exit code tells the story. |
| **OOMKilled (exit 137)** | Container exceeded memory limit. Check if memory leak or limit too low. |
| **Pending forever** | Insufficient resources, PVC not bound, nodeSelector not matching, taints not tolerated |
| **Service has no endpoints** | Selector doesn't match any pod, or readiness probe is failing |
| **ConfigMap not updating** | subPath mounts don't auto-update. Must restart pod. |
| **DNS not resolving** | CoreDNS pods down, custom resolv.conf in pod, or NetworkPolicy blocking UDP 53 |
| **RBAC: can't list pods** | ServiceAccount missing Role/RoleBinding. Check with `kubectl auth can-i`. |
| **Helm release stuck** | `pending-upgrade` or `pending-install` from interrupted operation. Rollback or uninstall. |
| **Node NotReady** | kubelet down, disk pressure, memory pressure, CNI plugin broken |
| **PVC stuck in Pending** | No StorageClass default, no PV matches, or CSI driver not running |
| **HPA not scaling** | metrics-server not running, missing resource requests, or target metric unresolvable |
| **LoadBalancer pending** | Cloud controller manager not working, or service type not supported |
| **certificate expired** | Ingress TLS cert expired. `kubectl get secret` and check cert dates |

---

## References

- [Kubernetes Debugging Documentation](https://kubernetes.io/docs/tasks/debug/)
- [kubectl Command Reference](https://kubernetes.io/docs/reference/generated/kubectl/kubectl-commands)
- [Helm Documentation](https://helm.sh/docs/)
- [Kubernetes Failure Stories](https://github.com/hjacobs/kubernetes-failure-stories)
