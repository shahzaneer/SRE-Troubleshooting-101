# etcd Backup & Restore

> **Category:** Kubernetes | etcd | Disaster Recovery
> **Difficulty:** Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#kubernetes` `#etcd` `#disaster-recovery` `#backup`

---

## Table of Contents

1. [etcd Architecture](#etcd-architecture)
2. [Backup Procedures](#backup-procedures)
3. [Restore Procedures](#restore-procedures)
4. [etcd Health Issues](#etcd-health-issues)
5. [Disaster Scenarios](#disaster-scenarios)

---

## etcd Architecture

```text
etcd is the Kubernetes "source of truth." All cluster state is stored here:
  - Pods, Services, Deployments, etc.
  - ConfigMaps, Secrets
  - RBAC, ServiceAccounts
  - Node registrations
  - Cluster configuration

etcd runs as a 3 or 5 node cluster (for HA).
  - Odd number to maintain quorum: majority = (N/2)+1
  - 3 nodes: need 2 for quorum (can lose 1)
  - 5 nodes: need 3 for quorum (can lose 2)

Losing quorum = etcd goes read-only = cluster is frozen.
```

### Quick Health Check

```bash
# Check etcd pods (if running as static pods on control plane)
kubectl get pods -n kube-system | grep etcd

# Check etcd cluster health (SSH to control plane)
ETCDCTL_API=3 etcdctl \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key \
  endpoint health

# Check member list
ETCDCTL_API=3 etcdctl \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key \
  member list

# Check etcd metrics
kubectl exec -n kube-system etcd-CONTROL_PLANE -- \
  etcdctl endpoint status --cluster -w table
# Shows: endpoint, ID, version, db size, is leader, raft term/index, etc.
```

---

## Backup Procedures

### Manual Backup (kubeadm clusters)

```bash
# On a control plane node:
ETCDCTL_API=3 etcdctl \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key \
  snapshot save /backup/etcd-$(date +%Y%m%d-%H%M%S).db

# Verify the snapshot
ETCDCTL_API=3 etcdctl snapshot status /backup/etcd-20260101-120000.db
# Output:
# Hash: 2f5a8bf..., Revision: 87654321, TotalKey: 15234, TotalSize: 45 MB

# Compress and store
gzip /backup/etcd-*.db
# Upload to S3/GCS/remote storage
```

### Automated Backup (CronJob)

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: etcd-backup
  namespace: kube-system
spec:
  schedule: "0 */6 * * *"    # every 6 hours
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 1
  jobTemplate:
    spec:
      template:
        spec:
          hostNetwork: true
          nodeSelector:
            node-role.kubernetes.io/control-plane: ""
          tolerations:
          - key: node-role.kubernetes.io/control-plane
            operator: Exists
            effect: NoSchedule
          containers:
          - name: backup
            image: bitnami/etcd:3.5
            command:
            - /bin/sh
            - -c
            - |
              ETCDCTL_API=3 etcdctl \
                --endpoints=https://127.0.0.1:2379 \
                --cacert=/etc/kubernetes/pki/etcd/ca.crt \
                --cert=/etc/kubernetes/pki/etcd/server.crt \
                --key=/etc/kubernetes/pki/etcd/server.key \
                snapshot save /backup/etcd-$(date +%Y%m%d-%H%M%S).db && \
              echo "Backup complete"
            volumeMounts:
            - name: etcd-certs
              mountPath: /etc/kubernetes/pki/etcd
              readOnly: true
            - name: backup
              mountPath: /backup
          volumes:
          - name: etcd-certs
            hostPath:
              path: /etc/kubernetes/pki/etcd
          - name: backup
            hostPath:
              path: /var/backups/etcd
          restartPolicy: OnFailure
```

### Managed Kubernetes Backups

```text
EKS:  etcd is NOT accessible (managed by AWS). Use:
      - velero for workload data backup (not etcd)
      - AWS Backup for EKS (specific resources)
      
GKE:  Automatic etcd backups (managed by Google). Restore via:
      - GKE cluster restore (full cluster snapshot)

AKS:  Automatic etcd backups (managed by Azure). Restore via:
      - AKS cluster restore (full cluster snapshot)
```

---

## Restore Procedures

### Restore to Same Cluster (kubeadm)

```bash
# 1. Stop kube-apiserver (or stop kubelet to freeze cluster)
# On ALL control plane nodes:
mv /etc/kubernetes/manifests/kube-apiserver.yaml /tmp/
# Wait for apiserver to stop

# 2. Restore the snapshot on ONE control plane node:
ETCDCTL_API=3 etcdctl \
  --data-dir=/var/lib/etcd-restore \
  snapshot restore /backup/etcd-20260101-120000.db

# 3. Update etcd manifest to use restored data dir
# Edit /etc/kubernetes/manifests/etcd.yaml:
#   Change hostPath from /var/lib/etcd to /var/lib/etcd-restore
#   (Or move the directories)

# 4. Restart etcd (move manifesto back to manifests dir)

# 5. Restart kube-apiserver:
mv /tmp/kube-apiserver.yaml /etc/kubernetes/manifests/

# 6. Wait for cluster to stabilize, then:
#    - Reset other etcd members
#    - Rejoin them to the cluster
```

### Restore to New Cluster (Disaster Recovery)

```bash
# Scenario: Original cluster completely lost. Restore etcd data to new cluster.

# On new control plane node:

# 1. Initialize the cluster using the etcd snapshot
#    (kubeadm can use an existing etcd data dir)
kubeadm init --ignore-preflight-errors=DirAvailable--var-lib-etcd

# But this doesn't restore etcd data. Better approach:

# 1. Restore etcd snapshot to new data dir
ETCDCTL_API=3 etcdctl snapshot restore /backup/etcd-latest.db \
  --data-dir=/var/lib/etcd-restore \
  --name=control-plane-1 \
  --initial-cluster=control-plane-1=https://10.0.0.1:2380 \
  --initial-advertise-peer-urls=https://10.0.0.1:2380 \
  --initial-cluster-token=etcd-cluster-new

# 2. Start etcd with this data dir

# 3. Once etcd is up, initialize kubeadm with existing etcd:
#    Create kubeadm config pointing to existing etcd endpoints
#    kubeadm init --config=kubeadm-config.yaml
```

### Post-Restore Validation

```bash
# Check cluster is stable
kubectl get nodes
# Nodes may show NotReady initially — wait for kubelet to reconnect

# Check all control plane components
kubectl get pods -n kube-system

# Verify critical resources exist
kubectl get namespaces
kubectl get pods -A | wc -l

# Check if secrets are intact
kubectl get secrets -A | wc -l

# Verify RBAC
kubectl auth can-i '*' '*' --all-namespaces

# Check persistent volumes
kubectl get pv
# PVs may need manual intervention (they reference cloud resources)
```

---

## etcd Health Issues

### Common etcd Issues

| Issue | Symptom | Fix |
|-------|---------|-----|
| **etcd database too large** | API server slow, kubectl timeouts | Compact and defrag: `etcdctl compact REVISION && etcdctl defrag` |
| **etcd quorum lost** | API server read-only, "etcdserver: request timed out" | Restore quorum (bring back at least N/2+1 members) |
| **etcd member unhealthy** | `endpoint health` shows unhealthy member | Remove and re-add the member |
| **Disk I/O latency** | etcd logs: "took too long to execute", WAL fsync slow | Use faster disks (SSD). etcd is I/O sensitive. |
| **Network partition** | Split-brain, two etcd clusters each with partial state | The side with quorum (majority) survives. Minority side must re-join. |
| **"mvcc: database space exceeded"** | etcd quota reached (default 2GB) | Compact + defrag, or increase quota |

### Compaction & Defragmentation

```bash
# Check current revision
ETCDCTL_API=3 etcdctl \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key \
  endpoint status --cluster -w table

# Compact: remove old revisions (keep last N)
# Get current revision from above output
CURRENT_REV=87654321
ETCDCTL_API=3 etcdctl compact $CURRENT_REV

# Defrag: reclaim disk space (run on EACH etcd member)
ETCDCTL_API=3 etcdctl defrag --endpoints=https://127.0.0.1:2379
# Repeat for each member endpoint
```

### Etcd Quota Alarm

```text
When etcd reaches its storage quota:
  1. etcd raises an ALARM
  2. etcd becomes READ-ONLY (no writes)
  3. API server can't create/update/delete resources
  4. Cluster is effectively frozen

Diagnosis:
  etcdctl alarm list
  → memberID:12345 alarm:NOSPACE

Fix:
  # 1. Disable alarm (temporary)
  etcdctl alarm disarm
  
  # 2. Compact and defrag (see above)
  
  # 3. Increase quota (if needed):
  # --quota-backend-bytes=8589934592  (8GB, up from 2GB default)
```

---

## Disaster Scenarios

### Scenario 1: "etcd data dir corrupted on one member"

```text
Symptom: etcd-2 can't start. journalctl shows:
         "recovering backend from snapshot error: database snapshot 
          file invalid"

  The etcd data directory on control-plane-2 is corrupted.

Fix:
  # 1. Stop etcd on the corrupted member (or it's already stopped)
  
  # 2. Remove the member from the cluster:
  etcdctl member remove MEMBER_ID
  
  # 3. Clean up corrupted data:
  rm -rf /var/lib/etcd
  
  # 4. Re-add the member:
  etcdctl member add control-plane-2 --peer-urls=https://10.0.0.2:2380
  
  # 5. Start etcd on control-plane-2 with the new cluster config
  # The member will sync from the healthy members
```

### Scenario 2: "All etcd data lost — disaster recovery"

```text
Symptom: All control plane nodes lost. etcd data gone (disk failure on all 3 nodes).
         Only resource: /backup/etcd-20260101-120000.db on S3.

Recovery:
  # 1. Create new control plane nodes
  # 2. Download backup from S3
  aws s3 cp s3://my-backups/etcd-20260101-120000.db.gz /tmp/
  gunzip /tmp/etcd-20260101-120000.db.gz
  
  # 3. Restore etcd from snapshot (single node)
  ETCDCTL_API=3 etcdctl snapshot restore /tmp/etcd-20260101-120000.db \
    --data-dir=/var/lib/etcd \
    --name=control-plane-1 \
    --initial-cluster=control-plane-1=https://10.0.0.1:2380 \
    --initial-advertise-peer-urls=https://10.0.0.1:2380 \
    --initial-cluster-token=etcd-cluster-recovery
  
  # 4. Start etcd with restored data
  # 5. Initialize Kubernetes control plane against existing etcd
  kubeadm init --control-plane-endpoint=... --upload-certs
  
  # 6. Join other control plane nodes
  # 7. Verify cluster state
  kubectl get all -A
  
  # 8. IMPORTANT: Recreate any resources created AFTER the backup
  #    (deployments, configmaps, secrets, etc.)
  
  # 9. Applications will redeploy from their Deployment manifests
  #    (but PVC data from PVs may need separate recovery)
```

### Scenario 3: "etcd quorum lost due to 2 of 3 members down"

```text
Symptom: 2 control plane nodes went down simultaneously.
         API server returns:
         "etcdserver: request timed out, possibly due to connection lost"
         kubectl commands hang or timeout.

  With 2 of 3 members down, quorum is lost (1 < 2 required).

Fix:
  # If the 2 down members can be recovered:
  # 1. Bring back at least 1 of the 2 down members
  # 2. etcd restores quorum automatically
  # 3. Wait for cluster to stabilize (30-60 seconds)
  
  # If the 2 down members CANNOT be recovered (disk failure, etc.):
  # 1. On the surviving member, force a new cluster:
  etcdctl --endpoints=https://127.0.0.1:2379 member list
  # Note the ID of the surviving member
  
  # 2. Restart etcd on the surviving member with:
  # --force-new-cluster flag
  # This makes it the ONLY member of a new 1-node cluster
  
  # 3. After it's running, add new members
  
  # WARNING: --force-new-cluster can cause data inconsistency.
  # Use only as LAST RESORT when members are unrecoverable.
```

---

## Backup Strategy Best Practices

```text
1. Backup FREQUENCY:
   - Production: Every 1-4 hours
   - Staging: Every 12 hours
   - Always take a backup BEFORE cluster upgrades

2. Backup RETENTION:
   - Keep daily backups for 30 days
   - Keep weekly backups for 3 months
   - Keep monthly backups for 1 year

3. Backup VERIFICATION:
   - Periodically restore a backup to a test cluster
   - Verify critical resources exist in restore
   - Document restore time (RTO measurement)

4. Backup STORAGE:
   - Store off-node (S3, GCS, Azure Blob)
   - Store off-region (DR for region failure)
   - Encrypt backups at rest

5. What etcd BACKUP DOESN'T cover:
   - Persistent Volume DATA (use separate CSI snapshots)
   - Cloud resources (LBs, firewalls — store in IaC)
   - DNS records (ExternalDNS state)
   - Container images (registry must be available)
```

---

## References

- [Operating etcd Clusters for Kubernetes](https://kubernetes.io/docs/tasks/administer-cluster/configure-upgrade-etcd/)
- [etcd Disaster Recovery](https://etcd.io/docs/v3.5/op-guide/recovery/)
- [etcd Backup and Restore](https://kubernetes.io/docs/tasks/administer-cluster/configure-upgrade-etcd/#backing-up-an-etcd-cluster)
