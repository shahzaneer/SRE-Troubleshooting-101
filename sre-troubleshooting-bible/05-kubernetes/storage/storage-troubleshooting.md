# Storage Troubleshooting

> **Category:** Kubernetes | PV | PVC | StorageClasses | CSI
> **Difficulty:** Intermediate to Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#kubernetes` `#storage` `#pv` `#pvc` `#csi`

---

## Table of Contents

1. [PV & PVC Lifecycle](#pv--pvc-lifecycle)
2. [PVC Stuck in Pending](#pvc-stuck-in-pending)
3. [Volume Mount Failures](#volume-mount-failures)
4. [CSI Driver Issues](#csi-driver-issues)
5. [StorageClass Troubleshooting](#storageclass-troubleshooting)

---

## PV & PVC Lifecycle

```text
PVC lifecycle: Pending → Bound → Released (after pod deletion)
PV lifecycle:  Available → Bound → Released → (reclaimed)

Three reclaim policies:
  Retain:  PV is NOT deleted. Manual cleanup required.
  Delete:  PV AND associated storage asset deleted (EBS volume, etc.).
  Recycle: DEPRECATED. Runs `rm -rf /` on volume (basic scrub).

If a PVC is deleted while a pod is using it, the PVC remains in
Terminating state until the pod stops using the volume.
```

### Quick Diagnosis

```bash
# Check all PVs and PVCs
kubectl get pv,pvc -A

# Detailed info
kubectl describe pv PV_NAME
kubectl describe pvc PVC_NAME -n NAMESPACE

# Check which pods are using which PVCs
kubectl get pods -A -o json | jq -r '.items[] | select(.spec.volumes[]?.persistentVolumeClaim != null) | "\(.metadata.namespace)/\(.metadata.name): \(.spec.volumes[].persistentVolumeClaim.claimName)"'

# Check storage classes
kubectl get storageclass
kubectl describe storageclass SC_NAME
```

---

## PVC Stuck in Pending

### Why PVCs Stay Pending

```bash
kubectl describe pvc PVC_NAME -n NAMESPACE | tail -20
# The Events section tells you exactly why

# Common messages:
# "No PersistentVolume is available for this claim"
#   → No existing PV matches the PVC requirements

# "no volume plugin matched"
#   → StorageClass doesn't have a provisioner

# "waiting for a volume to be created by CSI driver"
#   → CSI driver is creating the volume but taking too long

# "external provisioner is not running"
#   → The CSI external-provisioner pod is down
```

### Common Causes & Fixes

| Cause | Diagnosis | Fix |
|-------|-----------|-----|
| **No default StorageClass** | `kubectl get sc` shows no (default) annotation | Set one as default: `kubectl patch sc SC_NAME -p '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}'` |
| **No PV matches PVC requirements** | PVC requests 100Gi, no PV has 100Gi+ | Create a PV or use dynamic provisioning with StorageClass |
| **Access mode mismatch** | PV is RWO, PVC requests RWX | Change PVC accessMode or create matching PV |
| **CSI driver not running** | `kubectl get pods -n kube-system \| grep csi` shows no pods | Install/restart the CSI driver |
| **Cloud storage quota exceeded** | EBS volume limit or GCP PD quota | Increase cloud quota or delete unused volumes |
| **Availability zone mismatch** | PV created in us-east-1a, pod scheduled in us-east-1b | Use `volumeBindingMode: WaitForFirstConsumer` (zone-aware) |
| **Selector doesn't match** | PVC has matchLabels but no PV has those labels | Remove selectors from PVC or label a PV |

### Scenario: "PVC pending because StorageClass has no default"

```text
Symptom: Created a PVC with no storageClassName specified.
         The PVC stays Pending forever.

Diagnosis:
  kubectl describe pvc myapp-data -n production
  → Events: <none>  (no events at all)

  kubectl get storageclass
  → NAME                 PROVISIONER            RECLAIMPOLICY   VOLUMEBINDINGMODE
  → gp2 (default)        kubernetes.io/aws-ebs  Delete          Immediate
  → gp3                  ebs.csi.aws.com        Delete          Immediate

  Wait — gp2 IS marked as default. But the provisioner is
  "kubernetes.io/aws-ebs" which is the IN-TREE provisioner
  (deprecated in K8s 1.27+, removed in 1.29).

  The cluster was upgraded to 1.29 and the in-tree provisioner was
  removed. The gp2 StorageClass still references the old provisioner.

Fix:
  # Delete the old StorageClass
  kubectl delete storageclass gp2

  # Mark gp3 (with CSI driver provisioner) as default
  kubectl patch storageclass gp3 -p '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}'

  # Or migrate gp2 to the CSI driver:
  kubectl patch storageclass gp2 -p '{"provisioner":"ebs.csi.aws.com"}'
```

### VolumeBindingMode

```text
Immediate:       PV is created as soon as PVC is created
WaitForFirstConsumer: PV is created only when a pod using the PVC
                      is scheduled (zone-aware, respects topology)

Scenario: "Pod stuck because PV is in wrong zone"

StorageClass with volumeBindingMode: Immediate
  → PVC is created, EBS volume provisioned in us-east-1a
  → Pod has nodeAffinity to us-east-1b
  → Pod can't schedule: "volume is in zone us-east-1a, pod in zone us-east-1b"

Fix: Use volumeBindingMode: WaitForFirstConsumer
  → PV created only after pod is scheduled
  → PV guaranteed to be in same zone as pod

kubectl patch storageclass gp3 -p '{"volumeBindingMode":"WaitForFirstConsumer"}'
```

---

## Volume Mount Failures

### Pod Stuck in ContainerCreating

```bash
kubectl describe pod POD -n NAMESPACE | grep -A10 Events
# "Unable to attach or mount volumes: unmounted volumes=[data], timed out waiting for the condition"

# Common messages:
# "MountVolume.SetUp failed for volume "pvc-xxx": mount failed"
# "MountVolume.MountDevice failed for volume "pvc-xxx": ..."
```

| Error | Cause | Fix |
|-------|-------|-----|
| **mount failed: exit status 32** | Filesystem corrupted or wrong fsType | Check PV's fsType (ext4 vs xfs). Fix in PV spec. |
| **mount failed: device is already mounted** | Volume already mounted elsewhere (ReadWriteOnce conflict) | RWO volumes can only be mounted by 1 pod at a time. Check if another pod is using it. |
| **timeout waiting for volume** | CSI driver can't attach volume (e.g., node not in AZ) | Check CSI driver logs, verify node is in same AZ as volume |
| **Permission denied** | fsGroup or runAsUser not matching volume permissions | Set spec.securityContext.fsGroup in pod |
| **too many volumes attached** | Node has reached attach limit (AWS: 25-40 volumes, GCP: 128) | Move pods to other nodes or reduce volume count |
| **device already in use** | Multipath or stale device mapping | SSH to node: `dmsetup ls`, clear stale devices |

### Scenario: "Pod can't mount EBS volume after node restart"

```text
Symptom: Pod was running fine on node-1. Node-1 was rebooted for
         maintenance. After reboot, pod restarts on node-2 but gets
         stuck in ContainerCreating.

Events:
  MountVolume.SetUp failed for volume "pvc-abc123" :
  mount failed: exit status 32

Diagnosis:
  kubectl describe pv pvc-abc123
  → NodeAffinity:
      required:
        nodeSelectorTerms:
        - matchExpressions:
          - key: topology.kubernetes.io/zone
            operator: In
            values: [us-east-1a]

  The EBS volume was created in us-east-1a. node-1 was also in
  us-east-1a (so it worked). node-2 is in us-east-1b — EBS volumes
  can't be attached cross-AZ.

Fix:
  1. Add a node in us-east-1a, OR
  2. Use volumeBindingMode: WaitForFirstConsumer (for future PVCs), OR
  3. For this incident: cordon node-2, pod will reschedule to node-1
     (or another node in us-east-1a):
     kubectl cordon node-2
     kubectl delete pod POD  # pod reschedules to us-east-1a node
```

### fsGroup for Permission Fixes

```yaml
# If app runs as non-root (UID 1000) but volume is owned by root:
spec:
  securityContext:
    fsGroup: 1000
    # fsGroupChangePolicy: "OnRootMismatch"  # only chown if needed
  containers:
  - name: app
    securityContext:
      runAsUser: 1000
      runAsGroup: 3000
```

---

## CSI Driver Issues

### CSI Driver Health

```bash
# Check CSI driver pods (names vary by driver)
kubectl get pods -n kube-system | grep csi

# Common CSI components:
# - controller plugin (Deployment): creates/deletes/attaches volumes
# - node plugin (DaemonSet): mounts volumes on nodes
# - external-provisioner: creates PVs
# - external-attacher: attaches volumes to nodes
# - external-resizer: resizes volumes
# - external-snapshotter: creates snapshots

# Check CSI driver logs
kubectl logs -n kube-system deploy/ebs-csi-controller -c ebs-plugin --tail=50
kubectl logs -n kube-system daemonset/ebs-csi-node -c ebs-plugin --tail=50

# Check CSI driver socket
kubectl exec -n kube-system POD -- ls /var/lib/kubelet/plugins/
```

### Common CSI Issues

```text
1. CSI driver pods CrashLoopBackOff
   → Check logs for missing RBAC permissions
   → Driver may need specific IAM role (AWS) or workload identity (GCP)
   → Check CSI driver version is compatible with K8s version

2. "Failed to create volume: quota exceeded"
   → Cloud provider volume quota reached
   → Increase limit or clean up unused volumes

3. "ControllerExpandVolume failed: volume in use"
   → Can't resize volume while pod is using it
   → Delete the pod (temporarily) or use online resizing (if driver supports it)

4. Snapshot not working
   → VolumeSnapshotClass must match the driver
   → Check: kubectl get volumesnapshotclass
```

### Scenario: "EBS CSI driver not creating volumes after EKS upgrade"

```text
Symptom: After upgrading EKS cluster from 1.27 to 1.29, new PVCs
         show "waiting for a volume to be created by CSI driver".

Diagnosis:
  kubectl get pods -n kube-system | grep ebs-csi
  → ebs-csi-controller-xxx   Running (but who knows...)

  kubectl logs deployment/ebs-csi-controller -n kube-system -c ebs-plugin
  → W0325 permission denied: service account "ebs-csi-controller-sa" 
  → cannot assume role "arn:aws:iam::123456789:role/ebs-csi-driver"

  The IAM role used by the CSI driver (via IRSA) lost permissions
  during the upgrade. The trust policy or role permissions were modified.

Fix:
  # Verify IRSA setup
  kubectl get sa ebs-csi-controller-sa -n kube-system -o yaml | grep eks.amazonaws.com/role-arn
  
  # Update the IAM role's trust policy to include the new OIDC provider
  # (EKS may have updated the OIDC endpoint)
  aws iam update-assume-role-policy --role-name ebs-csi-driver \
    --policy-document '{"Version":"2012-10-17","Statement":[...]}'

  # Restart CSI controller
  kubectl rollout restart deployment ebs-csi-controller -n kube-system
```

---

## StorageClass Troubleshooting

### Key Parameters

```yaml
kind: StorageClass
apiVersion: storage.k8s.io/v1
metadata:
  name: fast-ssd
  annotations:
    storageclass.kubernetes.io/is-default-class: "true"
provisioner: ebs.csi.aws.com
parameters:
  type: gp3
  iopsPerGB: "10"
  encrypted: "true"
  kmsKeyId: "arn:aws:kms:..."
volumeBindingMode: WaitForFirstConsumer
allowVolumeExpansion: true
reclaimPolicy: Delete
```

```bash
# Check if StorageClass allows expansion
kubectl get sc fast-ssd -o jsonpath='{.allowVolumeExpansion}'

# Check reclaim policy
kubectl get sc fast-ssd -o jsonpath='{.reclaimPolicy}'
# Delete: PV deleted when PVC deleted → DATA LOST
# Retain: PV preserved when PVC deleted → MANUAL cleanup needed

# Change default StorageClass
kubectl patch storageclass fast-ssd -p '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}'
kubectl patch storageclass old-default -p '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":null}}}'
```

### Scenario: "Data loss after PVC deletion — reclaimPolicy: Delete"

```text
Symptom: A developer accidentally ran `kubectl delete pvc myapp-data`.
         The PV and the underlying EBS volume were immediately deleted.
         All data lost.

Root cause: StorageClass had reclaimPolicy: Delete.
           When PVC is deleted, the PV is deleted and the cloud
           provider deletes the storage asset.

Prevention:
  # For production data, set reclaimPolicy: Retain on StorageClass
  kubectl patch storageclass fast-ssd -p '{"reclaimPolicy":"Retain"}'
  
  # Or create a separate SC for critical data:
  kubectl create -f - <<EOF
  apiVersion: storage.k8s.io/v1
  kind: StorageClass
  metadata:
    name: fast-ssd-retain
  provisioner: ebs.csi.aws.com
  reclaimPolicy: Retain
  parameters:
    type: gp3
  volumeBindingMode: WaitForFirstConsumer
  allowVolumeExpansion: true
  EOF

Recovery (if PV is retained):
  # PV shows "Released" status
  # The PV still references the EBS volume via volumeHandle
  # Recreate PVC to rebind:
  kubectl patch pv PV_NAME -p '{"spec":{"claimRef":{"namespace":"production","name":"myapp-data","uid":null}}}'
  # Then create PVC with same name — it will bind to this PV
```

---

## References

- [Persistent Volumes](https://kubernetes.io/docs/concepts/storage/persistent-volumes/)
- [Storage Classes](https://kubernetes.io/docs/concepts/storage/storage-classes/)
- [CSI Drivers](https://kubernetes-csi.github.io/docs/)
- [AWS EBS CSI Driver](https://github.com/kubernetes-sigs/aws-ebs-csi-driver)
