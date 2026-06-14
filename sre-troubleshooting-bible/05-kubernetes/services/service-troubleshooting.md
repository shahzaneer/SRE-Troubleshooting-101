# Service Troubleshooting

> **Category:** Kubernetes | Services | Networking
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#kubernetes` `#services` `#networking` `#endpoints`

---

## Table of Contents

1. [Service Types & Architecture](#service-types--architecture)
2. [Service Has No Endpoints](#service-has-no-endpoints)
3. [LoadBalancer Stuck in Pending](#loadbalancer-stuck-in-pending)
4. [NodePort Connection Refused](#nodeport-connection-refused)
5. [DNS Resolution Issues](#dns-resolution-issues)
6. [EndpointSlice Troubleshooting](#endpointslice-troubleshooting)

---

## Service Types & Architecture

```text
ClusterIP:     Internal-only IP, reachable within cluster
NodePort:      Exposes port on every node (30000-32767), forwards to ClusterIP
LoadBalancer:  Provisions external LB (cloud), forwards to NodePort/ClusterIP
ExternalName:  DNS CNAME (no proxying, just DNS alias)
Headless:      ClusterIP=None, returns pod IPs directly (no load balancing)
```

### Quick Diagnosis

```bash
# List all services and their endpoints
kubectl get svc,ep -A

# Detailed service info
kubectl describe svc SVC -n NAMESPACE

# Check endpoints
kubectl get endpoints SVC -n NAMESPACE
kubectl get endpoints SVC -n NAMESPACE -o yaml

# Check endpoint slices (K8s 1.21+)
kubectl get endpointslice -n NAMESPACE -l kubernetes.io/service-name=SVC

# Test service from inside cluster
kubectl run test --image=busybox --rm -it -- wget -O- http://SVC.NAMESPACE:8080
kubectl run test --image=busybox --rm -it -- nc -zv SVC 8080

# Test if kube-proxy is forwarding
kubectl exec -it POD -- curl http://SVC:PORT
```

---

## Service Has No Endpoints

### What It Means

```text
The service selector doesn't match any running pods, OR the pods
that match are failing their readiness probe.
```

### Diagnosis

```bash
# 1. Check if endpoints are populated
kubectl get endpoints SVC -n NAMESPACE
# If empty: "no endpoints" → selector doesn't match pods

# 2. Check what the service selector is
kubectl get svc SVC -n NAMESPACE -o jsonpath='{.spec.selector}'
# Example: {"app":"myapp","env":"production"}

# 3. Check if any pods match that selector
kubectl get pods -n NAMESPACE -l app=myapp,env=production
# If "No resources found" → selector mismatch

# 4. Check if matching pods are READY
kubectl get pods -n NAMESPACE -l app=myapp
# Pods must be 1/1 READY (not 0/1)
# If pods show Running but 0/1 READY → readiness probe failing

# 5. Check the service port names vs pod containerPort names
kubectl get svc SVC -n NAMESPACE -o yaml | grep -A5 ports
kubectl get pod POD -n NAMESPACE -o yaml | grep -A3 ports
```

### Common Causes & Fixes

| Cause | Diagnosis | Fix |
|-------|-----------|-----|
| **Selector mismatch** | `kubectl get pods -l KEY=VAL` returns nothing | Fix service selector to match pod labels |
| **Readiness probe failing** | Pods show Running but 0/1 Ready | Check readiness probe config. Temporarily remove to test. |
| **Pods in different namespace** | Service in ns `default`, pods in ns `production` | Services can ONLY route to pods in the SAME namespace |
| **Port name mismatch** | Service port name != containerPort name (for named ports) | Align port names OR use numeric ports instead |
| **Pods not yet created** | Deployment scaled to 0 or not yet applied | Scale up deployment |
| **targetPort wrong** | Service targetPort doesn't match containerPort | Fix targetPort in service spec |

### Scenario: "Service working yesterday, broken today — no endpoints"

```text
Symptom: 503 Service Unavailable from ingress. Service has no endpoints.
         kubectl get pods -l app=myapp → 5 pods Running but 0/1 Ready

Diagnosis:
  kubectl describe pod myapp-abc123 -n production
  → Readiness probe failed: HTTP probe failed with statuscode: 500
  → The /healthz endpoint started returning 500 after a config change
  → Readiness probe marks pods as NotReady → removed from endpoints

  kubectl logs myapp-abc123 -n production --tail=20
  → ERROR: database connection pool exhausted
  → The config change switched to a new DB but connection pool is full

Fix:
  # Immediate: Fix DB connection issue
  # Short term: Temporarily remove readiness probe if db is non-critical:
  kubectl patch deployment myapp -n production \
    -p '{"spec":{"template":{"spec":{"containers":[{"name":"app","readinessProbe":null}]}}}}'
  # Long term: Fix the /healthz endpoint to handle DB failures gracefully
```

### Scenario: "Service routes traffic to a single pod, ignores others"

```text
Symptom: 5 pods running and Ready, but all traffic goes to pod-0 only.
         Other pods show 0 requests in their logs.

Diagnosis:
  kubectl get endpoints myapp -n production -o yaml
  → addresses: [{ip: 10.244.1.5}]   ← only ONE endpoint, not 5

  Wait — 5 pods are Ready but only 1 endpoint? Check pod IPs:
  kubectl get pods -n production -l app=myapp -o wide
  → All 5 pods have IPs and are Ready.

  Check EndpointSlices:
  kubectl get endpointslice -n production
  → Only 1 slice for myapp, with 1 address

  Root cause: kube-proxy or endpoint controller is bugged/stale.
  Restart kube-proxy:
  kubectl rollout restart daemonset kube-proxy -n kube-system
```

---

## LoadBalancer Stuck in Pending

```bash
kubectl get svc myapp -n production
# NAME    TYPE           CLUSTER-IP   EXTERNAL-IP   PORT(S)        AGE
# myapp   LoadBalancer   10.43.1.10   <pending>     80:30080/TCP   30m
```

### Cloud-Specific Causes

| Cloud | Cause | Fix |
|-------|-------|-----|
| **AWS** | No AWS Load Balancer Controller installed (EKS) | Install AWS LBC via Helm: `helm install aws-lbc aws-load-balancer-controller` |
| **AWS** | Service using wrong annotation | For NLB: `service.beta.kubernetes.io/aws-load-balancer-type: nlb` |
| **AWS** | Subnet tagging missing | Tag subnets: `kubernetes.io/role/elb: 1` or `kubernetes.io/role/internal-elb: 1` |
| **GCP** | Firewall rule blocking health check | Allow GCP LB health check IP ranges (`35.191.0.0/16`, `130.211.0.0/22`) |
| **Azure** | AKS basic SKU LB limitation | Upgrade to Standard SKU or use Internal LB annotations |
| **Bare metal** | No LB controller (MetalLB) | Install MetalLB or use NodePort + external LB |

### Diagnosis

```bash
# Check events on the service
kubectl describe svc myapp -n production | tail -20

# For AWS: check LB controller logs
kubectl logs -n kube-system -l app.kubernetes.io/name=aws-load-balancer-controller

# For MetalLB: check controller logs
kubectl logs -n metallb-system -l component=controller
```

### Scenario: "EKS LoadBalancer stuck in pending after cluster upgrade"

```text
Symptom: After upgrading EKS from 1.27 to 1.29, LoadBalancer services
         are stuck in <pending>.

Diagnosis:
  kubectl describe svc myapp -n production
  → Events: <none>  (no events at all — controller not processing)

  kubectl get pods -n kube-system | grep aws-load-balancer
  → No pods found! The AWS LBC was installed in a namespace that got deleted.

  The AWS Load Balancer Controller was installed in the `kube-system`
  namespace of the OLD cluster. During the upgrade, the controller
  deployment was lost.

Fix:
  helm repo add eks https://aws.github.io/eks-charts
  helm upgrade --install aws-load-balancer-controller eks/aws-load-balancer-controller \
    -n kube-system --set clusterName=my-cluster --set serviceAccount.create=true
```

---

## NodePort Connection Refused

```text
NodePort services are accessible via <NodeIP>:<NodePort> from outside
the cluster. Common issues:

1. Firewall blocking the NodePort range (30000-32767)
   Fix: Open NodePort range in security group/firewall.

2. externalTrafficPolicy: Local
   If set to Local, traffic arriving at a node that DOESN'T have a
   local pod will be DROPPED (not forwarded to another node).
   Fix: Change to externalTrafficPolicy: Cluster (default) or ensure
        every node has a pod.

3. Node IP changed
   NodePort is tied to the node's IP. If the node was replaced, the
   IP changed.
   Fix: Use a LoadBalancer service or external LB with health checks.

4. Kube-proxy not running on the node
   kubectl get pods -n kube-system -l k8s-app=kube-proxy -o wide
   If kube-proxy is down on a node, NodePort won't work on that node.
```

### Test NodePort Locally

```bash
# Get the NodePort
kubectl get svc myapp -n production -o jsonpath='{.spec.ports[0].nodePort}'

# Get a node IP
kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}'

# Test connection (from outside cluster)
curl http://NODE_IP:NODE_PORT

# Test from any pod (to verify service works)
kubectl run test --image=curlimages/curl --rm -it -- curl http://myapp.production:8080
```

---

## DNS Resolution Issues

```text
Services are resolvable as:
  <service-name>.<namespace>.svc.cluster.local
  <service-name>.<namespace>
  <service-name>              (same namespace only)

If DNS resolution fails, check CoreDNS.
```

### CoreDNS Diagnosis

```bash
# Check CoreDNS pods
kubectl get pods -n kube-system -l k8s-app=kube-dns
# Or (newer clusters):
kubectl get pods -n kube-system -l k8s-app=coredns

# Check CoreDNS logs
kubectl logs -n kube-system -l k8s-app=kube-dns --tail=50

# Common CoreDNS log patterns:
# "no such host" → Service doesn't exist, or namespace wrong
# "i/o timeout" → CoreDNS can't reach upstream DNS
# "SERVFAIL" → DNS server returned error

# Check CoreDNS config
kubectl get configmap coredns -n kube-system -o yaml

# Test DNS from a pod
kubectl run dns-test --image=busybox --rm -it -- nslookup myapp.production.svc.cluster.local
kubectl run dns-test --image=busybox --rm -it -- nslookup google.com

# Check pod's /etc/resolv.conf
kubectl exec POD -- cat /etc/resolv.conf
# Should show:
#   nameserver 10.96.0.10  (or your cluster's DNS IP)
#   search <namespace>.svc.cluster.local svc.cluster.local cluster.local
```

### Scenario: "Service DNS resolves on some pods but not others"

```text
Symptom: Pod A can resolve myapp.production.svc.cluster.local.
         Pod B in the SAME namespace gets "can't resolve host."

Diagnosis:
  kubectl exec pod-a -- cat /etc/resolv.conf
  → nameserver 10.96.0.10
  → search default.svc.cluster.local svc.cluster.local cluster.local

  kubectl exec pod-b -- cat /etc/resolv.conf
  → nameserver 10.96.0.10
  → search default.svc.cluster.local svc.cluster.local cluster.local
  → options ndots:1

  Both look fine. Check if a NetworkPolicy is blocking UDP 53:
  kubectl get networkpolicies -n production
  → deny-outbound-traffic   ← blocks all outbound unless explicitly allowed

  Pod B has labels matching the deny policy but no allow rule for DNS.
  Pod A has an explicit allow rule for CoreDNS.

Fix: Add a NetworkPolicy rule allowing egress to CoreDNS on UDP 53.
```

---

## EndpointSlice Troubleshooting

```text
Kubernetes 1.21+ uses EndpointSlices (not Endpoints) for services
with >100 endpoints. EndpointSlices are more scalable (100 endpoints
per slice vs 1 Endpoints object).
```

```bash
# List endpoint slices for a service
kubectl get endpointslice -n NAMESPACE -l kubernetes.io/service-name=SVC

# Check endpoint slice details
kubectl describe endpointslice ES-NAME -n NAMESPACE

# Check if endpoint slice controller is working
kubectl get pods -n kube-system -l k8s-app=kube-proxy
kubectl logs -n kube-system -l k8s-app=kube-proxy | grep endpoints
```

### Common EndpointSlice Issues

```text
1. kube-proxy not watching EndpointSlices
   → Older kube-proxy versions may not support EndpointSlices.
   → Check kube-proxy version matches cluster version.

2. Stale endpoints in slices
   → EndpointSlices have a TTL. If the controller crashes mid-update,
     stale endpoints can persist.
   → Restart the endpoint-slice controller:
     kubectl delete pod -n kube-system -l k8s-app=kube-controller-manager

3. Large number of slices cause API server load
   → Monitor: kubectl get endpointslice -A --no-headers | wc -l
   → If >10,000 slices, API server watches may be slow
```

---

## References

- [Kubernetes Services](https://kubernetes.io/docs/concepts/services-networking/service/)
- [Debug Services](https://kubernetes.io/docs/tasks/debug/debug-application/debug-service/)
- [EndpointSlices](https://kubernetes.io/docs/concepts/services-networking/endpoint-slices/)
- [DNS for Services and Pods](https://kubernetes.io/docs/concepts/services-networking/dns-pod-service/)
