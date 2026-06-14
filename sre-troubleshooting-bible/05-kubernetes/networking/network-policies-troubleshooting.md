# Network Policy Troubleshooting

> **Category:** Kubernetes | NetworkPolicies | CNI | CoreDNS
> **Difficulty:** Intermediate to Advanced
> **Last Reviewed:** 2026-06
> **Tags:** `#kubernetes` `#networkpolicy` `#cni` `#coredns`

---

## Table of Contents

1. [Network Policy Architecture](#network-policy-architecture)
2. [Pod-to-Pod Communication Broken](#pod-to-pod-communication-broken)
3. [CoreDNS / DNS Issues](#coredns--dns-issues)
4. [CNI Plugin Failures](#cni-plugin-failures)
5. [Debugging Network Policies](#debugging-network-policies)

---

## Network Policy Architecture

```text
NetworkPolicies control traffic flow at the IP/port level.

Default behavior WITHOUT policies: ALL traffic allowed between pods.
Default behavior WITH policies: ONLY explicitly allowed traffic passes.

Key concepts:
  - podSelector: which pods the policy applies to
  - ingress: allow inbound traffic from...
  - egress: allow outbound traffic to...
  - namespaceSelector: allow from/to pods in specific namespaces
  - ipBlock: allow from/to specific CIDR ranges
  - policyTypes: Ingress, Egress, or both

NetworkPolicies are ENFORCED by the CNI plugin, NOT by kube-proxy.
Supported CNIs: Calico, Cilium, Weave Net, Antrea, Kube-router
NOT supported: Flannel (without additional plugins), kubenet
```

### Quick Diagnosis

```bash
# List all network policies
kubectl get networkpolicies -A

# Check if CNI supports NetworkPolicies
kubectl get pods -n kube-system | grep -E "calico|cilium|weave|antrea|kube-router"

# Check CNI plugin logs
kubectl logs -n kube-system -l k8s-app=calico-node --tail=50
kubectl logs -n kube-system -l k8s-app=cilium --tail=50
```

---

## Pod-to-Pod Communication Broken

### Step-by-Step Diagnosis

```bash
# 1. Check if any NetworkPolicies exist in the namespace
kubectl get networkpolicies -n NAMESPACE

# 2. Check if the pods involved are selected by any policy
kubectl describe networkpolicy -n NAMESPACE

# 3. Test connectivity from source pod
kubectl exec SOURCE_POD -n NAMESPACE -- nc -zv TARGET_IP TARGET_PORT
kubectl exec SOURCE_POD -n NAMESPACE -- curl -v http://TARGET_SVC:TARGET_PORT

# 4. Test from a pod with NO policies (different namespace without policies)
kubectl run test --image=nicolaka/netshoot --rm -it -n default -- nc -zv TARGET_IP TARGET_PORT

# 5. If it works from default ns but not from source ns → NetworkPolicy blocking
```

### Common NetworkPolicy Mistakes

| Issue | Symptom | Fix |
|-------|---------|-----|
| **Default deny all ingress** | All incoming traffic blocked | Add specific ingress rules for required sources |
| **Default deny all egress** | Pods can't reach DNS, DB, or external APIs | Add egress rules for required destinations |
| **port specified as string** | Policy silently doesn't work | `port: 5432` (number), not `port: "5432"` (string) |
| **namespaceSelector missing labels** | Blocking pods from another namespace | The source namespace must have matching labels |
| **ipBlock excludes pod CIDR** | ipBlock blocks pod-to-pod traffic (CIDR includes pod IPs) | Use podSelector or namespaceSelector instead |
| **Multiple policies merged** | Conflicting rules; policies are ADDITIVE | All policies are OR-ed together (union, not intersection) |

### Scenario: "Can't connect to database after applying NetworkPolicy"

```text
Symptom: Applied a NetworkPolicy for PCI compliance. Now app pods
         can't connect to the PostgreSQL database.

Policy YAML:
  apiVersion: networking.k8s.io/v1
  kind: NetworkPolicy
  metadata:
    name: db-access
    namespace: production
  spec:
    podSelector:
      matchLabels:
        app: postgres
    policyTypes:
    - Ingress
    ingress:
    - from:
      - podSelector:
          matchLabels:
            app: backend
      ports:
      - protocol: TCP
        port: 5432

Diagnosis:
  # app=backend pods can reach postgres:5432 — that's covered.
  # BUT: postgres pods cannot SEND responses back to backend pods.
  # NetworkPolicy applied to postgres pods only controls INGRESS
  # (traffic going INTO postgres). Response traffic is connection-
  # tracked and automatically allowed.

  Wait, that should work... Let's check if a DEFAULT DENY policy exists:

  kubectl get networkpolicy -n production
  → deny-all-ingress   ← blocks ALL ingress unless explicitly allowed

  The db-access policy explicitly allows app:backend on 5432. Fine.
  But the deny-all policy might be broader:

  kubectl get networkpolicy deny-all-ingress -n production -o yaml
  → podSelector: {}   ← selects ALL pods in namespace
  → policyTypes: [Ingress, Egress]
  → ingress: []       ← empty = deny all
  → egress: []        ← empty = deny all

  The default deny policy also blocks EGRESS! Postgres pods can't
  send response packets. Even though response traffic is connection
  tracked, TCP SYN-ACK must be allowed.

  Actually, connection tracking DOES handle response packets
  automatically... Unless the CNI plugin has a bug.

  Let's check with netshoot:
  kubectl exec backend-pod -- nc -zv postgres-svc 5432
  → connection timeout (never connects)

  kubectl exec backend-pod -- nc -zv POSTGRES_POD_IP 5432
  → connection refused

  Wait — postgres is listening on 5432 but connection refused?
  Check postgres config: postgresql.conf has listen_addresses='localhost'!
  Postgres is only listening on 127.0.0.1, not 0.0.0.0.

Fix: Update postgresql.conf: listen_addresses = '*'
     or set to the pod's IP: listen_addresses = '0.0.0.0'
```

### Default Deny Policies (Reference)

```yaml
# Default deny all ingress
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: deny-all-ingress
spec:
  podSelector: {}
  policyTypes:
  - Ingress

---
# Default deny all egress
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: deny-all-egress
spec:
  podSelector: {}
  policyTypes:
  - Egress

---
# Allow egress to DNS (required for default-deny-egress)
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-dns-egress
spec:
  podSelector: {}
  policyTypes:
  - Egress
  egress:
  - to:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: kube-system
    - podSelector:
        matchLabels:
          k8s-app: kube-dns
    ports:
    - protocol: UDP
      port: 53
    - protocol: TCP
      port: 53
```

---

## CoreDNS / DNS Issues

### CoreDNS Health Check

```bash
# CoreDNS pod status
kubectl get pods -n kube-system -l k8s-app=kube-dns
# Or newer clusters:
kubectl get pods -n kube-system -l k8s-app=coredns

# CoreDNS metrics
kubectl exec -n kube-system deploy/coredns -- curl localhost:9153/metrics 2>/dev/null | grep coredns_dns

# Test internal DNS resolution
kubectl run test --image=busybox --rm -i -- nslookup kubernetes.default.svc.cluster.local

# Test external DNS resolution
kubectl run test --image=busybox --rm -i -- nslookup google.com

# Check CoreDNS configmap
kubectl get configmap coredns -n kube-system -o yaml
```

### CoreDNS Node Cache (Performance)

```text
CoreDNS with node-local-dns:
  Pod DNS query → node-local-dns (169.254.20.10) → CoreDNS → upstream

Without node-local-dns:
  Pod DNS query → CoreDNS (ClusterIP) → upstream

Check if node-local-dns is enabled:
  kubectl get pods -n kube-system -l k8s-app=node-local-dns
  kubectl exec POD -- cat /etc/resolv.conf
  → nameserver 169.254.20.10  (node-local-dns enabled)
  → nameserver 10.96.0.10     (standard CoreDNS)
```

### Common DNS Issues

```text
1. CoreDNS pods CrashLoopBackOff
   → Check logs: kubectl logs -n kube-system -l k8s-app=kube-dns
   → Common cause: ConfigMap misconfiguration (Corefile syntax error)
   → Fix: kubectl edit configmap coredns -n kube-system

2. "i/o timeout" resolving external names
   → CoreDNS can't reach upstream DNS servers
   → Check: kubectl exec -n kube-system deploy/coredns -- nslookup google.com
   → Fix: Update upstream DNS in CoreDNS configmap (forward . /etc/resolv.conf or 8.8.8.8)

3. ndots:5 causing excessive DNS queries
   → /etc/resolv.conf has options ndots:5
   → Query "elasticsearch.production" tries:
       1. elasticsearch.production.default.svc.cluster.local
       2. elasticsearch.production.svc.cluster.local
       3. elasticsearch.production.cluster.local
       4. elasticsearch.production (5th attempt)
     Then repeats for search domains...
   → Fix: Use FQDN (trailing dot) or lower ndots for services
   → Or set dnsConfig in pod spec:
     dnsConfig:
       options:
       - name: ndots
         value: "2"
```

### Scenario: "Intermittent DNS failures after CoreDNS scale-up"

```text
Symptom: After scaling CoreDNS from 2 to 5 replicas, services
         intermittently fail to resolve (SERVFAIL).

Diagnosis:
  kubectl logs -n kube-system -l k8s-app=kube-dns | grep SERVFAIL
  → [ERROR] plugin/errors: 2 internal.production.svc.cluster.local. A: read udp 10.244.0.5:47123->172.16.0.3:53: i/o timeout

  CoreDNS is trying to forward to upstream DNS at 172.16.0.3.
  That upstream has a connection limit of 5 concurrent queries per IP.
  With 5 CoreDNS pods all sharing the same source IP (SNAT via node),
  the connection limit is exceeded.

Fix:
  1. Increase upstream DNS connection limits
  2. Or configure CoreDNS to use TCP for upstream:
     forward . 172.16.0.3 {
       prefer_udp
       max_concurrent 1000
     }
  3. Or scale CoreDNS back down to avoid hitting the limit
```

---

## CNI Plugin Failures

### Calico

```bash
# Check Calico node status
kubectl get pods -n kube-system -l k8s-app=calico-node -o wide

# Check Calico BGP/felix status
kubectl exec -n kube-system calico-node-xxx -- calico-node -felix-live
kubectl exec -n kube-system calico-node-xxx -- calico-node -bird-live

# Check for IP pool exhaustion
kubectl get ippools -o yaml | grep -A5 "available"
kubectl get ippool
```

### Cilium

```bash
# Cilium status
kubectl exec -n kube-system cilium-xxx -- cilium status
kubectl exec -n kube-system cilium-xxx -- cilium endpoint list

# Cilium connectivity test
kubectl exec -n kube-system cilium-xxx -- cilium connectivity test
```

### CNI Not Installed / Broken

```text
Symptom: New pods stuck in ContainerCreating.
         kubectl describe pod shows:
         "network: failed to set up sandbox container: network plugin
          is not ready: cni config uninitialized"

Cause: CNI plugin not installed, or CNI binary missing on node,
       or kubelet can't find CNI config.

Fix:
  # Check if CNI plugins are installed on node
  ls /opt/cni/bin/            # should contain bridge, loopback, etc.
  ls /etc/cni/net.d/          # should contain CNI config (e.g., 10-calico.conflist)

  # If missing, reinstall CNI:
  kubectl apply -f https://raw.githubusercontent.com/projectcalico/calico/v3.27/manifests/calico.yaml
```

---

## Debugging Network Policies

### Tools

```bash
# Test from a netshoot pod (has all networking tools)
kubectl run netshoot --image=nicolaka/netshoot --rm -it -- bash
# Inside: nc, curl, dig, tcpdump, iperf, etc.

# Test with specific labels (to test if label-based policy works)
kubectl run netshoot --image=nicolaka/netshoot --rm -it \
  --overrides='{"metadata":{"labels":{"app":"backend","env":"staging"}}}' -- bash

# Test as a specific service account
kubectl run test --image=nicolaka/netshoot --rm -it \
  --overrides='{"spec":{"serviceAccountName":"my-sa"}}' -- bash
```

### Checking Effective Rules

```bash
# Calico: Show applied policy for a specific pod
kubectl exec -n kube-system calico-node-xxx -- calicoctl get profile -o yaml

# Cilium: Show applied policy
kubectl exec -n kube-system cilium-xxx -- cilium policy get

# General: Dry-run approach — create a matching pod and test
kubectl run test-src --image=busybox -l app=backend -- sleep 300
kubectl exec test-src -- nc -zv TARGET_IP TARGET_PORT
kubectl delete pod test-src
```

### Scenario: "NetworkPolicy works on paper but traffic still blocked"

```text
Symptom: Created an egress allow rule for a specific IP, but
         connections are still blocked.

  egress:
  - to:
    - ipBlock:
        cidr: 10.0.0.0/8
        except:
        - 10.0.0.0/16
    ports:
    - protocol: TCP
      port: 443

  This allows egress to 10.0.0.0/8 EXCEPT 10.0.0.0/16 on TCP 443.

  But wait — the target is at 10.0.50.10:8443 (port 8443, not 443).
  The policy only allows port 443.

Fix: Add port 8443 to the allowed ports, or use:
     ports:
     - protocol: TCP
       port: 8443
```

---

## References

- [Network Policies](https://kubernetes.io/docs/concepts/services-networking/network-policies/)
- [Calico Troubleshooting](https://docs.tigera.io/calico/latest/operations/troubleshoot/)
- [Cilium Troubleshooting](https://docs.cilium.io/en/stable/operations/troubleshooting/)
- [CoreDNS Troubleshooting](https://coredns.io/manual/toc/#troubleshooting)
- [Debug DNS Resolution](https://kubernetes.io/docs/tasks/administer-cluster/dns-debugging-resolution/)
