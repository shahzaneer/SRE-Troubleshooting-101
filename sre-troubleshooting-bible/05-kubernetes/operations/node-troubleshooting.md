# Node Troubleshooting

> **Category:** Kubernetes | Nodes | Cluster Operations
> **Difficulty:** Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#kubernetes` `#nodes` `#cluster-operations` `#kubelet`

---

## Table of Contents

1. [Node Conditions](#node-conditions)
2. [Node NotReady](#node-notready)
3. [Disk Pressure](#disk-pressure)
4. [Memory Pressure](#memory-pressure)
5. [Kubelet Issues](#kubelet-issues)
6. [Node Drain & Maintenance](#node-drain--maintenance)

---

## Node Conditions

### All Node Conditions

```bash
# Check all node conditions
kubectl get nodes
kubectl describe node NODE | grep -A10 "Conditions:"

# Typical output:
# Conditions:
#   Type             Status  LastHeartbeatTime  Reason                       Message
#   MemoryPressure   False   <timestamp>        KubeletHasSufficientMemory   kubelet has sufficient memory available
#   DiskPressure     False   <timestamp>        KubeletHasNoDiskPressure     kubelet has no disk pressure
#   PIDPressure      False   <timestamp>        KubeletHasSufficientPID      kubelet has sufficient PID available
#   Ready            True    <timestamp>        KubeletReady                 kubelet is posting ready status
```

### Condition Meanings

| Condition | True Means | Action Needed |
|-----------|-----------|---------------|
| **Ready** | Node is healthy and accepting pods | If False: node can't run pods. Investigate kubelet. |
| **DiskPressure** | Node disk is full (85%+ on root or image fs) | Free disk space. Pods may be evicted. |
| **MemoryPressure** | Node memory is low | Reduce memory usage or add node. Pods may be evicted. |
| **PIDPressure** | Node PID count too high | Too many processes. Check for fork bombs, zombie processes. |
| **NetworkUnavailable** | CNI not configured on node | Check CNI plugin, network interface |

---

## Node NotReady

### Why Nodes Go NotReady

```bash
# Quick check
kubectl get nodes
# NAME     STATUS     ROLES    AGE   VERSION
# node-1   Ready      worker   30d   v1.29.0
# node-2   NotReady   worker   30d   v1.29.0   ← RED FLAG

kubectl describe node node-2 | grep -A15 Conditions
```

### Common Causes

| Cause | Diagnosis | Fix |
|-------|-----------|-----|
| **Kubelet stopped** | SSH to node: `systemctl status kubelet` | `systemctl restart kubelet` |
| **Kubelet can't reach API server** | Check node-to-control-plane network | Fix network/firewall rules. Check API server is up. |
| **Node out of disk** | `df -h /` shows 100% | Free disk space (see Disk Pressure) |
| **Node out of memory** | `free -h` shows 0 available | Reduce workloads or add node |
| **CNI uninitialized** | `kubectl describe node \| grep NetworkUnavailable` | Reinstall/reconfigure CNI |
| **Kubelet certificate expired** | Check kubelet logs for TLS errors | Rotate kubelet certs: `kubeadm certs renew` or manual rotation |
| **Node kernel panic / hardware failure** | SSH fails, node unresponsive | Hardware maintenance. Replace node. |

### Scenario: "All worker nodes went NotReady after control plane restart"

```text
Symptom: Restarted one control plane node for maintenance.
         Within 3 minutes, ALL worker nodes showed NotReady.
         Pods stuck in Terminating state.

Diagnosis:
  # Control plane restart was routine
  # But: only 1 of 3 control plane nodes was running
  # 2 control plane nodes were down for maintenance at the same time!

  # etcd quorum lost (needs 2 of 3)
  # API server went read-only → kubelets couldn't update node heartbeats
  # After 40s (node-monitor-grace-period), controller manager marks
  # nodes as NotReady because heartbeats stopped

Fix:
  1. Bring back at least 2 control plane nodes to restore etcd quorum
  2. Kubelets will reconnect and nodes will become Ready again
  3. NEVER take down more than (N/2) control plane nodes simultaneously

Prevention:
  # Check etcd cluster health before maintenance:
  kubectl exec -n kube-system etcd-control-plane-1 -- etcdctl \
    --endpoints=https://127.0.0.1:2379 \
    --cacert=/etc/kubernetes/pki/etcd/ca.crt \
    --cert=/etc/kubernetes/pki/etcd/server.crt \
    --key=/etc/kubernetes/pki/etcd/server.key \
    endpoint health
```

---

## Disk Pressure

### When It Happens

```text
Kubelet checks disk usage every 10s on:
  - Node's root filesystem (/)
  - Image filesystem (/var/lib/docker or /var/lib/containerd)

Thresholds (configurable via kubelet flags):
  --eviction-hard=memory.available<100Mi,nodefs.available<10%,nodefs.inodesFree<5%
  --eviction-soft=nodefs.available<15%
  --eviction-soft-grace-period=nodefs.available=2m

When threshold is reached:
  1. Node condition: DiskPressure = True
  2. Kubelet starts evicting pods (lowest priority first)
  3. Node tainted: node.kubernetes.io/disk-pressure:NoSchedule (if TaintBasedEvictions enabled)
```

### Diagnosis

```bash
# Check node conditions
kubectl describe node NODE | grep -A2 DiskPressure

# Check actual disk usage (SSH to node)
df -h /
df -h /var/lib/containerd
df -h /var/lib/docker

# What's eating disk?
du -sh /var/lib/containerd/*
du -sh /var/lib/docker/*
du -sh /var/log/*

# Check for large container logs
journalctl --disk-usage
du -sh /var/log/pods/*

# Check image disk usage
crictl images | sort -k4 -h
# Or: docker images

# Check orphaned volumes
ls -la /var/lib/kubelet/pods/*/volumes/
```

### Common Causes & Fixes

| Cause | Fix |
|-------|-----|
| **Dangling images** | `crictl rmi --prune` or `docker system prune -a` |
| **Large container logs** | Set log rotation: `--container-log-max-size=10Mi --container-log-max-files=5` in kubelet config |
| **Journal logs** | `journalctl --vacuum-size=500M` |
| **Orphaned volumes** | Delete pods with emptyDir that aren't cleaned up |
| **Root disk too small** | Add disk space or move /var/lib/kubelet to larger partition |
| **Core dumps** | `rm /var/lib/systemd/coredump/*` |

### Kubelet Log Rotation Config

```yaml
# /var/lib/kubelet/config.yaml
containerLogMaxSize: "10Mi"
containerLogMaxFiles: 5
```

### Scenario: "Node disk full — all pods evicted"

```text
Symptom: All pods on node-3 evicted. kubectl describe node shows DiskPressure: True.

Diagnosis:
  SSH to node-3:
  df -h /
  → /dev/xvda1  50G  50G  0G  100% /

  du -sh /* 2>/dev/null | sort -rh | head -5
  → 25G /var

  du -sh /var/* | sort -rh | head -5
  → 20G /var/lib

  du -sh /var/lib/* | sort -rh | head -5
  → 15G /var/lib/containerd

  du -sh /var/lib/containerd/* | sort -rh | head -5
  → 12G /var/lib/containerd/io.containerd.content.v1.content

  The image store has 12GB of images. A CI pipeline had been building
  and pushing images directly on this node (bad practice).

  crictl images | wc -l
  → 200 images accumulated from CI builds!

Fix:
  # Cleanup images
  crictl rmi --prune
  
  # Cleanup exited containers
  crictl rm $(crictl ps -a -q --state exited)
  
  # Cleanup journal
  journalctl --vacuum-size=200M

  # Set image garbage collection threshold:
  # --image-gc-high-threshold=80 (default: start GC at 80% disk usage)
  # --image-gc-low-threshold=50 (default: GC until 50% disk usage)
  
  # AFTER cleanup: uncordon node
  kubectl uncordon node-3
```

---

## Memory Pressure

### Diagnosis

```bash
# Node condition
kubectl describe node NODE | grep -A2 MemoryPressure

# Memory stats (SSH to node)
free -h
cat /proc/meminfo

# Check kubelet memory eviction thresholds
ps aux | grep kubelet | grep eviction-hard

# Top memory consumers on node
ps aux --sort=-%mem | head -20
```

### Memory Eviction

```text
Kubelet can evict pods when memory is low. Eviction order:
  1. Pods exceeding their memory limits (OOM)
  2. BestEffort pods (no requests, no limits)
  3. Burstable pods exceeding their requests (but within limits)
  4. Guaranteed pods (requests == limits)

To prevent eviction:
  - Set resource requests for all pods
  - Set limits above requests (headroom for bursting)
  - Use Guaranteed QoS for critical pods:
    spec.containers[].resources.requests == limits
```

---

## Kubelet Issues

### Kubelet Health Check

```bash
# On the node (SSH):
systemctl status kubelet
journalctl -u kubelet -f

# Check kubelet config
cat /var/lib/kubelet/config.yaml

# Check kubelet flags
ps aux | grep kubelet

# Check kubelet certificates
openssl x509 -in /var/lib/kubelet/pki/kubelet-client-current.pem -text -noout | grep -A2 Validity
```

### Common Kubelet Issues

```text
1. Kubelet won't start
   → journalctl -u kubelet shows error
   → Common: "failed to load kubelet config" → /var/lib/kubelet/config.yaml is corrupted
   → Common: "cannot find cgroup" → cgroup driver mismatch (systemd vs cgroupfs)

2. Kubelet certificate rotation failed
   → Check: ls -la /var/lib/kubelet/pki/
   → If cert expired: manually request new cert or restart kubelet
   → certs should auto-rotate (K8s 1.22+)

3. Kubelet fails to register node
   → "node is not authorized" → kubelet client cert not trusted
   → "node name mismatch" → hostname changed since cert was issued

4. Kubelet cgroup driver mismatch
   → Check: docker info | grep -i cgroup
   → And: ps aux | grep kubelet | grep cgroup-driver
   → Both must use the SAME driver (systemd OR cgroupfs)

5. "PLEG is not healthy" in kubelet logs
   → Pod Lifecycle Event Generator stuck
   → Often caused by: container runtime hanging, disk I/O blocking
   → Fix: restart container runtime (containerd/docker), restart kubelet
```

### Scenario: "Kubelet not starting after reboot — cgroup driver mismatch"

```text
Symptom: Node rebooted. Kubelet fails to start.
         journalctl -u kubelet: "failed to run Kubelet: misconfiguration:
         kubelet cgroup driver: "systemd" is different from docker 
         cgroup driver: "cgroupfs""

Diagnosis:
  # Kubelet is configured with --cgroup-driver=systemd
  # Docker daemon is using cgroupfs (default)
  # Mismatch causes resource accounting errors

Fix:
  # Option A: Change kubelet to use cgroupfs
  # Edit /var/lib/kubelet/config.yaml:
  cgroupDriver: cgroupfs
  systemctl restart kubelet

  # Option B: Change Docker to use systemd (preferred):
  # Edit /etc/docker/daemon.json:
  { "exec-opts": ["native.cgroupdriver=systemd"] }
  systemctl restart docker
  systemctl restart kubelet
```

---

## Node Drain & Maintenance

### Safe Node Drain

```bash
# 1. Cordon the node (no new pods scheduled)
kubectl cordon node-3

# 2. Drain the node (evict all pods gracefully)
kubectl drain node-3 --ignore-daemonsets --delete-emptydir-data

# If pods are stuck (PDB, finalizers, etc.):
kubectl drain node-3 --ignore-daemonsets --delete-emptydir-data --force --grace-period=30

# 3. Perform maintenance

# 4. Uncordon the node
kubectl uncordon node-3
```

### Drain Gotchas

```text
1. DaemonSet pods are NOT evicted by default
   → Use --ignore-daemonsets (they'll be recreated when node reboots)

2. Pods with emptyDir lose data
   → Use --delete-emptydir-data (knowing data loss is OK)
   → Or migrate data before draining

3. Pods without controllers (bare pods) block drain
   → Use --force but these pods are permanently deleted
   → Better: ensure all pods are managed by controllers

4. PodDisruptionBudget blocks drain
   → If PDB would be violated, drain waits indefinitely
   → kubectl describe pdb → check Allowed disruptions

5. Mirrored pods (static pods from /etc/kubernetes/manifests/) block drain
   → Use --force (mirrored pods are recreated by kubelet on same node)
```

### Graceful Node Shutdown (K8s 1.21+)

```yaml
# Kubelet config:
shutdownGracePeriod: 30s
shutdownGracePeriodCriticalPods: 10s
# Critical pods get the shorter grace period
```

---

## References

- [Node Conditions](https://kubernetes.io/docs/concepts/architecture/nodes/#condition)
- [Safely Drain a Node](https://kubernetes.io/docs/tasks/administer-cluster/safely-drain-node/)
- [Node Pressure Eviction](https://kubernetes.io/docs/concepts/scheduling-eviction/node-pressure-eviction/)
- [Kubelet Configuration](https://kubernetes.io/docs/reference/config-api/kubelet-config.v1beta1/)
