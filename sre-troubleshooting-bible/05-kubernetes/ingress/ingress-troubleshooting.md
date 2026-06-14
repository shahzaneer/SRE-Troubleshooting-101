# Ingress Troubleshooting

> **Category:** Kubernetes | Ingress | Networking
> **Difficulty:** Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#kubernetes` `#ingress` `#networking` `#tls`

---

## Table of Contents

1. [Ingress Architecture](#ingress-architecture)
2. [503 Backend Unavailable](#503-backend-unavailable)
3. [404 Not Found (Wrong Path/Host)](#404-not-found-wrong-pathhost)
4. [TLS Certificate Issues](#tls-certificate-issues)
5. [Ingress Controller Not Processing](#ingress-controller-not-processing)
6. [Wildcard & Regex Routing Problems](#wildcard--regex-routing-problems)

---

## Ingress Architecture

```text
Ingress = Routing rules (L7) + Ingress Controller (implementation)

Popular controllers:
  nginx-ingress    (Kubernetes community, most common)
  aws-alb-ingress  (AWS ALB controller for EKS)
  traefik          (Cloud-native edge router)
  istio-gateway    (Service mesh ingress)
  contour          (Envoy-based, VMware)
  haproxy-ingress  (HAProxy-based)

The Ingress resource defines RULES. The Ingress CONTROLLER
reads those rules and configures the actual proxy.
```

### Quick Diagnosis

```bash
# Check ingress
kubectl get ingress -A
kubectl describe ingress INGRESS -n NAMESPACE

# Check ingress controller pods
kubectl get pods -n ingress-nginx -o wide
# Or:
kubectl get pods -n kube-system -l app.kubernetes.io/name=ingress-nginx

# Check ingress controller logs
kubectl logs -n ingress-nginx deployment/ingress-nginx-controller --tail=50
kubectl logs -n kube-system deployment/nginx-ingress-controller --tail=50

# Check admission webhooks (if ingress not being processed)
kubectl get validatingwebhookconfiguration -A | grep ingress
```

---

## 503 Backend Unavailable

### What It Means

```text
The ingress controller found the Ingress rule, resolved the backend
service, but can't connect to it. This is the #1 ingress issue.

Common causes:
  1. Backend service has no endpoints (see Service Troubleshooting)
  2. Wrong service name or port in Ingress spec
  3. Service in a different namespace (not allowed)
  4. NetworkPolicy blocking ingress-controller → pod traffic
  5. Backend pod's readiness probe failing
```

### Diagnosis

```bash
# 1. Check if the backend service exists
kubectl get svc NAMESPACE/SVC

# 2. Check if the backend service has endpoints
kubectl get endpoints SVC -n NAMESPACE

# 3. Verify the service name and port in the ingress spec
kubectl get ingress INGRESS -n NAMESPACE -o yaml | grep -A10 "backend:"
# Or check all rules:
kubectl get ingress INGRESS -n NAMESPACE -o json | jq '.spec.rules[].http.paths[]'

# 4. Test the backend directly from the ingress controller pod
INGRESS_POD=$(kubectl get pods -n ingress-nginx -l app.kubernetes.io/component=controller -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n ingress-nginx $INGRESS_POD -- curl -v http://SVC.NAMESPACE:PORT/healthz

# 5. Check controller logs for upstream errors
kubectl logs -n ingress-nginx deployment/ingress-nginx-controller | grep "upstream"
```

### Scenario: "503 on specific path only"

```text
Symptom: https://myapp.example.com/ works fine (200).
         https://myapp.example.com/api returns 503.

Ingress YAML:
  - host: myapp.example.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: frontend-svc
            port: {number: 80}
      - path: /api
        pathType: Prefix
        backend:
          service:
            name: api-svc        ← suspect
            port: {number: 8080}

Diagnosis:
  kubectl get endpoints api-svc -n production
  → No endpoints

  kubectl get pods -n production -l app=api
  → 3 pods Running but 0/1 Ready

  kubectl describe pod api-abc123 -n production | grep -A5 Readiness
  → Readiness probe: HTTP GET /healthz on port 8080 ... delay=0s
  → Warning  Unhealthy  2m  kubelet  Readiness probe failed: Get "http://10.244.1.5:8080/healthz": dial tcp 10.244.1.5:8080: connection refused

  The API pods are starting up but the app binds to port 8080 AFTER
  the readiness probe fires (because readinessProbe has initialDelaySeconds: 0).
  The probe fires immediately, fails, and pods stay NotReady.

Fix:
  Add initialDelaySeconds: 15 to readinessProbe:
  readinessProbe:
    httpGet:
      path: /healthz
      port: 8080
    initialDelaySeconds: 15
    periodSeconds: 5
```

---

## 404 Not Found (Wrong Path/Host)

```text
The ingress controller receives the request but no Ingress rule matches.

Common causes:
  1. Host header doesn't match any Ingress spec.rules[].host
  2. Path doesn't match (case sensitivity, trailing slash, pathType)
  3. Missing defaultBackend (no default route)
  4. Multiple ingresses with conflicting rules
```

### PathType Gotchas

```text
pathType: Prefix → /foo matches /foo, /foo/bar, /foo/bar/baz
pathType: Exact  → /foo matches ONLY /foo (not /foo/ or /foo/bar)
pathType: ImplementationSpecific → depends on controller

Common mistake: pathType: Exact, path: /api
  → Matches /api but NOT /api/ (missing trailing slash = 404)
  
Fix: Use Exact: /api/ or Prefix: /api (without trailing slash)
     or add both paths.
```

```bash
# Test what rule matches a URL
kubectl exec -n ingress-nginx INGRESS_CONTROLLER -- nginx -T 2>/dev/null | grep -A10 "location"

# For nginx-ingress, check the generated nginx config
kubectl exec -n ingress-nginx INGRESS_CONTROLLER -- cat /etc/nginx/nginx.conf | grep -A5 "server_name MYHOST"
```

### Default Backend

```bash
# If no host/path matches, traffic goes to the ingress's defaultBackend
kubectl get ingress INGRESS -n NAMESPACE -o yaml | grep -A5 defaultBackend

# If defaultBackend is not set, nginx-ingress uses its own default (404 page)
# Create a catch-all backend:
spec:
  defaultBackend:
    service:
      name: catch-all
      port:
        number: 80
```

---

## TLS Certificate Issues

### Diagnosis

```bash
# Check if TLS secret exists
kubectl get secret tls-secret -n NAMESPACE

# Check TLS config in ingress
kubectl get ingress INGRESS -n NAMESPACE -o yaml | grep -A10 tls

# Check certificate details
kubectl get secret tls-secret -n NAMESPACE -o jsonpath='{.data.tls\.crt}' | base64 -d | openssl x509 -text -noout | grep -A2 "Validity"
# Validity
#   Not Before: Jan  1 00:00:00 2025 GMT
#   Not After : Dec 31 23:59:59 2026 GMT

# Test TLS from outside
echo | openssl s_client -connect myapp.example.com:443 -servername myapp.example.com 2>/dev/null | openssl x509 -noout -dates

# Ingress controller logs for TLS errors
kubectl logs -n ingress-nginx deployment/ingress-nginx-controller | grep -i "tls\|certificate\|ssl"
```

### Common TLS Issues

| Issue | Symptom | Fix |
|-------|---------|-----|
| **Cert expired** | Browser shows "cert expired" | Renew cert, update secret, restart ingress controller |
| **Secret missing** | "no SSL certificate for host" | Create TLS secret: `kubectl create secret tls tls-secret --cert=cert.pem --key=key.pem` |
| **Secret wrong format** | "failed to load certificate" | Secret must have keys `tls.crt` and `tls.key` (not `cert.pem`, etc.) |
| **TLS secret in wrong namespace** | "secret not found" | Secret must be in same namespace as Ingress |
| **Self-signed cert** | Browser shows "not secure" | Use Let's Encrypt with cert-manager for auto-renewal |
| **Missing intermediate cert** | "unable to verify the first certificate" | Include full chain in tls.crt (server cert + intermediates) |
| **SNI mismatch** | Wrong cert served | Host header must match cert's CN or SAN |

### Scenario: "SSL cert expired on a Friday night"

```text
Symptom: All users getting SSL errors. cert-manager has failed renewal.
         kubectl get certificate -A shows "Ready: False, Reason: Failed"

Diagnosis:
  kubectl get certificaterequest -A
  → certificate request failed: acme: error: 400 :: urn:ietf:params:acme:error:dns
  → DNS-01 challenge failed because the DNS zone was deleted during
    a domain migration last week.

  cert-manager can't complete the HTTP-01 challenge either because
  the ingress isn't routing /.well-known/acme-challenge/ correctly.

Fix:
  # Emergency: Create a self-signed cert to get site back up
  openssl req -x509 -nodes -days 7 -newkey rsa:2048 \
    -keyout /tmp/tls.key -out /tmp/tls.crt \
    -subj "/CN=myapp.example.com"
  
  kubectl create secret tls emergency-cert -n production \
    --cert=/tmp/tls.crt --key=/tmp/tls.key --dry-run=client -o yaml | kubectl apply -f -
  
  # Update ingress to use emergency cert
  kubectl patch ingress myapp -n production \
    --type='json' -p='[{"op":"replace","path":"/spec/tls/0/secretName","value":"emergency-cert"}]'

  # Fix DNS challenge issue (post-mortem)
  # Recreate DNS zone or switch to HTTP-01 challenge
```

---

## Ingress Controller Not Processing

```text
Ingress resources are created but the ingress controller isn't
picking them up. No load balancer created, no routing configured.
```

### Diagnosis

```bash
# 1. Check ingress controller is running
kubectl get pods -n ingress-nginx
kubectl get pods -n kube-system | grep ingress

# 2. Check controller logs for errors
kubectl logs -n ingress-nginx deployment/ingress-nginx-controller

# 3. Check if IngressClass matches
kubectl get ingressclass
# NAME    CONTROLLER                     PARAMETERS   AGE
# nginx   k8s.io/ingress-nginx          <none>       30d
# alb     ingress.k8s.aws/alb           <none>       30d

# 4. Check if your ingress references a valid IngressClass
kubectl get ingress INGRESS -n NAMESPACE -o yaml | grep ingressClassName
# ingressClassName: nginx  ← must match IngressClass name

# 5. If using ingressClassName implicitly (deprecated annotation):
kubectl get ingress INGRESS -n NAMESPACE -o yaml | grep "kubernetes.io/ingress.class"

# 6. Check admission webhook status
kubectl get validatingwebhookconfiguration | grep ingress
```

### Scenario: "Ingress created but no external IP assigned"

```text
Symptom: kubectl get ingress shows no ADDRESS after 30 minutes.
         No load balancer created in AWS/GCP.

Diagnosis:
  # Check ingress class
  kubectl get ingress myapp -n production -o yaml | grep ingressClassName
  → ingressClassName: ""    ← empty, no default IngressClass

  kubectl get ingressclass
  → NAME    CONTROLLER                   PARAMETERS   AGE
  → nginx   k8s.io/ingress-nginx        <none>       30d

  # The nginx IngressClass exists but is NOT marked as default.
  # The Ingress doesn't specify ingressClassName, so no controller
  # picks it up.

Fix:
  # Option A: Set ingressClassName on the Ingress
  kubectl patch ingress myapp -n production \
    -p '{"spec":{"ingressClassName":"nginx"}}'

  # Option B: Make the IngressClass the default
  kubectl patch ingressclass nginx \
    -p '{"metadata":{"annotations":{"ingressclass.kubernetes.io/is-default-class":"true"}}}'
```

---

## Wildcard & Regex Routing Problems

```text
Common nginx-ingress routing gotchas:

1. Path conflict: / and /api both Prefix
   → /api/foo matches BOTH rules. nginx-ingress uses longest match.
   → Prefix /api takes priority over Prefix /.

2. Rewrite target
   → annotation: nginx.ingress.kubernetes.io/rewrite-target: /
   → /api/users → rewrites to /users on backend

3. CORS headers
   → annotation: nginx.ingress.kubernetes.io/enable-cors: "true"
   → annotation: nginx.ingress.kubernetes.io/cors-allow-origin: "https://app.example.com"

4. Client body size (413 Request Entity Too Large)
   → annotation: nginx.ingress.kubernetes.io/proxy-body-size: "100m"

5. WebSocket support
   → annotation: nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
   → annotation: nginx.ingress.kubernetes.io/proxy-send-timeout: "3600"
```

---

## References

- [Kubernetes Ingress](https://kubernetes.io/docs/concepts/services-networking/ingress/)
- [nginx-ingress Troubleshooting](https://kubernetes.github.io/ingress-nginx/troubleshooting/)
- [cert-manager Troubleshooting](https://cert-manager.io/docs/faq/troubleshooting/)
