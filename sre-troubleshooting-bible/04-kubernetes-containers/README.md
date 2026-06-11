# 04 — Kubernetes & Containers

> **Debugging containerized workloads: pods, deployments, Helm releases, and container internals.**
> Kubernetes abstracts the infrastructure, but the abstractions fail in reproducible ways.

---

## Quick Navigation

| # | Document | What You'll Learn | Time to Read |
|---|----------|-------------------|--------------|
| 1 | [kubectl Cheatsheet](kubectl-cheatsheet.md) | Every essential kubectl command organized by purpose | 15 min |
| 2 | [Container Debugging](container-debugging.md) | Docker commands, exit codes, Dockerfile best practices, nsenter | 15 min |
| 3 | [Helm Troubleshooting](helm-troubleshooting.md) | Stuck releases, values precedence, rollback, chart rendering | 10 min |

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

# What's using resources?
kubectl top pods -A
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
| **DNS not resolving** | CoreDNS pods down, or custom resolv.conf in pod, or NetworkPolicy blocking UDP 53 |
| **RBAC: can't list pods** | ServiceAccount missing Role/RoleBinding. Check with `kubectl auth can-i`. |
| **Helm release stuck** | `pending-upgrade` or `pending-install` from interrupted operation. Rollback or uninstall. |

---

## References

- [Kubernetes Debugging Documentation](https://kubernetes.io/docs/tasks/debug/)
- [kubectl Command Reference](https://kubernetes.io/docs/reference/generated/kubectl/kubectl-commands)
- [Helm Documentation](https://helm.sh/docs/)
- [Docker Documentation](https://docs.docker.com/)
