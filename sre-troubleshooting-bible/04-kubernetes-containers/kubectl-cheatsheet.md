# kubectl Cheatsheet

> **Category:** Kubernetes | kubectl
> **Difficulty:** Basic to Intermediate
> **Last Reviewed:** 2026-06
> **Tags:** `#kubernetes` `#kubectl` `#cheatsheet`

---

## Getting Information

```bash
# Pods
kubectl get pods                              # pods in current namespace
kubectl get pods -A                           # all namespaces
kubectl get pods -o wide                      # with node and IP
kubectl get pods -l app=myapp                 # label selector
kubectl get pods -l 'env in (staging,prod)'   # set-based selector
kubectl get pods --field-selector=status.phase=Running  # field selector
kubectl get pods --sort-by=.metadata.creationTimestamp  # sort
kubectl get pods -o custom-columns=NAME:.metadata.name,IP:.status.podIP,NODE:.spec.nodeName

# All resources
kubectl get all                                # pods, svc, deploy, rs (current ns)
kubectl get all -A                             # all namespaces
kubectl get deploy,sts,ds,pods,svc,ing,cm,secret,pvc,pv -A  # everything

# Nodes
kubectl get nodes -o wide
kubectl get nodes -o custom-columns=NAME:.metadata.name,CPU:.status.capacity.cpu,MEM:.status.capacity.memory,INSTANCE:.metadata.labels.'node\.kubernetes\.io/instance-type'
kubectl describe node node-1                   # capacity, allocatable, conditions, events

# Resource usage (needs metrics-server)
kubectl top pods -A --sort-by=cpu
kubectl top pods -A --sort-by=memory
kubectl top nodes

# Detailed info
kubectl describe pod POD                       # events, containers, volumes, conditions
kubectl describe deployment DEPLOY             # strategy, conditions, rollout status
kubectl describe service SVC                   # selector, ports, endpoints, type

# Events (cluster-wide audit trail)
kubectl get events -A --sort-by=.lastTimestamp | tail -30
kubectl get events -A --sort-by=.lastTimestamp -w  # watch live
kubectl get events -A --field-selector type=Warning

# YAML output
kubectl get deployment myapp -o yaml
kubectl get pod POD -o json | jq '.status.containerStatuses[] | {name: .name, ready: .ready, restarts: .restartCount}'
```

---

## Debugging

```bash
# Logs
kubectl logs POD                               # latest pod logs
kubectl logs POD --tail=100 -f                  # follow tail
kubectl logs POD -c container-name              # specific container
kubectl logs POD --previous                     # logs from PREVIOUS crashed container
kubectl logs POD --since=5m                     # last 5 minutes
kubectl logs POD --timestamps                   # with timestamps
kubectl logs -l app=myapp --all-containers=true # all pods matching label

# Exec into pod
kubectl exec -it POD -- /bin/bash              # shell (if bash installed)
kubectl exec -it POD -- /bin/sh                # shell (alpine/busybox)
kubectl exec POD -- ls -la /app                # one-off command
kubectl exec POD -c container-name -- env      # specific container

# Port forward (local debugging)
kubectl port-forward POD 8080:80               # localhost:8080 → pod:80
kubectl port-forward svc/myapp 5432:5432        # localhost:5432 → service:5432
kubectl port-forward deploy/myapp 9090:8080     # deployment port forward

# Copy files
kubectl cp POD:/path/to/file ./local-file       # from pod
kubectl cp ./config.txt POD:/etc/app/           # to pod
kubectl cp ns/POD:/path ./local                 # cross-namespace

# Ephemeral debug container (K8s 1.23+)
kubectl debug -it POD --image=busybox --target=app  # attach to running pod
kubectl debug -it node-1 --image=alpine -- chroot /host  # node-level debug

# Run temporary pod
kubectl run -it --rm debug --image=alpine -- sh  # interactive, auto-delete
kubectl run debug --image=nicolaka/netshoot --rm -it -- bash  # network toolkit
kubectl run debug --image=busybox --rm -it --restart=Never -- wget -O- http://myapp:8080/healthz

# Run with service account
kubectl run debug-sa --image=alpine --rm -it --overrides='{"spec":{"serviceAccountName":"my-sa"}}' -- sh

# DNS debugging
kubectl exec -it POD -- nslookup my-service.default.svc.cluster.local
kubectl exec -it POD -- cat /etc/resolv.conf
```

---

## Management

```bash
# Deployments
kubectl rollout status deployment/myapp         # watch deployment progress
kubectl rollout history deployment/myapp         # revision history
kubectl rollout history deployment/myapp --revision=3  # specific rev details
kubectl rollout undo deployment/myapp           # rollback to previous
kubectl rollout undo deployment/myapp --to-revision=2  # rollback to specific rev
kubectl rollout restart deployment/myapp        # rolling restart (new RS)

# Scaling
kubectl scale deployment/myapp --replicas=5
kubectl scale deployment/myapp --replicas=5 --current-replicas=3  # conditional scale
kubectl scale --replicas=3 deployment/myapp deployment/myapp2

# Node management
kubectl cordon node-1                           # mark unschedulable (no new pods)
kubectl uncordon node-1                         # mark schedulable again
kubectl drain node-1 --ignore-daemonsets --delete-emptydir-data  # evict all pods
kubectl drain node-1 --ignore-daemonsets --force --grace-period=0  # emergency drain
kubectl taint nodes node-1 key=value:NoSchedule  # add taint
kubectl taint nodes node-1 key=value:NoSchedule-  # remove taint

# Force delete
kubectl delete pod POD --grace-period=0 --force  # immediate force delete
kubectl delete pod POD --force --grace-period=0 --wait=false  # async force delete
kubectl patch pod POD -p '{"metadata":{"finalizers":[]}}' --type=merge  # remove finalizers

# Edit resources
kubectl edit deployment/myapp                   # edit in $EDITOR
kubectl patch deployment myapp -p '{"spec":{"replicas":3}}'  # strategic merge patch
kubectl patch deployment myapp --type json -p='[{"op":"replace","path":"/spec/replicas","value":3}]'

# Annotate and label
kubectl label pod POD env=production --overwrite
kubectl annotate deployment myapp kubernetes.io/change-cause="Deploy v1.2.3: Fix login bug"
```

---

## Configuration

```bash
# Apply and diff
kubectl apply -f deployment.yaml
kubectl apply -k ./overlays/production/          # kustomize
kubectl apply -f deployment.yaml --dry-run=client  # validate only
kubectl diff -f deployment.yaml                 # show what would change

# ConfigMaps
kubectl create configmap my-config --from-file=app.properties
kubectl create configmap my-config --from-file=config-dir/
kubectl create configmap my-config --from-literal=key1=value1 --from-literal=key2=value2
kubectl create configmap my-config --from-env-file=.env
kubectl get configmap my-config -o yaml

# Secrets
kubectl create secret generic my-secret --from-literal=password=s3cret
kubectl create secret generic my-secret --from-file=id_rsa
kubectl create secret docker-registry regcred \
  --docker-server=https://index.docker.io/v1/ \
  --docker-username=user \
  --docker-password=token \
  --docker-email=ops@example.com
kubectl create secret tls tls-secret --cert=cert.pem --key=key.pem
kubectl get secret my-secret -o jsonpath='{.data.password}' | base64 -d

# Generate YAML without applying
kubectl create deployment myapp --image=nginx --dry-run=client -o yaml > deploy.yaml
kubectl create configmap my-config --from-file=app.properties --dry-run=client -o yaml
kubectl run mypod --image=nginx --dry-run=client -o yaml
```

---

## JSONPath and Custom Columns

```bash
# JSONPath basics
kubectl get pods -o jsonpath='{.items[*].metadata.name}'           # all pod names
kubectl get pods -o jsonpath='{.items[*].status.podIP}'            # all pod IPs
kubectl get pods -o jsonpath='{.items[0].spec.containers[*].image}' # first pod's images
kubectl get pods -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.podIP}{"\n"}{end}'

# Custom columns (most useful for quick inspections)
kubectl get pods -o custom-columns=\
NAME:.metadata.name,\
IP:.status.podIP,\
NODE:.spec.nodeName,\
STATUS:.status.phase

kubectl get nodes -o custom-columns=\
NAME:.metadata.name,\
CPU_REQ:.status.allocatable.cpu,\
MEM_REQ:.status.allocatable.memory,\
INSTANCE:.metadata.labels.'node\.kubernetes\.io/instance-type'

# Sortable custom columns
kubectl get pods -A -o custom-columns=\
NAMESPACE:.metadata.namespace,\
NAME:.metadata.name,\
RESTARTS:.status.containerStatuses[*].restartCount \
  --sort-by=.status.containerStatuses[*].restartCount
```

---

## Context and Namespace

```bash
# Context management
kubectl config get-contexts                     # list all contexts
kubectl config current-context                  # show current
kubectl config use-context production           # switch context
kubectl config set-context prod --cluster=prod --user=admin  # create context
kubectl config delete-context staging           # delete context

# Namespace
kubectl get namespaces
kubectl config set-context --current --namespace=staging    # set default ns for current context
kubectl create namespace dev
kubectl delete namespace dev --wait=false        # background delete

# Kubectl plugins and aliases
kubectl krew list                               # list installed plugins

# Common shell aliases
# alias k='kubectl'
# alias kg='kubectl get'
# alias kd='kubectl describe'
# alias kl='kubectl logs'
# alias ke='kubectl exec -it'
# alias kn='kubectl config set-context --current --namespace'
```

---

## RBAC Debugging

```bash
# Check permissions
kubectl auth can-i list pods                    # can I list pods?
kubectl auth can-i create deployments --namespace production  # scoped
kubectl auth can-i '*' '*'                     # am I admin?
kubectl auth can-i --list                       # list all my permissions

# Check what a service account can do
kubectl auth can-i list pods --as system:serviceaccount:default:my-sa
kubectl auth can-i --list --as system:serviceaccount:default:my-sa

# List roles and bindings
kubectl get roles,rolebindings -A
kubectl get clusterroles,clusterrolebindings
kubectl describe role my-role -n production
```

---

## API Resources

```bash
# Discover API resources
kubectl api-resources                          # all resource types
kubectl api-resources --namespaced=true        # namespaced only
kubectl api-resources --namespaced=false       # cluster-scoped only
kubectl api-versions                           # all API versions

# Explain resource structure
kubectl explain deployment                    # top-level fields
kubectl explain deployment.spec               # drill down
kubectl explain deployment.spec.template.spec.containers --recursive  # all fields
kubectl explain hpa.spec.metrics --recursive
```

---

## References

- [kubectl Command Reference](https://kubernetes.io/docs/reference/generated/kubectl/kubectl-commands)
- [kubectl JSONPath Documentation](https://kubernetes.io/docs/reference/kubectl/jsonpath/)
- [kubectl Cheat Sheet (official)](https://kubernetes.io/docs/reference/kubectl/cheatsheet/)
